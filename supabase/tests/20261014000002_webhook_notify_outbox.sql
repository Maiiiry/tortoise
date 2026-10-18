-- ============================================================================
-- SQL-level verification for migration 20261014000002 (issue #5323)
-- The billing-notification OUTBOX: `claim_webhook_event` (atomic claim+enqueue),
-- `claim_due_webhook_notifies` (the drain's leased batch claim),
-- `resolve_webhook_notify` (the settle door).
--
-- WHAT THIS SUITE IS FOR. #5323's whole claim is that the notification is
-- RECOVERABLE: the enqueue cannot be lost between the claim and the hand-off
-- (SECTION 2), a refused/failed attempt stays DUE (SECTION 4/5), a concurrent
-- drain cannot claim the same row twice (SECTION 5 — the LEASE), and an
-- exhausted row becomes a DEAD, REPORTABLE record instead of a silent one
-- (SECTION 6). A functional-only suite that just called the functions once each
-- would pass on the pre-fix read-then-insert too.
--
-- HOW TO RUN (no Docker — PGlite harness):
--   npm --prefix supabase/tests/pglite run validate
--
-- Every assertion RAISEs on failure; with ON_ERROR_STOP=1 any failure exits
-- non-zero. Test rows use the '5323-' prefix for safe cleanup.
--
-- HONEST LIMIT: PGlite is SINGLE-CONNECTION, so this suite cannot exercise real
-- contention (two drains racing for the same row). What it DOES prove is the
-- property that makes contention safe — `claim_due_webhook_notifies` moves the
-- due time forward whenever it hands a row out, so a second claimant's
-- predicate is false. That is asserted directly (SECTION 5), not inferred from
-- SKIP LOCKED.
-- ============================================================================

-- ── Assertion helper (tests schema; executed as the harness's superuser) ───
CREATE SCHEMA IF NOT EXISTS tests;
CREATE OR REPLACE FUNCTION tests.assert(cond boolean, msg text)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  IF cond IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'ASSERTION FAILED: %', msg;
  END IF;
END $$;

-- ── Cleanup any prior test rows (idempotent re-runs) ────────────────────────
DELETE FROM public.webhook_events WHERE event_id LIKE '5323-%';

-- ============================================================================
-- SECTION 1 — grant hygiene: service_role ONLY
-- ============================================================================
DO $$ BEGIN
  PERFORM tests.assert(
    NOT has_function_privilege('anon',
      'public.claim_webhook_event(text,text,text,text,text,integer)', 'EXECUTE'),
    'anon must NOT EXECUTE claim_webhook_event');
  PERFORM tests.assert(
    NOT has_function_privilege('authenticated',
      'public.claim_webhook_event(text,text,text,text,text,integer)', 'EXECUTE'),
    'authenticated must NOT EXECUTE claim_webhook_event');
  PERFORM tests.assert(
    has_function_privilege('service_role',
      'public.claim_webhook_event(text,text,text,text,text,integer)', 'EXECUTE'),
    'service_role must EXECUTE claim_webhook_event');

  PERFORM tests.assert(
    NOT has_function_privilege('anon',
      'public.claim_due_webhook_notifies(integer,integer,integer)', 'EXECUTE'),
    'anon must NOT EXECUTE claim_due_webhook_notifies');
  PERFORM tests.assert(
    NOT has_function_privilege('authenticated',
      'public.claim_due_webhook_notifies(integer,integer,integer)', 'EXECUTE'),
    'authenticated must NOT EXECUTE claim_due_webhook_notifies');
  PERFORM tests.assert(
    has_function_privilege('service_role',
      'public.claim_due_webhook_notifies(integer,integer,integer)', 'EXECUTE'),
    'service_role must EXECUTE claim_due_webhook_notifies');

  PERFORM tests.assert(
    NOT has_function_privilege('anon',
      'public.resolve_webhook_notify(text,boolean,text,integer,integer)', 'EXECUTE'),
    'anon must NOT EXECUTE resolve_webhook_notify');
  PERFORM tests.assert(
    NOT has_function_privilege('authenticated',
      'public.resolve_webhook_notify(text,boolean,text,integer,integer)', 'EXECUTE'),
    'authenticated must NOT EXECUTE resolve_webhook_notify');
  PERFORM tests.assert(
    has_function_privilege('service_role',
      'public.resolve_webhook_notify(text,boolean,text,integer,integer)', 'EXECUTE'),
    'service_role must EXECUTE resolve_webhook_notify');
END $$;

-- ============================================================================
-- SECTION 2 — the ATOMIC claim + enqueue
-- ============================================================================
-- A first delivery claims AND enqueues in one statement; a replay claims
-- nothing and — the load-bearing half — cannot re-enqueue, blank out, or
-- double-count what the first delivery owes.
DO $$
DECLARE
    v_first  boolean;
    v_replay boolean;
BEGIN
    v_first := public.claim_webhook_event(
        '5323-evt-1', 'checkout.session.completed', 'billing_upgrade',
        'org-5323', 'pro', 90);
    PERFORM tests.assert(v_first, 'the first claim of an unseen event must be TRUE');

    v_replay := public.claim_webhook_event(
        '5323-evt-1', 'checkout.session.completed', 'billing_cancel',
        'org-OTHER', 'free', 90);
    PERFORM tests.assert(NOT v_replay,
        'a REPLAY must claim nothing (Stripe retries the same event_id) — the claim is the CAS');

    -- Exactly ONE row, and it still carries the FIRST delivery's enqueue: the
    -- replay's kind/org/tier must not overwrite it (a replay that rewrote the
    -- outbox would deliver the wrong notification).
    PERFORM tests.assert(
        (SELECT count(*) FROM public.webhook_events WHERE event_id = '5323-evt-1') = 1,
        'the claim must be a single row (ON CONFLICT DO NOTHING, not an upsert)');
    PERFORM tests.assert(
        (SELECT notify_kind FROM public.webhook_events WHERE event_id = '5323-evt-1')
            = 'billing_upgrade',
        'the replay must not overwrite the enqueued notify_kind');
    PERFORM tests.assert(
        (SELECT notify_org_id FROM public.webhook_events WHERE event_id = '5323-evt-1')
            = 'org-5323',
        'the replay must not overwrite the enqueued notify_org_id');
    PERFORM tests.assert(
        (SELECT notify_tier FROM public.webhook_events WHERE event_id = '5323-evt-1')
            = 'pro',
        'the replay must not overwrite the enqueued notify_tier');
    PERFORM tests.assert(
        (SELECT notify_attempts FROM public.webhook_events WHERE event_id = '5323-evt-1') = 0,
        'a fresh enqueue starts at 0 attempts');
    PERFORM tests.assert(
        (SELECT notified_at FROM public.webhook_events WHERE event_id = '5323-evt-1') IS NULL,
        'a fresh enqueue is NOT delivered');
    PERFORM tests.assert(
        (SELECT notify_next_attempt_at FROM public.webhook_events WHERE event_id = '5323-evt-1') > now(),
        'the enqueue must set a FIRST-ATTEMPT GRACE in the future — the request path '
        'is about to attempt the delivery itself and a concurrent drain must not race it');
END $$;

-- A side-effect-free event owes nothing: no notify_kind, and no due time either
-- (a NULL due time on an enqueued row would be a row the drain could claim).
DO $$
DECLARE
    v_first boolean;
BEGIN
    v_first := public.claim_webhook_event(
        '5323-evt-noop', 'customer.created', NULL, NULL, NULL, 90);
    PERFORM tests.assert(v_first, 'the claim is still the first-seen gate without a notify');
    PERFORM tests.assert(
        (SELECT notify_kind FROM public.webhook_events WHERE event_id = '5323-evt-noop') IS NULL,
        'a no-notify claim must enqueue nothing');
    PERFORM tests.assert(
        (SELECT notify_next_attempt_at FROM public.webhook_events
          WHERE event_id = '5323-evt-noop') IS NULL,
        'a no-notify claim must not be DUE — NULL kind AND NULL due time');
END $$;

-- ============================================================================
-- SECTION 3 — the drain ignores what it must ignore
-- ============================================================================
-- Neither an un-notified no-op row nor a still-in-grace enqueued row is
-- claimable. (The grace is what keeps the request path's own first attempt and
-- a concurrent drain from delivering the same notification twice.)
DO $$
DECLARE
    v_rows integer;
BEGIN
    SELECT count(*) INTO v_rows
      FROM public.claim_due_webhook_notifies(100, 120, 5) r
     WHERE r.event_id LIKE '5323-%';
    PERFORM tests.assert(v_rows = 0,
        'a row with no notify_kind, and a row still inside its first-attempt '
        'grace, must NOT be claimed by the drain');
END $$;

-- ============================================================================
-- SECTION 4 — delivered settles the row out of the due set
-- ============================================================================
DO $$
DECLARE
    v_outcome text;
    v_again   text;
    v_rows    integer;
BEGIN
    -- Let the seeded row come due (the harness has no clock to advance).
    UPDATE public.webhook_events SET notify_next_attempt_at = now() - interval '1 second'
     WHERE event_id = '5323-evt-1';

    v_outcome := public.resolve_webhook_notify('5323-evt-1', true, NULL, 60, 5);
    PERFORM tests.assert(v_outcome = 'delivered',
        format('a delivered settle must report delivered, got %L', v_outcome));
    PERFORM tests.assert(
        (SELECT notified_at FROM public.webhook_events WHERE event_id = '5323-evt-1') IS NOT NULL,
        'delivered must stamp notified_at');
    PERFORM tests.assert(
        (SELECT notify_next_attempt_at FROM public.webhook_events
          WHERE event_id = '5323-evt-1') IS NULL,
        'a delivered row must not stay DUE (it would be re-delivered)');

    -- Idempotent: a second settle of a delivered row reports 'missing' rather
    -- than re-arming it. This is the request-path/drain race in one statement.
    v_again := public.resolve_webhook_notify('5323-evt-1', false, 'boom', 60, 5);
    PERFORM tests.assert(v_again = 'missing',
        format('settling an already-settled row must be a no-op (missing), got %L', v_again));
    PERFORM tests.assert(
        (SELECT notify_attempts FROM public.webhook_events WHERE event_id = '5323-evt-1') = 0,
        'the no-op settle must not increment attempts on a delivered row');

    SELECT count(*) INTO v_rows
      FROM public.claim_due_webhook_notifies(100, 120, 5) r
     WHERE r.event_id = '5323-evt-1';
    PERFORM tests.assert(v_rows = 0, 'a delivered row must never be claimed again');
END $$;

-- ============================================================================
-- SECTION 5 — a failed attempt stays DUE, and the claim LEASES it
-- ============================================================================
-- This is the #5323 fix in one section: the failed attempt does NOT lose the
-- notification. The row keeps its enqueue, the attempt is counted, the retry is
-- scheduled, and — critically — the drain that hands the row out pushes its due
-- time forward, so a second drain (the next boot, a second process mid-rollout)
-- cannot claim it while the first is still trying.
DO $$
DECLARE
    v_outcome text;
    v_first   record;
    v_second  integer;
    v_attempts integer;
    v_backoff interval;
BEGIN
    UPDATE public.webhook_events
       SET notify_kind = 'billing_payment_failed', notify_org_id = 'org-5323',
           notify_tier = 'pro', notify_attempts = 0, notified_at = NULL,
           notify_next_attempt_at = now() - interval '1 second',
           notify_dead_at = NULL, notify_dead_reported_at = NULL
     WHERE event_id = '5323-evt-1';

    -- The drain claims it (and leases it).
    SELECT * INTO v_first
      FROM public.claim_due_webhook_notifies(100, 120, 5) r
     WHERE r.event_id = '5323-evt-1';
    PERFORM tests.assert(v_first.event_id IS NOT NULL,
        'a due enqueued row must be claimed by the drain');
    PERFORM tests.assert(v_first.notify_kind = 'billing_payment_failed',
        'the claimed row must carry the enqueued kind');
    PERFORM tests.assert(v_first.notify_org_id = 'org-5323'
        AND v_first.notify_tier = 'pro',
        'the claimed row must carry the enqueued org + tier snapshot the '
        'notification needs (the drain cannot re-read the Stripe event)');
    PERFORM tests.assert(v_first.notify_dead_at IS NULL,
        'a live row is claimed with a NULL dead marker');

    -- THE LEASE. Immediately after the claim the row is NOT due again, so a
    -- concurrent/next claimer gets nothing — that is what makes at-least-once
    -- not "once per drain".
    SELECT count(*) INTO v_second
      FROM public.claim_due_webhook_notifies(100, 120, 5) r
     WHERE r.event_id = '5323-evt-1';
    PERFORM tests.assert(v_second = 0,
        'the claim must LEASE the row (push notify_next_attempt_at forward); '
        'a second claim inside the lease must return nothing');

    -- The attempt failed: counted, backed off, still owed.
    v_outcome := public.resolve_webhook_notify(
        '5323-evt-1', false, 'resend 503', 60, 5);
    PERFORM tests.assert(v_outcome = 'retry',
        format('a failed attempt below the cap must report retry, got %L', v_outcome));
    SELECT notify_attempts, notify_next_attempt_at - now()
      INTO v_attempts, v_backoff
      FROM public.webhook_events WHERE event_id = '5323-evt-1';
    PERFORM tests.assert(v_attempts = 1, 'a failed attempt must be counted');
    PERFORM tests.assert(v_backoff > interval '0 seconds',
        'a retry must be scheduled in the FUTURE (backoff), never immediately');
    PERFORM tests.assert(
        (SELECT notified_at FROM public.webhook_events WHERE event_id = '5323-evt-1') IS NULL,
        'THE FIX: a failed attempt must leave the notification OWED, not lost');
    PERFORM tests.assert(
        (SELECT notify_dead_at FROM public.webhook_events WHERE event_id = '5323-evt-1') IS NULL,
        'a row below the cap is not dead');
    PERFORM tests.assert(
        (SELECT left(notify_last_error, 11) FROM public.webhook_events
          WHERE event_id = '5323-evt-1') = 'resend 503',
        'the last error must be recorded for triage');
    PERFORM tests.assert(
        (SELECT count(*) FROM public.claim_due_webhook_notifies(100, 120, 5) r
          WHERE r.event_id = '5323-evt-1') = 0,
        'a backed-off row must not be claimed before its retry time');
END $$;

-- ============================================================================
-- SECTION 6 — exhaustion is a DEAD, REPORTABLE record (never a silent one)
-- ============================================================================
-- At the cap the row flips to DEAD and becomes due IMMEDIATELY, so the very
-- next drain claims it — for REPORTING only. A dead row is never attempted
-- again (resolve refuses it), and it leaves the due set only once the drain
-- stamps notify_dead_reported_at.
DO $$
DECLARE
    v_outcome text;
    v_first   record;
    v_rows    integer;
BEGIN
    UPDATE public.webhook_events
       SET notify_attempts = 1, notify_next_attempt_at = now() - interval '1 second'
     WHERE event_id = '5323-evt-1';

    -- p_max_attempts = 2, and this is the second failure.
    v_outcome := public.resolve_webhook_notify(
        '5323-evt-1', false, 'resend 503 again', 60, 2);
    PERFORM tests.assert(v_outcome = 'dead',
        format('the attempt at the cap must report dead, got %L', v_outcome));
    PERFORM tests.assert(
        (SELECT notify_dead_at FROM public.webhook_events WHERE event_id = '5323-evt-1') IS NOT NULL,
        'exhaustion must stamp notify_dead_at (the DLQ state)');
    PERFORM tests.assert(
        (SELECT notify_next_attempt_at FROM public.webhook_events
          WHERE event_id = '5323-evt-1') <= now(),
        'a DEAD row must become due IMMEDIATELY — that is how it gets REPORTED '
        '(the drain claims dead rows for reporting, not for delivery)');

    -- A dead row is not attemptable: the settle refuses it outright.
    v_outcome := public.resolve_webhook_notify(
        '5323-evt-1', false, 'again', 60, 2);
    PERFORM tests.assert(v_outcome = 'missing',
        'a DEAD row must refuse further attempts (no infinite retry loop)');

    -- The drain claims exactly the dead row, flagged as dead.
    SELECT * INTO v_first
      FROM public.claim_due_webhook_notifies(100, 120, 5) r
     WHERE r.event_id = '5323-evt-1';
    PERFORM tests.assert(v_first.event_id IS NOT NULL,
        'a DEAD row must still be claimed — the drain owes it a report');
    PERFORM tests.assert(v_first.notify_dead_at IS NOT NULL,
        'the claimed dead row must carry notify_dead_at, so the drain knows to '
        'REPORT it instead of attempting it');

    -- The drain reports it and stamps the row, which is what makes the report
    -- once-at-least: a crash before this stamp re-reports next cycle, and the
    -- incident sink dedups on (kind, subject).
    UPDATE public.webhook_events SET notify_dead_reported_at = now()
     WHERE event_id = '5323-evt-1';

    SELECT count(*) INTO v_rows
      FROM public.claim_due_webhook_notifies(100, 120, 5) r
     WHERE r.event_id = '5323-evt-1';
    PERFORM tests.assert(v_rows = 0,
        'a reported dead row must leave the due set — otherwise every drain '
        'cycle would re-report it forever');

    -- And the dead state is DURABLE: it survives as a row an operator can read
    -- (the record does not depend on the alert channel being configured).
    PERFORM tests.assert(
        (SELECT count(*) FROM public.webhook_events
          WHERE event_id = '5323-evt-1' AND notify_dead_at IS NOT NULL
            AND notify_last_error IS NOT NULL) = 1,
        'the dead row must remain readable with its last error after reporting');
END $$;

-- ============================================================================
-- SECTION 7 — the batch bound
-- ============================================================================
-- p_limit is the drain's work bound; it must be honoured, or one outage's
-- backlog would be drained in a single unbounded pass.
DO $$
DECLARE
    v_rows integer;
BEGIN
    INSERT INTO public.webhook_events
        (event_id, type, notify_kind, notify_org_id, notify_tier,
         notify_next_attempt_at)
    SELECT '5323-batch-' || g, 'invoice.payment_failed', 'billing_payment_failed',
           'org-5323', 'pro', now() - interval '1 second'
      FROM generate_series(1, 5) AS g;

    SELECT count(*) INTO v_rows
      FROM public.claim_due_webhook_notifies(3, 120, 5) r
     WHERE r.event_id LIKE '5323-batch-%';
    PERFORM tests.assert(v_rows = 3,
        format('p_limit must bound the pass, claimed %s of 5', v_rows));
END $$;

-- ── Cleanup ────────────────────────────────────────────────────────────────
DELETE FROM public.webhook_events WHERE event_id LIKE '5323-%';

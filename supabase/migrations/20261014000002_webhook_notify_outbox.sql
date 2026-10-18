-- ============================================================================
-- Migration 20261014000002: the Stripe billing notification's DURABLE delivery
-- (#5323, "marker-as-outbox")
-- ----------------------------------------------------------------------------
-- THE DEFECT. `POST /webhooks/stripe` claims its idempotency row
-- (`public.webhook_events`, migration 0013) and only THEN hands the billing
-- notification to the #3498 offload seam (`_cp_offload(op="billing_notify",
-- best_effort=True)`). The seam routes best-effort work to the SHARED telemetry
-- pool (4 workers, `CONTROL_PLANE_TELEMETRY_BACKLOG=256`), whose `submit()`
-- fails fast on a full backlog — returning a pre-failed `_WorkerBacklogFull`
-- future so the callable NEVER RUNS. `_cp_offload(best_effort=True)` maps that
-- to `OFFLOAD_REFUSED`, and because the claim was already committed, Stripe's
-- retry sees `is_first=False` forever: the notification is lost PERMANENTLY.
-- (#4456 made the refusal LOUD — a rate-limited ERROR line plus a
-- platform-scoped incident; it did not make the delivery RECOVERABLE. This
-- migration is the delivery half.)
--
-- THE FIX. The marker row becomes an OUTBOX. Dispatch state lives on the SAME
-- row, written ATOMICALLY with the claim by `claim_webhook_event` below — one
-- INSERT, so there is no dual-write to lose (the pre-fix shape was a standalone
-- PostgREST read-then-insert in `tortoise/supabase_control.py`, which has no
-- transaction boundary: an enqueue written after it is the textbook dual-write
-- hazard the issue names). A drain (`claim_due_webhook_notifies` +
-- `resolve_webhook_notify`, driven from `hosted_api`'s boot + periodic sweep)
-- then retries every row whose notification was never settled. Delivery becomes
-- AT-LEAST-ONCE, and it is INDEPENDENT of the telemetry pool: a refused
-- hand-off is no longer a loss, only a delay.
--
-- STATE MACHINE (one row, `event_id` is the key)
--
--   notify_kind IS NULL                        nothing owed (the pre-#5323 rows,
--                                              and events with no notification)
--   notify_kind NOT NULL, notified_at NULL,    ENQUEUED — due at
--     notify_dead_at NULL                        notify_next_attempt_at
--   notified_at NOT NULL                       DELIVERED (settled)
--   notify_dead_at NOT NULL,                   DEAD (attempts exhausted) —
--     notify_dead_reported_at NULL               still "due" so the drain
--                                                reports it exactly once
--   notify_dead_reported_at NOT NULL           terminal, leaves the due set
--
-- WHY `notify_next_attempt_at` IS THE WHOLE LEASE. `claim_due_webhook_notifies`
-- pushes it forward by the caller's lease whenever it claims a row, so a drain
-- that dies mid-attempt leaves the row invisible until the lease expires and
-- the next cycle retries it. No separate `lease_until` column, and no
-- `in_flight` flag to strand.
--
-- WHY THE FIRST ATTEMPT IS DELAYED (the enqueue sets a GRACE). The webhook's own
-- request path submits the first attempt itself (it must not wait for a sweep),
-- so the row must not be claimable by a concurrent drain while that attempt is
-- in flight. `p_first_attempt_grace_seconds` covers the notify's worst case —
-- Resend's 15 s timeout plus Telegram's on their own 15 s — plus the offload
-- queue wait. A REFUSED first attempt simply leaves the row enqueued, and the
-- drain picks it up once the grace elapses: that is the fix.
--
-- POLICY LIVES IN PYTHON, NOT HERE. Every policy number (attempt cap, backoff,
-- lease, grace) is a PARAMETER supplied by `tortoise/billing_notify_outbox.py`,
-- so there is one declaration site for behaviour the tests can drive; the SQL
-- DEFAULTS exist only so the functions are callable by hand (psql diagnostics)
-- and are deliberately not read by the app. Same ruling as
-- `20261001000002`: "pricing lives in app code, never SQL".
--
-- SECURITY DEFINER + `SET search_path`, EXECUTE restricted to service_role.
-- These are billing-dispatch primitives; anon/authenticated must never reach
-- them (an INSERT-shaped function reachable by anon would be a forge-an-event
-- surface).
-- ============================================================================

-- ============================================================================
-- 1) the outbox columns on the existing marker row
-- ============================================================================
-- Additive only (`ADD COLUMN IF NOT EXISTS`), so the migration is idempotent and
-- every pre-#5323 row keeps its current meaning: `notify_kind IS NULL` says
-- "nothing owed", which is exactly what those rows meant before (their side
-- effects were already resolved or already lost).
ALTER TABLE public.webhook_events
    ADD COLUMN IF NOT EXISTS notify_kind             text,
    ADD COLUMN IF NOT EXISTS notify_org_id           text,
    ADD COLUMN IF NOT EXISTS notify_tier             text,
    ADD COLUMN IF NOT EXISTS notified_at             timestamptz,
    ADD COLUMN IF NOT EXISTS notify_attempts         integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS notify_next_attempt_at  timestamptz,
    ADD COLUMN IF NOT EXISTS notify_dead_at          timestamptz,
    ADD COLUMN IF NOT EXISTS notify_dead_reported_at timestamptz,
    ADD COLUMN IF NOT EXISTS notify_last_error       text;

-- The drain's predicate, as a PARTIAL index: the due set is a tiny minority of
-- the table (every settled row leaves it), so a full index would be mostly dead
-- weight on a table that grows with every Stripe delivery for the retention
-- window. The predicate is spelled exactly as `claim_due_webhook_notifies`
-- tests it, so the planner can use the index for that query.
CREATE INDEX IF NOT EXISTS idx_webhook_events_notify_due
    ON public.webhook_events (notify_next_attempt_at)
    WHERE notify_kind IS NOT NULL
      AND notified_at IS NULL
      AND notify_dead_reported_at IS NULL;

COMMENT ON COLUMN public.webhook_events.notify_kind IS
    'The billing-notification kind ENQUEUED with the claim (NULL = nothing owed). #5323 outbox.';
COMMENT ON COLUMN public.webhook_events.notified_at IS
    'When the notification was settled as delivered. NULL + notify_kind NOT NULL = still owed. #5323 outbox.';
COMMENT ON COLUMN public.webhook_events.notify_next_attempt_at IS
    'Due time AND in-flight lease: the claim RPC pushes it forward so a drain that dies mid-attempt is retried after the lease. #5323 outbox.';
COMMENT ON COLUMN public.webhook_events.notify_dead_at IS
    'Attempts exhausted (DLQ). Still due until notify_dead_reported_at is set, so the drain reports it at least once. #5323 outbox.';

-- ============================================================================
-- 2) claim_webhook_event — the ATOMIC claim + enqueue
-- ============================================================================
-- One INSERT, so "is this event first-seen?" and "is its notification owed?"
-- are the same commit. `ON CONFLICT DO NOTHING` is the whole CAS: the PRIMARY
-- KEY on `event_id` (0013) makes the loser of a concurrent double-delivery a
-- zero-row statement, so it can NEVER double-enqueue (the pre-fix read-then-
-- insert raised on the PK violation instead, turning a duplicate delivery into
-- a 500 that Stripe then retried — a claim is idempotent, a raise is not).
--
-- `RETURNING true INTO v_claimed` + `coalesce(v_claimed, false)` is how a
-- zero-row INSERT reports FALSE: PL/pgSQL assigns NULL when the statement
-- returns no rows.
CREATE OR REPLACE FUNCTION public.claim_webhook_event(
    p_event_id                     text,
    p_type                         text,
    p_notify_kind                  text DEFAULT NULL,
    p_notify_org_id                text DEFAULT NULL,
    p_notify_tier                  text DEFAULT NULL,
    p_first_attempt_grace_seconds  integer DEFAULT 90
) RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_claimed boolean;
BEGIN
    INSERT INTO public.webhook_events (
        event_id,
        type,
        notify_kind,
        notify_org_id,
        notify_tier,
        notify_next_attempt_at
    ) VALUES (
        p_event_id,
        p_type,
        p_notify_kind,
        p_notify_org_id,
        p_notify_tier,
        -- NULL for a side-effect-free event: nothing is owed, so nothing is due.
        CASE
            WHEN p_notify_kind IS NULL THEN NULL
            ELSE now() + make_interval(secs => p_first_attempt_grace_seconds)
        END
    )
    ON CONFLICT (event_id) DO NOTHING
    RETURNING true INTO v_claimed;

    RETURN coalesce(v_claimed, false);
END $$;

COMMENT ON FUNCTION public.claim_webhook_event(text, text, text, text, text, integer) IS
    'ATOMIC first-seen claim + notification ENQUEUE for a Stripe webhook event (#5323): one INSERT, so the outbox row cannot be lost between the claim and the enqueue the way the pre-fix read-then-insert could. Returns TRUE when this call claimed the event.';

REVOKE ALL ON FUNCTION public.claim_webhook_event(text, text, text, text, text, integer)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_webhook_event(text, text, text, text, text, integer)
    TO service_role;

-- ============================================================================
-- 3) claim_due_webhook_notifies — the drain's atomic batch claim
-- ============================================================================
-- `FOR UPDATE SKIP LOCKED` inside the CTE, then the UPDATE through it: one
-- statement, so two concurrent drains (a boot sweep overlapping the periodic
-- one, or a second process mid-rollout) can never claim the same row — the
-- second gets the NEXT due rows instead. That is what keeps at-least-once from
-- becoming "twice on every deploy".
--
-- The predicate has TWO arms on purpose:
--   * `notify_dead_at IS NULL AND notify_attempts < p_max_attempts`  — an
--     attempt is still allowed;
--   * `notify_dead_at IS NOT NULL`                                   — the row is
--     DEAD and is claimed so the drain can REPORT it (and then stamp
--     `notify_dead_reported_at`, which takes it out of this set).
-- A dead row is therefore never attempted again, but its terminal state is
-- durably announced — the `#4872` requirement that a failure record is not
-- silent. A crash between the report and the stamp simply re-reports next
-- cycle, and the incident sink dedups on `(kind, subject)`.
--
-- `notify_next_attempt_at` is pushed forward for BOTH arms (the lease), so a
-- claimer that dies mid-report cannot strand the row.
CREATE OR REPLACE FUNCTION public.claim_due_webhook_notifies(
    p_limit          integer DEFAULT 10,
    p_lease_seconds  integer DEFAULT 120,
    p_max_attempts   integer DEFAULT 5
) RETURNS TABLE (
    event_id        text,
    type            text,
    notify_kind     text,
    notify_org_id   text,
    notify_tier     text,
    notify_attempts integer,
    notify_dead_at  timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    RETURN QUERY
    WITH due AS (
        SELECT c.event_id
          FROM public.webhook_events c
         WHERE c.notify_kind IS NOT NULL
           AND c.notified_at IS NULL
           AND c.notify_dead_reported_at IS NULL
           AND (c.notify_next_attempt_at IS NULL
                OR c.notify_next_attempt_at <= now())
           AND (c.notify_dead_at IS NOT NULL
                OR c.notify_attempts < p_max_attempts)
         ORDER BY c.notify_next_attempt_at NULLS FIRST
         LIMIT p_limit
           FOR UPDATE SKIP LOCKED
    )
    UPDATE public.webhook_events w
       SET notify_next_attempt_at = now() + make_interval(secs => p_lease_seconds)
      FROM due
     WHERE w.event_id = due.event_id
    RETURNING w.event_id, w.type, w.notify_kind, w.notify_org_id, w.notify_tier,
              w.notify_attempts, w.notify_dead_at;
END $$;

COMMENT ON FUNCTION public.claim_due_webhook_notifies(integer, integer, integer) IS
    'Atomically lease ONE batch of due billing-notification outbox rows for the drain (#5323): FOR UPDATE SKIP LOCKED, so concurrent drains never deliver the same row. Also leases DEAD rows so their terminal state is reported once.';

REVOKE ALL ON FUNCTION public.claim_due_webhook_notifies(integer, integer, integer)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_due_webhook_notifies(integer, integer, integer)
    TO service_role;

-- ============================================================================
-- 4) resolve_webhook_notify — settle ONE attempt
-- ============================================================================
-- The single settle door for both callers: the webhook's request path (which
-- attempts the first delivery itself) and the drain. Returns a TEXT outcome
-- rather than a boolean so the caller can tell "delivered" from "no such row"
-- without a second read:
--
--   'delivered' — settled; the row leaves the due set
--   'retry'     — the attempt failed; the row is due again after a backoff
--   'dead'      — the attempt failed and the cap is reached; DLQ
--   'missing'   — no such row, or it was ALREADY settled by another caller
--                 (the request path and the drain racing on the same row)
--
-- The `WHERE ... AND notified_at IS NULL` guards make the settle idempotent:
-- a second settle of a delivered row is a no-op that reports 'missing', never a
-- regression to "owed". `notify_attempts = notify_attempts + 1` is read-modify-
-- write INSIDE the UPDATE at READ COMMITTED, so two overlapping settles
-- serialize on the row lock and both increments land.
--
-- THE HONEST LIMIT OF THIS STATE MACHINE (stated, not hidden): the notify
-- itself is documented never-raise (`tortoise/notify.py`), so "the callable ran"
-- is what 'delivered' records — a per-CHANNEL failure inside the notification
-- (Resend refused, Telegram down) is surfaced by that module's own incident
-- paths, NOT retried here. The outbox closes the "the callable never ran"
-- loss, which is the loss this issue measured. A settle that FAILS after the
-- notification went out leaves the row owed, so a later drain re-delivers it:
-- at-least-once, i.e. a duplicate billing email in that window rather than a
-- silent loss. Bounded by `p_max_attempts`.
CREATE OR REPLACE FUNCTION public.resolve_webhook_notify(
    p_event_id         text,
    p_delivered        boolean,
    p_error            text    DEFAULT NULL,
    p_backoff_seconds  integer DEFAULT 60,
    p_max_attempts     integer DEFAULT 5
) RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_attempts integer;
BEGIN
    IF p_delivered THEN
        UPDATE public.webhook_events
           SET notified_at             = now(),
               notify_last_error       = NULL,
               notify_next_attempt_at  = NULL
         WHERE event_id = p_event_id
           AND notified_at IS NULL
        RETURNING notify_attempts INTO v_attempts;

        IF NOT FOUND THEN
            RETURN 'missing';
        END IF;
        RETURN 'delivered';
    END IF;

    UPDATE public.webhook_events
       SET notify_attempts = notify_attempts + 1,
           notify_last_error = left(p_error, 500),
           -- Exhaustion flips the row to DEAD and makes it due IMMEDIATELY, so
           -- the very next drain claims it and reports it (see arm 2 of the
           -- drain's predicate). Otherwise the retry waits out the backoff.
           notify_dead_at = CASE
               WHEN notify_attempts + 1 >= p_max_attempts THEN now()
               ELSE notify_dead_at
           END,
           notify_next_attempt_at = CASE
               WHEN notify_attempts + 1 >= p_max_attempts THEN now()
               ELSE now() + make_interval(secs => p_backoff_seconds)
           END
     WHERE event_id = p_event_id
       AND notified_at IS NULL
       AND notify_dead_at IS NULL
    RETURNING notify_attempts INTO v_attempts;

    IF NOT FOUND THEN
        RETURN 'missing';
    END IF;
    IF v_attempts >= p_max_attempts THEN
        RETURN 'dead';
    END IF;
    RETURN 'retry';
END $$;

COMMENT ON FUNCTION public.resolve_webhook_notify(text, boolean, text, integer, integer) IS
    'Settle ONE billing-notification attempt (#5323 outbox): delivered -> notified_at; failed -> attempts+1 with backoff, or DEAD at the cap. Idempotent (a second settle reports missing rather than re-arming the row).';

REVOKE ALL ON FUNCTION public.resolve_webhook_notify(text, boolean, text, integer, integer)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.resolve_webhook_notify(text, boolean, text, integer, integer)
    TO service_role;

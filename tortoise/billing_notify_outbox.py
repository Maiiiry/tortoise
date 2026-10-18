"""#5323 — the Stripe billing notification's DURABLE delivery (marker-as-outbox).

THE DEFECT THIS CLOSES. ``webhooks_stripe`` (``tortoise/hosted_api.py``) claims
its idempotency row FIRST (``webhook_events``, migration 0013) and only THEN
hands the billing notification to the #3498 offload seam
(``_cp_offload(op="billing_notify", best_effort=True)``). Best-effort work runs
on the SHARED telemetry pool — 4 workers, ``CONTROL_PLANE_TELEMETRY_BACKLOG``
slots — and ``_SingleSlotWorker.submit`` FAILS FAST on a full backlog, returning
a pre-failed ``_WorkerBacklogFull`` future so the callable NEVER RUNS.
``_cp_offload`` maps that to :data:`tortoise.hosted_api.OFFLOAD_REFUSED`, and
because the claim is already committed, Stripe's retry sees ``is_first=False``
forever: the notification is lost PERMANENTLY.

#4456 made that refusal LOUD (a rate-limited ERROR line plus a platform-scoped
incident); it did not make the delivery RECOVERABLE. This module is the delivery
half. It is deliberately NOT a second worker pool: a bigger backlog only shrinks
the window, and a crash between the claim and the hand-off loses the
notification with no pool involved at all.

THE DESIGN — the marker row IS the outbox
-----------------------------------------
Dispatch state lives on the SAME ``webhook_events`` row, written ATOMICALLY with
the claim (``claim_webhook_event``, migration 20261014000002): one INSERT, so
there is no second write to lose. That atomicity is the whole point — the
pre-fix shape was a standalone PostgREST read-then-insert, so an enqueue written
"after the claim" would have been the textbook dual-write hazard the issue
names.

Delivery itself is unchanged in shape and placement: the request path still
SUBMITS the first attempt through the #3498 seam (off the event loop, bounded,
never able to fail the webhook). What changes is what a refusal MEANS. The row
stays ENQUEUED, and :func:`drain` — armed at boot and on a short periodic
interval (`hosted_api._sweep_billing_notify_outbox`) — retries it. The
notification is therefore AT-LEAST-ONCE and no longer coupled to the telemetry
pool: a saturated pool delays a billing notification instead of destroying it,
and a process death between the claim and the attempt is recovered by the next
drain.

STATE MACHINE (see the migration for the SQL form)
--------------------------------------------------
``notify_kind`` NULL                  nothing owed (pre-#5323 rows, and events
                                      with no notification)
``notify_kind`` NOT NULL,
``notified_at`` NULL,
``notify_dead_at`` NULL                ENQUEUED — due at ``notify_next_attempt_at``
``notified_at`` NOT NULL               DELIVERED (settled)
``notify_dead_at`` NOT NULL            DEAD (the attempt cap was reached) —
``notify_dead_reported_at`` NULL       still due, so the drain REPORTS it
``notify_dead_reported_at`` NOT NULL   terminal

``notify_next_attempt_at`` is both the RETRY GATE and the IN-FLIGHT LEASE: the
claim that hands a row out pushes it forward, so whatever claims the row next
waits for the lease instead of racing the attempt that is already running. No
``in_flight`` flag to strand, no separate lease column.

THE HONEST LIMITS (stated, not hidden)
--------------------------------------
1. **"Delivered" means the callable RAN.** ``notify_billing_event`` is
   documented never-raise and handles its own per-channel failures (Resend
   files ``BILLING_SEND_FAILED``; Telegram logs). Re-driving an individual
   CHANNEL is that module's contract, not this one's; the loss this module
   closes is "the callable never ran".
2. **At-least-once, so a duplicate is possible.** If the notification goes out
   but its settle write fails (a control-plane outage — the very coupling this
   removes), the row stays owed and a later drain re-delivers it. A duplicate
   ops email in that window is the correct trade against a silent loss; it is
   bounded by the attempt cap.
3. **The registry (selfhost) twin is NOT as strong as the SQL one.** FalkorDB
   has no row locks and no statement-spanning transaction, so
   :class:`RegistryOutbox` cannot reproduce ``FOR UPDATE SKIP LOCKED``; it
   leases what it returns and that lease is what makes a re-claim rare rather
   than impossible. The DRAIN is one boot + one periodic runner per process, so
   a real concurrent claim needs two live processes against the same embedded
   graph — the SQL path has no such caveat.

WHO OWNS THE POLICY. Every number (attempt cap, backoff, lease, grace) is
declared HERE and passed into the store, so the SQL functions carry no behaviour
the tests cannot drive — the same ruling as ``20261001000002`` ("pricing lives
in app code, never SQL").
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta

logger = logging.getLogger("tortoise.billing_notify_outbox")

#: How many delivery attempts a row gets before it is DEAD. Small on purpose:
#: the common refusal is a transient pool saturation, and the notification's
#: value decays fast (an operator alert about a plan change). Five attempts with
#: the backoff below spans roughly an hour of retrying.
MAX_ATTEMPTS = 5

#: Exponential backoff base / cap for a failed attempt (`backoff_seconds`).
BACKOFF_BASE_S = 60
MAX_BACKOFF_S = 3600

#: The LEASE a claim puts on the rows it hands out. Must exceed ONE attempt's
#: worst case — Resend's 15 s timeout plus Telegram's on their own 15 s, plus the
#: settle write — or a drain that is still delivering could see its own row
#: claimed again.
ATTEMPT_LEASE_S = 120

#: The GRACE an enqueue sets before the row is first due. The webhook's request
#: path submits the first attempt itself (a notification must not wait for a
#: sweep) and the offload seam may QUEUE it; until the grace elapses the drain
#: must not touch the row, or the queued attempt and the drain would both
#: deliver. A refused first attempt simply waits out the grace.
FIRST_ATTEMPT_GRACE_S = 90

#: Rows claimed per drain pass — the drain's work bound.
DRAIN_BATCH = 10

#: The drain's default cadence (seconds). SHORT on purpose: a due row exists
#: because a delivery FAILED, and what it carries is an operator notification
#: about a billing event — the retention loop's hourly cadence is sized for GC,
#: not for a queue. A pass with nothing due is one indexed query.
DRAIN_INTERVAL_DEFAULT_S = 60

#: Settle outcomes (the store's return vocabulary, plus one this module adds).
SETTLE_DELIVERED = "delivered"
SETTLE_RETRY = "retry"
SETTLE_DEAD = "dead"
SETTLE_MISSING = "missing"
#: The notification ran (or raised) but the settle write itself failed: the row
#: is still owed. Not a store outcome — the store raised.
SETTLE_UNSETTLED = "unsettled"


def backoff_seconds(attempt: int) -> int:
    """The delay before retry after the Nth FAILED attempt (1-based), capped.

    A bounded exponential, not a fixed delay: a control-plane settle outage that
    lasts a minute recovers on the first or second retry, while a dependency that
    is wedged for the rest of the hour converges on the cap instead of being
    hammered once a minute for the whole attempt budget.
    """
    n = max(1, int(attempt))
    return min(MAX_BACKOFF_S, BACKOFF_BASE_S * (2 ** (n - 1)))


def _now() -> datetime:
    return datetime.now(UTC)


def drain_interval_s() -> int:
    """Validate ``TORTOISE_BILLING_NOTIFY_DRAIN_INTERVAL`` to a positive int.

    Same rule as ``monitoring.event_retention_interval``, and for the same
    reason: ``asyncio.sleep(0)`` (or a negative value) returns immediately, so a
    zero interval would turn the drain into a HOT RETRY LOOP against the control
    plane — the failure mode the cadence exists to bound. Anything that is not a
    positive whole number of seconds falls back to
    :data:`DRAIN_INTERVAL_DEFAULT_S` with a warning.
    """
    raw = os.environ.get("TORTOISE_BILLING_NOTIFY_DRAIN_INTERVAL")
    if raw is None or not str(raw).strip():
        return DRAIN_INTERVAL_DEFAULT_S
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning(
            "TORTOISE_BILLING_NOTIFY_DRAIN_INTERVAL=%r is not a whole number "
            "of seconds — using %ds", raw, DRAIN_INTERVAL_DEFAULT_S)
        return DRAIN_INTERVAL_DEFAULT_S
    if value < 1:
        logger.warning(
            "TORTOISE_BILLING_NOTIFY_DRAIN_INTERVAL=%r is not a positive "
            "number of seconds — using %ds; a zero interval makes "
            "asyncio.sleep() return immediately, so the drain would retry in a "
            "hot loop",
            raw, DRAIN_INTERVAL_DEFAULT_S)
        return DRAIN_INTERVAL_DEFAULT_S
    return value


def _iso(dt: datetime) -> str:
    """A FIXED-WIDTH UTC timestamp.

    The registry twin stores timestamps as node properties and FalkorDB has no
    ``timestamptz``, so the drain compares them LEXICOGRAPHICALLY. A
    variable-width ``isoformat()`` drops the microseconds when they are zero, and
    ``'…T12:00:00+00:00' < '…T12:00:00.000123+00:00'`` happens to hold ('+' sorts
    below '.') — correct by luck of the offset spelling, which is not a contract.
    Fixed width makes the ordering a property of the format.
    """
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def _redact(exc: BaseException) -> str:
    """A redacted error string for logs and the outbox row."""
    try:
        from tortoise.notify import redact_safe
        return redact_safe(exc)
    except Exception:  # pragma: no cover - the notifier module is importable
        from tortoise.security import redact_error
        return redact_error(exc)


def _row_attempts(row: dict) -> int:
    try:
        return int(row.get("notify_attempts") or 0)
    except (TypeError, ValueError):
        return 0


# ── the stores ─────────────────────────────────────────────────────────────
#
# Two implementations of ONE interface, because the two deployment modes have
# different substrates and the drain must not care which it is talking to:
#
#   claim(event_id, etype, notify_kind, org_id, tier) -> bool
#   claim_due(limit, max_attempts) -> list[row dict]
#   settle(event_id, delivered, error, attempt, max_attempts) -> str
#   mark_reported(event_id) -> None
#
# A `row dict` carries the SNAPSHOT the drain needs to deliver: the notification
# cannot be rebuilt from the event id alone (the org and the tier are not stored
# on the Stripe event), which is why `claim` writes them at enqueue time and
# `claim_due` returns them.
#
# BOTH stores raise on a substrate failure. The callers in this module are the
# only ones that decide what a raise MEANS (the webhook must never 500 on a
# notification; the drain must never die), so the raise/return split is
# deliberate: the store reports, the caller tolerates.


class SupabaseOutbox:
    """The hosted store — three SECURITY DEFINER RPCs (migration 20261014000002).

    The claim is a single INSERT (``ON CONFLICT DO NOTHING``), so the claim and
    the enqueue cannot be separated by a crash; the drain claim is
    ``FOR UPDATE SKIP LOCKED``, so two concurrent drains cannot take the same
    row.
    """

    def __init__(self, cp):
        self._cp = cp

    def claim(self, *, event_id: str, etype: str, notify_kind: str | None = None,
              org_id: str | None = None, tier: str | None = None) -> bool:
        return bool(self._cp.rpc_value("claim_webhook_event", {
            "p_event_id": event_id,
            "p_type": etype,
            "p_notify_kind": notify_kind,
            "p_notify_org_id": org_id,
            "p_notify_tier": tier,
            "p_first_attempt_grace_seconds": FIRST_ATTEMPT_GRACE_S,
        }))

    def claim_due(self, *, limit: int, max_attempts: int) -> list[dict]:
        rows = self._cp.rpc_value("claim_due_webhook_notifies", {
            "p_limit": limit,
            "p_lease_seconds": ATTEMPT_LEASE_S,
            "p_max_attempts": max_attempts,
        })
        if not isinstance(rows, list):
            # A non-list body where rows were expected is a contract break, not
            # an empty queue: raise so the drain reports it instead of reading
            # "nothing due" (the #3665/#3025 lesson about suppressed bodies).
            raise RuntimeError(
                "claim_due_webhook_notifies returned "
                f"{type(rows).__name__}, expected a row list")
        return [r for r in rows if isinstance(r, dict)]

    def settle(self, *, event_id: str, delivered: bool, error: str | None,
               attempt: int, max_attempts: int) -> str:
        out = self._cp.rpc_value("resolve_webhook_notify", {
            "p_event_id": event_id,
            "p_delivered": delivered,
            "p_error": error,
            "p_backoff_seconds": backoff_seconds(attempt),
            "p_max_attempts": max_attempts,
        })
        return out if isinstance(out, str) else SETTLE_MISSING

    def mark_reported(self, *, event_id: str) -> None:
        self._cp.query(
            "webhook_events",
            method="PATCH",
            filters=[("event_id", "eq", event_id),
                     ("notify_dead_reported_at", "is", None)],
            json_body={"notify_dead_reported_at": _iso(_now())},
        )


class RegistryOutbox:
    """The selfhost twin — the SAME state machine over the registry graph.

    The marker for a Stripe event in registry mode is the ``:WebhookEvent`` node
    (migration 0013's Supabase twin), and this class adds the dispatch
    properties to it. It is written as Cypher rather than through an SDK method
    because it IS the marker write the webhook already made inline.

    FIDELITY (see the module docstring, limit 3): the claim is ONE ``MERGE``
    with ``ON CREATE SET``, so a first delivery cannot create a second node for
    the same event id — but the graph has no ``SKIP LOCKED``, so the LEASE in
    ``claim_due`` is what keeps a concurrent drain off a row another drain is
    already delivering. A duplicate delivery under a real concurrent claim is
    possible here and is the documented at-least-once residue.
    """

    #: Atomic claim + enqueue. The claim TOKEN identifies OUR create: the node
    #: carries the token iff this call created it, so a replay (MERGE matching an
    #: existing node) reads back a different or absent token and gets False. This
    #: is the graph's stand-in for ``ON CONFLICT DO NOTHING RETURNING`` — a
    #: read-then-create could not tell the two apart, and that gap is the
    #: pre-existing non-atomicity of the twin.
    _CLAIM = (
        "MERGE (w:WebhookEvent {event_id:$id}) "
        "ON CREATE SET w.first_seen=$now, w.type=$type, w.notify_kind=$kind, "
        "w.notify_org_id=$org, w.notify_tier=$tier, w.notify_attempts=0, "
        "w.notify_next_attempt_at=$due, w.notify_claim_token=$token "
        "RETURN w.notify_claim_token"
    )

    #: The drain's batch claim + lease, in ONE statement. `WITH … ORDER BY …
    #: LIMIT` selects the batch and `SET` leases exactly those rows, so the
    #: returned set and the leased set cannot differ (a `RETURN … LIMIT` after a
    #: SET over the whole match would lease rows nobody received).
    _CLAIM_DUE = (
        "MATCH (w:WebhookEvent) "
        "WHERE w.notify_kind IS NOT NULL AND w.notified_at IS NULL "
        "AND w.notify_dead_reported_at IS NULL "
        "AND w.notify_next_attempt_at IS NOT NULL "
        "AND w.notify_next_attempt_at <= $now "
        "AND (w.notify_dead_at IS NOT NULL "
        "     OR coalesce(w.notify_attempts, 0) < $max) "
        "WITH w ORDER BY w.notify_next_attempt_at ASC LIMIT $limit "
        "SET w.notify_next_attempt_at = $lease "
        "RETURN w.event_id, w.type, w.notify_kind, w.notify_org_id, "
        "w.notify_tier, coalesce(w.notify_attempts, 0), w.notify_dead_at"
    )

    _SETTLE_DELIVERED = (
        "MATCH (w:WebhookEvent {event_id:$id}) "
        "WHERE w.notified_at IS NULL "
        "SET w.notified_at=$now "
        "RETURN w.event_id"
    )

    #: The failed attempt. The DEAD flip is a SEPARATE statement rather than a
    #: `CASE` inside one `SET`: a property assigned NULL is REMOVED in Cypher, so
    #: a single statement would have to set `notify_dead_at=NULL` on every
    #: non-terminal attempt — relying on null-set semantics for the state
    #: machine. Two statements keep "the property is absent" out of the design.
    #: (The cost is that the increment and the flip are not one atomic step here;
    #: the SQL store does both inside its UPDATE.)
    _SETTLE_FAILED = (
        "MATCH (w:WebhookEvent {event_id:$id}) "
        "WHERE w.notified_at IS NULL AND w.notify_dead_at IS NULL "
        "SET w.notify_attempts=$attempts, w.notify_last_error=$err, "
        "w.notify_next_attempt_at=$next "
        "RETURN w.event_id"
    )

    _MARK_DEAD = (
        "MATCH (w:WebhookEvent {event_id:$id}) "
        "SET w.notify_dead_at=$now "
        "RETURN w.event_id"
    )

    _MARK_REPORTED = (
        "MATCH (w:WebhookEvent {event_id:$id}) "
        "WHERE w.notify_dead_reported_at IS NULL "
        "SET w.notify_dead_reported_at=$now "
        "RETURN w.event_id"
    )

    def __init__(self, sdk):
        self._sdk = sdk

    def _reg(self):
        return self._sdk._get_registry()

    def claim(self, *, event_id: str, etype: str, notify_kind: str | None = None,
              org_id: str | None = None, tier: str | None = None) -> bool:
        import uuid
        token = uuid.uuid4().hex
        rows = self._reg().query(self._CLAIM, params={
            "id": event_id,
            "now": _iso(_now()),
            "type": etype,
            "kind": notify_kind,
            "org": org_id,
            "tier": tier,
            "due": (_iso(_now() + timedelta(seconds=FIRST_ATTEMPT_GRACE_S))
                    if notify_kind else None),
            "token": token,
        }).result_set
        return bool(rows) and rows[0][0] == token

    def claim_due(self, *, limit: int, max_attempts: int) -> list[dict]:
        now = _now()
        rows = self._reg().query(self._CLAIM_DUE, params={
            "now": _iso(now),
            "lease": _iso(now + timedelta(seconds=ATTEMPT_LEASE_S)),
            "max": max_attempts,
            "limit": limit,
        }).result_set
        return [
            {
                "event_id": r[0],
                "type": r[1],
                "notify_kind": r[2],
                "notify_org_id": r[3],
                "notify_tier": r[4],
                "notify_attempts": r[5],
                "notify_dead_at": r[6],
            }
            for r in rows
        ]

    def settle(self, *, event_id: str, delivered: bool, error: str | None,
               attempt: int, max_attempts: int) -> str:
        if delivered:
            rows = self._reg().query(self._SETTLE_DELIVERED, params={
                "id": event_id, "now": _iso(_now())}).result_set
            return SETTLE_DELIVERED if rows else SETTLE_MISSING

        attempts = max(1, int(attempt)) + 1
        dead = attempts >= max_attempts
        now = _now()
        params = {
            "id": event_id,
            "attempts": attempts,
            "err": (error or "")[:500],
            "next": _iso(now + timedelta(seconds=backoff_seconds(attempts))),
        }
        rows = self._reg().query(self._SETTLE_FAILED, params=params).result_set
        if not rows:
            return SETTLE_MISSING
        if dead:
            self._reg().query(self._MARK_DEAD, params={
                "id": event_id, "now": _iso(now)})
            return SETTLE_DEAD
        return SETTLE_RETRY

    def mark_reported(self, *, event_id: str) -> None:
        self._reg().query(self._MARK_REPORTED, params={
            "id": event_id, "now": _iso(_now())})


# ── delivery ───────────────────────────────────────────────────────────────


def deliver(store, *, event_id: str, etype: str | None,
            notify_kind: str | None, org_id: str | None,
            tier: str | None, attempt: int = 0,
            max_attempts: int | None = None) -> str:
    """ONE delivery attempt for ONE outbox row, followed by its settle.

    NEVER raises — every exit is an outcome the callers count:

    * :data:`SETTLE_DELIVERED` — the notification ran and the row is settled;
    * :data:`SETTLE_RETRY` — it raised; the row is due again after a backoff;
    * :data:`SETTLE_DEAD` — it raised and the attempt cap is now reached;
    * :data:`SETTLE_MISSING` — the row was already settled by someone else
      (the request path and the drain racing on the same row);
    * :data:`SETTLE_UNSETTLED` — the settle write itself failed, so the row is
      STILL OWED and a later drain will deliver it again (at-least-once).

    ``attempt`` is how many attempts this row has ALREADY failed (0 for the
    request path's first try); the backoff is derived from ``attempt + 1``.
    """
    cap = MAX_ATTEMPTS if max_attempts is None else max_attempts
    try:
        from tortoise.notify import notify_billing_event
        # The ORDER is #4015's and unchanged: the notification's payload is the
        # snapshot the claim recorded (org + tier), and ``subscription_status``
        # is the Stripe event type — the same three arguments the inline call
        # used before it moved behind this state machine.
        notify_billing_event(
            notify_kind,
            {"org_id": org_id, "tier": tier},
            {"subscription_status": etype},
        )
    except Exception as exc:  # noqa: BLE001 — a notifier that raised is a retry
        return _settle(store, event_id, delivered=False, error=_redact(exc),
                       attempt=attempt, max_attempts=cap)
    return _settle(store, event_id, delivered=True, error=None,
                   attempt=attempt, max_attempts=cap)


def _settle(store, event_id: str, *, delivered: bool, error: str | None,
            attempt: int, max_attempts: int) -> str:
    """Write the attempt's outcome; a substrate failure is NOT a raise.

    A settle that fails after a SUCCESSFUL notification leaves the row owed, so
    the next drain delivers it a second time — the duplicate half of
    at-least-once, and the right side of the trade against a silent loss. It is
    logged at WARNING (not swallowed silently) because it is the one path that
    can produce a duplicate ops email.
    """
    try:
        return store.settle(event_id=event_id, delivered=delivered, error=error,
                            attempt=attempt, max_attempts=max_attempts)
    except Exception as exc:  # noqa: BLE001 — the delivery path never raises
        logger.warning(
            "billing notify outbox: could not settle event=%s "
            "(delivered=%s) — the row stays owed and the drain will attempt it "
            "again (at-least-once, so a duplicate is possible): %s",
            event_id, delivered, _redact(exc))
        return SETTLE_UNSETTLED


# ── the drain (the missing caller half) ────────────────────────────────────


def report_dead(store, row: dict) -> bool:
    """Escalate a DEAD row, then stamp it reported. NEVER raises.

    The stamp is what makes the report at-least-once rather than unbounded: a
    crash between the escalation and the stamp re-reports on the next cycle, and
    ``AlertStore`` dedups the incident on ``(kind, subject)``, so re-reporting
    is cheap and cannot file a second issue. That is why the order is escalate
    THEN stamp: the other order can lose the escalation for good.
    """
    event_id = row.get("event_id")
    org_id = row.get("notify_org_id")
    event_type = row.get("type")
    logger.error(
        "billing notify outbox: event=%s kind=%s org=%s is DEAD — the "
        "notification was never settled after the attempt cap; deliver it by "
        "hand (BILLING_NOTIFY_UNDELIVERED)",
        event_id, row.get("notify_kind"), org_id)
    try:
        from tortoise.operator_alert import alert_billing_notify_undelivered
        alert_billing_notify_undelivered(org_id, event_type)
    except Exception as exc:  # noqa: BLE001 — the alert must never raise
        logger.warning("billing notify outbox: dead-row escalation failed "
                       "(event=%s): %s", event_id, _redact(exc))
    try:
        store.mark_reported(event_id=event_id)
        return True
    except Exception as exc:  # noqa: BLE001 — the substrate may be down
        logger.warning(
            "billing notify outbox: could not stamp event=%s as reported — it "
            "stays due and the next cycle re-reports it (the incident sink "
            "dedups, so that is not a second issue): %s",
            event_id, _redact(exc))
        return False


def drain(store, *, limit: int = DRAIN_BATCH,
          max_attempts: int | None = None) -> dict:
    """Retry every due outbox row once, and report the dead ones.

    The sweep entry point. NEVER raises: a substrate failure (the claim, a
    settle, the report stamp) is logged and leaves the row in its current state
    for the next cycle. Returns a COUNTS dict — the caller logs it, so "the
    drain ran and did nothing" and "the drain could not reach the substrate" are
    distinguishable in the log instead of both reading as silence (#4872).
    """
    cap = MAX_ATTEMPTS if max_attempts is None else max_attempts
    counts = {
        "claimed": 0, "delivered": 0, "retry": 0, "dead": 0,
        "reported": 0, "unsettled": 0, "settled_elsewhere": 0,
    }
    try:
        rows = store.claim_due(limit=limit, max_attempts=cap)
    except Exception as exc:  # noqa: BLE001 — a sweep must never kill the loop
        logger.warning("billing notify outbox: could not claim due rows: %s",
                       _redact(exc))
        return counts

    for row in rows:
        counts["claimed"] += 1
        if row.get("notify_dead_at"):
            # Arm 2 of the claim predicate: a DEAD row is claimed for its
            # REPORT, never for another attempt.
            if report_dead(store, row):
                counts["reported"] += 1
            continue
        outcome = deliver(
            store,
            event_id=row.get("event_id"),
            etype=row.get("type"),
            notify_kind=row.get("notify_kind"),
            org_id=row.get("notify_org_id"),
            tier=row.get("notify_tier"),
            attempt=_row_attempts(row),
            max_attempts=cap,
        )
        if outcome == SETTLE_DEAD:
            counts["dead"] += 1
            # Report the transition NOW rather than leaving it for the next
            # cycle: the row is already DEAD, and an hour of silence between the
            # terminal state and its announcement is exactly the #4872 gap.
            report_dead(store, row)
        elif outcome == SETTLE_MISSING:
            counts["settled_elsewhere"] += 1
        elif outcome in counts:
            counts[outcome] += 1
    return counts

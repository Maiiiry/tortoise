"""Outbound webhook delivery for the session-event stream — #2081 (epic #2080 W4 phase 2).

The delivery half of Topology C in
``docs/research/2026-09-01-gbrain-learnings/delivery-shape.md``: apps subscribe
to the durable ``:GraphEvent`` stream (written by ``event_store`` on every
graph mutation), and this module pushes those events to the subscriber's URL.

WHY IT EXISTS. The subscriber path that ships today is PULL — ``GET /v1/events``
+ ``tortoise_events_poll`` (``read_after`` on a cursor). A third-party app that
cannot hold a poll loop (a serverless app, a harness hook) has no way to learn
that its memory changed. This is the push half: a subscription registry, a
dispatcher, retry/backoff, and delivery receipts.

THE CONTRACTS THIS MODULE PINS

1. **At-least-once, ordered, per subscription.** A subscription's ``cursor``
   advances ONLY after a 2xx. A crash between the POST and the cursor write
   re-delivers the same event, so the consumer MUST be idempotent — the
   ``X-Tortoise-Delivery`` header (``<sub_id>:<seq>``) is stable across
   re-deliveries and ``event_id`` is the durable event identity.
2. **No EP work on the push path.** ``dispatch_pending`` READS
   ``:GraphEvent`` rows; the payload is the durable event payload computed at
   write time. Nothing here calls ``compute_confidence`` or opens the EP
   engine, so a push can never re-run belief propagation (indicator 2 of
   #2081: a precomputed payload served from the store, never a per-push
   recompute). The *why-block assembly* half of the phase — enriching these
   events with support chains/NANDs — is deliberately NOT here; it belongs to
   the dream-cycle precompute follow-up so this module has no dependency on
   the EP engine.
3. **Best-effort, never a caller's failure.** ``dispatch_pending`` never
   raises: a subscriber's dead endpoint, a TLS failure, or a graph hiccup
   becomes a receipt and a backoff, never an exception on the capture path.
   Delivery runs at the END of a tenant's dream cycle (``hosted_api``
   ``_dream_worker``) and on demand; it is never awaited by a mutation.
4. **Retry is bounded, then the subscription is DISABLED.** Exponential
   backoff (``BASE_BACKOFF_S * 2**(attempt-1)``, capped at
   ``MAX_BACKOFF_S``); after ``max_attempts`` consecutive failures the
   subscription stops being dispatched (``disabled_at`` set with a reason) so
   one dead endpoint cannot consume dispatch capacity forever. Re-enabled by
   deleting and re-creating the subscription.
5. **Signing.** ``X-Tortoise-Signature: t=<unix>,v1=<hex>`` — HMAC-SHA256 over
   ``b"<t>." + body`` with the subscription secret (the Stripe scheme).
   ``verify_signature`` is the consumer-side half and is exported so the pilot
   app and its tests share one implementation. The secret is returned ONCE, at
   creation, and never by a read path; it is stored encrypted via
   ``crypto.encrypt_token`` (Fernet, ``TORTOISE_ENCRYPTION_KEY``), the same
   at-rest treatment as the OAuth tokens on the Org node.
6. **ONE delivery layer, MANY sinks (#8100).** ``transport`` on the subscription
   selects the SINK — ``webhook`` (default: POST to ``url``) or ``sse`` (the
   event is served on the stream the subscriber's client opens,
   ``sse_delivery``). The subscription registry, the drain (this module's
   ``dispatch_pending``), the cursor, the retry/backoff policy and the receipts
   are SHARED, so a new transport must not bring a second dispatcher:
   ``tests/test_sse_delivery.py`` pins that by enumerating ``tortoise/**``.

STORAGE (graph-native, per-graph = per-org — mirrors ``event_store``: the
namespace IS the partition, there is no ``org_id`` property):

* ``:ContextSubscription {sub_id, url, secret, types, description, created_at,
  created_by, transport, cursor, attempts, next_attempt_at, disabled_at,
  disabled_reason, delivered, failed, last_error, last_delivery_at}``
* ``:DeliveryReceipt {receipt_id, sub_id, event_id, seq, attempt, status,
  http_status, error, ts, duration_ms}`` — one per ATTEMPT, capped per
  subscription.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import time
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from tortoise.env_truthy import FALSY

from .ids import ulid
from .security import env_int, redact_error, validate_webhook_url

#: The transports this ONE layer can deliver over (#8100). ``transport`` on the
#: subscription selects the SINK; the dispatcher, cursor, retry policy and
#: receipts are shared, so a second name here must NOT bring a second drain
#: (``tests/test_sse_delivery.py`` pins that by enumeration).
TRANSPORT_WEBHOOK = "webhook"
TRANSPORT_SSE = "sse"
TRANSPORTS = (TRANSPORT_WEBHOOK, TRANSPORT_SSE)
#: A subscription created before #8100 carries no ``transport`` property; absent
#: MEANS webhook (not "unknown"), so every existing row keeps working untouched.
DEFAULT_TRANSPORT = TRANSPORT_WEBHOOK

logger = logging.getLogger("tortoise.webhook_delivery")

#: Kill switch (mirrors gbrain's ``GBRAIN_HOOKS=0``, the transferable contract
#: named in the delivery-shape research). Falsy value → ``dispatch_pending``
#: is a no-op; registration still works so a tenant can stage a subscription
#: before ops flips egress on.
KILL_SWITCH_ENV = "TORTOISE_WEBHOOK_DISPATCH"

#: Consecutive failures before a subscription is disabled (no further dispatch).
MAX_ATTEMPTS_DEFAULT = 8
#: First retry delay, doubled per consecutive failure.
BASE_BACKOFF_S = 30
#: Retry-delay ceiling (1h) — a subscriber down for a day is retried hourly.
MAX_BACKOFF_S = 3600
#: Per-request connect/read/write timeout for a delivery POST.
TIMEOUT_S = 10
#: Events a single subscription may move per dispatch pass.
DEFAULT_BATCH = 50
#: Subscriptions a single graph may hold (abuse bound on the fan-out surface).
MAX_SUBSCRIPTIONS = 25
#: Receipts retained per subscription (newest kept; older attempts pruned).
MAX_RECEIPTS = 500
#: Tolerated clock skew when a consumer verifies a signature.
SIGNATURE_TOLERANCE_S = 300

_SCHEMA_ATTR = "_tortoise_webhook_schema"

#: ``signature`` is deliberately NOT in this list — it is only ever read to
#: sign a delivery and must never reach a response.
_PUBLIC_SUB_FIELDS = (
    "sub_id", "url", "types", "description", "created_at", "created_by",
    "transport", "cursor", "attempts", "next_attempt_at", "disabled_at",
    "disabled_reason", "delivered", "failed", "last_error",
    "last_delivery_at",
)

#: The ONE instance of the webhook sink (stateless; kept module-level so the
#: resolver below never re-builds it per pass).
WEBHOOK_TRANSPORT = None  # set after the class definition (see ``transport_for``)


def dispatch_enabled() -> bool:
    """Is outbound webhook egress enabled? (kill switch; default ON)."""
    raw = os.environ.get(KILL_SWITCH_ENV)
    if raw is None:
        return True
    value = raw.strip().lower()
    # Share the canonical vocabulary instead of declaring a literal (#4097).
    # Semantics are unchanged and deliberately NOT `is_truthy`: this is a kill
    # switch, so an UNKNOWN value keeps egress ON rather than off.
    return value != "" and value not in FALSY


def backoff_seconds(attempt: int) -> int:
    """Delay before retry number ``attempt`` (1-based): 30s, 60s, … capped 1h."""
    attempt = max(1, int(attempt))
    # Cap the EXPONENT, not just the result: 2**(attempt-1) with a corrupt
    # stored attempt count would otherwise build a number big enough to
    # overflow/materalize before the min() could clamp it (the
    # ``capture_spool`` clamp, same reason).
    exponent = min(attempt - 1, 16)
    return min(BASE_BACKOFF_S * (2 ** exponent), MAX_BACKOFF_S)


def max_attempts() -> int:
    return env_int("TORTOISE_WEBHOOK_MAX_ATTEMPTS", MAX_ATTEMPTS_DEFAULT)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def _now() -> datetime:
    return datetime.now(UTC)


# ── Schema ────────────────────────────────────────────────────────────────

def ensure_subscription_schema(proj) -> None:
    """Create the exact-match indexes (idempotent, non-fatal; ``event_store`` pattern)."""
    if getattr(proj, _SCHEMA_ATTR, False):
        return
    for stmt in (
        "CREATE INDEX FOR (s:ContextSubscription) ON (s.sub_id)",
        "CREATE INDEX FOR (r:DeliveryReceipt) ON (r.sub_id)",
        "CREATE INDEX FOR (r:DeliveryReceipt) ON (r.receipt_id)",
    ):
        try:
            proj.g.query(stmt)
        except Exception:  # noqa: BLE001, RUF100
            logger.debug("webhook_delivery: index may already exist: %s", stmt)
    setattr(proj, _SCHEMA_ATTR, True)


# ── Registry ──────────────────────────────────────────────────────────────

def _public(props: dict) -> dict:
    out = {k: props.get(k) for k in _PUBLIC_SUB_FIELDS}
    # Absent MEANS webhook (every row created before #8100): a read must not
    # report ``None`` for a contract field the dispatcher always resolves.
    out["transport"] = str(props.get("transport") or DEFAULT_TRANSPORT)
    out["secret_set"] = bool(props.get("secret"))
    out["active"] = not props.get("disabled_at")
    out["types"] = list(props.get("types") or [])
    out["cursor"] = int(props.get("cursor") or 0)
    out["attempts"] = int(props.get("attempts") or 0)
    return out


def _validated_transport(transport) -> str:
    """Validate a requested transport against ``TRANSPORTS``.

    Absent/empty means the DEFAULT (webhook) so an older caller keeps working;
    an unknown name is a loud ``ValueError`` (422 over REST) rather than a silent
    fallback — a subscriber that asked for a sink we do not have must not be
    created as a webhook and left to fail every delivery.
    """
    if transport is None or (isinstance(transport, str) and not transport.strip()):
        return DEFAULT_TRANSPORT
    value = str(transport).strip().lower()
    if value not in TRANSPORTS:
        raise ValueError(
            f"unknown transport: {value} (expected one of {', '.join(TRANSPORTS)})")
    return value


def _validated_types(types) -> list[str]:
    """Validate an event-type filter against the registry (``events_poll`` contract)."""
    if not types:
        return []
    if isinstance(types, str):
        types = [t.strip() for t in types.split(",") if t.strip()]
    from .shared_state.events import event_types

    registered = event_types()
    unknown = [t for t in types if t not in registered]
    if unknown:
        raise ValueError(f"unknown event type: {unknown[0]}")
    return list(dict.fromkeys(types))


def create_subscription(proj, *, url: str | None = None, types=None,
                        description: str = "",
                        created_by: str | None = None,
                        transport: str = DEFAULT_TRANSPORT,
                        now: datetime | None = None) -> dict:
    """Register a delivery target. Returns the record INCLUDING the one-time ``secret``.

    ``transport`` selects the SINK (#8100): ``webhook`` (default) POSTs to
    ``url``; ``sse`` has no target of its own — the subscriber reads the stream
    its client opens — so its stored ``url`` is empty and NO SSRF validation
    runs (there is no egress to police).

    Raises ``ValueError`` for a rejected url / unknown event type / unknown
    transport, and ``RuntimeError`` when ``TORTOISE_ENCRYPTION_KEY`` is unset (a
    signing secret is a long-lived credential; it is never stored in the clear —
    ``crypto.encrypt_token`` fails loudly rather than silently downgrading).
    """
    transport = _validated_transport(transport)
    # For an SSE subscription the URL is deliberately NOT validated and NOT
    # stored (#8100): there is no outbound target — the subscriber reads the
    # stream its client opens — and keeping a caller-supplied URL here would
    # invite a future reader to POST to it, i.e. the second dispatcher this
    # issue forbids.
    url = validate_webhook_url(url) if transport == TRANSPORT_WEBHOOK else ""
    type_list = _validated_types(types)
    ensure_subscription_schema(proj)
    if count_subscriptions(proj, include_disabled=True) >= MAX_SUBSCRIPTIONS:
        raise ValueError(
            f"this graph already holds {MAX_SUBSCRIPTIONS} delivery "
            f"subscriptions — delete one before adding another")
    from .crypto import encrypt_token

    secret = "whsec_" + ulid().replace("-", "")[:32]
    sub_id = "sub_" + ulid()
    now_dt = now or _now()
    proj.g.query(
        "CREATE (s:ContextSubscription {"
        "sub_id:$sub_id, url:$url, secret:$secret, types:$types, "
        "description:$description, created_at:$created_at, "
        "created_by:$created_by, transport:$transport, "
        "cursor:0, attempts:0, delivered:0, failed:0})",
        params={
            "sub_id": sub_id, "url": url,
            "secret": encrypt_token(secret), "types": type_list,
            "description": str(description or "")[:500],
            "created_at": _iso(now_dt), "created_by": created_by,
            "transport": transport,
        },
    )
    record = get_subscription(proj, sub_id) or {}
    record["secret"] = secret  # one-time; read paths never carry it
    record["signature_header"] = "X-Tortoise-Signature"
    return record


def get_subscription(proj, sub_id: str) -> dict | None:
    rows = proj.g.query(
        "MATCH (s:ContextSubscription {sub_id:$id}) RETURN properties(s)",
        params={"id": sub_id},
    ).result_set
    if not rows:
        return None
    return _public(rows[0][0])


def list_subscriptions(proj) -> list[dict]:
    rows = proj.g.query(
        "MATCH (s:ContextSubscription) RETURN properties(s) "
        "ORDER BY s.created_at ASC"
    ).result_set
    return [_public(r[0]) for r in rows]


def count_subscriptions(proj, *, include_disabled: bool = False) -> int:
    where = "" if include_disabled else "WHERE s.disabled_at IS NULL "
    rows = proj.g.query(
        f"MATCH (s:ContextSubscription) {where}RETURN count(s)"
    ).result_set
    return int(rows[0][0]) if rows and rows[0][0] is not None else 0


def delete_subscription(proj, sub_id: str) -> bool:
    """Delete a subscription and its receipts. Returns False if it never existed."""
    rows = proj.g.query(
        "MATCH (s:ContextSubscription {sub_id:$id}) RETURN count(s)",
        params={"id": sub_id},
    ).result_set
    if not rows or not rows[0][0]:
        return False
    # Two statements, not one OPTIONAL MATCH: an OPTIONAL MATCH fans the row out
    # per receipt, so a `count(s)` RETURN would report the RECEIPT count and a
    # missing subscription could read as a hit once receipts existed.
    proj.g.query(
        "MATCH (s:ContextSubscription {sub_id:$id}) DETACH DELETE s",
        params={"id": sub_id},
    )
    proj.g.query(
        "MATCH (r:DeliveryReceipt {sub_id:$id}) DETACH DELETE r",
        params={"id": sub_id},
    )
    # The SSE wake state is process-local optimisation only (see sse_delivery);
    # dropping it is housekeeping, never correctness — a failure here must not
    # turn a successful delete into an error.
    try:
        from .sse_delivery import BROKER

        BROKER.forget(sub_id)
    except Exception:  # noqa: BLE001, RUF100
        logger.debug("webhook_delivery: broker forget failed for %s", sub_id)
    return True


def read_receipts(proj, sub_id: str, limit: int = 50) -> list[dict]:
    """Delivery attempts for a subscription, NEWEST first."""
    limit = max(1, min(int(limit), 200))
    rows = proj.g.query(
        "MATCH (r:DeliveryReceipt {sub_id:$id}) RETURN properties(r) "
        "ORDER BY r.ts DESC, r.receipt_id DESC LIMIT $limit",
        params={"id": sub_id, "limit": limit},
    ).result_set
    return [r[0] for r in rows]


def _write_receipt(proj, *, sub_id: str, event: dict, attempt: int, status: str,
                   http_status: int | None, error: str | None,
                   now: datetime, duration_ms: int) -> None:
    ensure_subscription_schema(proj)
    proj.g.query(
        "CREATE (r:DeliveryReceipt {receipt_id:$rid, sub_id:$sid, "
        "event_id:$eid, seq:$seq, attempt:$attempt, status:$status, "
        "http_status:$http, error:$error, ts:$ts, duration_ms:$ms})",
        params={
            "rid": "rcp_" + ulid(), "sid": sub_id,
            "eid": event.get("event_id"), "seq": int(event.get("seq") or 0),
            "attempt": int(attempt), "status": status, "http": http_status,
            "error": error, "ts": _iso(now), "ms": int(duration_ms),
        },
    )
    # Prune: keep the newest MAX_RECEIPTS for this subscription.
    try:
        proj.g.query(
            "MATCH (r:DeliveryReceipt {sub_id:$sid}) "
            "WITH r ORDER BY r.ts DESC, r.receipt_id DESC SKIP $keep "
            "DETACH DELETE r",
            params={"sid": sub_id, "keep": MAX_RECEIPTS},
        )
    except Exception:  # noqa: BLE001, RUF100
        logger.debug("webhook_delivery: receipt prune failed for %s", sub_id)


# ── Signing ───────────────────────────────────────────────────────────────

def sign(secret: str, ts: int, body: bytes) -> str:
    """HMAC-SHA256 over ``b"<ts>." + body`` — the value of the ``v1=`` field."""
    mac = hmac.new(secret.encode("utf-8"),
                   str(int(ts)).encode("ascii") + b"." + body, sha256)
    return mac.hexdigest()


def signature_header(secret: str, ts: int, body: bytes) -> str:
    return f"t={int(ts)},v1={sign(secret, ts, body)}"


def verify_signature(secret: str, header: str, body: bytes, *,
                     tolerance_s: int = SIGNATURE_TOLERANCE_S,
                     now: int | None = None) -> bool:
    """Consumer-side verification (exported so a pilot app and its tests share it).

    Returns False — never raises — for a malformed header, an out-of-tolerance
    timestamp, or a mismatched signature. Comparison is ``hmac.compare_digest``.
    """
    try:
        fields = dict(p.split("=", 1) for p in str(header or "").split(","))
        ts = int(fields["t"])
        expected = sign(secret, ts, body)
    except Exception:  # noqa: BLE001, RUF100
        return False
    current = int(time.time()) if now is None else int(now)
    if abs(current - ts) > tolerance_s:
        return False
    return hmac.compare_digest(expected, str(fields.get("v1", "")))


# ── Transport ─────────────────────────────────────────────────────────────

class WebhookTransport:
    """The one place an outbound POST happens. Injectable for tests.

    ``follow_redirects`` is ``False`` on purpose: the registration-time SSRF
    predicate (``security.validate_webhook_url``) blessed THIS url, and a
    redirect would let the subscriber move the actual request somewhere the
    predicate never saw. A 3xx is therefore a FAILED delivery (the caller
    sees the status in the receipt).
    """

    def __init__(self, timeout_s: int = TIMEOUT_S) -> None:
        self.timeout_s = timeout_s

    def post(self, url: str, body: bytes, headers: dict) -> tuple[int | None, str | None]:
        """Return ``(status_code, error)`` — exactly one of the two is ``None``."""
        import httpx

        try:
            resp = httpx.post(url, content=body, headers=headers,
                              timeout=self.timeout_s, follow_redirects=False)
            return resp.status_code, None
        except Exception as e:
            return None, redact_error(e)


def transport_for(sub: dict):
    """The SINK for one subscription — the ONE place a transport is chosen.

    ``dispatch_pending(transport=…)`` still injects a single transport for EVERY
    subscription (the existing test seam); this resolver is what production uses
    when nothing is injected. An absent ``transport`` property reads as webhook
    (pre-#8100 rows), and a corrupt/unknown stored value ALSO falls back to
    webhook rather than raising: a delivery pass must never raise, and a webhook
    attempt records the failure on a receipt where it is visible.
    """
    kind = str(sub.get("transport") or DEFAULT_TRANSPORT).strip().lower()
    if kind == TRANSPORT_SSE:
        # Lazy import: sse_delivery imports ``_delivery_body`` from this module
        # (ONE envelope builder), so a module-level import here would be a cycle.
        from .sse_delivery import SSE_TRANSPORT

        return SSE_TRANSPORT
    return _webhook_transport()


def _webhook_transport():
    global WEBHOOK_TRANSPORT
    if WEBHOOK_TRANSPORT is None:
        WEBHOOK_TRANSPORT = WebhookTransport()
    return WEBHOOK_TRANSPORT


def _status_code(value) -> int | None:
    """Coerce a transport's status to an int, or ``None`` when unusable.

    Defensive on purpose: ``dispatch_pending`` promises never to raise, and a
    transport (or a subclass) that returns a non-numeric status would otherwise
    raise INSIDE the dispatch loop and escape to the dream worker as a failure.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _delivery_body(sub: dict, event: dict) -> bytes:
    envelope = {
        "subscription_id": sub["sub_id"],
        "delivery_id": f"{sub['sub_id']}:{int(event.get('seq') or 0)}",
        "event": {
            "event_id": event.get("event_id"),
            "seq": int(event.get("seq") or 0),
            "type": event.get("type"),
            "ts": event.get("ts"),
            "payload": event.get("payload"),
        },
    }
    return json.dumps(envelope, ensure_ascii=False, sort_keys=True).encode("utf-8")


# ── Dispatcher ────────────────────────────────────────────────────────────

def _due_subscriptions(proj, now: datetime) -> list[dict]:
    rows = proj.g.query(
        "MATCH (s:ContextSubscription) "
        "WHERE s.disabled_at IS NULL "
        "AND (s.next_attempt_at IS NULL OR s.next_attempt_at <= $now) "
        "RETURN properties(s) ORDER BY s.created_at ASC",
        params={"now": _iso(now)},
    ).result_set
    return [r[0] for r in rows]


def _mark_delivered(proj, sub: dict, event: dict, http_status: int | None,
                    now: datetime, duration_ms: int) -> None:
    proj.g.query(
        "MATCH (s:ContextSubscription {sub_id:$id}) SET s.cursor=$cursor, "
        "s.attempts=0, s.next_attempt_at=NULL, s.delivered=s.delivered+1, "
        "s.last_error=NULL, s.last_delivery_at=$ts",
        params={"id": sub["sub_id"], "cursor": int(event.get("seq") or 0),
                "ts": _iso(now)},
    )
    _write_receipt(proj, sub_id=sub["sub_id"], event=event,
                   attempt=int(sub.get("attempts") or 0) + 1, status="delivered",
                   http_status=http_status, error=None, now=now,
                   duration_ms=duration_ms)


def _mark_failed(proj, sub: dict, event: dict, http_status: int | None,
                 error: str | None, now: datetime, duration_ms: int) -> bool:
    """Record a failed attempt. Returns True when the subscription got DISABLED."""
    attempt = int(sub.get("attempts") or 0) + 1
    limit = max_attempts()
    disabled = attempt >= limit
    # The disable stamp is part of the DISABLED branch only: this pass only
    # dispatches subscriptions whose `disabled_at` is NULL, so a non-terminal
    # failure must leave the property ALONE rather than write an explicit NULL
    # (a `SET ...=NULL` here would silently un-disable a subscription an
    # unrelated writer disabled).
    disable_clause = (
        "s.disabled_at=$ts, s.disabled_reason=$reason" if disabled else "").rstrip()
    reason = (f"{limit} consecutive delivery failures — last: "
              f"{error or f'HTTP {http_status}'}") if disabled else None
    # `last_delivery_at` is deliberately NOT touched here: it records the last
    # SUCCESSFUL delivery (the read path reports it as such). A failed attempt
    # is recorded on the receipt, where the status and the error live.
    proj.g.query(
        "MATCH (s:ContextSubscription {sub_id:$id}) SET s.attempts=$attempt, "
        "s.next_attempt_at=$next, s.failed=s.failed+1, s.last_error=$err"
        + (", " + disable_clause if disable_clause else ""),
        params={"id": sub["sub_id"], "attempt": attempt,
                "next": None if disabled else _iso(
                    now + timedelta(seconds=backoff_seconds(attempt))),
                "err": (error or f"HTTP {http_status}")[:500],
                "reason": reason, "ts": _iso(now)},
    )
    _write_receipt(proj, sub_id=sub["sub_id"], event=event, attempt=attempt,
                   status="exhausted" if disabled else "retrying",
                   http_status=http_status, error=error, now=now,
                   duration_ms=duration_ms)
    return disabled


def dispatch_pending(proj, *, transport=None, now: datetime | None = None,
                     batch: int = DEFAULT_BATCH) -> dict:
    """Push pending events to every due subscription. NEVER raises.

    Runs at the end of a tenant's dream cycle (``hosted_api._dream_worker``)
    and on demand. Each subscription is drained IN ORDER from its cursor; the
    first failure stops that subscription's batch (head-of-line ordering is
    what makes the at-least-once contract re-deliverable) and schedules the
    retry. Returns a summary dict for logging/tests:

    ``{"enabled", "subscriptions", "delivered", "failed", "disabled", "more_pending"}``
    """
    summary = {"enabled": False, "subscriptions": 0, "delivered": 0,
               "failed": 0, "disabled": 0, "more_pending": 0}
    if not dispatch_enabled():
        return summary
    summary["enabled"] = True
    injected = transport
    now_dt = now or _now()
    try:
        # The due query runs FIRST, before ``ensure_subscription_schema``: a
        # graph with no subscriptions (i.e. every existing deployment, and
        # every test that drives a dream) must take this path WITHOUT creating
        # an index. Index creation is a graph WRITE, and a delivery pass that
        # has nothing to deliver should leave the graph byte-identical.
        subs = _due_subscriptions(proj, now_dt)
    except Exception as e:
        logger.warning("webhook_delivery: subscription read failed: %s",
                       redact_error(e))
        return summary
    if not subs:
        return summary
    ensure_subscription_schema(proj)
    from .crypto import decrypt_token
    from .event_store import read_after

    for sub in subs:
        summary["subscriptions"] += 1
        try:
            types = list(sub.get("types") or []) or None
            events = read_after(proj, int(sub.get("cursor") or 0), types=types,
                                limit=max(1, min(int(batch), 1000)))
        except Exception as e:
            logger.warning("webhook_delivery: event read failed for %s: %s",
                           sub.get("sub_id"), redact_error(e))
            continue
        if len(events) >= max(1, int(batch)):
            summary["more_pending"] += 1
        if not events:
            continue
        try:
            secret = decrypt_token(sub.get("secret") or "")
        except Exception as e:
            logger.error("webhook_delivery: cannot decrypt secret for %s: %s",
                         sub.get("sub_id"), redact_error(e))
            _mark_failed(proj, sub, events[0], None,
                         "subscription secret unreadable (encryption key "
                         "rotated or unset)", now_dt, 0)
            continue
        # The sink is chosen ONCE per subscription (#8100): a webhook POSTs out,
        # an SSE subscription publishes a wake for the stream that serves this
        # graph's durable log. NOTHING below knows which one it is talking to —
        # same cursor, same retry, same receipt — which is the whole point of
        # the shared layer (#8100 indicator 2: exactly ONE drain).
        sink = injected or transport_for(sub)
        for event in events:
            started = time.monotonic()
            body = _delivery_body(sub, event)
            ts = int(now_dt.timestamp())
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "tortoise-webhook/1",
                "X-Tortoise-Delivery": f"{sub['sub_id']}:{int(event.get('seq') or 0)}",
                "X-Tortoise-Event": str(event.get("type") or ""),
                "X-Tortoise-Seq": str(int(event.get("seq") or 0)),
                "X-Tortoise-Signature": signature_header(secret, ts, body),
            }
            try:
                http_status, error = sink.post(sub.get("url") or "", body, headers)
            except Exception as e:  # a transport that RAISES is a failed delivery
                http_status, error = None, redact_error(e)
            http_status = _status_code(http_status)
            if error is None and http_status is None:
                error = "transport returned no usable status"
            duration_ms = int((time.monotonic() - started) * 1000)
            if error is None and 200 <= http_status < 300:
                try:
                    _mark_delivered(proj, sub, event, http_status, now_dt, duration_ms)
                except Exception as e:
                    logger.warning("webhook_delivery: cursor write failed for %s: %s",
                                   sub.get("sub_id"), redact_error(e))
                summary["delivered"] += 1
                continue
            summary["failed"] += 1
            try:
                if _mark_failed(proj, sub, event, http_status, error, now_dt,
                                duration_ms):
                    summary["disabled"] += 1
            except Exception as e:
                logger.warning("webhook_delivery: failure write failed for %s: %s",
                               sub.get("sub_id"), redact_error(e))
            break  # head-of-line: retry THIS event first on the next pass
    return summary

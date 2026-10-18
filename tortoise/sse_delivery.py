"""Server-Sent Events transport on the ONE shared delivery layer — #8100 (epic #2080 W4 phase 2).

The SECOND ``transport`` implementation behind the dispatch contract that
``tortoise/webhook_delivery.py`` already owns (merged for #2081). Nothing here
dispatches, retries, backs off, signs or receipts — a subscriber of either
transport is drained by the SAME ``webhook_delivery.dispatch_pending``, from the
same ``cursor`` bookkeeping, with the same ``:DeliveryReceipt`` rows. This module
only answers two questions:

1. **What does an SSE delivery DO?** ``SseTransport.post`` publishes a WAKE for
   the subscription and returns ``(200, None)``. 200 means "this event is now on
   the subscriber's stream" — and the buffer behind that promise is the DURABLE
   ``:GraphEvent`` log, never process memory (see WHY, below).
2. **How does a client read the stream?** ``stream_subscription`` is a sync
   generator of SSE frames: replay from the client's ``Last-Event-ID`` floor out
   of ``event_store.read_after``, then keep reading, with ``: keepalive``
   comments while idle.

WHY THE DURABLE LOG IS THE BUFFER (the design decision, with its reasons)

* A per-subscription in-memory queue is WORKER-LOCAL: the dispatching worker (the
  end of a tenant's dream cycle) and the worker serving the stream are different
  processes on any real deployment, so the stream would see nothing. It also
  dies with the process, which is a gap by construction.
* The durable log already exists (``event_store`` writes a ``:GraphEvent`` for
  every mutation) and is exactly what ``dispatch_pending`` reads, so reusing it
  adds no write and no second source of truth.
* Reconnect therefore CANNOT gap: the client resumes strictly after the seq it
  names, and duplicates are only the documented at-least-once class (a floor
  below the subscription cursor replays events the dispatcher already marked
  delivered).
* Envelopes stay byte-identical to the webhook body: both go through
  ``webhook_delivery._delivery_body`` — the same builder, not a parallel one.

THE WAKE IS NOT LOAD-BEARING. ``SseBroker`` only shortens the idle wait of a
stream that is already polling the durable log; a lost wake costs at most one
``TORTOISE_SSE_POLL_S`` interval. Delivery cadence is the dream cycle (#2081's
"push latency ≤ one dream-cycle cadence"), so the poll bound is never the real
latency floor, and a broker that lives in one process is fine.

AUTHENTICATION / SIGNING. The webhook transport signs every POST because the body
leaves the trust boundary. An SSE stream does not: the client is authenticated by
the same API key that opened the connection, and frames travel over that
connection. ``X-Tortoise-Signature`` is therefore NOT emitted here (there is
nothing for the stream consumer to verify that the TLS session has not already
given it) — the envelope still carries ``delivery_id``/``event.event_id``, so a
consumer dedupes exactly as a webhook consumer does.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Iterator

from .security import redact_error

#: The ``text/event-stream`` media type (the one a compliant client requires).
SSE_MEDIA_TYPE = "text/event-stream"

#: Idle wait between durable-log reads, seconds (see module docstring: the wake
#: is an optimisation, this is the correctness bound).
POLL_ENV = "TORTOISE_SSE_POLL_S"
POLL_DEFAULT_S = 1.0
#: How long ONE stream request is held open before it ends and the client
#: reconnects (with ``Last-Event-ID``, so no gap). A hosted worker slot is not
#: held forever.
MAX_STREAM_ENV = "TORTOISE_SSE_MAX_STREAM_S"
MAX_STREAM_DEFAULT_S = 300.0
#: The ``retry:`` directive a client uses before reconnecting, ms.
RETRY_ENV = "TORTOISE_SSE_RETRY_MS"
RETRY_DEFAULT_MS = 3000
#: Events served per read; matches the dispatcher's own per-pass bound.
STREAM_BATCH = 100

#: An idle-stream comment. SSE ignores it; proxies keep the connection alive.
KEEPALIVE_FRAME = b": keepalive\n\n"


def _env_float(name: str, default: float) -> float:
    """Float env knob — a malformed or negative value falls back to ``default``.

    Same posture as ``security.env_int``: a settings typo must not take a
    delivery path down (and must not make a stream's lifetime negative).
    """
    import os

    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return float(default)
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return float(default)
    return value if value > 0 else float(default)


def poll_interval_s() -> float:
    return _env_float(POLL_ENV, POLL_DEFAULT_S)


def max_stream_s() -> float:
    return _env_float(MAX_STREAM_ENV, MAX_STREAM_DEFAULT_S)


def retry_ms() -> int:
    return int(_env_float(RETRY_ENV, float(RETRY_DEFAULT_MS)))


# ── Frames ────────────────────────────────────────────────────────────────

def sse_frame(seq: int, event_type, body: bytes) -> bytes:
    """One SSE event: ``id:`` + ``event:`` + ``data:`` lines, blank-line framed.

    ``body`` is the SAME envelope bytes the webhook transport POSTs
    (``webhook_delivery._delivery_body``). It is ``json.dumps`` output, so it
    carries no literal newline; the split below is the spec's multi-line ``data:``
    form kept as a guard rather than a formatting choice — a frame whose payload
    broke framing would be a silently corrupt stream, and the cost of the guard
    is one ``split``.
    """
    head = (
        f"id: {int(seq)}\n"
        f"event: {_event_name(event_type)}\n"
    ).encode()
    data = b"".join(b"data: " + line + b"\n" for line in bytes(body).split(b"\n"))
    return head + data + b"\n"


def _event_name(event_type) -> str:
    """The ``event:`` field — never empty, never carrying a newline.

    An SSE frame with a newline in the event name would inject framing, so the
    value is flattened rather than trusted. A missing type degrades to the SSE
    default ("message") instead of emitting ``event: None``.
    """
    name = str(event_type or "message").replace("\r", " ").replace("\n", " ")
    return name or "message"


# ── Wake signal (best-effort; see module docstring) ───────────────────────

class SseBroker:
    """Per-subscription wake signal, safe across threads and event loops.

    Deliberately NOT an event queue: there is nothing to lose when a wake is
    missed (the stream polls the durable log), which is what lets this be a
    ``threading.Condition`` shared by the dispatcher's worker thread and the
    event loop's stream — no cross-loop ``asyncio`` primitive, no queue growth,
    no memory the process can leak.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._wakes: dict[str, threading.Condition] = {}
        # Edge-triggered LATCH: a wake published while no stream is waiting is
        # still honoured by the next wait, so a delivery that lands between two
        # polls costs nothing and a stream cannot doze through a whole interval
        # with the event already durable. Guarded by the per-sub Condition (the
        # same lock that carries the notification, so publish/wait cannot
        # interleave into a lost wake).
        self._pending: dict[str, int] = {}

    def _condition(self, sub_id: str) -> threading.Condition:
        with self._lock:
            cond = self._wakes.get(sub_id)
            if cond is None:
                cond = threading.Condition()
                self._wakes[sub_id] = cond
            return cond

    def publish(self, sub_id: str) -> None:
        """Wake every stream waiting on ``sub_id`` (or latch for the next). Never raises."""
        try:
            cond = self._condition(sub_id)
            with cond:
                self._pending[sub_id] = 1
                cond.notify_all()
        except Exception:  # noqa: BLE001, RUF100 — a wake is never a delivery failure
            pass

    def wait(self, sub_id: str, timeout_s: float) -> bool:
        """Block until a wake for ``sub_id`` or ``timeout_s``. True iff woken."""
        cond = self._condition(sub_id)
        with cond:
            if self._pending.pop(sub_id, 0):
                return True
            return bool(cond.wait(max(0.0, float(timeout_s))))

    def forget(self, sub_id: str) -> None:
        """Drop a subscription's wake state (deletion). Not required for correctness."""
        cond = self._condition(sub_id)
        with cond:
            self._pending.pop(sub_id, None)
        with self._lock:
            self._wakes.pop(sub_id, None)


#: The ONE broker. Shared by the transport (publisher) and every stream (waiter).
BROKER = SseBroker()


# ── Transport (the dispatch contract) ─────────────────────────────────────

class SseTransport:
    """``post(url, body, headers) -> (status, error)`` — the dispatch contract.

    There is no ``url``: an SSE subscriber's sink is the stream the client opens,
    so the stored ``url`` for an SSE subscription is empty by construction. The
    ``body`` is accepted as-is (the frame's ``data:`` is built from the SAME
    bytes at stream time), and the wake carries only the subscription id.

    A 200 here means "the event is durably available on this subscription's
    stream" — true for every event, because the dispatcher only asks after
    ``event_store.read_after`` returned it from the durable log.
    """

    #: The header the dispatcher always sets: ``<sub_id>:<seq>``.
    DELIVERY_HEADER = "X-Tortoise-Delivery"

    def post(self, url, body, headers) -> tuple[int | None, str | None]:
        """Publish a wake and accept. Never raises — a raise is a test smell."""
        try:
            raw = str((headers or {}).get(self.DELIVERY_HEADER) or "")
            sub_id, _, seq = raw.partition(":")
            sub_id = sub_id.strip()
            if not sub_id or not seq.strip().isdigit():
                # A malformed delivery header is a TRANSPORT ERROR, not a silent
                # success: the shared retry/backoff records it and re-delivers,
                # which is how a bug here becomes visible instead of a hole.
                return None, (
                    f"malformed {self.DELIVERY_HEADER} header: {raw[:64]!r}")
        except Exception as e:  # noqa: BLE001, RUF100
            return None, redact_error(e)
        BROKER.publish(sub_id)
        return 200, None


#: The ONE instance (stateless apart from the shared broker).
SSE_TRANSPORT = SseTransport()


# ── Stream ────────────────────────────────────────────────────────────────

def resume_floor(sub: dict, last_event_id=None) -> int:
    """Where this stream starts, in ``seq`` terms.

    * ``Last-Event-ID`` present and parseable → that seq (the RECONNECT case:
      the client states what it last saw, so it resumes strictly after it with
      no gap). Deliberately NOT clamped to the subscription cursor: a client may
      legitimately be ahead of the delivery cursor (events are durable before
      the dispatcher runs), and clamping would silently skip events it has not
      seen. A client that names a future seq simply receives nothing until newer
      events arrive — its own claim to make.
    * absent/unparseable → the subscription's ``cursor`` (a FRESH connection
      starts at the tail of what the layer has already dispatched).
    """
    if last_event_id is not None:
        try:
            value = int(str(last_event_id).strip())
        except (TypeError, ValueError):
            value = None
        if value is not None and value >= 0:
            return value
    try:
        return max(0, int(sub.get("cursor") or 0))
    except (TypeError, ValueError):
        return 0


def stream_subscription(proj, sub: dict, *, last_event_id=None,
                        batch: int = STREAM_BATCH, poll_s: float | None = None,
                        budget_s: float | None = None,
                        monotonic=time.monotonic) -> Iterator[bytes]:
    """Yield SSE frames for ``sub``'s event stream. One dispatcher, one envelope.

    Blocking on purpose (a plain generator): Starlette iterates a sync iterator in
    a worker thread, so the graph reads never run on the event loop. The stream
    ends after ``TORTOISE_SSE_MAX_STREAM_S`` — the client reconnects with
    ``Last-Event-ID`` and
    continues with no gap, which is why holding the request forever buys nothing.

    Reads only. The subscription's ``cursor`` belongs to the DISPATCHER (it is the
    delivery bookkeeping); this function never writes it, so a stream can never
    make an undelivered event look delivered.
    """
    from .event_store import read_after
    from .webhook_delivery import _delivery_body

    poll = poll_interval_s() if poll_s is None else max(0.0, float(poll_s))
    budget = max_stream_s() if budget_s is None else max(0.0, float(budget_s))
    stop_at = monotonic() + budget
    sub_id = str(sub.get("sub_id") or "")
    types = list(sub.get("types") or []) or None
    cursor = resume_floor(sub, last_event_id)
    limit = max(1, min(int(batch), 1000))

    yield b"retry: %d\n\n" % retry_ms()
    while monotonic() < stop_at:
        events = read_after(proj, cursor, types=types, limit=limit)
        if events:
            for event in events:
                seq = int(event.get("seq") or 0)
                cursor = max(cursor, seq)
                yield sse_frame(seq, event.get("type"), _delivery_body(sub, event))
            continue
        remaining = max(0.0, stop_at - monotonic())
        woke = BROKER.wait(sub_id, min(poll, remaining)) if sub_id and poll > 0 else False
        if not woke:
            yield KEEPALIVE_FRAME
            if poll <= 0:
                # A zero poll with no wake would spin; end the stream instead and
                # let the client reconnect (the bounded-duration contract).
                return

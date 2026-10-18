"""#5324 — the Stripe billing notify runs on its OWN control-plane pool.

``notify_billing_event`` is the ONE payload the code itself calls
UNRECOVERABLE: the ``WebhookEvent`` idempotency marker is committed BEFORE the
notify runs, so a REFUSED submission (bounded backlog full — the callable never
runs) is a PERMANENT loss, because Stripe's retry then sees ``is_first=False``.
#4456 had routed it through the shared ``telemetry`` pool, which also carries
``update_last_used``, ``analytics_event``, ``github_repos_count`` and
``data_source_demand_read`` (all ``best_effort=True``, ``tortoise/hosted_api.py``)
— so a generic telemetry stall could refuse the one payload that cannot be
re-sent, exactly the shape the #3498 review P1 isolation rule exists to prevent
(``oauth`` #3669, ``graph`` #3773, ``org`` #7678).

Each property here has a falsifier that goes RED on revert:

* **Pool shape + measured isolation** — the billing pool is its OWN worker
  (not auth/telemetry/oauth/graph/org), and a submission completes promptly
  WHILE every telemetry and auth worker is parked. Identity alone would not
  prove it: this measures that the notify lane is actually free.
* **End-to-end wiring** — the REAL ``webhooks_stripe`` handler notifies while
  every telemetry worker is parked, and the notify reports the billing pool's
  thread name. Revert the ``pool="billing"`` argument and the notify lands in
  the saturated telemetry queue: it never runs inside the window, so this is
  the test that catches a silent return to generic telemetry.
* **Routing** — ``_cp_offload(best_effort=True, pool="billing")`` is NOT
  rewritten back to ``telemetry`` (the seam's ``best_effort`` rewrite is
  deliberately scoped to ``pool == "auth"``).
* **Bounded, lane-sized backlog** — the selector passes the billing lane's own
  ``max_backlog`` through, and it is a genuinely smaller bound than the 256 the
  notify inherited from generic telemetry (``_SingleSlotWorker``'s 32 default
  is a third, distinct value, so dropping the argument also reds).

The notify's off-loop, never-raise, refusal-escalation and bound semantics are
owned by ``tests/test_notify_offloop_4456.py``; this file owns the ISOLATION.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time

import pytest

import tortoise.hosted_api as ha
import tortoise.monitoring as monitoring
import tortoise.notify as nt
from tests.test_notify_offloop_4456 import _stripe_request, _wire_stripe_webhook

# Long enough that a saturated queue cannot drain inside it, short enough to
# keep CI quick. The seam's wait bound is collapsed to this in the parked case.
PARK_S = 1.5


# ── pool shape ──────────────────────────────────────────────────────────────


def test_billing_pool_is_separate_from_every_other_pool():
    auth = monitoring.control_plane_worker("auth")
    billing = monitoring.control_plane_worker("billing")
    telemetry = monitoring.control_plane_worker("telemetry")
    oauth = monitoring.control_plane_worker("oauth")
    graph = monitoring.control_plane_worker("graph")
    org = monitoring.control_plane_worker("org")

    assert len({auth, billing, telemetry, oauth, graph, org}) == 6, (
        "the billing notify shared a pool with another control-plane lane — "
        "generic telemetry work could then refuse the one UNRECOVERABLE "
        "payload (#5324)"
    )
    assert billing.workers == monitoring.CONTROL_PLANE_BILLING_WORKERS
    # The bound is the lane's own, not ``_SingleSlotWorker``'s 32 default: a
    # branch that forgot ``max_backlog=`` would otherwise pass unnoticed.
    assert billing._max_backlog == monitoring.CONTROL_PLANE_BILLING_BACKLOG
    assert (monitoring.CONTROL_PLANE_BILLING_BACKLOG
            < monitoring.CONTROL_PLANE_TELEMETRY_BACKLOG), (
        "the billing lane's backlog is not smaller than the telemetry backlog "
        "it inherited — it must be sized for O(billing-events) (#5324)"
    )
    assert monitoring.CONTROL_PLANE_BILLING_BACKLOG != \
        monitoring._SingleSlotWorker.MAX_BACKLOG, (
            "the billing backlog coincides with the class default, so the "
            "selector's max_backlog= pass-through is unmeasurable (#5324)"
        )


def test_billing_pool_does_not_queue_behind_saturated_telemetry_or_auth():
    """MEASURED isolation: park EVERY telemetry worker and EVERY auth worker,
    then run the same probe on the billing pool. The notify lane completes
    while both neighbours are wedged — the local stand-in for the hosted
    contention #5324 is about (there is no local PostgREST, so the queueing
    itself is what is measured).
    """
    billing = monitoring.control_plane_worker("billing")
    release = threading.Event()
    parked: list = []

    def _hold(ev: threading.Event) -> None:
        ev.set()
        release.wait(15)

    try:
        for pool in ("telemetry", "auth"):
            worker = monitoring.control_plane_worker(pool)
            started = [threading.Event() for _ in range(worker.workers)]

            for ev in started:
                parked.append(worker.submit(lambda e=ev: _hold(e)))
            for ev in started:
                assert ev.wait(5.0), (
                    f"a {pool} worker never picked up its blocker — the pool "
                    "was not saturated, so this run proves nothing"
                )

        t0 = time.monotonic()
        assert billing.submit(lambda: "billing-ok").result(timeout=2) == \
            "billing-ok"
        elapsed = time.monotonic() - t0
        assert elapsed < 0.5, (
            f"the billing pool was NOT free while telemetry+auth were parked "
            f"({elapsed:.3f}s) — the notify shares capacity it must not (#5324)"
        )
    finally:
        release.set()
        for future in parked:
            with contextlib.suppress(Exception):
                future.result(timeout=5)


# ── seam routing: an explicitly named pool is not rewritten away ────────────


def test_cp_offload_keeps_the_explicitly_named_billing_pool(monkeypatch):
    """The seam rewrites ``best_effort`` to ``telemetry`` ONLY for the default
    ``pool="auth"``. A rewrite broadened to every best-effort call would
    silently send the notify back to the shared telemetry queue while every
    pool-identity test stayed green (#5324).
    """
    seen: list[str] = []

    async def _recorder(fn, *, op, pool="auth", timeout=None,
                        cancel_on_timeout=True):
        seen.append(pool)
        return "ok"

    monkeypatch.setattr(ha, "run_control_plane_call", _recorder)

    async def _run():
        await ha._cp_offload(lambda: None, op="billing_notify",
                            best_effort=True, pool="billing",
                            cancel_on_timeout=False)
        await ha._cp_offload(lambda: None, op="analytics_event",
                            best_effort=True)

    asyncio.run(_run())
    assert seen == ["billing", "telemetry"], (
        f"_cp_offload pool routing is wrong: {seen} — an explicitly named pool "
        "must survive, and an unnamed best-effort call must still get telemetry "
        "(#5324)"
    )


def test_unknown_selector_still_fails_closed_with_a_billing_typo():
    """The pool selector is the only thing keeping the notify off the shared
    telemetry queue, so a typo must raise rather than fall back (#3498)."""
    with pytest.raises(ValueError, match="unknown control-plane pool"):
        monitoring.control_plane_worker("billing-notify")


# ── end-to-end: telemetry saturation cannot refuse the notify ───────────────


def test_webhook_notify_runs_while_every_telemetry_worker_is_parked(monkeypatch):
    """#5324 falsifier, driven through the REAL handler and the REAL pools.

    Every telemetry worker is parked for ``PARK_S`` and the seam's wait bound is
    collapsed well inside that window, so anything submitted to telemetry is
    QUEUED and never runs. The webhook's notify must nevertheless RUN, on the
    billing pool. Revert ``pool="billing"`` and the notify is submitted to the
    saturated telemetry pool, the bound abandons it, and ``notify_ran`` is never
    set — the exact permanent-loss path #4456 described and #5324 removes the
    generic-telemetry driver for.
    """
    order: list[str] = []
    notify_ran = threading.Event()
    thread_names: list[str] = []
    _wire_stripe_webhook(monkeypatch, order)

    def _notify(kind, org, details=None):
        order.append("notify")
        thread_names.append(threading.current_thread().name)
        notify_ran.set()

    monkeypatch.setattr(nt, "notify_billing_event", _notify)
    # The bound sits INSIDE the park window: a telemetry-routed notify would be
    # abandoned rather than served late (and on this lane REFUSED — a permanent
    # loss, since the marker is already committed).
    monkeypatch.setattr(monitoring, "CONTROL_PLANE_OFFLOAD_TIMEOUT_S", 0.2)

    telemetry = monitoring.control_plane_worker("telemetry")
    release = threading.Event()
    parked: list = []
    try:
        started = [threading.Event() for _ in range(telemetry.workers)]

        def _hold(ev: threading.Event) -> None:
            ev.set()
            release.wait(PARK_S + 5)

        for ev in started:
            parked.append(telemetry.submit(lambda e=ev: _hold(e)))
        for ev in started:
            assert ev.wait(5.0), (
                "a telemetry worker never picked up its blocker — the pool was "
                "not saturated, so this run proves nothing"
            )

        resp = asyncio.run(ha.webhooks_stripe(_stripe_request({})))
    finally:
        release.set()
        for future in parked:
            with contextlib.suppress(Exception):
                future.result(timeout=5)

    assert resp.status_code == 200, (
        f"the webhook did not 200 with telemetry saturated: {resp.status_code} "
        f"{resp.body[:200]!r} (#5324)"
    )
    assert notify_ran.is_set(), (
        "the billing notify did NOT run while every telemetry worker was "
        "parked — it is sharing the telemetry queue again, so a generic "
        "telemetry stall can refuse the one unrecoverable payload (#5324)"
    )
    assert all(
        name.startswith(monitoring.CONTROL_PLANE_BILLING_WORKER_NAME)
        for name in thread_names
    ), (
        f"the notify ran on {thread_names} — it must use the dedicated billing "
        f"daemon pool ({monitoring.CONTROL_PLANE_BILLING_WORKER_NAME}), and "
        "never the shared telemetry pool (#5324)"
    )

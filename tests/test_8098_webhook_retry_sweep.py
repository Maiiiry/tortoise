"""#8098 — the scheduled webhook retry sweep (epic #2080 W4 phase 2, #2081 follow-up).

The delivery layer (#2081) dispatches at the END of a tenant's dream cycle. That
is the correct cadence for NEW events — a write enqueues the dream that pushes
it — but it is a WRITE-TRIGGERED cadence, so a subscription whose retry backoff
(``next_attempt_at``) elapses while its tenant stays IDLE had no trigger at all:
the bookkeeping was durable and correct, the TRIGGER was missing. #8098 adds the
trigger.

What these tests pin, and why each one is shaped the way it is:

* **the trigger exists at all.** Driving the loop and asserting the pass ran
  (not just that the function is defined) — the #1890/#4241 shape, where the
  repair was never wrong, it just never ran. A definition-only test would have
  been green for the whole life of this bug.
* **an IDLE tenant is retried after the backoff elapses, and the cursor
  advances.** The integration case: fail a delivery, let the backoff elapse
  (no new write to that tenant's graph at all), run ONE sweep pass, and read the
  cursor back from the store.
* **the no-subscription cost.** One read, ZERO nodes, ZERO indexes — asserted by
  DIFFING the graph, not by reading the code: `ensure_subscription_schema`
  creates three indexes, so a sweep that reached it for every tenant would be a
  fleet-wide index-creation storm on the first deploy.
* **shutdown/handover.** The task is in the shared ``_LIVENESS_TASK_ATTRS``
  tuple (so a re-entry or shutdown disarms it) and ``_stop_liveness`` actually
  cancels it.
* **the gate cannot drift from the dispatcher.** Both read ONE predicate; the
  parity test drives a matrix of subscription states through both and requires
  the same yes/no.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

import pytest

pytest.importorskip("cryptography")  # the at-rest encryption dependency

from tortoise import backup_sweep
from tortoise import hosted_api as ha
from tortoise import webhook_delivery as wd

# ── harness ───────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _webhook_env(monkeypatch):
    """Real Fernet key + every webhook knob at its unset default, per test."""
    from cryptography.fernet import Fernet

    monkeypatch.setenv("TORTOISE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    for var in ("TORTOISE_WEBHOOK_DISPATCH", "TORTOISE_WEBHOOK_SWEEP",
                "TORTOISE_WEBHOOK_SWEEP_INTERVAL",
                "TORTOISE_WEBHOOK_SWEEP_CONCURRENCY"):
        monkeypatch.delenv(var, raising=False)
    ha._WEBHOOK_SWEEP_INFLIGHT.clear()
    yield
    ha._WEBHOOK_SWEEP_INFLIGHT.clear()


class FakeTransport:
    """Replays a scripted status per POST and records the calls."""

    def __init__(self, script=()):
        self.script = list(script)
        self.calls: list[tuple[str, bytes, dict]] = []

    def post(self, url, body, headers):
        self.calls.append((url, body, headers))
        item = self.script.pop(0) if self.script else 200
        if item == "err":
            return None, "connection refused"
        return item, None


class _FakeSDK:
    """Stands in for the per-graph SDK the dispatch path builds."""

    def __init__(self, proj):
        self._proj = proj
        self.closed = 0

    def _get_proj(self):
        return self._proj

    def close(self):
        self.closed += 1


class _Graph:
    """A raw graph handle whose ``query`` returns a scripted count."""

    def __init__(self, rows, log):
        self._rows = rows
        self._log = log

    def query(self, cypher, params=None):
        self._log.append(cypher)
        return SimpleNamespace(result_set=self._rows)


class _Db:
    """A data-plane handle: named graphs whose due count is scripted."""

    def __init__(self, graphs: set[str], counts: dict[str, int] | None = None,
                 raise_on_list: bool = False, raise_on_select=()):
        self._graphs = graphs
        self._counts = counts or {}
        self._raise_on_list = raise_on_list
        self._raise_on_select = set(raise_on_select)
        self.selected: list[str] = []
        self.queries: list[str] = []

    def list_graphs(self):
        if self._raise_on_list:
            raise RuntimeError("graph list unavailable")
        return list(self._graphs)

    def select_graph(self, name):
        self.selected.append(name)
        if name in self._raise_on_select:
            raise RuntimeError(f"graph {name} unreachable")
        return _Graph([[self._counts.get(name, 0)]], self.queries)


def _patch_enumeration(monkeypatch, db, orgs=("t1",), graphs_by_org=None):
    """Wire the sweep's enumeration seams to fakes (no control plane, no SDK)."""
    plan = graphs_by_org or {
        "t1": [{"graph_id": "default", "kind": "default",
                "namespace": "g_default", "graph_name": "g_default"}],
    }

    held = SimpleNamespace(closed=0)

    def _close():
        held.closed += 1

    held.close = _close
    monkeypatch.setattr(ha, "_data_plane_db", lambda: (held, db))
    monkeypatch.setattr(ha, "_control_plane_source_on_worker", lambda: object())
    monkeypatch.setattr(backup_sweep, "enumerate_orgs", lambda _source: list(orgs))
    monkeypatch.setattr(backup_sweep, "_sweep_graph_list",
                        lambda _source, org_id: list(plan.get(org_id, [])))
    return held


def _inline_daemon(monkeypatch):
    """Run the enumeration inline — this file must not depend on a worker thread."""
    async def _inline(fn, *, name, timeout=None):
        return fn()

    monkeypatch.setattr(ha, "run_on_daemon_worker", _inline)


def _seed_events(sdk, n=1):
    from tortoise import event_store
    from tortoise.ids import ulid

    proj = sdk._get_proj()
    seqs = []
    for i in range(n):
        seq = event_store.next_seq(proj)
        event_store.append_event(proj, seq, "PointAdded", {"id": f"pt_{i}"},
                                 f"evt-{ulid()}")
        seqs.append(seq)
    return seqs


def _count(proj) -> int:
    return int(proj.g.query("MATCH (n) RETURN count(n)").result_set[0][0])


def _indexes(proj):
    return proj.g.query("CALL db.indexes()").result_set


# ── the due predicate: ONE copy, and the gate == the dispatcher ────────────

def test_the_gate_counts_exactly_what_the_dispatcher_would_dispatch(sdk_factory):
    """Parity over a matrix of states — the gate may never admit a graph the
    dispatcher no-ops on, nor starve one it would have dispatched.

    Behavioural, not source-level: an AST pin on ``_DUE_PREDICATE`` would pass
    while the two queries built it into different Cypher.
    """
    sdk = sdk_factory(ensure_schema=True)
    proj = sdk._get_proj()
    now = datetime.now(UTC)

    # due: never attempted
    fresh = wd.create_subscription(proj, url="https://hooks.example.com/fresh",
                                   now=now)
    # not due: a backed-off retry in the FUTURE
    backing_off = wd.create_subscription(
        proj, url="https://hooks.example.com/wait", now=now)
    proj.g.query(
        "MATCH (s:ContextSubscription {sub_id:$id}) SET s.next_attempt_at=$next",
        params={"id": backing_off["sub_id"],
                "next": (now + timedelta(seconds=60)).isoformat()},
    )
    # not due: disabled by the attempt ceiling
    dead = wd.create_subscription(proj, url="https://hooks.example.com/dead",
                                  now=now)
    proj.g.query(
        "MATCH (s:ContextSubscription {sub_id:$id}) SET s.disabled_at=$ts "
        "REMOVE s.next_attempt_at",
        params={"id": dead["sub_id"], "ts": now.isoformat()},
    )

    due_ids = {s["sub_id"] for s in wd._due_subscriptions(proj, now)}
    assert due_ids == {fresh["sub_id"]}, due_ids
    assert wd.due_subscription_count(proj.g, now) == len(due_ids) == 1

    # Advance the clock past the backed-off retry: it becomes due, and the gate
    # and the dispatcher must agree on that too.
    later = now + timedelta(seconds=120)
    due_ids = {s["sub_id"] for s in wd._due_subscriptions(proj, later)}
    assert due_ids == {fresh["sub_id"], backing_off["sub_id"]}, due_ids
    assert wd.due_subscription_count(proj.g, later) == len(due_ids) == 2


def test_the_gate_is_one_read_that_writes_nothing_and_creates_no_index(sdk_factory):
    """Cost indicator: an empty graph answers ``0`` and stays byte-identical.

    ``ensure_subscription_schema`` mints three indexes on a graph's FIRST
    subscription; a gate that reached it for every tenant would be a fleet-wide
    index-creation storm on the first deploy of the sweep.
    """
    sdk = sdk_factory()
    proj = sdk._get_proj()
    before_nodes = _count(proj)
    before_indexes = _indexes(proj)

    assert wd.due_subscription_count(proj.g) == 0

    assert _count(proj) == before_nodes, "the gate created a node"
    assert _indexes(proj) == before_indexes, "the gate created an index"


# ── the enumeration: existence-gated, per-graph, fail-closed ───────────────

def test_the_default_graph_is_swept_even_without_a_graph_node(monkeypatch):
    """`_sweep_graph_list` SYNTHESIZES the default — the reason the sweep uses it.

    Pre-#2083 orgs have no ``Graph`` nodes at all, so a bare
    ``enumerate_org_graphs`` walk would silently skip exactly the oldest tenants.
    """
    db = _Db({"g_default"}, {"g_default": 2})
    _patch_enumeration(monkeypatch, db)

    assert ha._webhook_retry_targets() == [
        {"org_id": "t1", "graph_name": "g_default"}]
    assert db.selected == ["g_default"], db.selected


def test_a_graph_that_does_not_exist_is_never_selected_or_read(monkeypatch):
    """The #2251 probe-before-touch rule: querying an ABSENT graph MATERIALIZES it.

    So an orphan registry row must cost neither a read nor a graph.
    """
    db = _Db({"g_default"}, {"g_default": 1})
    _patch_enumeration(monkeypatch, db, graphs_by_org={"t1": [
        {"graph_id": "default", "kind": "default",
         "namespace": "g_default", "graph_name": "g_default"},
        {"graph_id": "g_ghost", "kind": "custom",
         "namespace": "g_ghost", "graph_name": "g_ghost"},        # absent
        {"graph_id": "g_invalid", "kind": "custom",
         "namespace": "", "graph_name": "", "_invalid": True},    # unresolved
    ]})

    assert ha._webhook_retry_targets() == [
        {"org_id": "t1", "graph_name": "g_default"}]
    assert "g_ghost" not in db.selected, (
        "an absent graph was selected — a read against it materializes it")
    assert "g_invalid" not in db.selected


def test_a_failed_graph_list_probe_skips_the_whole_pass(monkeypatch):
    """A read that did not answer is NOT "nothing is due"."""
    db = _Db({"g_default"}, raise_on_list=True)
    _patch_enumeration(monkeypatch, db)

    assert ha._webhook_retry_targets() == []
    assert db.selected == []


def test_an_unreadable_graph_costs_only_that_graph(monkeypatch):
    db = _Db({"g_a", "g_b"}, {"g_a": 1, "g_b": 1}, raise_on_select=("g_a",))
    _patch_enumeration(monkeypatch, db, graphs_by_org={"t1": [
        {"graph_id": "default", "kind": "default",
         "namespace": "g_a", "graph_name": "g_a"},
        {"graph_id": "g_b", "kind": "custom",
         "namespace": "g_b", "graph_name": "g_b"},
    ]})

    assert ha._webhook_retry_targets() == [
        {"org_id": "t1", "graph_name": "g_b"}]


def test_a_failed_org_enumeration_skips_the_pass(monkeypatch):
    db = _Db({"g_default"}, {"g_default": 1})
    held = _patch_enumeration(monkeypatch, db)

    def _boom(_source):
        raise RuntimeError("team enumeration failed: control plane down")

    monkeypatch.setattr(backup_sweep, "enumerate_orgs", _boom)

    assert ha._webhook_retry_targets() == []
    assert held.closed == 1, "the data-plane SDK leaked open"


def test_the_data_plane_handle_is_always_closed(monkeypatch):
    db = _Db({"g_default"}, {"g_default": 0})
    held = _patch_enumeration(monkeypatch, db)

    ha._webhook_retry_targets()
    assert held.closed == 1


# ── the trigger: an IDLE tenant is retried once its backoff elapses ────────

def test_an_idle_tenant_is_retried_by_the_sweep_and_its_cursor_advances(
        sdk_factory, monkeypatch):
    """The indicator: retried with NO new write to the tenant's graph."""
    sdk = sdk_factory(ensure_schema=True)
    proj = sdk._get_proj()
    seqs = _seed_events(sdk, n=2)
    # Two hours ago, so the retry's backoff (30s) elapsed long before "now".
    base = datetime.now(UTC) - timedelta(hours=2)
    sub = wd.create_subscription(proj, url="https://hooks.example.com/cb",
                                 types=["PointAdded"], now=base)

    first = wd.dispatch_pending(proj, transport=FakeTransport([503]), now=base)
    assert first["failed"] == 1 and first["delivered"] == 0
    state = wd.get_subscription(proj, sub["sub_id"])
    assert state["attempts"] == 1 and state["cursor"] == 0
    assert state["next_attempt_at"] is not None, (
        "a failed attempt must schedule the retry the sweep is looking for")

    # NOTHING writes to this tenant's graph between the failure and the sweep:
    # the subscription state is the same and the event stream is untouched.
    events_before = len(proj.g.query(
        "MATCH (e:GraphEvent) RETURN count(e)").result_set[0])

    transport = FakeTransport([200, 200])
    monkeypatch.setattr(wd, "WebhookTransport", lambda *a, **k: transport)
    monkeypatch.setattr(ha, "_webhook_retry_targets", lambda: [
        {"org_id": "t1", "graph_name": "g_default"}])
    monkeypatch.setattr(ha, "_make_sdk", lambda **_kw: _FakeSDK(proj))
    _inline_daemon(monkeypatch)

    summary = asyncio.run(ha._webhook_retry_sweep_pass())

    assert summary["enabled"] is True and summary["targets"] == 1, summary
    assert summary["delivered"] == 2, summary
    state = wd.get_subscription(proj, sub["sub_id"])
    assert state["cursor"] == seqs[-1], "the cursor did not advance"
    assert state["attempts"] == 0 and state["next_attempt_at"] is None
    assert len(transport.calls) == 2, "the idle tenant's events were not pushed"
    assert len(proj.g.query(
        "MATCH (e:GraphEvent) RETURN count(e)").result_set[0]) == events_before, (
        "the sweep invented or consumed events — it must only READ the stream")


def test_a_graph_already_in_flight_is_skipped_not_queued(monkeypatch):
    """A pass longer than the interval must not re-enter the same graph."""
    ha._WEBHOOK_SWEEP_INFLIGHT.add("g_default")
    monkeypatch.setattr(ha, "_webhook_retry_targets", lambda: [
        {"org_id": "t1", "graph_name": "g_default"}])
    _inline_daemon(monkeypatch)
    monkeypatch.setattr(ha, "_make_sdk", lambda **_kw: pytest.fail(
        "an in-flight graph was dispatched again"))

    summary = asyncio.run(ha._webhook_retry_sweep_pass())

    assert summary["targets"] == 0 and summary["skipped"] == 1, summary


def test_a_failing_graph_does_not_cost_the_pass_its_other_tenants(monkeypatch):
    monkeypatch.setattr(ha, "_webhook_retry_targets", lambda: [
        {"org_id": "t1", "graph_name": "g_bad"},
        {"org_id": "t2", "graph_name": "g_good"}])

    dispatched: list[str] = []

    def _make_sdk(*, graph_name=None, namespace=None):
        if graph_name == "g_bad":
            raise RuntimeError("graph unreachable")
        dispatched.append(graph_name)
        return _FakeSDK(SimpleNamespace())

    monkeypatch.setattr(ha, "_make_sdk", _make_sdk)
    monkeypatch.setattr(wd, "dispatch_pending", lambda proj: {"delivered": 1})
    _inline_daemon(monkeypatch)

    summary = asyncio.run(ha._webhook_retry_sweep_pass())

    assert dispatched == ["g_good"], dispatched
    assert summary["delivered"] == 1, summary


# ── knobs: kill switch, enable flag, interval, concurrency ─────────────────

def test_the_kill_switch_stops_the_sweep_before_any_graph_work(monkeypatch):
    """``TORTOISE_WEBHOOK_DISPATCH=0`` must stop the sweep too — an operator
    who turned egress off must not keep paying a fleet-wide pass every interval.
    """
    calls: list[int] = []
    monkeypatch.setattr(ha, "_webhook_retry_targets", lambda: calls.append(1) or [])
    monkeypatch.setenv("TORTOISE_WEBHOOK_DISPATCH", "0")

    summary = asyncio.run(ha._webhook_retry_sweep_pass())

    assert summary["enabled"] is False, summary
    assert calls == [], "the kill switch still enumerated the fleet"


def test_the_sweep_enable_knob_is_its_own_tristate(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(ha, "_webhook_retry_targets", lambda: calls.append(1) or [])
    monkeypatch.setenv("TORTOISE_WEBHOOK_SWEEP", "0")

    assert asyncio.run(ha._webhook_retry_sweep_pass())["enabled"] is False
    assert calls == []


@pytest.mark.parametrize("raw,expected", [
    (None, wd.SWEEP_INTERVAL_DEFAULT_S),
    ("", wd.SWEEP_INTERVAL_DEFAULT_S),
    ("90", 90),
    ("0", wd.SWEEP_INTERVAL_DEFAULT_S),        # a busy loop, refused
    ("-5", wd.SWEEP_INTERVAL_DEFAULT_S),
    ("oops", wd.SWEEP_INTERVAL_DEFAULT_S),
])
def test_sweep_interval_resolver(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv(wd.SWEEP_INTERVAL_ENV, raising=False)
    else:
        monkeypatch.setenv(wd.SWEEP_INTERVAL_ENV, raw)
    assert wd.sweep_interval_s() == expected


@pytest.mark.parametrize("raw,expected", [
    (None, wd.SWEEP_CONCURRENCY_DEFAULT),
    ("8", 8),
    ("0", 1),
    ("-3", 1),
    ("999", wd.SWEEP_CONCURRENCY_MAX),
    ("oops", wd.SWEEP_CONCURRENCY_DEFAULT),
])
def test_sweep_concurrency_clamps(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv(wd.SWEEP_CONCURRENCY_ENV, raising=False)
    else:
        monkeypatch.setenv(wd.SWEEP_CONCURRENCY_ENV, raw)
    assert wd.sweep_concurrency() == expected


@pytest.mark.parametrize("raw,expected", [
    (None, True),
    ("1", True), ("true", True), ("yes", True), ("on", True),
    ("0", False), ("false", False), ("no", False), ("off", False),
    ("banana", True),   # garbage NEVER silently disarms the retry trigger
])
def test_sweep_enabled_is_tristate(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv(wd.SWEEP_ENABLED_ENV, raising=False)
    else:
        monkeypatch.setenv(wd.SWEEP_ENABLED_ENV, raw)
    assert wd.sweep_enabled() is expected


# ── lifecycle: armed by the loop, disarmed by shutdown ─────────────────────

def test_the_periodic_loop_arms_the_sweep(monkeypatch, caplog):
    """Drive the REAL runner — the scheduling property, not the step.

    A direct call to ``_webhook_retry_sweep_pass()`` cannot observe whether it
    is armed at all, which is exactly the #8098 defect (the delivery path
    existed and was tested; nothing ever called it for an idle tenant).
    """
    passes: list[int] = []

    async def _pass():
        passes.append(1)
        return {"enabled": True}

    monkeypatch.setattr(ha, "_webhook_retry_sweep_pass", _pass)

    async def _one_pass():
        task = asyncio.create_task(ha._webhook_retry_sweep_loop(0.0))
        for _ in range(200):
            await asyncio.sleep(0)
            if passes:
                break
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(_one_pass())
    assert passes, "the periodic retry loop did not arm the sweep"


def test_a_raising_pass_cannot_end_the_loop(monkeypatch, caplog):
    """#5381: a failing pass must not silently stop retrying every tenant."""
    calls: list[int] = []

    async def _pass():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("sweep exploded")
        return {"enabled": True}

    monkeypatch.setattr(ha, "_webhook_retry_sweep_pass", _pass)

    async def _two_passes():
        task = asyncio.create_task(ha._webhook_retry_sweep_loop(0.0))
        for _ in range(400):
            await asyncio.sleep(0)
            if len(calls) >= 2:
                break
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    with caplog.at_level(logging.WARNING, logger="tortoise.api"):
        asyncio.run(_two_passes())

    assert len(calls) >= 2, "a raising pass ended the retry loop"


def test_the_sweep_task_is_in_the_shared_liveness_tuple_and_shutdown_cancels_it():
    """The task must be disarmed by the SAME tuple re-entry and shutdown read.

    A task armed in the lifespan but absent from ``_LIVENESS_TASK_ATTRS`` is
    orphaned on a re-entry (two sweeps, double delivery) or leaks at shutdown.
    """
    assert "_webhook_sweep_task" in ha._LIVENESS_TASK_ATTRS

    cancelled: list[int] = []

    async def _sleeper():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.append(1)
            raise

    async def _shutdown():
        app = SimpleNamespace(state=SimpleNamespace())
        app.state._webhook_sweep_task = asyncio.create_task(_sleeper())
        await asyncio.sleep(0)
        await ha._stop_liveness(app)
        assert app.state._webhook_sweep_task.cancelled(), (
            "shutdown left the sweep task running")

    asyncio.run(_shutdown())
    assert cancelled == [1]


def test_the_sweep_is_armed_as_a_background_task_not_awaited_at_boot():
    """#2953: the bind must not wait for a fleet-wide pass.

    Pinned from source so the test needs no app import: the arm site must be a
    ``create_task`` on ``app.state``, and nothing may ``await`` the loop.
    """
    import ast
    from pathlib import Path

    source = (Path(ha.__file__).resolve()).read_text()
    tree = ast.parse(source, filename="hosted_api.py")
    lifespan = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_lifespan")
    arms = [
        node for node in ast.walk(lifespan)
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Attribute)
        and node.targets[0].attr == "_webhook_sweep_task"
    ]
    assert len(arms) == 1, "expected exactly one arm site for the sweep task"
    value = arms[0].value
    assert isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute) \
        and value.func.attr == "create_task", (
        "the sweep must be scheduled with create_task — awaiting it would "
        "delay the listening socket (#2953)")
    awaited = [
        node for node in ast.walk(lifespan)
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "_webhook_retry_sweep_loop"
    ]
    assert not awaited, "the sweep loop is awaited on the startup path"

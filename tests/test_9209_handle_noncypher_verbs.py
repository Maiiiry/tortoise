"""#9209 — the write handle's NON-CYPHER verbs wipe a graph with no guard.

``_GuardedGraph`` guards its Cypher verbs (``query``/``ro_query``/``_query``/
``profile``/``explain``) and its ``execute_command`` carries the #3595 operator
guard. But the whole-graph *destructive* verbs are not Cypher at all:

* ``proj.g.delete()`` — ``GRAPH.DELETE`` (the vendor ``Graph.execute_command``
  is bound per-instance, so it never meets a class-level guard);
* ``proj.g.execute_command("GRAPH.DELETE", <name>)`` — the raw command channel,
  which ``cypher_guard._guard_execute_command`` deliberately forwards untouched
  ("Every other command … is forwarded untouched");
* ``proj.g.copy(<dst>)`` — ``GRAPH.COPY`` creates ``dst`` holding a whole
  unjournalled duplicate of this projection's derived cache. It does NOT
  destroy an existing graph: MEASURED, the engine refuses that
  (``ResponseError: destination key already exists``). What it does is mint a
  cache-shaped graph that no journal backs — the second-writer surface §17.1
  forbids — under a name the write handle chose freely;
* ``proj.g.execute_command("FLUSHALL")`` — every graph on the server, not just
  this one.

``__getattr__`` forwards all of them verbatim, by construction, so no verb-level
guard can see them. MEASURED on the pre-fix tree: on a NON-test server graph,
``proj.g.delete()`` and ``execute_command("GRAPH.DELETE", …)`` both succeeded
and left the graph GONE, ``proj.g.copy(<non-test dst>)`` minted the duplicate,
and ``FLUSHALL`` was accepted — while the module comment claimed the handle
escape was CLOSED.

These verbs are routed through the SAME L2 name rule the Cypher verbs already
apply (``FalkorProjection._assert_test_graph``): a whole-graph operation is
refused on a non-disposable server graph, and stays available on a ``test_``
graph (so the suite's own copy/delete flows are not over-blocked). ``FLUSHALL``
/ ``FLUSHDB`` are refused unconditionally: they reach EVERY graph on the server,
so no single disposable name can authorize them (see the module comment's
"do NOT FLUSHALL" note at ``projection/__init__.py``).

Docker/server lane only: the guard's destructive path is a real engine
operation and the name rule is a server-graph rule (embedded is exempt from the
L2 name check, exactly as for the Cypher verbs).
"""
from __future__ import annotations

import os
import uuid

import pytest

from tests import _live_utils
from tortoise.projection import (
    _FORWARDED_NON_CYPHER_ATTRS,
    FalkorProjection,
    _GuardedGraph,
)

# ── Non-embedded (docker/server) gate ────────────────────────────────────
FALKORDB_AVAILABLE = False
_WORKING_URI: str | None = None


def _probe_falkordb(candidates: list[str | None]) -> tuple[bool, str | None]:
    """Probe candidate URIs for a live non-embedded FalkorDB."""
    _env_uri = os.environ.get("TORTOISE_DB_URI")
    for _uri in candidates:
        if not _uri:
            continue
        _proj = None
        try:
            _proj = FalkorProjection.from_uri(_uri)
            _proj.g.query("RETURN 1")
            return True, _uri
        except Exception:
            if _uri == _env_uri and _uri:
                break  # env-specified DB unreachable — don't fall through
            continue
        finally:
            if _proj is not None:
                try:  # noqa: SIM105
                    _proj.close()
                except Exception:
                    pass
    return False, None


FALKORDB_AVAILABLE, _WORKING_URI = _probe_falkordb([
    os.environ.get("TORTOISE_DB_URI"),
    _live_utils.docker_uri("tortoise_test_9209"),
    _live_utils.legacy_uri("tortoise_test_9209"),
])

# NOTE: the skipif is per-test, not a module-level ``pytestmark``: the census
# test is pure static analysis over the vendored class surface and must run on
# a lane with no FalkorDB too (a vendor upgrade that adds a verb is exactly the
# case a live-DB-skipped lane would otherwise miss).
_requires_falkordb = pytest.mark.skipif(
    not FALKORDB_AVAILABLE, reason="FalkorDB not available")


def _uri() -> str:
    return os.environ.get("TORTOISE_DB_URI") or (_WORKING_URI or "")


def _non_test_name(stem: str) -> str:
    """A server graph name the L2 name rule treats as NOT disposable."""
    return f"prod_9209_{stem}_{uuid.uuid4().hex[:8]}"


def _test_name(stem: str) -> str:
    return f"test_9209_{stem}_{uuid.uuid4().hex[:8]}"


def _count(proj: FalkorProjection, name: str) -> int | None:
    """Node count of ``name`` read through the CLIENT (not the handle), or
    ``None`` when the graph is gone."""
    if name not in proj.db.list_graphs():
        return None
    return proj.db.select_graph(name).query(
        "MATCH (n) RETURN count(n)").result_set[0][0]


def _drop(proj: FalkorProjection, *names: str) -> None:
    """Teardown through the client: the handle is meant to refuse these."""
    for name in names:
        try:
            if name in proj.db.list_graphs():
                proj.db.select_graph(name).delete()
        except Exception:
            pass


# ── the three fabricated doors ───────────────────────────────────────────


@_requires_falkordb
def test_graph_delete_command_is_refused_on_a_non_test_graph():
    """``execute_command("GRAPH.DELETE", …)`` — the raw command channel."""
    name = _non_test_name("cmd_delete")
    proj = FalkorProjection.from_uri(_uri(), graph_name=name)
    try:
        proj.g.query("CREATE (p:Point {id:'p1'})")
        before = _count(proj, name)
        assert before and before >= 1

        with pytest.raises(RuntimeError, match="non-test graph"):
            proj.g.execute_command("GRAPH.DELETE", name)

        assert _count(proj, name) == before, "the graph was deleted despite the refusal"
    finally:
        _drop(proj, name)
        proj.close()


@_requires_falkordb
def test_delete_verb_is_refused_on_a_non_test_graph():
    """``proj.g.delete()`` — the ``__getattr__``-forwarded vendor verb."""
    name = _non_test_name("verb_delete")
    proj = FalkorProjection.from_uri(_uri(), graph_name=name)
    try:
        proj.g.query("CREATE (p:Point {id:'p1'})")
        before = _count(proj, name)
        assert before and before >= 1

        with pytest.raises(RuntimeError, match="non-test graph"):
            proj.g.delete()

        assert _count(proj, name) == before, "the graph was deleted despite the refusal"
    finally:
        _drop(proj, name)
        proj.close()


@_requires_falkordb
def test_copy_verb_is_refused_onto_a_non_test_destination():
    """``proj.g.copy(dst)`` MINTS a cache-shaped graph under ``dst``.

    The destination does not exist beforehand, so the vendor does not refuse
    anything itself (measured: it fails only on an EXISTING destination) — the
    duplicate is created unless this guard refuses it first.
    """
    src = _non_test_name("copy_src")
    dst = _non_test_name("copy_dst")
    proj = FalkorProjection.from_uri(_uri(), graph_name=src)
    try:
        proj.g.query("CREATE (p:Point {id:'src'})")
        assert _count(proj, dst) is None

        with pytest.raises(RuntimeError, match="non-test graph"):
            proj.g.copy(dst)

        assert _count(proj, dst) is None, (
            "a cache-shaped duplicate was minted under a non-disposable name"
        )
    finally:
        _drop(proj, src, dst)
        proj.close()


@_requires_falkordb
def test_copy_command_is_refused_onto_a_non_test_destination():
    """The same rule on the raw channel: ``GRAPH.COPY`` names its dst at args[2]."""
    src = _non_test_name("copycmd_src")
    dst = _non_test_name("copycmd_dst")
    proj = FalkorProjection.from_uri(_uri(), graph_name=src)
    try:
        proj.g.query("CREATE (p:Point {id:'src'})")

        with pytest.raises(RuntimeError, match="non-test graph"):
            proj.g.execute_command("GRAPH.COPY", src, dst)

        assert _count(proj, dst) is None
    finally:
        _drop(proj, src, dst)
        proj.close()


@_requires_falkordb
def test_flush_all_is_refused_even_on_a_disposable_graph():
    """``FLUSHALL`` reaches EVERY graph on the server — no name can authorize it."""
    name = _test_name("flush")
    proj = FalkorProjection.from_uri(_uri(), graph_name=name)
    try:
        proj.g.query("CREATE (p:Point {id:'p1'})")
        before = _count(proj, name)
        with pytest.raises(RuntimeError, match="FLUSHALL"):
            proj.g.execute_command("FLUSHALL")
        assert _count(proj, name) == before
    finally:
        _drop(proj, name)
        proj.close()


# ── no over-block: the disposable lane still works ───────────────────────


@_requires_falkordb
def test_the_disposable_lane_still_copies_and_deletes():
    """A ``test_`` graph keeps both verbs: the guard refuses the name, not the op."""
    src = _test_name("ok_src")
    dst = _test_name("ok_dst")
    proj = FalkorProjection.from_uri(_uri(), graph_name=src)
    try:
        proj.g.query("CREATE (p:Point {id:'p1'})")
        src_count = _count(proj, src)
        proj.g.copy(dst)
        assert _count(proj, dst) == src_count, "copy on a test graph must still work"

        proj.g.delete()
        assert _count(proj, src) is None, "delete on a test graph must still work"
    finally:
        _drop(proj, src, dst)
        proj.close()


# ── the census: a new vendor verb cannot arrive unexamined ───────────────


def test_every_vendor_verb_is_guarded_or_declared():
    """Enumerate ``falkordb.Graph``'s public surface.

    Every public attribute is either EXPLICITLY defined on ``_GuardedGraph``
    (i.e. carries a guard) or named in ``_FORWARDED_NON_CYPHER_ATTRS`` with the
    reason it is not a whole-graph destructive verb. A vendor upgrade that adds
    a verb fails here instead of silently widening the escape.
    """
    falkordb = pytest.importorskip("falkordb")
    vendor = {n for n in dir(falkordb.Graph) if not n.startswith("_")}

    defined = set()
    for klass in _GuardedGraph.__mro__:
        defined |= {n for n in vars(klass) if not n.startswith("_")}

    undeclared = vendor - defined - set(_FORWARDED_NON_CYPHER_ATTRS)
    assert not undeclared, (
        "#9209: the write handle forwards these vendor verbs unguarded and "
        f"undeclared: {sorted(undeclared)} — guard it (if it can destroy or "
        "replace a graph) or add it to _FORWARDED_NON_CYPHER_ATTRS with the "
        "reason it cannot."
    )

    stale = set(_FORWARDED_NON_CYPHER_ATTRS) & defined
    assert not stale, (
        f"#9209: declared as FORWARDED but actually guarded: {sorted(stale)} — "
        "the declaration must describe the surface as it is."
    )

    destructive = {"delete", "copy"}
    assert destructive <= defined, (
        "#9209: the whole-graph destructive verbs must be guarded, not "
        f"forwarded: missing {sorted(destructive - defined)}"
    )

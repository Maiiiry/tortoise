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
  this one;
* and, because a graph is a Redis KEY of type ``graphdata``, every keyspace
  write that names it: ``DEL``/``UNLINK`` (gone), ``SET`` (replaced by a
  string), ``EXPIRE``/``PEXPIRE`` (gone after the TTL), ``MOVE``/``RENAME``/
  ``RENAMENX`` (relocated) — all MEASURED on the pre-fix tree.

The command-channel guard is deliberately a PROPERTY ("this command names a
graph key"), not a list of dangerous verb spellings: the first cut of this fix
enumerated four verbs and the reviewer bypassed it with ``RENAMENX``/``SET``/
``EXPIRE`` — the same hole one syntax over. The parametrised samples below are
evidence of the property, never a claim that they are the complete set.

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


# The server-GLOBAL verbs are tested WITHOUT a live dispatch on purpose: if the
# guard regressed, the test's own "failure path" would be the wipe — on the
# shared lane that is every other lane's graphs, so a red test would be a
# destructive event. A recording fake pins the same property (refusal BEFORE the
# wire) with nothing to destroy.

class _RecordingHandle:
    """Minimal vendor-handle stand-in: records every command it is sent."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.client = None

    def execute_command(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return "OK"


class _StubProj:
    """The two projection attributes the guard reads, with L2's rule applied."""

    def __init__(self, name: str = "test_9209_stub"):
        self.graph_name = name

    def _assert_test_graph(self, reason: str = "", *, graph_name=None):
        name = self.graph_name if graph_name is None else graph_name
        if not name.startswith(("test_", "tortoise_test")):
            raise RuntimeError(
                f"Graph guard: operation blocked on non-test graph '{name}'. "
                f"{reason}"
            )


@pytest.mark.parametrize("verb", ["FLUSHALL", "FLUSHDB", "SWAPDB"])
def test_global_keyspace_verbs_are_refused_before_the_wire(verb):
    """``FLUSHALL``/``FLUSHDB``/``SWAPDB`` reach EVERY graph — no name authorizes them.

    Parametrised over the spellings a caller can use; the refusal must happen
    BEFORE the command is dispatched, which the recording fake proves directly.
    """
    inner = _RecordingHandle()
    guarded = _GuardedGraph(inner, _StubProj())
    with pytest.raises(RuntimeError, match=verb):
        guarded.execute_command(verb)
    assert inner.calls == [], f"{verb} reached the wire before the guard refused"


def test_graph_delete_is_refused_before_the_wire():
    """The same ordering pin for the ``GRAPH.*`` family."""
    inner = _RecordingHandle()
    guarded = _GuardedGraph(inner, _StubProj())
    with pytest.raises(RuntimeError, match="non-test graph"):
        guarded.execute_command("GRAPH.DELETE", "prod_9209_wire")
    assert inner.calls == []


# ── a graph is also a Redis KEY (#9209 review finding) ───────────────────
#
# MEASURED on the pre-fix tree, through ``proj.g.execute_command`` on a non-test
# server graph: ``DEL <graph>`` -> 1 (gone), ``UNLINK <graph>`` -> 1 (gone),
# ``RENAME <graph> <dst>`` -> True (source gone, dst holds it). None of these is
# a ``GRAPH.*`` verb, so enumerating only that family would have left the same
# destructive capability one syntax over.


#: The verbs below are not a CLAIM about which verbs are dangerous — they are
#: samples of the property "this command writes a graph KEY". The guard decides
#: on the resolved key, not on the verb, precisely because a verb list was
#: bypassed by the family's siblings (`RENAMENX`, `SET`, `EXPIRE`) in the first
#: cut of this fix. Each sample is a MEASURED pre-fix destroyer/relocator of a
#: non-test graph through `proj.g.execute_command`.
_KEYSPACE_WRITE_SAMPLES = [
    ("DEL", lambda name: (name,)),
    ("UNLINK", lambda name: (name,)),
    ("SET", lambda name: (name, "hijacked")),
    ("EXPIRE", lambda name: (name, 1)),
    ("PEXPIRE", lambda name: (name, 1000)),
    ("MOVE", lambda name: (name, 1)),
    ("RENAMENX", lambda name: (name, name + "_dst")),
]


@_requires_falkordb
@pytest.mark.parametrize(
    ("verb", "argv"), _KEYSPACE_WRITE_SAMPLES,
    ids=[verb for verb, _ in _KEYSPACE_WRITE_SAMPLES])
def test_a_command_that_writes_a_graph_key_is_refused(verb, argv):
    """A graph is a Redis KEY: any command that writes it carries the name rule."""
    name = _non_test_name(verb.lower())
    proj = FalkorProjection.from_uri(_uri(), graph_name=name)
    try:
        proj.g.query("CREATE (p:Point {id:'p1'})")
        before = _count(proj, name)
        assert before and before >= 1

        with pytest.raises(RuntimeError, match="non-test graph"):
            proj.g.execute_command(verb, *argv(name))

        assert _count(proj, name) == before, f"{verb} wrote/destroyed the graph"
    finally:
        _drop(proj, name, name + "_dst")
        proj.close()


@_requires_falkordb
def test_keyspace_rename_is_refused_when_the_source_is_a_non_test_graph():
    """Redis ``RENAME`` destroys the source AND replaces the destination."""
    src = _non_test_name("ren_src")
    dst = _non_test_name("ren_dst")
    proj = FalkorProjection.from_uri(_uri(), graph_name=src)
    try:
        proj.g.query("CREATE (p:Point {id:'p1'})")
        before = _count(proj, src)

        with pytest.raises(RuntimeError, match="non-test graph"):
            proj.g.execute_command("RENAME", src, dst)

        assert _count(proj, src) == before, "the source graph was relocated"
        assert _count(proj, dst) is None, "the source graph landed on the destination"
    finally:
        _drop(proj, src, dst)
        proj.close()


@_requires_falkordb
def test_keyspace_rename_is_refused_onto_a_non_test_destination():
    """A disposable graph must not be RENAMEd onto a live one (RENAME overwrites)."""
    src = _test_name("ren2_src")
    dst = _non_test_name("ren2_dst")
    proj = FalkorProjection.from_uri(_uri(), graph_name=src)
    try:
        proj.g.query("CREATE (p:Point {id:'p1'})")
        proj.db.select_graph(dst).query("CREATE (p:Point {id:'live'})")
        before = _count(proj, dst)

        with pytest.raises(RuntimeError, match="non-test graph"):
            proj.g.execute_command("RENAME", src, dst)

        assert _count(proj, dst) == before, "the live destination was overwritten"
    finally:
        _drop(proj, src, dst)
        proj.close()


@_requires_falkordb
def test_a_read_on_a_non_test_graph_is_allowed():
    """No over-block: reads are exempt — `sdk._graph_exists` uses EXISTS."""
    name = _non_test_name("exists")
    proj = FalkorProjection.from_uri(_uri(), graph_name=name)
    try:
        proj.g.query("CREATE (p:Point {id:'p1'})")
        assert proj.g.execute_command("EXISTS", name) == 1
        assert proj.g.execute_command("TYPE", name)  # does not raise
    finally:
        _drop(proj, name)
        proj.close()


@_requires_falkordb
def test_rename_of_a_non_graph_key_onto_a_live_graph_is_refused():
    """``RENAME <string key> <live graph>`` REPLACES the graph's value."""
    name = _test_name("ren3_src")
    dst = _non_test_name("ren3_dst")
    proj = FalkorProjection.from_uri(_uri(), graph_name=name)
    plain = f"9209:plain:{uuid.uuid4().hex[:8]}"
    try:
        proj.g.query("CREATE (p:Point {id:'p1'})")
        proj.db.select_graph(dst).query("CREATE (p:Point {id:'live'})")
        proj.g.execute_command("SET", plain, "1")
        before = _count(proj, dst)

        with pytest.raises(RuntimeError, match="non-test graph"):
            proj.g.execute_command("RENAME", plain, dst)

        assert _count(proj, dst) == before, "the live graph's value was replaced"
    finally:
        _drop(proj, name, dst)
        proj.close()


@_requires_falkordb
def test_a_keyspace_verb_on_a_non_graph_key_is_allowed():
    """PRECISION, not blanket: a lock-style key is not a graph and is not blocked."""
    name = _test_name("keyprec")
    proj = FalkorProjection.from_uri(_uri(), graph_name=name)
    lock = f"9209:lock:{uuid.uuid4().hex[:8]}"
    try:
        proj.g.execute_command("SET", lock, "1")
        assert proj.g.execute_command("DEL", lock) == 1, (
            "a non-graph key was refused — the guard over-blocks"
        )
    finally:
        _drop(proj, name)
        proj.close()


@_requires_falkordb
def test_a_keyspace_verb_on_a_disposable_graph_still_works():
    name = _test_name("keyok")
    proj = FalkorProjection.from_uri(_uri(), graph_name=name)
    try:
        proj.g.query("CREATE (p:Point {id:'p1'})")
        assert proj.g.execute_command("DEL", name) == 1
        assert _count(proj, name) is None
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


@_requires_falkordb
def test_the_census_also_covers_instance_attributes():
    """``falkordb.Graph.__init__`` sets attributes a CLASS-level census cannot see.

    Measured: ``proj.g.client.execute_command("GRAPH.DELETE", <non-test graph>)``
    returned OK and the graph was gone — and ``client`` appears in no class
    surface, so the class-only census above called the wrapper complete. The
    instance surface is enumerated here so that hop is DECLARED (it is an
    accepted escape, not a guard) rather than invisible.
    """
    name = _test_name("attrs")
    proj = FalkorProjection.from_uri(_uri(), graph_name=name)
    try:
        instance = {
            n for n in vars(proj.g._g) if not n.startswith("_")
        }
        assert "client" in instance, (
            "the vendor no longer exposes `client` on the instance — this "
            "census (and the SCOPE escape it names) needs re-reading"
        )
        defined = set()
        for klass in _GuardedGraph.__mro__:
            defined |= {n for n in vars(klass) if not n.startswith("_")}
        undeclared = instance - defined - set(_FORWARDED_NON_CYPHER_ATTRS)
        assert not undeclared, (
            "#9209: the write handle forwards these INSTANCE attributes "
            f"unguarded and undeclared: {sorted(undeclared)} — guard it (if it "
            "can destroy or replace a graph) or declare it in "
            "_FORWARDED_NON_CYPHER_ATTRS and name it in the SCOPE escape list."
        )
    finally:
        _drop(proj, name)
        proj.close()

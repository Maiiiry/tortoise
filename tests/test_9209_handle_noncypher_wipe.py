"""#9209 — the write handle's NON-CYPHER wipe verbs are guarded like the Cypher one.

Measured at ``origin/main`` before this change, with a fake handle and no DB
at all (so it holds in the embedded carve-out lane as well as the docker lane)::

    proj.g.query("MATCH (n) DETACH DELETE n")                        -> REFUSED (L2)
    proj.g.delete()                                                   -> ALLOWED
    proj.g.execute_command("GRAPH.DELETE", <own graph>)               -> ALLOWED
    proj.g.execute_command("graph.delete", <own graph>)               -> ALLOWED
    (own graph "test_own") execute_command("GRAPH.DELETE", "prod_tortoise") -> ALLOWED

The three ALLOWED rows are the SAME destruction as the refused one — a
``DETACH DELETE`` then a ``GRAPH.DELETE`` left the graph at 0 nodes — so the
guard was skipping the one call whose effect is the whole graph while refusing
the statement that only empties it. It is also the class the module forbids by
name: ``_GuardedGraph``'s ``query`` applies L2 and the class contract says the
guard "is unconditional — there is deliberately NO bypass attribute", and
``_GuardedGraph.__getattr__`` forwarded ``delete`` to the vendor handle, so
neither L2 nor §17.1's declaration was ever consulted.

Coverage map:
  (a) GRAPH.DELETE (attribute and raw-command forms) is refused on a non-test
      server graph, exactly like the Cypher wipe;
  (b) the §17.1 declaration applies — a journal-backed projection refuses an
      undeclared wipe and admits the sanctioned ``_replay_write_scope()`` one;
  (c) neither refusal can be defeated by the command's SPELLING (lowercase), its
      ENCODING (bytes), or by naming a graph OTHER than the handle's own;
  (d) the legitimate paths are unchanged: a test-named graph, the embedded
      backend, an ordinary ``GRAPH.QUERY``, and ``GRAPH.COPY`` (documented as
      NOT this class — it mints a new key and destroys nothing);
  (e) the fix is pinned structurally, so forwarding ``delete`` back through
      ``__getattr__`` reds the suite instead of silently re-opening the hole.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tortoise.projection import (
    DirectCacheWriteRefused,
    FalkorProjection,
    _GuardedGraph,
    _replay_write_scope,
)


class _Result:
    def __init__(self, rows=None):
        self.result_set = rows or []


class _RecordingGraph:
    """Records what actually reached the vendor handle.

    The refusal tests assert BOTH that the raise happened and that nothing was
    issued — a guard that raises after transmitting would pass a raise-only
    assertion while deleting the graph.
    """

    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    def query(self, cypher, params=None, timeout=None):
        self.calls.append(("query", (cypher,)))
        return _Result()

    def execute_command(self, *args, **kwargs):
        self.calls.append(("execute_command", tuple(args)))
        return "OK"

    def delete(self):
        self.calls.append(("delete", ()))
        return "OK"

    # The vendor graph exposes `name`; the guard reads it so a refusal names the
    # graph the DELETE will actually destroy.
    name = "prod_tortoise"


def _bare_projection(*, graph_name: str, embedded: bool = False,
                     journal_backed: bool = False) -> FalkorProjection:
    """A projection with no DB — enough to exercise the guard paths.

    The refusals fire before ANY graph I/O, so no connection is needed; the
    allow cases use the recording fake.
    """
    proj = object.__new__(FalkorProjection)
    proj._is_embedded = embedded
    proj._graph_name = graph_name
    proj.graph_name = graph_name
    proj._path = None
    proj._journal_backed = journal_backed
    return proj


def _handle(*, graph_name: str, embedded: bool = False,
            journal_backed: bool = False):
    proj = _bare_projection(graph_name=graph_name, embedded=embedded,
                            journal_backed=journal_backed)
    fake = _RecordingGraph()
    fake.name = graph_name
    return _GuardedGraph(fake, proj), fake


# ── (a) L2 applies to the non-Cypher wipe, exactly as to the Cypher one ──────


def test_noncypher_delete_is_refused_on_non_test_server_graph():
    guarded, fake = _handle(graph_name="prod_tortoise")
    with pytest.raises(RuntimeError, match="non-test graph"):
        guarded.delete()
    assert fake.calls == [], "GRAPH.DELETE was transmitted despite the refusal"


def test_graph_delete_command_is_refused_on_non_test_server_graph():
    guarded, fake = _handle(graph_name="prod_tortoise")
    with pytest.raises(RuntimeError, match="non-test graph"):
        guarded.execute_command("GRAPH.DELETE", "prod_tortoise")
    assert fake.calls == [], "GRAPH.DELETE was transmitted despite the refusal"


def test_the_cypher_wipe_path_is_unchanged():
    """Control: the statement the issue compares against keeps its own refusal."""
    guarded, fake = _handle(graph_name="prod_tortoise")
    with pytest.raises(RuntimeError, match="non-test graph"):
        guarded.query("MATCH (n) DETACH DELETE n")
    assert fake.calls == []


# ── (b) §17.1's declaration applies to both verbs ───────────────────────────


def test_journal_backed_projection_refuses_an_undeclared_wipe():
    """A whole-graph destruction has no journal record — so it needs a lane.

    The projection is the derived cache of a journal; a wipe is not a fold, so
    the refusal is §17.1's, not L2's (the graph here is test-named and would
    pass L2).
    """
    guarded, fake = _handle(graph_name="test_journal", journal_backed=True)
    with pytest.raises(DirectCacheWriteRefused, match="derived cache of a journal"):
        guarded.delete()
    with pytest.raises(DirectCacheWriteRefused, match="derived cache of a journal"):
        guarded.execute_command("GRAPH.DELETE", "test_journal")
    assert fake.calls == []


def test_the_sanctioned_replay_scope_admits_the_wipe():
    """The declared lane still works — the guard is a declaration, not a ban."""
    guarded, fake = _handle(graph_name="test_journal", journal_backed=True)
    with _replay_write_scope():
        assert guarded.delete() == "OK"
        assert guarded.execute_command("GRAPH.DELETE", "test_journal") == "OK"
    assert [c[0] for c in fake.calls] == ["delete", "execute_command"]


def test_embedded_is_exempt_from_the_NAME_rule_only():
    """Embedded carries no graph-name rule — but §17.1 still applies to it."""
    unarmed, fake = _handle(graph_name="prod_embedded", embedded=True)
    assert unarmed.delete() == "OK"
    assert [c[0] for c in fake.calls] == ["delete"], (
        "embedded is exempt from the NAME rule — the wipe must reach the "
        "handle, not be refused for a name it never had to satisfy")

    armed, fake2 = _handle(graph_name="prod_embedded", embedded=True,
                           journal_backed=True)
    with pytest.raises(DirectCacheWriteRefused):
        armed.delete()
    assert fake2.calls == []


# ── (c) the refusal cannot be spelled / encoded / re-targeted around ────────


def test_lowercase_and_bytes_spellings_are_refused():
    """The engine's command dispatch is case-insensitive and redis-py accepts
    BYTES — a guard matching only the literal ``"GRAPH.DELETE"`` would be
    defeated by either."""
    guarded, fake = _handle(graph_name="prod_tortoise")
    with pytest.raises(RuntimeError, match="non-test graph"):
        guarded.execute_command("graph.delete", "prod_tortoise")
    with pytest.raises(RuntimeError, match="non-test graph"):
        guarded.execute_command(b"GRAPH.DELETE", b"prod_tortoise")
    with pytest.raises(RuntimeError, match="non-test graph"):
        guarded.execute_command("Graph.Delete", "prod_tortoise")
    assert fake.calls == []


def test_the_checked_name_is_the_target_not_the_handles_own():
    """A test-named handle must not be a licence to delete a production graph.

    This is the lax half of the same hole: checking the CALLER's graph name
    would admit it, because the caller's name is disposable and the TARGET is
    not.
    """
    guarded, fake = _handle(graph_name="test_own")
    with pytest.raises(RuntimeError, match="non-test graph 'prod_tortoise'"):
        guarded.execute_command("GRAPH.DELETE", "prod_tortoise")
    assert fake.calls == []

    # …and a disposable TARGET is admitted from a non-disposable handle, because
    # the name rule is about what is DESTROYED.
    guarded2, fake2 = _handle(graph_name="prod_own")
    assert guarded2.execute_command("GRAPH.DELETE", "test_target") == "OK"
    assert [c[0] for c in fake2.calls] == ["execute_command"]


# ── (d) no collateral damage to the rest of the handle ──────────────────────


def test_other_commands_and_reads_are_untouched():
    guarded, fake = _handle(graph_name="prod_tortoise")
    assert guarded.execute_command("GRAPH.QUERY", "prod_tortoise",
                                   "MATCH (n) RETURN n") == "OK"
    # GRAPH.COPY mints a NEW key and destroys nothing: deliberately NOT in the
    # wipe class, so it must keep working (the decision is stated, not omitted).
    assert guarded.execute_command("GRAPH.COPY", "prod_tortoise",
                                   "test_copy_of_prod") == "OK"
    assert [c[0] for c in fake.calls] == ["execute_command", "execute_command"]


# ── (e) structural pin: the verb cannot be forwarded past the guards again ──


def test_delete_is_defined_on_the_handle_not_forwarded():
    """``__getattr__`` is what made the hole: an attribute the class does not
    define never meets a class-level check. Pinned on the class, so restoring
    the forwarding reds here instead of re-opening the wipe silently."""
    assert "delete" in _GuardedGraph.__dict__, (
        "_GuardedGraph.delete must be an explicit method — a forwarded "
        "`delete` (via __getattr__) reaches the vendor handle with neither "
        "§17.1's declaration nor L2 (#9209)")

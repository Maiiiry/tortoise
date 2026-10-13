"""#6976 — an id predicate must stay BOUND across a query expansion.

FalkorDB 6.0.0 drops a predicate of the form `MATCH (v:Point) WHERE <pred on v>`
— including `n.id IN $ids`, the `=` spelling, and AND-fragments — when a LATER
**plain** `MATCH` at the same query level **re-binds** `v` as a pattern endpoint.
The query does not return zero rows; it returns **other nodes' rows**. A caller
that asked to *narrow* to a set of ids therefore receives arbitrary graph rows:
a filter **bypass**, not an empty read.

The pinned test engine (4.20.4) BINDS the shape, so a behavioural test cannot
reproduce the defect here. What this test does pin is the shape of the Cypher the
builders emit: a `WITH <var>` must separate the id predicate from the expanding
`MATCH`, and it must come BEFORE that MATCH — a barrier placed after the
re-binding MATCH leaves the predicate dropped.

Each assertion is discriminating — deleting the barrier at a site makes the
matching test fail (verified by mutation).

## Why there is deliberately NO whole-tree scanner here

A class-level scan (fold every Cypher string under `tortoise/`, assert zero
offenders) was attempted and **measurably failed in both directions**, so it was
removed rather than shipped:

* **Under-inclusive** — Cypher held in `return (...)`, containers or keyword args
  is unreachable, so 7 real `MATCH`-bearing strings were never scanned (every
  string in `tortoise/navigation.py`). The rail would have reported "0 offenders"
  over strings it never read, which is exactly how the previous guard (#7050)
  reported clean over a tree holding the live bug.
* **False-positive** — `bound`/`constrained` must reset at a `UNION` boundary;
  without that, the benign two-branch `UNION` query already in
  `tortoise/navigation.py::_hop_query` is reported as an offender. A guard that
  reddens honest, unrelated PRs is a liability, and a `WITH` placed to silence it
  would be cargo.

The durable fix is NOT another source-shape rail — it is a normaliser/refusal at
the client seam plus an engine-version probe, which makes the shape
impossible-by-construction (tracked with the full evidence in tortoise#7162).

## #7162 LANDED — the seam, and why the scanner above is now unnecessary

``tortoise.cypher_guard._predicate_drop_barrier`` inserts a ``WITH *`` before a
plain ``MATCH`` that re-binds a variable a preceding ``WHERE`` constrains, and
``_predicate_barrier_required`` gates that on the SERVER's own version
(``MODULE LIST`` -> the ``graph`` module's ``ver``), decided once per client.
The tests below pin the normaliser, the gate, and the handle-level dispatch.

Two corrections to the shape's first write-up, both MEASURED (2026-10-13):

* the barrier is ``WITH *``, NOT ``WITH <var>``. ``WITH <var>`` DROPS a variable
  bound before the predicate and still referenced later — measured on BOTH 4.20.4
  and 6.0.1, a legitimate query goes from rows to ``'a' not defined``. A guard
  that breaks an honest query is worse than the bug, so ``WITH *`` (which passes
  every in-scope name) is what ships.
* nested ``CALL { … }`` is MEASURED UNAFFECTED (same discriminating topology as
  the defect: correct rows, not foreign ones), so the normaliser deliberately
  does not descend into a new brace scope.
"""
from __future__ import annotations

import re
from typing import ClassVar

import pytest

from tortoise.audit import audit_graph
from tortoise.cypher_guard import (
    _engine_needs_predicate_barrier,
    _graph_module_version,
    _predicate_drop_barrier,
    guarded_client,
)
from tortoise.search_engine import (
    filter_by_relationship,
    filter_by_traversal_predicate,
)


class _Result:
    result_set: ClassVar[tuple] = ()


class _StubGraph:
    """Captures the Cypher emitted by a builder; returns an empty result set."""

    def __init__(self) -> None:
        self.cypher: list[str] = []

    def query(self, cypher, params=None):
        self.cypher.append(cypher)
        return _Result()


class _StubProj:
    def __init__(self) -> None:
        self.g = _StubGraph()


def _assert_barrier(cypher: str, var: str) -> None:
    """`WITH <var>` must sit between the id predicate and the re-binding MATCH.

    Both halves matter. Presence alone is not enough: a barrier placed AFTER the
    re-binding MATCH leaves the predicate dropped, so this also pins the ORDER.
    """
    where = cypher.find("WHERE")
    assert where != -1, f"no WHERE in the emitted query: {cypher!r}"

    # The re-binding MATCH — `MATCH (v)` or `MATCH path=(v)`, at any level after
    # the predicate. A later `OPTIONAL MATCH` is NOT the defect shape.
    rebind = re.search(rf"\bMATCH\s+(?:path\s*=\s*)?\(\s*{re.escape(var)}\b", cypher[where:])
    assert rebind is not None, (
        f"#6976: no plain MATCH re-binds `{var}` after the predicate, so this "
        f"assertion would be vacuous for this query: {cypher!r}"
    )
    rebind_at = where + rebind.start()

    with_at = cypher.find(f"WITH {var}", where)
    assert with_at != -1, (
        f"#6976: the id predicate is UNBOUND — no `WITH {var}` after WHERE, so on "
        f"FalkorDB 6.0.0 the predicate is dropped and FOREIGN rows are returned: {cypher!r}"
    )
    assert with_at < rebind_at, (
        f"#6976: `WITH {var}` appears at {with_at} but the re-binding MATCH is at "
        f"{rebind_at} — a barrier placed AFTER the re-binding MATCH does not bind the "
        f"predicate: {cypher!r}"
    )


def test_filter_by_relationship_keeps_the_id_predicate_bound() -> None:
    graph = _StubGraph()
    filter_by_relationship(graph, ["p1"], "causes", "t1")
    assert graph.cypher, "the builder emitted no query"
    _assert_barrier(graph.cypher[0], "n")


def test_filter_by_traversal_predicate_keeps_the_id_predicate_bound() -> None:
    graph = _StubGraph()
    filter_by_traversal_predicate(graph, ["p1"], "causes")
    assert graph.cypher, "the builder emitted no query"
    _assert_barrier(graph.cypher[0], "n")


def test_audit_superseded_active_edges_keeps_its_predicate_bound() -> None:
    proj = _StubProj()
    audit_graph(proj)
    sup_queries = [q for q in proj.g.cypher if "sup" in q and "(sup:Point)" in q]
    assert sup_queries, "the superseded_active_edges check emitted no query"
    for q in sup_queries:
        _assert_barrier(q, "sup")


# ── #7162: the handle-level seam ─────────────────────────────────────────────
#
# Every test below is DISCRIMINATING: deleting the normaliser call (or the
# gate, or the dispatch of the rewritten statement) makes it fail. They are the
# runtime equivalent of the whole-tree scan the class cannot support — a new
# offender is barred on the way to the engine whatever builder emitted it.

#: The defect shape: `n` is filtered by the WHERE and then re-bound as a pattern
#: endpoint by a PLAIN `MATCH`.
_OFFENDER = "MATCH (n:Point) WHERE n.id IN $ids MATCH (n)-[:REL]->(m:Point) RETURN n.id"


def test_the_offender_shape_gains_a_barrier_before_the_rebinding_match() -> None:
    out = _predicate_drop_barrier(_OFFENDER)
    assert out != _OFFENDER, "the #6976 shape was not normalised"
    assert out.count("WITH *") == 1, out
    assert out.index("WITH *") < out.index("MATCH (n)-[:REL]"), out
    assert _OFFENDER.replace("MATCH (n)-[:REL]", "WITH * MATCH (n)-[:REL]") == out


def test_the_barrier_is_with_star_not_the_variable() -> None:
    """MEASURED: `WITH <var>` DROPS a variable bound before the predicate.

    On both 4.20.4 and 6.0.1, taking a legitimate two-variable query and
    inserting `WITH n` turns it into `'a' not defined`. `WITH *` passes every
    in-scope name through, so it cannot break such a query. Value that makes
    this fail: emitting `WITH n` (or `WITH n, m`) instead of `WITH *`.
    """
    multi = (
        "MATCH (a:Point {id:'z1'}) MATCH (n:Point) WHERE n.id IN $ids "
        "MATCH (n)-[:REL]->(m:Point) RETURN a.id, n.id"
    )
    out = _predicate_drop_barrier(multi)
    assert out != multi
    assert "WITH *" in out, out
    assert re.search(r"\bWITH\s+(?!\*)\w", out) is None, out


def test_the_barrier_is_idempotent() -> None:
    once = _predicate_drop_barrier(_OFFENDER)
    assert _predicate_drop_barrier(once) == once, "a second pass stacked a barrier"


@pytest.mark.parametrize("statement", [
    # already barred by #7155 — a `WITH` (not a plain MATCH) follows the WHERE
    "MATCH (n:Point) WHERE n.id IN $ids WITH n MATCH (n)-[:REL]->(m) RETURN n.id",
    # OPTIONAL MATCH re-binds the same var: MEASURED UNAFFECTED
    "MATCH (n:Point) WHERE n.id IN $ids OPTIONAL MATCH (n)-[:REL]->(m) RETURN n.id",
    # a DIFFERENT variable is re-bound: nothing the WHERE constrains changes
    "MATCH (n:Point) WHERE n.id IN $ids MATCH (m:Point)-[:REL]->(z) RETURN n.id",
    # nested scope: `CALL { … }` is MEASURED UNAFFECTED, and has its own scope
    "MATCH (n:Point) WHERE n.id IN $ids CALL { MATCH (n)-[:REL]->(m) RETURN m } "
    "RETURN n.id",
    # no WHERE at all
    "MATCH (n:Point) MATCH (n)-[:REL]->(m) RETURN n.id",
    # a predicate on a DIFFERENT name than the one re-bound
    "MATCH (n:Point) WHERE n.kind = 'x' MATCH (m:Point)-[:REL]->(o:Point) RETURN n.id",
])
def test_legitimate_statements_are_left_byte_identical(statement: str) -> None:
    """The false-positive rail: a query with no defect shape is NOT rewritten."""
    assert _predicate_drop_barrier(statement) == statement


def test_the_shape_inside_a_quoted_literal_is_data_not_syntax() -> None:
    statement = (
        "MATCH (n:Point) WHERE n.name = 'MATCH (n) MATCH (n)' "
        "MATCH (n)-[:R]->(m) RETURN n"
    )
    out = _predicate_drop_barrier(statement)
    # n IS re-bound by the real second MATCH, so a barrier belongs — but it must
    # sit before the REAL one, never inside the literal.
    assert out.count("WITH *") == 1, out
    assert "'MATCH (n) MATCH (n)'" in out, "the literal was rewritten"
    assert out.index("WITH *") > out.index("'MATCH (n) MATCH (n)'")


def test_the_benign_union_query_the_source_scanner_false_positived_on() -> None:
    """#7050's FALSE POSITIVE, pinned: `bound` was not reset at a UNION.

    ``navigation._hop_query`` emits a two-branch UNION whose branches each end
    in ``RETURN`` before the next ``UNION`` — no plain ``MATCH`` re-binds the
    filtered name, so no barrier is owed. The source-shape scanner reported it
    as an offender (a `WITH` added to silence it would have been cargo), which is
    half the reason that approach was abandoned. Value that makes this fail: any
    rewrite of this statement.
    """
    from tortoise.navigation import _hop_query

    statement = _hop_query("Point")
    assert statement is not None
    assert "UNION" in statement
    assert _predicate_drop_barrier(statement) == statement, (
        f"the benign UNION query was rewritten: {_predicate_drop_barrier(statement)!r}"
    )


def test_an_interpolated_predicate_is_seen_at_runtime() -> None:
    """#7050's blind spot: a whole interpolated predicate (`WHERE {fragment}`).

    The source scan could not see the fragment's variables, so it reported 0
    offenders over a tree holding the live defect. At this seam the fragment is
    already resolved, so the re-bound name is visible and the barrier lands.
    """
    fragment = "(sup.status IN ['superseded'] OR sup.outdated = true)"
    statement = (
        f"MATCH (sup:Point) WHERE {fragment}\n"
        "MATCH (sup)<-[r:IMPL|NAND]-(active:Point)\n"
        "RETURN count(DISTINCT r)"
    )
    out = _predicate_drop_barrier(statement)
    assert out != statement
    assert "WITH *" in out, out


# ── the version gate, decided from the SERVER's own version ──────────────────


def test_the_gate_opens_on_the_affected_engine_and_shuts_on_the_safe_one() -> None:
    """MEASURED range: 6.0.0/6.0.1 drop the predicate, 4.20.x bind it.

    The gate is the whole point of the seam: `WITH *` is a no-op on the engines
    that already bind, so the version decision is made ONCE per client rather
    than by 27 hand-written literals that cannot know what they run on.
    """

    class _Engine:
        def __init__(self, ver):
            self._ver = ver

        def execute_command(self, *args):
            return [[b"name", b"graph", b"ver", self._ver]]

    assert _engine_needs_predicate_barrier(_Engine(60001)) is True   # 6.0.1
    assert _engine_needs_predicate_barrier(_Engine(60000)) is True   # 6.0.0
    assert _engine_needs_predicate_barrier(_Engine(42004)) is False  # 4.20.4
    assert _engine_needs_predicate_barrier(_Engine(42006)) is False  # 4.20.6


def test_an_unreadable_version_gets_the_barrier_not_a_free_pass() -> None:
    """A probe that cannot answer must never become a silent filter bypass."""

    class _Refuses:
        def execute_command(self, *args):
            raise RuntimeError("MODULE LIST is denied here")

    assert _engine_needs_predicate_barrier(_Refuses()) is True
    assert _engine_needs_predicate_barrier(None) is True

    class _NoGraphModule:
        def execute_command(self, *args):
            return [[b"name", b"vectorset", b"ver", 1]]

    assert _engine_needs_predicate_barrier(_NoGraphModule()) is True


def test_the_module_version_is_decoded_from_the_raw_ver_integer() -> None:
    """`ver` is `MAJOR*10000 + MINOR*100 + PATCH` (measured on real containers)."""

    class _Engine:
        def __init__(self, rows):
            self._rows = rows

        def execute_command(self, *args):
            return self._rows

    assert _graph_module_version(
        _Engine([[b"name", b"graph", b"ver", 60001]])) == (6, 0, 1)
    assert _graph_module_version(
        _Engine([[b"name", b"vectorset", b"ver", 1],
                 [b"name", b"graph", b"ver", 42004]])) == (4, 20, 4)
    assert _graph_module_version(_Engine([])) is None


# ── handle-level: the seam covers every builder, not just the 27 patched ones ─


class _VersionedWire:
    """A connection that answers the version probe and records the wire."""

    def __init__(self, ver):
        self.ver = ver
        self.commands: list[tuple] = []

    def execute_command(self, *args, **kwargs):
        self.commands.append(args)
        if args and str(args[0]).upper() == "MODULE":
            if self.ver is None:
                return []
            return [[b"name", b"graph", b"ver", self.ver]]
        return [[], [], []]


class _RealGraphClient:
    """A client whose ``select_graph`` yields the REAL vendor graph class."""

    def __init__(self, wire):
        self._wire = wire

    def select_graph(self, graph_id):
        from falkordb import Graph as VendorGraph

        return VendorGraph(self._wire, graph_id)


def _handle_on(ver) -> tuple[object, _VersionedWire]:
    wire = _VersionedWire(ver)
    return guarded_client(_RealGraphClient, wire).select_graph("g"), wire


def _sent(wire: _VersionedWire) -> list[str]:
    return [c[2] for c in wire.commands if str(c[0]).upper().startswith("GRAPH.")]


def test_a_new_unbarriered_site_cannot_reach_a_6x_engine() -> None:
    """The class rail: an offender emitted by ANY builder is barred on the wire.

    This is the property a whole-tree scan cannot provide and #7050 proved it:
    a source regex is blind to interpolation, so it reports 0 over a live bug.
    Here the statement is already assembled, so the offender cannot ship green
    no matter which of the ~150 handle call sites produced it.
    """
    handle, wire = _handle_on(60001)
    handle.query(_OFFENDER, {"ids": ["p1"]})
    sent = _sent(wire)
    assert len(sent) == 1, wire.commands
    assert "WITH *" in sent[0], f"the offender reached a 6.0.1 engine: {sent[0]!r}"


def test_the_same_statement_is_untouched_on_a_binding_engine() -> None:
    """Reversibility: on 4.20.x the barrier is not needed, so no text changes.

    The vendor inlines its own param header (``CYPHER `ids`=[…] ``) into the
    statement, so the assertion is about the STATEMENT — the caller's Cypher
    reaches the engine byte-identical, with no barrier inserted.
    """
    handle, wire = _handle_on(42004)
    handle.query(_OFFENDER, {"ids": ["p1"]})
    sent = _sent(wire)
    assert len(sent) == 1, wire.commands
    assert "WITH *" not in sent[0], f"a binding engine had a barrier inserted: {sent[0]!r}"
    assert sent[0].endswith(_OFFENDER), sent[0]

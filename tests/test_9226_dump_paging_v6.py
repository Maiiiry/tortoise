"""#9226 — `dump_graph`'s keyset paging must not compare a NODE `id()` to a
NEGATIVE value: on the v6 Rust core measured here (graph module **60002**) the
node read matches nothing, so every dump started with an empty node page,
exported 0 nodes and refused — no backup could be taken on that engine.

Measured 2026-10-19 against live servers holding 3 nodes, the pinned v4 core
(**42006**) and a v6 server (**60002** — `falkordb/falkordb:latest`; `60001` is
`falkordb:6.0.1`, per `docs/evidence/7846-indexed-graph-restore/README.md`):

```
nodes:                            v4 (42006)   v6 (60002)
id(n) > -1  <-- the form used        3 rows      0 rows
id(n) >= 0                           3 rows      3 rows
id(n) > 0                            2 rows      2 rows
id(n) > -2                           0 rows      0 rows
id(n) >= -1                          0 rows      0 rows

edges (measured on the same two servers):
id(r) > -1                           2 rows      2 rows
id(r) > -2                           2 rows      2 rows
id(r) >= -1                          2 rows      2 rows
```

So the two cores disagree at exactly ONE point — `> -1` on a NODE id, the value
the old code sent — and that is the whole defect. Everything below `-1` answers
0 node rows on both cores, and relationship ids are unaffected on either. The
v4 cores answer the `-1` form correctly, which is why eight releases of green CI
never saw it: CI only runs v4.

THE REGRESSION TEST THEREFORE ENCODES THE MEASURED v6 SEMANTICS FOR NODES. A
fake handle is not a convenience here — it is the only way a v4-only CI can pin
a v6 defect, and each rule it applies below is a measurement, not a guess:

* ``id(n) > <negative>``  -> NO rows   (the v6 node-id rule that broke the dump;
  the MECHANISM is not established — see the note in `hosted_backup.py` — so the
  fake encodes the OBSERVED rule and no theory of why)
* ``id(n) >= <n>=0``      -> rows with ``id >= n``
* ``id(n) > <n>=0``       -> rows with ``id > n``
* ``id(r) > <any>``/``>=``-> normal comparison, NEGATIVES INCLUDED (edges are
  measured unaffected, so the fake must not pretend otherwise)
* ``ORDER BY``/``LIMIT``  -> applied (so paging boundaries are exercised)

Consequence when the old form returns: the first node page is empty, `dump_graph`
exports 0 data nodes for a populated graph, and `hosted_backup`'s fail-closed
guard refuses with a TRUNCATION message — the wrong cause for a whole-engine
outage (#9226).
"""
from __future__ import annotations

import re

import pytest

from tests import _live_utils
from tortoise.hosted_backup import (
    _DUMP_EDGE_PAGE_QUERY,
    _DUMP_NODE_PAGE_QUERY,
    _paged_rows,
)

#: (id, labels, props) for 5 nodes, and 6 edges so paging needs >1 page at
#: batch=2 — a single page would hide an off-by-one in the cursor offset.
_NODES = [(nid, ["Point"], {"id": f"p{nid}"}) for nid in range(5)]
_EDGES = [(0, 1, "IMPL", {}), (1, 2, "IMPL", {}), (2, 3, "NAND", {}),
          (3, 4, "IMPL", {}), (4, 0, "IMPL", {}), (0, 2, "IMPL", {})]


class _Result:
    def __init__(self, rows):
        self.result_set = rows


class _V6Graph:
    """A FalkorDB v6 handle carrying the paging semantics MEASURED on it.

    The negative-operand rule is NODE-only: on v6 (60002, and on v4) every
    ``id(r) > <negative>`` returns the edges, so the edge branches below apply a
    plain comparison and the fake never invents a defect the engine does not have.
    """

    def __init__(self, calls):
        self.calls = calls  # the (query, params) pairs, for the shape assertions

    def query(self, cypher, params=None, timeout=None):
        params = params or {}
        self.calls.append((cypher, dict(params)))
        cursor = params["cursor"]
        batch = params["batch"]
        if "id(n)" in cypher and "id(n) >=" in cypher:
            rows = [n for n in _NODES if n[0] >= cursor]
            key = 0
        elif "id(n) > $" in cypher:
            # THE MEASURED v6 NODE RULE, and the reason this class exists.
            if cursor < 0:
                return _Result([])
            rows = [n for n in _NODES if n[0] > cursor]
            key = 0
        elif "id(r) >=" in cypher:
            rows = [(s, d, t, p, i) for i, (s, d, t, p) in enumerate(_EDGES)
                    if i >= cursor]
            key = 4
        elif "id(r) > $" in cypher:
            # MEASURED: relationship ids are NOT subject to the negative-operand
            # rule — v6 answers `id(r) > -1` with every edge, exactly like v4.
            rows = [(s, d, t, p, i) for i, (s, d, t, p) in enumerate(_EDGES)
                    if i > cursor]
            key = 4
        else:  # pragma: no cover - a new statement needs its own rule
            raise AssertionError(f"unmodelled statement: {cypher}")
        rows = sorted(rows, key=lambda r: r[key])
        return _Result(rows[:batch])


# ── the queries themselves ───────────────────────────────────────────────


@pytest.mark.parametrize("query", [_DUMP_NODE_PAGE_QUERY, _DUMP_EDGE_PAGE_QUERY],
                         ids=["nodes", "edges"])
def test_the_page_predicate_never_uses_a_negative_comparison(query):
    """Shape pin: the cursor is OFFSET, never NEGATED.

    A refactor back to ``id(x) > $cursor`` with a negative start silently
    restores a whole-engine backup outage, and no v4 lane can observe it — so the
    form is pinned here rather than left to a live v6 lane that CI never runs.
    """
    assert ">= $cursor" in query, query
    assert not re.search(r"id\(\w\)\s*>\s*\$cursor", query), (
        f"a strict `>` on the cursor came back — v6 answers it with 0 rows for a "
        f"NEGATIVE cursor on node ids (#9226): {query}")


# ── the paging behaviour ─────────────────────────────────────────────────


def test_the_first_page_is_not_empty_on_a_v6_engine():
    """THE REGRESSION: before the fix this yielded NOTHING, so `dump_graph`
    exported 0 nodes for a populated graph on every v6 server."""
    calls = []
    rows = list(_paged_rows(_V6Graph(calls), _DUMP_NODE_PAGE_QUERY, 0, batch=2))

    assert [r[0] for r in rows] == [n[0] for n in _NODES], (
        "the node read must return every node on a v6 engine — an empty first "
        "page is the #9226 backup outage")
    assert calls, "the page query was never issued"
    assert calls[0][1]["cursor"] >= 0, (
        f"the FIRST page must not send a negative cursor (v6 matches nothing "
        f"for it): params={calls[0][1]}")


def test_paging_neither_duplicates_nor_skips_rows():
    """The offset arithmetic is only correct if ``>= last + 1`` really is the old
    ``> last`` — a page boundary that repeats or drops an id would silently
    corrupt every multi-page dump."""
    calls = []
    rows = list(_paged_rows(_V6Graph(calls), _DUMP_NODE_PAGE_QUERY, 0, batch=2))

    assert len(rows) == len(_NODES)
    assert len({r[0] for r in rows}) == len(_NODES), [r[0] for r in rows]
    assert len(calls) >= 2, "batch=2 over 5 nodes must page more than once"


def test_the_edge_read_pages_the_same_way_on_v6():
    """Edges page identically — and are MEASURED unaffected by the node defect.

    ``id(r) > -1`` returns every edge on v6 (and on v4), so this test guards the
    edge read's page arithmetic and its non-negative first cursor. It is NOT a
    regression test for #9226: the defect was node-only, and asserting otherwise
    would be pinning behaviour the engine does not have.
    """
    calls = []
    rows = list(_paged_rows(_V6Graph(calls), _DUMP_EDGE_PAGE_QUERY, 4, batch=2))

    assert [r[4] for r in rows] == list(range(len(_EDGES)))
    assert calls[0][1]["cursor"] >= 0, calls[0][1]


def test_the_page_size_constant_reaches_the_page_read(monkeypatch):
    """The page size must be read at CALL time, not bound as a def-time default.

    ``_paged_rows``'s ``batch`` defaulted to ``_DUMP_PAGE_SIZE`` in the
    signature, which binds once at import: retuning the module constant changed
    nothing, so the dump always paged at 1000 and finished on the
    ``len(rows) < batch`` branch. That made the live leg below unable to exercise
    the ``last + 1`` advance on any engine — a wrong offset stayed green. It is
    pinned hermetically here (this test needs no server) by asserting the
    constant visibly changes the ``$batch`` the read actually sends, and that the
    small size really forces a second page.
    """
    from tortoise import hosted_backup

    monkeypatch.setattr(hosted_backup, "_DUMP_PAGE_SIZE", 2)

    calls = []
    rows = list(hosted_backup._paged_rows(
        _V6Graph(calls), _DUMP_NODE_PAGE_QUERY, 0))

    assert [c[1]["batch"] for c in calls] == [2, 2, 2], (
        "the monkeypatched page size never reached the read — a def-time default "
        f"bound it at import: params sent were {[c[1] for c in calls]}")
    assert [r[0] for r in rows] == [n[0] for n in _NODES]
    assert len(calls) > 1, "5 nodes at batch=2 must take more than one page"


# ── the phantom row on a registry-absent graph ───────────────────────────


class _AbsentGraphV6:
    """A v6 handle for a graph name the engine registry does NOT contain.

    MEASURED on 60002, 2026-10-19: ``MATCH (n) WHERE id(n) >= 0`` answers with
    the phantom row ``(0, [], {})`` although ``MATCH (n)`` and both aggregate
    counts answer 0; on v4 (42006) every one of them answers 0. `dump_graph`
    must therefore NOT page a graph whose independent count is 0 — otherwise the
    phantom is exported as a node, and `restore_graph` materialises it.
    """

    def __init__(self, calls):
        self.calls = calls

    def query(self, cypher, params=None, timeout=None):
        params = params or {}
        self.calls.append((cypher, dict(params)))
        if "count(" in cypher:
            return _Result([[0]])
        if "id(n) >=" in cypher:
            return _Result([[0, [], {}]])  # the phantom
        return _Result([])


def test_an_absent_graph_dumps_as_empty_and_never_pages_the_phantom():
    """MEASURED v6 behaviour: a graph absent from the registry answers the node
    page with a phantom ``(0, [], {})`` while its counts are 0. Exporting that
    phantom is a regression the truncation guard cannot see (the guard is
    one-sided), so the dump must be empty AND must not have issued the page."""
    from tortoise.hosted_backup import dump_graph

    calls = []
    payload = dump_graph(_AbsentGraphV6(calls), "never_written")

    assert payload["node_count"] == 0, (
        f"an absent graph must dump as empty — the v6 phantom was exported: "
        f"{payload['nodes']}")
    assert payload["edge_count"] == 0
    assert not [c for c in calls if "id(n) >=" in c[0]], (
        "the node page was issued for a graph whose independent count is 0 — the "
        f"v6 phantom only exists on that read: {[c[0] for c in calls]}")


# ── the live proof, on whatever engine this lane can reach ───────────────


def _live_target() -> str | None:
    """A reachable, SANCTIONED non-embedded FalkorDB target, or None.

    The version is not consulted: the fix must be a no-op on v4 as well, so this
    asserts the same round-trip on whichever engine the lane can reach. The v6
    rule itself is pinned by the fake above, which needs no server, and by the
    first-page assertion — so a v4-only CI still covers the v6 defect.

    ⛔ The canonical store (port 16379, ``tests/_canonical_store_guard.py``) is
    NOT a candidate. It is the fleet's only map instance; minting graphs on it
    raises its cache-eviction churn and can abort fleet writes (#7357). A lane
    that deliberately means to use it sets ``TORTOISE_TEST_ALLOW_CANONICAL=1``,
    and the guard's own predicate decides — so the opt-in is honoured without
    this helper re-implementing it.
    """
    import os

    from tests._canonical_store_guard import canonical_store_refusal

    for uri in (os.environ.get("TORTOISE_DB_URI"),
                _live_utils.docker_uri("tortoise_test_9226_paging")):
        if not uri or canonical_store_refusal(uri) is not None:
            continue
        try:
            from tortoise.projection import FalkorProjection

            proj = FalkorProjection.from_uri(uri)
        except Exception:
            # an unreachable/refusing target is not a failure of this probe
            continue
        try:
            proj.g.query("RETURN 1")
        except Exception:
            proj.close()
            continue
        proj.close()
        return uri
    return None


def test_dump_graph_pages_a_live_graph_end_to_end(monkeypatch):
    """The end-to-end shape the outage broke: seed → dump → every node exported.

    The page size is pinned SMALL on purpose (``_DUMP_PAGE_SIZE`` monkeypatched to
    4 against 15 nodes, which works only because ``_paged_rows`` reads the
    constant at CALL time — see
    ``test_the_page_size_constant_reaches_the_page_read``): with the production
    default of 1000 this would take one page and terminate on the
    ``len(rows) < batch`` branch, never exercising the ``last + 1`` advance on a
    real engine, so a wrong offset would leave the test green. Skips (visibly)
    when this lane has no non-embedded FalkorDB, exactly like the rest of the live
    suite — it never early-returns green (#942).
    """
    uri = _live_target()
    if uri is None:
        pytest.skip("requires a live non-embedded FalkorDB (see tests/_live_utils.py)")

    import uuid

    from tortoise import hosted_backup
    from tortoise.hosted_backup import dump_graph
    from tortoise.projection import FalkorProjection

    monkeypatch.setattr(hosted_backup, "_DUMP_PAGE_SIZE", 4)

    name = f"test_9226_paging_{uuid.uuid4().hex[:8]}"
    proj = FalkorProjection.from_uri(uri, graph_name=name)
    try:
        for i in range(15):
            proj.g.query(
                "CREATE (:Point {id:$id, pointKind:'statement', is_operator:false})",
                params={"id": f"p{i}"},
            )
        payload = dump_graph(proj.g, name)
        assert payload["node_count"] == 15, (
            "dump_graph must export every node — 0 here is the #9226 v6 outage: "
            f"exported {payload['node_count']}")
    finally:
        # Delete BEFORE close, through the repo's sanctioned per-run teardown
        # (#8746): it is fail-closed on the graph name and never raises, whereas
        # issuing GRAPH.DELETE through an ALREADY-CLOSED projection — behind a
        # bare ``suppress(Exception)`` — cannot be observed at all when it does
        # not reach the server. Leftovers on a shared lane instance are the
        # #7357 cache-eviction churn this file's ``_live_target`` docstring cites.
        _live_utils.drop_test_graph(proj, name=name)
        proj.close()

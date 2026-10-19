"""#9226 — `dump_graph`'s keyset paging must not compare `id()` to a NEGATIVE
value: on the FalkorDB v6 Rust core nothing matches, so every dump exported 0
nodes and no backup could be taken on that engine.

Measured 2026-10-19 against a live v6 server (`MODULE LIST` `graph` ver
**60001**) holding 3 nodes:

```
literal  id(n) > -1  -> 0 rows      <-- the form `_paged_rows` used
literal  id(n) >= 0  -> 3 rows
literal  id(n) > 0   -> 2 rows
param    {cursor: -1} with `id(n) > $cursor` -> 0 rows
```

The v4 cores answer all of them correctly (`42004` = the app-image family,
`42006` = the engine CI pins), which is why eight releases of green CI never saw
it: the defect is engine-specific and CI only runs v4.

THE REGRESSION TEST THEREFORE ENCODES THE v6 RULE. A fake handle is not a
convenience here — it is the only way a v4-only CI can pin a v6 defect, and each
rule it applies below is a measurement, not a guess:

* ``id(x) > <negative>``  -> NO rows   (the coercion that broke the dump)
* ``id(x) >= <n>=0``      -> rows with ``id >= n``
* ``id(x) > <n>=0``       -> rows with ``id > n``
* ``ORDER BY``/``LIMIT``  -> applied (so paging boundaries are exercised)

Consequence when the old form returns: the first page is empty, `dump_graph`
exports 0 data nodes for a populated graph, and `hosted_backup`'s fail-closed
guard refuses with a TRUNCATION message — the wrong cause for a whole-engine
outage (#9226).
"""
from __future__ import annotations

import re

import pytest

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
    """A FalkorDB v6 handle, reduced to the paging semantics we measured."""

    def __init__(self, calls):
        self.calls = calls  # the (query, params) pairs, for the shape assertions

    def query(self, cypher, params=None, timeout=None):  # noqa: ARG002
        params = params or {}
        self.calls.append((cypher, dict(params)))
        cursor = params["cursor"]
        batch = params["batch"]
        if "id(n)" in cypher and "id(n) >=" in cypher:
            op = ">="
            rows = [n for n in _NODES if n[0] >= cursor]
            key = 0
        elif "id(n) > $" in cypher:
            op = ">"
            # THE MEASURED v6 RULE, and the reason this class exists.
            if cursor < 0:
                return _Result([])
            rows = [n for n in _NODES if n[0] > cursor]
            key = 0
        elif "id(r) >=" in cypher:
            op = ">="
            rows = [(s, d, t, p, i) for i, (s, d, t, p) in enumerate(_EDGES)
                    if i >= cursor]
            key = 4
        elif "id(r) > $" in cypher:
            op = ">"
            if cursor < 0:
                return _Result([])
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
        f"a strict `>` on the cursor came back — v6 answers it with 0 rows when "
        f"the cursor is negative (#9226): {query}")


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
    calls = []
    rows = list(_paged_rows(_V6Graph(calls), _DUMP_EDGE_PAGE_QUERY, 4, batch=2))

    assert [r[4] for r in rows] == list(range(len(_EDGES)))
    assert calls[0][1]["cursor"] >= 0, calls[0][1]


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

    from tests import _live_utils
    from tests._canonical_store_guard import canonical_store_refusal

    for uri in (os.environ.get("TORTOISE_DB_URI"),
                _live_utils.docker_uri("tortoise_test_9226_paging")):
        if not uri or canonical_store_refusal(uri) is not None:
            continue
        try:
            from tortoise.projection import FalkorProjection

            proj = FalkorProjection.from_uri(uri)
        except Exception:  # noqa: BLE001 - unreachable/refusing target is not a failure
            continue
        try:
            proj.g.query("RETURN 1")
        except Exception:  # noqa: BLE001
            proj.close()
            continue
        proj.close()
        return uri
    return None


def test_dump_graph_pages_a_live_graph_end_to_end():
    """The end-to-end shape the outage broke: seed → dump → every node exported.

    Skips (visibly) when this lane has no non-embedded FalkorDB, exactly like the
    rest of the live suite — it never early-returns green (#942).
    """
    uri = _live_target()
    if uri is None:
        pytest.skip("requires a live non-embedded FalkorDB (see tests/_live_utils.py)")

    import uuid

    from tortoise.hosted_backup import dump_graph
    from tortoise.projection import FalkorProjection

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
        proj.close()
        try:
            proj.db.select_graph(name).delete()
        except Exception:  # noqa: BLE001
            pass

"""#6211 — the EP pass's id-anchored queries must be PLANNED as index reads.

The issue's first failure mode is a hop that dies on a graph of a few thousand
points: ``compute_confidence(anchors=[one], max_hops=2)`` aborted after 1.01 s
over a **29-claim** frontier on a 3,353-claim / 160,000-edge graph with the
engine at its 1,000 ms query bound. The cause is not the frontier and not the
seed count (the issue says so, and the measurement confirms it): every
id-anchored query of the pass was planned as a **label scan of the traversal's
other endpoint**, with ``n.id IN $ids`` applied per record afterwards.

Measured plans (``GRAPH.PROFILE``, falkordb-server 4.20.6, 23,354 nodes /
160,000 edges), two-hop bridge at a 30-id frontier::

    shipped     Node By Label Scan (op:Point) 23,353 -> 160,000 -> 1,280,000
                records, 1.15 s      (the id filter runs over all 1.28 M)
    + barrier   Node By Index Scan (n:Point) 30 -> 1,445 records, 0.009 s

so the cost tracked GRAPH size, not the frontier — the reason it degrades with
every point the map gains and the reason ``max_hops=1`` "converges" while
``max_hops=2`` never finishes.

WHAT IS PINNED HERE, and why each test is discriminating:

1. ``test_every_id_anchored_pass_query_is_planned_as_an_index_read`` — the
   queries ``ep.py`` actually EMITS (captured off the graph handle, not
   re-written in the test) plan through ``Node By Index Scan`` and contain no
   ``Node By Label Scan``. Measured discriminating power: removing the barrier
   reddens it at the seed neighbourhood, the plain-seed direct-edge expansion,
   the bridge hop, Batch 1 and both OR-split sweeps. Removing it at the Batch 3
   site alone does NOT (the two-``MATCH`` form happens to anchor on its own
   there) — the barrier is kept there as the engine-6.0.0 predicate-drop guard
   (#6976) rather than as a cost fix, and this file does not claim otherwise.
2. ``test_the_hop_budget_tracks_the_frontier_not_the_graph`` — the cost half:
   the hop's traversal touches fewer records than the graph has edges, on a
   fixture whose background is deliberately much larger than the frontier.
   Without the barrier the traversal produces more records than the graph has
   edges, so this is a bound a label-scan plan cannot satisfy.
3. ``test_direct_edge_factors_are_collected_once_per_edge_from_both_ends`` —
   the two OR-shaped sweeps (Batch 3 factor extraction, the cache's
   back-message load) were split into endpoint-anchored halves; this pins that
   the split returns each direct edge exactly ONCE for all three cases (source
   in the affected set only, target only, both), that two byte-identical
   parallel edges stay two factors, and that the cache's back-message sweep
   populates both directions.
4. ``test_the_operator_selection_query_is_planned_as_an_index_read`` — the
   operator-viability query in ``tortoise/analyze.py`` is the SAME defect class
   over the LARGEST id set of the pass (the whole operator frontier); measured
   ``All Node Scan (t)`` at 242.5 ms -> ``Node By Index Scan (op)`` at 12.6 ms
   on the same fixture.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.ep import TortoiseEP
from tortoise.sdk import TortoiseSDK

#: Operators wired into the hub claim — the hop's real neighbourhood, and much
#: smaller than the background field the query used to scan instead.
HUB_OPS = 300
#: Background operators: edge volume that a LABEL-SCAN plan must pay for and an
#: index-anchored plan must not. 4,000 ops x 2 inputs = 8,000 edges.
BACKGROUND_OPS = 4_000
HUB = "hub"
DIRECT_OUT = "direct-out"
DIRECT_IN = "direct-in"
DIRECT_BOTH = "direct-both"


def _build(sdk: TortoiseSDK) -> None:
    proj = sdk._get_proj()
    claims = [HUB, DIRECT_OUT, DIRECT_IN, DIRECT_BOTH]
    claims += [f"f{i}" for i in range(HUB_OPS)]
    claims += [f"bg{i}" for i in range(BACKGROUND_OPS)]
    for s in range(0, len(claims), 2_000):
        proj.g.query(
            "UNWIND $ids AS id CREATE (n:Point) SET n.id = id, n.is_operator = false, "
            "n.status = 'live', n.ep_alpha = 4.0, n.ep_beta = 1.0",
            params={"ids": claims[s : s + 2_000]},
        )
    ops = [f"hop{i}" for i in range(HUB_OPS)] + [f"bgop{i}" for i in range(BACKGROUND_OPS)]
    for s in range(0, len(ops), 2_000):
        proj.g.query(
            "UNWIND $ids AS id CREATE (n:Point) SET n.id = id, n.is_operator = true, "
            "n.op_type = 'IMPL', n.direction = 'bidirectional', n.status = 'live'",
            params={"ids": ops[s : s + 2_000]},
        )
    hub_rows = [{"op": f"hop{i}", "target": HUB, "filler": f"f{i}"} for i in range(HUB_OPS)]
    bg_rows = [
        {"op": f"bgop{i}", "target": f"bg{i}", "filler": f"bg{(i * 7 + 3) % BACKGROUND_OPS}"}
        for i in range(BACKGROUND_OPS)
    ]
    for rows in (hub_rows, bg_rows):
        for s in range(0, len(rows), 2_000):
            proj.g.query(
                "UNWIND $rows AS row MATCH (o:Point {id: row.op}) "
                "MATCH (t:Point {id: row.target}) MATCH (f:Point {id: row.filler}) "
                "CREATE (o)-[:IMPL {idx: 0, direction: 'bidirectional'}]->(t), "
                "       (o)-[:IMPL {idx: 1, direction: 'bidirectional'}]->(f)",
                params={"rows": rows[s : s + 2_000]},
            )
    # Operator-less direct edges (#888 W5): one leaving the affected set, one
    # entering it, one entirely inside it, and TWO byte-identical parallel
    # edges leaving it (they must stay two factors — a row-keyed dedupe would
    # collapse them, which is why the split does not dedupe).
    proj.g.query(
        "MATCH (h:Point {id: $hub}), (o:Point {id: $out}), (i:Point {id: $in}), "
        "(b:Point {id: $both}) "
        "CREATE (h)-[:IMPL {direction: 'bidirectional'}]->(o), "
        "       (i)-[:IMPL {direction: 'bidirectional'}]->(h), "
        "       (h)-[:IMPL {direction: 'bidirectional'}]->(b), "
        "       (h)-[:IMPL {direction: 'bidirectional'}]->(b)",
        params={"hub": HUB, "out": DIRECT_OUT, "in": DIRECT_IN, "both": DIRECT_BOTH},
    )


@pytest.fixture(scope="module")
def graph():
    with tempfile.TemporaryDirectory(prefix="tt6211a_") as td:
        sdk = TortoiseSDK(os.path.join(td, "t.db"))
        try:
            _build(sdk)
            yield sdk
        finally:
            sdk.close()


class _Recorder:
    """Records the statements ``ep.py`` emits, then delegates unchanged."""

    def __init__(self, handle):
        self._handle = handle
        self.statements: list[tuple[str, dict]] = []

    def query(self, cypher, params=None, timeout=None):
        self.statements.append((cypher, params or {}))
        return self._handle.query(cypher, params=params, timeout=timeout)

    def __getattr__(self, name):
        return getattr(self._handle, name)


def _capture(ep: TortoiseEP, fn, *args, **kw) -> list[tuple[str, dict]]:
    """Run ``fn`` with the graph handle recording, return what it emitted."""
    real = ep.g
    rec = _Recorder(real)
    ep.g = rec
    try:
        fn(*args, **kw)
    finally:
        ep.g = real
    # Only the id-set statements are in scope: a whole-graph count or the
    # :EpMeta epoch read are not id-anchored and a label scan is correct there.
    return [(q, p) for q, p in rec.statements if "$ids" in q]


def _plan_text(handle, cypher: str, params: dict) -> str:
    return " ".join(handle.explain(cypher, params=params).plan)


def _statements_under_test(ep: TortoiseEP) -> list[tuple[str, str, dict]]:
    """Every id-anchored statement the pass emits, with a name for the report."""
    out: list[tuple[str, str, dict]] = []

    def add(name, stmts):
        for q, p in stmts:
            out.append((name, q, p))

    add("seed neighbourhood", _capture(ep, ep._live_neighbors_many, [HUB], False, "Point"))
    add("bridge hop", _capture(ep, ep._affected_claims, [HUB], 2, False))
    affected = ep._affected_claims([HUB], 2, False)
    add("factor extraction", _capture(ep, ep._affected_factors, affected, False))
    ep._load_cache(affected)
    add("cache load", _capture(ep, ep._load_cache, affected))
    return out


def test_the_fixture_is_dense_enough_for_the_bound_to_bite(graph):
    """Guard the fixture: the background must dwarf the hub's neighbourhood."""
    g = graph._get_proj().g
    edges = g.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
    assert edges >= 8_000, f"background too small to discriminate ({edges} edges)"
    assert edges // 4 > HUB_OPS, "the hub's own reach must be a fraction of the graph"


def test_every_id_anchored_pass_query_is_planned_as_an_index_read(graph):
    ep = TortoiseEP(graph._get_proj())
    statements = _statements_under_test(ep)
    assert statements, "no id-anchored statements captured — the recorder missed the pass"

    offenders = []
    for name, cypher, params in statements:
        plan = _plan_text(ep.g, cypher, params)
        if "Node By Label Scan" in plan or "Node By Index Scan" not in plan:
            offenders.append((name, cypher, plan))
    assert not offenders, (
        "an id-anchored EP query is planned as a label scan, so its cost tracks "
        "the GRAPH and not the frontier (#6211):\n"
        + "\n".join(f"  [{name}] {cypher}\n      plan: {plan}" for name, cypher, plan in offenders)
    )


def test_the_hop_budget_tracks_the_frontier_not_the_graph(graph):
    ep = TortoiseEP(graph._get_proj())
    g = ep.g
    total_edges = g.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
    statements = _capture(ep, ep._live_neighbors_many, [HUB], False, "Point")
    assert len(statements) == 1, statements

    cypher, params = statements[0]
    # The hop walks ~HUB_OPS operators x 2 inputs; a label-scan plan walks the
    # whole graph's edges and then some.
    bound = HUB_OPS * 4
    assert bound * 2 < total_edges, "fixture cannot discriminate the two plans"

    worst = 0
    for line in g.profile(cypher, params=params).plan:
        match = re.search(r"Conditional Traverse.*?Records produced: (\d+)", line)
        if match:
            worst = max(worst, int(match.group(1)))
    assert worst, (
        f"no traversal record count in the profile: {g.profile(cypher, params=params).plan}"
    )
    assert worst < bound, (
        f"the hop traversed {worst} records for a {HUB_OPS}-operator neighbourhood "
        f"on a {total_edges}-edge graph — the plan is not anchored on the frontier "
        f"(bound {bound})"
    )


def test_direct_edge_factors_are_collected_once_per_edge_from_both_ends(graph):
    ep = TortoiseEP(graph._get_proj())
    affected = {HUB, DIRECT_BOTH}  # source-only, target-only and both-endpoint edges
    factors = ep._affected_factors(affected, include_draft=False)
    direct = {(f[2][0], f[2][1]) for f in factors if f[4] is None}

    # (hub)->(direct-out) is reached from its SOURCE, (direct-in)->(hub) from its
    # TARGET, (hub)->(direct-both) from BOTH ends — each must appear exactly once.
    assert (HUB, DIRECT_OUT) in direct, direct
    assert (DIRECT_IN, HUB) in direct, direct
    assert (HUB, DIRECT_BOTH) in direct, direct
    for pair in ((HUB, DIRECT_OUT), (DIRECT_IN, HUB)):
        assert sum(1 for f in factors if f[4] is None and tuple(f[2]) == pair) == 1, (
            f"direct edge {pair} produced more than one factor"
        )
    # ... but TWO byte-identical parallel edges are TWO factors, not one.
    both = [f for f in factors if f[4] is None and tuple(f[2]) == (HUB, DIRECT_BOTH)]
    assert len(both) == 2, f"parallel direct edges collapsed into {len(both)} factor(s)"

    # The cache's back-message sweep is the other OR split: it must load the
    # back-message slot of a direct edge discovered from EITHER end.
    ep._load_cache(affected)
    for src, tgt in ((HUB, DIRECT_OUT), (DIRECT_IN, HUB), (HUB, DIRECT_BOTH)):
        assert (src, tgt, "IMPL") in ep._back_cache, (
            f"back-message of direct edge {src}->{tgt} was not loaded — the "
            f"split sweep covers only one endpoint"
        )


def test_the_operator_selection_query_is_planned_as_an_index_read(graph):
    """The SAME defect over the LARGEST id set of the pass (analyze.py).

    `MATCH (op:Point {is_operator:true})-[:IMPL|NAND]-(t) WHERE op.id IN $ids`
    was planned as `All Node Scan (t)` — measured 242.5 ms against 12.6 ms
    with the anchor barrier on the 23k-node / 160k-edge fixture, identical
    rows. It is the operator-viability query, and its id set is the whole
    operator frontier, so it was the worst-anchored statement of the pass.
    """
    from tortoise.analyze import _bfs_select_operators

    proj = graph._get_proj()
    statements = _capture(proj, _bfs_select_operators, proj, [HUB], 2)
    anchored = [(q, p) for q, p in statements if "$ids" in q]
    assert anchored, f"no id-set statement emitted by the selector: {statements}"
    offenders = [
        (q, _plan_text(proj.g, q, p))
        for q, p in anchored
        if "Node By Index Scan" not in _plan_text(proj.g, q, p)
        or "Node By Label Scan" in _plan_text(proj.g, q, p)
        or "All Node Scan" in _plan_text(proj.g, q, p)
    ]
    assert not offenders, (
        "the operator-selection query is not index-anchored (#6211):\n"
        + "\n".join(f"  {q}\n      plan: {plan}" for q, plan in offenders)
    )

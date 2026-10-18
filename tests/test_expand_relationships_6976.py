"""#6976 — `expand_relationships()` must not read a connected node as isolated.

`sdk.expand_relationships` is the Tortoise graph brief's mandated READ recipe
(§3, and §8 rule 8 mandates it over ``traverse``). It delegates to
``search_engine.get_relationships``, which selected its rows with ``:Point``
hard-coded on BOTH endpoints of the operator expansion:

    MATCH (n:Point) WHERE n.id IN $ids
    ...
    MATCH (op)-[r2:IMPL|NAND|hasPart]-(other:Point)
    WHERE other.is_operator = false

ONTOLOGY §5 (owner ruling #7813) allows a claim's endpoints to carry any of the
four epistemic labels — ``:Point`` / ``:Subject`` / ``:Object`` / ``:Event``;
only an OPERATOR stays ``:Point`` — and the #8882 re-typing migration moves
exactly those nodes onto their own layer. On such a graph the anchor matched
nothing (a hub that IS an ``:Event``) or the peer matched nothing (an
``:Object`` endpoint), the seeds ``{pid: []}`` were never filled, and
``expand_relationships`` returned that empty seed verbatim: a node with a dozen
relationships reported ``[]``.

Measured at the base of the fixing lane, same graph / same run (embedded):

    :Event hub, 10 edges   raw Cypher → expand_relationships == []      (BROKEN)
    :Point hub, :Object peers, 8 edges   expand → 1 of 5 relationships
    :Point hub, isolated           expand → []

The tests below are the acceptance gate for the fix. Each is discriminating:

* restoring the label-restricted predicate (``(n:Point)`` + ``(other:Point)``)
  reds the hub tests — expand goes back to ``[]`` / loses every non-``:Point``
  peer;
* restoring ``other.is_operator = false`` reds the Object/Subject/Event peer
  assertions (a non-Point node carries no ``is_operator`` property, so the
  ``= false`` spelling excludes it on the NULL, not on the value);
* returning a placeholder for an isolated node reds the isolated test.

The ``related_status`` contract (live/superseded/retracted/draft, #689/#898) and
the empty case are pinned alongside, because a widening that bought the peers
but lost the status annotation would be a different silent hole.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.cypher_guard import _predicate_drop_barrier
from tortoise.live import EPISTEMIC_LABELS
from tortoise.sdk import TortoiseSDK
from tortoise.search_engine import get_relationships

TEST_GRAPH = "tortoise_test_6976_expand_relationships"


@pytest.fixture
def sdk(tmp_path):
    sdk = TortoiseSDK(str(tmp_path / "expand.db"), namespace=TEST_GRAPH)
    yield sdk
    try:
        sdk.test_guard()
        sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")
    except Exception:
        pass
    sdk.close()


def _graph(sdk):
    return sdk._get_proj().g


def _point(sdk, content):
    return sdk.create_point("statement", content)["id"]


def _object(sdk, name):
    return sdk.create_entity("object", name=name, objectKind="component")["node"]["id"]


def _subject(sdk, name):
    return sdk.create_entity("subject", name=name, subjectKind="team")["node"]["id"]


def _event(sdk, name):
    return sdk.create_entity("event", name=name, eventKind="incident")["node"]["id"]


def _raw_relationships(graph, node_id):
    """The SAME relation `get_relationships` promises, written raw.

    One row per (mechanism, co-endpoint) — the operator-mediated view, with no
    label narrowing on the anchor, the operator or the peer. This is the ground
    truth the method must reproduce; it deliberately does not call into the
    reader under test.
    """
    rows = graph.query(
        "MATCH (n)-[r:IMPL|NAND|hasPart]-(op:Point)-[r2:IMPL|NAND|hasPart]-(other) "
        "WHERE n.id = $id "
        "  AND (op.is_operator = true OR op.op_type IS NOT NULL) "
        "  AND other.id <> n.id "
        "  AND (other.is_operator IS NULL OR other.is_operator = false) "
        "  AND (other:Point OR other:Subject OR other:Object OR other:Event) "
        "RETURN op.op_type, other.id",
        params={"id": node_id},
    ).result_set
    return [(row[0], row[1]) for row in rows]


def _raw_edge_types(graph, node_id):
    rows = graph.query(
        "MATCH (n)-[r]-(m) WHERE n.id = $id RETURN DISTINCT type(r)",
        params={"id": node_id},
    ).result_set
    return {row[0] for row in rows}


def _expand_set(entries):
    return {(e["mechanism"], e["related_id"]) for e in entries}


# ── The hub: ≥3 relationship types, ≥1 non-Point peer ───────────────────

def _hub_with_non_point_peers(sdk):
    """An INCIDENT ``:Event`` hub wired through IMPL / NAND / composedOf.

    Three operator mechanisms and three peer layers (:Object, :Subject, :Point)
    — the shape the #8882 re-typing migration produces, and the shape the
    graph brief's read recipe is aimed at.
    """
    hub = _event(sdk, "INCIDENT-42 the outage")
    svc_a = _object(sdk, "svc-a the affected service")
    svc_b = _object(sdk, "svc-b the other affected service")
    team = _subject(sdk, "the on-call team")
    cause = _point(sdk, "the outage was caused by a bad deploy")

    sdk.create_operator("IMPL", hub, [svc_a], label="addresses")
    sdk.create_operator("NAND", hub, [cause])
    sdk.create_operator("composedOf", hub, [svc_b, team])
    return hub, {"svc_a": svc_a, "svc_b": svc_b, "team": team, "cause": cause}


def test_hub_matches_raw_cypher(sdk):
    """THE acceptance test: same count AND same (type, related_id) set."""
    hub, peers = _hub_with_non_point_peers(sdk)
    graph = _graph(sdk)

    # The node really is densely connected — this is what raw Cypher reports.
    assert len(_raw_edge_types(graph, hub)) >= 3, "fixture must span ≥3 edge types"

    raw = _raw_relationships(graph, hub)
    entries = sdk.expand_relationships(hub)

    assert len(entries) == len(raw), (
        f"expand_relationships returned {len(entries)} of the {len(raw)} "
        f"relationships raw Cypher reports for the hub: {entries!r}"
    )
    assert _expand_set(entries) == set(raw), (
        "expand_relationships disagrees with raw Cypher on WHICH relationships "
        f"exist — missing {sorted(set(raw) - _expand_set(entries))}, "
        f"extra {sorted(_expand_set(entries) - set(raw))}"
    )
    # every peer layer that is actually wired is represented
    related = {e["related_id"] for e in entries}
    assert set(peers.values()) <= related, (
        f"peers dropped from the expand payload: {set(peers.values()) - related}"
    )


def test_non_point_hub_is_not_isolated(sdk):
    """The reported defect: an ``:Event`` hub read as ``[]``.

    Base: ``MATCH (n:Point) WHERE n.id IN $ids`` matched the ``:Event`` anchor
    nowhere, so the seed ``{id: []}`` was returned as the answer.
    """
    hub, peers = _hub_with_non_point_peers(sdk)
    entries = sdk.expand_relationships(hub)
    assert entries != [], (
        "an :Event hub with 4 operator-mediated relationships read as isolated "
        "— the anchor's label predicate excluded it (#6976)"
    )
    assert peers["svc_a"] in {e["related_id"] for e in entries}


def test_non_point_peer_carries_its_own_layer_name(sdk):
    """A widened peer must not arrive empty: kind and text live per layer."""
    hub, peers = _hub_with_non_point_peers(sdk)
    by_related = {e["related_id"]: e for e in sdk.expand_relationships(hub)}
    obj = by_related[peers["svc_a"]]
    assert obj["related_kind"] == "component", (
        "a :Object peer's kind lives on objectKind — reporting '' is the same "
        "hole one layer down"
    )
    assert "svc-a" in obj["related_content"], (
        "a :Object peer's text lives on name — the expand side exists to read "
        f"exactly this, got {obj['related_content']!r}"
    )


def test_operator_without_is_operator_flag_is_visible(sdk):
    """#943 parity: an operator identified by ``op_type`` alone still counts."""
    hub = _point(sdk, "INCIDENT-44 with a flagless operator")
    a = _point(sdk, "peer A of incident 44")
    b = _point(sdk, "peer B of incident 44")
    op = sdk.create_operator("IMPL", hub, [a, b])
    _graph(sdk).query(
        "MATCH (op:Point) WHERE op.id = $oid SET op.is_operator = false",
        params={"oid": op["id"]},
    )

    entries = sdk.expand_relationships(hub)
    assert {a, b} <= {e["related_id"] for e in entries}, (
        "an operator carrying op_type but not the is_operator flag made its "
        "endpoints invisible (#943 parity is what every other reader uses)"
    )


# ── The contracts the widening must not break ───────────────────────────

def test_isolated_point_still_returns_empty(sdk):
    """The empty case stays an empty LIST — not an error, not a placeholder."""
    lonely = _point(sdk, "an isolated statement with no edges at all")
    assert sdk.expand_relationships(lonely) == []


def test_isolated_non_point_still_returns_empty(sdk):
    """Same for the labels the widening admits — isolation is not label-bound."""
    lonely = _event(sdk, "an isolated incident with no edges")
    assert sdk.expand_relationships(lonely) == []


def test_related_status_survives_the_widening(sdk):
    """#689/#898: the expand payload is not blind to a peer's terminal state."""
    hub = _event(sdk, "INCIDENT-45 the superseded-cause outage")
    live_peer = _point(sdk, "the current explanation of incident 45")
    stale_peer = _point(sdk, "the retracted explanation of incident 45")
    obj_peer = _object(sdk, "svc-c the affected service")
    _graph(sdk).query(
        "MATCH (n:Point) WHERE n.id = $id SET n.status = 'superseded'",
        params={"id": stale_peer},
    )
    sdk.create_operator("IMPL", hub, [live_peer, stale_peer, obj_peer])

    statuses = {e["related_id"]: e["related_status"]
                for e in sdk.expand_relationships(hub)}
    assert statuses[live_peer] == "live"
    assert statuses[stale_peer] == "superseded"
    # an :Object carries its own lifecycle status — the lookup must find it
    # under whichever of the four labels the peer actually holds
    assert statuses[obj_peer] == "live"


# ── The shape of the emitted Cypher (the #6976 barrier class) ───────────

class _RecordingGraph:
    """Pass-through graph that records every statement sent to the engine."""

    def __init__(self, inner):
        self._inner = inner
        self.cyphers: list[str] = []

    def query(self, cypher, params=None, timeout=None):
        self.cyphers.append(cypher)
        return self._inner.query(cypher, params=params, timeout=timeout)


def test_emitted_query_keeps_the_id_predicate_and_the_widening(sdk):
    hub, _ = _hub_with_non_point_peers(sdk)
    recorder = _RecordingGraph(_graph(sdk))
    get_relationships(recorder, [hub])

    assert len(recorder.cyphers) == len(EPISTEMIC_LABELS), (
        "the anchor is id-anchored: one query per epistemic label keeps each "
        f"(label, id) range index, got {len(recorder.cyphers)} queries"
    )
    for label, cypher in zip(EPISTEMIC_LABELS, recorder.cyphers):
        assert f"MATCH (n:{label})" in cypher
        # the load-bearing barrier: a WHERE on n.id directly followed by the
        # expanding MATCH is planned as a whole-graph read on FalkorDB 6.x and
        # the point reads as isolated (#6976 / tests/test_id_predicate_barrier_6976)
        assert "WITH n" in cypher
        assert cypher.index("WITH n") < cypher.index("MATCH (n)-["), (
            "the barrier must come BEFORE the expanding MATCH"
        )
        assert _predicate_drop_barrier(cypher) == cypher, (
            "the emitted statement still holds a predicate-drop shape the "
            "runtime normaliser would have to repair"
        )
        # no :Point-only narrowing on either endpoint of the expansion
        assert "(other:Point)" not in cypher
        assert "is_operator:true" not in cypher
        # ... and the non-operator test admits an ABSENT flag (a :Object /
        # :Subject / :Event carries no is_operator property at all)
        assert "(other.is_operator IS NULL OR other.is_operator = false)" in cypher

"""#8882 Phase 1 — the graph CENSUS (``tortoise/graph_census.py``).

What this module pins, and what it deliberately does not:

* pins the **measurement**: a partition over field-defined classes, degree over
  KNOWLEDGE edges only (a provenance pointer is not topology), near-duplicate
  groups with a stated survivor, and a keep/drop predicate whose rules each
  report their own independent hit count.
* pins the **determinism** #8882's evidence bar demands ("the predicate …
  reproduces the same partition twice"): same snapshot → same digest, whatever
  order the node list arrives in.
* pins the **cost** of a cut: a predicate over a curated graph is not a filter
  over rows — the graph's value is in its edges, and an edge survives only if
  both endpoints do. The counterexample test below pins the measured lesson
  that made this block necessary.
* does NOT pin any judgement about what is "useful". The rules are field tests
  and their numbers; which of them an owner applies is an input.

Every test is DB-free: the subject is a ``tortoise-logical-dump-v1`` dict, the
same shape ``tortoise.hosted_backup.dump_graph`` emits.
"""

from __future__ import annotations

import copy
import json

import pytest

from tortoise.graph_census import (
    ABSENT,
    CENSUS_FORMAT,
    DEFAULT_RULES,
    FRAGMENT_MAX_CHARS,
    RULE_DUPLICATE,
    RULE_EXTRACTED,
    RULE_FRAGMENT,
    RULE_ISOLATED,
    RULE_UNTYPED,
    CensusError,
    census,
    classify_provenance,
    diff_census,
    flatten_counts,
    normalize_text,
    scan_nodes,
)

# ── fixtures ────────────────────────────────────────────────────────────────


def _node(dump_id: int, labels: list[str], **props) -> dict:
    return {"dump_id": dump_id, "labels": labels, "props": props}


def _dump(nodes: list[dict], edges: list[dict] | None = None) -> dict:
    edges = edges or []
    return {
        "format": "tortoise-logical-dump-v1",
        "dump_revision": 2,
        "graph_name": "test",
        "dumped_at": "2026-01-01T00:00:00+00:00",
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges,
    }


def _edge(src: int, dst: int, etype: str) -> dict:
    return {"src": src, "dst": dst, "type": etype, "props": {}}


def _curated_shaped() -> dict:
    """The measured shape of a real curated graph, in miniature.

    An operator (``:Point {is_operator:true, op_type:'IMPL'}``) wired by an
    ``IMPL`` edge to two claims: one TYPED statement with no provenance pointer
    (the population an agent asserted deliberately), and one EXTRACTED statement
    that carries an ``extractedFrom`` edge to its ``:Source``. Plus a typed lone
    statement, and a conversational fragment with no edges at all.
    """
    nodes = [
        _node(1, ["Point"], id="op-1", is_operator=True, op_type="IMPL", content="IMPL(a,b)"),
        _node(2, ["Point"], id="pt-typed-1", pointKind="statement",
              content="Retrieval is hybrid: keyword plus dense (#7180)."),
        _node(3, ["Point"], id="pt-extracted-1", pointKind="statement",
              provenanceSource="2026-07-23-abc.md",
              content="FalkorDB is the graph engine behind the projection."),
        _node(4, ["Point"], id="pt-lone-1", pointKind="goal",
              content="Ship the alpha by November."),
        _node(5, ["Point"], id="pt-frag-1", content="Great, let's build it."),
        _node(6, ["Source"], id="src-1", name="2026-07-23-abc.md"),
    ]
    edges = [
        _edge(1, 2, "IMPL"),
        _edge(1, 3, "IMPL"),
        _edge(3, 6, "extractedFrom"),
    ]
    return _dump(nodes, edges)


# ── shape / reconciliation ──────────────────────────────────────────────────


def test_report_reconciles_and_uses_the_declared_format():
    report = census(_curated_shaped())
    assert report["format"] == CENSUS_FORMAT
    assert report["totals"] == {"nodes": 6, "edges": 3}
    assert report["snapshot"]["declared_node_count"] == 6
    assert report["snapshot"]["counted_node_count"] == 6
    assert report["snapshot"]["declared_edge_count"] == 3
    assert report["snapshot"]["counted_edge_count"] == 3
    assert report["edges_by_type"] == {"IMPL": 2, "extractedFrom": 1}


def test_partition_invariants_hold():
    """Every node in exactly one class, one layer bucket and one predicate bucket."""
    report = census(_curated_shaped())
    totals = report["totals"]["nodes"]
    assert sum(report["label_sets"].values()) == totals
    assert sum(report["layers"].values()) == totals
    assert sum(report["provenance"]["classes"].values()) == totals
    assert sum(report["provenance"]["matrix"].values()) == totals
    predicate = report["predicate"]
    assert predicate["keep"] + predicate["drop"] == totals
    assert predicate["droppable"] + predicate["operators_kept"] == totals
    assert sum(predicate["drop_by_reason"].values()) == predicate["drop"]
    assert sum(predicate["drop_by_class"].values()) == predicate["drop"]
    assert sum(predicate["kept_by_label"].values()) == predicate["keep"]
    assert sum(predicate["dropped_by_label"].values()) == predicate["drop"]


def test_kind_histogram_counts_absent_as_a_class():
    report = census(_curated_shaped())
    kinds = report["kinds"]["pointKind"]
    assert kinds["statement"] == 2
    assert kinds["goal"] == 1
    # pts 1 (operator) and 5 (fragment) carry no pointKind at all.
    assert kinds[ABSENT] == 2
    assert sum(kinds.values()) == 5  # the five :Point nodes
    # :Source carries no kind property, so it contributes no bucket.
    assert set(report["kinds"]) == {"pointKind"}


def test_layers_bucket_multi_label_and_non_epistemic():
    nodes = [
        _node(1, ["Point"], id="p1", content="a statement long enough to pass"),
        _node(2, ["Point", "Object"], id="p2", content="a statement long enough to pass"),
        _node(3, ["Source"], id="s1", name="doc.md"),
    ]
    report = census(_dump(nodes))
    assert report["layers"]["Point"] == 1
    assert report["layers"]["multi-label"] == 1
    assert report["layers"]["non-epistemic"] == 1
    assert report["label_sets"]["Object|Point"] == 1


# ── determinism (the issue's evidence bar) ──────────────────────────────────


def test_same_snapshot_same_digest():
    dump = _curated_shaped()
    assert census(dump)["census_sha256"] == census(dump)["census_sha256"]


def test_node_order_does_not_change_the_partition():
    dump = _curated_shaped()
    shuffled = copy.deepcopy(dump)
    shuffled["nodes"] = list(reversed(shuffled["nodes"]))
    shuffled["edges"] = list(reversed(shuffled["edges"]))
    assert census(dump) == census(shuffled)


def test_edge_order_does_not_change_the_partition():
    dump = _curated_shaped()
    shuffled = copy.deepcopy(dump)
    shuffled["edges"] = list(reversed(shuffled["edges"]))
    assert census(dump)["census_sha256"] == census(shuffled)["census_sha256"]


def test_census_does_not_mutate_the_input():
    dump = _curated_shaped()
    before = copy.deepcopy(dump)
    census(dump, samples=2)
    assert dump == before


def test_samples_do_not_change_the_measured_digest():
    dump = _curated_shaped()
    without = census(dump)
    with_samples = census(dump, samples=2)
    assert without["census_sha256"] == with_samples["census_sha256"]
    assert "samples" not in without
    assert with_samples["samples"]
    for rule, rows in with_samples["samples"].items():
        assert rule in DEFAULT_RULES
        for row in rows:
            assert set(row) == {
                "key",
                "class",
                "labels",
                "kind",
                "knowledge_degree",
                "content",
            }


def test_sample_selection_is_stable_across_runs():
    dump = _curated_shaped()
    first = census(dump, samples=1)["samples"]
    second = census(dump, samples=1)["samples"]
    assert first == second


# ── provenance classes ──────────────────────────────────────────────────────


def test_provenance_class_order():
    """operator wins over extracted; extracted wins over typed; untyped last."""
    operator = _node(1, ["Point"], id="o", is_operator=True, provenanceSource="x.md",
                     pointKind="statement")
    extracted = _node(2, ["Point"], id="e", pointKind="statement", provenanceSource="x.md")
    typed = _node(3, ["Point"], id="t", pointKind="statement")
    untyped = _node(4, ["Point"], id="u", content="no kind here")
    assert classify_provenance(operator) == "operator"
    assert classify_provenance(extracted) == "extracted"
    assert classify_provenance(typed) == "typed"
    assert classify_provenance(untyped) == "untyped"


def test_classify_provenance_reads_the_edge_form_too():
    node = _node(2, ["Point"], id="e", pointKind="statement")
    assert classify_provenance(node) == "typed"
    assert classify_provenance(node, from_edge=True) == "extracted"


def test_provenance_field_presence_counts_non_empty_strings_only():
    nodes = [
        _node(1, ["Point"], id="a", pointKind="statement", provenanceSource="x.md",
              content="long enough statement for the fragment rule"),
        _node(2, ["Point"], id="b", pointKind="statement", provenanceSource="",
              content="long enough statement for the fragment rule"),
        _node(3, ["Point"], id="c", pointKind="statement", provenanceSource="   ",
              content="long enough statement for the fragment rule"),
    ]
    report = census(_dump(nodes), rules=[RULE_ISOLATED])
    assert report["provenance"]["fields"]["provenanceSource"] == 1
    assert report["provenance"]["distinct_pointer_values"] == 1


# ── degree ──────────────────────────────────────────────────────────────────


def test_provenance_edges_are_not_topology():
    """A claim whose ONLY edge is ``extractedFrom`` has no knowledge degree."""
    nodes = [
        _node(1, ["Point"], id="a", pointKind="statement", provenanceSource="x.md",
              content="a statement comfortably longer than the fragment threshold"),
        _node(2, ["Source"], id="s", name="x.md"),
    ]
    report = census(_dump(nodes, [_edge(1, 2, "extractedFrom")]), rules=[RULE_ISOLATED])
    # BOTH nodes are endpoints of the pointer edge, so neither is isolated by
    # the all-edge count …
    assert report["degree"]["isolated_all_edges"] == 0
    # … and NEITHER has a knowledge edge, which is the count that matters.
    assert report["degree"]["isolated_knowledge"] == 2
    # BOTH nodes are dropped by the isolated rule (neither has a knowledge
    # edge) — the pointer edge is not topology for either of them.
    assert report["predicate"]["drop_by_reason"][RULE_ISOLATED] == 2
    # …and the class assignment is independent of the rule hit: the claim is
    # ``extracted``, the :Source is ``untyped`` (no kind property of any layer).
    assert report["provenance"]["classes"] == {"extracted": 1, "untyped": 1}


def test_knowledge_degree_ignores_the_pointer_edge_but_counts_the_rest():
    report = census(_curated_shaped())
    # node 3 (extracted) has ONE IMPL edge and ONE extractedFrom edge.
    assert report["edges_by_type"] == {"IMPL": 2, "extractedFrom": 1}
    assert report["degree"]["isolated_by_class"]["extracted"] == 0


def test_degree_counts_both_directions():
    report = census(_curated_shaped())
    # The operator is an endpoint of two IMPL edges.
    assert report["degree"]["by_class"]["operator"]["median"] == 2
    assert report["degree"]["by_class"]["operator"]["max"] == 2
    assert report["degree"]["isolated_by_class"]["operator"] == 0


# ── the proposed predicate ──────────────────────────────────────────────────


def test_operator_is_never_dropped_even_when_every_rule_fires():
    nodes = [_node(1, ["Point"], id="op", is_operator=True, op_type="NAND", content="x")]
    report = census(_dump(nodes))
    assert report["predicate"]["keep"] == 1
    assert report["predicate"]["drop"] == 0
    assert report["predicate"]["droppable"] == 0
    assert report["predicate"]["operators_kept"] == 1


def test_extracted_rule_reads_the_property_form():
    nodes = [
        _node(1, ["Point"], id="a", pointKind="statement", provenanceSource="x.md",
              content="a statement comfortably longer than the fragment threshold"),
    ]
    report = census(_dump(nodes), rules=[RULE_EXTRACTED])
    assert report["predicate"]["drop_by_reason"][RULE_EXTRACTED] == 1


def test_extracted_rule_reads_the_edge_form():
    nodes = [
        _node(1, ["Point"], id="a", pointKind="statement",
              content="a statement comfortably longer than the fragment threshold"),
        _node(2, ["Source"], id="s", name="x.md"),
    ]
    report = census(_dump(nodes, [_edge(1, 2, "extractedFrom")]), rules=[RULE_EXTRACTED])
    assert report["predicate"]["drop_by_reason"][RULE_EXTRACTED] == 1


def test_untyped_rule_hits_a_node_with_no_kind_property():
    nodes = [
        _node(1, ["Point"], id="typed", pointKind="statement",
              content="a statement comfortably longer than the fragment threshold"),
        _node(2, ["Point"], id="untyped",
              content="another statement comfortably longer than the fragment threshold"),
    ]
    report = census(_dump(nodes), rules=[RULE_UNTYPED])
    assert report["predicate"]["drop_by_reason"][RULE_UNTYPED] == 1
    assert report["predicate"]["kept_by_label"] == {"Point/statement": 1}


def test_fragment_threshold_is_a_reported_parameter():
    nodes = [_node(1, ["Point"], id="a", pointKind="statement", content="x" * 30)]
    tight = census(_dump(nodes), rules=[RULE_FRAGMENT], fragment_max_chars=FRAGMENT_MAX_CHARS)
    loose = census(_dump(nodes), rules=[RULE_FRAGMENT], fragment_max_chars=5)
    assert tight["predicate"]["drop_by_reason"][RULE_FRAGMENT] == 1
    assert loose["predicate"]["drop_by_reason"][RULE_FRAGMENT] == 0
    assert tight["predicate"]["fragment_max_chars"] == FRAGMENT_MAX_CHARS
    assert loose["predicate"]["fragment_max_chars"] == 5


def test_duplicate_survivor_is_the_best_connected_member():
    text = "Retrieval is hybrid:  keyword plus dense."
    nodes = [
        _node(1, ["Point"], id="a", pointKind="statement", content=text.upper()),
        _node(2, ["Point"], id="b", pointKind="statement", content=text),
        _node(3, ["Point"], id="c", pointKind="statement", content=f"  {text}  "),
        _node(9, ["Point"], id="op", is_operator=True, content="IMPL(a,b)"),
    ]
    edges = [_edge(9, 3, "IMPL")]  # node 3 is the only connected member
    report = census(_dump(nodes, edges), rules=[RULE_DUPLICATE])
    duplicates = report["duplicates"]
    assert duplicates["groups"] == 1
    assert duplicates["redundant_nodes"] == 2
    assert duplicates["largest_group"] == 3
    predicate = report["predicate"]
    assert predicate["drop_by_reason"][RULE_DUPLICATE] == 2
    # The survivor is the best-connected member, so node 3 stays.
    assert predicate["keep"] == 2  # operator + survivor


def test_normalize_text_under_counts_never_over_counts():
    assert normalize_text("  Hello,  World.  ") == normalize_text("hello, world")
    assert normalize_text("Hello world") != normalize_text("Hello, world?" * 1)
    assert normalize_text("two   words") == "two words"


def test_rule_hits_are_independent_of_which_rules_are_selected():
    dump = _curated_shaped()
    full = census(dump)
    single = census(dump, rules=[RULE_ISOLATED])
    assert [r["hits"] for r in full["predicate"]["rules"]] == [
        r["hits"] for r in single["predicate"]["rules"]
    ]
    # …but only the SELECTED rule may contribute a drop reason.
    assert set(single["predicate"]["drop_by_reason"]) == set(DEFAULT_RULES)
    assert single["predicate"]["drop_by_reason"][RULE_EXTRACTED] == 0
    assert single["predicate"]["drop"] == full["predicate"]["rules"][4]["hits"]


def test_canonical_rule_order_regardless_of_caller_order():
    dump = _curated_shaped()
    forward = census(dump, rules=[RULE_EXTRACTED, RULE_ISOLATED])
    reversed_ = census(dump, rules=[RULE_ISOLATED, RULE_EXTRACTED])
    assert forward["predicate"]["rules_selected"] == [RULE_EXTRACTED, RULE_ISOLATED]
    assert forward == reversed_


def test_unknown_rule_id_is_refused():
    with pytest.raises(CensusError, match="unknown rule id"):
        census(_curated_shaped(), rules=["nonsense"])
    report = census(_curated_shaped())
    assert report["predicate"]["rules_available"] == list(DEFAULT_RULES)


def test_empty_rule_selection_is_refused():
    with pytest.raises(CensusError, match="no rules selected"):
        census(_curated_shaped(), rules=[])


def test_duplicate_rule_id_is_refused():
    with pytest.raises(CensusError, match="duplicate rule id"):
        census(_curated_shaped(), rules=[RULE_ISOLATED, RULE_ISOLATED])


def test_negative_parameters_are_refused():
    with pytest.raises(CensusError, match="fragment_max_chars"):
        census(_curated_shaped(), fragment_max_chars=-1)
    with pytest.raises(CensusError, match="samples"):
        census(_curated_shaped(), samples=-1)


# ── the cost of a cut: topology ─────────────────────────────────────────────


def test_topology_block_counts_severed_edges():
    """Dropping an IMPL endpoint severs the edge — the count must say so.

    ``retains_topology`` is a PRESERVATION verdict, so severing even one of two
    knowledge edges makes it false. ``kept_knowledge_edges > 0`` would have read
    "yes" here — a cut that severs half the topology reported as preserving it
    (review finding: the verdict column flattened 16,444/21,636 severed to "yes").
    """
    dump = _curated_shaped()
    report = census(dump, rules=[RULE_EXTRACTED])
    topology = report["predicate"]["topology"]
    # pt-extracted-1 (3) is dropped and it is an IMPL endpoint.
    assert topology["knowledge_edges"] == 2
    assert topology["severed_knowledge_edges"] == 1
    assert topology["dropped_knowledge_endpoints"] == 1
    assert topology["kept_knowledge_edges"] == 1  # > 0 …
    assert topology["retains_topology"] is False  # … and STILL not "retains"
    assert topology["kept_knowledge_edge_fraction"] == 0.5
    assert any("retains_topology is false" in w for w in report["warnings"])
    assert report["predicate"]["dropped_by_label"]["Point/statement"] == 1


def test_retains_topology_is_true_only_when_nothing_is_severed():
    """The complementary half: a cut that severs nothing IS topology-preserving."""
    report = census(_curated_shaped(), rules=[RULE_ISOLATED])
    topology = report["predicate"]["topology"]
    assert topology["severed_knowledge_edges"] == 0
    assert topology["retains_topology"] is True
    assert topology["kept_knowledge_edge_fraction"] == 1.0


def test_a_cut_that_severs_every_edge_is_a_warning_not_a_silent_success():
    """The measured counterexample this block exists for.

    A graph whose knowledge edges all run operator→extracted-claim: dropping the
    claims leaves the operators with no edges at all. Node counts alone would
    report a clean reduction, so ``retains_topology`` must be false and a
    warning must say why.
    """
    nodes = [
        _node(1, ["Point"], id="op-1", is_operator=True, op_type="IMPL", content="IMPL(a,b)"),
        _node(2, ["Point"], id="a", pointKind="statement", provenanceSource="x.md",
              content="a claim extracted from a captured conversation transcript"),
        _node(3, ["Point"], id="b", pointKind="statement", provenanceSource="x.md",
              content="another claim extracted from the same captured transcript"),
    ]
    edges = [_edge(1, 2, "IMPL"), _edge(1, 3, "IMPL")]
    dump = _dump(nodes, edges)
    everything = census(dump)
    topology = everything["predicate"]["topology"]
    assert topology["kept_knowledge_edges"] == 0
    assert topology["severed_knowledge_edges"] == 2
    assert topology["retains_topology"] is False
    assert topology["kept_operators_isolated_by_the_cut"] == 1
    assert any("retains NONE" in w for w in everything["warnings"])

    # The lesson, pinned: a cut that removes ONLY unconditional nodes costs no
    # topology. Here both claims are endpoints, so such a cut keeps everything —
    # which is the point: the destructive rule is the provenance rule, not the
    # count of nodes it removes.
    isolated_only = census(dump, rules=[RULE_ISOLATED])
    assert isolated_only["predicate"]["topology"]["kept_knowledge_edges"] == 2
    assert isolated_only["predicate"]["topology"]["retains_topology"] is True


def test_untyped_only_cut_keeps_every_edge_in_the_measured_shape():
    """On the curated shape, the untyped cut is topology-free."""
    report = census(_curated_shaped(), rules=[RULE_UNTYPED])
    topology = report["predicate"]["topology"]
    assert topology["severed_knowledge_edges"] == 0
    assert topology["kept_knowledge_edges"] == topology["knowledge_edges"]
    assert topology["retains_topology"] is True


# ── identities: reported, never refused ─────────────────────────────────────


def test_duplicate_node_keys_are_reported_not_refused():
    """A real graph carries two ``:Source`` rows per captured document.

    Refusing would make the instrument unable to measure the snapshot Phase 1
    exists to describe, and would hide the finding behind an error.
    """
    nodes = [
        _node(1, ["Source"], id="2026-07-23-abc.md", documentKind="conversation"),
        _node(2, ["Source"], url="2026-07-23-abc.md", sourceKind="document"),
        _node(3, ["Point"], id="p1", pointKind="statement",
              content="a statement comfortably longer than the fragment threshold"),
    ]
    report = census(_dump(nodes))
    identities = report["identities"]
    assert identities["duplicate_key_groups"] == 1
    assert identities["duplicate_key_nodes"] == 2
    assert identities["duplicate_key_by_label_set"] == {"Source": 2}
    assert identities["examples"] == ["2026-07-23-abc.md"]
    assert report["totals"]["nodes"] == 3
    assert any("cannot address those nodes unambiguously" in w for w in report["warnings"])


def test_unique_key_graph_has_no_identity_warning():
    report = census(_curated_shaped())
    assert report["identities"]["duplicate_key_groups"] == 0
    assert not any("shared by more than one node" in w for w in report.get("warnings", []))


def test_scan_nodes_reports_keys_and_problems():
    rows, keys, problems = scan_nodes(_curated_shaped())
    assert len(rows) == 6
    assert problems == []
    assert keys["op-1"] == [1]


# ── fail-closed on structural corruption ────────────────────────────────────


def test_dangling_edge_is_refused():
    dump = _curated_shaped()
    dump["edges"].append(_edge(1, 999, "IMPL"))
    with pytest.raises(CensusError, match="reference a dump_id the dump does not carry"):
        census(dump)


def test_non_integer_dump_id_is_refused():
    dump = _curated_shaped()
    dump["nodes"].append(_node("seven", ["Point"], id="bad"))  # type: ignore[arg-type]
    with pytest.raises(CensusError, match="not an integer"):
        census(dump)


def test_duplicate_dump_id_is_refused():
    dump = _curated_shaped()
    dump["nodes"].append(_node(1, ["Point"], id="other"))
    with pytest.raises(CensusError, match="duplicate dump_id"):
        census(dump)


def test_non_dict_prop_bag_is_refused():
    dump = _curated_shaped()
    dump["nodes"][0]["props"] = ["not", "a", "dict"]
    with pytest.raises(CensusError, match="non-dict prop bag"):
        census(dump)


@pytest.mark.parametrize("prop_bag", [[], 0, False, "", ()])
def test_falsy_non_dict_prop_bag_is_also_refused(prop_bag):
    """``or {}`` used to normalise every falsy non-dict to a dict and accept it.

    Accepted-as-empty makes the node ``untyped`` (it has no kind property), and
    ``RULE_UNTYPED`` then PROPOSES DROPPING it — corruption converted into a drop
    recommendation by the instrument whose docstring promises a malformed dump is
    refused with the full problem list.
    """
    dump = _curated_shaped()
    dump["nodes"][0]["props"] = prop_bag
    with pytest.raises(CensusError, match="non-dict prop bag"):
        census(dump)


def test_absent_prop_bag_is_still_accepted():
    """The fail-closed check must not fire on a node that simply carries props=None."""
    dump = _curated_shaped()
    dump["nodes"][0]["props"] = None
    report = census(dump)
    assert report["totals"]["nodes"] == len(dump["nodes"])


def test_non_list_label_set_is_refused():
    dump = _curated_shaped()
    dump["nodes"][0]["labels"] = "Point"
    with pytest.raises(CensusError, match="non-list label set"):
        census(dump)


def test_non_list_edge_container_is_refused():
    dump = _curated_shaped()
    dump["edges"] = {"not": "a list"}
    with pytest.raises(CensusError, match="edges is not a list"):
        census(dump)


def test_edge_without_type_is_refused():
    dump = _curated_shaped()
    dump["edges"].append({"src": 1, "dst": 2})
    with pytest.raises(CensusError, match="no relationship type"):
        census(dump)


def test_missing_node_list_is_refused():
    with pytest.raises(CensusError, match="no node list"):
        census({"format": "tortoise-logical-dump-v1"})


# ── diff ────────────────────────────────────────────────────────────────────


def test_flatten_counts_records_booleans_as_numbers():
    """A bool is 0/1 here, never skipped.

    ``predicate.topology.retains_topology`` is the report's headline verdict; if
    ``flatten_counts`` skipped bools, a diff could not show it flip — the one
    comparison the plan's "and then the diff" exists to make.
    """
    report = census(_curated_shaped())
    flat = flatten_counts(report)
    assert flat == flatten_counts(report)
    assert flat["totals.nodes"] == 6
    assert flat["predicate.keep"] == report["predicate"]["keep"]
    assert flat["predicate.topology.retains_topology"] in (0, 1)
    assert not any(isinstance(v, bool) for v in flat.values())


def test_diff_reports_only_counts_that_moved():
    small = _curated_shaped()
    bigger = copy.deepcopy(small)
    bigger["nodes"].append(
        _node(7, ["Point"], id="extra", pointKind="goal",
              content="A new goal added between the two snapshots.")
    )
    bigger["node_count"] = 7
    diff = diff_census(small, bigger)
    assert diff["format"] == f"{CENSUS_FORMAT}+diff"
    moved = diff["moved"]
    assert moved["totals.nodes"] == {"before": 6, "after": 7, "delta": 1}
    assert moved["snapshot.counted_node_count"]["delta"] == 1
    assert "totals.edges" not in moved  # unchanged paths are omitted, not zeroed
    assert diff["changed_paths"] == len(moved)
    assert diff["before_sha256"] == census(small)["census_sha256"]


def test_diff_of_identical_snapshots_moves_nothing():
    dump = _curated_shaped()
    diff = diff_census(dump, copy.deepcopy(dump))
    assert diff["moved"] == {}
    assert diff["changed_paths"] == 0
    assert diff["before_sha256"] == diff["after_sha256"]


def test_diff_refuses_an_unlike_instrument():
    """A dump format mismatch is REFUSED — it is not a moved row.

    Both reports are stamped by ``census()`` in the same call, so the old
    ``report["format"] != report["format"]`` guard was unreachable; the dumps'
    own ``format`` / ``dump_revision`` genuinely can differ, and a diff across
    them would report a schema change as drifted data.
    """
    dump = _curated_shaped()
    other = copy.deepcopy(dump)
    other["format"] = "some-other-dump-v9"
    with pytest.raises(CensusError, match="unlike instruments"):
        diff_census(dump, other)

    revised = copy.deepcopy(dump)
    revised["dump_revision"] = 3
    with pytest.raises(CensusError, match="dump_revision"):
        diff_census(dump, revised)


def test_diff_reports_a_moved_graph_name_instead_of_refusing_it():
    """The plan's own comparison: local snapshot vs hosted graph.

    Their ``graph_name`` (and ``dumped_at``) legitimately differ, so the difference
    must be REPORTED — that is the drift a reader is looking for — not refused.
    """
    dump = _curated_shaped()
    other = copy.deepcopy(dump)
    other["graph_name"] = "tortoise-hosted"
    diff = diff_census(dump, other)
    assert diff["moved_labels"]["snapshot.graph_name"] == {
        "before": "test",
        "after": "tortoise-hosted",
    }
    assert diff["changed_paths"] == len(diff["moved"]) + 1


def test_diff_shows_a_retains_topology_flip():
    """The headline verdict must be diffable, or the diff is blind to the change.

    Two graphs that differ only in whether the IMPL target is droppable: under
    ``extracted`` the first severs the edge (retains_topology false) and the
    second keeps it (true). ``flatten_counts`` used to skip bools, so this flip
    produced ``moved == {}``.
    """
    def shaped(with_pointer: bool) -> dict:
        claim = _node(
            2, ["Point"], id="claim", pointKind="statement",
            content="a statement comfortably longer than the fragment threshold",
        )
        if with_pointer:
            claim["props"]["provenanceSource"] = "x.md"
        return _dump(
            [_node(1, ["Point"], id="op", is_operator=True, op_type="IMPL",
                      content="IMPL(op,claim)"), claim],
            [_edge(1, 2, "IMPL")],
        )

    before = shaped(with_pointer=True)
    after = shaped(with_pointer=False)
    assert census(before)["predicate"]["topology"]["retains_topology"] is False
    assert census(after)["predicate"]["topology"]["retains_topology"] is True

    diff = diff_census(before, after, fragment_max_chars=0)
    assert diff["moved"]["predicate.topology.retains_topology"] == {
        "before": 0,
        "after": 1,
        "delta": 1,
    }


# ── the operator test is the CONSUMER's test ────────────────────────────────


@pytest.mark.parametrize("flag", [True, 1, "true", "yes"])
def test_truthy_non_boolean_is_operator_like_retype_reads_it(flag):
    """``retype`` decides "operator, never move" by TRUTHINESS (``retype.py``).

    A census stricter than its consumer classified a truthy non-boolean
    ``is_operator`` as ``untyped`` and proposed dropping it — the exact topology
    Phase 2 refuses to move. Divergence here fails open toward losing edges.
    """
    nodes = [
        _node(1, ["Point"], id="op", is_operator=flag, op_type="IMPL", content="x"),
        _node(2, ["Point"], id="claim", pointKind="statement",
              content="a statement comfortably longer than the fragment threshold"),
    ]
    report = census(_dump(nodes, [_edge(1, 2, "IMPL")]))
    assert report["provenance"]["classes"]["operator"] == 1
    assert report["predicate"]["droppable"] == 1
    assert report["predicate"]["operators_kept"] == 1
    assert classify_provenance(nodes[0]) == "operator"


# ── the duplicate reason must be true under the default cut ────────────────


def test_duplicate_reason_does_not_claim_the_survivor_is_kept():
    """Survivors are chosen BEFORE the rules run, so an earlier rule can drop them.

    The published reason said the duplicate's twin is "already kept"; in this dump
    the better-connected survivor is ``extracted`` and is dropped, while its
    redundant twin is attributed to ``duplicate``.
    """
    from tortoise.graph_census import RULE_REASONS

    assert "already kept" not in RULE_REASONS[RULE_DUPLICATE]
    nodes = [
        _node(1, ["Point"], id="op", is_operator=True, op_type="IMPL", content="x"),
        _node(2, ["Point"], id="survivor", pointKind="statement",
              provenanceSource="x.md",
              content="the same long sentence about the graph twice over"),
        _node(3, ["Point"], id="twin", pointKind="statement",
              content="The same long sentence about the graph twice over."),
    ]
    report = census(_dump(nodes, [_edge(1, 2, "IMPL")]))
    reasons = report["predicate"]["drop_by_reason"]
    assert reasons[RULE_EXTRACTED] == 1  # the SURVIVOR, dropped as extracted
    assert reasons[RULE_DUPLICATE] == 1  # its twin
    assert report["predicate"]["keep"] == 1  # only the operator


# ── the CLI ─────────────────────────────────────────────────────────────────


def _write(tmp_path, dump: dict, name: str = "dump.json"):
    path = tmp_path / name
    path.write_text(json.dumps(dump), encoding="utf-8")
    return path


def test_cli_writes_a_report(tmp_path):
    from tools.graph_census import main

    dump_path = _write(tmp_path, _curated_shaped())
    out = tmp_path / "census.json"
    assert main(["--dump", str(dump_path), "--out", str(out), "--samples", "1"]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["format"] == CENSUS_FORMAT
    assert report["totals"]["nodes"] == 6
    assert report["samples"]


def test_cli_stdout_when_no_out(tmp_path, capsys):
    from tools.graph_census import main

    dump_path = _write(tmp_path, _curated_shaped())
    assert main(["--dump", str(dump_path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["totals"]["nodes"] == 6


def test_cli_refuses_a_corrupt_dump_with_exit_2(tmp_path, capsys):
    from tools.graph_census import main

    dump = _curated_shaped()
    dump["edges"].append(_edge(1, 999, "IMPL"))
    dump_path = _write(tmp_path, dump)
    assert main(["--dump", str(dump_path)]) == 2
    captured = capsys.readouterr()
    assert "REFUSED — no report was written" in captured.err
    assert captured.out == ""


def test_cli_refuses_an_unknown_rule_before_reading_the_dump(tmp_path):
    from tools.graph_census import main

    with pytest.raises(SystemExit):
        main(["--dump", str(tmp_path / "missing.json"), "--rules", "nonsense,isolated"])


def test_cli_diff_mode(tmp_path, capsys):
    from tools.graph_census import main

    first = _write(tmp_path, _curated_shaped(), "a.json")
    bigger = _curated_shaped()
    bigger["nodes"].append(
        _node(7, ["Point"], id="extra", pointKind="goal",
              content="A new goal added between the two snapshots.")
    )
    bigger["node_count"] = 7
    second = _write(tmp_path, bigger, "b.json")
    assert main(["--dump", str(first), "--diff", str(second)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["moved"]["totals.nodes"]["delta"] == 1


def test_cli_usage_errors_exit_1_not_the_refusal_code(tmp_path):
    """``2`` means REFUSED (a dump that cannot be measured); usage is ``1``.

    argparse's default is ``2``, so a caller keying on the exit code could not
    tell a mistyped flag from corruption.
    """
    from tools.graph_census import main

    with pytest.raises(SystemExit) as no_dump:
        main([])
    assert no_dump.value.code == 1

    dump_path = _write(tmp_path, _curated_shaped())
    with pytest.raises(SystemExit) as samples_with_diff:
        main(["--dump", str(dump_path), "--diff", str(dump_path), "--samples", "3"])
    assert samples_with_diff.value.code == 1

    # …and a genuinely refused dump still exits 2, so the two stay distinct.
    corrupt = _curated_shaped()
    corrupt["edges"].append(_edge(1, 999, "IMPL"))
    assert main(["--dump", str(_write(tmp_path, corrupt, "bad.json"))]) == 2


def test_cli_requires_a_dump(tmp_path):
    from tools.graph_census import main

    with pytest.raises(SystemExit):
        main([])

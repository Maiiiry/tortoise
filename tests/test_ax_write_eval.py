"""The write-pipeline eval instrument's scoring rules (#9675 Step 1).

These pin the RULES, not a run's outcome: the committed per-input records are
evidence and must stay editable-by-reason (a Step-3 re-run replaces them), while
the scoring semantics are what the next lane relies on to read a delta. Each
test states the write failure it exists to catch, because the instrument is only
as good as the failures it can still see.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLD = REPO_ROOT / "eval" / "how-to-use-tortoise" / "step1" / "inputs.json"


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "ax_write_eval", REPO_ROOT / "tools" / "ax_write_eval.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


axe = _load_tool()


def _record(nodes=(), edges=(), operators=(), sources=(), mitigations=()):
    nodes = list(nodes)
    counts = {layer: 0 for layer in axe.LAYERS}
    for node in nodes:
        counts[node["layer"]] += 1
    return {
        "nodes": nodes,
        "edges": list(edges),
        "counts": counts,
        "operators": list(operators),
        "sources": list(sources),
        "relations": sorted({e["relation"] for e in edges}),
        "mitigations": list(mitigations),
    }


def _node(layer, text, kind="", status="", is_operator=False, op_type="",
          kind_prop=None, foreign_kinds=None):
    return {
        "layer": layer,
        "labels": [layer],
        "text": text,
        "kind": kind,
        "kind_prop": kind_prop if kind_prop is not None else axe.KIND_PROP.get(layer, ""),
        "foreign_kinds": dict(foreign_kinds or {}),
        "is_operator": is_operator,
        "op_type": op_type,
        "status": status,
        "url": "",
    }


def _gold(entry):
    """A one-input gold document — the frozen file is not mutated by tests."""
    return {"inputs": [entry]}


def test_same_layer_preference_does_not_report_confusion_when_the_item_is_also_right():
    """Catch the instrument's own worst false positive.

    An agent that writes the claim AND an Object naming the same figure filed
    the item on the expected layer. Reporting "layer confusion" because the
    shorter mention sorted first would bury the real finding and make the
    document teach a rule nobody broke.

    The fixture is DISCRIMINATING on purpose (#9695 review P1): the wrong-layer
    Point is an EXACT match (1.0) and the correct-layer Subject only a
    containment match (0.867), so the Point ranks first on score alone and the
    same-layer preference is the only thing that can produce `OK`. The previous
    fixture put both nodes at 0.8 — a tie broken by list order — so the whole
    preference could be DELETED and this test still passed.
    """
    nodes = [
        _node("Point", "Marta Reyes", kind="statement"),
        _node("Subject", "Marta Reyes, CFO", kind="naturalPerson"),
    ]
    gold = _gold(
        {
            "id": "x",
            "expect": [{"item": "Marta Reyes", "layer": "subject", "kind": ["naturalPerson"]}],
        }
    )

    # Non-vacuity: asked for a layer NEITHER node is on, `_match` returns the
    # node the raw ranking put first — the wrong-layer Point, at 1.0. Only the
    # preference can turn that into the Subject.
    ranked_first, score, _ = axe._match("Marta Reyes", nodes, "Event")
    assert (ranked_first["layer"], score) == ("Point", 1.0)
    assert axe._match("Marta Reyes", nodes, "Subject")[0]["layer"] == "Subject"

    result = axe.score(_record(nodes=nodes), gold, "x")
    assert not [
        f for f in result["findings"] if "LAYER confusion" in f["finding"]
    ], result["findings"]
    assert result["item_rows"][0]["verdict"] == "OK"


def test_same_layer_preference_has_a_floor_so_a_weak_mention_cannot_outrank_the_real_match():
    """The bug a fresh-context review found in this instrument (fixed here).

    A same-layer node that shares ONE token must not outrank a different-layer
    containment match: doing so accuses a node the agent never wrote the item as
    and hides the real layer failure. Measured before the floor: the goal
    "sub-5-minute ingestion latency" was attributed to the Object "Ingestion
    pipeline" (1 of 6 tokens) and the layer P0 disappeared.
    """
    gold = _gold(
        {
            "id": "x",
            "expect": [
                {
                    "item": "sub 5 minute ingestion latency",
                    "layer": "object",
                    "kind": ["goal", "target"],
                }
            ],
        }
    )
    doc = _record(
        nodes=[
            _node("Object", "Ingestion pipeline", kind="workflow"),
            _node(
                "Point",
                "Platform team goal: sub-5-minute end-to-end ingestion latency by 2027-03-31",
                kind="target",
            ),
        ]
    )
    result = axe.score(doc, gold, "x")
    assert result["item_rows"][0]["verdict"] == "LAYER"
    assert result["severity_counts"]["P0"] == 1


def test_layer_confusion_is_reported_when_the_item_exists_only_on_another_layer():
    """The failure #9675 exists for: an actor filed as a claim."""
    gold = _gold(
        {
            "id": "x",
            "expect": [{"item": "Marta Reyes", "layer": "subject", "kind": ["naturalPerson"]}],
        }
    )
    doc = _record(nodes=[_node("Point", "Marta Reyes is the new CFO", kind="statement")])
    result = axe.score(doc, gold, "x")
    assert result["item_rows"][0]["verdict"] == "LAYER"
    assert result["severity_counts"]["P0"] == 1


def test_pack_declared_kind_is_not_an_undeclared_kind():
    """`disbursement`/`tranche` are declared by the venture pack, not invented.

    The first draft of the instrument imported only the core §5 sets, so every
    write that followed a loaded pack was reported as a failure — the false
    failure that inflated the first draft's undeclared-kind count.
    """
    assert axe.kind_status("Event", "disbursement") == "PACK"
    assert axe.kind_status("Object", "tranche") == "PACK"
    assert axe.kind_status("Point", "statement") == "CORE"
    assert axe.kind_status("Point", "issue") == "UNDECLARED"


def test_the_declared_subject_spelling_is_not_a_failure():
    """`core:organization` is the DECLARED §5 key; the bare form is its alias.

    The first draft hardcoded the bare forms and reported the canonical spelling
    as "an invented namespace prefix" — a rule the product's own vocabulary
    contradicts, taught to the next agent by this very document.
    """
    assert axe.kind_status("Subject", "core:organization") == "CORE"
    assert axe.kind_status("Subject", "organization") == "CORE"
    assert axe.kind_status("Subject", "person") == "UNDECLARED"
    gold = _gold(
        {
            "id": "x",
            "expect": [{"item": "Acme", "layer": "subject", "kind": ["organization"]}],
        }
    )
    doc = _record(nodes=[_node("Subject", "Acme", kind="core:organization")])
    result = axe.score(doc, gold, "x")
    assert result["item_rows"][0]["verdict"] == "OK"


def test_a_mitigation_is_not_scored_when_the_gold_says_nothing_about_it():
    """`expect_mitigation: false` is not a default — it is an assertion.

    Boilerplate `false` on nine inputs turned the research input's sanctioned
    mitigation of an under-evidenced inference into a P0.
    """
    gold = _gold({"id": "x", "expect": []})  # no expect_mitigation key
    doc = _record(
        nodes=[_node("Point", "", is_operator=True, op_type="IMPL")],
        operators=[{"op_type": "IMPL"}],
        mitigations=[{"relation": "mitigated_by"}],
    )
    result = axe.score(doc, gold, "x")
    assert not [f for f in result["findings"] if "mitigation" in f["finding"]]


def test_a_duplicate_identity_is_measured_not_merely_described():
    """The gold declares `duplicate_expected`; the check must exist and count NODES.

    A claim that merely starts with the company name is a claim ABOUT it — the
    first draft counted those and reported five duplicates on a run that folded
    the alias correctly.
    """
    gold = _gold(
        {
            "id": "x",
            "duplicate_expected": {"of": "Northwind Logistics", "max_nodes": 1},
            "expect": [],
        }
    )
    one = _record(
        nodes=[
            _node("Object", "Northwind Logistics", kind="other"),
            _node("Point", "Northwind Logistics confirmed the renewal", kind="statement"),
            _node("Source", "Northwind Logistics — signed SOW", kind="document"),
        ]
    )
    assert not [f for f in axe.score(one, gold, "x")["findings"] if "identity" in f["finding"]]
    two = _record(
        nodes=[
            _node("Object", "Northwind Logistics", kind="other"),
            _node("Object", "Northwind Logistics Ltd", kind="other"),
        ]
    )
    result = axe.score(two, gold, "x")
    assert any("one identity" in f["finding"] for f in result["findings"])


def test_undeclared_kind_is_distinguished_from_a_declared_but_wrong_kind():
    """"A kind nothing declares is a kind nothing routes to" (issue step 2).

    Both fail, but they teach different lessons: an invented `person` means the
    vocabulary was never consulted; `observation`-instead-of-`statement` is a
    real canonical kind, just the wrong one.
    """
    gold = _gold(
        {
            "id": "x",
            "expect": [
                {"item": "Dana", "layer": "subject", "kind": ["naturalPerson"]},
                {"item": "churn figure", "layer": "point", "kind": ["statement"]},
            ],
        }
    )
    doc = _record(
        nodes=[
            _node("Subject", "Dana", kind="person"),
            _node("Point", "churn figure was 4.1%", kind="observation"),
        ]
    )
    result = axe.score(doc, gold, "x")
    kinds = {row["item"]: row["verdict"] for row in result["item_rows"]}
    assert kinds["Dana"] == "KIND-UNDECLARED"
    assert kinds["churn figure"] == "KIND"
    # `observation` is a DECLARED legacy point kind — the finding must say so
    message = next(f["finding"] for f in result["findings"] if "churn figure" in f["finding"])
    assert "declared, but not the kind" in message


def test_a_contradiction_handled_by_mitigation_is_a_P0_even_with_an_operator_present():
    """The pipeline's most expensive confusion: relevance dampened where truth is at issue."""
    gold = _gold({"id": "x", "expect_nand": True, "expect": []})
    doc = _record(
        nodes=[_node("Point", "", is_operator=True, op_type="IMPL")],
        operators=[{"op_type": "IMPL"}],
        mitigations=[{"relation": "mitigated_by", "from": "op", "to": "claim"}],
    )
    result = axe.score(doc, gold, "x")
    p0 = [f for f in result["findings"] if f["severity"] == "P0"]
    assert any("NO NAND" in f["finding"] for f in p0)


def test_one_write_is_charged_once_when_a_contradiction_is_mitigated_instead_of_nand():
    """One act, one P0.

    A NAND-less contradiction answered with a dampener is a SINGLE mistake. It
    used to be billed twice — a P0 for the missing NAND *and* a P0 for the
    mitigation — so one write counted twice in the aggregate a Step-3 delta is
    read against (#9695 review P1). The dampener must still be named inside the
    finding, and still reported as written.
    """
    gold = _gold({"id": "x", "expect_nand": True, "expect_mitigation": False, "expect": []})
    doc = _record(
        nodes=[_node("Point", "", is_operator=True, op_type="IMPL")],
        operators=[{"op_type": "IMPL"}],
        mitigations=[{"relation": "mitigated_by", "from": "op", "to": "claim"}],
    )
    result = axe.score(doc, gold, "x")
    truth = [f for f in result["findings"] if f["item"] == "(truth-vs-relevance)"]
    assert len(truth) == 1, [f["finding"] for f in truth]
    assert "mitigations: 1" in truth[0]["finding"]
    assert "relevance was dampened where truth was at issue" in truth[0]["finding"]
    assert result["severity_counts"]["P0"] == 1
    assert result["mitigations"] == ["mitigated_by: op -> claim"]


def test_a_dampener_on_an_input_with_no_contradiction_is_its_own_P0():
    """The genuinely separate case: `expect_mitigation: false` with truth NOT at issue.

    The `elif` in `score()` must still fire when nothing was NAND-less —
    otherwise fixing the double charge would have deleted the rule rather than
    made it disjoint from the missing-NAND rule.
    """
    gold = _gold(
        {"id": "x", "expect_mitigation": False, "expect_nand": False, "expect": []}
    )
    doc = _record(
        nodes=[_node("Point", "", is_operator=True, op_type="IMPL")],
        operators=[{"op_type": "IMPL"}],
        mitigations=[{"relation": "mitigated_by"}],
    )
    result = axe.score(doc, gold, "x")
    truth = [f for f in result["findings"] if f["item"] == "(truth-vs-relevance)"]
    assert len(truth) == 1
    assert "INVALIDATES/mitigation" in truth[0]["finding"]


def test_a_kind_written_on_another_layer_property_is_its_own_finding():
    """`subjectKind` on an Object is invisible to every reader of an Object.

    One cross-property `coalesce` read a foreign kind back as the layer's own
    kind, so the worst version of this write (a legal word on the wrong axis —
    `eventKind: meeting` on an Object) passed, and `KIND_PROP` was declared and
    referenced nowhere (#9695 review P2).
    """
    node = axe._node_from_row(
        ["Object"], "Northwind Logistics", {"subjectKind": "organization"},
        False, "", "", "", "n1",
    )
    assert node["kind"] == "", "a foreign-axis kind must not become the layer's kind"
    assert node["kind_prop"] == "objectKind"
    assert node["foreign_kinds"] == {"subjectKind": "organization"}
    assert node["id"] == "n1"

    gold = _gold(
        {"id": "x", "expect": [{"item": "Northwind Logistics", "layer": "object"}]}
    )
    result = axe.score(_record(nodes=[node]), gold, "x")
    rows = [f for f in result["findings"] if f["item"] == "Northwind Logistics"]
    assert rows, result["findings"]
    assert "CROSS-AXIS" in rows[0]["finding"]
    assert "subjectKind='organization'" in rows[0]["finding"]
    assert result["mitigations"] == []


def test_the_graph_wide_census_catches_a_kind_on_the_wrong_axis():
    """An unnamed node's foreign-axis kind is invisible unless the census says so.

    `kind` is empty on the layer's own property, so the undeclared-kind census
    skips it and the unkinded check would describe it as "no kind property" — a
    different (and less true) finding than "you wrote it on the wrong property".
    """
    gold = _gold({"id": "x", "expect": []})
    doc = _record(
        nodes=[
            _node("Object", "Renewal window", foreign_kinds={"eventKind": "meeting"}),
        ]
    )
    result = axe.score(doc, gold, "x")
    census = [f for f in result["findings"] if "cross-axis" in f["finding"]]
    assert census, result["findings"]
    assert "eventKind='meeting'" in census[0]["finding"]
    assert "carry no kind property" not in census[0]["finding"]
    assert result["cross_axis_kinds"] == ["Object 'Renewal window' carries eventKind='meeting'"]


def test_every_layer_reads_its_own_kind_property():
    """The record must say WHICH property carried the kind, per layer."""
    assert set(axe.KIND_PROPS) == set(axe.KIND_PROP.values())
    derived = tuple(axe.KIND_PROP[layer] for layer in axe.LAYERS)
    assert derived == axe.KIND_PROPS, "KIND_PROPS must be derived from KIND_PROP"
    for layer, prop in axe.KIND_PROP.items():
        node = axe._node_from_row([layer], "t", {prop: "x"}, False, "", "", "")
        read = node["kind_prop"], node["kind"], node["foreign_kinds"]
        assert read == (prop, "x", {}), f"{layer} did not read {prop}"
    # infrastructure labels are not knowledge nodes
    assert axe._node_from_row(["Meta"], "t", {}, False, "", "", "") is None


def test_record_reads_the_kind_columns_in_KIND_PROPS_order(monkeypatch):
    """Pin `record()`'s positional column order — a silent mis-zip swaps kinds.

    The five kind properties come back as separate columns now, so one off-by-one
    in the slice would report another layer's kind as this node's. No database:
    the seam is `_rows`.
    """

    class _SDK:
        closed = False

        def close(self):
            self.closed = True

    sdk = _SDK()
    monkeypatch.setattr(axe, "_open", lambda uri: (sdk, object()))
    node_row = (
        ["Object", "Node"], "Northwind ARR", "", "", "other", "", "",
        False, "", "live", "https://example.invalid/nw", "n7",
    )
    edge_row = ("aboutObject", "p", "Northwind ARR", ["Point"], ["Object"], "p1", "n7")
    seen: list[str] = []

    def fake_rows(proj, cypher):
        seen.append(cypher)
        return [edge_row] if "-[e]->" in cypher else [node_row]

    monkeypatch.setattr(axe, "_rows", fake_rows)
    doc = axe.record("memory://x")

    assert sdk.closed, "record() must close the SDK it opened"
    node = doc["nodes"][0]
    assert (node["layer"], node["kind"], node["kind_prop"]) == ("Object", "other", "objectKind")
    assert node["foreign_kinds"] == {}
    assert (node["status"], node["url"], node["id"]) == (
        "live", "https://example.invalid/nw", "n7",
    )
    assert doc["counts"]["Object"] == 1
    assert doc["edges"][0] == {
        "relation": "aboutObject",
        "from": "p",
        "to": "Northwind ARR",
        "from_layer": "Point",
        "to_layer": "Object",
        "from_id": "p1",
        "to_id": "n7",
    }
    for prop in axe.KIND_PROPS:
        assert f"coalesce(n.{prop}" in seen[0], f"{prop} is not a column of the node query"


def test_supersession_must_leave_a_superseded_node_behind():
    gold = _gold({"id": "x", "expect_supersede": "3.2 over 4.1", "expect": []})
    doc = _record(nodes=[_node("Point", "churn 3.2%", kind="statement", status="live")])
    result = axe.score(doc, gold, "x")
    assert any("superseded" in f["finding"] for f in result["findings"])


def test_the_graph_wide_census_reports_what_the_gold_never_names():
    """Deleting the census used to leave every test green while 8 of the findings vanished.

    The census is what keeps a run from reading clean: `score()` audits the nodes
    the gold names, so a gold that happens to match says nothing about the rest of
    the graph.
    """
    gold = _gold(
        {"id": "x", "expect": [{"item": "churn", "layer": "point", "kind": ["statement"]}]}
    )
    doc = _record(
        nodes=[
            _node("Point", "churn was 3.2%", kind="statement"),
            _node("Point", "dashboard double-counted an account", kind="incident"),
        ]
    )
    result = axe.score(doc, gold, "x")
    census = [f for f in result["findings"] if f["finding"].startswith("graph-wide")]
    assert len(census) == 1
    assert result["kind_census"] == {"Point": ["incident"]}
    # the negative control: a kind already reported against a gold ITEM is not
    # counted a second time by the census
    gold2 = _gold(
        {"id": "x", "expect": [{"item": "churn", "layer": "point", "kind": ["statement"]}]}
    )
    doc2 = _record(nodes=[_node("Point", "churn was 3.2%", kind="incident")])
    result2 = axe.score(doc2, gold2, "x")
    assert result2["kind_census"] == {}


def test_two_homes_is_a_P1_and_absence_is_a_P0():
    """A review found three P0s accusing an absence the graph disproved.

    When the expected layer ALSO carries the item, the finding is "one fact, two
    layers" — weaker than an absence, and differently instructive — so the
    instrument must say which of the two it is.
    """
    gold = _gold(
        {
            "id": "x",
            "expect": [
                {"item": "marta reyes joined northwind logistics", "layer": "event",
                 "kind": ["roleCreated", "occurrence"]},
                {"item": "tobias lund", "layer": "subject", "kind": ["naturalPerson"]},
            ],
        }
    )
    two_homes = _record(
        nodes=[
            _node(
                "Event",
                "Marta Reyes joins Northwind Logistics as its new Chief Financial "
                "Officer this week, the company confirmed on Tuesday",
                kind="occurrence",
            ),
            # the exact-name Point wins `_match`; the Event still carries the item
            _node("Point", "marta reyes joined northwind logistics", kind="statement"),
            _node("Point", "Tobias Lund is the CEO", kind="statement"),
        ]
    )
    result = axe.score(two_homes, gold, "x")
    verdicts = {row["item"]: row["verdict"] for row in result["item_rows"]}
    assert verdicts["marta reyes joined northwind logistics"] == "TWO-HOMES"
    assert verdicts["tobias lund"] == "LAYER"


def test_the_committed_gold_file_has_ten_inputs_with_pairwise_confusions():
    """Step 1's own contract: ten representative inputs, each naming the ADJACENT pair."""
    gold = json.loads(GOLD.read_text())
    assert len(gold["inputs"]) == 10
    assert len({entry["id"] for entry in gold["inputs"]}) == 10
    cells = {entry["cell"] for entry in gold["inputs"]}
    assert {"portfolio update", "meeting transcript", "research paper"} <= cells
    for entry in gold["inputs"]:
        assert entry["confuses"], f"{entry['id']} names no adjacent-layer confusion"
        assert entry["expect"], f"{entry['id']} has no frozen gold structure"


def test_every_committed_run_record_names_its_input_and_its_writes():
    """The records are the evidence: an unreadable one silently disarms Step 3's delta."""
    runs = sorted((GOLD.parent / "runs").glob("*/score.json"))
    assert len(runs) == 10, f"expected 10 committed run records, found {len(runs)}"
    for path in runs:
        record = json.loads(path.read_text())
        assert record["input"] == path.parent.name
        assert set(record) >= {
            "counts",
            "item_rows",
            "findings",
            "severity_counts",
            "kind_census",
            "packs",
        }
        # a verdict without its evidence cannot be audited
        graph = json.loads((path.parent / "graph.json").read_text())
        assert set(graph) >= {"nodes", "edges", "counts"}
        assert graph["counts"] == record["counts"], (
            f"{path.parent.name}: the verdict's counts disagree with the graph "
            f"they were read from"
        )


def test_the_reported_totals_match_the_committed_records():
    """The report's headline must be a function of the records, not of memory.

    A reviewer found the first draft's totals wrong; this pin is what makes the
    next stale number a red test instead of a claim. The count moved 13 -> 12
    when one write stopped being charged twice (#9695 review P1: a NAND-less
    contradiction answered with a dampener was a P0 for the missing NAND *and* a
    P0 for the mitigation).
    """
    totals = {"P0": 0, "P1": 0}
    with_p0 = 0
    for path in sorted((GOLD.parent / "runs").glob("*/score.json")):
        record = json.loads(path.read_text())
        totals["P0"] += record["severity_counts"]["P0"]
        totals["P1"] += record["severity_counts"]["P1"]
        with_p0 += 1 if record["severity_counts"]["P0"] else 0
    assert totals == {"P0": 12, "P1": 33}, f"report claims 12/33, records say {totals}"
    assert with_p0 == 6, f"report claims 6 of 10 inputs carry a P0, records say {with_p0}"
    doc = (REPO_ROOT / "docs" / "ax" / "how-to-use-tortoise-step1-eval.md").read_text()
    assert "12 P0 and 33 P1" in doc, "the evidence doc's headline drifted from the records"
    assert "6 of 10 inputs" in doc
    index = (REPO_ROOT / "docs" / "00_index.md").read_text()
    assert "12 P0 / 33 P1" in index, "the docs index headline drifted from the records"


def test_the_documented_failure_classes_reconcile_with_the_committed_records():
    """The class table must be derivable from the `score.json` files alone.

    A reviewer caught the first draft's class counts contradicting its own
    records ("1 input, 2 findings" for a class the records carried three of, a
    research-paper row silent about the P0 it had). Counting the classes here —
    from the committed verdicts, by the wording the verdicts use — is what makes
    the next stale number a red test.
    """
    runs = [
        json.loads(path.read_text())
        for path in sorted((GOLD.parent / "runs").glob("*/score.json"))
    ]

    def count(needle: str) -> tuple[int, int]:
        hits = [
            (record["input"], f)
            for record in runs
            for f in record["findings"]
            if needle in f["finding"] or needle == f["item"]
        ]
        return len(hits), len({name for name, _ in hits})

    classes = {
        "neither a core": (16, 6),  # a kind no vocabulary declares, on a gold item
        "graph-wide": (8, 8),  # the census beyond the gold
        "LAYER confusion": (8, 4),
        "two homes:": (3, 3),
        "kind outside the frozen set": (5, 3),
        "no node represents it": (2, 2),
        "a NAND was written where nothing contradicts": (1, 1),
        "(provenance)": (1, 1),
        "nodes carry one identity": (0, 0),
        "INVALIDATES/mitigation": (0, 0),  # truth-vs-relevance is charged once
    }
    measured = {needle: count(needle) for needle in classes}
    assert measured == classes, measured

    # One P0 for the one contradiction: the dampener is named INSIDE it, so the
    # record carries exactly one truth-vs-relevance P0 for `contradicting_claim`.
    contradicting = next(r for r in runs if r["input"] == "contradicting_claim")
    truth_p0 = [
        f
        for f in contradicting["findings"]
        if f["item"] == "(truth-vs-relevance)" and f["severity"] == "P0"
    ]
    assert len(truth_p0) == 1, [f["finding"] for f in truth_p0]
    assert "mitigations: 1" in truth_p0[0]["finding"]

    doc = (REPO_ROOT / "docs" / "ax" / "how-to-use-tortoise-step1-eval.md").read_text()
    for claim in (
        "16 scored items across 6 inputs",
        "8 items across 4 inputs",
        "3 items across 3 inputs",
        "1 input, 1 finding",
    ):
        assert claim in doc, f"the class table no longer states {claim!r}"


def test_rescoring_a_committed_graph_reproduces_its_score():
    """The audit path: a verdict must be a pure function of the evidence beside it.

    `score --graph <committed graph.json>` is how a reader checks a number
    without a database — and it is what makes "every number is read from the
    records" falsifiable. Re-scoring must reproduce the committed `score.json`
    BYTE for byte, for all ten runs.
    """
    gold = json.loads(GOLD.read_text())
    runs = sorted((GOLD.parent / "runs").glob("*/graph.json"))
    assert len(runs) == 10, f"expected 10 committed graphs, found {len(runs)}"
    for graph_path in runs:
        run_dir = graph_path.parent
        graph = json.loads(graph_path.read_text())
        committed = json.loads((run_dir / "score.json").read_text())
        rescored = axe.score(graph, gold, run_dir.name)
        assert json.dumps(rescored, indent=2) + "\n" == json.dumps(committed, indent=2) + "\n", (
            f"{run_dir.name}: re-scoring the committed graph does not reproduce "
            f"its committed score — the verdict and its evidence disagree"
        )


@pytest.mark.parametrize("layer", axe.LAYERS)
def test_vocabulary_loads_for_every_layer(layer):
    """The declared vocabularies are imported, not re-spelled, so they cannot drift."""
    vocab = axe._vocab()[layer]
    assert vocab, f"no declared {layer} vocabulary — UNDECLARED detection would be blind"


@pytest.mark.parametrize("kind", ["core:organization", "core:team", "core:naturalPerson"])
def test_the_subject_vocabulary_is_the_products_own(kind):
    """Pinned against `subject_binding`, not against the six literals this tool once held."""
    from tortoise.subject_binding import is_subject_kind

    assert axe.kind_status("Subject", kind) == "CORE"
    assert is_subject_kind(kind)

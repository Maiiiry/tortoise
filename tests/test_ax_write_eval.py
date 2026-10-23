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


def _node(layer, text, kind="", status="", is_operator=False, op_type=""):
    return {
        "layer": layer,
        "labels": [layer],
        "text": text,
        "kind": kind,
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
    """
    gold = _gold(
        {
            "id": "x",
            "expect": [{"item": "northwind arr 8.4m", "layer": "object", "kind": ["other"]}],
        }
    )
    doc = _record(
        nodes=[
            # containment match on the expected layer
            _node("Object", "Northwind ARR 8.4m (2026-08)", kind="other"),
            # a WORSE same-layer mention: one token of three
            _node("Object", "ARR reporting policy", kind="other"),
            # the different-layer node that mentions every token
            _node("Point", "Northwind ARR is 8.4m as of 2026-08", kind="statement"),
        ]
    )
    result = axe.score(doc, gold, "x")
    assert not [f for f in result["findings"] if "LAYER confusion" in f["finding"]]
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
        mitigations=[{"relation": "mitigated_by"}],
    )
    result = axe.score(doc, gold, "x")
    p0 = [f for f in result["findings"] if f["severity"] == "P0"]
    assert any("NO NAND" in f["finding"] for f in p0)


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
    next stale number a red test instead of a claim.
    """
    totals = {"P0": 0, "P1": 0}
    with_p0 = 0
    for path in sorted((GOLD.parent / "runs").glob("*/score.json")):
        record = json.loads(path.read_text())
        totals["P0"] += record["severity_counts"]["P0"]
        totals["P1"] += record["severity_counts"]["P1"]
        with_p0 += 1 if record["severity_counts"]["P0"] else 0
    assert totals == {"P0": 13, "P1": 33}, f"report claims 13/33, records say {totals}"
    assert with_p0 == 6, f"report claims 6 of 10 inputs carry a P0, records say {with_p0}"
    doc = (REPO_ROOT / "docs" / "ax" / "how-to-use-tortoise-step1-eval.md").read_text()
    assert "13 P0 and 33 P1" in doc, "the evidence doc's headline drifted from the records"
    assert "6 of 10 inputs" in doc


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

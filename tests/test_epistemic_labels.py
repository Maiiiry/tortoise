"""#7853 — the epistemic label vocabulary is defined ONCE.

The defect this pins is not a wrong label; it is that the SAME concept was written out ad hoc as
``(x:Point OR x:Event)`` in twelve places across seven files, so the codebase carried **two notions of
one concept** and a widening applied to one copy silently left the others behind.

So the load-bearing test here is :func:`test_no_ad_hoc_epistemic_label_pair_survives_in_the_source`,
which is a REPO PIN: it fails the moment someone re-introduces an inline pair instead of asking
``tortoise.epistemic_labels``. That is the regression test for the actual defect — the unit tests below
only pin the helper's shape.
"""
from __future__ import annotations

import re
from pathlib import Path

from tortoise.epistemic_labels import (
    EPISTEMIC_LABELS,
    epistemic_anchored_union,
    epistemic_clause,
)

ROOT = Path(__file__).resolve().parent.parent


def test_the_vocabulary_is_the_ruled_four_and_is_ordered():
    # #7813's ruling. ORDERED, not a set: the order is the emitted predicate's order, so the
    # generated Cypher is stable and diffable rather than dependent on set iteration.
    assert EPISTEMIC_LABELS == ("Point", "Object", "Event", "Subject")


def test_clause_admits_every_label_and_is_parenthesised():
    clause = epistemic_clause("c")
    assert clause == "(c:Point OR c:Object OR c:Event OR c:Subject)"
    # Parenthesised, so it is safe to join with AND/OR without re-associating.
    assert clause.startswith("(") and clause.endswith(")")
    for label in EPISTEMIC_LABELS:
        assert f"c:{label}" in clause


def test_clause_rejects_an_empty_variable():
    import pytest
    with pytest.raises(ValueError):
        epistemic_clause("")


def test_anchored_union_is_one_label_anchored_leg_per_label():
    q = epistemic_anchored_union("n", "n.id IN $ids", "n.id")
    legs = q.split(" UNION ALL ")
    assert len(legs) == len(EPISTEMIC_LABELS) == 4
    # EVERY leg is label-anchored — that anchor is the whole point (it is what the disjunctive
    # form loses, and why the disjunctive form scans the graph).
    for leg, label in zip(legs, EPISTEMIC_LABELS):
        assert leg.startswith(f"MATCH (n:{label}) "), leg
        assert "WHERE n.id IN $ids RETURN n.id" in leg, leg
    # ... and no leg is a bare disjunction
    assert " OR " not in q


def test_anchored_union_rejects_empty_parts():
    import pytest
    for args in (("", "n.id = $id", "n.id"), ("n", "", "n.id"), ("n", "n.id = $id", "")):
        with pytest.raises(ValueError):
            epistemic_anchored_union(*args)


def test_endpoint_clause_is_the_NARROWER_writable_set_and_is_not_the_read_clause():
    """The second concept, pinned as DIFFERENT rather than as a duplicate.

    A read may admit all four labels; a WRITE may only connect an operator to the labels the write
    path actually validates. Collapsing these two into one clause is how a read-widening silently
    changes which nodes a MERGE resolves — the hazard this module exists to make visible.
    """
    from tortoise.epistemic_labels import EPISTEMIC_ENDPOINT_LABELS, endpoint_clause
    assert EPISTEMIC_ENDPOINT_LABELS == ("Point", "Event")
    assert set(EPISTEMIC_ENDPOINT_LABELS) < set(EPISTEMIC_LABELS), "must be a strict subset"
    assert endpoint_clause("s") == "(s:Point OR s:Event)"
    # Same concept name, different scope — and the difference is exactly the owner question.
    assert endpoint_clause("s") != epistemic_clause("s")


def test_no_ad_hoc_epistemic_label_pair_survives_in_the_source():
    """The REPO PIN: the concept is single-sourced.

    Reddens on the pre-#7853 tree, where this pair existed in sdk.py, projection/edges.py,
    commit_ops.py and hosted_api.py. A reader who adds a fifth label to
    ``epistemic_labels.EPISTEMIC_LABELS`` must not have to hunt for copies that were left behind —
    and that is exactly what this forbids.
    """
    pair = re.compile(r"\(\s*[A-Za-z_]\w*\s*:\s*Point\s+OR\s+[A-Za-z_]\w*\s*:\s*Event\s*\)")
    offenders = []
    for path in sorted((ROOT / "tortoise").rglob("*.py")):
        if path.name == "epistemic_labels.py":
            continue           # the module itself documents the historical form
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue       # prose about the form (search_engine.py's measurement note) is not code
            if pair.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{i}: {line.strip()[:100]}")
    assert not offenders, (
        "ad-hoc epistemic-label pair outside epistemic_labels.py — ask the module instead:\n  "
        + "\n  ".join(offenders))

"""#7853 — the epistemic label concept is defined ONCE, and a gate agrees with the write it gates.

Two defects are pinned here.

1. The concept was written out ad hoc as ``(x:Point OR x:Event)`` in twelve places across seven
   files — two notions of one concept, so a widening applied to one copy silently left the others
   behind. :func:`test_no_ad_hoc_epistemic_label_pair_survives_in_the_source` is the repo pin.

2. **The one review round 1 caught, and the reason this file has a second subject:** the
   ``create_operator`` "do the inputs exist?" GATE was widened to all four labels while the
   ``MERGE`` it gates still admitted two. A ``:Subject``-only input then passed validation and the
   write matched nothing — an operator with no ``IMPL``/``NAND`` and no ``INPUT`` edges plus an
   ``OperatorAdded`` event, where the old code raised ``ValueError``. A gate must admit exactly what
   the write it gates can connect.
   :func:`test_create_operator_gate_admits_exactly_what_its_write_can_connect` is that pin.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tortoise.epistemic_labels import (
    EPISTEMIC_ENDPOINT_LABELS,
    EPISTEMIC_LABELS,
    endpoint_clause,
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
    assert clause.startswith("(") and clause.endswith(")")     # join-safe with AND/OR
    for label in EPISTEMIC_LABELS:
        assert f"c:{label}" in clause


def test_endpoint_clause_is_the_NARROWER_writable_set_and_is_not_the_read_clause():
    """The second concept, pinned as DIFFERENT rather than as a duplicate.

    A read may admit all four labels; a WRITE may only connect an operator to what the write path
    validates. Collapsing the two is how a read-widening silently changes which nodes a MERGE
    resolves — exactly defect 2 above.
    """
    assert EPISTEMIC_ENDPOINT_LABELS == ("Point", "Event")
    assert set(EPISTEMIC_ENDPOINT_LABELS) < set(EPISTEMIC_LABELS), "must be a strict subset"
    assert endpoint_clause("s") == "(s:Point OR s:Event)"
    assert endpoint_clause("s") != epistemic_clause("s")


@pytest.mark.parametrize("fn", [epistemic_clause, endpoint_clause])
def test_clauses_reject_an_empty_variable(fn):
    with pytest.raises(ValueError):
        fn("")


def _function_body(path: Path, name: str) -> str:
    """The source of ``def name`` (method or function) up to the next sibling ``def``/``class``."""
    src = path.read_text(encoding="utf-8")
    m = re.search(rf"^(\s*)def {re.escape(name)}\(", src, re.M)
    assert m, f"no def {name} in {path}"
    indent = m.group(1)
    rest = src[m.start():]
    nxt = re.search(rf"\n{indent}(?:def |class )", rest[1:])
    return rest[: nxt.start() + 1] if nxt else rest


def test_create_operator_gate_admits_exactly_what_its_write_can_connect():
    """The round-1 P1, pinned.

    Inside ``create_operator`` the input-existence gate and the edge-creating write must use the SAME
    clause helper. If they ever disagree, a node can pass the gate and then never be connected — the
    silent phantom-operator regression.
    """
    body = _function_body(ROOT / "tortoise" / "sdk.py", "create_operator")
    assert "endpoint_clause(" in body, "the gate/write must ask epistemic_labels for its clause"
    assert "epistemic_clause(" not in body, (
        "create_operator uses the READ clause on a gate or write — a node admitted here but not "
        "connectable by the MERGE becomes a phantom operator. Use endpoint_clause.")
    # ... and both sides of the pair are present, so this cannot pass by the write having moved out.
    assert body.count("endpoint_clause(") >= 2, (
        "expected BOTH the input-existence gate and the edge-creating write to name the endpoint "
        "set; found only %d use(s)" % body.count("endpoint_clause("))


def test_no_ad_hoc_epistemic_label_pair_survives_in_the_source():
    """The REPO PIN: the concept is single-sourced.

    Reddens on the pre-#7853 tree (sdk.py, projection/edges.py, commit_ops.py, hosted_api.py) and on
    any reintroduction. ``battery/`` is scanned too — review round 1 found a surviving executable pair
    at ``battery/runner/setup.py:436``, so the first version of this pin scanned too little.
    """
    pair = re.compile(r"\(\s*[A-Za-z_]\w*\s*:\s*Point\s+OR\s+[A-Za-z_]\w*\s*:\s*Event\s*\)")
    offenders = []
    for root in ("tortoise", "battery"):
        for path in sorted((ROOT / root).rglob("*.py")):
            if path.name == "epistemic_labels.py":
                continue           # the module itself documents the historical form
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue       # prose about the form (search_engine's measurement note) is not code
                if pair.search(line):
                    offenders.append(f"{path.relative_to(ROOT)}:{i}: {line.strip()[:100]}")
    assert not offenders, (
        "ad-hoc epistemic-label pair outside epistemic_labels.py — ask the module instead:\n  "
        + "\n  ".join(offenders))


def test_the_unused_union_helper_is_gone_rather_than_shipped_dead():
    """Round 1 removed it: every by-id site on this path is a gate or a write, so it had no correct
    caller and would have been dead code with a live trap beside it."""
    import tortoise.epistemic_labels as m
    assert not hasattr(m, "epistemic_anchored_union")

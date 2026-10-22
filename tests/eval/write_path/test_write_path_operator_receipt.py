"""#9077 (E4) — the operator receipt must be EVIDENCE, not a numerator.

Two claims are pinned here, and they are deliberately separate:

1. **The receipt carries what a reader cannot re-derive.** Which extractor ran
   (so a monkeypatched WIRE run can never masquerade as a product result —
   ``test_write_path_operator_lane``'s own docstring says its seam grades the
   wire and never the extractor's behaviour), the CURRENT 15-edge denominator
   (not the discarded 4), and per edge: the verdict, a named cause on a miss,
   the pre/post-commit endpoint refs (#4716's two id spaces), and whether the
   committed nodes are in the eventId-keyed retrieval layer (the half the
   historical 0/4 failed structurally).
2. **``tools/operator_receipt_check.py`` refuses every way that evidence can
   be forged, dropped, or restated.** It is the gate the epic's closing rule
   needs; a checker that only counts ``edge_correct`` would re-create the
   defect the issue exists to name.

⛔ exit 0 from the checker means "this receipt is valid evidence" — NOT "the
extractor is perfect". A red E4 whose misses are individually named is a valid
outcome (#9077: do not "fix" the extractor to make the number green), and
``test_checker_accepts_a_red_e4_with_every_miss_named`` pins exactly that.

No graph, no LLM, no network: every fixture here is a dict.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.eval.write_path import corpus, grading, runner, schema  # noqa: E402
from tortoise.ids import content_hash  # noqa: E402


def _payload_id(content: str) -> str:
    """The PAYLOAD id a point with ``content`` is emitted under.

    Deliberately NOT ``grading._pre_commit_ref``: asking the function under
    test to compute its own expected value makes the #4716 id-space claim
    unfalsifiable (a ``_pre_commit_ref`` that returns a constant leaves every
    assertion below green). This is the independent oracle — the same
    content-addressed id ``extractor_v2._content_id("pt", ...)`` emits.
    """
    return f"pt_{content_hash(content)[:62]}"

sys.path.insert(0, str(REPO_ROOT / "tools"))
import operator_receipt_check as checker  # noqa: E402

RECEIPTS = REPO_ROOT / "tests/eval/write_path/receipts"
# The receipt that recorded the historical 0/4 — the one #9077 exists to
# supersede. It is COMMITTED, so this is a regression on real evidence.
HISTORICAL_0_OF_4 = RECEIPTS / "w2b-phaseg-llm-2026-09-08-2514corpus.json"
# The E4 receipt this issue produces: 2/15 wired+persisted on the REAL path,
# 13 misses individually named, measured at `6164c683c`. Its `date` field is
# 2026-10-19T01:39Z UTC = 2026-10-18 local, which is why an earlier filename
# carried the 18th; the file is named after the receipt's own date.
E4_RECEIPT = RECEIPTS / "w2b-operator-2026-10-19.json"


# ── fixture builders ────────────────────────────────────────────────────────


def _ref(content_id: str, *, graph_id: str | None = None) -> dict:
    """One endpoint ref; ``graph_id`` None keeps the payload id (no re-key)."""
    post = graph_id or content_id
    return {
        "pre_ref": content_id,
        "post_ref": post,
        "remapped": content_id != post,
        "resolves": True,
    }


def _edge(edge_id: str, *, kind: str = "SUPPORTS", **over) -> dict:
    edge = {
        "id": edge_id,
        "expected_kind": kind,
        "verdict": "edge_correct",
        "edge_correct": True,
        "cause": None,
        "forms_found": ["IMPL"],
        "edge_op_ids": [f"op-{edge_id}"],
        "endpoints": {
            "from": [_ref(f"pt_from_{edge_id}")],
            "to": [_ref(f"pt_to_{edge_id}", graph_id=f"pre-existing-{edge_id}")],
        },
        "persisted": True,
        "from_session": "wp01_quarry_debug",
        "to_session": "wp01_quarry_debug",
        "owner_session": "wp01_quarry_debug",
    }
    edge.update(over)
    return edge


def _receipt(edges: list[dict] | None = None, **over) -> dict:
    """A minimal evidence-grade receipt with ``denominator`` real edges."""
    if edges is None:
        edges = [_edge(f"wp01_quarry_debug_op_{i:02d}") for i in (1, 2)]
    receipt = {
        "receipt_version": 1,
        "run_id": "w2b-test",
        "date": "2026-01-01T00:00:00Z",
        "run_status": "completed",
        "verdict": "pass",
        "failure_origin": None,
        "commit": "9037c7a7b",
        "corpus_hash": "sha256:test",
        "judge_pin": "w2-write-path-mechanical-v2",
        "resolved_config": {"extractor_posture": "llm"},
        "cost_usd": 0.0,
        "metrics": dict.fromkeys(schema.METRIC_VALUES, 0.0),
        "extractor": {
            "module": "tortoise.extractor_v2",
            "qualname": "extract_session_v2",
            "source_file": "/repo/tortoise/extractor_v2.py",
            "real": True,
        },
        "operator_audit": {
            "planted": len(edges),
            "edge_correct": sum(1 for e in edges if e.get("edge_correct")),
            # ``grading.operator_audit`` defines content_ok as "the verdict is
            # not a content miss", so the fixture derives it the same way — a
            # fixture that hard-coded ``len(edges)`` here would disagree with
            # its own rows and (correctly) red E4-NUMERATOR.
            "content_ok": sum(
                1 for e in edges
                if e.get("verdict") not in ("from_content_missing", "to_content_missing")
            ),
            "operators_total": 7,
            "operators_provenanced": 7,
            "results": {e["id"]: e for e in edges},
        },
    }
    receipt.update(over)
    return receipt


def _failures(result: dict) -> set[str]:
    return {r["rule"] for r in result["rules"] if not r["ok"]}


def _check(receipt: dict, **over) -> dict:
    """``check_receipt`` with the GOLD this fixture declares.

    The live gold is 15; most fixtures here carry fewer edges on purpose (they
    pin a NON-denominator rule), so the fixture declares its own id->kind map —
    the strong form of an offline binding. Nothing here claims to be measured
    against the committed corpus, which is exactly what `edge_ids` says; a bare
    `denominator` may NOT contradict the readable live gold (pinned by
    ``test_checker_refuses_a_bare_override_that_contradicts_the_live_gold``).
    """
    audit = receipt.get("operator_audit", {})
    over.setdefault("denominator", audit.get("planted"))
    over.setdefault(
        "edge_ids",
        {eid: (row or {}).get("expected_kind")
         for eid, row in (audit.get("results") or {}).items()},
    )
    return checker.check_receipt(receipt, **over)


# ── the denominator is re-MEASURED, never trusted from prose ────────────────


def test_current_operator_denominator_is_fifteen_not_the_discarded_four():
    """#9077's own warning: this issue's body is provenance-stale in places, so
    the denominator is measured from the committed gold here, not quoted.

    A 4-edge denominator swung 0,1,1,1,2/4 on IDENTICAL code, so it could not
    separate a fix from LLM variance — the lane discarded it. If the gold moves
    again this test is where a reader learns it.
    """
    sessions = corpus.operator_session_ids()
    assert corpus.planted_operator_count(sessions) == 15
    assert checker.current_denominator() == 15
    assert checker.current_denominator() != checker.DISCARDED_DENOMINATOR


# ── the checker refuses the historical receipt ──────────────────────────────


def test_checker_rejects_the_committed_zero_of_four_receipt():
    """The 0/4 receipt is shape-valid and must still be refused as E4
    evidence — it measures the discarded denominator, carries no per-edge
    verdicts, and never names the extractor."""
    receipt = json.loads(HISTORICAL_0_OF_4.read_text(encoding="utf-8"))
    result = _check(receipt)
    assert result["ok"] is False
    failures = _failures(result)
    assert {"E4-DENOM", "E4-EXTRACTOR", "E4-SHAPE"} <= failures


def test_checker_rejects_a_denominator_of_four_even_when_shapes_match():
    """The discarded denominator is refused on its own, with its own reason —
    a receipt can be otherwise perfect and still measure the wrong thing."""
    receipt = _receipt([_edge(f"op_{i}") for i in range(4)])
    result = checker.check_receipt(receipt, denominator=4)
    assert result["ok"] is False
    assert "E4-DENOM" in _failures(result)
    detail = next(r["detail"] for r in result["rules"]
                  if r["rule"] == "E4-DENOM")
    assert "DISCARDED" in detail


def test_checker_rejects_a_receipt_measured_against_a_different_corpus():
    result = checker.check_receipt(
        _receipt([_edge(f"op_{i}") for i in range(5)], ), denominator=15)
    assert "E4-DENOM" in _failures(result)


def test_checker_rejects_a_rate_without_a_denominator():
    receipt = _receipt()
    receipt["operator_audit"].pop("planted")
    assert "E4-DENOM" in _failures(_check(receipt))


# ── the anti-monkeypatch leg ────────────────────────────────────────────────


def test_checker_rejects_a_monkeypatched_extractor():
    """The WIRE lane (``test_write_path_operator_lane``'s seam) must never
    publish a product number. ``extractor_posture`` reads 'llm' either way, so
    the identity is the only field that can tell them apart."""
    receipt = _receipt()
    receipt["extractor"] = {
        "module": "tests.eval.write_path.test_write_path_operator_lane",
        "qualname": "_extractor.<locals>._run",
        "source_file": None,
        "real": False,
    }
    result = _check(receipt)
    assert "E4-EXTRACTOR" in _failures(result)
    assert "WIRE" in next(r["detail"] for r in result["rules"]
                          if r["rule"] == "E4-EXTRACTOR")


def test_checker_rejects_a_receipt_that_does_not_name_its_extractor():
    receipt = _receipt()
    receipt.pop("extractor")
    assert "E4-EXTRACTOR" in _failures(_check(receipt))


def test_runner_records_the_real_extractor_identity():
    identity = runner.extractor_identity()
    assert identity["real"] is True, identity
    assert identity["module"] == "tortoise.extractor_v2"
    assert identity["source_file"].endswith("tortoise/extractor_v2.py")


def test_runner_identity_flags_a_monkeypatched_seam(monkeypatch):
    """The real seam the WIRE lane installs, measured through the same
    function the runner records — not a hand-written dict."""
    import tortoise.extractor_v2 as ev2

    def _seam(_model, _conversation=None, **_kw):  # pragma: no cover
        raise AssertionError("never called")

    monkeypatch.setattr(ev2, "extract_session_v2", _seam)
    identity = runner.extractor_identity()
    assert identity["real"] is False
    assert identity["module"] != "tortoise.extractor_v2"


# ── the product lane is the only lane E4 can be measured on ─────────────────


def test_checker_rejects_the_m2_echo_lane():
    receipt = _receipt()
    receipt["resolved_config"] = {"extractor_posture": "m2"}
    result = _check(receipt)
    assert "E4-LANE" in _failures(result)


def test_checker_requires_the_commit_sha():
    receipt = _receipt()
    receipt["commit"] = ""
    assert "E4-COMMIT" in _failures(_check(receipt))


# ── misses must be NAMED, and a named miss is a VALID outcome ───────────────


def test_checker_rejects_an_unexplained_miss():
    receipt = _receipt([
        _edge("op_01"),
        _edge("op_02", verdict="edge_missing", edge_correct=False,
              persisted=False, cause=None),
    ])
    result = _check(receipt)
    assert "E4-CAUSE" in _failures(result)
    assert "unexplained" in next(r["detail"] for r in result["rules"]
                                 if r["rule"] == "E4-CAUSE")


def test_checker_accepts_a_red_e4_with_every_miss_named():
    """A red E4 with causes is the outcome the epic NEEDS when the real path
    misses edges — the checker validates the evidence, never the score."""
    receipt = _receipt([
        _edge("op_01"),
        _edge("op_02", verdict="edge_missing", edge_correct=False,
              persisted=False,
              cause="edge_missing: both endpoint claims are present but no "
                    "qualifying SUPPORTS edge connects them"),
        _edge("op_03", verdict="to_content_missing", edge_correct=False,
              persisted=False, endpoints={"from": [_ref("pt_a")], "to": []},
              cause="to_content_missing: no memory Point carries the "
                    "to-anchor claim"),
    ])
    result = _check(receipt)
    assert result["ok"] is True, _failures(result)
    assert result["summary"]["wired"] == 1
    assert result["summary"]["named_misses"] == 2


# ── wiring without persistence is the historical 0/4 half ───────────────────


def test_checker_rejects_a_wired_edge_that_did_not_persist():
    receipt = _receipt([_edge("op_01", persisted=False)])
    result = _check(receipt)
    assert "E4-PERSIST" in _failures(result)


def test_checker_rejects_unprovenanced_operator_nodes():
    receipt = _receipt()
    receipt["operator_audit"]["operators_provenanced"] = 6
    assert "E4-PERSIST" in _failures(_check(receipt))


def test_checker_rejects_a_capture_that_committed_no_operator_nodes():
    receipt = _receipt()
    receipt["operator_audit"]["operators_total"] = 0
    receipt["operator_audit"]["operators_provenanced"] = 0
    assert "E4-PERSIST" in _failures(_check(receipt))


# ── the refs are the #4716 proof, not a prose claim ─────────────────────────


def test_checker_rejects_a_wired_edge_without_endpoint_refs():
    receipt = _receipt([_edge("op_01", endpoints={"from": [], "to": []})])
    assert "E4-REFS" in _failures(_check(receipt))


def test_checker_rejects_a_remap_flag_that_contradicts_the_refs():
    """`remapped` is derived: it must equal (pre_ref != post_ref). A flag that
    disagrees with its own refs is a receipt asserting the remap instead of
    showing it."""
    bad = _ref("pt_same", graph_id="pt_same")
    bad["remapped"] = True
    receipt = _receipt([_edge("op_01", endpoints={"from": [bad], "to": [bad]})])
    result = _check(receipt)
    assert "E4-REFS" in _failures(result)


def test_checker_rejects_an_unresolved_post_ref():
    bad = _ref("pt_x")
    bad["resolves"] = False
    receipt = _receipt([_edge("op_01", endpoints={"from": [bad], "to": [bad]})])
    assert "E4-REFS" in _failures(_check(receipt))


def test_checker_accepts_a_rekeyed_endpoint():
    """The #4716 case the receipt exists to show: the payload id resolved to a
    pre-existing node under a DIFFERENT id, and the edge survived anyway."""
    receipt = _receipt([_edge("op_01")])
    ref = receipt["operator_audit"]["results"]["op_01"]["endpoints"]["to"][0]
    assert ref["remapped"] is True
    assert ref["pre_ref"] != ref["post_ref"]
    result = _check(receipt)
    assert result["ok"] is True, _failures(result)


# ── coverage: every edge in the denominator has a verdict ───────────────────


def test_checker_rejects_a_receipt_missing_edges_from_its_denominator():
    """A denominator of 15 with only 3 per-edge verdicts is a receipt that
    reports 12 edges it never graded."""
    receipt = _receipt([_edge(f"op_{i}") for i in range(3)])
    receipt["operator_audit"]["planted"] = 15
    failures = _failures(checker.check_receipt(receipt, denominator=15))
    assert "E4-COVERAGE" in failures
    assert "E4-DENOM" not in failures


# ── the grader emits the evidence the checker reads ─────────────────────────


def _point(pid: str, content: str) -> dict:
    return {"point_id": pid, "content": content, "event_id": "ev-1"}


def _gold(kind: str, from_anchor: str, to_anchor: str, **over) -> dict:
    planted = {
        "id": f"wp01_quarry_debug_op_{kind.lower()}",
        "expected_kind": kind,
        "from": {"verbatim_anchor": from_anchor},
        "to": {"verbatim_anchor": to_anchor},
    }
    planted.update(over)
    return {"wp01_quarry_debug": {"planted_operators": [planted]}}


FOOT = "the lease makes re-entry impossible while held"
CLAIM = "the race cannot re-enter once the lease is held"


def _audit_snapshot(*, operator_event_id: str | None = "ev-1") -> tuple:
    """A snapshot pair in which an IMPL edge wires the two anchors."""
    from_ref = grading._pre_commit_ref(FOOT)
    to_ref = grading._pre_commit_ref(CLAIM)
    points = [_point(from_ref, FOOT), _point(to_ref, CLAIM)]
    snapshot = {
        "points": points,
        "operator_edges": [{
            "op_id": "op-graph-1",
            "op_type": "IMPL",
            "direction": "unidirectional",
            "endpoints": {from_ref: 0, to_ref: 1},
            "source_id": from_ref,
        }],
        "operator_nodes": [{"point_id": "op-graph-1",
                            "event_id": operator_event_id}],
        "direct_edges": [],
        "mitigations": [],
    }
    return points, snapshot


def test_grading_emits_per_edge_refs_persistence_and_no_cause():
    points, snapshot = _audit_snapshot()
    audit = grading.grade_planted_operators(
        _gold("SUPPORTS", FOOT, CLAIM),
        {"wp01_quarry_debug": points},
        {"wp01_quarry_debug": snapshot},
    )
    detail = audit["results"]["wp01_quarry_debug_op_supports"]
    assert detail["edge_correct"] is True
    assert detail["persisted"] is True
    assert detail["cause"] is None
    assert detail["edge_op_ids"] == ["op-graph-1"]
    for side, content in (("from", FOOT), ("to", CLAIM)):
        ref = detail["endpoints"][side][0]
        # The committed node kept its content-addressed payload id here, so the
        # #4716 re-key did NOT fire — `remapped` must say so, honestly. The
        # expectation is the INDEPENDENT oracle, not the function under test.
        assert ref["pre_ref"] == _payload_id(content)
        assert ref["post_ref"] == _payload_id(content)
        assert ref["remapped"] is False
        assert ref["resolves"] is True


def test_grading_marks_an_unstamped_operator_not_persisted():
    """The #2552 signature: the edge grade can be green while the committed
    operator never entered the eventId-keyed layer. #9077 requires BOTH."""
    points, snapshot = _audit_snapshot(operator_event_id=None)
    audit = grading.grade_planted_operators(
        _gold("SUPPORTS", FOOT, CLAIM),
        {"wp01_quarry_debug": points},
        {"wp01_quarry_debug": snapshot},
    )
    detail = audit["results"]["wp01_quarry_debug_op_supports"]
    assert detail["edge_correct"] is True
    assert detail["persisted"] is False


def test_grading_names_the_cause_of_a_miss():
    """Both endpoint claims present, no edge committed → a NAMED cause (the
    unexplained-miss rule), and the endpoints still carry their refs so a
    reader can see the miss is the EDGE, not the content."""
    points, snapshot = _audit_snapshot()
    snapshot["operator_edges"] = []
    audit = grading.grade_planted_operators(
        _gold("NEGATE", FOOT, CLAIM),
        {"wp01_quarry_debug": points},
        {"wp01_quarry_debug": snapshot},
    )
    detail = audit["results"]["wp01_quarry_debug_op_negate"]
    assert detail["edge_correct"] is False
    assert detail["verdict"] == "edge_missing"
    assert detail["persisted"] is False
    assert "NEGATE" in detail["cause"]
    assert detail["endpoints"]["from"][0]["post_ref"]


def test_grading_names_a_missing_endpoint_claim():
    points, snapshot = _audit_snapshot()
    audit = grading.grade_planted_operators(
        _gold("SUPPORTS", FOOT, "a claim nobody wrote down"),
        {"wp01_quarry_debug": points},
        {"wp01_quarry_debug": snapshot},
    )
    detail = audit["results"]["wp01_quarry_debug_op_supports"]
    assert detail["verdict"] == "to_content_missing"
    assert detail["cause"].startswith("to_content_missing")
    assert detail["endpoints"]["to"] == []


# ── the runner receipt carries it end to end ───────────────────────────────


def test_build_receipt_carries_the_per_edge_evidence_and_extractor():
    """The report → receipt bridge: a receipt that kept only the numerator is
    the restatement #9077 exists to stop."""
    points, snapshot = _audit_snapshot()
    audit = grading.grade_planted_operators(
        _gold("SUPPORTS", FOOT, CLAIM),
        {"wp01_quarry_debug": points},
        {"wp01_quarry_debug": snapshot},
    )
    audit["operators_total"] = 1
    audit["operators_provenanced"] = 1
    report = {
        "run_id": "w2b-test",
        "date": "2026-01-01T00:00:00Z",
        "run_status": "completed",
        "verdict": "pass",
        "failure_origin": None,
        "commit": "9037c7a7b",
        "corpus_hash": "sha256:test",
        "judge_pin": "w2-write-path-mechanical-v2",
        "resolved_config": {"extractor_posture": "llm"},
        "cost_usd": 0.0,
        "metrics": dict.fromkeys(schema.METRIC_VALUES, 0.0),
        "quote_spans_total": 0,
        "operator_audit": audit,
        "extractor_identity": {
            "module": "tortoise.extractor_v2",
            "qualname": "extract_session_v2",
            "source_file": "/repo/tortoise/extractor_v2.py",
            "real": True,
        },
        "session_results": [],
        "notes": [],
        "log": [],
    }
    receipt = runner.build_receipt(report)
    assert runner.validate_receipt(receipt) == []
    assert receipt["extractor"]["real"] is True
    edge = receipt["operator_audit"]["results"]["wp01_quarry_debug_op_supports"]
    assert edge["persisted"] is True
    assert edge["cause"] is None
    assert edge["endpoints"]["from"][0]["pre_ref"]
    assert edge["endpoints"]["to"][0]["post_ref"]
    # ...and the checker accepts the receipt the runner actually produces.
    # The fixture plants ONE synthetic edge, so the gold it grades against is
    # declared explicitly (a bare `--denominator 1` would contradict the
    # readable 15-edge gold — pinned by the override test above).
    result = checker.check_receipt(
        receipt, denominator=1,
        edge_ids={"wp01_quarry_debug_op_supports": "SUPPORTS"})
    assert result["ok"] is True, _failures(result)


def test_receipt_without_an_extractor_block_stays_shape_valid():
    """Back-compat: every committed receipt predates #9077. Shape validation
    must not red them — only the E4 CHECKER refuses them."""
    receipt = _receipt()
    receipt.pop("extractor")
    assert runner.validate_receipt(receipt) == []
    assert "E4-EXTRACTOR" in _failures(_check(receipt))


def test_checker_cli_exit_codes(tmp_path):
    """The CLI path takes NO override, so the committed gold binds: a real
    receipt exits 0, a tampered one exits 1, a missing file exits 2."""
    assert checker.main([str(E4_RECEIPT)]) == 0

    bad = tmp_path / "bad.json"
    payload = json.loads(E4_RECEIPT.read_text(encoding="utf-8"))
    payload["extractor"]["real"] = False
    bad.write_text(json.dumps(payload), encoding="utf-8")
    assert checker.main([str(bad)]) == 1

    assert checker.main([str(tmp_path / "missing.json")]) == 2


# ── the checker cannot be handed a quieter gold (#9077 review findings) ─────


def test_checker_refuses_a_bare_override_that_contradicts_the_live_gold():
    """A count override is not a way to restate the denominator: with the
    committed gold readable, 15 is the answer, so `--denominator 999` (or 2)
    is refused instead of silently binding the check to itself."""
    receipt = _receipt([_edge(f"op_{i:02d}") for i in range(2)])
    for stated in (999, 2):
        failures = _failures(checker.check_receipt(receipt, denominator=stated))
        assert "E4-DENOM" in failures


def test_checker_refuses_a_receipt_grading_other_edges():
    """Fifteen verdicts for the WRONG edges is 15 verdicts, not an E4 result:
    the count matches, the edge set does not. Without this binding the checker
    could not tell "the real 15" from "any 15"."""
    invented = [_edge(f"wp01_quarry_debug_op_{i:02d}") for i in range(1, 16)]
    receipt = _receipt(invented)
    failures = _failures(checker.check_receipt(receipt))
    assert "E4-COVERAGE-IDS" in failures
    assert "E4-COVERAGE" not in failures  # the COUNT was right — that is the point
    # ...and the SAME must hold for an agreeing count override: `--denominator
    # 15` restates a number the gold already fixes, so it may not switch the
    # edge-set binding off (#9077 round-2 finding).
    agreeing = _failures(checker.check_receipt(receipt, denominator=15))
    assert "E4-COVERAGE-IDS" in agreeing


def test_checker_refuses_a_relabelled_edge_kind():
    """Same ids, same count, wrong operator kind: the receipt grades an edge
    nobody planted, so the number means something else."""
    planted_kinds = checker.current_edge_map()
    assert planted_kinds is not None
    edges = [
        _edge(eid, kind=("NEGATE" if kind != "NEGATE" else "SUPPORTS"))
        for eid, kind in planted_kinds.items()
    ]
    failures = _failures(checker.check_receipt(_receipt(edges)))
    assert "E4-KIND" in failures
    assert "E4-COVERAGE-IDS" not in failures  # the id SET is right


def test_checker_binds_a_declared_edge_set_not_the_committed_gold():
    """The strong override is the caller DECLARING the gold it grades against
    (fixtures, offline reads) — visible in the call, never a silenced check."""
    receipt = _receipt([_edge("op_a", kind="SUPPORTS")])
    result = checker.check_receipt(
        receipt, denominator=1, edge_ids={"op_a": "SUPPORTS"})
    assert result["ok"] is True, _failures(result)
    # The declared gold also supplies the DENOMINATOR — on its own, with no
    # count passed, it must not be measured against the committed 15 (#9077
    # round-3 finding).
    declared_only = checker.check_receipt(
        receipt, edge_ids={"op_a": "SUPPORTS"})
    assert declared_only["ok"] is True, _failures(declared_only)
    # ...and a declared edge set that contradicts its own denominator is bad.
    failures = _failures(checker.check_receipt(
        receipt, denominator=5, edge_ids={"op_a": "SUPPORTS"}))
    assert "E4-DENOM" in failures


@pytest.mark.parametrize("field", ["edge_op_ids", "verdict"])
def test_checker_rejects_an_edge_without_its_verdict(field):
    receipt = _receipt()
    edge = receipt["operator_audit"]["results"]["wp01_quarry_debug_op_01"]
    if field == "verdict":
        edge["verdict"] = ""
        assert "E4-SHAPE" in _failures(_check(receipt))
    else:
        edge[field] = []
        # An empty op-id list is legal ONLY for a SUPERSEDE (it rides the
        # point-level CORRECTS). Pinned here because a checker that silently
        # accepted it for an operator-kind edge would pass a receipt whose
        # edge was graded but never found — which is how `persisted` becomes
        # vacuous (`all(oid in provenanced for oid in [] or [])` is True).
        assert "E4-FORMING" in _failures(_check(receipt))
        # ...and the exemption is real, not a gap: the same receipt with the
        # point-level kind is accepted on this axis.
        supersede = _receipt([_edge("op_a", kind="SUPERSEDE")])
        supersede["operator_audit"]["results"]["op_a"][field] = []
        assert "E4-FORMING" not in _failures(_check(supersede))

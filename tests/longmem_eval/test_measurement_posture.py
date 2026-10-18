"""#8486 — the run's MEASUREMENT POSTURE (ingest mode + belief activation).

Hermetic: pure functions, no DB, no network. The load-bearing contract is
that a LongMemEval number can never be published with an IMPLIED posture —
the belief layer is EP-INERT on a v2 graph unless ``activate_beliefs`` ran
(#2598: every claim reads the Beta(1,1) ``0.50`` of a prior that was never
measured), so "was it activated" must be an explicit, recorded, non-unknown
fact wherever the number appears.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.longmem_eval import measurement_posture as mp  # noqa: E402


def _manifest(**over) -> dict:
    m = {"points_promoted": 10, "operators_promoted": 4, "ep_ran": True,
         "ep_engine": "dream:full", "points_with_ep": 10,
         "points_unmeasured": 0, "duration_ms": 12}
    m.update(over)
    return m


# ── summarize_belief_activation ───────────────────────────────────────────

def test_summary_sums_the_manifests_and_counts_ep_runs():
    outcomes = [{"belief_activation": _manifest()},
                {"belief_activation": _manifest(points_promoted=5,
                                                operators_promoted=2)}]
    summary = mp.summarize_belief_activation(outcomes, requested=True)
    assert summary["requested"] is True
    assert summary["questions"] == 2
    assert summary["ep_ran"] == 2
    assert summary["failed"] == 0
    assert summary["points_promoted"] == 15
    assert summary["operators_promoted"] == 6
    assert summary["engine"] == "dream:full"
    assert summary["errors"] == []


def test_summary_counts_a_fail_open_manifest_as_failed():
    """The runner records ``{"ep_ran": False, "error": ...}`` when activation
    raises — that question's belief layer is inert, so the run must not read
    as activated."""
    outcomes = [{"belief_activation": _manifest()},
                {"belief_activation": {"ep_ran": False,
                                       "error": "RuntimeError: boom"}}]
    summary = mp.summarize_belief_activation(outcomes, requested=True)
    assert summary["questions"] == 2
    assert summary["ep_ran"] == 1
    assert summary["failed"] == 1
    assert "boom" in summary["errors"][0]
    assert mp.build(ingest_mode="v2", activation=summary)["belief_layer"] == \
        mp.BELIEF_LAYER_PARTIAL


def test_summary_ignores_outcomes_without_a_manifest():
    """The arm-OFF path leaves the outcome shape untouched."""
    summary = mp.summarize_belief_activation(
        [{"question_id": "q1"}, {"belief_activation": "junk"}],
        requested=False)
    assert summary["requested"] is False
    assert summary["questions"] == 0
    assert summary["ep_ran"] == 0


# ── belief_layer_state ────────────────────────────────────────────────────

def test_inert_when_the_arm_was_not_requested():
    summary = mp.summarize_belief_activation([], requested=False)
    assert mp.belief_layer_state(requested=False, activation=summary) == \
        mp.BELIEF_LAYER_INERT


def test_armed_but_unproven_is_partial_never_activated():
    """A request is not consent: an armed run whose questions produced no
    manifest did not measure the belief layer."""
    empty = mp.summarize_belief_activation([], requested=True)
    assert mp.belief_layer_state(requested=True, activation=empty) == \
        mp.BELIEF_LAYER_PARTIAL
    assert mp.build(ingest_mode="v2", activation=empty)["belief_layer"] == \
        mp.BELIEF_LAYER_PARTIAL


def test_activated_requires_every_question_to_have_run_ep():
    ok = mp.summarize_belief_activation(
        [{"belief_activation": _manifest()}], requested=True)
    assert mp.belief_layer_state(requested=True, activation=ok) == \
        mp.BELIEF_LAYER_ACTIVATED
    # an EP pass that failed (ep_ran False, no exception) is NOT activated
    half = mp.summarize_belief_activation(
        [{"belief_activation": _manifest()},
         {"belief_activation": _manifest(ep_ran=False)}], requested=True)
    assert mp.belief_layer_state(requested=True, activation=half) == \
        mp.BELIEF_LAYER_PARTIAL


# ── build ────────────────────────────────────────────────────────────────

def test_build_records_both_halves_of_the_disclosure():
    inert = mp.build(ingest_mode="deterministic")
    assert inert["ingest_mode"] == "deterministic"
    # no decision reached this run → UNKNOWN, never an assumed "off"
    assert inert["beliefs_activated"] is None
    assert inert["belief_layer"] == mp.BELIEF_LAYER_UNKNOWN

    off = mp.build(ingest_mode="v2", activation=mp.summarize_belief_activation(
        [], requested=False))
    assert off["beliefs_activated"] is False
    assert off["belief_layer"] == mp.BELIEF_LAYER_INERT

    on = mp.build(ingest_mode="v2", activation=mp.summarize_belief_activation(
        [{"belief_activation": _manifest()}], requested=True))
    assert on["beliefs_activated"] is True
    assert on["belief_layer"] == mp.BELIEF_LAYER_ACTIVATED
    assert on["activation"]["points_promoted"] == 10


def test_of_report_reads_only_a_real_posture_block():
    assert mp.of_report(None) is None
    assert mp.of_report({}) is None
    assert mp.of_report({"methodology": {}}) is None
    assert mp.of_report({"methodology": {"measurement_posture": "junk"}}) is None
    posture = mp.build(ingest_mode="v2")
    assert mp.of_report(
        {"methodology": {"measurement_posture": posture}}) == posture


# ── whole-chain: report methodology → published receipt gate ─────────────

def test_completed_receipt_refuses_every_undisclosed_posture():
    """The gate: a completed number publishes ONLY with an explicit posture.

    RED mutations: drop ``measurement_posture`` → refused; ``beliefs_activated
    = None`` (a pre-#8486 producer) → refused; ``belief_layer = 'unknown'`` →
    refused; a posture whose ingest mode disagrees with the receipt's →
    refused.
    """
    from tools.longmem_eval.w7_publish import build_receipt, validate_receipt

    def _receipt(**over):
        kwargs = dict(
            run_id="r", date="d", commit="c", corpus_hash="sha256:x",
            judge_pin="pin", judge_model="m", judge_rubric_id_hash="h",
            reader_model_spec="s", reader_pinned=True, ingest_mode="v2",
            run_status="completed", verdict="pass", failure_origin=None,
            cost_usd=1.0, notes=[], resolved_config={},
            metrics={
                "official_recall_all@5": 0.5,
                "official_recall_all@5_by_category": {
                    "rows": {c: {"n": 0} for c in (
                        "full_mix", "multi-session", "knowledge-update",
                        "single-session-preference")}},
            },
            measurement_posture=mp.build(
                ingest_mode="v2",
                activation=mp.summarize_belief_activation(
                    [{"belief_activation": _manifest()}], requested=True)),
        )
        kwargs.update(over)
        return build_receipt(**kwargs)

    assert validate_receipt(_receipt()) == []

    missing = _receipt()
    del missing["measurement_posture"]
    assert any("measurement_posture" in i for i in validate_receipt(missing))

    unrecorded = _receipt(measurement_posture=mp.build(ingest_mode="v2"))
    issues = validate_receipt(unrecorded)
    assert any("beliefs_activated" in i for i in issues)
    assert any("belief_layer" in i for i in issues)

    mismatch = _receipt(measurement_posture=mp.build(
        ingest_mode="deterministic"))
    assert any("disagrees" in i for i in validate_receipt(mismatch))

    # a FAILED run is not asked for the disclosure (nothing is published)
    assert validate_receipt(_receipt(run_status="failed",
                                     measurement_posture=None,
                                     metrics={"x": 1})) == []

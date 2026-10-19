"""The decision rule, tested without a run.

`evaluate_branches` is the only place the pre-registered §7 rule is applied, so
these tests pin the two ways it can be wrong that a diff would not show: an
inequality decided by floating-point representation, and a branch certified on
an extract the extractor itself marked incomplete.

No database, no fixtures, no model — pure arithmetic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.eval.write_path.score_qualifiers import (
    BAND_PP,
    HOLDOUT_SESSIONS,
    PR_TOLERANCE_PP,
    TUNING_SESSIONS,
    check_protocol_pins,
    evaluate_branches,
    verdict_label,
)

CLEAN: list[str] = []


def test_exactly_on_the_band_does_not_fire_branch_1():
    """+10.0 pp is the branch-2 ceiling, NOT one step past the branch-1 trigger.

    The registration says branch 1 fires when Δ > +10 pp and branch 2 applies
    when |Δ| <= 10 pp. Computed in floats, the committed holdout is
    `100 * (7/10 - 6/10)` = 9.999999999999998 — so a raw `> 10.0` test and a raw
    `<= 10.0` test can disagree about the SAME cell depending on summation order.
    """
    verdict = evaluate_branches(100 * (7 / 10 - 6 / 10), 0.8, 3.0, contaminated=CLEAN)
    assert verdict["branch_1_fires"] is False
    assert verdict["branch_2_applies"] is True
    assert verdict["branch_2_certified"] is True


def test_both_boundaries_are_inclusive_and_symmetric():
    assert evaluate_branches(BAND_PP, 0.0, 0.0, contaminated=CLEAN)["branch_2_applies"]
    assert evaluate_branches(-BAND_PP, 0.0, 0.0, contaminated=CLEAN)["branch_2_applies"]
    assert evaluate_branches(PR_TOLERANCE_PP, 0.0, 0.0, contaminated=CLEAN)["branch_2_applies"]


def test_representation_noise_above_the_band_does_not_fire_branch_1():
    """10.000000000000002 pp is the band, not past it — the guard must be tested.

    `round(v, 9)` is what makes the boundary reproducible; without it this cell
    fires branch 1 (mutation-verified: dropping the rounding reddens this test).
    """
    verdict = evaluate_branches(10.000000000000002, 0.0, 0.0, contaminated=CLEAN)
    assert verdict["branch_1_fires"] is False
    assert verdict["branch_2_applies"] is True


def test_one_ulp_past_the_band_still_fires_branch_1():
    verdict = evaluate_branches(BAND_PP + 1e-6, 0.0, 0.0, contaminated=CLEAN)
    assert verdict["branch_1_fires"] is True
    assert verdict["branch_2_applies"] is False
    assert verdict["branch_2_certified"] is False


@pytest.mark.parametrize("prec,rec", [(-PR_TOLERANCE_PP - 0.1, 0.0),
                                      (0.0, -PR_TOLERANCE_PP - 0.1), (-99.0, 99.0)])
def test_a_precision_or_recall_drop_fires_branch_1_even_at_parity_loss(prec, rec):
    verdict = evaluate_branches(0.0, prec, rec, contaminated=CLEAN)
    assert verdict["branch_1_fires"] is True
    assert verdict["branch_2_applies"] is False


@pytest.mark.parametrize("prec,rec", [(-PR_TOLERANCE_PP, 0.0), (0.0, -PR_TOLERANCE_PP)])
def test_a_drop_exactly_at_the_tolerance_is_inside_the_band(prec, rec):
    """"> 5 pp" is strict: a −5.0 pp drop is the ceiling of acceptable, not past it."""
    verdict = evaluate_branches(0.0, prec, rec, contaminated=CLEAN)
    assert verdict["branch_1_fires"] is False
    assert verdict["branch_2_applies"] is True


def test_the_committed_run_is_provisional_because_its_receipt_is_incomplete():
    """The real cell: met the band, but `wp07` raw is `partial` (3 of 6 raw losses).

    Certifying here would record "D3 settles" off an extract whose tail the
    extractor dropped — and one qualifier in that cell crosses the band.
    """
    verdict = evaluate_branches(10.0, 0.8, 3.0,
                               contaminated=["wp07_bluepeak_followup:raw"])
    assert verdict["branch_1_fires"] is False
    assert verdict["branch_2_applies"] is True
    assert verdict["branch_2_certified"] is False, "an incomplete receipt must not certify"
    assert verdict["branch_3_stays_provisional"] is True


def test_contamination_does_not_rescue_the_falsifier_either():
    """A partial receipt cannot be used to *argue against* the arm either."""
    verdict = evaluate_branches(20.0, 0.0, 0.0, contaminated=["wp07_bluepeak_followup:raw"])
    assert verdict["branch_1_fires"] is True
    assert verdict["branch_2_applies"] is False


def test_missing_deltas_never_certify():
    assert evaluate_branches(None, None, None, contaminated=CLEAN)["branch_2_applies"] is False
    assert evaluate_branches(None, None, None, contaminated=CLEAN)["branch_3_stays_provisional"]


# ── the recorded verdict string: ONE route per case ──────────────────────────────

def test_verdict_label_distinguishes_uncertifiable_parity_from_branch_3():
    """The two land on the same OUTCOME (provisional) by different ROUTES.

    A single `branch_3` flag cannot tell them apart, and collapsing them records a
    route the flags contradict — run 1 reaches branch 3 (branch 2 never applied),
    run 2 meets branch 2 and cannot certify it. The artifact a reader audits must
    say which.
    """
    run_2 = evaluate_branches(10.0, 0.8, 3.0, contaminated=["wp07_bluepeak_followup:raw"])
    assert run_2["branch_2_applies"] is True
    assert "branch 3 not reached" in verdict_label(run_2)

    run_1 = evaluate_branches(-60.0, -0.8, 24.2, contaminated=CLEAN)
    assert run_1["branch_2_applies"] is False
    assert "branch 3" in verdict_label(run_1)
    assert "not reached" not in verdict_label(run_1), "branch 3 WAS reached here"


def test_verdict_label_names_the_fire_and_the_settlement():
    assert verdict_label(evaluate_branches(20.0, 0.0, 0.0, contaminated=CLEAN)).startswith(
        "the falsifier FIRES")
    assert verdict_label(evaluate_branches(2.0, 0.0, 0.0, contaminated=CLEAN)).startswith(
        "settled (branch 2 certified)")


def test_the_committed_run2_routing_is_recorded_on_the_artifact() -> None:
    """End-to-end: the committed score file's verdict must be the run-2 route."""
    out = Path(__file__).parent / "results" / "7947-score-replication-2026-10-18.json"
    verdict = json.loads(out.read_text())["decision_input"]
    assert verdict["branch_2_applies"] is True
    assert verdict["branch_2_certified"] is False
    assert verdict["contaminated_receipts"] == ["wp07_bluepeak_followup:raw"]
    assert verdict["verdict"] == (
        "provisional (branch 2 met, not certifiable; branch 3 not reached)")


def test_a_suppressed_fire_is_recorded_as_suppressed_not_as_branch_3():
    """The suppression must not be laundered into "branch 3 applies".

    Branch 1's condition was MET; it was withheld because the arm that met it is
    incomplete. Recording that as branch 3 would assert a route the rule did not
    take — the same defect the route table exists to prevent.
    """
    verdict = evaluate_branches(20.0, 0.0, 0.0, contaminated=["wp06_quarry_rollout:narrative"])
    label = verdict_label(verdict)
    assert "SUPPRESSED" in label
    assert "branch 3" not in label


# ── a broken NARRATIVE arm must not be able to manufacture the falsifier ──────

def test_a_partial_narrative_receipt_cannot_fire_branch_1():
    """Truncation removes claims, so an incomplete narrative arm OVERSTATES Δ.

    Firing branch 1 off it would publish "the narrative loses qualifiers" on the
    strength of an extract the extractor itself calls incomplete — the mirror image
    of the raw-arm case, where truncation UNDERSTATES Δ and a fire is therefore
    conservative.
    """
    verdict = evaluate_branches(20.0, 0.0, 0.0,
                               contaminated=["wp06_quarry_rollout:narrative"])
    assert verdict["branch_1_fires"] is False
    assert verdict["falsifier_suppressed_by_contamination"] is True
    assert verdict["branch_3_stays_provisional"] is True
    # ...and the same delta on a CLEAN run does fire.
    assert evaluate_branches(20.0, 0.0, 0.0, contaminated=CLEAN)["branch_1_fires"] is True


# ── the protocol-pins guard ───────────────────────────────────────────────

SESSIONS = (*TUNING_SESSIONS, *HOLDOUT_SESSIONS)


def _write_receipts(tmp_path, **overrides):
    base = {"model_id": "deepseek/deepseek-v4-flash", "chunk_size": 50,
            "session_date": "2026-10-10", "env_pins": {}, "embed_list": {}, "s2_stats": {}}
    for sid in SESSIONS:
        rec = {**base, **{k: (v[sid] if isinstance(v, dict) else v)
                          for k, v in overrides.items()}}
        (tmp_path / f"{sid}.arms.json").write_text(json.dumps(rec))
    return tmp_path


def test_consistent_receipts_pass_the_protocol_guard(tmp_path):
    assert check_protocol_pins(_write_receipts(tmp_path), SESSIONS) == []


def test_a_different_model_in_one_receipt_is_refused(tmp_path):
    d = _write_receipts(tmp_path, model_id={s: "deepseek/deepseek-v4-flash" for s in SESSIONS}
                        | {"wp05_retro_writeup": "other/model"})
    errors = check_protocol_pins(d, SESSIONS)
    assert errors and "model_id" in errors[0]


def test_a_different_environment_pin_is_refused(tmp_path):
    """The decisive pins: `TORTOISE_LABEL_ORDER=shuffle` reseeds the label order FROM
    THE STORY, so it differs between arms at identical prompt length — exactly the
    difference §10's token arithmetic is blind to. A guard that ignores it would
    score a shuffle run as if it were this one."""
    d = _write_receipts(tmp_path, env_pins={s: {} for s in SESSIONS}
                        | {"wp03_ember_design": {"TORTOISE_LABEL_ORDER": "shuffle"}})
    errors = check_protocol_pins(d, SESSIONS)
    assert errors and "ENVIRONMENT" in errors[0]
    assert "TORTOISE_LABEL_ORDER" in errors[0]


def test_unset_environment_pins_are_consistent_with_each_other(tmp_path):
    """The 7 committed receipts carry NO `env_pins` — absence is not a disagreement."""
    assert check_protocol_pins(_write_receipts(tmp_path, env_pins={s: {} for s in SESSIONS}),
                               SESSIONS) == []

"""#9076 — the E1+E2 one-run measurement: its corpus, its census, and its honesty gates.

**Why a test file for a probe.** The probe
(`docs/evidence/7949-capstone/probes/e1_e2_probe.py`) is the only artifact that
puts E1 and E2 through the shipped pipeline on one corpus in one process, and the
log it writes is a CLAIM about a product promise. Nothing in the tree would fail
if that claim quietly lost its denominator, scored itself against a threshold
chosen after the run, or reported a counterfactual arm as the product's result.
So the pins here are:

1. **The corpus is reproducible from its two named sources** — a silent edit to
   ``e1e2_corpus.json`` fails here rather than moving a number in a log.
2. **The census is arithmetic, not narrative** — numerator/denominator/share are
   checked against each other on a synthetic payload, including the empty case.
3. **The measured numbers are PINNED.** The E1 finding this lane delivers is that
   the shipped arm moves the share by exactly 0.0 — the discards it does make are
   all downgraded by ``apply_vet``'s Layer-1 referential guard. Pinning 0.0 is what
   makes a future movement visible: if the arbiter seam ever gets a production
   caller, this file forces the measurement to be re-taken on purpose (the same
   discipline ``tests/test_salience_labels_4917.py`` applies to 9/15).
4. **The structural checker fails on every dishonest shape** — one test per
   mutation, so the gate cannot pass by omission.
5. **No criterion is PASS without a frozen threshold**, the SCORED arm is
   never a counterfactual, and a threshold the run MEETS cannot green it while a
   control is red (the probe's `green = …` conjunction is otherwise unpinned).
6. **Collecting this file must not mutate the interpreter.** The probe is imported
   at collection time, so a process-global `logging.disable` in it would silence
   every `caplog` assertion in the files that run after this one (#9110 review P1).

DB-free: the pipeline runs with a scripted model, ``sdk=None``, no network.
"""
from __future__ import annotations

import importlib.util as _ilu
import json
import logging
import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PROBE_PATH = (_REPO_ROOT / "docs" / "evidence" / "7949-capstone" / "probes"
               / "e1_e2_probe.py")
_CORPUS_DIR = _REPO_ROOT / "docs" / "evidence" / "7949-capstone" / "corpus"
_CORPUS = _CORPUS_DIR / "e1e2_corpus.json"
_LOG = _REPO_ROOT / "docs" / "evidence" / "7949-capstone" / "e1-e2.log"

sys.path.insert(0, str(_REPO_ROOT))
from tools import e1_e2_census_check as check  # noqa: E402


def _load(path: Path, name: str):
    """Load a hyphenated-directory module by path — the probes' established pattern."""
    spec = _ilu.spec_from_file_location(name, str(path))
    assert spec and spec.loader
    module = _ilu.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = _load(_PROBE_PATH, "e1_e2_probe")
builder = _load(_CORPUS_DIR / "build_e1e2_corpus.py", "build_e1e2_corpus")


# ── 1. the corpus ─────────────────────────────────────────────────────────


def test_committed_corpus_matches_a_fresh_build():
    """A hand-edited corpus would move the denominator without moving any rule."""
    committed = _CORPUS.read_text(encoding="utf-8")
    fresh = json.dumps(builder.build(), indent=1, sort_keys=True,
                       ensure_ascii=False) + "\n"
    assert committed == fresh, (
        "e1e2_corpus.json no longer matches build_e1e2_corpus.py — rebuild it "
        "with `python docs/evidence/7949-capstone/corpus/build_e1e2_corpus.py` "
        "and say why the population changed"
    )


def test_corpus_populations_are_the_two_named_sources():
    corpus = probe.load_corpus(_CORPUS)
    assert len(corpus["objects"]) == 25, (
        "the Object population is the 25 owner-confirmed audit names")
    audit = [p for p in corpus["points"] if p["provenance"].startswith("prod-graph-audit")]
    planted = [p for p in corpus["points"] if p.get("must_keep")]
    assert len(audit) == 25, "each audited Object keeps the statement it was minted from"
    assert len(planted) == 90, (
        "the anti-gaming control is the versioned corpus's 90 planted units")
    assert all(p["source"].startswith("tests/eval/write_path/") for p in corpus["points"])
    # the four names a LATER owner ruling (#1026) makes must-keep are flagged, so
    # no arm can read the audit set as a drop list (#4917's own warning).
    flagged = {o["name"] for o in corpus["objects"] if o["keep_object_ruling"]}
    assert flagged == {"guard 2", "_TOOL_BY_NAME", "daniel-ospina/agent-infra",
                       "_capture_cost_props"}, flagged


def test_no_arm_discards_a_keep_object_name():
    """#1026 rules four audited names MUST-KEEP; dropping one would reverse it."""
    corpus = probe.load_corpus(_CORPUS)
    embed = probe.build_embed_list(corpus)
    _minted, meta = probe.mint_skip_transform(embed)
    keep = {o["name"] for o in corpus["objects"] if o["keep_object_ruling"]}
    assert not (set(meta["dropped_objects"]) & keep), (
        "the counterfactual mint-time arm dropped a #1026 keep-object")


# ── 2. the census is arithmetic ───────────────────────────────────────────


def test_find_values_finds_the_literals_the_carve_out_exists_for():
    text = ("it hit 4.2 seconds yesterday, the p95 was 62 percent, a ten-minute "
            "TTL, and forty rows per page on the October 5 release")
    found = {lit for _cls, lit in probe.find_values(text)}
    assert "4.2 seconds" in found
    assert "62 percent" in found
    assert "ten-minute" in found
    assert "forty rows" in found
    assert "October 5" in found


def test_find_values_returns_nothing_for_prose_without_values():
    assert probe.find_values("the plan is fine and the review is pending") == []


def test_claim_units_splits_sentences_and_semicolons():
    assert len(probe.claim_units("the cache is warm")) == 1
    assert len(probe.claim_units("the cache is warm. the queue is flat")) == 2
    assert len(probe.claim_units("first do this; then do that")) == 2


def test_census_reports_the_share_as_both_counts():
    corpus = {"points": []}
    payload = {"entities": [{"name": "the thing"}, {"name": "the other"},
                            {"name": "Maya Chen"}],
               "points": [{"content": "it hit 4.2 seconds"}]}
    cen = probe.census({"payload": payload, "vet": {}}, corpus)
    assert (cen["objects_the_prefix"], cen["objects_total"]) == (2, 3)
    assert cen["share"] == pytest.approx(2 / 3)
    assert cen["state_value_count"] >= 1


def test_census_never_divides_by_zero_on_an_empty_graph():
    cen = probe.census({"payload": {"entities": [], "points": []}, "vet": {}}, {"points": []})
    assert cen["objects_total"] == 0
    assert cen["share"] is None


# ── 3. the measurement, pinned ────────────────────────────────────────────

#: MEASURED at the commit this file landed on. If any of these move, re-take the
#: measurement and update them on purpose — do not let the finding drift silently.
MEASURED_BEFORE_SHARE = 0.60          # 15 of the 25 audited Objects are `the <X>`
MEASURED_SHIPPED_REDUCTION = 0.0      # the shipped arm moves the share by NOTHING
MEASURED_MINT_SKIP_REDUCTION = 0.225  # 9/25 dropped → 6/16 remain `the <X>`
MEASURED_STATE_VALUES = 42            # value literals across 34 Points
MEASURED_REAL_FACTS = 90              # planted must-keep units, both sides


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    """One real probe run, reused by every pin below (≈2.5 s for four arms)."""
    import io
    from contextlib import redirect_stdout

    out = tmp_path_factory.mktemp("e1e2") / "e1-e2.json"
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = probe.main(["--json-out", str(out)])
    assert out.exists(), "--json-out must write the machine-readable census"
    return {"text": buf.getvalue(), "exit": code}


def test_the_run_measures_instead_of_scoring_when_no_prereg_is_frozen(run):
    """Measured numbers are evidence; a verdict needs a frozen threshold (#9075)."""
    assert run["exit"] == 3, run["text"]
    assert "result=UNPREREGISTERED" in run["text"]
    assert "prereg=UNPREREGISTERED" in run["text"]


def test_e1_share_is_the_pinned_number_with_both_counts(run):
    line = next(ln for ln in run["text"].splitlines()
                if ln.startswith("arm=before metric=objects "))
    assert "numerator=15" in line and "denominator=25" in line
    assert f"share={MEASURED_BEFORE_SHARE:.6f}" in line


def test_the_shipped_arm_moves_the_share_by_exactly_zero(run):
    """THE FINDING: the shipped gate cannot move E1, and this is why.

    ``TORTOISE_VET=1`` + ``TORTOISE_VALUE_GATE=1`` fires the identifier-only
    predicate on 3 candidates and ``apply_vet`` Rule 2 downgrades all 3 to KEEP —
    each is referenced by the statement it was minted from, and dropping the
    Object would break Layer-1 referential integrity. So volume is unchanged. The
    cause is structural (the reference), not a threshold.
    """
    before = next(ln for ln in run["text"].splitlines()
                  if ln.startswith("arm=before metric=objects "))
    after = next(ln for ln in run["text"].splitlines()
                 if ln.startswith("arm=after-vet-shipped metric=objects "))
    assert re.search(r"share=(\S+)", before).group(1) == \
        re.search(r"share=(\S+)", after).group(1)
    assert "observed=NO_REDUCTION" in run["text"]
    assert "vet-downgrades count=6" in run["text"]  # 3 per pass (S2 + union)
    assert "kept (Layer-1 referential integrity)" in run["text"]


def test_the_counterfactual_arms_are_reported_and_never_scored(run):
    assert "scored_arm=after-vet-shipped" in run["text"]
    for arm in ("after-vet-arbiter", "counterfactual-mint-skip"):
        line = next(ln for ln in run["text"].splitlines()
                    if ln.startswith(f"counterfactual arm={arm} "))
        assert "note=NOT SCORED" in line
    mint = next(ln for ln in run["text"].splitlines()
                if ln.startswith("counterfactual arm=counterfactual-mint-skip "))
    assert f"reduction={MEASURED_MINT_SKIP_REDUCTION:.6f}" in mint


def test_e2_controls_do_not_move_on_any_arm(run):
    """The anti-gaming property: E1 may not be bought with facts or values."""
    line = next(ln for ln in run["text"].splitlines() if ln.startswith("e1-control "))
    assert f"before_real_facts={MEASURED_REAL_FACTS}" in line
    assert f"after_real_facts={MEASURED_REAL_FACTS}" in line
    state = next(ln for ln in run["text"].splitlines() if ln.startswith("e2-state "))
    assert f"before={MEASURED_STATE_VALUES}" in state
    assert f"after={MEASURED_STATE_VALUES}" in state


def test_atomicity_is_reported_and_split_is_named_as_the_blocker(run):
    assert "split-implemented=no" in run["text"]
    fused = [ln for ln in run["text"].splitlines() if ln.startswith("e2-atomic fused ")]
    assert fused, "the fused Point must be listed, not merely counted"
    assert all("cause=SPLIT-not-implemented" in ln for ln in fused)


def test_coverage_honesty_names_the_classes_with_no_detector(run):
    line = next(ln for ln in run["text"].splitlines() if ln.startswith("coverage rows="))
    assert "positively_classified=9" in line
    assert "in_scope=15" in line
    undetected = re.search(r"undetected_classes=(\S+)", line).group(1).split(",")
    assert "symbol_name" in undetected and "repo_slug" in undetected
    # the value gate DOES reach two classes the dd predicate cannot — reported apart
    assert "class=pr_or_issue_number rows=2 dd_positive=0 value_gate=2" in run["text"]
    assert "class=file_path rows=1 dd_positive=0 value_gate=1" in run["text"]


# ── 4. the structural checker — one test per dishonest shape ──────────────


@pytest.fixture(scope="module")
def log_text(run):
    return run["text"]


def test_the_produced_log_passes_the_structural_check(log_text):
    findings, state = check.check(log_text)
    assert findings == []
    assert state["result"] == "UNPREREGISTERED"


def test_the_committed_log_passes_the_structural_check():
    findings, _state = check.check(_LOG.read_text(encoding="utf-8"))
    assert findings == [], findings


def test_checker_fails_when_a_denominator_is_missing(log_text):
    mutated = log_text.replace("numerator=15 denominator=25", "numerator=15 denominator=")
    findings, _ = check.check(mutated)
    assert any("[V3]" in f for f in findings), findings


def test_checker_fails_when_the_share_disagrees_with_its_counts(log_text):
    mutated = log_text.replace("share=0.600000", "share=0.900000", 1)
    findings, _ = check.check(mutated)
    assert any("is not 15/25" in f for f in findings), findings


def test_checker_fails_when_the_scored_arm_is_a_counterfactual(log_text):
    mutated = log_text.replace("scored_arm=after-vet-shipped",
                               "scored_arm=counterfactual-mint-skip")
    findings, _ = check.check(mutated)
    assert any("is a counterfactual" in f for f in findings), findings


def test_checker_fails_when_the_anti_gaming_control_is_absent(log_text):
    mutated = "\n".join(ln for ln in log_text.splitlines()
                        if not ln.startswith("e1-control "))
    findings, _ = check.check(mutated)
    assert any("[V5]" in f for f in findings), findings


def test_checker_fails_when_a_criterion_passes_without_a_frozen_threshold(log_text):
    mutated = log_text.replace("e1-control before_real_facts=90 after_real_facts=90 "
                               "verdict=UNPREREGISTERED",
                               "e1-control before_real_facts=90 after_real_facts=90 "
                               "verdict=PASS")
    findings, _ = check.check(mutated)
    assert any("no frozen threshold" in f for f in findings), findings


def test_checker_fails_when_coverage_honesty_is_missing(log_text):
    mutated = "\n".join(ln for ln in log_text.splitlines()
                        if not ln.startswith("coverage "))
    findings, _ = check.check(mutated)
    assert any("[V8]" in f for f in findings), findings


def test_checker_fails_when_split_is_not_declared_unimplemented(log_text):
    mutated = log_text.replace("split-implemented=no", "split-implemented=yes")
    findings, _ = check.check(mutated)
    assert any("[V7]" in f for f in findings), findings


def test_checker_fails_when_a_dropped_point_names_no_cause(log_text):
    line = ("arm=after-vet-shipped dropped kind=point content='the thing' "
            "cause= values=['4.2']")
    findings, _ = check.check(log_text + line + "\n")
    assert any("[V6]" in f for f in findings), findings


def test_checker_fails_when_the_e1_reduction_is_borrowed_from_a_counterfactual(
        log_text):
    """The `e1` line is the one the VERDICT is read from — reconcile it (#9110 P1).

    The exact mutation the review measured as passing: keep both shares at 0.6 (as
    every arm line prints) and claim the counterfactual's 22.5 pp as the SHIPPED
    arm's reduction — the number this whole bundle exists to attribute to a
    mechanism that does not exist in production.
    """
    assert "reduction=-0.000000" in log_text
    mutated = log_text.replace("reduction=-0.000000", "reduction=0.225000")
    findings, _ = check.check(mutated)
    assert any("[V3]" in f and "difference" in f for f in findings), findings


def test_checker_fails_when_the_e1_shares_disagree_with_the_arm_lines(log_text):
    mutated = log_text.replace(
        "before_share=0.600000 after_share=0.600000",
        "before_share=0.900000 after_share=0.900000")
    assert mutated != log_text
    findings, _ = check.check(mutated)
    assert any("[V3]" in f and "before_share" in f for f in findings), findings
    assert any("[V3]" in f and "after_share" in f for f in findings), findings


def test_checker_fails_when_the_scored_arm_counts_were_edited_consistently(log_text):
    """A self-consistent edit to the after arm still has to match the e1 line."""
    original = ("arm=after-vet-shipped metric=objects total=25 numerator=15 "
                "denominator=25 share=0.600000")
    assert original in log_text
    mutated = log_text.replace(
        original,
        "arm=after-vet-shipped metric=objects total=20 numerator=1 "
        "denominator=20 share=0.050000")
    findings, _ = check.check(mutated)
    # V3 is satisfied by the arms (1/20 is 0.05) — the e1 line is what objects
    assert any("[V3]" in f and "after_share" in f for f in findings), findings


def test_checker_fails_when_the_observation_contradicts_the_reduction(log_text):
    mutated = log_text.replace("observed=NO_REDUCTION", "observed=REDUCTION_OBSERVED")
    assert mutated != log_text
    findings, _ = check.check(mutated)
    assert any("[V10]" in f and "contradicts" in f for f in findings), findings
    # ... and the reverse direction, so neither side can drift alone
    back = log_text.replace("reduction=-0.000000", "reduction=0.225000")
    back_findings, _ = check.check(back)
    assert any("[V10]" in f and "NO_REDUCTION" in f for f in back_findings), back_findings


def test_checker_requires_green_when_asked(log_text, tmp_path):
    # the log's result is UNPREREGISTERED, so --require-green must exit 1
    path = tmp_path / "e1-e2.log"
    path.write_text(log_text, encoding="utf-8")
    assert check.main([str(path)]) == 0
    assert check.main([str(path), "--require-green"]) == 1


# ── 5. the pre-registration binding ───────────────────────────────────────


def test_scoring_uses_the_frozen_threshold_and_can_be_red(monkeypatch):
    """With a threshold the measured 0.0 reduction cannot meet, the run is RED."""
    monkeypatch.setattr(probe, "load_prereg",
                        lambda sha: {"e1": {"min_share_reduction": 0.05}})
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        code = probe.main(["--prereg", "deadbeef" * 5])
    text = buf.getvalue()
    assert code == 1, text
    assert "result=RED" in text
    assert "e1 before_share=0.600000 after_share=0.600000" in text
    assert "threshold=0.05" in text
    assert "verdict=FAIL" in text


def test_a_zero_threshold_greens_the_arm_but_the_observation_still_says_no(
        monkeypatch):
    """A zero threshold is MET by a 0.0 reduction — and cannot hide it.

    This is the threshold-meets-the-run leg, NOT the control-conjunction leg
    (that one is ``test_a_red_control_keeps_a_scoring_run_red`` below): every
    control holds in the produced log, so what is pinned here is that a bar the
    run meets greens it while the threshold-free ``observed=`` line still reports
    that the share did NOT move.
    """
    monkeypatch.setattr(probe, "load_prereg",
                        lambda sha: {"e1": {"min_share_reduction": 0.0}})
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        code = probe.main(["--prereg", "deadbeef" * 5])
    text = buf.getvalue()
    assert "threshold=0.0" in text
    assert code == 0, text
    assert "result=GREEN" in text
    assert "observed=NO_REDUCTION" in text, (
        "a zero threshold must not hide that the share did not move")


def test_a_red_control_keeps_a_scoring_run_red(monkeypatch):
    """A threshold the run DOES meet cannot go green while a control is red.

    Pins the control conjunction in the probe's
    ``green = scored and e1_ok and control_ok and state_ok and atomic_ok``
    (#9110 review P2). Without this pin the terms could be deleted and every
    other test in this file would still pass — leaving a run that LOST a planted
    must-keep fact printing ``result=GREEN`` and exiting 0, which is exactly what
    ``--require-green`` accepts. The loss is injected at the ``run_arm`` seam: the
    SHIPPED arm's payload drops one planted unit, which is what a gate that bought
    E1 with facts would produce. E1 itself still meets its 0.0 bar, so the only
    red criterion is the control.
    """
    import io
    from contextlib import redirect_stdout

    corpus = probe.load_corpus(_CORPUS)
    keep = {p["content"] for p in corpus["points"] if p.get("must_keep")}
    real_run_arm = probe.run_arm

    def run_arm_dropping_one_planted_fact(embed_list, *, vet, arbiter=None):
        res = real_run_arm(embed_list, vet=vet, arbiter=arbiter)
        shipped = (vet and arbiter is None
                   and len(embed_list["entities"]) == len(corpus["objects"]))
        if not shipped:
            return res
        points = list(res["payload"].get("points") or [])
        idx = next(i for i, p in enumerate(points) if p.get("content") in keep)
        payload = dict(res["payload"])
        payload["points"] = points[:idx] + points[idx + 1:]
        return {**res, "payload": payload}

    monkeypatch.setattr(probe, "run_arm", run_arm_dropping_one_planted_fact)
    monkeypatch.setattr(probe, "load_prereg",
                        lambda sha: {"e1": {"min_share_reduction": 0.0}})
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = probe.main(["--prereg", "deadbeef" * 5])
    text = buf.getvalue()
    control = next(ln for ln in text.splitlines() if ln.startswith("e1-control "))
    e1 = next(ln for ln in text.splitlines() if ln.startswith("e1 "))
    assert "after_real_facts=89" in control, control
    assert "verdict=FAIL" in control, control
    # the E1 bar itself is met — so the RED below is the control's doing, not E1's
    assert "after_share=0.600000" in e1 and "verdict=PASS" in e1, e1
    assert code == 1, text
    assert "result=RED" in text
    assert "result=GREEN" not in text


def test_the_threshold_used_must_be_the_one_the_artifact_froze(monkeypatch):
    """A log that names a pre-registration and scores against its OWN bar fails."""
    monkeypatch.setattr(probe, "load_prereg",
                        lambda sha: {"e1": {"min_share_reduction": 0.05}})
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        probe.main(["--prereg", "deadbeef" * 5])
    text = buf.getvalue()
    # the pair is printed: what the artifact froze, and what the run used
    assert "prereg_e1_threshold=0.05" in text
    assert "threshold=0.05" in text
    findings, _ = check.check(text)
    assert findings == [], findings
    # mutate the USED bar only — the run now scores against a threshold the
    # artifact never stated, and the checker must refuse it. The leading space is
    # load-bearing: without it the FIRST match is `prereg_e1_threshold=0.05`, the
    # FROZEN side, which is the wrong direction to tamper and passes anyway
    # because V9's comparison is symmetric (#9110 review P2).
    tampered = text.replace(" threshold=0.05", " threshold=0.01", 1)
    assert "prereg_e1_threshold=0.05" in tampered, (
        "the FROZEN threshold must be untouched — only the used bar is tampered")
    assert " threshold=0.01" in tampered
    findings, _ = check.check(tampered)
    assert any("the bar moved" in f for f in findings), findings


def test_a_prereg_commit_without_the_artifact_stays_unpreregistered(monkeypatch):
    """`git show` reads the COMMIT's tree — a missing artifact cannot be scored.

    The absence is injected at the ``git show`` seam rather than pinned on a named
    commit (#9110 review P2). A HEAD-based pin — the original shape — self-invalidates
    the moment #9075 lands ``preregistration.json``, which is the very artifact this
    probe says it is waiting for: `load_prereg` would then succeed and this file
    would break in whichever lane lands #9075. A fixed pre-artifact sha has the
    mirror-image flaw — if a squash-merge rewrites it the case silently becomes
    "the sha does not exist", which is not the property under test. The seam keeps
    the property itself: the artifact is ABSENT from the tree `git show` is asked
    for, and the run must MEASURE rather than invent a verdict.
    """
    import io
    import subprocess
    from contextlib import redirect_stdout

    real_git = probe._git

    def git_whose_tree_lacks_the_artifact(*args: str) -> str:
        if args and args[0] == "show":
            raise subprocess.CalledProcessError(
                128, ["git", *args],
                output="",
                stderr=f"fatal: path '{probe.PREREG_ARTIFACT}' does not exist "
                       f"in 'deadbeef'")
        return real_git(*args)

    monkeypatch.setattr(probe, "_git", git_whose_tree_lacks_the_artifact)
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = probe.main(["--prereg", "deadbeef" * 5])
    text = buf.getvalue()
    assert code == 3, text
    assert "prereg-error=" in text
    assert "result=UNPREREGISTERED" in text


# ── 6. the probe must not touch PROCESS-global logging ────────────���───────


def test_importing_the_probe_leaves_process_logging_enabled(caplog):
    """#9110 review P1: `logging.disable` at module scope poisoned every `caplog`.

    This module imports the probe at COLLECTION time, so a module-scope
    `logging.disable(WARNING)` ran before any test in the process. It sets
    ``logging.Manager.disable``, which ``caplog.at_level()`` does NOT reset (that
    touches only the logger and handler levels), so `Logger.isEnabledFor` returns
    False for every level <= 30 and the record is never created — every positive
    `caplog` assertion later in the same pytest process silently saw zero records,
    and every `assert not caplog.records` passed for the wrong reason.
    """
    assert logging.root.manager.disable == logging.NOTSET, (
        "importing the probe disabled logging for the whole pytest process: "
        f"logging.Manager.disable={logging.root.manager.disable}")
    with caplog.at_level(logging.WARNING):
        logging.getLogger("e1e2.pin").warning(
            "this WARNING must survive the probe's import")
    assert "this WARNING must survive the probe's import" in caplog.text, (
        "the probe's import swallowed a WARNING record — every caplog assertion "
        "in this process is now vacuous")


def test_a_run_restores_the_logging_state_it_found():
    """The mute is scoped to the printed log, and the previous state comes back."""
    import io
    from contextlib import redirect_stdout

    previous = logging.root.manager.disable
    buf = io.StringIO()
    with redirect_stdout(buf):
        probe.main([])
    assert buf.getvalue(), "the run must still print its log"
    assert logging.root.manager.disable == previous, (
        "the run left logging disabled after it returned: "
        f"{logging.root.manager.disable} != {previous}")
    assert logging.getLogger("e1e2.pin.after").isEnabledFor(logging.WARNING), (
        "a WARNING emitted after the run is no longer even created")

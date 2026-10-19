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
5. **No criterion is PASS without a frozen threshold**, and the SCORED arm is
   never a counterfactual.

DB-free: the pipeline runs with a scripted model, ``sdk=None``, no network.
"""
from __future__ import annotations

import importlib.util as _ilu
import json
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


def test_a_green_run_requires_every_criterion(log_text, monkeypatch):
    """A threshold the run DOES meet still cannot go green while a control is red."""
    monkeypatch.setattr(probe, "load_prereg",
                        lambda sha: {"e1": {"min_share_reduction": 0.0}})
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        code = probe.main(["--prereg", "deadbeef" * 5])
    text = buf.getvalue()
    assert "threshold=0.0" in text
    # reduction == 0.0 >= 0.0 and every control held, so this arm scores green —
    # and the threshold-free observation line still says the share did NOT move.
    assert code == 0, text
    assert "result=GREEN" in text
    assert "observed=NO_REDUCTION" in text, (
        "a zero threshold must not hide that the share did not move")


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
    # mutate the used bar only — the run now scores against a threshold the
    # artifact never stated, and the checker must refuse it
    tampered = text.replace("threshold=0.05", "threshold=0.01", 1)
    findings, _ = check.check(tampered)
    assert any("the bar moved" in f for f in findings), findings


def test_a_prereg_commit_without_the_artifact_stays_unpreregistered():
    """`git show` reads the COMMIT's tree — a missing artifact cannot be scored."""
    import io
    import subprocess
    from contextlib import redirect_stdout

    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(_REPO_ROOT),
                          capture_output=True, text=True, check=True).stdout.strip()
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = probe.main(["--prereg", head])
    text = buf.getvalue()
    assert code == 3, text
    assert "prereg-error=" in text
    assert "result=UNPREREGISTERED" in text

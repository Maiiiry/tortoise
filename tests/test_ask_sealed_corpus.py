"""#9097 — the sealed ask corpus + pre-registration CONTRACT (hermetic).

The CHEAP half of the sealed-benchmark suite, split out on purpose: a diff that
touches only `tests/eval/ask/**` (a fixture or a gold byte) falls back to the
`core` surface, so a hermetic guard registered there is what catches it. The
pipeline half — the sealed run, the D8 mutation, the verbatim regression — lives
in `tests/test_ask_sealed_benchmark.py` (embedded stores, ~45s).

What this file pins, with no DB, no network and no LLM:

  1. the corpus manifests clean and the hash covers fixtures AND the SEALED gold
     (a gold-only edit must invalidate a published number);
  2. a fixture that grows a grading key is refused, and the gold never
     duplicates the corpus — the two halves cannot drift into each other;
  3. the pre-registration pins corpus hash + split + scoring + decision rule, is
     refused for a corpus missing either class, and states NO knowledge-update
     threshold (epic D8: the first run sets it);
  4. the gate says NO — a KU win with a verbatim loss is a `regression`, and a
     hash/config mismatch is `inconclusive` (never a silent re-bless);
  5. the receipt uses the W7 vocabulary with ASK metric validation, and is
     deliberately NOT routed through `validate_receipt` (LongMemEval-only
     completed-run requirements) — asserted by running both;
  6. the published report is machine-rendered, and says PENDING with no number
     while no run has been executed.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.eval.ask import corpus, generate_corpus  # noqa: E402, RUF100
from tools.longmem_eval import ask_sealed  # noqa: E402, RUF100
from tools.longmem_eval.w7_publish import validate_receipt  # noqa: E402, RUF100


@pytest.fixture(scope="module")
def prereg() -> dict:
    return ask_sealed.build_preregistration()


def _synthetic_report(*, corpus_hash: str | None = None,
                      ku_committed: int = 3,
                      verbatim_raw: int = 3,
                      verbatim_distilled: int = 3,
                      rendered_live: int = 0,
                      seen: int = 3,
                      stub_reader: bool = False,
                      judge_model: str = "openai/gpt-4o-2024-08-06") -> dict:
    """A contract-shaped run report — NOT a measurement.

    Used to exercise the gate/receipt arithmetic on outcomes that are cheap to
    state and expensive to produce: the point is the CONTRACT, never a number.
    """
    def arm(committed: bool, *, live: bool = False, observed: bool = True):
        return {"ok": True, "hypothesis": "…", "committed": committed,
                "old_seen": observed, "rendered_live": live,
                "flagged": not live, "note": ""}

    cases = []
    for index, case_id in enumerate(("diet", "employer", "residence")):
        cases.append({
            "case_id": case_id, "case_class": corpus.CLASS_KNOWLEDGE_UPDATE,
            "arms": {"raw": arm(False, observed=False),
                     "distilled": arm(index < ku_committed,
                                      live=index < rendered_live,
                                      observed=index < seen)},
        })
    for index, case_id in enumerate(("diet-verbatim", "employer-verbatim",
                                     "residence-verbatim")):
        cases.append({
            "case_id": case_id, "case_class": corpus.CLASS_VERBATIM,
            "arms": {"raw": arm(index < verbatim_raw),
                     "distilled": arm(index < verbatim_distilled)},
        })
    return {
        "schema": ask_sealed.RUN_SCHEMA,
        "benchmark": ask_sealed.BENCHMARK,
        "issue": ask_sealed.ISSUE,
        "corpus_hash": corpus_hash or corpus.compute_fixtures_hash(),
        "reader_model": ask_sealed.DEFAULT_READER_MODEL,
        "stub_reader": stub_reader,
        "judge_model": judge_model,
        "judge_pin": ask_sealed.JUDGE_PIN,
        "judge_rubric_id_hash": ask_sealed._sha16(ask_sealed.JUDGE_RUBRIC_ID),
        "graded": True,
        "cases": cases,
    }


# ── 1. the corpus, and the sealing split ──────────────────────────────────

def test_committed_corpus_verifies_and_covers_fixtures_and_gold():
    manifest = corpus.verify_manifest()
    assert manifest["ok"], manifest
    assert manifest["fixtures_hash"] == corpus.compute_fixtures_hash()
    # Scope = fixtures AND gold: a gold-only edit has to invalidate a published
    # number, exactly as a fixture edit does.
    files = corpus.load_manifest()["files"]
    assert any(rel.startswith("fixtures/") for rel in files)
    assert any(rel.startswith("gold/") for rel in files)
    assert len(files) == 2 * len(corpus.case_ids())


def test_a_fixture_carrying_the_grading_key_is_refused():
    """The runner's input must not contain the sealed key. This is the guard
    that keeps `load_fixtures` handout-safe."""
    leaking = dict(corpus.load_fixture("residence"))
    leaking["answer"] = "I live in Porto."
    issues = corpus.fixture_issues(leaking)
    assert any("SEALED" in issue and "answer" in issue for issue in issues), issues
    # The verbatim control's key is the OLD words: the two classes must not be
    # answerable from the same field.
    assert corpus.load_gold("residence")["answer"] != corpus.load_gold(
        "residence-verbatim")["answer"]


def test_the_sealed_gold_does_not_duplicate_the_corpus():
    for case_id, key in corpus.load_gold_all().items():
        assert corpus.gold_issues(key) == [], case_id
        for field in corpus.CORPUS_ONLY_FIELDS:
            assert field not in key, (case_id, field)


def test_corpus_carries_both_classes_of_the_exit_bar():
    fixtures = corpus.load_fixtures()
    by_class = {name: [f["case_id"] for f in fixtures
                       if f["case_class"] == name]
                for name in corpus.CASE_CLASSES}
    assert by_class[corpus.CLASS_KNOWLEDGE_UPDATE] == [
        "diet", "employer", "residence"]
    assert by_class[corpus.CLASS_VERBATIM] == [
        "diet-verbatim", "employer-verbatim", "residence-verbatim"]
    for fixture in fixtures:
        assert corpus.fixture_issues(fixture) == [], fixture["case_id"]
    # Every authored pair contributes BOTH a knowledge-update case and its
    # verbatim control — the exit bar is conjunctive, so a pair may not
    # contribute only one.
    assert {pair["case_id"] for pair in generate_corpus.PAIRS} == {
        case_id for case_id in corpus.case_ids()
        if not case_id.endswith("-verbatim")}


def test_a_gold_only_edit_changes_the_hash_and_breaks_the_manifest(tmp_path):
    root = tmp_path / "ask"
    shutil.copytree(corpus.ASK_DIR, root)
    before = corpus.compute_fixtures_hash(root)
    gold = corpus.gold_path("residence", root)
    payload = json.loads(gold.read_text(encoding="utf-8"))
    payload["answer"] = "I live somewhere else."
    gold.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    assert corpus.compute_fixtures_hash(root) != before
    # The committed manifest no longer describes the tree: `extra`/`mismatched`
    # surfaces the edit — a gold-only edit is never silent.
    after = corpus.verify_manifest(root)
    assert after["ok"] is False
    assert after["mismatched"] == ["gold/ask-residence.gold.json"], after


# ── 2. the pre-registration ───────────────────────────────────────────────

def test_preregistration_pins_corpus_split_scoring_and_decision_rule(prereg):
    assert prereg["schema"] == ask_sealed.PREREGISTRATION_SCHEMA
    assert prereg["written_before_any_arm_run"] is True
    assert prereg["corpus"]["fixtures_hash"] == corpus.compute_fixtures_hash()
    assert prereg["corpus"]["n_cases"] == len(corpus.case_ids())
    assert set(prereg["split"]) == set(corpus.CASE_CLASSES)
    assert prereg["scoring"] == ask_sealed.scoring()
    assert prereg["decision_rule"] == ask_sealed.decision_rule()
    assert prereg["falsification"], (
        "a bar with no falsification probe is a claim, not a measurement")
    # The KU number is pre-registered as THRESHOLD-FREE (epic D8).
    ku_rule = prereg["decision_rule"]["d7_current_state"]
    assert ku_rule["rule"].startswith("RECORD")
    assert "NO pass threshold" in ku_rule["rule"]
    assert "threshold" not in prereg["decision_rule"]["d7_current_state"]
    # ... and the two real bars are stated in advance.
    assert prereg["decision_rule"]["d8_superseded_live"]["pass_iff"]
    assert prereg["decision_rule"]["verbatim_no_regression"]["rule"]


def test_a_corpus_missing_a_class_cannot_be_pre_registered(tmp_path):
    """The exit bar is conjunctive, so a half corpus can never be registered."""
    root = tmp_path / "ask"
    (root / "fixtures").mkdir(parents=True)
    (root / "gold").mkdir(parents=True)
    for case_id in corpus.case_ids():
        if case_id.endswith("-verbatim"):
            continue
        shutil.copy(corpus.fixture_path(case_id), root / "fixtures")
        shutil.copy(corpus.gold_path(case_id), root / "gold")
    corpus.write_json(root / "_manifest.json", {
        "corpus": "ask", "schema_version": 1, "case_ids": [],
        "files": {corpus._relative(p, root): corpus._sha256_file(p)
                  for p in corpus.corpus_file_paths(root)},
        "fixtures_hash": corpus.compute_fixtures_hash(root),
    })
    with pytest.raises(SystemExit, match="carry no cases"):
        ask_sealed.build_preregistration(corpus_root=root)


# ── 3. the gate can say NO ────────────────────────────────────────────────

def test_a_passing_contract_run_passes(prereg):
    evaluation = ask_sealed.evaluate(_synthetic_report(), prereg=prereg)
    assert evaluation["verdict"] == "pass", evaluation
    assert evaluation["run_status"] == "completed"
    # The KU number is a METRIC, never a gate — and carries no threshold.
    assert "d7_current_state" not in evaluation["gates"]
    assert evaluation["metrics"]["current_state_distilled"]["rate"] == 1.0
    assert "threshold" not in json.dumps(evaluation["metrics"])


def test_a_ku_win_never_pays_for_a_verbatim_loss(prereg):
    """THE exit bar: a perfect knowledge-update number cannot rescue a verbatim
    regression — "a win on KU with a loss on verbatim is a FAIL, not a trade"."""
    evaluation = ask_sealed.evaluate(
        _synthetic_report(ku_committed=3, verbatim_raw=3,
                          verbatim_distilled=1),
        prereg=prereg)
    assert evaluation["metrics"]["current_state_distilled"]["rate"] == 1.0
    verbatim = evaluation["gates"]["verbatim_no_regression"]
    assert verbatim["regressions"] == ["employer-verbatim",
                                       "residence-verbatim"], verbatim
    assert evaluation["verdict"] == "regression"
    assert evaluation["failure_origin"] == "gate_regression"


def test_a_superseded_claim_rendered_live_fails_the_d8_gate(prereg):
    evaluation = ask_sealed.evaluate(_synthetic_report(rendered_live=1),
                                     prereg=prereg)
    assert evaluation["gates"]["d8_superseded_live"]["ok"] is False
    assert evaluation["verdict"] == "regression"


def test_an_unobserved_claim_is_never_a_pass(prereg):
    """Fail closed: a claim the instrument could not see was not shown to be
    flagged. `seen < n` fails the gate even with zero rendered live."""
    evaluation = ask_sealed.evaluate(_synthetic_report(seen=2), prereg=prereg)
    d8 = evaluation["gates"]["d8_superseded_live"]
    assert d8["seen"] == 2 and d8["rendered_live"] == 0
    assert d8["ok"] is False
    assert evaluation["verdict"] == "regression"


def test_a_hash_mismatch_is_inconclusive_not_a_re_bless(prereg):
    evaluation = ask_sealed.evaluate(
        _synthetic_report(corpus_hash="sha256:" + "0" * 64), prereg=prereg)
    assert evaluation["verdict"] == "inconclusive"
    assert evaluation["failure_origin"] == "hash_mismatch"
    assert any("the RUN's recorded corpus hash" in issue
               for issue in evaluation["issues"])


def test_a_retuned_decision_rule_is_a_config_mismatch(prereg):
    tampered = json.loads(json.dumps(prereg))
    tampered["decision_rule"]["verbatim_no_regression"]["rule"] = (
        "aggregate only, a KU win may pay for one verbatim loss")
    evaluation = ask_sealed.evaluate(_synthetic_report(), prereg=tampered)
    assert evaluation["verdict"] == "inconclusive"
    assert evaluation["failure_origin"] == "config_mismatch"
    assert any("decision rule differs" in issue for issue in evaluation["issues"])


def test_a_stub_reader_or_mock_judge_run_is_not_publishable(prereg):
    stub = ask_sealed.evaluate(
        _synthetic_report(stub_reader=True, judge_model="mock-judge"),
        prereg=prereg)
    assert stub["publishable"] is False
    assert stub["run_status"] == "skipped"
    assert any("NOT publishable" in issue for issue in stub["issues"])


# ── 4. the receipt: W7 vocabulary, ask-lane metric validation ────────────

def test_receipt_uses_w7_vocabulary_with_ask_metric_validation(prereg):
    run = _synthetic_report()
    evaluation = ask_sealed.evaluate(run, prereg=prereg)
    receipt = ask_sealed.build_receipt(
        run, evaluation, prereg=prereg, commit="deadbeef", date="2026-10-19",
        cost_usd=0.0, prereg_commit="cafebabe")
    assert ask_sealed.validate_ask_receipt(receipt) == [], \
        ask_sealed.validate_ask_receipt(receipt)
    assert receipt["verdict"] == "pass"
    assert receipt["corpus_hash"].startswith("sha256:")
    for key in ask_sealed.ASK_METRIC_KEYS:
        assert key in receipt["metrics"], key
    assert receipt["preregistration"]["commit"] == "cafebabe"
    # The decision recorded on #9097: the W7 VALIDATOR is not reusable as-is —
    # it demands LongMemEval metric names an ask run does not have. If this ever
    # stops being true, the ask lane has a second, untested validation path.
    assert validate_receipt(receipt), (
        "validate_receipt was expected to reject an ask receipt (its "
        "completed-run requirements are LongMemEval-only)")


def test_a_completed_ask_receipt_missing_its_metrics_is_rejected(prereg):
    run = _synthetic_report()
    evaluation = ask_sealed.evaluate(run, prereg=prereg)
    receipt = ask_sealed.build_receipt(run, evaluation, prereg=prereg,
                                       commit="deadbeef", date="2026-10-19")
    receipt["metrics"] = {"something_else": 1}
    issues = ask_sealed.validate_ask_receipt(receipt)
    assert any("missing" in issue for issue in issues), issues


# ── 5. the published report is machine-derived ───────────────────────────

def test_the_committed_report_is_pending_and_claims_no_number(prereg):
    rendered = ask_sealed.render_report(prereg=prereg, date="2026-10-19")
    assert "RUN STATUS (this commit): PENDING" in rendered
    assert "No metric value is claimed here" in rendered
    # The reproduction command and the corpus identity are on the page.
    assert prereg["corpus"]["fixtures_hash"] in rendered
    assert "tools.longmem_eval.ask_sealed run" in rendered
    # No metric table cell claims a value while the run is pending.
    for row in rendered.splitlines():
        if row.startswith("| `current_state`") or row.startswith(
                "| `superseded_live`"):
            assert ask_sealed.PENDING in row, row


def test_a_completed_report_is_rendered_from_the_receipt(prereg):
    run = _synthetic_report(corpus_hash=prereg["corpus"]["fixtures_hash"])
    evaluation = ask_sealed.evaluate(run, prereg=prereg)
    receipt = ask_sealed.build_receipt(run, evaluation, prereg=prereg,
                                       commit="deadbeef", date="2026-10-19",
                                       prereg_commit="cafebabe")
    rendered = ask_sealed.render_report(prereg=prereg, run=run,
                                        evaluation=evaluation, receipt=receipt,
                                        date="2026-10-19")
    assert "RUN STATUS (this commit): EXECUTED" in rendered
    assert "3/3" in rendered and "cafebabe" in rendered
    assert ask_sealed.PENDING not in rendered.split("## 3.")[0], (
        "a completed report must not carry PENDING cells in its tables")

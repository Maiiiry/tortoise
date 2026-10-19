"""Sealed ask-lane benchmark — pre-registration, sealed run, gate, receipt.

Epic #2281's Q3 exit bar is *"a published, sealed-benchmark measurement … of
the ask lane over a distilled + planted-supersession corpus"*. Issue #9095 built
the internal instrument (``tools/ask_spotcheck.py --distilled``); this module is
the PROMOTION of that instrument to the repo's pre-registered sealed-benchmark
discipline — the one artifact that closes the epic.

## Sealing (why the run cannot see its own answer key)

The corpus (`tests/eval/ask/`) is split in two trees:

* ``fixtures/`` — what the runner ingests: the planted turns + claim texts +
  the question. It carries NO grading key (``corpus.fixture_issues`` refuses a
  fixture that grows one).
* ``gold/`` — the SEALED key: what a correct answer must say. Only
  :func:`score_with_gold` reads it, and it runs AFTER the read path has
  finished. :func:`get_sealed_run` never opens it, and
  ``tests/test_ask_sealed_benchmark.py`` proves that by making the gold loader
  explode for the duration of a run.

``fixtures_hash`` covers fixtures AND gold, so a gold-only edit invalidates a
published number exactly as a fixture edit does.

## Pre-registration (why the bar is fixed before the run)

:func:`build_preregistration` writes the record — corpus identity (file
digests + ``fixtures_hash``), the split by case class, the scoring rules, and
the decision rule — BEFORE any arm runs. The published report then cites it,
and :func:`verify_preregistration_history` proves the two properties the issue
names as targets:

1. the pre-registration commit is an ANCESTOR of the run commit (git, not a
   claim), and
2. the pre-registration's bytes are unchanged between the two (so the scoring
   and decision rule cannot have been edited after seeing the numbers).

## Decision rule (fixed in advance, never re-chosen after the fact)

Three gates, evaluated on ONE run (the D4 rule: extend W2-b, no parallel
benchmark — both arms run per case in the same invocation):

* **D8 product gate — superseded claims never render live.** On the
  ``knowledge-update`` class, distilled arm: every planted claim must be
  OBSERVED as a distilled render (an unmeasured claim is never a pass) and
  ZERO may render live. This is the product promise
  (`docs/product-success-eval.md:132`), already stated — this module does not
  invent it.
* **D7 number — the knowledge-update current-state rate.** RECORDED, with **no
  pass threshold**: D8 of the epic says the first run SETS it. Carrying a
  threshold here would contradict that decision.
* **verbatim/raw no-regression.** On the ``verbatim`` class (a question whose
  answer is stated verbatim in a turn), the distilled arm must not commit
  FEWER correct answers than the paired raw arm — per case. The epic's exit bar
  is "commits current state on the knowledge-update class AND no regression on
  verbatim/raw", so a win on KU with a loss on verbatim is a FAIL, not a trade.
  This is a PAIRED COMPARISON inside one run, not a threshold, which is why it
  does not contradict the no-preset-threshold decision.

A corpus that carries only one class cannot make the claim, so an empty class
is a config mismatch (``inconclusive``), never a silent pass.
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/longmem_eval/ask_sealed.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        "`uv run python -m tools.longmem_eval.ask_sealed`"
    )

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from tests.eval.ask import corpus

#: Pre-registration schema id (sibling of
#: ``measure_temporal.py``'s ``measurement-preregistration/v1``).
PREREGISTRATION_SCHEMA = "ask-sealed-preregistration/v1"
RUN_SCHEMA = "ask-sealed-run/v1"

BENCHMARK = "ask-distilled-supersession"
EPIC = "2281"
ISSUE = "9097"

#: The two arms, per case, in ONE invocation (D4). ``distilled`` = the turn
#: store PLUS the distilled claims and the planted supersession.
ARMS = ("raw", "distilled")
GATE_ARM = "distilled"

#: Reader + judge identities the published number is pinned to. The reader is
#: the ask lane's own default (``tortoise/model_adapters.py``); the judge is the
#: OFFICIAL LongMemEval anscheck judge the rest of the eval lanes grade with, so
#: the number is comparable to them (never a bespoke rubric).
DEFAULT_READER_MODEL = "deepseek/deepseek-v4-flash"
JUDGE_PIN = "longmemeval-official-anscheck-gpt4o-2024-08-06-v1"
JUDGE_RUBRIC_ID = "longmemeval-official"

PREREGISTRATION_PATH = ("docs/benchmarks/ask-distilled-supersession/"
                        "preregistration.json")
REPORT_PATH = ("docs/benchmarks/ask-distilled-supersession/run-report.md")


def _sha16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ══════════════════════════════════════════════════════════════════════════
# The pre-registration
# ══════════════════════════════════════════════════════════════════════════

def decision_rule() -> dict:
    """The bar, fixed BEFORE the run. Immutable by contract: a change here is a
    NEW pre-registration, never an edit to a committed one (the history check
    exists precisely to make that visible)."""
    return {
        "d8_superseded_live": {
            "class": corpus.CLASS_KNOWLEDGE_UPDATE,
            "arm": GATE_ARM,
            "pass_iff": ("every planted claim observed as a distilled-claim "
                         "render (seen == n) AND zero rendered live"),
            "on_fail": "regression (gate_regression)",
            "source": ("docs/product-success-eval.md:132 — "
                       "'0% of superseded points appear as live answers'"),
            "note": ("the promise is already stated; this module does not "
                     "invent a threshold for it, it measures the stated one"),
        },
        "d7_current_state": {
            "class": corpus.CLASS_KNOWLEDGE_UPDATE,
            "arm": GATE_ARM,
            "rule": ("RECORD current_state committed/n. NO pass threshold — "
                     "epic D8: the first run sets it"),
            "reported_alongside": "the paired raw arm, same cases, same run",
        },
        "verbatim_no_regression": {
            "class": corpus.CLASS_VERBATIM,
            "arms": list(ARMS),
            "rule": ("for EVERY verbatim case, distilled.committed >= "
                     "raw.committed; any per-case loss fails the run"),
            "why": ("the epic's exit bar is 'commits current state on the "
                    "knowledge-update class AND no regression on verbatim/raw' "
                    "— a win on KU with a loss on verbatim is a FAIL, not a "
                    "trade"),
            "why_not_a_threshold": ("a paired within-run comparison of the "
                                    "same case in two arms, so it needs no "
                                    "pre-set number"),
        },
        "verdicts": {"pass": "all three gates pass",
                     "regression": "any gate fails (failure_origin "
                                   "gate_regression)",
                     "inconclusive": "corpus hash / config identity mismatch"},
    }


def scoring() -> dict:
    """How each number is computed — fixed before the run."""
    return {
        "arms_per_case": list(ARMS),
        "invocation": ("both arms in ONE invocation, one fresh store per arm "
                       "per case (D4: extend W2-b, no parallel benchmark)"),
        "ingest": ("raw arm: capture-shaped episodic turns; distilled arm: the "
                   "same turn store PLUS distilled claims written through the "
                   "product's own create_point(is_episodic=False, validFrom=…)"
                   " and the supersession through the product's own "
                   "supersede_point"),
        "current_state": ("semantic judge on question_type/question from the "
                          "fixture against the SEALED gold answer, "
                          "abstention=False; denominator = the class's cases"),
        "superseded_live": ("read over the ASSEMBLED READER EVIDENCE: a "
                            "distilled-claim render (a validity window) whose "
                            "BODY is the superseded claim with no flag = live; "
                            "[SUPERSEDED BY: …] or a closed window "
                            "[valid <from> → <to>] = flagged"),
        "judge_pin": JUDGE_PIN,
        "judge_rubric_id_hash": _sha16(JUDGE_RUBRIC_ID),
        "reader_pin": DEFAULT_READER_MODEL,
        "reader_constancy": ("one reader identity across both arms; the "
                             "resolution is recorded in the receipt and a "
                             "stub reader is never evidence"),
        "precision": ("n is small by design (3 planted pairs → 3 KU + 3 "
                      "verbatim cases; the standing method is run it small "
                      "and by hand first). The rates are recorded with their "
                      "n and carry NO precision claim; this is a first-run "
                      "record, not an estimate of a population"),
        "excluding_abs": ("no abstention cases; the ask lane's abstention path "
                          "is measured elsewhere"),
    }


def build_preregistration(*, corpus_root: Path = corpus.ASK_DIR,
                          reader_model: str | None = None,
                          run_command: str | None = None) -> dict:
    """The pre-registration record. Call this BEFORE any arm runs."""
    manifest = corpus.verify_manifest(corpus_root)
    if not manifest["ok"]:
        raise SystemExit(
            "refusing to pre-register a corpus whose manifest does not verify "
            f"({ {k: v for k, v in manifest.items() if k != 'fixtures_hash'} })")
    fixtures = corpus.load_fixtures(corpus_root)
    issues = [issue for fixture in fixtures for issue in corpus.fixture_issues(fixture)]
    if issues:
        raise SystemExit("refusing to pre-register a malformed corpus:\n  "
                         + "\n  ".join(issues))
    split = {name: sorted(f["case_id"] for f in fixtures
                          if f["case_class"] == name)
             for name in corpus.CASE_CLASSES}
    empty = [name for name, ids in split.items() if not ids]
    if empty:
        raise SystemExit(
            f"refusing to pre-register: class(es) {empty} carry no cases — the "
            "exit bar needs both a knowledge-update class and a verbatim "
            "control")
    return {
        "schema": PREREGISTRATION_SCHEMA,
        "benchmark": BENCHMARK,
        "issue": ISSUE,
        "epic": EPIC,
        "written_before_any_arm_run": True,
        "corpus": {
            "root": "tests/eval/ask",
            "files": manifest_files(corpus_root),
            "fixtures_hash": manifest["fixtures_hash"],
            "n_cases": len(fixtures),
        },
        "split": split,
        "arms": [{"id": arm} for arm in ARMS],
        "scoring": scoring(),
        "decision_rule": decision_rule(),
        "falsification": [
            {"probe": "drop the planted supersession (DELETE the CORRECTS edge, "
                      "reopen the window)",
             "expected": "d8_superseded_live fails (rendered_live > 0) ⇒ "
                         "verdict regression — the gate can say NO"},
            {"probe": "make the distilled arm lose a verbatim case",
             "expected": "verbatim_no_regression fails ⇒ verdict regression, "
                         "even when the KU number improves"},
            {"probe": "edit any fixture or gold byte",
             "expected": "fixtures_hash no longer matches the pre-registration "
                         "⇒ verdict inconclusive (hash_mismatch)"},
        ],
        "reader_model": reader_model or os.environ.get(
            "TORTOISE_ASK_MODEL", "").strip() or DEFAULT_READER_MODEL,
        "run_command": run_command or (
            "tools/run-with-eval-keys.sh .venv/bin/python -m "
            "tools.longmem_eval.ask_sealed run --prereg "
            f"{PREREGISTRATION_PATH} --commit $(git rev-parse HEAD) "
            "--run-out <run.json> --report-out " + REPORT_PATH),
        "report_path": REPORT_PATH,
    }


def manifest_files(corpus_root: Path = corpus.ASK_DIR) -> dict[str, str]:
    return dict(corpus.load_manifest(corpus_root)["files"])


def manifest_files_hash(corpus_root: Path = corpus.ASK_DIR) -> str:
    return corpus.compute_fixtures_hash(corpus_root)


def write_preregistration(path: str | Path, **kwargs: Any) -> dict:
    record = build_preregistration(**kwargs)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    return record


def load_preregistration(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ══════════════════════════════════════════════════════════════════════════
# Pre-registration history — "fixed in advance" as a CHECK, not a claim
# ══════════════════════════════════════════════════════════════════════════

def _git(*args: str, repo: str | Path = ".") -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True)


def verify_preregistration_history(prereg_path: str | Path,
                                   prereg_commit: str, run_commit: str,
                                   *, repo: str | Path = ".") -> dict:
    """Prove target (1): the pre-registration commit is an ancestor of the run
    commit AND the pre-registration is byte-identical at both.

    Returns ``{ok, ancestry_ok, unchanged_ok, issues}``. ``ok`` requires BOTH —
    ancestry alone is not enough: a file edited in the run commit would be an
    ancestor and still post-hoc.
    """
    issues: list[str] = []
    ancestry_ok = False
    unchanged_ok = False
    if prereg_commit == run_commit:
        issues.append(
            "pre-registration and run are the SAME commit — the bar was not "
            "fixed in advance")
    else:
        res = _git("merge-base", "--is-ancestor", prereg_commit, run_commit,
                   repo=repo)
        if res.returncode == 0:
            ancestry_ok = True
        else:
            issues.append(
                "the pre-registration commit is NOT an ancestor of the run "
                f"commit ({prereg_commit} ⊄ {run_commit}); git said: "
                f"{(res.stderr or res.stdout).strip()}")
    rel = str(Path(prereg_path).resolve().relative_to(
        Path(repo).resolve())) if Path(prereg_path).is_absolute() else str(
            prereg_path)
    fixed_at = _git("show", f"{prereg_commit}:{rel}", repo=repo)
    shown = _git("show", f"{run_commit}:{rel}", repo=repo)
    committed_at_pre = fixed_at.stdout if fixed_at.returncode == 0 else None
    at_run = shown.stdout if shown.returncode == 0 else None
    if committed_at_pre is None:
        issues.append(
            f"the pre-registration file {rel!r} does not exist at the "
            f"pre-registration commit {prereg_commit}: "
            f"{(fixed_at.stderr or '').strip()}")
    elif at_run is None:
        issues.append(
            f"the pre-registration file {rel!r} does not exist at the run "
            f"commit {run_commit}: {(shown.stderr or '').strip()}")
    elif committed_at_pre != at_run:
        issues.append(
            f"the pre-registration {rel!r} was CHANGED between the two commits "
            "— scoring/decision rule are not the pre-registered ones")
    else:
        unchanged_ok = True
        # An uncommitted retune is the same offence one step earlier.
        current = Path(repo, rel).read_text(encoding="utf-8")
        if current != at_run:
            unchanged_ok = False
            issues.append(
                f"the pre-registration {rel!r} differs from its committed bytes "
                "(uncommitted edit) — commit a NEW pre-registration instead")
    return {"ok": ancestry_ok and unchanged_ok, "ancestry_ok": ancestry_ok,
            "unchanged_ok": unchanged_ok, "issues": issues}


# ══════════════════════════════════════════════════════════════════════════
# The sealed run — no gold, no judge, no answer key in reach
# ══════════════════════════════════════════════════════════════════════════

def get_sealed_run(*, corpus_root: Path = corpus.ASK_DIR,
                   reader_model: str | None = None,
                   sdk_factory=None, mutate=None,
                   stub_reader: bool = False) -> dict:
    """Run BOTH arms over the sealed corpus. The sealed key is never read.

    Deliberately contains no call to ``corpus.load_gold`` (or any judge): the
    read path's only input is ``corpus.load_fixtures``, and grading happens
    afterwards in :func:`score_with_gold`. That ordering is the sealing, and the
    test suite enforces it by making the gold loader raise here.
    """
    # Lazy imports: the pure functions above (preregistration, evaluation) must
    # not require the graph stack.
    from tools.ask_spotcheck import (
        _seed_memory,
        _superseded_live_state,
        _to_iso_date,
        seed_distilled_corpus,
    )
    from tortoise.ask_lane import run_ask_lane
    from tortoise.sdk import TortoiseSDK

    if sdk_factory is None:
        import tempfile

        def sdk_factory():
            db = os.path.join(tempfile.mkdtemp(prefix="ask_sealed_"), "t.db")
            return TortoiseSDK(db)

    fixtures = corpus.load_fixtures(corpus_root)
    manifest = corpus.verify_manifest(corpus_root)
    if not manifest["ok"]:
        raise SystemExit(
            f"refusing to run on a corpus whose manifest does not verify: "
            f"{manifest}")

    records: list[dict] = []
    for case in fixtures:
        arms: dict[str, dict] = {}
        for arm in ARMS:
            sdk = sdk_factory()
            try:
                # The ONE write surface both arms share: the capture-shaped
                # turn store. The distilled arm adds the claims + supersession.
                _seed_memory(sdk, case)
                if arm == GATE_ARM:
                    seed_distilled_corpus(sdk, case)
                    if mutate is not None:
                        mutate(sdk)
                try:
                    result = run_ask_lane(
                        sdk, case["question"],
                        question_date=_to_iso_date(case.get("question_date")
                                                   or ""))
                except Exception as exc:
                    # retrieval fault is a MISS for this case, never a
                    # whole-run abort; the D8 gate fails CLOSED on the
                    # unmeasured, so a miss can never read as a pass.
                    arms[arm] = {"ok": False, "hypothesis": "",
                                 "old_seen": False, "rendered_live": False,
                                 "flagged": False,
                                 "note": f"ask unavailable: {type(exc).__name__}"}
                    continue
                evidence = result.get("evidence") or ""
                seen, rendered_live, flagged = _superseded_live_state(
                    evidence, case["old_claim"]["text"])
                arms[arm] = {"ok": True,
                             "hypothesis": result.get("answer") or "",
                             "old_seen": seen,
                             "rendered_live": rendered_live,
                             "flagged": flagged, "note": ""}
            finally:
                sdk.close()
        records.append({"case_id": case["case_id"],
                        "case_class": case["case_class"],
                        "arms": arms})

    return {
        "schema": RUN_SCHEMA,
        "benchmark": BENCHMARK,
        "issue": ISSUE,
        "corpus_hash": manifest["fixtures_hash"],
        "corpus_files": manifest_files(corpus_root),
        "reader_model": (reader_model
                         or os.environ.get("TORTOISE_ASK_MODEL", "").strip()
                         or DEFAULT_READER_MODEL),
        "stub_reader": bool(stub_reader),
        "judge_model": "",
        "judge_pin": "",
        "judge_rubric_id_hash": "",
        "graded": False,
        "cases": records,
    }


def score_with_gold(run: dict, *, corpus_root: Path = corpus.ASK_DIR,
                    judge=None) -> dict:
    """Grade a sealed run against the SEALED gold. The only gold reader.

    Runs AFTER the read path is finished, so the answer key cannot influence
    retrieval or generation.
    """
    if judge is None:
        from tools.longmem_eval.judge import build_judge
        judge = build_judge()
    gold = corpus.load_gold_all(corpus_root)
    fixtures = {f["case_id"]: f for f in corpus.load_fixtures(corpus_root)}
    key_issues = [issue for key in gold.values()
                  for issue in corpus.gold_issues(key)]
    if key_issues:
        raise SystemExit("sealed gold is malformed:\n  " + "\n  ".join(key_issues))

    scored = json.loads(json.dumps(run))  # deep copy, no shared state
    for record in scored["cases"]:
        case_id = record["case_id"]
        fixture, key = fixtures[case_id], gold[case_id]
        for arm in ARMS:
            reading = record["arms"][arm]
            reading["committed"] = bool(reading["hypothesis"]) and bool(
                judge.judge(question_type=fixture.get("question_type")
                            or "knowledge-update",
                            question=fixture["question"],
                            answer=key["answer"],
                            hypothesis=reading["hypothesis"],
                            abstention=False))
    scored["graded"] = True
    scored["judge_model"] = getattr(judge, "model_id", "") or ""
    scored["judge_pin"] = JUDGE_PIN
    scored["judge_rubric_id_hash"] = _sha16(JUDGE_RUBRIC_ID)
    scored["gold_case_ids"] = sorted(gold)
    return scored


# ══════════════════════════════════════════════════════════════════════════
# The gate + verdict
# ══════════════════════════════════════════════════════════════════════════

def _rate(count: int, n: int) -> float:
    return (count / n) if n else 0.0


def _case_rows(run: dict, case_class: str) -> list[dict]:
    return [c for c in run.get("cases") or []
            if c.get("case_class") == case_class]


def evaluate(run: dict, *, prereg: dict,
             corpus_root: Path = corpus.ASK_DIR) -> dict:
    """Apply the PRE-REGISTERED decision rule to a scored run.

    Returns ``{verdict, failure_origin, run_status, publishable, gates,
    metrics, issues}``. The rule comes from the pre-registration on disk (and
    is cross-checked against this module's constants, so a drifted module is a
    config mismatch rather than a quiet re-bar).
    """
    issues: list[str] = []
    origin: str | None = None

    manifest = corpus.verify_manifest(corpus_root)
    if not manifest["ok"]:
        issues.append(
            "corpus manifest does not verify "
            f"(missing={manifest['missing']} extra={manifest['extra']} "
            f"mismatched={manifest['mismatched']} "
            f"malformed={manifest['malformed']})")
        origin = "hash_mismatch"
    elif manifest["fixtures_hash"] != prereg["corpus"]["fixtures_hash"]:
        issues.append(
            "the corpus is not the pre-registered corpus: fixtures_hash "
            f"{manifest['fixtures_hash']} != "
            f"{prereg['corpus']['fixtures_hash']}")
        origin = "hash_mismatch"
    if run.get("corpus_hash") != prereg["corpus"]["fixtures_hash"]:
        issues.append(
            "the RUN's recorded corpus hash is not the pre-registered one: "
            f"{run.get('corpus_hash')} != {prereg['corpus']['fixtures_hash']}")
        origin = origin or "hash_mismatch"

    if prereg.get("decision_rule") != decision_rule():
        issues.append(
            "the pre-registration's decision rule differs from this module's — "
            "the bar was changed after it was fixed (new pre-registration "
            "required)")
        origin = origin or "config_mismatch"
    if prereg.get("scoring") != scoring():
        issues.append(
            "the pre-registration's scoring rules differ from this module's")
        origin = origin or "config_mismatch"
    if run.get("graded") and run.get("judge_pin") != prereg["scoring"]["judge_pin"]:
        issues.append(
            f"judge pin {run.get('judge_pin')!r} != pre-registered "
            f"{prereg['scoring']['judge_pin']!r}")
        origin = origin or "config_mismatch"
    if run.get("reader_model") != prereg.get("reader_model"):
        issues.append(
            f"reader model {run.get('reader_model')!r} != pre-registered "
            f"{prereg.get('reader_model')!r}")
        origin = origin or "config_mismatch"

    ku = _case_rows(run, corpus.CLASS_KNOWLEDGE_UPDATE)
    vb = _case_rows(run, corpus.CLASS_VERBATIM)
    for name, rows in ((corpus.CLASS_KNOWLEDGE_UPDATE, ku),
                       (corpus.CLASS_VERBATIM, vb)):
        if not rows:
            issues.append(
                f"the run carries no {name!r} cases — the exit bar needs both "
                "classes, so it cannot be evaluated")
            origin = origin or "config_mismatch"

    # ── D8: the product gate ────────────────────────────────────────────
    ku_seen = sum(1 for c in ku if c["arms"][GATE_ARM].get("old_seen"))
    ku_live = sum(1 for c in ku if c["arms"][GATE_ARM].get("rendered_live"))
    d8_ok = bool(ku) and ku_live == 0 and ku_seen == len(ku)
    d8 = {"class": corpus.CLASS_KNOWLEDGE_UPDATE, "arm": GATE_ARM,
          "n": len(ku), "seen": ku_seen, "rendered_live": ku_live,
          "ok": d8_ok,
          "reason": ("zero rendered live and every planted claim observed"
                     if d8_ok else
                     f"{ku_live} rendered live / {ku_seen} of {len(ku)} "
                     "observed — the promise is 0% live and an unobserved "
                     "claim is never a pass")}

    # ── D7: the KU number (recorded, no threshold) ──────────────────────
    ku_commit = sum(1 for c in ku if c["arms"][GATE_ARM].get("committed"))
    ku_raw = sum(1 for c in ku if c["arms"]["raw"].get("committed"))
    d7 = {"class": corpus.CLASS_KNOWLEDGE_UPDATE, "arm": GATE_ARM,
          "committed": ku_commit, "n": len(ku), "rate": _rate(ku_commit, len(ku)),
          "raw_committed": ku_raw, "raw_rate": _rate(ku_raw, len(ku)),
          "threshold": None,
          "note": ("RECORDED ONLY — no pass threshold (epic D8: the first run "
                   "sets it)")}

    # ── verbatim/raw no-regression (paired, per case) ───────────────────
    losses = []
    for case in vb:
        raw_committed = bool(case["arms"]["raw"].get("committed"))
        dist_committed = bool(case["arms"][GATE_ARM].get("committed"))
        if raw_committed and not dist_committed:
            losses.append(case["case_id"])
    vb_raw = sum(1 for c in vb if c["arms"]["raw"].get("committed"))
    vb_dist = sum(1 for c in vb if c["arms"][GATE_ARM].get("committed"))
    vb_ok = bool(vb) and not losses
    verbatim = {"class": corpus.CLASS_VERBATIM, "n": len(vb),
                "raw_committed": vb_raw, "distilled_committed": vb_dist,
                "regressions": losses, "ok": vb_ok,
                "reason": ("no verbatim case lost on the distilled arm"
                           if vb_ok else
                           f"distilled arm lost {losses} — a KU win never pays "
                           "for a verbatim loss")}

    gates = {"d8_superseded_live": d8, "verbatim_no_regression": verbatim}
    publishable = (bool(run.get("graded")) and not run.get("stub_reader")
                   and (run.get("judge_model") or "") != "mock-judge")
    if origin is not None:
        verdict = "inconclusive"
    elif all(gate["ok"] for gate in gates.values()):
        verdict = "pass"
    else:
        verdict = "regression"
        origin = "gate_regression"

    metrics = {
        "current_state_distilled": {
            "committed": d7["committed"], "n": d7["n"], "rate": d7["rate"]},
        "current_state_raw": {
            "committed": d7["raw_committed"], "n": d7["n"],
            "rate": d7["raw_rate"]},
        "superseded_live_distilled": {
            "seen": d8["seen"], "rendered_live": d8["rendered_live"],
            "n": d8["n"], "rate": _rate(d8["rendered_live"], d8["n"])},
        "verbatim_no_regression": {
            "raw_committed": vb_raw, "distilled_committed": vb_dist,
            "n": len(vb), "regressions": losses},
    }
    if not publishable:
        issues.append(
            "this run is NOT publishable evidence (stub reader or mock judge) "
            "— the numbers are a wiring/gate proof, never the epic's number")
    return {
        "verdict": verdict,
        "failure_origin": origin,
        "run_status": "completed" if publishable else "skipped",
        "publishable": publishable,
        "gates": gates,
        "metrics": metrics,
        "issues": issues,
    }


# ══════════════════════════════════════════════════════════════════════════
# Receipt (W7 vocabulary, ask-lane metric validation)
# ══════════════════════════════════════════════════════════════════════════

#: Ask-lane receipt metrics a completed run must carry. Deliberately NOT the
#: LongMemEval names: `w7_publish.validate_receipt` requires
#: `official_recall_all@k` + the per-category rows, which an ask run has no
#: meaning for. The VERDICT vocabulary is reused verbatim (see below) because it
#: already expresses "a win is not a trade" and the hash-mismatch case.
ASK_METRIC_KEYS = ("current_state_distilled", "superseded_live_distilled",
                   "verbatim_no_regression")


def build_receipt(run: dict, evaluation: dict, *, prereg: dict, commit: str,
                  date: str, cost_usd: float = 0.0,
                  notes: list[str] | None = None,
                  run_id: str | None = None,
                  prereg_commit: str | None = None) -> dict:
    """A receipt in the W7 shape, with ask-lane metric validation.

    Reuses `w7_publish.build_receipt`'s key vocabulary and `VERDICT_VALUES`
    verbatim (benchmark-generic), and deliberately does NOT route through
    `validate_receipt` — its completed-run requirements
    (`official_recall_all@k`, `official_recall_all@5_by_category` over
    `CATEGORY_GATE_TYPES`) are LongMemEval-only.
    """
    from tools.longmem_eval.w7_publish import build_receipt as _build

    receipt = _build(
        run_id=run_id or f"{BENCHMARK}-{date}",
        date=date,
        commit=commit,
        corpus_hash=run.get("corpus_hash") or "",
        judge_pin=run.get("judge_pin") or "",
        judge_model=run.get("judge_model") or "",
        judge_rubric_id_hash=run.get("judge_rubric_id_hash") or "",
        reader_model_spec=run.get("reader_model") or "",
        reader_pinned=True,
        ingest_mode="capture-turn-store + distilled-claims + supersede_point",
        run_status=evaluation["run_status"],
        verdict=evaluation["verdict"],
        failure_origin=evaluation["failure_origin"],
        cost_usd=cost_usd,
        metrics=evaluation["metrics"],
        notes=list(notes or []),
        resolved_config={"arms": list(ARMS), "gate_arm": GATE_ARM,
                         "preregistration": prereg.get("corpus", {}).get(
                             "fixtures_hash", ""),
                         "stub_reader": bool(run.get("stub_reader"))},
    )
    receipt["gates"] = evaluation["gates"]
    receipt["preregistration"] = {
        "schema": prereg.get("schema"),
        "path": PREREGISTRATION_PATH,
        "commit": prereg_commit or "",
        "corpus_hash": prereg.get("corpus", {}).get("fixtures_hash"),
    }
    return receipt


def validate_ask_receipt(receipt: dict) -> list[str]:
    """Ask-lane receipt validation (the auditor's re-run). Empty = valid.

    The generic half mirrors `w7_publish.validate_receipt`'s vocabulary checks;
    the metric half requires the ASK metrics instead of the LongMemEval ones.
    """
    from tools.longmem_eval.w7_publish import (
        FAILURE_ORIGIN_VALUES,
        RUN_STATUS_VALUES,
        VERDICT_VALUES,
    )

    issues: list[str] = []
    if not isinstance(receipt, dict):
        return ["receipt: not an object"]
    for key in ("run_id", "date", "commit", "corpus_hash"):
        value = receipt.get(key)
        if not isinstance(value, str) or not value.strip():
            issues.append(f"receipt.{key}: expected a non-empty string, "
                          f"got {value!r}")
    if receipt.get("run_status") not in RUN_STATUS_VALUES:
        issues.append(f"receipt.run_status: unexpected "
                      f"{receipt.get('run_status')!r}")
    if receipt.get("verdict") not in VERDICT_VALUES:
        issues.append(f"receipt.verdict: unexpected {receipt.get('verdict')!r}")
    if receipt.get("failure_origin") not in FAILURE_ORIGIN_VALUES:
        issues.append(
            f"receipt.failure_origin: unexpected {receipt.get('failure_origin')!r}")
    metrics = receipt.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        issues.append("receipt.metrics: expected a non-empty object")
    elif receipt.get("run_status") == "completed":
        missing = [key for key in ASK_METRIC_KEYS if key not in metrics]
        if missing:
            issues.append(
                "receipt.metrics: completed ask run is missing "
                f"{missing} (the ask-lane equivalents of the LongMemEval "
                "official rows)")
        if not isinstance(receipt.get("corpus_hash"), str) or not (
                receipt.get("corpus_hash") or "").startswith("sha256:"):
            issues.append("receipt.corpus_hash: expected a sha256:… corpus hash")
        for key in ("judge_model", "judge_rubric_id_hash"):
            if not str(receipt.get(key) or "").strip():
                issues.append(
                    f"receipt.{key}: completed run requires the pinned judge "
                    "identity")
    if not isinstance(receipt.get("resolved_config"), dict):
        issues.append("receipt.resolved_config: expected an object")
    if isinstance(receipt.get("cost_usd"), bool) or not isinstance(
            receipt.get("cost_usd"), (int, float)) or receipt["cost_usd"] < 0:
        issues.append("receipt.cost_usd: expected a number >= 0")
    if not isinstance(receipt.get("gates"), dict):
        issues.append("receipt.gates: expected the gate block (a verdict "
                      "without its gates is not re-checkable)")
    return issues


# ══════════════════════════════════════════════════════════════════════════
# Report rendering (the published artifact is MACHINE-derived)
# ══════════════════════════════════════════════════════════════════════════

PENDING = "PENDING"


def render_report(*, prereg: dict, run: dict | None = None,
                  evaluation: dict | None = None,
                  receipt: dict | None = None,
                  date: str | None = None) -> str:
    """Render `docs/benchmarks/<name>/run-report.md`.

    With no run this renders the PENDING machinery report (the W7-a precedent:
    a machinery commit states PENDING explicitly and filling it is an annotated
    erratum, never a silent edit). With a run it renders every number from the
    receipt — no hand-authored values.
    """
    executed = bool(run and evaluation and receipt)
    corpus_hash = prereg["corpus"]["fixtures_hash"]
    reader = prereg.get("reader_model", "")
    judge = prereg["scoring"]["judge_pin"]
    prereg_commit = ((receipt or {}).get("preregistration", {}).get("commit")
                     or PENDING) if executed else PENDING
    run_status_line = (
        "> **RUN STATUS (this commit): EXECUTED** — every number below is "
        "derived from the committed run artifact by this module "
        "(`ask_sealed.render_report`); no number is hand-authored."
        if executed else
        "> **RUN STATUS (this commit): PENDING — the sealed run was NOT "
        "executed in this commit.** The corpus, the sealed gold, the "
        "pre-registration, the runner, the gate and the receipt path are "
        "committed and tested; the read+judge execution needs live provider "
        "keys (reader + the official judge) and is a separately scheduled "
        "operation. **No metric value is claimed here.** Filling the tables "
        "is an annotated update (erratum discipline), never a silent edit.")
    lines: list[str] = [
        "---",
        f'title: "Ask lane — sealed distilled + planted-supersession '
        f'measurement ({BENCHMARK})"',
        "type: benchmarks",
        f'issue: "#{ISSUE}"',
        f'epic: "#{EPIC}"',
        f"date: {date or (receipt or {}).get('date') or 'PENDING'}",
        "doc_status: live",
        "ownedBy: epistemic-team",
        "aboutObjects: tortoise-ask-lane",
        f"preregistration: {PREREGISTRATION_PATH}",
        "---",
        "",
        "# Ask lane — sealed distilled + planted-supersession measurement",
        "",
        f"> **Epic:** #{EPIC} (D4 → extend W2-b; D7 measurement; D8 product "
        f"gate). **Issue:** #{ISSUE}. **Pre-registration:** "
        f"`{PREREGISTRATION_PATH}` (corpus, split, scoring and decision rule "
        "fixed BEFORE the run; the run commit is a descendant of the "
        "pre-registration commit, checked by "
        "`ask_sealed.verify_preregistration_history`).",
        ">",
        run_status_line,
        "",
        "## 1. Run identity",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Benchmark | `{BENCHMARK}` — sealed distilled + planted-supersession "
        "ask measurement |",
        f"| Corpus | `{prereg['corpus']['root']}` — "
        f"{prereg['corpus']['n_cases']} cases "
        f"({len(prereg['split'][corpus.CLASS_KNOWLEDGE_UPDATE])} "
        "knowledge-update + "
        f"{len(prereg['split'][corpus.CLASS_VERBATIM])} verbatim control) |",
        f"| Corpus hash (`fixtures_hash`, fixtures **and** sealed gold) | "
        f"`{corpus_hash}` |",
        f"| Reader | `{reader}` (ask lane default; one identity across both "
        "arms) |",
        f"| Judge | `{judge}` (official LongMemEval anscheck, rubric "
        f"`{prereg['scoring']['judge_rubric_id_hash']}`) |",
        "| Arms | `raw` (turn store) · `distilled` (turn store + distilled "
        "claims + planted supersession), BOTH in one invocation (D4) |",
        f"| Pre-registration commit | "
        f"{prereg_commit} |",
        f"| Run commit | {receipt['commit'] if executed else PENDING} |",
        f"| Verdict | {receipt['verdict'] if executed else PENDING} |",
        f"| Receipt validation | "
        f"{('clean' if not validate_ask_receipt(receipt) else 'ISSUES') if executed else PENDING} |",
        "",
        "## 2. The numbers",
        "",
        "### 2.1 D7 — knowledge-update current-state rate (distilled arm)",
        "",
        "**No pass threshold by design** — epic D8: *the first run sets it*. "
        "The paired raw arm is reported alongside because both arms run the "
        "same cases in the same invocation.",
        "",
        "| Metric | Value | n | Semantics |",
        "|---|---|---|---|",
    ]
    if executed:
        m = receipt["metrics"]
        cs, raws = m["current_state_distilled"], m["current_state_raw"]
        live = m["superseded_live_distilled"]
        vb = m["verbatim_no_regression"]
        lines += [
            f"| `current_state` (distilled) | {cs['committed']}/{cs['n']} "
            f"({cs['rate']:.2f}) | {cs['n']} | judge: does the answer commit "
            "the CURRENT state |",
            f"| `current_state` (raw, paired) | {raws['committed']}/{raws['n']} "
            f"({raws['rate']:.2f}) | {raws['n']} | same cases, turn store only |",
            "",
            "### 2.2 D8 — superseded claims rendered live (product gate)",
            "",
            "| Metric | Value | n | Semantics |",
            "|---|---|---|---|",
            f"| `superseded_live` | {live['rendered_live']}/{live['n']} "
            f"({live['rate']:.2f}) | {live['n']} | "
            "'0% of superseded points appear as live answers' "
            "(`docs/product-success-eval.md:132`) |",
            f"| `observed` | {live['seen']}/{live['n']} | {live['n']} | a "
            "claim the instrument could not observe is UNMEASURED, never a "
            "pass |",
            "",
            "### 2.3 Verbatim/raw no-regression (paired, per case)",
            "",
            "| Arm | Committed | n | Regressions |",
            "|---|---|---|---|",
            f"| `raw` | {vb['raw_committed']}/{vb['n']} | {vb['n']} | — |",
            f"| `distilled` | {vb['distilled_committed']}/{vb['n']} | "
            f"{vb['n']} | {vb['regressions'] or 'none'} |",
            "",
            "The exit bar is conjunctive: a knowledge-update win with a "
            "verbatim loss is a **FAIL**, not a trade.",
        ]
    else:
        lines += [
            f"| `current_state` (distilled) | {PENDING} | — | judge: does the "
            "answer commit the CURRENT state (no threshold — first run sets "
            "it) |",
            f"| `current_state` (raw, paired) | {PENDING} | — | same cases, "
            "turn store only |",
            "",
            "### 2.2 D8 — superseded claims rendered live (product gate)",
            "",
            "| Metric | Value | n | Semantics |",
            "|---|---|---|---|",
            f"| `superseded_live` | {PENDING} | — | "
            "'0% of superseded points appear as live answers' "
            "(`docs/product-success-eval.md:132`) |",
            f"| `observed` | {PENDING} | — | a claim the instrument could not "
            "observe is UNMEASURED, never a pass — the gate fails closed |",
            "",
            "### 2.3 Verbatim/raw no-regression (paired, per case)",
            "",
            "| Arm | Committed | n | Regressions |",
            "|---|---|---|---|",
            f"| `raw` | {PENDING} | — | — |",
            f"| `distilled` | {PENDING} | — | {PENDING} |",
            "",
            "The exit bar is conjunctive: a knowledge-update win with a "
            "verbatim loss is a **FAIL**, not a trade.",
        ]
    lines += [
        "",
        "## 3. Reproducing the run",
        "",
        "```bash",
        prereg["run_command"],
        "```",
        "",
        "The sealed gold (`tests/eval/ask/gold/`) is read ONLY by the scorer, "
        "after the read path finishes: `get_sealed_run` opens fixtures only "
        "(asserted in `tests/test_ask_sealed_benchmark.py`, which makes the "
        "gold loader raise for the duration of a run).",
        "",
        "## 4. Falsification (the gate can say NO)",
        "",
    ]
    for probe in prereg["falsification"]:
        lines.append(f"- **{probe['probe']}** → {probe['expected']}")
    lines += [
        "",
        "## 5. What this measures — and what it does not",
        "",
        "- **Measures:** whether the distilled + planted-supersession read "
        "path (a) tells the reader the current state, (b) never shows a "
        "superseded claim as live, and (c) does not cost verbatim recall.",
        "- **Does NOT measure** a population: n is small by design (3 planted "
        "pairs). Rates are first-run records carrying no precision claim.",
        "- **Does NOT set a threshold** on the knowledge-update number (epic "
        "D8). The only bars are the stated product promise (0% live) and the "
        "paired verbatim comparison.",
        "- **Not a marketing number** — a defensible one, same discipline as "
        "the existing LongMemEval publications.",
        "",
        "## 6. Errata discipline",
        "",
        "Filling the PENDING tables is a separate commit. The pre-registration "
        "file is immutable: changing the corpus, split, scoring or decision "
        "rule requires a NEW pre-registration (+ a new corpus hash), never an "
        "edit — a change after the numbers are known is what the "
        "history check exists to make impossible.",
    ]
    return "\n".join(lines).rstrip("\n") + "\n"


# ══════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════

def _main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m tools.longmem_eval.ask_sealed",
        description="Sealed ask-lane benchmark: pre-registration, sealed run, "
                    "gate, receipt, report (epic #2281 Q3 exit bar, #9097).")
    sub = p.add_subparsers(dest="cmd", required=True)

    pre = sub.add_parser("preregister",
                         help="write the pre-registration (BEFORE any run)")
    pre.add_argument("--out", default=PREREGISTRATION_PATH)
    pre.add_argument("--reader-model", default=None)

    rep = sub.add_parser("report",
                         help="render the report (PENDING with no --run)")
    rep.add_argument("--prereg", default=PREREGISTRATION_PATH)
    rep.add_argument("--run", default=None, help="scored run JSON")
    rep.add_argument("--out", default=REPORT_PATH)
    rep.add_argument("--date", default=None,
                     help="report date (default: today)")

    run = sub.add_parser("run", help="execute the sealed run + grade + gate")
    run.add_argument("--prereg", default=PREREGISTRATION_PATH)
    run.add_argument("--commit", required=True, help="exact run commit sha")
    run.add_argument("--run-out", required=True)
    run.add_argument("--report-out", default=None)
    run.add_argument("--cost-usd", type=float, default=0.0)
    run.add_argument("--prereg-commit", default=None,
                     help="pre-registration commit (history check)")

    args = p.parse_args(argv)

    if args.cmd == "preregister":
        record = write_preregistration(args.out,
                                       reader_model=args.reader_model)
        print(f"wrote {args.out} — corpus hash "
              f"{record['corpus']['fixtures_hash']}, "
              f"{record['corpus']['n_cases']} cases")
        return 0

    if args.cmd == "report":
        from datetime import date as _date

        prereg = load_preregistration(args.prereg)
        report_date = args.date or _date.today().isoformat()
        if not args.run:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(
                render_report(prereg=prereg, date=report_date),
                encoding="utf-8")
            print(f"wrote PENDING report {args.out}")
            return 0
        payload = json.loads(Path(args.run).read_text(encoding="utf-8"))
        run_doc, evaluation = payload["run"], payload["evaluation"]
        receipt = payload["receipt"]
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            render_report(prereg=prereg, run=run_doc, evaluation=evaluation,
                          receipt=receipt, date=report_date),
            encoding="utf-8")
        print(f"wrote report {args.out} — verdict {receipt['verdict']}")
        return 0

    # run
    prereg = load_preregistration(args.prereg)
    manifest = corpus.verify_manifest()
    if not manifest["ok"]:
        print(f"refusing to run: corpus manifest does not verify: {manifest}",
              file=sys.stderr)
        return 2
    if manifest["fixtures_hash"] != prereg["corpus"]["fixtures_hash"]:
        print("refusing to run: the corpus is not the pre-registered corpus "
              f"({manifest['fixtures_hash']} != "
              f"{prereg['corpus']['fixtures_hash']})", file=sys.stderr)
        return 2
    if args.prereg_commit:
        history = verify_preregistration_history(
            args.prereg, args.prereg_commit, args.commit)
        if not history["ok"]:
            print("refusing to run: the pre-registration history check failed "
                  f"({history['issues']})", file=sys.stderr)
            return 2

    raw = get_sealed_run()
    from tools.longmem_eval.judge import build_judge

    scored = score_with_gold(raw, judge=build_judge())
    evaluation = evaluate(scored, prereg=prereg)
    from datetime import date as _date
    receipt = build_receipt(scored, evaluation, prereg=prereg,
                            commit=args.commit, date=_date.today().isoformat(),
                            cost_usd=args.cost_usd)
    issues = validate_ask_receipt(receipt)
    payload = {"run": scored, "evaluation": evaluation, "receipt": receipt,
               "receipt_issues": issues}
    Path(args.run_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.run_out).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"verdict {receipt['verdict']} "
          f"(failure_origin {receipt['failure_origin']}) — {args.run_out}")
    if issues:
        print("receipt issues: " + "; ".join(issues), file=sys.stderr)
    if args.report_out:
        Path(args.report_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report_out).write_text(
            render_report(prereg=prereg, run=scored, evaluation=evaluation,
                          receipt=receipt), encoding="utf-8")
        print(f"wrote report {args.report_out}")
    return 0 if receipt["verdict"] != "regression" else 1


if __name__ == "__main__":
    raise SystemExit(_main())

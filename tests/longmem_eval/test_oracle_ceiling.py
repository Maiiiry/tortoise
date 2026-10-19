"""#2976 — the ORACLE CEILING, made reproducible from committed artifacts.

The issue's central inference is: the shipped config answers 0/52 answerable
temporal questions while an oracle reader served the gold sessions answers
42/52, therefore the defect is retrieval/admission, not reader capability. The
42/52 came from `tools/longmem_eval/oracle_ceiling.py`, which was **never
committed** — only its rows (`docs/runbook/2578-oracle-ceiling.jsonl`) were.
These tests pin the harness that closes that provenance gap:

* the grader's semantics — the benchmark's own multi-variant gold form
  ("7 days. 8 days (including the last day) is also acceptable.") is split
  ONLY on the "is also acceptable" marker, so a marker-free multi-sentence
  gold cannot be inflated into extra variants;
* the REPRODUCTION — scoring the committed rows against the pinned gold map
  yields 42/52 variant-aware and 25/52 plain (the floor), the per-class table
  the issue quotes, and per-row agreement with the committed `grade` column
  (which is the PLAIN rule — counting it reproduces the floor, not the
  ceiling, and that trap is pinned here explicitly);
* the reader half — `gold_session_hits` serves ONLY the answer sessions (not
  the full haystack), fails closed on an unresolvable join, and the mock run
  end-to-end scores a smaller context than the full-context cell for the same
  instance.

Hermetic: committed artifacts + the committed gold fixture. No DB, no
network, no LLM. Refs #2976, #2578.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.longmem_eval.full_context import full_haystack_hits
from tools.longmem_eval.judge import MockJudge, build_judge
from tools.longmem_eval.oracle_ceiling import (
    ceiling_instances,
    gold_session_hits,
    gold_variants,
    grade_plain,
    grade_variant_aware,
    load_rows,
    run_cell,
    score_rows,
)
from tools.longmem_eval.reader import build_reader
from tools.longmem_eval.retrieve import _estimate_tokens, render_context

REPO = Path(__file__).resolve().parent.parent.parent
ORACLE_ROWS = REPO / "docs" / "runbook" / "2578-oracle-ceiling.jsonl"
GOLD_FIXTURE = REPO / "tests" / "fixtures" / "longmem_oracle_gold_2578.json"

#: the reproduction pins (the figures #2976 quotes)
ANSWERABLE = 52
VARIANT_AWARE = 42
PLAIN_FLOOR = 25
REFUSALS = 4
BY_CLASS = {
    "current-state": (1, 1, 1),      # n, plain, variant-aware
    "interval": (19, 3, 18),
    "ordering/compare": (32, 21, 23),
}
ORACLE_TOKEN_MEDIAN = 5011.5

#: the benchmark's own multi-variant gold form, verbatim from the split.
MULTI_VARIANT_GOLD = "7 days. 8 days (including the last day) is also acceptable."


def _gold_fixture() -> dict:
    return json.loads(GOLD_FIXTURE.read_text(encoding="utf-8"))


def _score_committed_rows() -> dict:
    return score_rows(load_rows(ORACLE_ROWS), _gold_fixture()["gold"])


def _instance(**overrides) -> dict:
    base = {
        "question_id": "q1",
        "question_type": "temporal-reasoning",
        "question": "Which happened first, the museum visit or the exhibit?",
        "question_date": "2023/02/01 (Wed) 10:00",
        "answer": "the museum visit",
        "answer_session_ids": ["sess-a"],
        "haystack_session_ids": ["sess-a", "sess-b", "sess-c"],
        "haystack_dates": [
            "2023/01/08 (Sun) 09:00",
            "2023/01/15 (Sun) 09:00",
            "2023/01/20 (Fri) 09:00",
        ],
        "haystack_sessions": [
            [{"role": "user", "content": "I went to the museum today.",
              "has_answer": True}],
            [{"role": "user", "content": "I saw the ancient exhibit.",
              "has_answer": False}],
            [{"role": "user", "content": "I bought a new lamp.",
              "has_answer": False}],
        ],
    }
    base.update(overrides)
    return base


# ── the grader: variant-aware containment ───────────────────────────────────


def test_gold_variants_splits_the_benchmark_multivariant_form():
    """The gold's own "is also acceptable" convention yields both readings."""
    assert gold_variants(MULTI_VARIANT_GOLD) == ["7 days", "8 days"]
    assert gold_variants(
        "10 days ago. 11 days (including the last day) is also acceptable."
    ) == ["10 days ago", "11 days"]


def test_gold_variants_never_splits_a_marker_free_gold():
    """No marker → ONE variant, even across sentences (no inflation path).

    Splitting every multi-sentence gold would let any incidental clause match
    a hypothesis and inflate the ceiling this harness exists to bound.
    """
    gold = "You posted the recipe first. The challenge came later."
    assert gold_variants(gold) == [gold.strip(".")]
    assert gold_variants("The arrival of the new prime lens") == [
        "The arrival of the new prime lens"]


def test_gold_variants_coerces_numeric_and_empty_gold():
    """The benchmark ships integer golds; empty gold is no variant at all."""
    assert gold_variants(4) == ["4"]
    assert gold_variants(None) == []
    assert gold_variants("   ") == []


def test_variant_aware_grades_where_plain_containment_fails():
    """The whole point of the variant rule, on the issue's own example.

    A substantively correct answer ("7 days") does not contain the multi-
    variant gold verbatim, so plain containment scores it WRONG; the
    variant-aware rule scores it CORRECT.
    """
    hypothesis = ("7 days passed between your visit to the Museum of Modern "
                  "Art and the 'Ancient Civilizations' exhibit.")
    assert grade_plain(MULTI_VARIANT_GOLD, hypothesis) is False
    assert grade_variant_aware(MULTI_VARIANT_GOLD, hypothesis) is True

    # and the second acceptable reading is honoured too
    assert grade_variant_aware(MULTI_VARIANT_GOLD, "8 days (including today).")


def test_variant_aware_never_grades_empties():
    """An empty hypothesis/gold is never a free pass (containment's trap)."""
    assert grade_variant_aware(MULTI_VARIANT_GOLD, "") is False
    assert grade_variant_aware("", "7 days") is False
    assert grade_variant_aware(MULTI_VARIANT_GOLD, "   ") is False
    assert grade_plain(MULTI_VARIANT_GOLD, "") is False


def test_variant_aware_does_not_inflate_a_wrong_answer():
    """A wrong answer stays wrong: the rule adds readings, not leniency."""
    assert grade_variant_aware(MULTI_VARIANT_GOLD, "11 days passed.") is False
    assert grade_variant_aware(
        "I participated in the charity bake sale first.",
        "The charity gala came first.") is False


# ── the reproduction: committed rows + committed gold ───────────────────────


def test_score_reproduces_the_oracle_ceiling_the_issue_cites():
    """42/52 variant-aware, 25/52 plain, 4 refusals — from committed artifacts."""
    result = _score_committed_rows()
    assert result["answerable"] == ANSWERABLE
    assert result["variant_aware"]["correct"] == VARIANT_AWARE
    assert result["variant_aware"]["rate"] == round(VARIANT_AWARE / ANSWERABLE, 4)
    assert result["plain_containment"]["correct"] == PLAIN_FLOOR
    assert result["reader_refusals"]["n"] == REFUSALS
    assert result["missing_gold"] == []


def test_score_reproduces_the_per_class_table():
    """interval 18/19, ordering/compare 23/32, current-state 1/1."""
    result = _score_committed_rows()
    assert result["by_class"] == {
        cls: {"n": n, "plain": plain, "variant_aware": variant}
        for cls, (n, plain, variant) in BY_CLASS.items()
    }


def test_score_reports_the_oracle_evidence_volume():
    """The oracle serves ~5k tokens of whole sessions — the 52x gap to ~97."""
    result = _score_committed_rows()
    tokens = result["oracle_context_tokens"]
    assert tokens["median"] == ORACLE_TOKEN_MEDIAN
    assert tokens["min"] == 3329 and tokens["max"] == 13735
    # the measured retrieval arms hand the reader ~97 tokens (#2578): the
    # ceiling's context is ~50x larger, which is why "gold admitted" never
    # equalled "enough evidence to answer".
    assert tokens["median"] > 50 * 97


def test_committed_grade_column_is_the_PLAIN_floor_not_the_ceiling():
    """The trap this harness exists to expose.

    The committed rows' `grade` column applies the PLAIN rule, so counting
    CORRECT reproduces 25/55 — a downstream reader who does not apply the
    variant rule half-credits the ceiling. The scorer's agreement census is
    what makes that discrepancy visible: it must agree on every row AND the
    two figures must differ.
    """
    rows = load_rows(ORACLE_ROWS)
    committed_correct = sum(1 for r in rows if r["grade"] == "CORRECT")
    assert committed_correct == PLAIN_FLOOR
    assert committed_correct != VARIANT_AWARE

    result = _score_committed_rows()
    assert result["grade_agreement"]["disagree"] == []
    assert result["grade_agreement"]["agree"] == len(rows)


def test_score_never_scores_a_missing_gold_as_wrong():
    """A fixture/join gap is reported, not silently converted into a miss."""
    rows = load_rows(ORACLE_ROWS)[:2]
    gold = _gold_fixture()["gold"]
    only_first = {rows[0]["qid"]: gold[rows[0]["qid"]]}
    result = score_rows(rows, only_first)
    assert result["missing_gold"] == [rows[1]["qid"]]
    assert result["answerable"] == 1


# ── the reader half: gold sessions only ────────────────────────────────────


def test_gold_session_hits_serve_only_the_answer_sessions():
    """The oracle removes RETRIEVAL: only the gold session is in context."""
    question = _instance()
    hits = gold_session_hits(question)
    assert len(hits) == 1
    hit = hits[0]
    assert hit["lme_session_index"] == 0
    assert hit["session_date"] == question["haystack_dates"][0]
    assert hit["has_answer"] is True
    assert "museum" in hit["content"]
    # the OTHER sessions' content is absent by construction
    assert "ancient exhibit" not in hit["content"]
    assert "new lamp" not in hit["content"]

    # ... and the full-context cell for the same instance serves all three.
    assert len(full_haystack_hits(question)) == 3


def test_gold_session_hits_fail_closed_on_an_unresolvable_join():
    """Never serve an empty/partial oracle context as if it were the ceiling."""
    broken = _instance(answer_session_ids=["not-in-haystack"])
    try:
        gold_session_hits(broken)
    except ValueError as exc:
        assert "did not resolve" in str(exc)
    else:  # pragma: no cover - the assertion IS the test
        raise AssertionError("an unresolvable answer-session id must raise")

    abstention = _instance(answer_session_ids=[])
    try:
        gold_session_hits(abstention)
    except ValueError as exc:
        assert "no answer session" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("an abstention instance is not a ceiling question")


def test_ceiling_instances_counts_abstention_questions_instead_of_failing():
    """Abstention instances are excluded up front, not reported as failures."""
    kept, skipped = ceiling_instances([
        _instance(),
        _instance(question_id="q_abs", answer_session_ids=[]),
    ])
    assert [q["question_id"] for q in kept] == ["q1"]
    assert skipped == ["q_abs"]


def test_mock_run_scores_a_smaller_context_than_the_full_haystack_cell():
    """End-to-end (offline): the gold restriction is what reaches the reader.

    Same instance, same mock reader/judge: the oracle cell's context is the
    gold session alone, so its token count is strictly smaller than the
    full-haystack cell's — the mechanical proof that `hits_for` is wired
    through the reader loop rather than ignored.
    """
    question = _instance()
    reader = build_reader(None, mock=True)
    judge = build_judge(None, mock=True)
    assert isinstance(judge, MockJudge) or judge.model_id.startswith("mock")

    outcomes, report = run_cell(
        [question], reader=reader, judge=judge, split="oracle")
    assert len(outcomes) == 1
    oracle_tokens = outcomes[0]["context_tokens"]
    # the same estimator + renderer the reader loop uses, over the full haystack
    full_tokens = _estimate_tokens(
        render_context(full_haystack_hits(question),
                       question_date=question["question_date"]))
    assert 0 < oracle_tokens < full_tokens
    # the report is labelled as the oracle cell, never as a full-context or
    # retrieval-backed run (a published number carries its methodology).
    assert report["cell"].startswith("oracle-ceiling")
    assert "gold answer sessions" in report["methodology"]["retrieval_scope"]
    assert report["oracle_ceiling"]["answerable"] == 1

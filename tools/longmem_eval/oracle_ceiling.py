"""#2976 — ORACLE CEILING: what the reader answers when retrieval is removed.

WHY THIS EXISTS: issue #2976 turns on one number. The shipped retrieval
config answers **0 of 52** answerable temporal questions, while a reader
handed the **gold sessions verbatim** answers **42/52 (81%)** and refuses
only 4/52 — so the 0/52 is a retrieval/admission defect, not a reader or
temporal-reasoning defect. That inference is only as good as the ceiling it
rests on, and the ceiling was produced by a script that was **never
committed**: `tools/longmem_eval/oracle_ceiling.py` did not exist in the
repository or its history, while the rows it produced
(`docs/runbook/2578-oracle-ceiling.jsonl`) and the derived figure did.

This module is that script, in the two halves the measurement has:

``run`` — serve the gold sessions verbatim (the ``answer_session_ids``
    sessions, rendered in the official ``gen.py`` shape via the same
    ``render_context`` the retrieval path uses), ask the reader, and write
    per-question rows in the committed schema. ``--mock`` runs it fully
    offline (MockReader + MockJudge), so the wiring is exercised keylessly.

``score`` — re-derive the ceiling from ALREADY-COMMITTED hypotheses and the
    benchmark gold, with no reader call at all. This is the half that makes
    the issue's number reproducible, and it is where the honest caveat lives
    (see below).

THE GRADER — why not plain containment
--------------------------------------
The official judge (``gpt-4o-2024-08-06``) was unavailable when the ceiling
was measured, so the run graded with a deterministic **variant-aware
containment** check. Plain containment is WRONG on this benchmark's own gold
format: a gold answer may be multi-variant —

    7 days. 8 days (including the last day) is also acceptable.

— and no reasonable answer contains that string verbatim even when it is
substantively correct. ``gold_variants`` therefore splits the gold on the
benchmark's own ``"is also acceptable"`` convention (and ONLY that
convention: a gold with no marker is one variant, so this cannot inflate an
answer just by being multi-sentence), and the variant-aware grade is correct
iff ANY variant is contained in the hypothesis (the strict containment rule
of ``judge.grade_label``, applied per variant).

Both figures are reported, and the plain one is a FLOOR:

    plain          25/52   what single-string containment sees
    variant-aware  42/52   the ceiling figure #2976 cites
    official judge  unmeasured — the gap between the two is a grader
                   artifact, not a reader artifact

MEASURED, from the committed rows (``score --rows docs/runbook/2578-oracle-ceiling.jsonl``):

    answerable                       52
    plain containment                25/52  (48.1%)
    variant-aware                    42/52  (80.8%)
    reader refusals (answerable)      4/52
    by class (variant-aware)  interval 18/19 · ordering/compare 23/32 ·
                              current-state 1/1
    oracle context tokens      median 5 012 · mean 5 068 · min 3 329 · max 13 735

Two facts this harness exists to keep reproducible:

1. **The committed per-row ``grade`` field is the PLAIN figure.** Summing it
   gives 25/55 — a downstream reader who counts grades without applying the
   variant rule reproduces the FLOOR and concludes the ceiling is half what
   the issue says. ``score`` reports the agreement between the committed
   grade column and its own plain grade so that discrepancy is explicit, not
   silent (measured: 55/55 rows agree on the plain rule).
2. **The evidence-volume gap is ~52x.** The reader is served a median 5 012
   oracle tokens of whole sessions; the measured retrieval arms hand it ~97
   tokens of ~8-token fragments (#2578). "Gold point admitted" is not "enough
   evidence to answer" — that is #2976's second defect (evidence granularity).

WHAT THIS DOES NOT DO: it does not re-run the reader, and it does not move
the ceiling. A re-measure under the official judge needs a judge key (the
OpenRouter key that blocked the original run is exhausted); ``run --judge``
with a real model is the path, and ``score`` alone reproduces the cited
number from committed artifacts. The oracle is an UPPER BOUND by
construction: real retrieval never serves the gold sessions verbatim.

Usage::

    # re-derive the ceiling from the committed rows (offline, no keys)
    uv run python -m tools.longmem_eval.oracle_ceiling score \
        --rows docs/runbook/2578-oracle-ceiling.jsonl \
        --gold tests/fixtures/longmem_oracle_gold_2578.json

    # the same, straight from the digest-pinned dataset split (downloads once)
    uv run python -m tools.longmem_eval.oracle_ceiling score --split oracle

    # offline wiring check of the reader half
    uv run python -m tools.longmem_eval.oracle_ceiling run \
        --data tests/fixtures/longmemeval_mini.json --mock --limit 5

Refs #2976, #2578.
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/longmem_eval/oracle_ceiling.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python -m tools.longmem_eval.oracle_ceiling`"
    )

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any

from . import dataset as ds
from .judge import _normalize_answer_text, build_judge, grade_label
from .reader import build_reader
from .report import save_report
from .retrieve import (
    _estimate_tokens,
    render_context,
    resolve_answer_session_indices,
)

#: The benchmark's own multi-variant gold marker. A gold answer may offer
#: several acceptable readings ("7 days. 8 days (including the last day) is
#: also acceptable."), and no hypothesis contains that string verbatim. This
#: is the ONLY convention we split on — see ``gold_variants``.
GOLD_VARIANT_MARKER = "is also acceptable"

#: Trailing punctuation stripped from a gold variant before containment
#: (mirrors ``judge._normalize_answer_text``'s convention).
_EDGE_PUNCTUATION = ".,;:!?()[]{}\"'`’‘“”«»…-"

CELL = "oracle-ceiling (gold sessions served verbatim, no retrieval)"

CELL_EXTRACTION_APPROACH = (
    "oracle-ceiling cell (#2976): the reader received ONLY the gold answer "
    "sessions verbatim (answer_session_ids), rendered in the official gen.py "
    "shape — no graph, no retrieval, no ranking. Measures the conversion "
    "ceiling any retrieval leg must approach; an UPPER BOUND by construction."
)


# ── gold sessions → reader context ───────────────────────────────────────────


def gold_session_hits(question: dict) -> list[dict]:
    """Synthesize the hit list that IS the gold-session oracle context.

    Sibling of ``full_context.full_haystack_hits`` with the oracle's
    restriction: only the sessions the benchmark names as ANSWER sessions
    (``answer_session_ids``) are served — the retrieval step is removed
    entirely, so anything the reader fails here is reader/grader, not
    retrieval.

    The id → position mapping is ``retrieve.resolve_answer_session_indices``,
    the same resolver the retrieval lane's presence gate uses, so "which
    session is gold" cannot drift between the two lanes. A join failure
    (``(None, reason)``) or the abstention exemption (no answer sessions)
    raises rather than silently serving an EMPTY context: an empty oracle
    context would measure nothing while looking like a measured ceiling.
    """
    from .ingest import _session_transcript

    indices, reason = resolve_answer_session_indices(question)
    if indices is None:
        raise ValueError(
            f"answer_session_ids did not resolve to haystack sessions "
            f"({reason}) for {question.get('question_id')!r}")
    if not indices:
        raise ValueError(
            f"{question.get('question_id')!r} names no answer session "
            f"(abstention exemption) — not an oracle-ceiling question")

    dates: list[str] = question.get("haystack_dates") or []
    sessions: list[list[dict]] = question.get("haystack_sessions") or []
    hits: list[dict[str, Any]] = []
    for si in indices:
        session = sessions[si]
        hits.append({
            "id": f"oracle:{question['question_id']}:s{si}",
            "content": _session_transcript(session),
            "match_source": "oracle",
            "session_id": f"oracle-{si}",
            "lme_session_index": si,
            "session_date": dates[si] if si < len(dates) else "",
            # every gold session is answer-bearing by construction; the mark
            # is what the READER prompt and the mock reader key on.
            "has_answer": True,
            "superseded_by": None,
            "supersedes": [],
        })
    return hits


def ceiling_instances(instances: list[dict]) -> tuple[list[dict], list[str]]:
    """Split instances into (oracle-ceiling questions, abstention qids).

    An ABSTENTION instance names no answer session by design — there is no
    gold session to serve, so it is not part of the ceiling; it is excluded
    up front (and counted) rather than failing per question, because a run
    that reports abstentions as failures makes a healthy run look broken.
    """
    kept: list[dict] = []
    skipped: list[str] = []
    for question in instances:
        if question.get("answer_session_ids"):
            kept.append(question)
        else:
            skipped.append(str(question.get("question_id")))
    return kept, skipped


# ── the grader: variant-aware containment ────────────────────────────────────


def _split_sentences(text: str) -> list[str]:
    """Split on sentence-final punctuation followed by whitespace."""
    return [p.strip() for p in re.split(r"(?<=[.!?])\s+", text.strip())
            if p.strip()]


def _strip_acceptable_wrapper(sentence: str) -> str:
    """``"8 days (including the last day) is also acceptable."`` → ``"8 days"``.

    A sentence carrying the marker is a WRAPPER around a variant: the variant
    is everything before the marker, minus a trailing parenthetical (the
    benchmark's "(including the last day)" qualifier) and edge punctuation.
    A sentence without the marker is returned with edge punctuation stripped.
    """
    sentence = sentence.strip()
    marker_at = sentence.lower().find(GOLD_VARIANT_MARKER)
    if marker_at < 0:
        return sentence.strip(_EDGE_PUNCTUATION).strip()
    head = sentence[:marker_at]
    head = re.sub(r"\([^()]*\)\s*$", "", head).strip()
    return head.strip(_EDGE_PUNCTUATION).strip()


def gold_variants(answer: Any) -> list[str]:
    """The acceptable readings of a (possibly multi-variant) gold answer.

    Splits ONLY on the benchmark's own ``"is also acceptable"`` convention:
    when the gold carries no such marker it is a SINGLE variant, even if it
    spans several sentences. That restriction is deliberate — splitting every
    multi-sentence gold would let any incidental clause match a hypothesis and
    inflate the ceiling this module exists to bound (measured: on the #2578
    rows, unconditional sentence splitting and marker-only splitting both give
    42/52, so the stricter rule costs nothing and cannot inflate).

    Non-string golds are coerced (the benchmark ships integer golds, e.g. the
    temporal-reasoning ``"4"``), matching ``judge._normalize_answer_text``.
    """
    text = str(answer) if answer is not None else ""
    text = text.strip()
    if not text:
        return []
    if GOLD_VARIANT_MARKER not in text.lower():
        return [_strip_acceptable_wrapper(text)]
    variants = [v for v in (_strip_acceptable_wrapper(s)
                            for s in _split_sentences(text)) if v]
    return variants or [text.strip(_EDGE_PUNCTUATION).strip()]


def grade_variant_aware(answer: Any, hypothesis: str) -> bool:
    """Correct iff ANY gold variant is contained in the hypothesis.

    Uses the strict containment rule (``judge.grade_label``: normalized gold
    contained in normalized hypothesis) applied per variant. An empty
    hypothesis is never correct, and an empty gold is never a free pass.
    """
    hyp = _normalize_answer_text(hypothesis)
    if not hyp:
        return False
    return any(v and grade_label(v, hypothesis) for v in gold_variants(answer))


def grade_plain(answer: Any, hypothesis: str) -> bool:
    """The single-string containment FLOOR (the pre-variant figure)."""
    return grade_label(answer, hypothesis)


# ── scoring the committed rows (offline, no reader) ──────────────────────────


def _gold_map_from_instances(instances: list[dict]) -> dict[str, Any]:
    return {str(q.get("question_id")): q.get("answer") for q in instances}


def _gold_map_from_fixture(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    gold = payload.get("gold") if isinstance(payload, dict) else None
    if not isinstance(gold, dict) or not gold:
        raise ValueError(
            f"{path}: expected a JSON object with a non-empty 'gold' map "
            f"(qid -> gold answer)")
    return gold


def score_rows(rows: list[dict], gold_by_qid: dict[str, Any]) -> dict[str, Any]:
    """Score committed reader rows against the gold (pure, deterministic).

    ``rows`` carry the eval lane's per-question record; the fields used are
    ``qid``, ``cls``, ``answerable``, ``hypothesis``, ``grade`` (the
    committed grader's own verdict), ``reader_refusal`` and
    ``oracle_context_tokens``. Rows whose ``qid`` is absent from the gold map
    are reported as ``missing_gold`` — never silently scored as wrong (a
    missing gold answer is a fixture/join defect, not a reader failure).
    """
    answerable = plain = variant = refusals = 0
    missing_gold: list[str] = []
    grade_agreement: list[str] = []
    grade_disagreement: list[str] = []
    by_class: dict[str, dict[str, int]] = {}
    tokens: list[int] = []

    for row in rows:
        qid = str(row.get("qid"))
        if qid not in gold_by_qid:
            missing_gold.append(qid)
            continue
        gold = gold_by_qid[qid]
        hypothesis = row.get("hypothesis") or ""
        plain_ok = grade_plain(gold, hypothesis)
        # the committed grade column is the PLAIN rule: CORRECT <=> true
        committed = str(row.get("grade") or "").strip().upper()
        if committed in {"CORRECT", "NEAR_MISS", "WRONG"}:
            (grade_agreement if (committed == "CORRECT") == plain_ok
             else grade_disagreement).append(qid)
        if not row.get("answerable"):
            continue
        answerable += 1
        plain += int(plain_ok)
        variant += int(grade_variant_aware(gold, hypothesis))
        refusals += int(bool(row.get("reader_refusal")))
        cls = str(row.get("cls") or "unknown")
        bucket = by_class.setdefault(cls, {"n": 0, "plain": 0,
                                           "variant_aware": 0})
        bucket["n"] += 1
        bucket["plain"] += int(plain_ok)
        bucket["variant_aware"] += int(grade_variant_aware(gold, hypothesis))
        if isinstance(row.get("oracle_context_tokens"), (int, float)):
            tokens.append(int(row["oracle_context_tokens"]))

    def _rate(n: int) -> float | None:
        return round(n / answerable, 4) if answerable else None

    return {
        "n_rows": len(rows),
        "answerable": answerable,
        "plain_containment": {"correct": plain, "rate": _rate(plain)},
        "variant_aware": {"correct": variant, "rate": _rate(variant)},
        "reader_refusals": {"n": refusals, "rate": _rate(refusals)},
        "by_class": dict(sorted(by_class.items())),
        "oracle_context_tokens": ({
            "median": median(tokens), "mean": round(mean(tokens), 1),
            "min": min(tokens), "max": max(tokens), "n": len(tokens),
        } if tokens else None),
        "grade_agreement": {
            "agree": len(grade_agreement),
            "disagree": sorted(grade_disagreement),
            "basis": (
                "the row's `grade` column vs plain containment — on a "
                "committed #2578 row that column IS the plain rule, so a "
                "disagreement is a receipt defect; on a run-mode row it is "
                "the judge, so a disagreement is the judge-vs-containment "
                "gap #2976 quantifies"),
        },
        "missing_gold": sorted(missing_gold),
        "grader": (
            "variant-aware containment over the benchmark's own "
            f"'{GOLD_VARIANT_MARKER}' gold form; plain containment is the "
            "FLOOR; the official LLM judge is unmeasured (see module "
            "docstring)"),
    }


def load_rows(path: str | Path) -> list[dict]:
    """Read a per-question JSONL receipt."""
    rows: list[dict] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


# ── the reader half (gold sessions → reader → rows) ──────────────────────────


def run_cell(
    instances: list[dict],
    *,
    reader,
    judge,
    checkpoint: str | None = None,
    max_retries: int = 3,
    split: str = ds.DEFAULT_SPLIT,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run the oracle ceiling over instances (reader + judge, no retrieval).

    Delegates the per-question loop, retry/backoff, checkpoint/resume and
    report assembly to ``full_context.run_cell`` — the reader plumbing is
    already pinned there — and injects the gold-session hit builder through
    its ``hits_for`` seam. Only the methodology/cell labels are replaced, so
    an oracle report can never be misread as a full-context or
    retrieval-backed run.
    """
    from .full_context import run_cell as _full_context_run_cell

    outcomes, report = _full_context_run_cell(
        instances, reader=reader, judge=judge, checkpoint=checkpoint,
        max_retries=max_retries, split=split, hits_for=gold_session_hits,
        cell=CELL, extraction_approach=CELL_EXTRACTION_APPROACH,
    )
    report["methodology"]["retrieval"] = (
        "NONE — oracle ceiling: the reader received ONLY the gold answer "
        "sessions verbatim (answer_session_ids), no graph, no retrieval")
    report["methodology"]["retrieval_scope"] = (
        "oracle ceiling: the reader received ONLY the gold answer sessions "
        "verbatim — an UPPER BOUND by construction, since real retrieval "
        "never serves them verbatim")
    report["methodology"]["recall_definition"] = (
        "trivially 1.0 — the gold sessions are the context; there is no "
        "top-k to miss")
    report["grader"] = (
        "the run grades with the configured judge; re-derive the "
        "variant-aware figure from the rows with `oracle_ceiling score`")
    by_qid = {str(q.get("question_id")): q for q in instances}
    report["oracle_ceiling"] = score_rows(
        [_row_from_outcome(o, by_qid[o["question_id"]]) for o in outcomes
         if o["question_id"] in by_qid],
        _gold_map_from_instances(instances))
    return outcomes, report


def _row_from_outcome(outcome: dict[str, Any], question: dict) -> dict:
    """Project a runner outcome onto the committed receipt schema.

    ``grade`` is the JUDGE's verdict (the run mode's grader) and
    ``plain_grade`` is the deterministic containment rule, recorded alongside
    so a run-mode receipt shows the judge-vs-containment gap instead of
    hiding which grader produced the label.
    """
    hypothesis = outcome.get("hypothesis", "")
    return {
        "qid": outcome["question_id"],
        "cls": outcome.get("question_type", ""),
        "answerable": bool(question.get("answer_session_ids")),
        "label": bool(outcome.get("label")),
        "grade": "CORRECT" if outcome.get("label") else "WRONG",
        "plain_grade": (
            "CORRECT" if grade_plain(question.get("answer", ""), hypothesis)
            else "WRONG"),
        "reader_refusal": not str(hypothesis).strip(),
        "hypothesis": hypothesis,
        "oracle_context_tokens": outcome.get("context_tokens"),
        "seconds": round((outcome.get("total_ms") or 0) / 1000.0, 2),
    }


# ── CLI ─────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m tools.longmem_eval.oracle_ceiling",
        description="Oracle-ceiling harness (#2976): score the gold-session "
                    "ceiling from committed rows, or run it against a reader.")
    p.add_argument("mode", choices=("score", "run"), nargs="?", default="score",
                   help="'score' re-derives the ceiling from committed rows "
                        "(offline); 'run' serves the gold sessions to a reader")
    p.add_argument("--rows", default="docs/runbook/2578-oracle-ceiling.jsonl",
                   help="committed per-question JSONL to score (score mode)")
    p.add_argument("--gold", default=None,
                   help="gold-map JSON fixture (qid -> gold answer); default "
                        "resolves it from the dataset split")
    p.add_argument("--split", default="oracle", choices=sorted(ds.SPLIT_FILES),
                   help="dataset split to resolve gold from (score mode)")
    p.add_argument("--data", default=None,
                   help="local dataset JSON/JSONL path (skips download)")
    p.add_argument("--cache-dir", default=None)
    p.add_argument("--no-download", action="store_true")
    p.add_argument("--limit", type=int, default=None,
                   help="run only the first N questions (run mode)")
    p.add_argument("--mock", action="store_true",
                   help="offline MockReader + MockJudge (wiring check, no keys)")
    p.add_argument("--checkpoint", default=None)
    p.add_argument("--reader-model", default=None)
    p.add_argument("--judge-model", default=None)
    p.add_argument("--output", default=None,
                   help="write the JSON result here (default: stdout only)")
    return p


def _print_score(result: dict[str, Any]) -> None:
    print("=" * 64)
    print(f"Oracle ceiling — {result['answerable']} answerable questions")
    print(f"  variant-aware   {result['variant_aware']['correct']}"
          f"/{result['answerable']}  ({result['variant_aware']['rate']})")
    print(f"  plain (floor)   {result['plain_containment']['correct']}"
          f"/{result['answerable']}  ({result['plain_containment']['rate']})")
    print(f"  reader refusals {result['reader_refusals']['n']}"
          f"/{result['answerable']}  ({result['reader_refusals']['rate']})")
    for cls, v in result["by_class"].items():
        print(f"  {cls:<24} n={v['n']:<3} plain={v['plain']:<3} "
              f"variant-aware={v['variant_aware']}")
    tok = result.get("oracle_context_tokens")
    if tok:
        print(f"  oracle context tokens: median {tok['median']} · "
              f"mean {tok['mean']} · min {tok['min']} · max {tok['max']}")
    agree = result["grade_agreement"]
    print(f"  committed grade column agrees with the plain rule on "
          f"{agree['agree']}/{result['n_rows']} rows"
          + (f" — DISAGREE: {agree['disagree']}" if agree["disagree"] else ""))
    if result["missing_gold"]:
        print(f"  ⚠️ missing gold for {len(result['missing_gold'])} qids: "
              f"{result['missing_gold']}")
    print(f"  grader: {result['grader']}")
    print("=" * 64)


def run_main(argv: list[str] | None = None) -> dict[str, Any]:
    args = _build_parser().parse_args(argv)

    if args.mode == "score":
        if args.gold:
            gold = _gold_map_from_fixture(args.gold)
        else:
            instances = ds.load_dataset(
                args.split, data_path=args.data,
                cache=(Path(args.cache_dir).expanduser()
                       if args.cache_dir else None),
                download=not args.no_download)
            gold = _gold_map_from_instances(instances)
        result = score_rows(load_rows(args.rows), gold)
        result["source"] = {
            "rows": str(args.rows),
            "gold": (str(args.gold) if args.gold
                     else f"dataset split {args.split!r}"),
        }
        _print_score(result)
        if args.output:
            Path(args.output).write_text(
                json.dumps(result, indent=2) + "\n", encoding="utf-8")
            print(f"result saved to: {args.output}")
        return result

    instances = ds.load_dataset(
        args.split, limit=args.limit, data_path=args.data,
        cache=Path(args.cache_dir).expanduser() if args.cache_dir else None,
        download=not args.no_download)
    instances, abstention = ceiling_instances(instances)
    if abstention:
        print(f"[oracle_ceiling] excluded {len(abstention)} abstention "
              f"question(s) — no gold session to serve: {abstention}",
              file=sys.stderr)
    if not instances:
        raise SystemExit(
            "[oracle_ceiling] no answerable questions in this selection — "
            "there is no ceiling to measure")
    reader = build_reader(args.reader_model, mock=args.mock)
    judge = build_judge(args.judge_model, mock=args.mock)
    outcomes, report = run_cell(
        instances, reader=reader, judge=judge,
        checkpoint=args.checkpoint, split=args.split)
    if args.output:
        out = args.output
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")  # noqa: UP017
        out = str(Path.cwd() / f"oracle_ceiling_{stamp}.report.json")
    save_report(report, out)
    _print_score(report["oracle_ceiling"])
    print(f"report saved to: {out}")
    return report


if __name__ == "__main__":
    run_main()

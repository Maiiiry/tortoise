---
title: "Ask lane — sealed distilled + planted-supersession measurement (ask-distilled-supersession)"
type: benchmarks
issue: "#9097"
epic: "#2281"
date: 2026-10-19
doc_status: live
ownedBy: epistemic-team
aboutObjects: tortoise-ask-lane
preregistration: docs/benchmarks/ask-distilled-supersession/preregistration.json
---

# Ask lane — sealed distilled + planted-supersession measurement

> **Epic:** #2281 (D4 → extend W2-b; D7 measurement; D8 product gate). **Issue:** #9097. **Pre-registration:** `docs/benchmarks/ask-distilled-supersession/preregistration.json` (corpus, split, scoring and decision rule fixed BEFORE the run; the run commit is a descendant of the pre-registration commit, checked by `ask_sealed.verify_preregistration_history`).
>
> **RUN STATUS (this commit): PENDING — the sealed run was NOT executed in this commit.** The corpus, the sealed gold, the pre-registration, the runner, the gate and the receipt path are committed and tested; the read+judge execution needs live provider keys (reader + the official judge) and is a separately scheduled operation. **No metric value is claimed here.** Filling the tables is an annotated update (erratum discipline), never a silent edit.

## 1. Run identity

| Field | Value |
|---|---|
| Benchmark | `ask-distilled-supersession` — sealed distilled + planted-supersession ask measurement |
| Corpus | `tests/eval/ask` — 6 cases (3 knowledge-update + 3 verbatim control) |
| Corpus hash (`fixtures_hash`, fixtures **and** sealed gold) | `sha256:7e99ae35bfd4e09fe8866318e1a655cca1787a85b7e95abf743bffb5bba90ad2` |
| Reader | `deepseek/deepseek-v4-flash` (ask lane default; one identity across both arms) |
| Judge | `longmemeval-official-anscheck-gpt4o-2024-08-06-v1` (official LongMemEval anscheck, rubric `82d07b0de05daa48`) |
| Arms | `raw` (turn store) · `distilled` (turn store + distilled claims + planted supersession), BOTH in one invocation (D4) |
| Pre-registration commit | PENDING |
| Run commit | PENDING |
| Verdict | PENDING |
| Receipt validation | PENDING |

## 2. The numbers

### 2.1 D7 — knowledge-update current-state rate (distilled arm)

**No pass threshold by design** — epic D8: *the first run sets it*. The paired raw arm is reported alongside because both arms run the same cases in the same invocation.

| Metric | Value | n | Semantics |
|---|---|---|---|
| `current_state` (distilled) | PENDING | — | judge: does the answer commit the CURRENT state (no threshold — first run sets it) |
| `current_state` (raw, paired) | PENDING | — | same cases, turn store only |

### 2.2 D8 — superseded claims rendered live (product gate)

| Metric | Value | n | Semantics |
|---|---|---|---|
| `superseded_live` | PENDING | — | '0% of superseded points appear as live answers' (`docs/product-success-eval.md:132`) |
| `observed` | PENDING | — | a claim the instrument could not observe is UNMEASURED, never a pass — the gate fails closed |

### 2.3 Verbatim/raw no-regression (paired, per case)

| Arm | Committed | n | Regressions |
|---|---|---|---|
| `raw` | PENDING | — | — |
| `distilled` | PENDING | — | PENDING |

The exit bar is conjunctive: a knowledge-update win with a verbatim loss is a **FAIL**, not a trade.

## 3. Reproducing the run

```bash
tools/run-with-eval-keys.sh .venv/bin/python -m tools.longmem_eval.ask_sealed run --prereg docs/benchmarks/ask-distilled-supersession/preregistration.json --commit $(git rev-parse HEAD) --run-out <run.json> --report-out docs/benchmarks/ask-distilled-supersession/run-report.md
```

The sealed gold (`tests/eval/ask/gold/`) is read ONLY by the scorer, after the read path finishes: `get_sealed_run` opens fixtures only (asserted in `tests/test_ask_sealed_benchmark.py`, which makes the gold loader raise for the duration of a run).

## 4. Falsification (the gate can say NO)

- **drop the planted supersession (DELETE the CORRECTS edge, reopen the window)** → d8_superseded_live fails (rendered_live > 0) ⇒ verdict regression — the gate can say NO
- **make the distilled arm lose a verbatim case** → verbatim_no_regression fails ⇒ verdict regression, even when the KU number improves
- **edit any fixture or gold byte** → fixtures_hash no longer matches the pre-registration ⇒ verdict inconclusive (hash_mismatch)

## 5. What this measures — and what it does not

- **Measures:** whether the distilled + planted-supersession read path (a) tells the reader the current state, (b) never shows a superseded claim as live, and (c) does not cost verbatim recall.
- **Does NOT measure** a population: n is small by design (3 planted pairs). Rates are first-run records carrying no precision claim.
- **Does NOT set a threshold** on the knowledge-update number (epic D8). The only bars are the stated product promise (0% live) and the paired verbatim comparison.
- **Not a marketing number** — a defensible one, same discipline as the existing LongMemEval publications.

## 6. Errata discipline

Filling the PENDING tables is a separate commit. The pre-registration file is immutable: changing the corpus, split, scoring or decision rule requires a NEW pre-registration (+ a new corpus hash), never an edit — a change after the numbers are known is what the history check exists to make impossible.

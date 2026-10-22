---
title: "`how-to-use-tortoise` — Step 1 write-pipeline eval (the skill-less baseline)"
type: evidence
domain: epistemic
doc_status: live
created: 2026-10-22
issue: 9675
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise, how-to-use-tortoise
---

# Step 1 — the skill-less write-pipeline eval (#9675)

> **Status: instrument + frozen gold landed; runs in progress.** The per-input
> results table below is filled from the committed run records in
> `eval/how-to-use-tortoise/step1/runs/`. Nothing here is a review verdict — the
> measurement is the artifact.

## Why this eval exists

Issue #9675 replaces a job-by-job framing of `how-to-use-tortoise` with **one
operation**: the write. *"If events are classified as objects and subjects as
points, the graph doesn't work. If mitigations are not understood as relevance
mitigations, and hence not used, then there's no proper logic used and the
outputs of the graph's calculation are wrong."*

The method the issue prescribes is the opposite of how the document was built.
Anthropic's authoring guidance is explicit that documentation-first means
*"documenting imagined ones"*: run the agent without the skill, record the real
failures, then write. We never did that for this document.

**Step 1 is the only scope #9675 releases today.** Steps 2–3 (the rewrite) are
gated behind #4282 Phase 3, because the rewrite must cite the 27-tool surface's
names, and writing against the 81 would document names about to be retired.

## What is frozen, and when

`eval/how-to-use-tortoise/step1/inputs.json` holds ten representative inputs and
their **gold structure**, frozen **before** any run. The gold is derived from
`docs/ONTOLOGY.md` — the definitions owner (§4.1 Point, §4.2 Subject, §4.3
Object, §4.5 Event, §4.6 Source, §5 state-centric vocabulary + the #7813 owner
ruling on the point/object/event overlap). This document deliberately does **not**
restate those definitions; two ontologies that drift is the failure class the
issue names.

Each input names the **adjacent layer pair it is designed to confuse** — the
issue's own requirement: *"pairwise discriminator tests … because the failures
are confusions between adjacent layers — a per-layer positive test does not catch
the agent that saw the right word and picked the neighbouring layer."*

| # | Input cell | Confusion under test |
|---|---|---|
| 1 | portfolio update | Object-vs-Event, Event-vs-Object |
| 2 | meeting transcript | Point-vs-Event, Event-vs-Object |
| 3 | research paper | Subject-vs-Point, Object-vs-Point |
| 4 | decision request | Object-vs-Point, Event-vs-Object |
| 5 | correction to an existing claim | Point-vs-Event, truth-vs-relevance |
| 6 | duplicate entity | Object-vs-Point (identity) |
| 7 | regulatory filing | Source-vs-Object, Point-vs-Source |
| 8 | person appointment | Subject-vs-Point, Subject-vs-Event |
| 9 | contradicting claim | truth-vs-relevance (NAND-vs-mitigation) |
| 10 | product spec / commitment | Object-vs-Point, state-vs-occurrence |

## Method

**The condition is "the tools and no skill".** Each input is handed to an agent
that (a) has the write surface, (b) is given the input text and a one-line
instruction to record it, and (c) is **not** given the skill, this document, or
`docs/ONTOLOGY.md`. Inputs 5, 6, 9 and 10 are *incremental* — a pre-built graph
holds the pre-existing claim/entity they act on, because the interesting failures
are the ones against existing knowledge (supersede vs edit; duplicate vs update;
NAND vs mitigate).

**The record is read off the graph, never off the agent's summary.**
`tools/ax_write_eval.py record` opens the graph the agent wrote and records every
node's **label** (the layer), its kind property, the operator points and
`op_type`, every edge, and the Sources. `tools/ax_write_eval.py score` compares
that record against the frozen gold and emits P0/P1/P2 findings with the
severity rules the review skills use:

| Severity | Rule |
|---|---|
| **P0** | layer confusion (an item filed on a different label than its gold layer) · item absent/absorbed · no Source at all · a contradicting claim filed with no operator · relevance dampening where truth is at issue · change written without supersession |
| **P1** | kind outside the frozen set · duplicate nodes for one entity · Source nodes with zero claim→source edges · a node with no kind property |

The matcher is deliberately dumb (token overlap). A fuzzy matcher would *hide*
the exact failure being measured: a `Northwind Logistics Ltd` that became a
SECOND node must read as a duplicate, not be silently unified with the first.

Reproduce:

```bash
# one input, standalone graph
uv run python tools/ax_write_eval.py score \
    --db /tmp/ax-eval/portfolio_update.db \
    --gold eval/how-to-use-tortoise/step1/inputs.json \
    --id portfolio_update
```

## Results

*(filled from `eval/how-to-use-tortoise/step1/runs/*.json`; pending)*

## The failure list = the document's outline

*(pending — one outline section per failure class that actually occurred)*

## What remains

- **Steps 2–3** (the rewrite itself) stay gated behind **#4282 Phase 3**
  (live 81 → 27). Re-running this exact eval — same inputs, same gold — is
  Step 3's acceptance criterion: *the delta per input, not a review verdict*.
- The served mirror is a **derived** artifact; the rewrite is authored upstream
  and pulled with `uv run python tools/served_skills_parity.py --sync`
  (absorbed issue #4358), never edited by hand here.

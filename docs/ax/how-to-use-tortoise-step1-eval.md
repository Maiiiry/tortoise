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

> **Status: Step 1 complete — instrument, frozen gold, ten runs and the derived
> failure list all landed.** Every number is read from the committed run records
> in `eval/how-to-use-tortoise/step1/runs/`, with each agent's own `write.py`
> beside it. Nothing here is a review verdict — the measurement is the artifact.
> **Steps 2–3 (the rewrite) remain gated behind #4282 Phase 3.**

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

Reproduce (one input, or all ten):

```bash
# one input, standalone graph
uv run python tools/ax_write_eval.py score \
    --db /tmp/ax-eval/portfolio_update.db \
    --gold eval/how-to-use-tortoise/step1/inputs.json \
    --id portfolio_update

# the four incremental inputs need their before-graph first
uv run python eval/how-to-use-tortoise/step1/setup.py \
    --id contradicting_claim --db /tmp/ax-eval/contradicting_claim.db --fresh
```

The instrument's scoring rules are pinned by
`tests/test_ax_write_eval.py` (12 tests, no database needed):

```bash
TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_ax_write_eval.py -q
# 12 passed
```

## Results — measured 2026-10-22, ten runs

Every number below is read off the graph each agent wrote
(`eval/how-to-use-tortoise/step1/runs/<input>/record.json`), scored against the
frozen gold. The agent's own `write.py` is committed beside each record.

| # | Input cell | P0 | P1 | Layers actually written | The failure, in one line |
|---|---|---|---|---|---|
| 1 | portfolio update | 2 | 4 | 5 Point · 3 Subject · 2 Object · 3 Event · 1 Source | ARR/headcount (state) written as **Points**, not on the Object; `subjectKind: company\|investor` undeclared |
| 2 | meeting transcript | 3 | 4 | 7 Point · 3 Subject · 4 Object · 1 Event · 1 Source | the decision exists **twice** — as the meeting Event and as a decision Point; people get `subjectKind: person` |
| 3 | research paper | 1 | 4 | 6 Point · 1 Subject · 3 Object · 1 Event · 1 Source | the paper's `sourceKind` is `research` — that is the **documentKind (genre) axis**, not `sourceKind`; `authorGroup` undeclared |
| 4 | decision request | 2 | 2 | 7 Point · 1 Subject · 4 Object · 2 Event · 2 Source | the goal and its deadline live as **Points** while an Object exists; cost estimate as a Point |
| 5 | correction to a claim | 0 | 0 | 6 Point · 1 Event · 1 Source | **clean** — supersede with the old claim kept, provenance edge written |
| 6 | duplicate entity | 0 | 2 | 3 Point · 1 Subject · 1 Object · 1 Event · 1 Source | **no duplicate node** (the alias was recorded as a claim); `subjectKind: person` |
| 7 | regulatory filing | 0 | 3 | 5 Point · 2 Subject · 1 Object · 1 Event · 1 Source | `subjectKind: core:organization` — an invented **namespace prefix**; `sourceKind: document` correct |
| 8 | person appointment | 2 | 4 | 4 Point · 2 Subject · 5 Object · 1 Event · 1 Source | the organisation and the role were written as **Objects**; appointment as Point beside the Event |
| 9 | contradicting claim | 3 | 1 | 7 Point · 1 Event | **no NAND** — the contradiction was handled by *mitigating the IMPL operator* (relevance dampened where truth was at issue), and no Source was written |
| 10 | product spec | 1 | 1 | 4 Point · 1 Subject · 2 Object · 1 Event · 1 Source | v1 **was** superseded (correct), but v1/v2 ship dates and success bars are **Points** while the plan Objects carry none |

**Aggregate: 14 P0, 25 P1 across 10 inputs; 7 of 10 inputs contain at least
one P0.** One input (the correction) is clean end-to-end — the document must
keep whatever produced that, because it is the one thing an agent got right
unaided.

### Failure classes, ranked by how many inputs they touch

| Class | Count | Evidence (from the run records) |
|---|---|---|
| **Undeclared kind** | 17 nodes / 7 inputs | `subjectKind` ∈ {`person` ×6, `company` ×2, `investor`} · `objectKind` ∈ {`offering`, `tranche`, `deliverable`, `productSurface`, `openQuestion`, `role`, `organization`} · `pointKind` ∈ {`issue`, `estimate`, `evidence`} · `eventKind` ∈ {`employmentStart`, `customerCommitment`, `disbursement`, `planRevision`} · `sourceKind` ∈ {`research`, `spec`, `contract`, `note`} · and one **namespace-prefixed** `core:organization`. The write path accepted every one of them (warn-not-block, #1934) |
| **Commitment-state on the logic layer** — Object exists *and* the state is also a Point | 4 inputs (portfolio, decision request, product spec, meeting) | the same fact has two homes, so the Object can never be the answer to “what is X now”: ARR, headcount, the goal + its deadline, and the plan's dates all landed as Points |
| **Layer confusion, entity/occurrence side** | 5 nodes / 3 inputs | an organisation and a role written as **Objects** (person appointment); the decision written as a **Point** (meeting) |
| **Truth vs relevance — no NAND** | 1 input, 2 findings | the contradicting claim was filed and the **IMPL operator mitigated**; nothing attacked the Point. This is exactly the failure the issue's premise names: *“if mitigations are not understood as relevance mitigations … the outputs of the graph's calculation are wrong”* |
| **Provenance** | 2 findings / 2 inputs | one run wrote no Source at all; another wrote a Source with **zero** claim→source edges. In the runs that did get provenance right, the working route was `create_point(extractedFrom=[url])` — a **property**, not an edge verb |
| **Duplicate / double representation** | 1 input | the decision recorded as both the meeting Event and a decision Point |

### Instrument limitations, stated rather than hidden

- The scorer matches by token overlap. Four “item missing” verdicts were
  **human-verified as present and mis-layered** (meeting: the decision Point;
  the 80% forecast; the EU question; product spec: v1's dates). The class table
  above uses the human-verified reading, and the raw machine verdicts stay in
  the records untouched.
- `aboutPoint` cannot be written by `create_edge` (refused: unknown predicate,
  though the about\* family is declared valid and `create_about_edge` accepts
  it — and `TortoiseSDK` has no `create_about_edge` at all), and
  `create_event(aboutPoint=[id])` — the **list** form — creates **no edge and no
  error** (verified: `[]`; the string form writes it). A silent write failure in
  the surface this document teaches is a finding in its own right.

## The failure list = the document's outline

Derived from the classes above, in the order an agent has to decide.

1. **Layer first, and it is a decision about the ITEM, not the sentence.** The
   dominant failure is not “chose the wrong word” — it is filing a *state*
   (ARR, headcount, a goal, a plan's dates) on the logic layer **while also**
   creating the Object, so the state has two homes and neither answers “what is
   true now”. The discriminator the document must teach: *does this hold now
   (Object), did it happen at a time (Event), is it a belief we hold (Point), is
   it an actor (Subject), is it something we read it from (Source)?*
2. **An occurrence is not a belief.** The decision in the transcript became a
   Point beside the meeting Event; the appointment became a Point beside the
   Event. Rule: if it has a date on which it happened, it is an Event with
   `eventKind` — the belief layer records what we *conclude*, not what *occurred*.
3. **A person, an organisation and a role are Subjects; a role is not an
   Object.** Two runs wrote the employer/role as Objects. Rule: `Subject` for
   anything that can act or be acted upon, `Object` only for the thing whose
   state changes.
4. **Kinds come from the declared vocabulary — and the two axes are not one.**
   The paper got `sourceKind: research`, which is the *genre* axis
   (`documentKind`); one run invented `core:organization`. Rule: pick the kind
   the layer's vocabulary declares (§5 + pack `kindDefs`), and never confuse
   `sourceKind` (what kind of source) with `documentKind` (what genre it is).
5. **Truth attacks the Point; relevance dampens the operator.** The one
   contradicting input was handled by mitigating an `IMPL`. Rule: a claim that is
   *wrong* gets a `NAND`; a claim that is *less relevant* gets
   `mitigate_operator` (0.10–0.50). Writing a mitigation where a NAND belongs
   leaves the wrong confidence computed, not missing.
6. **Change is supersession with the old claim kept.** This is the one class the
   agents already got right unaided (both supersession inputs) — the document
   should not spend lines re-teaching it, but must keep the *why* (the
   before-state stays answerable).
7. **Provenance is a property lane, not an edge verb** (`extractedFrom=[url]`
   on the claim). The successful runs found it; the failing ones wrote a Source
   and attached nothing. Rule: every claim written from something we read gets
   the source at write time, and a Source with no claim→source edge is a
   scrapbook entry.
8. **The silent-failure warning.** Every failure above succeeded — the call
   returned, the node was created, and nothing disclosed the error. That is the
   sentence the document opens with, and it is the reason the checklist exists.

## What remains

- **Steps 2–3** (the rewrite itself) stay gated behind **#4282 Phase 3**
  (live 81 → 27). Re-running this exact eval — same inputs, same gold — is
  Step 3's acceptance criterion: *the delta per input, not a review verdict*.
- The served mirror is a **derived** artifact; the rewrite is authored upstream
  and pulled with `uv run python tools/served_skills_parity.py --sync`
  (absorbed issue #4358), never edited by hand here.

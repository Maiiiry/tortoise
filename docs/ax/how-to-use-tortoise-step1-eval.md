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
`tests/test_ax_write_eval.py` (21 tests, no database needed):

```bash
TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_ax_write_eval.py -q
# 21 passed
```

## Results — measured 2026-10-22, ten runs

Every number below is read off the graph each agent wrote. Each run directory
holds **both** halves: `graph.json` (the structural record — every node's
label, kind and id, every edge) and `score.json` (the verdicts read from it),
beside the agent's own `write.py`. A verdict without its evidence cannot be
audited, which is how a first draft of this table carried two numbers that did
not match their own records.

| # | Input cell | P0 | P1 | Layers actually written | The failure, in one line |
|---|---|---|---|---|---|
| 1 | portfolio update | 2 | 5 | 5 Point · 3 Subject · 2 Object · 3 Event · 1 Source | ARR and headcount (state) written as **Points**; `subjectKind: company\|investor` undeclared; `eventKind: disbursement` is a declared *pack* kind but not the kind the release is |
| 2 | meeting transcript | 2 | 7 | 7 Point · 3 Subject · 4 Object · 1 Event · 1 Source | the decision is a **Point** beside the meeting Event (so the occurrence has no Event of its kind); `subjectKind: person` ×3; `objectKind: offering`; `pointKind: issue` for the open EU question; an **unexplained NAND** |
| 3 | research paper | 0 | 4 | 6 Point · 1 Subject · 3 Object · 1 Event · 1 Source | `sourceKind: research` — that is the **documentKind (genre) axis**; `subjectKind: authorGroup` undeclared; the reported findings use `pointKind: observation` (a canonical legacy kind) instead of `statement` |
| 4 | decision request | 4 | 1 | 7 Point · 1 Subject · 4 Object · 2 Event · 2 Source | the **request** is a Point (not an Event), the **goal** is a Point (not an Object), the **deadline** is an Event, the **estimate** is a Point — four layers decisions in one input |
| 5 | correction to a claim | 0 | 1 | 6 Point · 1 Event · 1 Source | both gold items landed — supersession with the old claim kept, provenance written — but the run minted undeclared kinds elsewhere (`pointKind: incident`, `eventKind: correction`). **No run is clean graph-wide** |
| 6 | duplicate entity | 1 | 2 | 3 Point · 1 Subject · 1 Object · 1 Event · 1 Source | the **alias was correctly folded onto the existing node** (no duplicate — the measurable the input exists for); the confirmed renewal is a Point, not the occurrence |
| 7 | regulatory filing | 0 | 2 | 5 Point · 2 Subject · 1 Object · 1 Event · 1 Source | no layer failure at all: Source + subject kinds correct (`core:organization` is the **declared** spelling), the two figures use the legacy `observation` kind rather than `statement` |
| 8 | person appointment | 3 | 3 | 4 Point · 2 Subject · 5 Object · 1 Event · 1 Source | the **organisation and the role** were written as Objects; the appointment itself as a Point beside the Event; `subjectKind: person` ×2 |
| 9 | contradicting claim | 3 | 2 | 7 Point · 1 Event | **no NAND** — the contradiction was handled by *mitigating the IMPL operator*: relevance dampened where truth was at issue. No Source written at all |
| 10 | product spec | 3 | 1 | 4 Point · 1 Subject · 2 Object · 1 Event · 1 Source | v1 **was** superseded (correct), but the ship date and the success bar are **Points** while the plan Objects carry neither |

**Aggregate: 18 P0 and 28 P1 across the ten inputs; 7 of 10 inputs carry at
least one P0, and no input is clean graph-wide.** The nearest miss is the
correction (input 5): both gold items land perfectly and it still wrote two
undeclared kinds.

### Failure classes (each count is read from the ten `score.json`s)

| Class | Count | What it looked like |
|---|---|---|
| **Undeclared kind** (a kind neither the core §5 vocabulary nor a loaded pack declares) | 14 scored items across 6 inputs | `subjectKind` ∈ {`person` ×6, `company` ×2, `investor`, `authorGroup`} · `objectKind: offering` · `sourceKind: research`. Every one accepted by the write path (warn-not-block, #1934). **Not** counted as failures: `core:organization` (the declared key), `disbursement`/`tranche`/`estimate` (venture and pm packs declare them) — a first draft of this table called those failures and was wrong |
| **Uncatalogued kinds beyond the gold** (the graph-wide census) | 8 inputs | the gold only names some items; the census asks the whole graph. It is what stops a run reading "clean" while writing `pointKind: incident`, `eventKind: correction`, `objectKind: openQuestion`, `eventKind: employmentStart`, … |
| **Layer confusion** | 12 items across 5 inputs | portfolio: ARR + headcount as Points. decision request: **four** (request, goal, deadline, estimate). person appointment: **three** (organisation, role, appointment). duplicate entity: the renewal as a Point. product spec: ship date + success bar as Points |
| **Wrong-but-declared kind** | 5 | `pointKind: observation` where the state-centric ruling says `statement`; `eventKind: disbursement` where the release event is an occurrence/milestone. These are the *legacy-valid* kinds — a different lesson from an invented kind, which is why the instrument separates them |
| **Truth vs relevance** | 1 input, 2 findings | contradicting claim: no NAND **and** a mitigation applied. One root failure, two facets — counted as two findings, and stated as one failure here |
| **NAND where nothing contradicts** | 1 | meeting transcript: a NAND from the slip forecast onto the comms due date. A judgment call, and the instrument treats it as a finding because a contradiction operator on a claim nobody attacked computes a wrong confidence |
| **Provenance** | 1 | contradicting claim wrote no Source at all. Every other input wrote one; the route that worked is `create_point(extractedFrom=[url])` — a **property**, not an edge verb |
| **Duplicate identity** | 0 | the one input built to produce a duplicate produced none: the alias was folded onto the existing Object. This is the measurable the document must keep |

### Instrument limitations and corrections, stated rather than hidden

This section exists because a **fresh-context review of the first draft of this
PR found four ways the instrument was lying**, and the numbers above are what
remains after fixing them. The corrections are recorded because a measurement
artifact that hides its own revisions teaches the wrong thing:

1. **The same-layer preference had no floor.** It let a same-layer candidate
   scoring 0.08 beat a different-layer containment match at 0.83, so two
   committed findings accused a node the agent never wrote the item as, and two
   real layer failures disappeared underneath. Fixed with a 0.5 floor plus a
   margin rule; the layer count went from 6 to 12.
2. **Pack-declared kinds were reported as undeclared.** `_vocab()` imported only
   the core sets, so `disbursement`, `tranche`, `estimate` and `rationale` —
   declared by the venture/pm packs — read as failures. The instrument now loads
   the pack registry and reports **CORE / PACK / UNDECLARED**; the failure class
   exists only for the third.
3. **`core:organization` was called "an invented namespace prefix".** It is the
   declared key (`subject_binding.subject_kind_names()`), and the bare form is
   its alias. The subject vocabulary is now imported, and the input's one
   substantive P1 is gone as a false positive.
4. **`"clean end-to-end"` was true only of the two gold items.** `score()` audits
   the nodes the gold names, so the correction run read 0/0 while writing
   `pointKind: incident` and `eventKind: correction`. The graph-wide kind census
   now makes "clean" mean clean.
5. **`record.json` did not exist — the committed file was the score output.**
   The Method section describes graph evidence a reader could not audit.
   `score --out DIR` now writes `graph.json` (nodes, labels, kinds, edges, ids)
   beside `score.json`.
6. **`expect_mitigation: false` was boilerplate on nine inputs**, which turned
   the research input's *sanctioned* mitigation of an under-evidenced inference
   into a P0. Both operator rules are now three-valued: absent = not scored.
7. **`duplicate_expected` was declared in the gold and implemented nowhere** —
   the input's own stated measurable was unenforced until the review.
8. The matcher is still token overlap, and four "item missing" verdicts remain
   **human-verified as present and mis-layered** (meeting: the decision Point,
   the 80% forecast, the EU question; product spec: v1's dates). The raw machine
   verdicts stay in `score.json` untouched; the class table uses the
   human-verified reading.

Two tool-surface defects were hit by the eval agents and **filed as #9696**,
not worked around: `create_event(aboutPoint=[id])` — the **list** form — writes
no edge and raises nothing (the string form writes it), the `about*` props wire
nothing at all outside the Event create path, and `aboutPoint` is refused by
`create_edge` while `TortoiseSDK` exposes no `create_about_edge`. A silent write
failure in the surface this document teaches is a finding in its own right;
fixing it is not this issue's scope.

## The failure list = the document's outline

Derived from the classes above, in the order an agent has to decide.

1. **Layer first, and it is a decision about the ITEM, not the sentence.** The
   dominant failure is not "chose the wrong word" — it is filing a *state*
   (ARR, headcount, a goal, a plan's dates) on the logic layer **while also**
   creating the Object, so the state has two homes and neither answers "what is
   true now". Twelve of the nineteen P0s are this. The discriminator the
   document must teach: *does this hold now (Object), did it happen at a time
   (Event), is it a belief we hold (Point), is it an actor (Subject), is it
   something we read it from (Source)?*
2. **An occurrence is not a belief.** The decision in the transcript, the
   appointment in the note, and the "request" in the decision request all became
   **Points**. Rule: if it happened on a date, it is an Event with an
   `eventKind` — the belief layer records what we *conclude*, not what
   *occurred*.
3. **A person, an organisation and a role are Subjects — a role is not an
   Object.** One run wrote the employer and the role as Objects. Rule: `Subject`
   for anything that can act or be acted upon, `Object` only for the thing whose
   state changes.
4. **Kinds come from the declared vocabulary — and the two axes are not one.**
   The most common single failure (14 scored items) is an invented kind:
   `subjectKind: person`, `company`, `investor`, `authorGroup`. And the paper got
   `sourceKind: research`, which is the *genre* axis (`documentKind`). Rule:
   pick the kind the layer's vocabulary declares (§5 core, or a pack kind spelled
   the way the pack declares it), and never confuse `sourceKind` (what kind of
   source) with `documentKind` (what genre it is).
5. **Truth attacks the Point; relevance dampens the operator.** The one
   contradicting input was answered by mitigating an `IMPL`, and nothing attacked
   the Point. Rule: a claim that is *wrong* gets a `NAND`; a claim that is *less
   relevant* gets `mitigate_operator` (0.10–0.50). A mitigation where a NAND
   belongs leaves the wrong confidence computed, not missing.
6. **Change is supersession with the old claim kept.** The one class the agents
   already got right unaided (both supersession inputs, and the alias fold on the
   duplicate input) — the document should keep the *why* (the before-state stays
   answerable) and not spend lines re-teaching the mechanics.
7. **Provenance is a property lane, not an edge verb** (`extractedFrom=[url]`
   on the claim). Nine of ten runs found it; the one that wrote no Source at all
   is the input where the claim was being attacked. Rule: every claim written
   from something we read gets its source at write time, and a Source with no
   claim→source edge is a scrapbook entry.
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

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
| **P1** | a kind **no vocabulary declares** (core §5 or a loaded pack) · a kind written on **another layer's property** (`subjectKind` on an Object — nothing on the Object layer reads it) · the graph-wide census of undeclared kinds beyond the gold · **two homes** for one item (the expected layer holds it *and* a second layer does) · a declared-but-wrong kind · duplicate nodes for one entity · Source nodes with zero claim→source edges · a NAND where nothing contradicts · a node with no kind property |

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
`tests/test_ax_write_eval.py` (31 tests, no database needed):

```bash
TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_ax_write_eval.py -q
# 31 passed
```

## Results — measured 2026-10-22, ten runs

Every number below is recomputed from the graph each agent wrote. Each run
directory holds **both** halves — `graph.json` (every node's label, kind and id,
every edge) and `score.json` (the verdicts read from it) — beside the agent's own
`write.py`. Re-scoring a committed `graph.json` reproduces its `score.json`
exactly — one command, no database:

```bash
uv run python tools/ax_write_eval.py score \
    --graph eval/how-to-use-tortoise/step1/runs/contradicting_claim/graph.json \
    --id contradicting_claim --out /tmp/rescore
```

That is what makes these numbers auditable rather than asserted, and
`test_rescoring_a_committed_graph_reproduces_its_score` pins it for all ten
runs. A `graph.json` written before the kind-axis fix carries one `kind` per
node and no `kind_prop`/`foreign_kinds`; the scorer falls back to the layer's
own property, so old records stay scorable and the ten committed verdicts are
unchanged by that fix.

| # | Input cell | P0 | P1 | Layers written | The failure |
|---|---|---|---|---|---|
| 1 | portfolio update | 2 | 5 | 5 Point · 3 Subject · 2 Object · 3 Event · 1 Source | ARR and headcount (state) written as **Points**; `subjectKind: company\|investor` undeclared; the release event's `eventKind: disbursement` is a declared *pack* kind but not the kind this occurrence is |
| 2 | meeting transcript | 1 | 8 | 7 Point · 3 Subject · 4 Object · 1 Event · 1 Source | the decision is **two homes** — the meeting Event *and* a decision Point; `subjectKind: person` ×3, `objectKind: offering`, `pointKind: issue`; one **unexplained NAND** (two edges, onto the comms plan and onto the slip forecast) |
| 3 | research paper | 0 | 4 | 6 Point · 1 Subject · 3 Object · 1 Event · 1 Source | `sourceKind: research` — that is the **documentKind (genre) axis**; `subjectKind: authorGroup` undeclared; the findings use the legacy `observation` kind instead of `statement` |
| 4 | decision request | 3 | 2 | 7 Point · 1 Subject · 4 Object · 2 Event · 2 Source | **two homes** for the request (Event + Point); the **goal** is a Point not an Object; the **deadline** is an Event; the **estimate** is a Point |
| 5 | correction to a claim | 0 | 1 | 6 Point · 1 Event · 1 Source | both gold items perfect — supersession with the old claim kept, provenance written — and two undeclared kinds elsewhere (`pointKind: incident`, `eventKind: correction`) |
| 6 | duplicate entity | 0 | 3 | 3 Point · 1 Subject · 1 Object · 1 Event · 1 Source | the **alias was correctly folded onto the existing node** (no duplicate — the measurable this input exists for); the renewal lands on an Event, but with `eventKind: customerCommitment` (undeclared) and a `subjectKind: person` |
| 7 | regulatory filing | 0 | 2 | 5 Point · 2 Subject · 1 Object · 1 Event · 1 Source | no layer failure and no undeclared kind: Source + subject kinds correct (`core:organization` is the **declared** spelling); the two figures use the legacy `observation` kind rather than `statement` |
| 8 | person appointment | 2 | 4 | 4 Point · 2 Subject · 5 Object · 1 Event · 1 Source | the **organisation and the role** written as Objects; `subjectKind: person` ×2; the appointment Event carries an undeclared `eventKind: employmentStart` |
| 9 | contradicting claim | 2 | 2 | 7 Point · 1 Event | **no NAND** — the contradiction was answered by *mitigating the IMPL operator*: relevance dampened where truth was at issue. No Source written at all |
| 10 | product spec | 2 | 2 | 4 Point · 1 Subject · 2 Object · 1 Event · 1 Source | **two homes** for the ship date (plan Object + target Point); the success bar is only a Point; v1's dates are Points, not on the v1 Object |

**Aggregate: 12 P0 and 33 P1 across the ten inputs; 6 of 10 inputs carry at
least one P0, and no input is clean graph-wide.** Two inputs are *near* misses —
the correction (0 P0, two undeclared kinds elsewhere) and the duplicate (0 P0,
the alias fold correct) — and saying so is the point: the failures are not
distributed evenly, they cluster where a *judgement* has to be made.

### Failure classes (each count recomputed from the ten `score.json`s)

| Class | Count | What it looked like |
|---|---|---|
| **Undeclared kind** (neither a core §5 kind nor one any loaded pack declares) | 16 scored items across 6 inputs | `subjectKind` ∈ {`person` ×6, `company` ×2, `investor`, `authorGroup`} · `objectKind: offering` · `pointKind: issue` ×2 · `sourceKind: research` · `eventKind: customerCommitment`, `employmentStart`. Every one accepted by the write path (warn-not-block, #1934). **Not** counted: `core:organization` (the declared key), `disbursement`/`tranche`/`estimate` (the venture/pm packs declare them) |
| **Uncatalogued kinds beyond the gold** (the graph-wide census, 8 inputs) | 8 findings | the gold names some items; the census asks the whole graph. It is what keeps a near-miss from reading 0/0 — `pointKind: incident`, `eventKind: correction` / `planRevision` / `meetingTranscript`, `objectKind: openQuestion`/`governanceSeat`/`deliverable`, `sourceKind: spec`/`note`/`contract` |
| **Layer confusion — only the wrong layer holds the item** | 8 items across 4 inputs | portfolio: ARR + headcount as Points · decision request: goal, deadline, estimate · person appointment: organisation, role · product spec: the success bar |
| **Two homes — the right layer holds it AND a second one does** | 3 items across 3 inputs | decision request: the request (Event + Point) · meeting: the decision (meeting Event + decision Point) · product spec: the ship date (plan Object + target Point). Weaker than an absence, and a different instruction: express the second reading as a claim *about* the node |
| **Wrong-but-declared kind** (legacy-valid, not invented) | 5 | `pointKind: observation` where the state-centric ruling says `statement`; `eventKind: disbursement` where the occurrence is an occurrence/milestone. A different lesson from an invented kind, which is why the instrument separates them |
| **Item absent from the graph** | 2 | meeting: the 80% slip forecast (the run wrote a Point about it — the matcher's stems miss "slips"/"slip") and product spec: v1's dates. Both are human-verified below |
| **Truth vs relevance** | 1 input, 1 finding | contradicting claim: no NAND, with the dampener named inside that finding (the record's `mitigations` shows the `mitigated_by` edge). One act, one finding — it used to be charged twice |
| **NAND where nothing contradicts** | 1 | meeting: one NAND operator with two edges (onto the comms plan, onto the slip forecast). A judgment call, and the instrument treats it as a finding because a contradiction operator on a claim nobody attacked computes a wrong confidence |
| **Provenance** | 1 | contradicting claim wrote no Source at all. Nine of ten runs wrote one; the route that worked is `create_point(extractedFrom=[url])` — a **property**, not an edge verb |
| **Duplicate identity** | 0 | the one input built to produce a duplicate produced none: the alias was folded onto the existing Object. This is the measurable the document must keep |

### Instrument limitations and corrections, stated rather than hidden

This section exists because **two rounds of fresh-context review found the
instrument lying about its own subject**, and the numbers above are what remains
after every fix. The corrections are recorded, not quietly absorbed: a
measurement artifact that hides its own revisions teaches the wrong thing.

Round 1:
1. **The same-layer preference had no floor** — a same-layer mention scoring
   0.08 beat a different-layer containment match at 0.83, so findings accused
   nodes the agent never wrote the item as. Fixed with a floor and a margin rule.
2. **Pack-declared kinds read as undeclared** — `_vocab()` imported only the
   core §5 sets. The instrument now answers **CORE / PACK / UNDECLARED** from the
   loaded pack registry, and only the third is a finding.
3. **`core:organization` was called "an invented namespace prefix"** — it is the
   *declared* key (`subject_binding.subject_kind_names()`). The subject
   vocabulary is imported now.
4. **`"clean end-to-end"` was true only of the gold items** — `score()` audits
   the nodes the gold names. The graph-wide census makes "no undeclared kind"
   auditable; it does **not** make a run clean (a declared-but-wrong kind or a
   wrong layer beyond the gold is still unaudited — stated so the claim is not
   read wider than it is).
5. **The committed run file was the score output**, so the graph evidence the
   Method describes could not be audited. `score --out DIR` writes `graph.json`
   beside `score.json`.
6. **`expect_mitigation: false` was boilerplate on nine inputs**, turning the
   research input's sanctioned mitigation of an under-evidenced inference into a
   P0. Both operator rules are three-valued: absent = not scored.
7. **`duplicate_expected` was declared and implemented nowhere.** Implemented,
   restricted to entity layers and legal-form suffixes.

Round 2:
8. **"written as a Point" was asserted where the expected layer held the item
   too.** Three P0s (the decision request, the meeting decision, the plan's ship
   date) accused an absence the graph disproved: an Event / an Object existed
   beside the Point. Fixed twice over — a crude stemmer so `join`/`joins` and
   `renew`/`renewal` compare equal, and a **two-homes** verdict (P1) that names
   both nodes instead of claiming the item is absent. The layer count fell from
   12 to 8 and the two-homes class appeared; the totals moved to 13/33.
9. **The census was unpinned and double-counted.** It now skips the nodes a gold
   item already resolved to, and it has its own test — deleting the census used
   to leave 21/21 green.
10. **The document contradicted itself** ("nineteen P0s" beside an 18 headline,
    "four MISSING verdicts" beside three, an enumeration summing to 12 for a
    total of 14). Every number in this section is now recomputed from the
    records and pinned by `test_the_reported_totals_match_the_committed_records`.

Round 3:
11. **One write was charged twice.** `contradicting_claim` carried a P0 for the
    missing NAND *and* a second P0 for the mitigation that replaced it — one act,
    two findings, so the aggregate counted it twice. The dampener is now named
    **inside** the missing-NAND finding (and in the record's `mitigations`), the
    standalone dampener rule fires only when no NAND-less contradiction already
    accounts for the write, and the totals moved 13/33 → **12/33**.
12. **The test that was supposed to catch the instrument's own worst false
    positive could not fail.** Its two nodes tied at 0.80, so list order decided
    the verdict and the whole same-layer preference could be deleted with the
    suite still green. The fixture now makes the wrong-layer node an exact match
    (1.0) against a 0.867 correct-layer one, and the test asserts the raw ranking
    puts the wrong-layer node first — deleting the preference now reds it.
13. **`KIND_PROP` was declared and referenced nowhere.** The record read the
    kind through one `coalesce` across all five kind properties, so it could not
    say WHICH property carried it — and a kind on the wrong axis (`eventKind` on
    an Object) read back as a legal-looking kind. Each layer is now read from its
    own property, a foreign-axis kind is kept as `foreign_kinds` and reported as
    its own **P1 cross-axis** finding (gold-named nodes) or inside the
    graph-wide census.

Still true, and stated as a limit rather than a rule: the matcher is token
overlap with a crude stemmer. **Two "item absent" verdicts remain
human-verified as present** (meeting: the 80% forecast was written as a Point,
which is the *expected* layer, so it is a kind finding not a layer one; product
spec: v1's dates live on Points rather than on the v1 Object). The class table
uses that human-verified reading; the raw machine verdicts stay in `score.json`
untouched.

Two tool-surface defects were hit by the eval agents and **filed as #9696**, not
worked around: `create_event(aboutPoint=[id])` — the **list** form — writes no
edge and raises nothing (the string form writes it), the `about*` props wire
nothing at all outside the Event create path, and `aboutPoint` is refused by
`create_edge` while `TortoiseSDK` exposes no `create_about_edge`. A silent write
failure in the surface this document teaches is a finding in its own right;
fixing it is not this issue's scope.

## The failure list = the document's outline

Derived from the classes above, in the order an agent has to decide.

1. **Layer first, and it is a decision about the ITEM, not the sentence.** Eleven
   of the findings are a *state* (ARR, headcount, a goal, a plan's ship date and
   success bar) sitting on the logic layer — eight of them with **no** Object
   holding it, three with **both**, which is the subtler half: two homes is not
   "nearly right", it is a fact no query can answer, because neither node is
   authoritative. The discriminator the document must teach: *does this hold now
   (Object), did it happen at a time (Event), is it a belief we hold (Point), is
   it an actor (Subject), is it something we read it from (Source)?*
2. **An occurrence is not a belief.** The decision in the transcript, the
   appointment in the note and the "request" in the decision request were all
   written as **Points** beside (or instead of) an Event. Rule: if it happened on
   a date, it is an Event with an `eventKind` — the belief layer records what we
   *conclude*, not what *occurred*.
3. **A person, an organisation and a role are Subjects — a role is not an
   Object.** One run wrote the employer and the role as Objects. Rule: `Subject`
   for anything that can act or be acted upon, `Object` only for the thing whose
   state changes.
4. **Kinds come from the declared vocabulary — and the two axes are not one.**
   The most common single failure (16 scored items, 6 of 10 inputs) is an
   invented kind: `subjectKind: person`, `company`, `investor`, `authorGroup`.
   And the paper got `sourceKind: research`, which is the *genre* axis
   (`documentKind`). Rule: pick the kind the layer's vocabulary declares (§5
   core, or a pack kind spelled the way the pack declares it), and never confuse
   `sourceKind` (what kind of source) with `documentKind` (what genre it is).
5. **Truth attacks the Point; relevance dampens the operator.** The one
   contradicting input was answered by mitigating an `IMPL` and nothing attacked
   the Point. Rule: a claim that is *wrong* gets a `NAND`; a claim that is *less
   relevant* gets `mitigate_operator` (0.10–0.50). A mitigation where a NAND
   belongs leaves the wrong confidence computed, not missing.
6. **Change is supersession with the old claim kept.** The one class the agents
   already got right unaided (both supersession inputs, and the alias fold on the
   duplicate input) — keep the *why* (the before-state stays answerable) and do
   not spend lines re-teaching the mechanics.
7. **Provenance is a property lane, not an edge verb** (`extractedFrom=[url]` on
   the claim). Nine of ten runs found it; the one that wrote no Source at all is
   the input where the claim was being attacked. Rule: every claim written from
   something we read gets its source at write time, and a Source with no
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

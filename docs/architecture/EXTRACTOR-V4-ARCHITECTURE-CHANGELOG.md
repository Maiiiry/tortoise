---
title: "Extractor v4 — Architecture Changelog"
type: engineering
domain: platform
doc_status: live
---

# Extractor v4 — Architecture Changelog

> **What this is.** The record of *how the extractor design got where it is* —
> the decision-protocol runs, the review cycles and what they found, and the
> corrections applied to the document. It is process and change history.
>
> **What `EXTRACTOR-V4-ARCHITECTURE.md` is.** The design. It states the target
> architecture and the decisions that fix it; it does not narrate the process
> that produced them or keep a running status of the work.
>
> **Moved out of the design document on 2026-10-15** (issue #8542). The material
> is moved verbatim; nothing is deleted. The design document keeps a pointer at
> §16 so its numbering — and the cross-references into §16.3/§16.4 — still
> resolve.

**The decisions themselves stay in the design document** (§16.3, §16.4). Only
the record of *running the protocol* and *what each review cycle did* is here.

---

## 16. Decision protocol run (AGENTS.md, 2026-09-23)

Each open decision was run through the mandated sequence: **contradiction test first → research (does it actually need a human?) → the required artifact.**

| decision | contradiction test | needs the owner? | artifact / action |
|---|---|---|---|
| **Keep contention + EP confidence** (ruled Option 1) | **No contradiction** — it *is* the recorded decision (`ONTOLOGY.md` §3.1). The field converges **against** it. | ruled | ✅ **`OVERRIDES:` posted on `#2730`** — the field's default is resolve/erase; we hold in tension |
| **Granularity** | The **merge-ban** was withdrawn (O4, §16.3); **the fuse prohibition was re-affirmed** by the same owner comment. ⚠️ **GraphRAG's consolidation STILL contradicts that position** — §9 REFUSES it (*"Directly destructive of the epistemic layer"*), and §16.4 authorises `MERGE` only *"when nothing distinguishing is lost"* and **never across a negation or a condition**, which is exactly what *"resolve the contradictions and provide a single, coherent summary"* does. | **merge-ban withdrawn**; **fuse re-affirmed** | ⛔ **The `OVERRIDES:` on `#4899` is WITHDRAWN** (comment `2026-09-24T04:33:23Z`); **near-duplicate `MERGE` is authorised** (bounded by §16.4, arbitrated by Jev) — and **`FUSE` REMAINS FORBIDDEN**: the admissible set is `KEEP` / `NOOP` / `DISCARD` / `MERGE` |
| **D6: LLM proposes to-graph connections** | **Contradiction** — Hindsight's mechanical cross-batch contradicts D6, so **not adoptable**. Route = reopen. | **still open** | reopen in front of the owner (§13.8). **No `OVERRIDES:`** — LLM edge extraction *is* the field default, so D6 is not against the grain |
| **`aboutObject`: stored edge vs derived join** | No contradiction — no decision mandates materialization | **no** | ✅ **MEASURED + RULED: KEEP the stored edge.** The primary search path **does not** read it, but **four CUSTOMER-CALLABLE sites do** (`belief_timeline`, `topic_summarize`, `recall_subgraph`, `search(order_by="graph")` — §13.8 question 2) plus two default-ON ingestion sites → deriving would silently break four public surfaces. **Owner 2026-09-23: the remaining unknown — *are those tools ever called?* — is unanswerable pre-launch, so the question is DEFERRED, not decided.** Keep for now; optimise with users. |
| **Fan-out caps** | No contradiction; the field converges (Hindsight caps at **200** *and* carries a timeout that drops the whole arm); ⚠️ **the bound is explicit rather than implicit** | **no** | ✅ **ADOPTED at 200** (owner, 2026-09-23: *"fine on adding the cap"*). **Above our current worst hub (123), so it binds nothing today and cannot lose data now** — a guard rail against runaway fan-out, **not** an optimisation. Refine with real usage. See `STORAGE-ARCHITECTURE.md` §11.5. |
| **Keep the epistemic + entity connection layer** | No contradiction — it *is* the product. The field's cost-driven default (Hindsight derives instead of storing) points the other way. | ruled | ✅ **KEPT — owner, 2026-09-23.** Cheap because edges are **property-free** and Postgres holds them **on disk** (~3 MB ≈ $0.0004/mo). **`OVERRIDES:` posted on `#4333`** — never cut this layer to save money. See `STORAGE-ARCHITECTURE.md` §11. |
| **D3: raw vs narrative** | No contradiction (D3 is provisional) | **no** | ✅ The owner already approved the measurement. **The measurement decides** — not a gate |

**Deliberately NOT marked:** our class-based `Source` deviates from PROV (§9.2 of the storage doc), but modelling a source/document as a node is **not** against the industry default — GraphRAG, Graphiti and Hindsight all anchor to a document node. Deviation noted; **no `OVERRIDES:`** because it is not a departure from practice.

**⚠️ The "decision ledger" does not exist in this repo.** `docs/plans/2026-09-22-2814-rebuild-config-durability.md:316` records it: *"the 'decision ledger' earlier drafts named does not exist in this repo (`rg -il 'decision ledger'` matches only this plan)"*. **The `OVERRIDES:` comment on the issue is the marker's only home** — do not cite a ledger that isn't there.

### 16.1 The review cycle — outcome recorded (2026-09-23)

**Two fresh-context reviewers per document, complementary lenses; full consolidation in `REVIEW-CONSOLIDATED-2026-09-23.md` (that review's own summary: 6 P0 · 34 P1 · 18 P2).** This section records **what was done with each category**, so a later lane can tell a correction from an omission.

| category | count | disposition |
|---|---|---|
| **P0 — a false load-bearing claim, or two documents that contradict each other** | **6** | **All 6 corrected in place — A4 only after §16.2 caught it** (A1 vector-index model · A2 truth/derived test · A3 `#2453` landed · A4 VET/S3 circular dependency — `MERGE-INTO-EXISTING` removed from VET's outputs, restoring S3 as the sole authority · A5 the 10× cannot reproduce itself · A6 *"an Object is a name"*). **The corrections are marked in place — most with `⚠️ CORRECTED`, some with the error named directly in the row (A1 *"REWRITTEN TWICE"*, A3 *"THIS ROW WAS WRONG"*, A6 *"DO NOT WRITE"*).** |
| **P1 — code-evidence, citation, or arithmetic defects** | **34** | **All corrected.** Arithmetic recomputed (B1.1 §1, B1.2/B1.3/B1.5/B1.19, §5's table); citations fixed or the claim deleted (B1.16 `#1509 §9`/`E2`, B1.18 `MITIGATES`, B2.9 `llm_tail`, B2.18 `#3011`); **B2.1's constraint 4 kept visible with the evidence that falsifies it**, so it cannot be re-derived. |
| **P2 — completeness gaps** | **18** | **Recorded, not silently dropped** — this document's §4.3 (G1–G5) + `STORAGE-ARCHITECTURE.md` §5.1/§14/§15. |
| **Genuinely a decision, not a correction** | **6** | ⛔ **Escalated to the owner — §16.3. These are NOT applied and NOT decided.** |

**⚠️ The rule applied throughout, and it is the one that matters:** where a claim about **process** had no artifact to check it against, it was **DELETED rather than reworded** (the `#3011` thresholds, *"carries most of the retrieval value"*, the `#2453` reading). **Rewording a self-referential claim just generates the next review round; deleting it ends the question.**

### 16.2 ⛔⛔ THE SECOND REVIEW CYCLE FOUND THAT THE *FIRST REVIEW* WAS WRONG — and this is the most important lesson in this document

**Cycle 2 (2026-09-24, four fresh-context reviewers + two gap analysts) checked the *corrections*, not the original text. Five of them were false. Three trace to the same source: `REVIEW-CONSOLIDATED-2026-09-23.md` — the cycle-1 review itself — whose findings were applied verbatim without re-verification.**

| what the doc now said | what the primary source says | where the falsehood came from |
|---|---|---|
| *"`#3011`'s thresholds were embellished 3 of 3; REMOVED"* | the frozen doc **carries all three** (`H1 ≥15`, `H2 ≥10`, `H3 −5`) | **cycle-1 B2.18** — conflated *"also in H4 / also in the falsifiers"* with *"not in H1/H3"* |
| *"the lookup does not need kinds → constraint 4 evaporates"* | `_find_existing_entity` **uses `kind` as its exact-match key** | **cycle-1 B2.1** — read the query builder and called it the lookup |
| *"there is no `:Document` node and none is created"* | ✅ **NOW TRUE** — the `:Document` NODE label is **retired** (PR `#5127`, D10); the **`documents` cap** counts document-bearing `:Source` nodes (the node cap does not) | not from cycle 1 — a **lane inference** from "D30 puts raw outside the graph" |
| *"All 6 P0s corrected in place"* | **at the time, A4 was NOT corrected** — `MERGE-INTO-EXISTING` was still in VET's outputs, so the VET/S3 circularity stood | **cycle-1 A4's disposition was claimed, not verified** · **✅ NOW FIXED** — `MERGE-INTO-EXISTING` is removed from VET's outputs (§S2.2) and §16.1 is corrected |
| *"the narrowing is PROPOSED… ✅ Recorded as such in §9 (D4)"* | §9 D4 says **`DECIDED (narrowed)`** | **cycle-1 B2.20** — the "fix" replaced one two-ways status with another · **✅ NOW FIXED** — the ⚠️ pending line is deleted and §S3 states `DECIDED (narrowed)` |

**⇒ THE MECHANISM, STATED PLAINLY: a review finding is a CLAIM, not a fact.** Applying one is a **change to the artifact**, and it must be verified against the same primary source the finding cites — **otherwise the review becomes a propagation vector for its own errors**, and the `⚠️ CORRECTED` marker makes the wrong text *more* credible than the right text it replaced. **That is exactly what happened here: the doc's markers became evidence for the wrong reading.**

**⇒ THE RULES THIS PRODUCES, and they are binding on future cycles:**

1. **Verify a finding against its cited primary source before applying it.** A line range, a frozen doc, a `gh` output — **open it.**
2. **A `⚠️ CORRECTED` marker is not evidence.** It says the text was *changed*, not that the change was *right*.
3. **Correct the review record too.** `REVIEW-CONSOLIDATED-2026-09-23.md` B2.1 and B2.18 are **themselves wrong** and are the propagation vector; a corrected artifact with an uncorrected review leaves the next lane free to re-apply the error.
4. **"Also present elsewhere" is not "absent here".** Two of these five are that single misreading.

**⚠️ And the honest counter-note, so this does not become an argument against reviewing:** cycle 2 also found the things cycle 1 got **right** and this document now depends on — the `#4997` index drift, the four-layer placement, the four dedup keys, the `c/r` ordering rule and the sequence of the ten `!`-corrections that hold. **The failure was not review; it was unverified application.**

---

## Removed wording, by section (moved from the design document, 2026-10-15, #8542)

Every line this pass removed from the design document, keyed to the section it sat
in. Emphasis markers are dropped from the recorded wording so the record renders.
The **substance** each carried — a measurement, a rule, an open question, a
decision — is stated in the design document's rewritten sentence; what is not
carried anywhere is the delivery state and the account of the passage's own
revision, which is what this file is for.

### (front matter)

- `EXTRACTOR-V4-ARCHITECTURE.md:14` — Status: design in progress, 2026-09-23. Home: `#4894` (write-path redesign). Predecessor: `#1509` (extractor v3, approved 2026-08-20; ⚠️ epic CLOSED `NOT_PLANNED` 2026-10-10 — the v4 desi…

### v2 → v3 → v4

- `EXTRACTOR-V4-ARCHITECTURE.md:95` — v3 has no discard, trim or gate anywhere. It is a fidelity workstream. v4 is a selection workstream. ⚠️ The ordering obligation this passage used to state — "it must land with, or before,…

### ⚠️ Storage rule for the first two tiers — they are OUTSIDE the graph (owner, 2026-09-23)

- `EXTRACTOR-V4-ARCHITECTURE.md:121` — Neither raw nor narrative belongs in the graph. Both live in Postgres. ⚠️ The owner's wording is "store in supabase" — and "supabase" there means the DATABASE, not the object bucket. The …
- `EXTRACTOR-V4-ARCHITECTURE.md:128` — - It is far cheaper than the graph — the storage research measured ~580× on disk-resident data (⚠️ not the "~1,000×" an earlier draft stated — that number was unsourced; 580× applies to t…
- `EXTRACTOR-V4-ARCHITECTURE.md:133` — ⇒ D1 restated, correctly: the narrative is not a graph node and not graph metadata — it is Postgres-stored text referenced by its `Source` (the owner's "supabase" is the DATABASE, not the…

### The mechanism is also already filed — `#2684`

- `EXTRACTOR-V4-ARCHITECTURE.md:196` — Related, and not to be duplicated: `#2405` CLOSED (paraphrase-tolerant survival leg — the deformation measurement) · `#2542` family (value-fidelity discipline, cited by `#2684`) · `#2104`…
- `EXTRACTOR-V4-ARCHITECTURE.md:200` — ⚠️ Storage consequence, harmonised with `STORAGE-ARCHITECTURE.md` §9.1: the raw fact is Postgres text, not a graph node (the owner's "supabase" = the DATABASE, not the object bucket; dest…

### ⭐ D10 and the version model — what the extractor must respect

- `EXTRACTOR-V4-ARCHITECTURE.md:467` — ⭐ CORRECTED 2026-09-24 (owner): a document is a SOURCE, not a graph node. "I am suggesting making them sources so our entity layer can be extracted from them… That way our entity layer be…

### ⚠️ This REOPENS D4 — and narrows it along its own reasoning

- `EXTRACTOR-V4-ARCHITECTURE.md:551` — ✅ STATUS: `DECIDED (narrowed)` — the owner ruled. The narrowing is in force: the lookup sits after extraction, and it is consumed by the salience judgment and by S3 only. Extraction does …
- `EXTRACTOR-V4-ARCHITECTURE.md:553` — > ✅ Superseded and deleted (reviewer B2.20, then §16.2). This line used to say the narrowing was "PROPOSED and awaiting the owner's confirmation" while §9 recorded D4 as `DECIDED (narrowe…
- `EXTRACTOR-V4-ARCHITECTURE.md:555` — > ✅ Note — `#4511` is CLOSED, and it no longer gates anything. It was the blocker when the lookup sat before extraction (`_fts_rows` read `r.get("kind")` while the callee emits `point_kin…

### Level 1 — per-candidate (many, cheap, evaluated in parallel)

- `EXTRACTOR-V4-ARCHITECTURE.md:581` — > This is where the pack stops being prose. `packs/dev/manifest.yaml` already declares `memory_granularity` — "Durable: problem-family reasoning … Ephemeral: issue/PR numbers, CI status, …

### ⚠️ Owner question: if the narrative is poor, should we send it back? (2026-09-23)

- `EXTRACTOR-V4-ARCHITECTURE.md:613` — ⚠️ There is no S4 — do not restore one. A previous draft listed a separate "S4 — granularity review" and folded it back in here. It is not a step: its purpose was identical to question 2 …
- `EXTRACTOR-V4-ARCHITECTURE.md:626` — > ⚠️ `TORTOISE_S4_DELTA=0` does NOT disable S4 — it selects the legacy complete-list contract. There is no S4-specific off switch: S4 is reached whenever the v2 pipeline runs a story, and…
- `EXTRACTOR-V4-ARCHITECTURE.md:638` — > pipeline". ⚠️ An earlier version of this marker said `S2.3` had no code counterpart because the
- `EXTRACTOR-V4-ARCHITECTURE.md:639` — > string `S2.3` is absent from `extractor_v2.py`. The string is absent; the mechanism is not. ⚠️ An earlier version of this marker claimed
- `EXTRACTOR-V4-ARCHITECTURE.md:640` — > `S3` and `S5` were absent from the code as well; that was wrong — `S3` and `S5` appear 51 and 22 times

### The five correctness constraints — and ONE of them was FALSE

- `EXTRACTOR-V4-ARCHITECTURE.md:761` — ⚠️ Constraint 4 stands, and it is the basis of the S2.3-before-S3 order. CLASSIFY runs after the gate (so only survivors are classified) and BEFORE S3 — because `_find_existing_entity` (`…

### 4.3 ⚠️ What the steps above do NOT yet cover (review-provided, all unaddressed)

- `EXTRACTOR-V4-ARCHITECTURE.md:816` — | G1 → `#5064` (open remainder; `#5007` CLOSED `COMPLETED` via PR `#5108` — the schema + passthrough shipped, but `extractor_v2.py` emits no `span_start` (the repo's document extractor do…
- `EXTRACTOR-V4-ARCHITECTURE.md:824` — Where the five gaps live in the tracker (checked 2026-10-13): G1 `#5064` (the open remainder — `#5007` closed `COMPLETED` via PR `#5108`, which shipped the span schema + passthrough; what…

### 4.4 Per-step FAILURE POLICY (G3) — ✅ declared 2026-10-14

- `EXTRACTOR-V4-ARCHITECTURE.md:870` — | I3 | A committed run is ALL-OR-NOTHING, and the journal is the truth (`derived = replay(journal)` — `#4240` restored this at the lifecycle layer, `#4716` at the operator layer; both CLO…

### ✅ The carve-out DID NOT cover decision-relevant numbers — measured, and FIXED (`#2453`, PR `#2456`)

- `EXTRACTOR-V4-ARCHITECTURE.md:1022` — | Personal state | "personal best 5K = 27:12" | retained (E2 carve-out — `#1534`, now CLOSED) | retained |
- `EXTRACTOR-V4-ARCHITECTURE.md:1023` — | Operational / decision values | "p99 latency 480 ms at 3k rps" | ⚠️ RETAINED — THE FIX ALREADY LANDED (commit `4a690d0be`, 2026-09-07, PR `#2456`) | retained |
- `EXTRACTOR-V4-ARCHITECTURE.md:1029` — ⚠️ And the mechanical DISCARD (`#4899`, §16 — ✅ LANDED, `value_gate.py`) would previously have inherited this bug — had it been defined against the old, stale reading, it would have disca…

### Where numeric facts should live (owner, 2026-09-23) — structured, so they are never re-processed

- `EXTRACTOR-V4-ARCHITECTURE.md:1039` — - ⚠️ Harmonise, do not duplicate. `#2453` (retain operational values verbatim) · `#1534` CLOSED (E2 state-value Tier-A extraction) · `#2817` (no numeric/locale canonicalisation exists — P…

### 8. Already in the code — do not rebuild

- `EXTRACTOR-V4-ARCHITECTURE.md:1052` — | S2/S4 merge | `merge_embed_lists` (def `extractor_v2.py:2284`; called at `:4845` — ⚠️ an earlier draft cited `:4838`, which is `try:` (`stage_stats` is `:4837`)) |

### 13.8 What this does to our design

- `EXTRACTOR-V4-ARCHITECTURE.md:1304` — | connection fan-out | bounded everywhere, with a timeout that drops an arm | ✅ Cap adopted AND implemented — `PER_ENTITY_FANOUT_CAP = 200` (`tortoise/fanout.py`), consumed by `assembly.p…
- `EXTRACTOR-V4-ARCHITECTURE.md:1306` — ⇒ One of the three questions below is still the owner's (question 1). Question 2 is measured, ruled and deferred; question 3 was adopted and is CLOSED — `#5010` (`PER_ENTITY_FANOUT_CAP = …

### 16. Decision protocol run (AGENTS.md, 2026-09-23)

- `EXTRACTOR-V4-ARCHITECTURE.md:1329` — ## 16. Decision protocol run (AGENTS.md, 2026-09-23)
- `EXTRACTOR-V4-ARCHITECTURE.md:1331` — Each open decision was run through the mandated sequence: contradiction test first → research (does it actually need a human?) → the required artifact.
- `EXTRACTOR-V4-ARCHITECTURE.md:1333` — | decision | contradiction test | needs the owner? | artifact / action |
- `EXTRACTOR-V4-ARCHITECTURE.md:1334` — |---|---|---|---|
- `EXTRACTOR-V4-ARCHITECTURE.md:1335` — | Keep contention + EP confidence (ruled Option 1) | No contradiction — it is the recorded decision (`ONTOLOGY.md` §3.1). The field converges against it. | ruled | ✅ `OVERRIDES:` posted o…
- `EXTRACTOR-V4-ARCHITECTURE.md:1336` — | Granularity | The merge-ban was withdrawn (O4, §16.3); the fuse prohibition was re-affirmed by the same owner comment. ⚠️ GraphRAG's consolidation STILL contradicts that position — §9 R…
- `EXTRACTOR-V4-ARCHITECTURE.md:1337` — | D6: LLM proposes to-graph connections | Contradiction — Hindsight's mechanical cross-batch contradicts D6, so not adoptable. Route = reopen. | still open | reopen in front of the owner …
- `EXTRACTOR-V4-ARCHITECTURE.md:1338` — | `aboutObject`: stored edge vs derived join | No contradiction — no decision mandates materialization | no | ✅ MEASURED + RULED: KEEP the stored edge. The primary search path does not re…
- `EXTRACTOR-V4-ARCHITECTURE.md:1339` — | Fan-out caps | No contradiction; the field converges (Hindsight caps at 200 and carries a timeout that drops the whole arm); ⚠️ the bound is explicit rather than implicit | no | ✅ ADOPT…
- `EXTRACTOR-V4-ARCHITECTURE.md:1340` — | Keep the epistemic + entity connection layer | No contradiction — it is the product. The field's cost-driven default (Hindsight derives instead of storing) points the other way. | ruled…
- `EXTRACTOR-V4-ARCHITECTURE.md:1341` — | D3: raw vs narrative | No contradiction (D3 is provisional) | no | ✅ The owner already approved the measurement. The measurement decides — not a gate |
- `EXTRACTOR-V4-ARCHITECTURE.md:1343` — Deliberately NOT marked: our class-based `Source` deviates from PROV (§9.2 of the storage doc), but modelling a source/document as a node is not against the industry default — GraphRAG, G…
- `EXTRACTOR-V4-ARCHITECTURE.md:1345` — ⚠️ The "decision ledger" does not exist in this repo. `docs/plans/2026-09-22-2814-rebuild-config-durability.md:316` records it: "the 'decision ledger' earlier drafts named does not exist …

### 16.1 The review cycle — outcome recorded (2026-09-23)

- `EXTRACTOR-V4-ARCHITECTURE.md:1347` — ### 16.1 The review cycle — outcome recorded (2026-09-23)
- `EXTRACTOR-V4-ARCHITECTURE.md:1349` — Two fresh-context reviewers per document, complementary lenses; full consolidation in `REVIEW-CONSOLIDATED-2026-09-23.md` (that review's own summary: 6 P0 · 34 P1 · 18 P2). This section r…
- `EXTRACTOR-V4-ARCHITECTURE.md:1351` — | category | count | disposition |
- `EXTRACTOR-V4-ARCHITECTURE.md:1352` — |---|---|---|
- `EXTRACTOR-V4-ARCHITECTURE.md:1353` — | P0 — a false load-bearing claim, or two documents that contradict each other | 6 | All 6 corrected in place — A4 only after §16.2 caught it (A1 vector-index model · A2 truth/derived tes…
- `EXTRACTOR-V4-ARCHITECTURE.md:1354` — | P1 — code-evidence, citation, or arithmetic defects | 34 | All corrected. Arithmetic recomputed (B1.1 §1, B1.2/B1.3/B1.5/B1.19, §5's table); citations fixed or the claim deleted (B1.16 …
- `EXTRACTOR-V4-ARCHITECTURE.md:1355` — | P2 — completeness gaps | 18 | Recorded, not silently dropped — this document's §4.3 (G1–G5) + `STORAGE-ARCHITECTURE.md` §5.1/§14/§15. |
- `EXTRACTOR-V4-ARCHITECTURE.md:1356` — | Genuinely a decision, not a correction | 6 | ⛔ Escalated to the owner — §16.3. These are NOT applied and NOT decided. |
- `EXTRACTOR-V4-ARCHITECTURE.md:1358` — ⚠️ The rule applied throughout, and it is the one that matters: where a claim about process had no artifact to check it against, it was DELETED rather than reworded (the `#3011` threshold…

### 16.2 ⛔⛔ THE SECOND REVIEW CYCLE FOUND THAT THE *FIRST REVIEW* WAS WRONG — and this is the most important lesson in this document

- `EXTRACTOR-V4-ARCHITECTURE.md:1360` — ### 16.2 ⛔⛔ THE SECOND REVIEW CYCLE FOUND THAT THE FIRST REVIEW WAS WRONG — and this is the most important lesson in this document
- `EXTRACTOR-V4-ARCHITECTURE.md:1362` — Cycle 2 (2026-09-24, four fresh-context reviewers + two gap analysts) checked the corrections, not the original text. Five of them were false. Three trace to the same source: `REVIEW-CONS…
- `EXTRACTOR-V4-ARCHITECTURE.md:1364` — | what the doc now said | what the primary source says | where the falsehood came from |
- `EXTRACTOR-V4-ARCHITECTURE.md:1365` — |---|---|---|
- `EXTRACTOR-V4-ARCHITECTURE.md:1366` — | "`#3011`'s thresholds were embellished 3 of 3; REMOVED" | the frozen doc carries all three (`H1 ≥15`, `H2 ≥10`, `H3 −5`) | cycle-1 B2.18 — conflated "also in H4 / also in the falsifiers…
- `EXTRACTOR-V4-ARCHITECTURE.md:1367` — | "the lookup does not need kinds → constraint 4 evaporates" | `_find_existing_entity` uses `kind` as its exact-match key | cycle-1 B2.1 — read the query builder and called it the lookup |
- `EXTRACTOR-V4-ARCHITECTURE.md:1368` — | "there is no `:Document` node and none is created" | ✅ NOW TRUE — the `:Document` NODE label is retired (PR `#5127`, D10); the `documents` cap counts document-bearing `:Source` nodes (t…
- `EXTRACTOR-V4-ARCHITECTURE.md:1369` — | "All 6 P0s corrected in place" | at the time, A4 was NOT corrected — `MERGE-INTO-EXISTING` was still in VET's outputs, so the VET/S3 circularity stood | cycle-1 A4's disposition was cla…
- `EXTRACTOR-V4-ARCHITECTURE.md:1370` — | "the narrowing is PROPOSED… ✅ Recorded as such in §9 (D4)" | §9 D4 says `DECIDED (narrowed)` | cycle-1 B2.20 — the "fix" replaced one two-ways status with another · ✅ NOW FIXED — the ⚠️…
- `EXTRACTOR-V4-ARCHITECTURE.md:1372` — ⇒ THE MECHANISM, STATED PLAINLY: a review finding is a CLAIM, not a fact. Applying one is a change to the artifact, and it must be verified against the same primary source the finding cit…
- `EXTRACTOR-V4-ARCHITECTURE.md:1374` — ⇒ THE RULES THIS PRODUCES, and they are binding on future cycles:
- `EXTRACTOR-V4-ARCHITECTURE.md:1376` — 1. Verify a finding against its cited primary source before applying it. A line range, a frozen doc, a `gh` output — open it.
- `EXTRACTOR-V4-ARCHITECTURE.md:1377` — 2. A `⚠️ CORRECTED` marker is not evidence. It says the text was changed, not that the change was right.
- `EXTRACTOR-V4-ARCHITECTURE.md:1378` — 3. Correct the review record too. `REVIEW-CONSOLIDATED-2026-09-23.md` B2.1 and B2.18 are themselves wrong and are the propagation vector; a corrected artifact with an uncorrected review l…
- `EXTRACTOR-V4-ARCHITECTURE.md:1379` — 4. "Also present elsewhere" is not "absent here". Two of these five are that single misreading.
- `EXTRACTOR-V4-ARCHITECTURE.md:1381` — ⚠️ And the honest counter-note, so this does not become an argument against reviewing: cycle 2 also found the things cycle 1 got right and this document now depends on — the `#4997` index…

### 16.3 ✅ ALL SIX DECISIONS CLOSED (owner, 2026-09-24)

- `EXTRACTOR-V4-ARCHITECTURE.md:1390` — | O2 | ⛔ WITHDRAWN — the owner rejected the framing: "we're not optimising stupidly for a number… this is not a corporate OKR setting exercise. We need to balance multiple things at each …

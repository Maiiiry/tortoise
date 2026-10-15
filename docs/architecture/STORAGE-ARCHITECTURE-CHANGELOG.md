---
title: "Storage Architecture — Changelog"
type: engineering
domain: platform
doc_status: live
---

# Storage Architecture — Changelog

> **What this is.** The record of *how the storage design got where it is* — the
> issue map that indexed the document's sections to the work they drove, the
> review outcomes, and the corrections applied.
>
> **What `STORAGE-ARCHITECTURE.md` is.** The design and the measurements behind
> it. It states the physical layout and the evidence for each ruling; it does
> not index its own sections to tracker entries, and it does not narrate its own
> revisions.
>
> **Moved out of the design document on 2026-10-15** (issue #8542), verbatim.
> Nothing is deleted. The design document keeps the measurement evidence and the
> rulings the map pointed at; only the index moved here.

**CRITICAL RULE: no modification to this file is allowed without explicit approval of the exact changes by Daniel Ospina.**

---

## The issue map (moved from `STORAGE-ARCHITECTURE.md` §13)

**Purpose:** every section of the design document exists to inform a filed
issue. This table was the index, so a reader holding an issue could find the
reasoning, and a reader holding a section could find the work it drove. Issue
states are as they stood on the move date. The table itself is moved verbatim.

| section | issue(s) | what the issue takes from here |
|---|---|---|
| §1 the problem | `#4333` | the measured volume, and the true bytes per quota node. ⚠️ **Corrected 2026-09-23:** the earlier *"5.6 KB vs 1 KB, a 5.6× understatement"* divided **total instance RAM (140 MB)** by the **capped node count (25,000)** — but the numerator includes ~19,944 **quota-FREE** episodic turns, plus Events, Sessions, Documents, scaffolding and the full HNSW index. **`#4333` — the authority this row cites — estimates 2.5–4×.** The honest figure is **~3 KB per node (140 MB ÷ ~45k total nodes) ≈ ~3×**; 5.6 KB must be labelled *"total resident bytes per CAPPED node, including uncapped nodes"* |
| §2 mechanism | `#4333` | why the RAM/disk difference *is* the economics |
| §3 architecture | `#4333` | the physical split, **in the ontology's own vocabulary** — not a new layer |
| §3 what is already in code | **`#4240`** | **the hosted path does not journal unless `TORTOISE_EVENT_LOG_BASE_DIR` is set (`#4240` wired the per-graph journal) → a wiring gap, not a design gap** |
| §3 why this shape | `#3895` | the derived layer becomes **REBUILDABLE** — ⚠️ **NOT "disposable"**: §3 forbids that reading in bold, and `#3895` is a *restore*. "Disposable" is the label that would license dropping the only copy. It regenerates **from the truth layer**, and only because the journal carries the payload |
| §4 tenancy | `#3885` | **one project, tenant-scoped rows** — not one project per team |
| §5 cost | `#4333` · **`#7954`** | published rates; ~10× headroom at 1,000 users. ⚠️ **CORRECTED 2026-10-10: the earlier `#4614` citation was WRONG** — `#4614` is a *quota-cliff capture bug* (`402` at 9,998/10,000), not a rates issue. **This subsection's prices are now vendor-page-verified, and the two-store arithmetic is `#7954`'s (§17.4)** |
| §6 the split does NOT fix volume | **`#4899`** · `#1026` | **storage ~580× on the STORAGE line × selection ~10× (a target, not a measurement) — neither alone reaches 100×.** This is the section that stops a storage migration being sold as the fix |
| §7 state values | **`#2453`** (LANDED) · **`#4899`** (enforcement) · `#2820` | the carve-out, plus the DECIDED placement (owner, 2026-10-06): a state/numeric value is a **structural field on an entity**, with the embedding clause conditional on the §7 query-pattern measurement. ⚠️ **The former "`#1509` (E2, §9)" citation was WRONG** — `#1509` has no §9 and no E2; the placement recommendation there is a **comment** on `#1509` — now **SUPERSEDED by the PLACEMENT ACCEPTED decision (§7)** — and **E2 is `#1534`'s slot** (CLOSED). See §7's own corrected note |
| §7 caveat | **`#4889`** | ⚠️ **CORRECTED (owner, 2026-10-06).** The live graph is a **DOGFOOD instance** — an edge count there was never the right test, and the earlier *"0 edges"* framing is withdrawn. **The code and its tests exist** (`tortoise/subject_binding.py` and the `aboutSubject` write paths exist; `#4889` is CLOSED), and the **end-to-end check needs a capstone issue (not yet filed)**, not a caveat asserting a production count. |
| §8 granularity | `#1509` (E3) · `#4333` | **volume reduction may not come from fusing claims** — it bounds the whole document |
| §9.1 narrative placement | `#2281` | the narrative **is** the ingest-time-distilled layer that issue asks for |
| §9.2 PR / Object / Event | `#1844` | object-only sources; the anchor is a document, **never an event** |
| §10.1 relocation | `#4240` | the hot table stays the working set — which is the invariant this document rests on |
| §10.2 allowlist | — | a reusable correctness rule (no issue needed) |
| §12.1b unindexed embeddings | ⭐ **`#4997`** · **`#4999`** | **only `:Point` has a vector index; `Object`/`Event` full-scan (`leg_trace.reason` remains `ok`; since PR `#5159` the entry also carries the `mechanism` — `#4999` CLOSED `COMPLETED`); 19 MB stored unindexed — and the unindexed-embeddings defect itself is still LIVE, homed on the V1/§12.1c decision** |
| §12.2c vector vs traversal | ⭐ **`#4997`** · `#2730` | **the entity vector is an ENTRY/RESOLUTION key, never the reasoning mechanism — and our Objects have no description to embed** |
| §9.4 source summary vector | — | **needs a ruling (V2); `Source` already carries `summary`, `topics`, `url`, `contentHash`** |
| §9.6 source versioning | — | **the version must be recorded on the extraction link as `sourceVersion`**; a version costs **three timestamps + a hash** (D30 keeps content out of the graph) — **bound it: windows and hashes only, never content copies** |
| §12.1a index shape | `#5090` · `#5331` | **the decision is the size-earned conditional index (C)** — not per-tenant partitioning, not a shared filtered index. The partition comparison is a measured *alternative*: `PARTITION BY` flat to T=500 (96.6 ms at T=2,100, unexplained), the partial-index form degrading to 83 ms at T=500 |
| §17.2 the completeness marker | **`#7952`** ✅ **built 2026-10-12** | a read for a node outside the known-hot set returns explicit **`not-hot`** — **never empty** — and coherence is reconciled as a **number**, not a boolean. This is the input §17.3's router reads. Landed: `tortoise/residency.py` (the marker, its own `residency` field, off by default) + `tortoise/reconciliation.py` (divergence as integer counts, **refusing** when a side is unreadable) + the hosted `/v1/search` field. ⚠️ The declaration's authoritative home is §2.6's control-plane metadata (`#7971`); until then the reader takes the graph's `:Meta` marker and defaults to **complete**, under which the marker cannot fire |
| §17.3 the residency router | **`#7953`** ✅ **built 2026-10-13** | a residency miss is not an insufficiency: **fetch from the truth store**, never escalate a tier, never surface an absence. Decidable only against §17.2's marker (**hard edge**). Landed: `tortoise/residency_router.py` (a pure decision — `serve` / `fetch-record` / `escalate-tier`, with residency outranking sufficiency, a `cache-unreachable` branch, and `route_for_coordinate` for a partial declaration's unwritten key) + the read path's per-key coordinate (`tortoise/sdk.py::tortoise_fts_query(_residency_key=…)`) |
| §17.4 the two-store cost model | **`#7954`** | price **both** stores (Postgres compute + disk **and** FalkorDB RAM) plus the blended total §5–§6 omit |
| §17 the five gaps | **`#7951`** (17.1) · **`#7952`** (17.2) · **`#7953`** (17.3) · **`#7954`** (17.4) · **`#7955`** (17.5) · **`#7967`** (the surface map) | what the record/cache split still needs: a write-path chokepoint, cache coherence + a completeness marker, partial-cache semantics, a two-store cost model, and a field-level placement table. **None is a new decision** — each follows from §2.0. ⚠️ **Corrected 2026-10-10 — this row previously read `#5089` (17.1) · `#5090` (17.5), which named the wrong owners:** **the routing half of §17.1 is `#7951`'s** (`#5089` owns the journal half — §17.1 says so itself), and **§17.5's placement ENUMERATION is `#7955`'s** (`#5090` is a *consumer* of the vector layer, not the enumeration). The five gaps are children of **`#7879`**; **`#7950`** is the capstone that verifies them; **`#7968`**/**`#7969`**/**`#7971`** carry the schema, the record projection and the cache projection, and **`#7970`** the read path. The integration-surface map every one of them cites is **`#7967`** → `docs/epics/2026-10-10-7879-postgres-record/01-test-design.md` |

---

## Removed wording, by section (moved from the design document, 2026-10-15, #8542)

Every line this pass removed from the design document, keyed to the section it sat
in. Emphasis markers are dropped from the recorded wording so the record renders.
The **substance** each carried — a measurement, a rule, an open question, a
decision — is stated in the design document's rewritten sentence; what is not
carried anywhere is the delivery state and the account of the passage's own
revision, which is what this file is for.

### (front matter)

- `STORAGE-ARCHITECTURE.md:14` — Status: design — AMENDED 2026-10-08 to the owner's hybrid ruling (§2.0): Postgres is the system of record for every layer; FalkorDB is a derived hot cache. Read §2.0 and §3's table first.…
- `STORAGE-ARCHITECTURE.md:20` — Related: `#7869` (this amendment — the doc's canonicalisation) · `#5086` (substrate-rebuild tracking) · `#5089` (durability — derived = replay(journal)) · `#5090` (the vector layer) · `#3…

### ⛔ THE STORAGE UNIT IS BYTES — decision, owner, 2026-09-26 (`#4495`)

- `STORAGE-ARCHITECTURE.md:79` — block reads 143 MB / 46 MB, and §13's issue-map row concludes "~3 KB per node (140 MB ÷ ~45k total nodes)" —
- `STORAGE-ARCHITECTURE.md:82` — §13's row carries the earlier denominator correction (the retracted 5.6 KB claim); this block, not §1, is the

### 1. The problem

- `STORAGE-ARCHITECTURE.md:119` — > ⚠️ FIGURE CORRECTED 2026-09-23. An earlier draft said "~6 GB ≈ ~$460/month". The $460 implied a 6.3 GB horizon that was never stated and mixed a growth rate with a total. The correct st…

### 2.0 ⭐ DECISION (owner, 2026-10-08): **hybrid now — Postgres is the system of record; FalkorDB is a derived hot cache**

- `STORAGE-ARCHITECTURE.md:141` — ⚠️ SCOPE — this decides a PLACEMENT AND AN AUTHORITY, and it settles what §2 previously deferred. The earlier note here read "this decides a PROVIDER, not a PLACEMENT (owner clarification…

### 2.1 What FalkorDB actually charges for — measured 2026-09-24

- `STORAGE-ARCHITECTURE.md:162` — ⚠️ CORRECTED TWICE, AND THE TWO CORRECTIONS POINT OPPOSITE WAYS — read this before the table. (i) An earlier draft described "disk-resident, RAM as a cache" as the mechanism; that was cor…

### The class that dominates our bill — measured

- `STORAGE-ARCHITECTURE.md:196` — In our graph the vector class — measured (§12.1b) — is ≈50 MB of stored vector properties plus a 45 MB index total (of which only part is the `Point` HNSW index) against a 141 MB graph, i…

### ✅ THE `#2881` COLLISION IS CLOSED — by SCOPING, not by overriding (see §14.1 **O1**)

- `STORAGE-ARCHITECTURE.md:272` — #### ✅ THE `#2881` COLLISION IS CLOSED — by SCOPING, not by overriding (see §14.1 O1)
- `STORAGE-ARCHITECTURE.md:274` — `docs/durability-posture.md` (`#2881`) is a recorded decision: the journal "is never the durability mechanism … authority stays in a store-managed artifact." An earlier draft of this sect…

### 4. Tenancy — DECIDED (owner, 2026-09-23): one database, tenant-scoped rows

- `STORAGE-ARCHITECTURE.md:379` — ⭐ AND ITS CONSEQUENCE FOR THE VECTOR INDEX — corrected 2026-09-23. "Rows scoped by tenant" and "the index is partitioned by tenant" are different physical designs, and only the second bou…
- `STORAGE-ARCHITECTURE.md:385` — ⇒ The TENANCY decision above does not change. What this bullet settles is the INDEX SHAPE, and it is THE SIZE-EARNED CONDITIONAL INDEX (C, §12.1a) — bounded relation count, earned by volu…

### 5.1 ⚠️ Corrections to earlier drafts of this section — and the two lines it omitted

- `STORAGE-ARCHITECTURE.md:401` — ### 5.1 ⚠️ Corrections to earlier drafts of this section — and the two lines it omitted
- `STORAGE-ARCHITECTURE.md:403` — ⚠️ These figures were quoted elsewhere in an earlier draft at amounts up to ~14× higher, and that draft was never reconciled. The numbers in the table above are the ones to use; the ~$460…

### 6. The split does NOT fix the volume problem

- `STORAGE-ARCHITECTURE.md:422` — ⚠️ CORRECTED 2026-09-23 — this section's multiplier was derived from a wrong vector share, and the correction moves it in the opposite direction from the other fixes. An earlier draft sai…

### 7. State values — RETAIN is decided and landed; PLACEMENT is DECIDED (2026-10-06, below)

- `STORAGE-ARCHITECTURE.md:455` — - ✅ RETAINING verbatim operational values is DECIDED and LANDED — commit `4a690d0be` / PR `#2456` (2026-09-07): `STATE_VALUE_CARVE_OUT` (`extractor_v2.py:145-171`) plus `VALUE_FIDELITY_RU…
- `STORAGE-ARCHITECTURE.md:456` — - ⛔ What is missing is enforcement BY DEFAULT — not the mechanism. `valueGate` exists in code (`tortoise/value_gate.py`) and ships OFF, subordinate to VET. `#4899` is CLOSED.
- `STORAGE-ARCHITECTURE.md:459` — ✅ PLACEMENT ACCEPTED (owner, 2026-10-06): a state/numeric value is a STRUCTURAL FIELD ON AN ENTITY, not a `Point`. The reasoning is the ontology's own: operators connect only epistemic ta…
- `STORAGE-ARCHITECTURE.md:465` — ⚠️ CITATION CORRECTED 2026-09-23. An earlier draft cited "`#1509` §9" for the decision that state values are Points, and "`#1509` E2" for verbatim preservation. Neither exists. `#1509` ha…
- `STORAGE-ARCHITECTURE.md:478` — | Operational / decision values | "p99 latency 480 ms at 3k rps" | ⚠️ RETAINED — the fix LANDED (commit `4a690d0be`, 2026-09-07; PR `#2456`) | retained |
- `STORAGE-ARCHITECTURE.md:480` — Operational values are DURABLE. `#2453` landed and extended `STATE_VALUE_CARVE_OUT` with an operational-value paragraph — "a concrete value that is the SUBJECT of a decision, observation,…
- `STORAGE-ARCHITECTURE.md:484` — > ⚠️ Caveat from `#4889` — CORRECTED 2026-10-06 (owner). An earlier version of this caveat asserted that the `aboutSubject` half does not exist in production today and cited 0 `aboutSubje…

### 8. Constraint carried from the extractor — granularity is NOT negotiable

- `STORAGE-ARCHITECTURE.md:492` — This bounds how far volume reduction may go. The Epistemic layer's operators (`IMPL`, `NAND`) act on individual claims — and the mitigation mechanism is the `mitigated_by` edge on an oper…

### 9.1 The narrative lives in **Postgres** — NOT in the graph (D1, destination corrected 2026-10-09 — the owner's own answer, one day after the §2.0 ruling)

- `STORAGE-ARCHITECTURE.md:512` — ### 9.1 The narrative lives in Postgres — NOT in the graph (D1, destination corrected 2026-10-09 — the owner's own answer, one day after the §2.0 ruling)
- `STORAGE-ARCHITECTURE.md:518` — ⚠️ The same rule applies to raw turns (owner, 2026-09-23): "the narrative is not something we're suggesting to store in the graph (same as raw) but store in supabase." Both of the two non…

### 9.3 ⭐ A document is a **SOURCE**, not a graph node — and this is what makes the entity layer an abstraction

- `STORAGE-ARCHITECTURE.md:557` — ⚠️ What this corrects in an earlier draft. The earlier version placed `:Document` in the truth layer and then discovered it also appeared in the derived layer:
- `STORAGE-ARCHITECTURE.md:563` — ⇒ `:Document` folds into `:Source` — LANDED. Its content leaves the graph — to raw text in Postgres (true binaries to the object bucket; §3) — referenced per D30, and the entities extract…

### ⭐ Scope of the anchor — a **Point-level** guarantee, extended to derivation links (decided 2026-09-25)

- `STORAGE-ARCHITECTURE.md:706` — - derivation — an `Event` (or `Document`) built from a source's content: the connector path materializes the link at the projection choke point (`projection/entities.py::_materialize_conn…

### ⭐ Why it is affordably cheap HERE and expensive elsewhere

- `STORAGE-ARCHITECTURE.md:751` — Bound it there: windows and hashes only, never content copies. If a version ever starts carrying bytes, this stops being cheap and §13's cost argument changes. The content lives in Postgr…

### ⭐ Where this stands (2026-09-25) — a status pointer, not a new rule

- `STORAGE-ARCHITECTURE.md:765` — - What a stale fact does — Policy B, REFINED by the owner on 2026-09-26 (`#5038` comment `5845802608`): "We should not return an out-of-date fact when we have a newer one" — so when a new…
- `STORAGE-ARCHITECTURE.md:767` — - The re-inference engine — `#5422` (its ownership gap) is CLOSED `COMPLETED` (2026-09-28); acceptance A2 of `#5038` is what remains outstanding for it. `#5024` landed (`SourceVersioned`,…

### 11.7 The exception, unchanged

- `STORAGE-ARCHITECTURE.md:851` — ⚠️ The claim that used to sit here — "Vectors are the one layer that does not follow the disk rule (§2) — `pgvector`'s HNSW index is RAM-resident" — was STALE and is removed (corrected 20…

### 12.1 The vector index — the class that dominates our bill

- `STORAGE-ARCHITECTURE.md:863` — ⚠️ THIS SECTION WAS REWRITTEN TWICE. Read the version below, not either earlier one.
- `STORAGE-ARCHITECTURE.md:865` — - Draft 1 claimed the index "is RAM-mandatory and does not follow the disk rule" and inferred an unprovisionable 7 TB of RAM.
- `STORAGE-ARCHITECTURE.md:866` — - Draft 2 corrected that to "disk-backed and page-cached" — correct for pgvector, but written for the then-deferred Postgres target.
- `STORAGE-ARCHITECTURE.md:867` — - ✅ Draft 3 (this one) is about the engine we actually run. ⚠️ And the 2026-10-08 ruling (§2.0) makes that Postgres target the RECORD — so Draft 2's mechanism is now live for the record s…
- `STORAGE-ARCHITECTURE.md:884` — ⚠️ Correcting an earlier draft that said "95.6 MB of 140 MB (~68%)". That figure added the whole index total — but the index total also contains every fulltext index we have (`Point.conte…

### ⭐ A vector is POINTER-SIZED — 1.5 KB, whatever the text says

- `STORAGE-ARCHITECTURE.md:899` — A 3 KB summary → a 1.50 KB vector. A 50 KB transcript → the same 1.50 KB vector. A 384-dim float32 embedding is a fixed-width row: it is a pointer, not a payload. So "vectorise the summar…

### What is NOT a problem: latency (measured 2026-09-24 — an earlier draft got this badly wrong)

- `STORAGE-ARCHITECTURE.md:905` — #### What is NOT a problem: latency (measured 2026-09-24 — an earlier draft got this badly wrong)
- `STORAGE-ARCHITECTURE.md:907` — An earlier draft claimed a cold index produced "p90 96ms vs 24ms" and that this was "20–50× over every published latency budget, so it cannot be user-facing." BOTH HALVES WERE WRONG.
- `STORAGE-ARCHITECTURE.md:909` — Wrong half 1 — the number was not ours. 96ms/24ms came from OpenSearch's designed disk mode — a different engine. And the "10,500ms cold" figure came from a billion-vector AWS deployment.…
- `STORAGE-ARCHITECTURE.md:911` — Wrong half 2 — the budget is not a requirement. The "50–500ms" figures are vendor self-report:
- `STORAGE-ARCHITECTURE.md:925` — ⇒ CONCLUSION: latency is not a constraint at our size and is not a reason to change anything. Recorded because the earlier draft would have justified an architecture on it. (Retrieval gen…

### 12.1a Why "just partition per tenant" is NOT one mechanism — and the two halves have OPPOSITE answers

- `STORAGE-ARCHITECTURE.md:933` — ⚠️ CORRECTED 2026-10-08 by this lane's own first-party measurement. An earlier version of this section withdrew per-tenant partitioning wholesale, on a study whose mechanism does not reac…
- `STORAGE-ARCHITECTURE.md:940` — §12.1a's cited mechanism — "Postgres takes a shared lock on every index of a table during planning" — can only reach (A). It is a count of indexes per relation, so it cannot reach (B), wh…

### How big our index is, and why "fewer vectors" is the whole game

- `STORAGE-ARCHITECTURE.md:970` — ⚠️ The single most valuable number to stop guessing: (caveat kept from the earlier draft) `2.5M` has ONE meaning in this document: the product total. An earlier draft used it a second tim…

### 12.1b ⚠️ CORRECTED ACCOUNTING — where the vectors actually sit (2026-09-24)

- `STORAGE-ARCHITECTURE.md:974` — ### 12.1b ⚠️ CORRECTED ACCOUNTING — where the vectors actually sit (2026-09-24)
- `STORAGE-ARCHITECTURE.md:976` — An earlier draft attributed the whole vector class to one index. Reading the code changed the picture; then the live graph changed it again. One finding is a filed defect.
- `STORAGE-ARCHITECTURE.md:1002` — `:Object` costs 3.4× `:Point` for the same query. Before PR `#5159` the trace could not distinguish an index hit from a table scan — both reported `ok` on the brute-force path, so degrada…
- `STORAGE-ARCHITECTURE.md:1006` — ⇒ Filed as `#4997` — CLOSED `COMPLETED`. ⚠️ REFRAMED 2026-09-24 (owner): the "stop storing OR index them" framing was a FALSE CHOICE. The third option is better than either: keep the vect…

### 12.2 ⇒ The lever is "what gets an embedding"

- `STORAGE-ARCHITECTURE.md:1069` — ⚠️ Correction from the owner's counterexample (2026-09-23). An earlier draft of this section proposed leaning on class ("don't embed Events / Objects / mechanical statements"). The owner …

### 12.2a Where the rule lives — ALREADY DECIDED (adoption gate, contradiction test run 2026-09-23)

- `STORAGE-ARCHITECTURE.md:1110` — ⚠️ Correction to an earlier note in this section. An earlier draft recommended "computed, because a class-level rule fails", citing the owner's meeting counterexample as the reason to mov…

### 12.2b The levers, ranked — and one earlier recommendation is WITHDRAWN

- `STORAGE-ARCHITECTURE.md:1163` — ### 12.2b The levers, ranked — and one earlier recommendation is WITHDRAWN
- `STORAGE-ARCHITECTURE.md:1176` — ⚠️ Lever 4 is PARTIALLY RESTORED, and the withdrawal stays visible on purpose. It was published in an earlier draft as "none — pure win", then withdrawn wholesale when a study showed a pe…

### 12.2c ⭐ What earns a VECTOR and what earns a TRAVERSAL — the research answer (2026-09-24)

- `STORAGE-ARCHITECTURE.md:1215` — ⭐ What we already have is the right shape: `Object.name` already has a FULLTEXT index, as do `Event.subject`/`Event.name` (measured, §12.1b). Lexical entity lookup is the resolution signa…
- `STORAGE-ARCHITECTURE.md:1217` — ⚠️ CORRECTION ACCEPTED (owner, 2026-09-24) — weak names are a SEQUENCING fact, not an argument against the vector. The definite-description problem is already an in-flight extractor works…

### 12.8 What this means for our design — the four things to do

- `STORAGE-ARCHITECTURE.md:1306` — ⚠️ And the discipline that keeps this honest: our own numbers for the cost TIERS still do not exist. Every tier figure above is published third-party and must be re-measured on our data b…

### 13. Issue map — what this document informs

- `STORAGE-ARCHITECTURE.md:1308` — ## 13. Issue map — what this document informs
- `STORAGE-ARCHITECTURE.md:1310` — Purpose: every section above exists to inform a filed issue. This table is the index, so a reader holding an issue can find the reasoning, and a reader holding a section can find the work…
- `STORAGE-ARCHITECTURE.md:1312` — | section | issue(s) | what the issue takes from here |
- `STORAGE-ARCHITECTURE.md:1313` — |---|---|---|
- `STORAGE-ARCHITECTURE.md:1314` — | §1 the problem | `#4333` | the measured volume, and the true bytes per quota node. ⚠️ Corrected 2026-09-23: the earlier "5.6 KB vs 1 KB, a 5.6× understatement" divided total instance RA…
- `STORAGE-ARCHITECTURE.md:1315` — | §2 mechanism | `#4333` | why the RAM/disk difference is the economics |
- `STORAGE-ARCHITECTURE.md:1316` — | §3 architecture | `#4333` | the physical split, in the ontology's own vocabulary — not a new layer |
- `STORAGE-ARCHITECTURE.md:1317` — | §3 what is already in code | `#4240` | the hosted path does not journal unless `TORTOISE_EVENT_LOG_BASE_DIR` is set (`#4240` wired the per-graph journal) → a wiring gap, not a design gap |
- `STORAGE-ARCHITECTURE.md:1318` — | §3 why this shape | `#3895` | the derived layer becomes REBUILDABLE — ⚠️ NOT "disposable": §3 forbids that reading in bold, and `#3895` is a restore. "Disposable" is the label that woul…
- `STORAGE-ARCHITECTURE.md:1319` — | §4 tenancy | `#3885` | one project, tenant-scoped rows — not one project per team |
- `STORAGE-ARCHITECTURE.md:1320` — | §5 cost | `#4333` · `#7954` | published rates; ~10× headroom at 1,000 users. ⚠️ CORRECTED 2026-10-10: the earlier `#4614` citation was WRONG — `#4614` is a quota-cliff capture bug (`402…
- `STORAGE-ARCHITECTURE.md:1321` — | §6 the split does NOT fix volume | `#4899` · `#1026` | storage ~580× on the STORAGE line × selection ~10× (a target, not a measurement) — neither alone reaches 100×. This is the section…
- `STORAGE-ARCHITECTURE.md:1322` — | §7 state values | `#2453` (LANDED) · `#4899` (enforcement) · `#2820` | the carve-out, plus the DECIDED placement (owner, 2026-10-06): a state/numeric value is a structural field on an e…
- `STORAGE-ARCHITECTURE.md:1323` — | §7 caveat | `#4889` | ⚠️ CORRECTED (owner, 2026-10-06). The live graph is a DOGFOOD instance — an edge count there was never the right test, and the earlier "0 edges" framing is withdra…
- `STORAGE-ARCHITECTURE.md:1324` — | §8 granularity | `#1509` (E3) · `#4333` | volume reduction may not come from fusing claims — it bounds the whole document |
- `STORAGE-ARCHITECTURE.md:1325` — | §9.1 narrative placement | `#2281` | the narrative is the ingest-time-distilled layer that issue asks for |
- `STORAGE-ARCHITECTURE.md:1326` — | §9.2 PR / Object / Event | `#1844` | object-only sources; the anchor is a document, never an event |
- `STORAGE-ARCHITECTURE.md:1327` — | §10.1 relocation | `#4240` | the hot table stays the working set — which is the invariant this document rests on |
- `STORAGE-ARCHITECTURE.md:1328` — | §10.2 allowlist | — | a reusable correctness rule (no issue needed) |
- `STORAGE-ARCHITECTURE.md:1329` — | §12.1b unindexed embeddings | ⭐ `#4997` · `#4999` | only `:Point` has a vector index; `Object`/`Event` full-scan (`leg_trace.reason` remains `ok`; since PR `#5159` the entry also carrie…
- `STORAGE-ARCHITECTURE.md:1330` — | §12.2c vector vs traversal | ⭐ `#4997` · `#2730` | the entity vector is an ENTRY/RESOLUTION key, never the reasoning mechanism — and our Objects have no description to embed |
- `STORAGE-ARCHITECTURE.md:1331` — | §9.4 source summary vector | — | needs a ruling (V2); `Source` already carries `summary`, `topics`, `url`, `contentHash` |
- `STORAGE-ARCHITECTURE.md:1332` — | §9.6 source versioning | — | the version must be recorded on the extraction link as `sourceVersion`; a version costs three timestamps + a hash (D30 keeps content out of the graph) — bou…
- `STORAGE-ARCHITECTURE.md:1333` — | §12.1a index shape | `#5090` · `#5331` | the decision is the size-earned conditional index (C) — not per-tenant partitioning, not a shared filtered index. The partition comparison is a …
- `STORAGE-ARCHITECTURE.md:1334` — | §17.2 the completeness marker | `#7952` ✅ built 2026-10-12 | a read for a node outside the known-hot set returns explicit `not-hot` — never empty — and coherence is reconciled as a numb…
- `STORAGE-ARCHITECTURE.md:1335` — | §17.3 the residency router | `#7953` ✅ built 2026-10-13 | a residency miss is not an insufficiency: fetch from the truth store, never escalate a tier, never surface an absence. Decidabl…
- `STORAGE-ARCHITECTURE.md:1336` — | §17.4 the two-store cost model | `#7954` | price both stores (Postgres compute + disk and FalkorDB RAM) plus the blended total §5–§6 omit |
- `STORAGE-ARCHITECTURE.md:1337` — | §17 the five gaps | `#7951` (17.1) · `#7952` (17.2) · `#7953` (17.3) · `#7954` (17.4) · `#7955` (17.5) · `#7967` (the surface map) | what the record/cache split still needs: a write-pat…
- `STORAGE-ARCHITECTURE.md:1339` — Filed from this document — the tracking issue and its current state are named on each bullet:
- `STORAGE-ARCHITECTURE.md:1341` — - `aboutObject` as a stored edge vs a derived join — `#7948` (OPEN) — the measurement is DONE (research brief, 2026-09-23) and the answer is SPLIT, which decides it.
- `STORAGE-ARCHITECTURE.md:1363` — ⇒ The comparable's conclusion does NOT transfer. Their justification was "recall never touched them" — false here. Deleting the edge would silently break four customer-callable surfaces. …
- `STORAGE-ARCHITECTURE.md:1369` — ⚠️ The derived design is what CREATES a fan-out problem. Hindsight's join needed a `LATERAL LIMIT per_entity_limit` (default 200) and a timeout that drops the entire entity arm. A join on…
- `STORAGE-ARCHITECTURE.md:1374` — - Fan-out caps — `#5010` (CLOSED `COMPLETED`) — nothing in our write path bounds how many edges one entity accumulates (`config/ci-surfaces.yml` at 123, `durations map` at 111, `the admin…
- `STORAGE-ARCHITECTURE.md:1375` — - ⭐ NEW (2026-09-24) — the unindexed embeddings (V3, §12.1b). MEASURED ON THE LIVE GRAPH AND FILED: `#4997` — the measurement half is CLOSED `COMPLETED`; the unindexed-embeddings defect i…

### 14. What this document does not decide

- `STORAGE-ARCHITECTURE.md:1381` — - The vendor — Supabase Postgres is the owner's recorded choice for the system of record (local default now, Supabase post-beta). ⚠️ `pgvectorscale` availability on Supabase Postgres is n…

### 14.1 ✅ Decisions — all six closed (owner, 2026-09-24)

- `STORAGE-ARCHITECTURE.md:1394` — | O2 | Per-item or per-batch target; what recall floor? | ⛔ THE QUESTION IS WITHDRAWN — the owner rejected its framing. "we're not optimising stupidly for a number… this is not a corporat…

### 14.2 ⭐ The engine decision — AMENDED 2026-10-08: the engine stays, its ROLE changes

- `STORAGE-ARCHITECTURE.md:1400` — ### 14.2 ⭐ The engine decision — AMENDED 2026-10-08: the engine stays, its ROLE changes

### 14.3 ⚠️ Open — raised 2026-09-24, NOT decided

- `STORAGE-ARCHITECTURE.md:1417` — ⚠️ AMENDED 2026-10-08 — the hybrid makes V1 more urgent, not less. Under the 2026-09-24 decision the graph was the store, so moving the vector index was an optimisation. Under the hybrid,…
- `STORAGE-ARCHITECTURE.md:1423` — | V3 | ⚠️ The unindexed-embeddings defect (§12.1b) | MEASURED AND FILED 2026-09-24 → `#4997`. REFRAMED: this and V1 are the SAME decision (owner, 2026-09-24) — "isn't the research pointin…
- `STORAGE-ARCHITECTURE.md:1424` — | V4 | ✅ The silent `ok` — FIXED. | `#4999` — CLOSED `COMPLETED` (PR `#5159`). `run_vector_query` now records the mechanism (`index` / `scan` / `scan_fallback`), so a full scan is disting…

### 15. The measurement plan — what is actually worth measuring

- `STORAGE-ARCHITECTURE.md:1430` — ⚠️ CUT HARD 2026-09-24. An earlier version listed seven measurements, several invented to defend numbers this document has since withdrawn. A measurement is only worth listing if a decisi…
- `STORAGE-ARCHITECTURE.md:1441` — WITHDRAWN (recorded so they are not re-invented): the cold-index p95/p99 study (premise was vendor marketing), the quantization-recall study (no engine to apply it to), the 1,000-partitio…

### 16.2 The latency question — the claim was withdrawn, not softened

- `STORAGE-ARCHITECTURE.md:1462` — ### 16.2 The latency question — the claim was withdrawn, not softened

### 17.2 Cache coherence — and a COMPLETENESS MARKER

- `STORAGE-ARCHITECTURE.md:1526` — ⚠️ What is NOT built, and what is deferred rather than absent: the declaration's authoritative home is the control-plane residency metadata named in §2.6 (`#7971`'s cache projection write…

### The marker is NOT a fifth status term

- `STORAGE-ARCHITECTURE.md:1566` — Owner and verification. `#7953` — interface D, part 2; test-design `#7967` surfaces S4 (e2e: a non-resident read returns `not-hot`, never empty) and S5 (unit + integration: a miss is neve…

### 17.4.1 The two rate cards — one per store

- `STORAGE-ARCHITECTURE.md:1619` — ⚠️ `#4614` is cited in this issue — and in §13's issue map, corrected there — as the "published rates" issue, and it is NOT one. It is a quota-cliff capture bug (`402` at 9,998/10,000). T…

### 17.4.2 The measured inputs — every per-node figure with its base AND its denominator

- `STORAGE-ARCHITECTURE.md:1636` — | ⛔ the withdrawn 5.6 KB | 5,600 B ≈ 5.6 KB | 140 decimal MB ÷ 25,000 (CAPPED) | decimal MB | WITHDRAWN — total RAM ÷ the capped count |
- `STORAGE-ARCHITECTURE.md:1643` — 1. `~3 KB` is not the withdrawn `5.6 KB` — different denominators (≈45k vs the capped 25,000), so the `~3×` headline stands. ⚠️ But even `~3 KB` is now superseded for pricing, because the…

### 17.5 A field-level placement table — 9 derived/volatile property names on the sampled surface

- `STORAGE-ARCHITECTURE.md:1745` — §3's per-write test IS the mechanism — "if the journal does not carry it, it is not derived — it is PRIMARY, and belongs in a truth-layer table" — and §7 moves state values onto entity fi…
- `STORAGE-ARCHITECTURE.md:1751` — ✅ The canonical id contract has LANDED (`#7955`, 2026-10-11). The stable key is fixed per label — `:Point` → `id` (minted by `tortoise/ids.py::ulid()`), `:Event` → `eventId`, `:Subject`/`…
- `STORAGE-ARCHITECTURE.md:1753` — ✅ THE SCHEMA HAS LANDED (`#7968`, 2026-10-12). The four ontology layers are relational structure under `supabase/migrations/20261012000001`–`0004` (event stream · semantic · epistemic · p…

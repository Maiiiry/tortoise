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
states are as they stood on the move date.

## 13. Issue map — what this document informs

**Purpose:** every section above exists to inform a filed issue. This table is the index, so a reader holding an issue can find the reasoning, and a reader holding a section can find the work it drives.

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

## Copy pass — status and change narration removed from `STORAGE-ARCHITECTURE.md` (2026-10-15, #8542)

The design document stated, in places, *how it had changed* rather than what the
design is — an issue's open/closed state, a figure an earlier draft carried, a
claim that had been withdrawn, a review cycle's finding. None of that belongs in
a document that states the target architecture: it is change history, and it
re-stales. Each entry records the removed wording; the **substance** it carried
(a measurement, a rule, a trap) is stated in `STORAGE-ARCHITECTURE.md`.

- §1 — *"FIGURE CORRECTED 2026-09-23. An earlier draft said '~6 GB ≈ ~$460/month'. The $460 implied a 6.3 GB horizon that was never stated and mixed a growth rate with a total."* The rate rule is kept.
- §10 — *"Policy B, REFINED by the owner on 2026-09-26"* and *"This refines rather than reverses Policy B"*.
- §12.1c — *"CORRECTION ACCEPTED (owner, 2026-09-24) … An earlier draft of this section used it as a reason to doubt entity vectors; that overstated it."* The sequencing rule is kept.
- §12.1d heading — *"an earlier draft got this badly wrong"*.
- §12.1d — *"An earlier draft claimed a cold index produced 'p90 96ms vs 24ms' and that this was '20–50× over every published latency budget, so it cannot be user-facing.' BOTH HALVES WERE WRONG."*
- §12.1d — sub-heading *"Wrong half 1 — the number was not ours."*
- §12.1d — sub-heading *"Wrong half 2 — the budget is not a requirement."*
- §12.1e — *"The claim that used to sit here — 'Vectors are the one layer that does not follow the disk rule … `pgvector`'s HNSW index is RAM-resident' — was STALE and is removed (corrected 2026-09-24)."* The corrected premise is kept.
- §12.1f — *"Correcting an earlier draft that said '95.6 MB of 140 MB (~68%)'."*
- §12.2 — *"CORRECTED 2026-10-08 by this lane's own first-party measurement. An earlier version of this section withdrew per-tenant partitioning wholesale … It conflated TWO different mechanisms."* The conflated-mechanism finding is kept.
- §12.2b heading — *"and one earlier recommendation is WITHDRAWN"*.
- §12.2b — *"Lever 4 is PARTIALLY RESTORED, and the withdrawal stays visible on purpose. It was published in an earlier draft as 'none — pure win', then withdrawn wholesale … Both readings were wrong about the mechanism."* The mechanism is kept.
- §12.2b — *"The former 'our 95.6 MB measurement', cited here, has been withdrawn as wrong"*.
- §12.3 — *"(caveat kept from the earlier draft) … An earlier draft used it a second time for 'one tenant' … That second usage is deleted"*. The single-meaning rule is kept.
- §12.3 — *"An earlier draft attributed the whole vector class to one index. Reading the code changed the picture; then the live graph changed it again."*
- §13 — *"An earlier draft of this bullet said 'unverified' while §2/§12.2b presented it as a settled blocker — that contradiction is now resolved in the blocker's favour."*
- §13 — *"An earlier version of this table named four sites and called TWO of them customer-callable — that undercounts on both axes"*. The requirement is kept.
- §13 — *"§11.5 is the specific record; an earlier draft of this bullet said 'adopted in principle; no value set yet', which contradicted it."* The guard-rail rule is kept.
- §13.1 — *"Correction from the owner's counterexample (2026-09-23). An earlier draft of this section proposed leaning on class … The owner falsified it:"*. The counterexample and the rule are kept.
- §13.1 — *"Correction to an earlier note in this section. An earlier draft recommended 'computed, because a class-level rule fails' … That is the wrong conclusion:"*. The rule is kept.
- §14.1 — O2 row *"THE QUESTION IS WITHDRAWN"*, *"Removed from this document"*, *"AMENDED 2026-09-30"*.
- §15 — *"CUT HARD 2026-09-24. An earlier version listed seven measurements, several invented to defend numbers this document has since withdrawn."* The rule is kept.
- §15 — *"WITHDRAWN (recorded so they are not re-invented)"*.
- §16.2 heading — *"the claim was withdrawn, not softened"*.
- §17 — *"`~3 KB` is not the withdrawn `5.6 KB`"*.
- §17 — table row *"the withdrawn 5.6 KB"* / cell *"WITHDRAWN"*.
- §2.1 — *"An earlier draft said '95.6 MB of the 140 MB (~68%)'"*. The arithmetic caution is kept.
- §2.1 — *"CORRECTED TWICE, AND THE TWO CORRECTIONS POINT OPPOSITE WAYS"* with the (i)/(ii) account of what earlier drafts described. The live-column rule and the reason the table matters are kept.
- §4.5 — *"COST — CORRECTED 2026-10-06. An earlier version of this line read 'the cost lever is therefore skipping the vector, not leaving the graph', and that OVERSTATES it."* The measured model is kept.
- §4.5/§12.1 — *"An earlier draft of this line cited a zero  count … that reading is WITHDRAWN"*; the figure is not evidence either way.
- §5 — *"CORRECTED 2026-09-23 … An earlier draft said ~15× blended … derived from §2's wrong 'vectors are ~1/3' figure."* The derivation caution is kept.
- §5 — *"These figures were quoted elsewhere in an earlier draft at amounts up to ~14× higher, and that draft was never reconciled."* The rule (use the table) is kept.
- §8 — *"an earlier draft of this line listed it as one, which invites a lane to implement a type that does not exist"*. The prohibition is kept.
- §9.1 heading — *"destination corrected 2026-10-09 — the owner's own answer, one day after the §2.0 ruling"*.
- §9.1 — *"CORRECTED 2026-10-09"* in the meaning note.
- §9.2 — *"What this corrects in an earlier draft. The earlier version placed … and then discovered …"*. The reason is kept.
- §9.4 — *"destination corrected 2026-10-09"*.

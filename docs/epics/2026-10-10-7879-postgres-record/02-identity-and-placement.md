---
title: "Interface E — the canonical id contract, and the field-level placement table (#7955)"
type: plan
domain: engineering
doc_status: draft
created: 2026-10-11
updated: 2026-10-11
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "issue #7955, epic #7879, interface E, canonical id contract, placement table, stable-name replay keys"
---

# Interface E — the canonical id contract, and the field-level placement table

**Issue:** **#7955** — *§17.5 the field-level PLACEMENT TABLE*. **Lane:** `c6-durability`. **Parent epic:** **#7879**.
**Ruling:** `docs/architecture/STORAGE-ARCHITECTURE.md` §2.0 (owner, 2026-10-08) — **Postgres is the system of record; FalkorDB is a derived hot cache.** §17's five are its consequences, **not new decisions**.
**Scope ruling:** owner, 2026-10-10 — **the only customer is the dogfood instance.** Migration safety apparatus is deliberately absent; this document adds none.
**Interface:** the workstream plan's **E** (`/tmp/plan-storage.md` §2.5) · **units W1 (identity) and W2 (placement)**.
**Test-design consumers:** `#7967` surfaces **S1**, **S3**, **S6** (capstone **S5** is the criterion this table is the evidence for).
**Measured at:** `origin/main` `9201b731b`, 2026-10-11.

> ⚠️ **This is a contract, so it is test-pinnable.** Every claim below that a machine can check is pinned by `tests/test_identity_contract.py`. A claim without a pin is marked as prose.

---

## 0. Plan — before the prose (why this shape)

### 0.1 Confirmed problem

The two-store split cannot be built without two things it does not have: **(a)** a key both stores agree names the same node by, and **(b)** a per-field decision about which store owns each property. The issue states the second as an *enumeration*, and a prior measurement pass produced one (216 names → 185 PRIMARY / 23 DERIVED-VOLATILE / 8 UNRESOLVED, #7955 comment `6097993997`). The first is a genuine gap: **there is no recorded decision about the canonical id anywhere** — not in `STORAGE-ARCHITECTURE.md`, not on the graph (searched 2026-10-11: `canonical id` → the storage-architecture decision only, which *assumes* "the same canonical id as the graph node" without defining it).

The failure this prevents, named concretely: **a rebuilt edge that binds its sibling by a graph-resident id resolves to nothing, in silence** (`#2489`, `#5086`'s *"one interface that must not be broken"*), and a Postgres PK that disagrees with the graph's MERGE key turns every projection into a duplicate-node generator.

### 0.2 Alternatives considered, and why they lose

| Approach | Verdict |
|---|---|
| **A — new global id scheme** (e.g. a single `tortoise:<uuidv7>` minted by a new module, both stores re-key) | **Rejected.** It re-keys ~9,700 existing nodes and every id-holding edge — the *normalisation* work the scope ruling deliberately pushes out of scope. It buys nothing the existing scheme lacks: `ulid()` is already time-sortable, collision-safe and carried by every journal payload. |
| **B — derive the id from content** (hash the node's content) | **Rejected.** An id must be stable under content revision (`PointRevised`), and content-derived ids change on every edit — a rebuilt node would re-key and orphan every edge. Also breaks the `_entity_name_id` case where the id is a function of `name` already. |
| **C — the FalkorDB internal node id as the key** | **Rejected, and it is the defect this contract exists to prevent.** Internal ids die at rebuild (`#2489`); the journal deliberately never carries them. Replay would resolve to nothing. |
| **D — adopt the existing `id` property as canonical, define it precisely, and pin the replay keys** ✅ | **Chosen.** It is the smallest change that makes the interface true: the key already exists and is already journaled; what was missing is the *written contract* and the *enforcement*. Nothing is re-keyed, nothing is migrated, and no public surface changes. |

### 0.3 Chosen approach

1. **Write the canonical id contract** — the key property per label, the mint format, the stability rule, the cross-store rule, and the legacy rule — into this document (§1).
2. **Pin it with a test** (`tests/test_identity_contract.py`) so a violation fails CI rather than a rebuild (§1.6).
3. **Verify the stable-name replay keys against the code**, not against the plan's paraphrase (§1.3) — this produced a correction.
4. **Deliver the placement table per layer**, state the deciding test, and resolve the 8 UNRESOLVED rows (§2).
5. **Carry the `baseline_*` correction** into `STORAGE-ARCHITECTURE.md` §17.5 (§2.8).

### 0.4 Verification

- `tests/test_identity_contract.py` — pins the id format, the replay-key map, and the "never `target.id`" rule (§1.6).
- `uv run python tools/surface_manifest.py check` — proves **no public-surface change** (§1.6).
- The placement table is reconciled against the **EP scalar carve-out** (§2.2) and against the code's own declarations (`_POINT_DENY`, `_GRAPH_EVENT_TYPES`, §3's truth set).

### 0.5 Out of scope (explicitly)

- **Normalising the ~9,700 legacy ids.** Separate unit (W1b), see §1.4.
- **Postgres DDL / pgTAP.** That is `#7968` (the schema) — this contract is what it writes against.
- **No MCP tool or public SDK method is added, removed or renamed.** `tools/surface-guard.py` / `surface_manifest.py` are drift control, not consent; a surface change needs Daniel first.

---

## 1. Interface E — the canonical id contract

### 1.1 The key, per label — one stable key each

The graph does not key every node by one property, and pretending otherwise is what makes a naive Postgres schema duplicate rows. The contract is: **every node has exactly one stable key, and which property it is, is fixed by the label.**

| label | the key property | minted by | why this property |
|---|---|---|---|
| `:Point` | **`id`** | `tortoise/ids.py::ulid()` (or an accepted opaque legacy id) | the MERGE key in `create_point` / `_upsert_point_props`; the journal payload carries it on every event |
| `:Event` | **`eventId`** | caller / `ulid()` | the MERGE key (`MERGE (e:Event {eventId:$eid})`); `id` mirrors it |
| `:Subject` | **`name`** | the caller | the MERGE key — Subjects MERGE by name, and `id` may be a webhook stub ulid (`#1918`) |
| `:Object` | **`name`** | the caller | the MERGE key; `id` is `_entity_name_id(label, name)`, a pure function **of** the name |
| `:Source` | **`url`** | `source_identity.normalize_source_url` (S0a) | the MERGE key — Sources MERGE by canonicalised url; `id` mirrors `url` |

**The rule that follows from the table, and it is the one the schema needs:** a **foreign key is `(label, key)`**, never a bare id. `aboutDocument` is the worked example — it targets a `:Source`, so it resolves by `url`; the same string used as a `:Subject` would be a *different* node. This is why `STRUCTURAL_REL_LABELS` exists (§1.3).

### 1.2 The mint format and the stability rule

**Format (minted `:Point` / `:Event` ids).** `ulid()` returns `<lowercase hex of the millisecond timestamp>-<12 lowercase hex>` — e.g. `1a121bfbe39-5a056891dd27`. It is validated on input by `_is_ulid` (`tortoise/sdk.py`), which accepts the canonical form (`^[0-9a-f]+-[0-9a-f]{12}$`) **and** a standard Crockford base32 ULID (26 chars). Entity handles minted by `_entity_name_id` are `<label[:3]>-<sha256[:26]>` (e.g. `sub-<hex26>`).

**Stability rule — four clauses, all enforceable:**

1. **Minted once, never changed.** An id is assigned at creation and is immutable for the node's lifetime.
2. **A rename is a new node.** A `:Subject`/`:Object`/`:Source` whose key changes is a different node; the old key is not rewritten.
3. **Supersede mints a new Point id and links old→new** (`PointSuperseded`); the old id is never edited, so the reasoning trail survives.
4. **The FalkorDB internal node id is NEVER a key** — it dies at rebuild, and the journal deliberately never carries it (`#2489`). An id is never derived from mutable content.

**Cross-store rule.** The same key names the same node in all three places: the journal carries it in the event payload (the `id` key of every `_GRAPH_EVENT_TYPES` event), Postgres holds it as the primary key of the layer's table (`#7968`), and the cache projection carries it as the node property. The contract exists so that `derived = replay(journal) ∪ preserved_config` (§3) holds *by construction* rather than by luck.

### 1.3 The second half of the same interface — the stable-name replay keys

A rebuilt structural edge must find its sibling by **stable name**, never by a graph-resident id. This is implemented in `tortoise/projection/edges.py` and is **verified against the code** here, not restated from the plan:

- **`STRUCTURAL_REL_LABELS`** maps each derivable structural rel to its **target label**: `aboutSubject→Subject`, `aboutObject→Object`, `aboutEvent→Event`, `aboutPoint→Point`, `aboutDocument→Source`, `extractedFrom→Source`.
- **`stub_key(rel, target)`** returns the replayable `(label, key)` — **`target.get('url')` for `aboutDocument` and `extractedFrom`, `target.get('name')` for all other derivable rels**, and `None` (skip emission) for an empty/non-string key. The function's own docstring states the rule: ***"NEVER `target.id`"***.
- **`resolve_structural_target(g, label, key, rel)`** is the replay-side resolver; it matches label-scoped by `name` (Subject/Event/Point) or `url` (Source), and it is the *single* resolution point (`sdk.py`'s 2b emission and the projection replay both import the same map — no duplicate key map to drift).

**⚠️ Correction to the plan / test-design as written — `coalesce(title, name)` is NOT a current replay key.** The plan §2.5, the test-design map's **S6** row, and `EXTRACTOR-V4` / `STORAGE-ARCHITECTURE` §9.5 all still list the keys as `url` / `name` / `coalesce(title, name)`. That third key is **stale**: it was the **pre-D10** `aboutDocument` key, when a document was a `:Document` resolved by name-or-title. `#2489` moved it to **`url`** when a document became a `:Source` (a `:Source` resolves by `url`), and the code moved with it. Today the code returns `url` for `aboutDocument`, and a title-only target resolves to **nothing** (`stub_key` → `None`). The test at `tests/test_document_source_gold.py::…old_key == "coalesce(title, name)"` pins the **migration fixture's old key**, not the live key — the two are not in conflict, but a reader taking the string as current is wrong.

> **OVERRIDES:** none — this is an application of the recorded D10/`#2489` decision, not a departure from it. The correction is to the *prose that describes* the key, not to the key. (Recorded because §2.5/S6 read as authority to a lane that never opens `edges.py`.)

### 1.4 The legacy ids — the rule that makes the backfill safe later

~9,700 live ids do not match any minted shape: **8,700 `<hex>-<hex>`** (the current `ulid()` form), **1,000 `p-NNNNNNN`** (a legacy sweep format — **no current minter in the repo**), and **~30 `EV-*`** strings used as ids. The contract's position:

> **An id that does not match a minted shape is OPAQUE and VALID.** The contract governs **what new ids look like** and **how any id behaves** (stable · immutable · cross-store · label-keyed). It does **not** require existing ids to match a format, and it does **not** validate ids at read time — a validation gate on legacy data would refuse live rows for a spelling the current code never minted.

**The rule that makes normalising them a safe, later, one-shot migration** (W1b — **not done here**):

1. **The mapping is total and single-valued** — each legacy id maps to exactly one canonical id, because the legacy id is already the node's unique key; the rewrite is a *rename of the key value*, not a merge.
2. **The rewrite is a journal event, not a raw `SET`** — so `replay(journal)` reproduces it and `rebuild == live` still holds (`#5285`).
3. **It is cheap only while the only data is the dogfood instance** — after customers write rows under the old scheme it becomes a client migration, which the owner's launch criterion forbids. That is *why* the scope ruling says it may break the dogfood graph freely.

**Recorded priority:** the workstream plan (§0, §4) filed this at **P2**; the issue's **own correction comment** (`#7955` comment `6099602248`, 2026-10-10T16:18Z) raises it to **P1-severe** — *"the scope ruling removes the urgency of migration safety; it does not license launching with two id schemes."* Either way it is a **separate unit (W1b) and is not done in this deliverable**. The divergence between the plan's P2 and the issue's P1-severe is recorded here rather than silently resolved.

### 1.5 What pins the contract

**`tests/test_identity_contract.py`** — the enforcement, so the contract is real rather than prose:

| test | what it fails on |
|---|---|
| `test_ulid_shape_and_sortability` | `ulid()` drifting from `^[0-9a-f]+-[0-9a-f]{12}$`, or losing time-sortability |
| `test_ulid_and_crockford_accepted_junk_rejected` | `_is_ulid` accepting/rejecting the wrong spellings |
| `test_entity_handle_shape_and_determinism` | `_entity_name_id` drifting from `label[:3]-<hex26>`, or becoming non-deterministic (idempotent-MERGE guarantee) |
| `test_structural_rel_labels_is_the_pinned_map` | a rel added to `DERIVABLE_STRUCTURAL_RELS` without a label, or the label map changing silently |
| `test_stub_key_resolves_by_stable_name_never_id` | `stub_key` returning `target['id']` instead of `name`/`url` — **the exact regression `#2489`/`#5086` exist to prevent** |
| `test_about_document_key_is_url_not_coalesce_title` | a revert to the pre-D10 `coalesce(title, name)` key, which would resolve to nothing on a `:Source` |
| `test_stub_key_skips_unresolvable_keys` | emitting a null-key descriptor where the caller must SKIP |

**The `target.id` pin is the load-bearing one.** It constructs a target `{"id": "<opaque graph id>", "name": "acme"}` and asserts `stub_key("aboutSubject", target) == ("Subject", "acme")` — a resolver that preferred `id` returns `("<opaque>", …)` and fails. No graph connection is needed, so the pin runs in the carve-out lane too.

### 1.6 What this does NOT change

- **No public surface change.** `tests/test_identity_contract.py` imports only existing private helpers; `surface_manifest.py check` is run to prove no MCP tool or public SDK method moved.
- **No legacy backfill** (§1.4).
- **No new id scheme** (§0.2 alternative A rejected).

---

## 2. The field-level placement table

### 2.1 The deciding test, stated precisely — and a contradiction in the document it applies

The issue names the mechanism: **§3's per-write test** — *"if the journal does not carry it, it is not derived — it is PRIMARY, and belongs in a truth-layer table"* (§17.5 quoting §3 at line 346). **But §2.1 of the plan states the inverse** — *"a field is PRIMARY **iff** the journal carries it"*. Read literally the two sentences are logical opposites: one says *non-journaled ⇒ PRIMARY*, the other says *PRIMARY ⇒ journaled*.

**The operative rule is the §3 two-branch one, made explicit** (this is what the table below applies, and the measurement already applied it):

1. **The journal carries the value verbatim** → **PRIMARY** (a record column; replay reconstructs it).
2. **Not carried, but deterministically recomputable from carried data** → **DERIVED** (EP scalars, `content_hash`, declared caches). *Recomputable* is the test, not "not journaled".
3. **Not carried, not recomputable, and a transient stamp/token** whose exact value need not survive replay → **VOLATILE** (a dirty flag, a run token, a best-effort removal marker).
4. **Not carried, not recomputable, and a fact the record must keep** → **PRIMARY-WITHOUT-CARRIER**: it belongs in the record, and the journal must be extended to carry it. This is a §3 **journal-completeness defect** (`#4240`/`#5089` class), **not** a placement of "derived". The table names each one.

Classes 3 and 4 are what §17.5's "9 of 87 derived/volatile" conflates; separating them is what resolves the 8 UNRESOLVED rows (§2.7).

### 2.2 The EP scalar carve-out — reconciled

§2.0's carve-out — **`ep_*`, `confidence`, `posterior_*`, `baseline_*` are recomputed from the structure and never stored as authority** — is applied as written, with **one documented narrowing**:

| name | carve-out says | this table | reconciliation |
|---|---|---|---|
| `ep_alpha`, `ep_beta` | DERIVED | DERIVED | ✓ holds — recomputed by the EP fold |
| `ep_dirty`, `ep_dirty_at` | DERIVED | DERIVED (volatile flag) | ✓ holds |
| `posterior_alpha`, `posterior_beta` | DERIVED | DERIVED | ✓ holds |
| `confidence` | DERIVED | DERIVED | ✓ holds — but see §2.9: the code persists a payload value into it today |
| `baseline_set` | DERIVED | DERIVED (mirror of baseline existence) | ✓ holds |
| **`baseline_source`** | DERIVED | **PRIMARY when `='set-by-author'`; DERIVED otherwise** | ⚠️ **narrowing** — see §2.8 |
| `c_cal`, `lastDreamedAt` | (EP caches) | DERIVED | ✓ holds — correctly-implemented CQRS caches |

The narrowing is **not a reopening of §2.0**: the carve-out's own test is *"recomputed from the structure"*, and an **author-stated prior is not recomputed from anything** — it is the input. §2.0 does not reach it; §3's per-write test classifies it. Applying a decision correctly is not overriding it.

### 2.3–2.6 The table, per layer

**Read the "journal carrier" column as the *why*: it is §3's per-write test, source-cited.** A `PRIMARY` row names the event+key that carries it (or is marked `PRIMARY-WITHOUT-CARRIER`); a `DERIVED` row names the recomputation; a `VOLATILE` row names why the value need not survive replay. Weights are the **floor** the prior pass measured (`/tmp/w2-placement.md`, #7955 comment `6097993997`): the open-set `SET n += $props` passthrough makes the surface unbounded, exactly as §17.5's 87 is a floor.

**Layer assignment.** `Object`+`Subject` → **semantic**; `Point`+operators/edges → **epistemic**; `Event`+`Source`+`Session` → **event stream**. ⚠️ `:Source` is filed under **Semantic** in `ONTOLOGY.md` §5 *and* is part of §3's truth set — the two are **different axes** (what-exists vs who-writes) and do not conflict; it is listed under the event stream here because it is an append-only truth row the journal writes. The **procedural** layer owns **no property name of its own** (§2.6).

**LEGEND:** `P` = PRIMARY · `D` = DERIVED/VOLATILE · `U` = UNRESOLVED (as measured) · `P*` = PRIMARY-WITHOUT-CARRIER (journal it).

#### 2.3 Event stream — `Event` · `Source` · `Session` (100 names · 94 P · 6 D · 0 U)

`Event` (30: 29 P / 1 D) · `Source` (49: 44 P / 5 D) · `Session` (21: 21 P).

*Source-layer note: `Source.reliability`, `reliabilityComponents`, `reliability_derived_at` are a **correctly-implemented CQRS cache** — the pattern the rest of the volatile set should follow; `__runId` is a **transient merge token** (stripped from the journal, best-effort removed) and is not a record field.*

#### 2.4 Semantic — `Object` · `Subject` (23 names · 23 P · 0 D · 0 U)

`Object` (18 P) · `Subject` (5 P).

*All 23 are journaled on `ObjectRegistered` / `SubjectAdded` snapshots; `embedding` on both is recomputed from `name` but is carried on the snapshot, so it is PRIMARY-with-a-recompute (the recompute is an optimisation, not the authority).*

#### 2.5 Epistemic — `Point` · operators/edges (93 names · 68 P · 17 D · 8 U)

`Point` (74: 55 P / 13 D / 6 U) · edges/operators (19: 13 P / 4 D / 2 U).

*This is where the EP scalars live: `ep_alpha`, `ep_beta`, `ep_dirty`, `ep_dirty_at`, `posterior_alpha`, `posterior_beta`, `confidence`, `c_cal`, `lastDreamedAt`, and the four BP `msg_*`/`back_msg_*` slots — all DERIVED per the carve-out. `content_hash` is DERIVED (a pure function of `content`; deliberately stripped from the journal). The 8 UNRESOLVED are resolved in §2.7.*

#### 2.6 Procedural — the fold over the event stream (0 own names)

**The procedural layer owns no property name.** Per `ONTOLOGY.md` §5 it is *"Event + folded `Object.status`"*: `Object.status`, `supersededBy` and `supersededAt` are **DERIVED** (a mechanical fold over the lifecycle `Event`s, `#2193` resolved, keep-first per Object), and they live on the **semantic `Object` row** — counted there in §2.4. The work-state counters a session carries (`turn_count`, `commit_count`, …) live on the **`Session` row** — counted in §2.3. **Reconciliation:** a future schema must not look for a procedural table of its own; the procedural layer is a *view/read*, and its fields are classified where they physically live.

### 2.7 The 8 UNRESOLVED rows — resolved, each with the check that confirms it

The prior pass left 8 names unresolved because their polarity was contestable. Applying §2.1's four classes decides each. **All 8 are resolved by decision; where the decision itself depends on an empirical reading (a live-vs-rebuild diff), that reading is named as the confirmation the schema unit must run before relying on it.**

| # | property | layer | was | **resolved** | why (the test) | confirmation |
|---|---|---|---|---|---|---|
| 1 | `updatedAt` | epistemic (`Point`) | U | **P\*** (journal the write-time) | minted inside the fold (`projection/entities.py:1218` `n.updatedAt=$now`), so replay re-mints → `live ≠ replay`. It is a mutable fact the record must keep, not a recompute and not a stamp the record may lose → §2.1 class 4 | live `update_point` → `rebuild_all` → `DIFF n.updatedAt`; if it differs, add it to the `PointRevised`/`PointAdded` payload |
| 2 | `version` | epistemic (`Point`) | U | **DERIVED** | an ordinal counted by the fold over `PointRevised` — replay reconstructs it **iff** the journal is complete; it is `coalesce(n.version,0)+1`, i.e. a fold counter, not an independent fact | same live-vs-rebuild `DIFF n.version`; a divergence means a `PointRevised` is missing from the journal (`#5089`), not that `version` is a record column |
| 3 | `inherited_at` | epistemic (`Point`) | U | **VOLATILE** | it stamps **when** an inherited baseline was last recomputed (`sdk.py:16097`); like `reliability_derived_at` it stamps a derived recomputation — the value need not survive replay because the recomputation re-stamps it | rebuild → assert the recomputed inherited baselines carry a fresh `inherited_at` (the gate is re-applied, not replayed) |
| 4 | `baseline_source` | epistemic (`Point`) | U | **PRIMARY when `='set-by-author'`; DERIVED otherwise** | §2.8 — the author token records a non-reconstructible input | group by token → rebuild → count; any `set-by-author` row that disappears is the lost input |
| 5 | `baseline_set` | epistemic (`Point`) | U | **DERIVED** | a boolean mirror of "a baseline exists" — derivable from `baseline_source IS NOT NULL` / `ep_alpha IS NOT NULL`; carries no independent information | falls out of #4's check |
| 6 | `temporal_reviewed` | epistemic (`Point`) | U | **P\*** (journal it) | a human review decision (`sdk.py:10090`), a *decision output* with no dedicated event found → §2.1 class 4; `reviewed` rides `PointPromoted`/`DedupeRecorded`, `temporal_reviewed` does not | grep the journal for a carrier after a live mining review; absent ⇒ add it to the mining event (mirror `DedupeRecorded`) |
| 7 | `IMPL.confidence` | epistemic (edges) | U | **PRIMARY** | carried verbatim by `DirectEdgeCreated` (`sdk.py:12988`) — the caller's recorded edge attribute, §2.1 class 1 | already journaled; confirm the replay writes the same value (a naive `DirectEdgeCreated` replay test) |
| 8 | `IMPL.weight` | epistemic (edges) | U | **PRIMARY** | carried verbatim by `DirectEdgeCreated` (`sdk.py:12989`) | as #7 |

**The `IMPL.confidence` / `weight` tension is resolved by *home*, not by polarity:** the *edge attribute* is the caller's input (PRIMARY, on the edge); *EP confidence* is the propagated belief (DERIVED, on the operator). They are different fields with the same word; the schema must not collapse them.

**Final counts:** **216 names → 190 PRIMARY · 26 DERIVED/VOLATILE · 0 UNRESOLVED** (was 185 / 23 / 8). The eight resolutions move **5 to PRIMARY** (`updatedAt`, `baseline_source`, `temporal_reviewed`, `IMPL.confidence`, `IMPL.weight`) and **3 to DERIVED/VOLATILE** (`version`, `inherited_at`, `baseline_set`).

### 2.8 The `baseline_*` correction to §17.5 (as delivered)

> **⛔ CORRECTION — §17.5 lists `baseline_set` / `baseline_source` as unconditionally DERIVED/VOLATILE. They are not: when an author sets the prior (`baseline_source = 'set-by-author'`, the `set_point_baseline` default), the value is a NON-RECONSTRUCTIBLE human/agent input, and §3's own per-write test calls that PRIMARY.**

- **The token family** (`sdk.py`, `#2199`): `set-by-author` — *"NEVER recomputed by `_apply_source_inheritance`"*; `inherited-from-source` — recomputed per EP run; `system-default` — a deterministic default. **Only the first is an input; the other two are recomputable.**
- **Today the value is LOST on rebuild**, and the code says so itself: `sdk.py:16431-16440` — *"the SAME statement also writes `ep_alpha`, `ep_beta`, `baseline_set` and `baseline_source`, and NO journaled event carries those four — they are in `_POINT_DENY` … An author-set baseline is therefore LOST on rebuild (and `_hydrate_evidence` then finds no prior, refusing the next EP run)."*
- **Placement:** `baseline_source` → **PRIMARY when `set-by-author`** (the record must keep the token and the α/β it names); `baseline_set` → **DERIVED** (a mirror). The fix is to **journal the author-set baseline as an explicit input** (the §3 E3 pre-write); once the input is carried, `ep_alpha`/`ep_beta` stay DERIVED as the carve-out says.
- **Not a reopened decision.** The carve-out is for values *"recomputed from the structure"*; an author-stated prior is not. This applies §2.0, it does not override it.

This correction is also recorded on the graph (`CORR-17.5-baseline-author-set-is-primary-7955`) and carried into `STORAGE-ARCHITECTURE.md` §17.5.

### 2.9 Consequential findings the enumeration surfaces

These are **findings, not this unit's work**; each is a §3 invariant violation that the table makes visible, and each belongs to the journal-completeness root (`#4240` / `#5089`):

1. **`confidence` is not actually protected.** It is persisted from the payload by the fixed clause `n.confidence=coalesce($cf, n.confidence)` (`projection/entities.py:1214`) and sits in `_POINT_HANDLED`, **not `_POINT_DENY`** — so the deny-list does not guard a field `ONTOLOGY.md` §2 calls *"derived at read time … never stored independently"*. **Doc-vs-code disagreement, now visible.**
2. **Unjournalled raw `SET`s** (each a live≠rebuild divergence): `Point.ep_dirty`/`ep_dirty_at` and `inherited_at`; `Session.commit_count`/`draft_count`/`value_nodes_created`/`value_nodes_held`/`updated_at` (`hosted_api.py:14262,15092`); `Object.routed_team`/`routed_role`/`routed_product`/`complexity`/`ux_rating` (`connectors/github.py:267`); `Event.embeddingRepairFailedAt` (`sdk.py:24165`).
3. **Point `updatedAt` live≠replay** (§2.7 #1) — same defect class `#5024` fixed for `Source`.
4. **`Source.reliability*`** is the **correct** CQRS pattern (declared, recomputed, never authoritative) — the template the volatile set should converge to.

**The reusable artifact:** the journal-carrier map (`_emit_event`/`_emit` call sites → payload keys) is what lets any future property be classified mechanically — *a key that appears in no journal payload is a PRIMARY-without-carrier candidate.* This document is the enumeration; that map is how it stays current.

---

## Appendix — the raw table, with every writer and carrier

The full per-property table (216 rows, with the `file:line` writer and the journal carrier for each) is reproduced below from the measurement pass that produced the 185/23/8 counts (`/tmp/w2-placement.md`, #7955 comment `6097993997`), grouped by layer.
> ⚠️ **The classes below are the MEASURED (pre-resolution) classes** from the 2026-10-10 pass. The 8 `U` rows are resolved in §2.7 — read the resolution there, not the `U` here. Numbers are a **floor** (the open-set `SET n += $props` passthrough is unbounded).

### Event stream — `Event`

## Event (node) — 30 properties (PRIMARY 29 · DERIVED/VOLATILE 1 · UNRESOLVED 0)

| property | class | evidence (writer) | journal carrier | notes |
|---|---|---|---|---|
| `id` | P | projection/entities.py:3085 props['id']=eid; hosted_api.py:14296 SET e.id | EventRecorded.id | dual of eventId (#122) |
| `eventId` | P | projection/entities.py:3135 MERGE (e:Event {eventId:$eid}) | EventRecorded via id= kwarg | MERGE key |
| `eventKind` | P | projection/entities.py:3086 e.eventKind / $props | EventRecorded.eventKind |  |
| `subject` | P | projection/entities.py:3087 | EventRecorded.subject |  |
| `object` | P | projection/entities.py:3088 | EventRecorded.object |  |
| `startedAt` | P | projection/entities.py:3089 | EventRecorded.startedAt |  |
| `endedAt` | P | projection/entities.py:3090 | EventRecorded.endedAt |  |
| `parentEvent` | P | projection/entities.py:3091 (via $props) | `_EVENT_HANDLED`; no EventRecorded kwarg — open-set |  |
| `participants` | P | projection/entities.py:3092 | EventRecorded.participants |  |
| `classificationLevel` | P | projection/entities.py:3093 | EventRecorded.classificationLevel |  |
| `format` | P | projection/entities.py:3094 | EventRecorded.format |  |
| `embedding` | P | projection/entities.py:3137 e.embedding=CASE..vecf32 | EventRecorded payload via inner.embedding | stored; computed from subject/kind/object if absent |
| `eventStatus` | P | hosted_api.py:14296; entities.py `_event_plain_merge` open-set | open-set passthrough ($props) | lifecycle |
| `name` | P | hosted_api.py:14296 | open-set passthrough |  |
| `summary` | P | hosted_api.py:14296 | open-set passthrough |  |
| `story_arc` | P | hosted_api.py:14296 | open-set passthrough |  |
| `keywords` | P | hosted_api.py:14296 | open-set passthrough |  |
| `capturedAt` | P | hosted_api.py:14296 | open-set passthrough |  |
| `sessionId` | P | hosted_api.py:14296 | open-set passthrough |  |
| `is_episodic` | P | hosted_api.py:14296 | open-set passthrough |  |
| `is_operator` | P | ranking.py:986 | open-set passthrough | DEFENSIVE — a non-Point node carrying a Point flag |
| `session_id` | P | sdk.py:22963 | open-set passthrough |  |
| `source_file` | P | projection/entities.py:3491; meeting guard | guard comparison prop (meeting branch) | §4.2 |
| `source_ref` | P | hosted_api.py:14317 | open-set passthrough |  |
| `updatedAt` | P | hosted_api.py:14296 | open-set passthrough |  |
| `mined_at` | P | mining.py:1242 | mining write (check carrier) |  |
| `mined_hash` | P | mining.py:1242 | mining write (check carrier) |  |
| `embeddingRepairFailedAt` | P | sdk.py:24165 SET e.embeddingRepairFailedAt | repair path raw write (check carrier) | ⚠ raw repair write |
| `confidence` | D | hosted_api.py:14317 | — | EP scalar on Event (should be derived) |
| `content` | P | hosted_api.py:14317 | — | event content |

### Event stream — `Source`

## Source (node) — 49 properties (PRIMARY 44 · DERIVED/VOLATILE 5 · UNRESOLVED 0)

| property | class | evidence (writer) | journal carrier | notes |
|---|---|---|---|---|
| `id` | P | projection/entities.py:3643 s.id=coalesce($id,$url); hosted_api.py:14277 | SourceCreated payload **ev (create_source) | identity |
| `url` | P | projection/entities.py:3643 MERGE (s:Source {url:$url}); edges.py:779 | SourceCreated ev.url | MERGE key |
| `canonicalUrl` | P | projection/entities.py:3644 s.canonicalUrl=$cu; edges.py:151 | SourceCreated ev (S0a/S0b #5012) | identity |
| `urlAliases` | P | projection/entities.py:3645 s.urlAliases=[$raw_url] | SourceCreated ev | identity aliases |
| `sourceKind` | P | projection/entities.py:3646 s.sourceKind=$sk | SourceCreated ev.sourceKind |  |
| `contentHash` | P | projection/entities.py:3647,3676 hash-diff; sdk.py:26429 | SourceCreated/SourceVersioned.contentHash | version anchor |
| `title` | P | projection/entities.py:3648,3677 | SourceCreated ev.title |  |
| `ingestedAt` | P | projection/entities.py:3649 s.ingestedAt=coalesce($ingestedAt,$now) | SourceCreated ev.ingestedAt (#5024) | recorded instant, not replay clock |
| `version` | P | projection/entities.py:3650 s.version=1; 3679 bump; sdk.py:26430 | SourceCreated/SourceVersioned (reproduced by the hash gate) | ordinal reproduced by the SAME hash-diff gate |
| `externalId` | P | projection/entities.py:3651 | SourceCreated ev.externalId |  |
| `format` | P | projection/entities.py:3652 | SourceCreated ev.format | D10: moved onto :Source |
| `sourcePath` | P | projection/entities.py:3653 s.sourcePath=coalesce($sp,..) | SourceCreated ev.source_path | #4.1 |
| `rawState` | P | projection/entities.py:3656 s.rawState=$rawState | SourceCreated ev.rawState (#3998 D30) | availability, third value on the record |
| `rawStateAt` | P | projection/entities.py:3657,3684 | SourceCreated ev.rawStateAt |  |
| `_searchText` | P | projection/entities.py:3658 s.`_searchText`=$st | SourceCreated ev.`_searchText` | fulltext index text |
| `updatedAt` | P | projection/entities.py:3681 coalesce($updatedAt,$now); sdk.py:25312 ev[updatedAt] | SourceCreated/SourceVersioned ev.updatedAt (#5024) | ONE clock minted by the caller |
| `sourceDate` | P | `_SOURCE_EXTRA_PROPS`; create_source(sourceDate=) | SourceCreated ev.sourceDate | evidence-age clock |
| `credibilityTier` | P | `_SOURCE_EXTRA_PROPS`; projection/entities.py:3424 | SourceCreated ev.credibilityTier | #388 |
| `is_episodic` | P | `_SOURCE_EXTRA_PROPS`; projection/entities.py:3424 | SourceCreated ev.is_episodic | quota discriminator |
| `name` | P | `_SOURCE_EXTRA_PROPS` | SourceCreated ev.name |  |
| `team` | P | `_SOURCE_EXTRA_PROPS` | SourceCreated ev.team |  |
| `provenance_spans` | P | `_SOURCE_EXTRA_PROPS` | SourceCreated ev.provenance_spans |  |
| `domain` | P | `_SOURCE_EXTRA_PROPS` (D10 B6) | SourceCreated ev.domain |  |
| `summary` | P | `_SOURCE_EXTRA_PROPS`; hosted_api.py:14277 | SourceCreated ev.summary |  |
| `topics` | P | `_SOURCE_EXTRA_PROPS` | SourceCreated ev.topics |  |
| `status` | P | `_SOURCE_EXTRA_PROPS` (D10 B6) | SourceCreated ev.status |  |
| `embedding` | P | `_SOURCE_EXTRA_PROPS` | SourceCreated ev.embedding | vector retrieval leg |
| `about_entities` | P | `_SOURCE_EXTRA_PROPS` | SourceCreated ev.about_entities |  |
| `documentKind` | P | `_SOURCE_EXTRA_PROPS`; hosted_api.py:14277 | SourceCreated ev.documentKind | D10 document-is-a-Source |
| `document_kind` | P | `_SOURCE_EXTRA_PROPS` | SourceCreated ev.document_kind |  |
| `sessionId` | P | `_SOURCE_EXTRA_PROPS`; `_SOURCE_NODE_PROPS` | SourceCreated ev.sessionId | capture writer |
| `capturedAt` | P | `_SOURCE_NODE_PROPS` | SourceCreated ev.capturedAt | session-capture writer |
| `eventId` | P | `_SOURCE_NODE_PROPS`; hosted_api.py:14277 | SourceCreated ev.eventId |  |
| `session_id` | P | `_SOURCE_NODE_PROPS` | SourceCreated ev.session_id |  |
| `event_id` | P | `_SOURCE_NODE_PROPS` | SourceCreated ev.event_id |  |
| `source_path` | P | `_SOURCE_NODE_PROPS` | SourceCreated ev.source_path |  |
| `needs_extraction` | P | `_SOURCE_NODE_PROPS`; ingest.py:271 | SourceCreated ev.needs_extraction | read back by ingest |
| `story_arc` | P | `_SOURCE_NODE_PROPS`; hosted_api.py:14277 | SourceCreated ev.story_arc |  |
| `document_knowledge_domain` | P | `_SOURCE_NODE_PROPS` | SourceCreated ev.document_knowledge_domain |  |
| `authored_by` | P | `_SOURCE_NODE_PROPS` | SourceCreated ev.authored_by | document provenance metadata |
| `owned_by` | P | `_SOURCE_NODE_PROPS` | SourceCreated ev.owned_by |  |
| `managed_by` | P | `_SOURCE_NODE_PROPS` | SourceCreated ev.managed_by |  |
| `governing_agreement` | P | `_SOURCE_NODE_PROPS` | SourceCreated ev.governing_agreement |  |
| `createdAt` | P | `_SOURCE_NODE_PROPS` | SourceCreated ev.createdAt |  |
| `__runId` | D | projection/entities.py:3660 s.__runId=$rid transient merge token | NEVER journalled (stripped, sdk.py:25380 excludes __runId) | transient merge token, removed best-effort — not a record field |
| `reliability` | D | sdk.py:25967 `_write_reliability_cache` | NOT carried | documented CQRS cache (#398) — recomputed |
| `reliabilityComponents` | D | sdk.py:25967 | NOT carried | cache |
| `reliability_derived_at` | D | sdk.py:25967 | NOT carried | cache timestamp |
| `previousContentHash` | D | sdk.py SourceVersioned kwarg | SourceVersioned.previousContentHash (event key, NOT a node prop) | `_SOURCE_HANDLED` excludes it — describes the transition, not the node |

### Event stream — `Session`

## Session (node) — 21 properties (PRIMARY 21 · DERIVED/VOLATILE 0 · UNRESOLVED 0)

| property | class | evidence (writer) | journal carrier | notes |
|---|---|---|---|---|
| `id` | P | sdk.py:2317 MERGE (s:Session {id:$sid}); projection/entities.py:1819 | SessionRecorded.id | MERGE key |
| `created_at` | P | sdk.py:2244 s.created_at=coalesce(s.created_at,$now); projection/entities.py:1758 | SessionRecorded.created_at | first-writer-wins |
| `turn_count` | P | sdk.py:2245 s.turn_count=$tc; projection/entities.py:1811 | SessionRecorded.turn_count | latest capture wins |
| `is_episodic` | P | sdk.py:2246 s.is_episodic=true; projection/entities.py:1817 | SessionRecorded fold hardcodes true | session container flag (ONTOLOGY §4.5) |
| `harness` | P | sdk.py:2248 s.harness=$harness; projection/entities.py:1811 | SessionRecorded.harness | #1727 first-writer-wins |
| `capture_lane` | P | sdk.py:2256 s.capture_lane=coalesce(..); projection/entities.py:1768 | SessionRecorded.capture_lane (#3516) | producer lane |
| `client_captured_at` | P | sdk.py:2277 s.client_captured_at=coalesce(..); projection/entities.py:1802 | SessionRecorded.client_captured_at | #3516 client clock |
| `client_captured_at_source` | P | sdk.py:2272 CASE; projection/entities.py:1797 | SessionRecorded.client_captured_at_source | clock identity pair |
| `actor_user_id` | P | sdk.py:2282 s.actor_user_id=coalesce(..); projection/entities.py:1821 | SessionRecorded.actor_user_id (#2600) |  |
| `machine_id` | P | sdk.py:2286 s.machine_id=coalesce(..) | SessionRecorded.machine_id (hosted-only, #2599) |  |
| `model` | P | sdk.py:2289 s.model=coalesce(..) | SessionRecorded.model |  |
| `capture_redactions` | P | sdk.py:1758 SET s.capture_redactions; projection/entities.py:1811 | SessionRecorded.capture_redactions (#4911) | overwrite semantics |
| `entity_links_attempted` | P | sdk.py:6821 SET; projection/entities.py:1811 | SessionRecorded.entity_links_attempted (#3664) |  |
| `entity_links_created` | P | sdk.py:6821; projection/entities.py:1811 | SessionRecorded.entity_links_created |  |
| `capture_ok` | P | hosted_api.py:12218; projection/entities.py:1811 | SessionRecorded.capture_ok (#3722) | retry gate input |
| `capture_extractor` | P | hosted_api.py:12218; projection/entities.py:1811 | SessionRecorded.capture_extractor |  |
| `commit_count` | P | hosted_api.py:14262 SET s.commit_count=coalesce+1 | NO SessionRecorded carrier found | ⚠ raw SET, unjournalled → lost on rebuild |
| `draft_count` | P | hosted_api.py:14262 | NO SessionRecorded carrier found | ⚠ raw SET, unjournalled |
| `value_nodes_created` | P | hosted_api.py:14262 | NO SessionRecorded carrier found | ⚠ raw SET, unjournalled |
| `value_nodes_held` | P | hosted_api.py:15092 | NO SessionRecorded carrier found | ⚠ raw SET, unjournalled |
| `updated_at` | P | hosted_api.py:14262 SET s.updated_at=$now | NO SessionRecorded carrier found | ⚠ raw SET, unjournalled |

### Semantic — `Object`

## Object (node) — 18 properties (PRIMARY 18 · DERIVED/VOLATILE 0 · UNRESOLVED 0)

| property | class | evidence (writer) | journal carrier | notes |
|---|---|---|---|---|
| `id` | P | projection/entities.py:2661 ON CREATE o.id=$id; commit_ops.py:916 | ObjectRegistered.id | identity |
| `name` | P | projection/entities.py:2661 MERGE (o:Object {name:$name}) | ObjectRegistered.name | MERGE key; #1155 |
| `objectKind` | P | projection/entities.py:2661 o.objectKind=coalesce($ok,'other') | ObjectRegistered.object_kind |  |
| `createdAt` | P | projection/entities.py:2661 o.createdAt=coalesce($ca,$now) | ObjectRegistered.createdAt | #2295/#2194 |
| `title` | P | projection/entities.py:2661 o.title=coalesce($title,'') | ObjectRegistered (open-set snapshot) |  |
| `embedding` | P | projection/entities.py:2661 o.embedding=CASE..vecf32; #4524 REMOVE | computed/recomputed from name (#7845) | derived-from-name but stored; not journaled as a payload key on ObjectRegistered → recomputable (name is primary) |
| `status` | P | projection/entities.py:2661 o.status=coalesce($st,'live'); `_fold_object_superseded` 2730 | ObjectRegistered.status; ObjectSuperseded (folded) | §3: mechanical fold over lifecycle events; mutable in place (safe because recomputable) |
| `supersededBy` | P | projection/entities.py:2730 SET o.supersededBy=$sb | ObjectSuperseded.supersedes_by | #1350 |
| `supersededAt` | P | projection/entities.py:2731 SET o.supersededAt=$sa | ObjectSuperseded (fold timestamp) |  |
| `canonical_name` | P | sdk.py:9773 (read/alias match) — writer via open-set | open-set passthrough | topic alias |
| `repo` | P | sdk.py:25064 MERGE (o:Object) SET o.repo=$repo | connector write (check journal carrier) | github entity field |
| `issue_number` | P | sdk.py:25064 SET o.issue_number=$issue_number | connector write (check) | github entity field |
| `url` | P | sdk.py:25064 SET o.url=$url | connector write (check) | github entity field |
| `routed_team` | P | connectors/github.py:267 SET o.routed_team | raw connector write — NO journal carrier | ⚠ raw SET, unjournalled (#4240 class) |
| `routed_role` | P | connectors/github.py:267 | raw connector write — NO journal carrier | ⚠ raw SET, unjournalled |
| `routed_product` | P | connectors/github.py:267 | raw connector write — NO journal carrier | ⚠ raw SET, unjournalled |
| `complexity` | P | connectors/github.py:267 | raw connector write — NO journal carrier | ⚠ raw SET, unjournalled |
| `ux_rating` | P | connectors/github.py:267 | raw connector write — NO journal carrier | ⚠ raw SET, unjournalled |

### Semantic — `Subject`

## Subject (node) — 5 properties (PRIMARY 5 · DERIVED/VOLATILE 0 · UNRESOLVED 0)

| property | class | evidence (writer) | journal carrier | notes |
|---|---|---|---|---|
| `id` | P | projection/entities.py:2585 s.id=$id / coalesce; edges.py:104 stub | SubjectAdded.id | #1918 canonical id wins |
| `name` | P | projection/entities.py:2585 MERGE (s:Subject {name:$name}) | SubjectAdded.name | MERGE key |
| `subjectKind` | P | projection/entities.py:2585 s.subjectKind=coalesce($sk,'other') | SubjectAdded.subject_kind |  |
| `createdAt` | P | projection/entities.py:2585 s.createdAt=coalesce($ca,$now) | SubjectAdded.createdAt | #2295 |
| `embedding` | P | projection/entities.py:2585 s.embedding=CASE..vecf32; #4524 REMOVE | computed from name (#7845) | recomputable from name (primary) |

### Epistemic — `Point`

## Point (node) — 74 properties (PRIMARY 55 · DERIVED/VOLATILE 13 · UNRESOLVED 6)

| property | class | evidence (writer) | journal carrier | notes |
|---|---|---|---|---|
| `id` | P | projection/entities.py:1163 MERGE (n:Point {id:$id}); sdk.py create_point CREATE map | PointAdded.point.id / PointPromoted.point.id / OperatorAdded.point.id | identity / MERGE key |
| `content` | P | projection/entities.py:1166 n.content=$content | PointAdded.point.content (full snapshot) | §3: JOURNAL — decision output |
| `is_operator` | P | projection/entities.py:1167 n.is_operator=$isop | PointAdded.point.is_operator | identity flag; #5048 |
| `op_type` | P | projection/entities.py:1168 n.op_type=$opt | OperatorAdded.point.op_type | operator kind |
| `pointKind` | P | projection/entities.py:1169 n.pointKind=coalesce($pk,..) | PointAdded.point.pointKind | kind |
| `status` | P | projection/entities.py:1170 n.status=coalesce($st,..,'live'); sdk.py:8410 | PointAdded.point.status, PointRevised, PointPromoted, PointRetracted | lifecycle, journaled by dedicated events |
| `authoredBy` | P | projection/entities.py:1171 n.authoredBy=coalesce($ab,..) | PointAdded.point.authoredBy (`_META_KEY` handled as edge) |  |
| `confidence` | D | projection/entities.py:1214 n.confidence=coalesce($cf,..); ep.py:199; dream.py:272 | ConfidenceChanged {confidence}; ALSO written from PointAdded payload | §17.5 + §3: EP scalar 'never stored independently'. ⚠ TENSION: the fixed clause DOES persist a payload value (coalesce), so the code stores what the doc calls derived |
| `createdAt` | P | projection/entities.py:1215 n.createdAt=coalesce($ca,..,$now) | PointAdded.point.createdAt |  |
| `validFrom` | P | projection/entities.py:1216 | PointAdded.point.validFrom; PointSuperseded.valid_from |  |
| `validTo` | P | projection/entities.py:1217; sdk.py:8762 | PointSuperseded.valid_to; PointInvalidated.valid_to |  |
| `updatedAt` | U | projection/entities.py:1218 n.updatedAt=$now | NOT carried — replay re-mints via `_now_iso`() | replay clock, not recorded instant → live≠replay; journal it or accept volatile |
| `embedding` | P | projection/entities.py:1172/CASE,1244 REMOVE; sdk.py:5140 stamp_journal_embedding | PointAdded.point.embedding + embedding_model/revision/text_hash (#5004) | §3: JOURNAL IT — re-embed is a re-run not a replay |
| `embedding_verbatim` | P | projection/entities.py:1176 n.embedding_verbatim | PointAdded.point.embedding_verbatim | caller-owned vector storage form |
| `content_hash` | D | projection/entities.py:1213 n.content_hash=coalesce($ch,..) via `_content_hash`() | STRIPPED from journal: sdk.py:5140 `if k != 'content_hash'` | §3: RECOMPUTE — pure function of content (#2795 D2) |
| `direction` | P | projection/entities.py:1310 (op branch) n.direction=$dir | OperatorAdded.point.direction | operator edge direction |
| `label` | P | projection/entities.py:1312 n.label=$label | OperatorAdded.point.label | operator domain verb |
| `extractedFrom` | P | projection/entities.py:1348 SET n.extractedFrom=$ref | PointAdded.point.extractedFrom (edge authoritative) | query-convenience scalar; edges authoritative |
| `provenanceSource` | P | projection/entities.py:1352 SET n.provenanceSource=$sid | PointAdded.point.provenance.source_id | P1-2 cached provenance id |
| `is_episodic` | P | projection/entities.py:1330 n.is_episodic=$episodic | PointAdded.point.is_episodic | server-managed quota discriminator (#1486) |
| `sourceVersionTransit` | P | projection/entities.py:1304 n.sourceVersionTransit=$sv | PointAdded.point.sourceVersionTransit (#5256) | replay carrier for extractedFrom version |
| `mitigation_strength` | P | sdk.py:10582 CREATE (m:Point {.., mitigation_strength:$s}); `projection/__init__.py`:11119 n.mitigation_strength | OperatorAdded.point.mitigation_strength (open-set) | #2315 graded dampener; edge read |
| `batch_id` | P | sdk.py:12678 / `projection/__init__.py`:8387 SET n.batch_id; mining.py:312 | BatchIdStamped (JSONL-only record) | #902 A4; restored by pass-2b |
| `version` | U | sdk.py:8413/8422 n.version=coalesce(n.version,0)+1 | not carried on PointRevised | counter; replay increments from a different base → divergence candidate |
| `promotedAt` | P | sdk.py:9500 SET n.promotedAt=$now | PointPromoted.point.promotedAt (full snapshot) | #785 reviewer-gated promotion |
| `reviewed` | P | sdk.py:9500 SET n.reviewed=true; 10091/10107 | PointPromoted.point.reviewed; DedupeRecorded | DERIVED reviewer flag per code comment, but rides the journaled snapshot → in record |
| `outdated` | P | sdk.py:8761 SET n.outdated=true; projection/entities.py:2323 | PointInvalidated {expired_at,valid_to}; PointSuperseded | lifecycle terminal flag; deny-listed from PointAdded passthrough |
| `expiredAt` | P | sdk.py:8762/9312; projection/entities.py:2324 | PointInvalidated.expired_at; PointSuperseded.expired_at |  |
| `search_keys` | P | `projection/__init__.py`:10233 SET n.search_keys=$flat | PointAdded.point.search_keys (declared list prop) | declared payload prop; the FLATTENED value is a pure function |
| `speaker` | P | sdk.py:1758; `_POINT_DECLARED_PROPS` | PointAdded.point.speaker | declared payload prop |
| `quote` | P | `_POINT_DECLARED_PROPS` | PointAdded.point.quote | declared payload prop |
| `when` | P | `_POINT_DECLARED_PROPS` | PointAdded.point.when | declared payload prop |
| `source_turn_id` | P | `_POINT_DECLARED_PROPS` | PointAdded.point.source_turn_id | declared payload prop |
| `tags` | P | sdk.py create_point `SET n += $props` (raw list); `_POINT_LIST_PROPS` empty | PointAdded.point.tags | #2897 — raw-list half-restore refused on replay (parity gap) |
| `span_start` | P | `_POINT_DECLARED_PROPS` (#5007 E4) | PointAdded.point.span_start | verbatim span pointer |
| `span_end` | P | `_POINT_DECLARED_PROPS` (#5007 E4) | PointAdded.point.span_end | verbatim span pointer |
| `annotator_bias` | P | `_POINT_DECLARED_PROPS` (#3689); OperatorAnnotated.bias | PointAdded.point.annotator_bias; OperatorAnnotated |  |
| `annotator_precision` | P | `_POINT_DECLARED_PROPS` (#3689) | PointAdded.point.annotator_precision; OperatorAnnotated.precision |  |
| `annotator_consistency` | P | `_POINT_DECLARED_PROPS` (#3689) | PointAdded.point.annotator_consistency; OperatorAnnotated.consistency |  |
| `annotator_directness` | P | `_POINT_DECLARED_PROPS` (#3689) | PointAdded.point.annotator_directness; OperatorAnnotated.directness |  |
| `goalState` | P | `_POINT_DECLARED_PROPS` (#7856) | PointAdded.point.goalState | closed vocabulary, separate from status |
| `source_harness` | P | hosted_api.py:13044 | open-set passthrough on PointAdded snapshot | capture provenance prop |
| `source_session` | P | hosted_api.py:13044 | open-set passthrough | capture provenance prop |
| `ingested_at` | P | hosted_api.py:13044 | open-set passthrough |  |
| `eventId` | P | hosted_api.py:13044 | open-set passthrough | link to Event |
| `sourceDate` | P | audit.py:364 | open-set passthrough | evidence-age clock |
| `assessor` | P | sdk.py:25904 | open-set passthrough |  |
| `targetSource` | P | sdk.py:25703 | open-set passthrough |  |
| `grounding` | P | projection/grounding.py:83 SET n.grounding=$g | open-set passthrough |  |
| `lme_question_id` | P | session_reinjection.py:1 | open-set passthrough | eval-lane tag |
| `dedup_candidate` | P | sdk.py:9950 | DedupeRecorded.point | #784 |
| `dedup_method` | P | sdk.py:9950 | DedupeRecorded.point | #784 |
| `dedup_similarity` | P | sdk.py:9950 | DedupeRecorded.point | #784 |
| `dedup_target_id` | P | sdk.py:9950 | DedupeRecorded.point | #784 |
| `dedup_reviewed` | P | sdk.py:10107 | open-set / DedupeRecorded |  |
| `temporal_candidate` | P | mining.py:566 | mining record (check)  | temporal-mining family |
| `temporal_target_ids` | P | mining.py:567 | mining record (check) |  |
| `temporal_target_id` | P | mining.py:568 | mining record (check) |  |
| `temporal_replacement` | P | mining.py:569 | mining record (check) |  |
| `temporal_reviewed` | U | sdk.py:10090 SET n.temporal_reviewed, n.reviewed=true | no dedicated event found | confirm whether a journal carrier exists |
| `lastDreamedAt` | D | dream.py:272 SET n.lastDreamedAt=$now | ConfidenceChanged via `_journal_belief_writeback` (lastDreamedAt) | EP-owned recompute stamp; deny-listed |
| `ep_alpha` | D | ep.py:199 SET n.ep_alpha; sdk.py:15666 | NOT carried (fold drops; `_POINT_DENY`) | §17.5 |
| `ep_beta` | D | ep.py:199 SET n.ep_beta; sdk.py:15666 | NOT carried | §17.5 |
| `baseline_set` | U | sdk.py:15666 SET n.baseline_set=true | NOT carried — sdk.py:15733 states the 4 baseline props are in `_POINT_DENY` and lost on rebuild | set-by-author prior is a non-reconstructible INPUT (§3 test ⇒ PRIMARY), inherited/system-default are recomputable (⇒ DERIVED). §17.5's blanket 'derived' is wrong for the first |
| `baseline_source` | U | sdk.py:15666 SET n.baseline_source=$src | NOT carried | same as baseline_set — separates set-by-author from inherited/system-default |
| `posterior_alpha` | D | ep.py:199 SET n.posterior_alpha; sdk.py:15744 | ConfidenceChanged.posterior_alpha | §17.5; recomputed posterior |
| `posterior_beta` | D | ep.py:199 SET n.posterior_beta; sdk.py:15744 | ConfidenceChanged.posterior_beta | §17.5; recomputed posterior |
| `ep_dirty` | D | sdk.py:14351 SET n.ep_dirty=null; `_mark_dirty` | NOT journalled (#5166, stated sdk.py:10564) | §17.5; volatile dirty flag |
| `ep_dirty_at` | D | sdk.py:14351 SET n.ep_dirty_at=null | NOT journalled (#5166) | §17.5; volatile dirty timestamp |
| `inherited_at` | U | sdk.py:16097 SET n.inherited_at=$ts | NOT carried | per-run time gate for inherited baselines — an input to a decision, not a pure recompute; decide journal-vs-derive |
| `c_cal` | D | `_POINT_DENY` (calibration cache; written by calibration path) | deny-listed from passthrough | EP calibration cache |
| `_nid` | D | `_POINT_DENY` / projection replay bookkeeping | never a payload prop | replay bookkeeping, not a record field |
| `_graph_id` | D | `_POINT_DENY` / projection replay bookkeeping | never a payload prop | replay bookkeeping |
| `reason` | D | `_POINT_DENY` (D4) | deny-listed | dropped by design |

### Epistemic — operators / edges

## Edges / operators (IMPL/NAND/INPUT/references/extractedFrom/mitigated_by/about*) — 19 properties (PRIMARY 13 · DERIVED/VOLATILE 4 · UNRESOLVED 2)

| property | class | evidence (writer) | journal carrier | notes |
|---|---|---|---|---|
| `IMPL.idx` | P | sdk.py:12492/10411 CREATE (o)-[:IMPL {idx:$i}]; projection/edges.py:644 | OperatorAdded (edge re-created by fold from inputs+idx) | position index; replayed from OperatorAdded.inputs |
| `NAND.idx` | P | same as IMPL | OperatorAdded |  |
| `INPUT.idx` | P | projection/edges.py:648/718 CREATE (s)-[:INPUT {idx:$i}]->(o) | OperatorAdded.inputs order |  |
| `IMPL.direction` | P | sdk.py:12986 create_direct_edge SET r += attrs{direction} | DirectEdgeCreated.direction; DirectEdgeRepoint.direction |  |
| `IMPL.confidence` | U | sdk.py:12988 attrs['confidence']=float(confidence) | DirectEdgeCreated (confidence kwarg) | EP also derives confidence → stored raw edge confidence may conflict with EP; classify |
| `IMPL.weight` | U | sdk.py:12989 attrs['weight']=float(weight) | DirectEdgeCreated.weight | same tension as confidence |
| `IMPL.label` | P | sdk.py:12990 attrs['label']=label | DirectEdgeCreated.label |  |
| `IMPL.batch_id` | P | sdk.py:12991 attrs['batch_id']=batch_id | DirectEdgeCreated.batch_id; BatchIdStamped |  |
| `NAND.direction/confidence/weight/label/batch_id` | P | same writers with rel_type=NAND | DirectEdgeCreated |  |
| `IMPL.msg_alpha` | D | ep.py:241/333 SET r.msg_alpha=p.a | NOT carried | BP message — recomputed each EP run; REMOVE'd by invalidate_messages |
| `IMPL.msg_beta` | D | ep.py:241/333 | NOT carried | BP message |
| `IMPL.back_msg_alpha` | D | ep.py:256/366 SET r.back_msg_alpha | NOT carried | BP back-message |
| `IMPL.back_msg_beta` | D | ep.py:256/366 | NOT carried | BP back-message |
| `references.sourceVersion` | P | projection/edges.py:358 `_anchor_on_create`; edges.py:1010/1043/1063 | SourceCreated/SourceVersioned (sourceVersionTransit on the Point snapshot) | #5199 version anchor |
| `extractedFrom.sourceVersion` | P | projection/edges.py:892/927 `_link_source` | Point.sourceVersionTransit (#5256) | replay passes the Point's own transit |
| `mitigated_by.*` | P | projection/edges.py:710 MERGE (s)-[:mitigated_by]->(o) — no props | OperatorAdded.point.mitigation_strength | no edge properties |
| `aboutSubject/aboutObject/aboutEvent/aboutPoint/aboutDocument.*` | P | projection/edges.py:754/`_create_about_edges` — no props | about_entities / about* keys on the snapshot | no edge properties |
| `CONTAINS.*` | P | sdk.py turn loop MERGE (s)-[:CONTAINS]->(t) — no props | PointAdded envelope contains_session (#3947) | no edge properties |
| `authoredBy/ownedBy/managedBy/performs/produces/uses/participatesIn/TAGGED.*` | P | projection/entities.py, `_META_KEYS` handled as edges — no props | event.subject/object/uses/participants; about_entities | no edge properties |

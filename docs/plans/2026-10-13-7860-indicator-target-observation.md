<!-- research-path: none — prior research lives in the issue comments (see "Prior Research Intake") -->

# Indicator / Target / Observation — Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Give the graph a first-class, general **`indicator`** (an Object: a measured quantity, no pass/fail), an optional inline **`target`** composite on it (0..*), a journaled goal→indicator link, and derived-on-read `currentValue` / `targetAssessment` / derived goal state — so a goal's progress outside software engineering can be held and read from the graph.

**Team:** organisation-design-team (issue carries no `**Team:**`; `AGENT_SESSION_TEAM` unset → default per `writing-plans/04`)
**Role:** omit — `AGENT_SESSION_ROLE` unset

**Architecture:** Three layers and a boundary. `indicator` is an **Object kind**; a `target` is an **inline composite field on that Object** (0..*, never a kind, never a node); a `measurement` is an **Event**; `currentValue` / `targetAssessment` / the derived goal state are **computed on read and never stored**. The goal→indicator *and* measurement→indicator links are **layer-3 labels on operators** (`create_operator(..., label=...)`) — not new predicates — per the #7852 three-layer model. ⛔ **Not `aboutObject`:** measured on this tree, an `aboutObject` edge is **not journaled and does not survive `rebuild_all`**, while the operator link does (see Grounding G6/G7).

---

## Prerequisite check — record, do not paper over

The `writing-plans` pre-flight requires `<!-- issue-scoping:` or `<!-- issue-planning:` in the issue. **It is absent.** The issue carries only the `scoping` label and eight substantive design comments. `issue-scoping` cannot be re-run retrospectively (its output is already superseded by the comments). Proceeding is a **deliberate, recorded departure** — the *substance* the signature exists to guarantee (requirements, priorities, rejected alternatives, contradiction-test resolution) is present in the comments, and `## The settled design` below pins it. Flagged for the owner (O7); if a scoping comment is required for pipeline-compliance, it must be authored retroactively.

---

## The settled design (authoritative sources, latest wins)

Sources in precedence order: the issue body → comments 0–3 (design + precedents) → comment 4 (contradiction test) → comments 5–6 (owner rulings, 2026-10-11) → **comment 7 (`Research synthesis (2026-10-11)`) is authoritative where they differ.**

1. **Three layers, one derived, a boundary.** `indicator` (Object kind) · `target` (inline composite field on the indicator, `0..*`) · `measurement` (an Event) · and **derived on read, never stored** — `currentValue` / `targetAssessment` / derived goal state.
2. **`indicator` is an OBJECT.** Issue **#7902 is CLOSED** (`tortoise/live.py:318` `EPISTEMIC_LABELS = ("Point","Subject","Object","Event")`; `sdk.py:11027`/`11082`) — an Object↔Object (and Event↔Object) link via `create_operator` is **journaled and durable** (verified — G6/G7).
3. **`metric` must NOT be minted.** The owner ruled **metric ≡ indicator**; a second kind is the #2786 two-vocabularies disease.
4. **Both links are LAYER-3 LABELS, not new predicates.** ⛔ `_VALID_EDGE_PREDICATES` (`tortoise/projection/edges.py:247`, **22 entries, verified**) is **not** touched; `EntityLinked` is **not** widened. The flat allowlist that treats labels as mechanism *is itself* the defect tracked by #2766.
5. **Parent-state derivation = a 4-declaration set + `temporal_pattern`.** **Automatic rollup is NOT adopted** — the contradiction test forbids making it a default (it would contradict the owner's #6792 ruling that objectives are qualitative and indicators are used only where useful).
6. **Numeric values must be EXTENDED from Points to Objects.** WS-C (`docs/plans/2026-09-10-ws-c-numeric-normalization.md` §2/§2.4, OD1) declares a value as a **flat structural field on an entity**, **scalars only** — FalkorDB has **no map type and no decimal type**; money as **integer minor units** with the exponent stored; **never a binary float**.
7. **Thin target history.** Keep the *current* target cheap. No speculative versioning; an append-only `TargetChange` event only when a stakeholder asks "what was the target then".

### Chosen approach

| Layer | Choice | Where it lives |
|---|---|---|
| `indicator` | **CORE Object kind** | `tortoise/pack_registry.py:43`, `tortoise/extractor_v2.py:405`, `tortoise/value_extractor.py:~212`, `docs/ONTOLOGY.md` §5 Object block ⛔ (owner-only) |
| `target` | **inline composite, `0..*`**, parallel scalar arrays on the indicator Object (no node) | `tortoise/indicators.py` (new) + the Object open-set passthrough (`_upsert_object` → `_persist_extra_props`, `entities.py:3368`/`1394`) |
| `measurement` | **CORE Event kind** | `tortoise/pack_registry.py:67`, `tortoise/commit_schema.py:134`, `docs/ONTOLOGY.md` §5 Event block ⛔ (owner-only) |
| goal→indicator **and** measurement→indicator links | **layer-3 label on an operator** (journaled, rebuild-durable) | `tortoise/sdk.py:10822` `create_operator` (unchanged) + `packs/product-strategy/manifest.yaml` `relations` (`declarationOnly: true`) |
| derived values | pure functions, derived on read | `tortoise/indicators.py` (new) |

### Alternatives REJECTED (carried so the next reader does not re-propose them)

| # | Alternative | Why rejected |
|---|---|---|
| A1 | **A single `target_float` on the indicator** | **12 of 12** real specs that model a target keep it a **multi-field inline composite** (OpenSLO `op`+`value`, Datadog `target`+`warning`+`timeframe`, FHIR `referenceRange`). A bare scalar cannot express comparator/unit/window. Comment 7, Amendment 1. |
| A2 | **`target` as its own kind / node** | `core:target` already means *"a target state (commitment-state family)"* (`tortoise/value_extractor.py:213`) — reuse collides with a different meaning; a node reifies a *condition*, doubles node count, needs an embedding/index it does not earn, and is not a claim. Comment 6C; comment 7 ("12 of 12 keep it inline"). |
| A3 | **Automatic rollup of child state into the parent** (MIN / weighted average) | **Contradiction test fired.** It contradicts #6792. OMG BMM declines to derive at all; GRL leaves softgoals to judgment; ITU-T Z.151 imposes **no propagation algorithm** and rules a declared value is **never overridden** by the computed one. Adopt the declaration set; `MANUAL` is the default mode. Comment 7 §2. |
| A4 | **New predicates `measuredBy` / `conditionsOn` in `_VALID_EDGE_PREDICATES`, plus an `EntityLinked` widening** | **Contradiction test fired.** #7852 makes `measuredBy` a **layer-3 label** — the machine does not *act* on it — so it needs **no** vocabulary growth. Widening `EntityLinked` re-scopes an about*-capture contract. Comment 4. |
| A5 | **Mint `metric` as a new kind** | Owner ruled metric ≡ indicator. A second kind for one concept reproduces #2786. Comment 2 §3. |
| A6 | **Declare `indicator` / `measurement` as product-strategy pack kinds** (avoids the ONTOLOGY.md edit) | #7848's owner ruling ("ONE declaration, as an Object kind") and the sanctioned-same-name precedent (`dev:issue ≡ pm:issue`) both apply — but a *second* declaration alongside `dev:indicator` re-creates the ambiguity the issue is about, and a measured value is **general**. Recorded as the **fallback if the owner declines O1**. |
| A7 | **Target versioning now** (`TargetChange` event / Type-2 window) | Nobl9 makes a change destructive; Datadog takes `target` as a query parameter rather than storing versions; no stakeholder has asked. Thin posture per comment 7 §1. |
| A8 | **Store the assessment / materialize `lastValue`** | §11 fold-cache doctrine + the second-source-of-truth defect class this track exists to remove (comment 1: Wikidata's stored rank is the counter-example; keep *deriving*). |
| A9 | **Use `create_event(aboutObject=[indicator_id])` to attach an observation to its indicator** | ⛔ **MEASURED — this silently loses the link.** `create_event(aboutObject=<name>)` wires `(Event)-[:aboutObject]->(Object)` live, but after `rebuild_all` the edge is **gone** (G6). Comment 3 §13 predicted exactly this ("any observation→indicator link must be written on a journaled path, or it will not survive a rebuild"). Rejected; use the operator link (G7). |
| A10 | **Encode the target group as one JSON string field** | Flat and scalar, and it removes the index-alignment invariant — but WS-C §2.4 explicitly rejects string-encoding a number ("a Python `Decimal` would have to be stored as an unindexable, unsummable string"), and a JSON blob hides the value from plain Cypher. Rejected; kept as O5 so a reviewer can argue it. |
| A11 | **Extend `extractor_v2.EVENTS` and `value_extractor.EVENT_KINDS` with `measurement`** | `extractor_v2.EVENTS` is byte-pinned in `tests/fixtures/core_only_master.golden.txt`; `measurement` Events are written by an explicit helper with an explicit `eventKind`, never extracted, so the prompt vocabulary does not need it. `value_extractor.EVENT_KINDS` is a **fourth, already-drifted, dead** copy (it lacks `milestone`) — extending it would propagate the defect. Recorded as O9. |

### ⚠ Places the settled design is ambiguous or self-contradictory (recorded, not guessed)

**AMB-1 — a target is "one owned composite" AND FalkorDB has "no map type" (0..*).** Comment 7 Amendment 1 says *"one owned composite, not three loose columns and not a node"*; comment 6B says *"scalars only, because FalkorDB has no map type"*. A `0..*` composite therefore cannot be one stored field. **Resolution taken:** an **index-aligned parallel scalar-array group**, one vocabulary shared with WS-C, with the alignment invariant upheld **by construction** in `set_targets` (every array is built at length N) and **fail-closed** in `read_targets` (`ValueError` on a group it is handed with unequal-length arrays — reachable from a direct property write). The repo has no property-testing dependency and this plan adds none — see Tech Stack. A10 records the alternative. **This is a decision this plan makes, not a settled one** — see O5.

**AMB-2 — `goalState` is already STORED (#7856, ONTOLOGY §5) but the design lists it as DERIVED.** `GOAL_STATE_VALUES` (`sdk.py:166`) is a closed vocabulary validated in `_sanitize_props` (`sdk.py:2851`), and `docs/ONTOLOGY.md` §5 states it is validated at the SDK write boundary. These are two different things sharing one name. **Resolution taken:** the stored `goalState` stays **authoritative** (a declared value is never overridden by a computed one — ITU-T Z.151, cited in comment 7); the derived rollup is returned under a **distinct key, `derivedGoalState`**, whose vocabulary is comment 7's convergent derived-state set and is **disjoint from `GOAL_STATE_VALUES` by construction** (`GOAL_STATE_VALUES` has no `unsatisfied`/`partiallySatisfied`/`denied`/`conflict`/`unknown` token). Making the derived value *be* `goalState` would be a **reopen of #7856** — O4.

**AMB-3 — scope of the goal-state half.** The issue body says #7860 *"owns only the measurement/kind half"*, and names #7856 (CLOSED) as owning *"the cascade and state side"*. The prompt and comment 7's design include derived **goal state**. **Resolution taken:** the *derivation* lands here as declared-vocabularies + a pure reader whose default is **not to compute**; the issue body's boundary is recorded as **O8** for the owner to confirm or move.

---

### Pattern Research

> **Findings date:** 2026-10-13

**Library docs (preflight)** — no third-party libraries in this plan. The graph is reached only through in-repo wrappers (`TortoiseSDK`, `PackRegistry`, `projection/entities.py`), each used 2+ times. No vendor SDK is touched.

> Gate skipped: zero third-party dependencies — pure in-repo Python, pack-manifest YAML, and the existing FalkorDB wrapper. (Skip rule: `writing-plans/SKILL.md` → *Skip Step B … when the plan touches zero third-party dependencies … in-repo wrappers used 2+ times*.)

**Prior Research Intake (Step A)** — ran. The issue's eight comments **are** the research artifact: SOSA-SSN / RDF Data Cube / FHIR / OpenSLO / Wikidata / Graphiti precedents (comments 0–1), the storage-architecture alignment (comment 3), the contradiction-test resolution (comment 4), the owner rulings (comments 5–6) and the two-pass primary-spec synthesis (comment 7). No separate research brief exists (`ls docs/plans/*7860*research*` → none); the issue is not epic-parented.

**Library version & API surface / Idiomatic usage patterns / Library-framework pitfalls** — all skipped (no third-party deps). The in-repo patterns used are established 2+ times: `create_operator` with a label (`sdk.py:10135` docstring; `promote_point` calls it with `label="alreadyDecided"`), and `_persist_extra_props` open-set passthrough on Point and Object.

---

## Grounding — what this plan verified against the tree (not guessed)

Every claim was **executed or read** in this worktree on 2026-10-13.

| ID | Claim | How verified | Result |
|---|---|---|---|
| G1 | `indicator` ∉ core object kinds; `measurement` ∉ core event kinds | `PackRegistry`, `CANONICAL_*_KINDS` | `False` / `False` |
| G2 | `indicator` is declared **only** by `packs/dev/manifest.yaml` (objectKinds `:18`, kindDef `:111`) — *in pack manifests* | `grep -rn indicator packs/*/manifest.yaml` | dev only |
| G3 | ⚠ **The pack-manifest search was too narrow.** `indicator`/`dev:indicator` is also referenced in **code and tooling**: `tortoise/value_extractor.py:~212` (core dict → brief → **byte-pinned golden**), `tortoise/extractor.py:379` `_OBJECT_KIND_VOCAB` (+ `:866` prompt default already lists `indicator`), `tools/calibration_harness.py:205` `VOCAB`, `tortoise/migrate_kinds.py:49` | review-2/review-5 findings, re-verified by reading each | **all confirmed** |
| G4 | The Object↔Object operator link works, is journaled, survives rebuild | live embedded run + `rebuild_all` | ✅ |
| G5 | An Object accepts list-of-scalars and it survives rebuild | live run, `targetAmount=[1000000,2000000]` (stored key spelling: `targetAmount`, **not** `targetAmountMinor`) | ✅ |
| G6 | ⛔ **`create_event(aboutObject=…)` does NOT survive rebuild.** By **name** it wires `(Event)-[:aboutObject]->(Object)` live; by **id** it does not wire at all; after `rebuild_all` the edge is **gone** either way | live run, PRE/POST query | **LOST** |
| G7 | ✅ **The durable link is the operator**: `create_operator("IMPL", <Event id>, [<indicator id>], label=…)` → `(op:Point)-[:IMPL]->(:Event)` **and** `->(:Object)` both survive `rebuild_all` | live run | ✅ |
| G8 | A pack relation declared **without** `fromKind`/`toKind` **and** without `from`/`to` is a hard load error (`invalid 'from' type: None`); the **new** `fromKind`/`toKind` form **requires** a `mechanism`, so it **cannot** be `declarationOnly`; only the **old** `from:`/`to:` form loads clean as declaration-only | 5 candidate manifests loaded through `PackRegistry` | verified see R1 |
| G9 | A relation `nearMisses` target must resolve against `CORE_PREDICATES ∪ SEMANTICS_VALUES ∪ declared predicates` — `conditionsOn` and `targets` both **failed** in isolation | same run | fail |
| G10 | `tests/test_pack_kinds.py:1341` `test_three_way_diff_empty`: `ONTOLOGY §5 Object block == CANONICAL_OBJECT_KINDS == extractor_v2.CORE_OBJECT_KEYS` — parses **only** the Object section | read | real guard |
| G11 | `tests/test_pack_kinds.py:1449` `test_legacy_extractor_vocab_is_a_documented_subset` asserts `canonical_lower - _OBJECT_KIND_VOCAB == {"strategy","plan","goal","target"}` and `_OBJECT_KIND_VOCAB` (`extractor.py:379`) is a 12-kind frozenset | read | **reds if `indicator` is added to the canonical set without adding it to `_OBJECT_KIND_VOCAB`** |
| G12 | `tortoise/value_extractor.py:~212` holds a hardcoded `core:{Kind}` description dict feeding `compile_value_brief` → `extractor_v2._build_master_from_brief` → the byte-pinned `tests/fixtures/core_only_master.golden.txt` (`test_extractor_v2.py:3720` `test_core_only_render_byte_pinned_golden`) | read | **golden churn on `core:indicator`** |
| G13 | `tools/calibration_harness.py:205` hardcodes `"dev:indicator"`; `tests/test_calibration_tools.py:176` `test_probe_vocab_is_subset_of_packs_and_core` checks every `ch.VOCAB` entry against pack kinds ∪ a hardcoded core list lacking `core:indicator` | read | **reds after Task 1** |
| G14 | `tortoise/migrate_kinds.py:49` carries `("indicator","dev:indicator","object")`; `_do_migrate` SETs `objectKind='dev:indicator'` on every `:Object {objectKind:'indicator'}` | read | **would reverse the promotion** |
| G15 | Fourth event copy: `tortoise/value_extractor.py:504` `EVENT_KINDS` (15 entries, **already drifted** — no `milestone`), read by no test/guard | read (review-5), confirmed | dead + drifted |
| G16 | `_OBJECT_DECLARED_PROPS` does **not** exist; only `_POINT_DECLARED_PROPS` (`entities.py:1350`) — Object extra props raise no drift warning, **Point** props do (pinned by `test_7856_goal_state.py::test_goal_state_declared_for_point_replay`) | `grep` + read | confirmed |
| G17 | `_persist_extra_props` (`entities.py:1394`) skips only `v is None`; `_is_persistable_prop_value([])` is `True`; the Object path passes `list_props=None` ⇒ **an empty array IS written** | read | plan's earlier note was **wrong**, corrected |
| G18 | Existing shared predicates to reuse instead of re-deriving: `time_aware.is_stale_entry` (supersession + validity window + `live.is_terminal_status`), `commit_schema.validate_validity_window` | read | reuse |
| G19 | `docs/ONTOLOGY.md` mentions `indicator` **0** times; the issue has **no** `<!-- issue-scoping:` signature | `grep` | `0` / absent |
| G20 | Collision pre-flight | `python tools/collision_preflight.py 7860 --repo .` | **CLEAN (exit 0)** |

---

## Integration Surface Map

> Produced per `test-design` (Steps 1–5), adapted: no Supabase/pgTAP; the DB is **FalkorDB**, and the integration layer is the **embedded lane** (`TortoiseSDK(db_path)` + `projection.rebuild_all`), matching `tests/test_7856_goal_state.py`.

| # | Surface | Type | Data Flow | Test Layer | Contract | Key Failure Modes |
|---|---|---|---|---|---|---|
| 1 | `PackRegistry._validate` / `_validate_cross_pack_refs` — pack kind + relation declarations | Config / graph-adjacent | In | **Unit + pack-load** (`test_pack_kinds.py`, `test_pack_content_v3.py`, `test_calibration_tools.py`) | `ontology.objectKinds`/`kindDefs`/`relations`; a pack may not re-declare a canonical kind; relation needs `fromKind`+`toKind`+`mechanism` **or** `from`+`to`; `nearMisses` must resolve | relation missing both forms → hard load error (G8); `nearMisses` unresolved → hard error (G9); double kind declaration |
| 2 | Object-kind vocabulary — **six** coupled surfaces (see Task 1) | Vocabulary | Both | **Unit** — `test_pack_kinds.py::TestCanonicalObjectKindAlignment::test_three_way_diff_empty` + the legacy-subset pin + the byte-pinned golden + `test_vocab_gating_by_graph.py:529` | `ONTOLOGY §5 Object block == CANONICAL_OBJECT_KINDS == CORE_OBJECT_KEYS == value_extractor core dict keys == extractor prompted set ∪ {excluded 4}` | **(1)** a partial edit reds the three-way guard / the legacy-subset pin / the byte-pinned golden independently; **(2)** a fourth copy (`value_extractor` core dict) drifting silently because only the guard is read |
| 3 | Event-kind vocabulary — `CANONICAL_EVENT_KINDS` / `commit_schema.EVENT_KINDS` / `ONTOLOGY §5` | Vocabulary | Both | **Unit** — new ONTOLOGY-Event-block pin (no guard exists today, G19/§Grounding) | `EVENT_KINDS == ONTOLOGY §5 Event block` and `EVENT_KINDS ⊇ CANONICAL_EVENT_KINDS` (the 12-vs-16 subset is pre-existing and is **pinned, not widened**) | **(1)** a write gate refuses an `eventKind` only one copy carries; **(2)** the pre-existing subset silently widening, which the old `a <= b or b <= a` shape could not catch |
| 4 | Object property write — `_upsert_object` → `_persist_extra_props` | DB (graph) | In | **Integration (embedded) + rebuild round-trip** | flat scalars / scalar arrays; `None` dropped; **`[]` is written** (G17) | list dropped by a list policy; prop lost on rebuild |
| 5 | `create_operator(op_type, source, [target], label, direction)` — operator `:Point` + typed edge + reverse `INPUT` + journal | DB (graph) + Event | Both | **Integration (embedded) + rebuild round-trip** | endpoints may carry any `EPISTEMIC_LABELS` label; the operator node is always `:Point` | label/direction lost on rebuild; endpoint not found; undeclared-label violation |
| 6 | ⛔ **Rejected surface:** `create_event(aboutObject=…)` / `create_edge("aboutObject", …)` | DB (graph) | Both | **Integration (embedded) + rebuild regression** — `test_operator_link_is_the_observation_mechanism` | the observation link must be the **operator** one (G7); `(Event)-[:aboutObject]->(Object)` is written live but is **not journaled** (#8398, **OPEN**) and is not used | **(1)** an observation re-attached via `aboutObject` is silently lost on the next `rebuild_all` (G6; #8398); **(2)** the **positive** contract this guard asserts is that the **operator link survives** — so when #8398 is fixed the guard stays green and must not be deleted as “obsolete” |
| 7 | `_sanitize_props` — goal declaration vocabularies | Validation boundary | In | **Unit** (fail-closed) + `_POINT_DECLARED_PROPS` pin (G16) | each a closed frozenset; `None` clears; invalid → `ValueError` before any graph write | unknown word persisted verbatim; validation bypassed on one of the five write surfaces; false Point-prop drift warning |
| 8 | `tortoise/indicators.py` — pure derived readers | Pure logic | In | **Unit** — enumerated boundary examples + the fail-closed `read_targets` group check (no property-testing dependency in this repo) | reuses `is_stale_entry` / `validate_validity_window` / `is_terminal_status` (G18) — but **no in-plan writer sets the staleness props** (corrections out of scope, O10) | off-by-one validity bounds; `[]` vs absent (precedence pinned: `[]` ≡ absent only for the whole-empty group, else `"misaligned"`); a **mixed comparator/unit** group misread; a directly-written malformed group silently truncated; >1 active target silently picked; zero observations read as `0`; corrections **not** supported (O10) |
| 9 | `measurement` Event write + journaled `measures` link + `currentValue` read | DB (graph) + logic | Both | **Integration (embedded) + rebuild round-trip** | `{amount, currency, minorUnitExponent, unit}` in the **stored Event-prop spelling** (record shape **pinned in Task 4 Step 1**), `at` = phenomenonTime, `createdAt` = resultTime (**the field; there is no `resultTime` key**), `current_value` tie-breaks on `(at, createdAt, id)`; append-only | over-cadence write accepted; link lost on rebuild (G6 is the counter-example); invalid observation wins the tie-break |

### Bug Pattern Flags

- **Conditional guards** (surfaces 7, 8) — every branch of the declaration set and every `targetAssessment` outcome needs tests on **both** sides. `derivation_mode` **defaults to `MANUAL`** — the default branch is itself a guard.
- **Boundary values** (surface 8) — 0, 1, max targets; validity bounds; `[]` **vs absent** (both read `[]` **only when the whole group is empty**; a length-0 array beside populated ones is **`"misaligned"`** — review-6 F2); a **mixed comparator/unit** group (a `between` money target beside a `gte` count target) must round-trip — the shape the old all-or-nothing rule made unrepresentable.
- **Ordering / staleness** (surfaces 8, 9) — `current_value` tie-breaks on **`(at, createdAt, id)`** — **`createdAt` IS resultTime; there is no `resultTime` field** (the record shape is pinned in Task 4 Step 1) — and drops entries the shared predicate `time_aware.is_stale_entry` reports stale — **via the shared predicate, not a re-derivation** (G18). ⚠️ **Corrections are OUT OF SCOPE**: no writer in this plan sets `superseded_by`/`valid_to` on an observation, so that clause is inert and the reader is not described as correction support (**O10**).
- **N+1** (surface 8) — `derive_goal_state` over N children must not issue one read per child; readers take already-fetched records.
- **Silent function skips** — `target_assessment` must return `no-target` / `ambiguous-multi-target` explicitly; never a falsy default a caller reads as "breached".
- **Journal durability** (surfaces 5, 6, 9) — **every** new link needs a `rebuild_all` round-trip test. G6 is the incident this is written from.

### Checklist Notes

- **Atomic writes:** the operator write is one `create_operator` call (node + edge + reverse `INPUT` + event). Do **not** hand-write Cypher for it.
- **Idempotency:** no uniqueness constraint exists on operators. `link_indicator` checks first; the check-then-write race is **accepted and documented** (read-time consumers must tolerate a duplicate link, which they already do — a set of indicators). Test the **sequential** duplicate at minimum.
- **Empty vs null:** the `targetAmount` group absent ⇒ 0 targets. An explicit `[]` **is** written by the graph (G17) and `read_targets` must treat a **wholly empty** group and an absent one **identically**; the "absent" guarantee is a property of `set_targets`, not of `_persist_extra_props`. **Precedence (review-6 F2): a length-0 array in an otherwise-populated group is NOT "absent" — it is the `"misaligned"` length mismatch; `[]` ≡ absent applies ONLY when every array in the group is empty (arity 0).** (The stored key spelling is `targetAmount` — the probe recorded in G5 used the older `targetAmountMinor` spelling before it was retired.)
- Live graph **refuses writes** (#7405/#6968) — every graph test uses the embedded lane.

### Verification Plan

**Domain(s):** code (vocabulary + graph write + pure logic). No UI/content/config-domain gate beyond the pack manifests.

**Complexity:** Architecture=**medium** · Ontology=**high** (this issue *is* an ontology change; six coupled vocabulary surfaces + a strict three-way guard) · UX=n/a · Accessibility=n/a.

| # | Skill | Depth | Reason |
|---|---|---|---|
| 1 | `test-writing` | standard | pure logic (`tortoise/indicators.py`) — unit TDD with enumerated boundary examples; the alignment invariant is upheld by construction in `set_targets` and fail-closed in `read_targets` (the repo has no property-testing dependency and this plan adds none) |
| 2 | `test-integration` | **full** | graph surfaces 4, 5, 9 — embedded FalkorDB, journal + `rebuild_all` round-trip |
| 3 | `test-review` | standard | new module + new vocabularies; boundary coverage and test quality |
| 4 | ontology-alignment review | full | required by **Ontology=high** — dispatch via `code-review` (the six surfaces, the ONTOLOGY §5 diff, the #2786 class) |

### Skipped / Deferred

| Item | Reason |
|---|---|
| `test-e2e` | no browser surface; the embedded SDK round-trip **is** the end-to-end path |
| `ux-verification` | no UI changes |
| content / config-validation | not applicable; pack-manifest YAML is validated by the registry's own tests (surface 1) |

---

## Tech Stack

- Python 3.12+ (`uv run`), pytest; embedded FalkorDBLite lane (`TORTOISE_TEST_CARVE_OUT=1`) for graph tests.
- Graph: FalkorDB — **scalars and scalar arrays only**; no map, no decimal.
- Pack manifests: YAML v3 (`ontology.objectKinds` / `kindDefs` / `relations`).
- No new third-party dependency.

---

## Task 0: Owner gate — `docs/ONTOLOGY.md` §5 (⛔ BLOCKING, owner-only)

**Intent:** The core-kind promotion in Task 1 is *coupled by a test* to the canonical ontology doc. `docs/ONTOLOGY.md` is **Daniel-only**; this lane must not edit it. Without this edit, `test_three_way_diff_empty` reds and the repo ships the doc/code divergence this issue exists to close.
**Acceptance:** `grep -c '\bindicator\b' docs/ONTOLOGY.md` > 0; the §5 Object block contains `indicator`; the §5 Event block contains `measurement`; the legacy-extractor prose reflects the new count.

**Files:**
- Modify (⛔ **owner only — the lane never writes this file**): `docs/ONTOLOGY.md`

**Step 1 — Apply the proposed diff (owner).** In §5 → `### Object Kind Vocabulary (core)`:

```diff
 Project, WorkItem, Problem, tag, user, skill, tool, agent, workflow, agreement, standard, other,
-strategy, plan, goal, target    # commitment-state family (state-centric, 2026-08-12) — states that
-                                # commitments produce; carry lifecycle + derived confidence
+strategy, plan, goal, target,   # commitment-state family (state-centric, 2026-08-12) — states that
+                                # commitments produce; carry lifecycle + derived confidence
+indicator                       # a measured quantity — a DEFINITION (unit, source, cadence, owner);
+                                # carries no pass/fail. An optional inline `target` is a condition on
+                                # it (#7860). General, NOT dev-scoped: DORA/DRIVE metrics are one
+                                # family of indicators. Owner ruled metric ≡ indicator — there is no
+                                # separate `metric` kind.
```

In §5 → `### Event Kind Vocabulary (core)`:

```diff
-occurrence, turn    # state-centric (2026-08-12): occurrence = generic extracted occurrence;
+occurrence, turn,   # state-centric (2026-08-12): occurrence = generic extracted occurrence;
                     # turn = capture turn records (replaces pointKind 'event', issue #1013)
+measurement         # #7860: one observation of an indicator — {amount, currency, unit, at, method};
+                    # `at` is phenomenonTime, `createdAt` is resultTime. Append-only; the indicator
+                    # node is never mutated.
```

In §5 → the `Legacy extraction path (pinned, #2727)` prose under the Object block: change *"a narrower 12-kind subset"* to **13-kind**, and **keep** *"omits the four commitment-state kinds"* (`strategy`/`plan`/`goal`/`target`). `indicator` is **added** to `_OBJECT_KIND_VOCAB` in Task 1, so it is **not** omitted and the pin at `tests/test_pack_kinds.py:1463` stays exactly `{strategy, plan, goal, target}` (G11). Name `indicator` as the kind **added** to the legacy vocabulary. The stale `"(16 kinds each)"` counts in `pack_registry.py:53` and `value_extractor.py:128` are updated in Task 1.

**Step 2 — Independent verification of the edit (does not need Task 1).**

Run:
```bash
python3 - <<'PY'
import re, pathlib
t = pathlib.Path("docs/ONTOLOGY.md").read_text()
sec = t.split("### Object Kind Vocabulary (core)",1)[1].split("```",2)[1].split("\n",1)[1]
kinds = {tok for line in sec.splitlines() for tok in line.split("#",1)[0].replace(","," ").split()}
assert "indicator" in kinds, sorted(kinds)
sec = t.split("### Event Kind Vocabulary (core)",1)[1].split("```",2)[1].split("\n",1)[1]
kinds = {tok for line in sec.splitlines() for tok in line.split("#",1)[0].replace(","," ").split()}
assert "measurement" in kinds, sorted(kinds)
print("ONTOLOGY §5 edit verified")
PY
```
Expected: `ONTOLOGY §5 edit verified`. (The full three-way guard is Task 1's proof, not Task 0's — it needs the code change.)

**Step 3 — DECISION RELAY.** Post the applied diff as a comment on `#7860`, **with the `OVERRIDES:` line** (Task 7 carries the exact text).

---

## Task 1: Promote `indicator` (Object) and `measurement` (Event) — all coupled surfaces

**Intent:** Make a general measured quantity a first-class, discoverable graph vocabulary — reachable from where a product-strategy goal lives, without installing `dev`. Satisfies O/I/T 1 and 3.
**Acceptance:** `indicator` present on **every** object-kind surface (Object §5 block, `CANONICAL_OBJECT_KINDS`, `CORE_OBJECT_KEYS`, the `value_extractor` core dict, `extractor._OBJECT_KIND_VOCAB`); the three-way guard green; the legacy-subset pin still `{strategy, plan, goal, target}`; the byte-pinned golden regenerated and green; shipped packs + calibration probe load clean; `measurement` present on the event surfaces; `migrate_kinds` can no longer rewrite a core `indicator`.

**Files:**
- Modify: `tortoise/pack_registry.py` — `CANONICAL_OBJECT_KINDS:43`, `CANONICAL_EVENT_KINDS:67`, `LEGACY_KIND_ALIASES:~135`
- Modify: `tortoise/extractor_v2.py:405` (`CORE_OBJECT_KEYS`)
- Modify: `tortoise/commit_schema.py:134` (`EVENT_KINDS`)
- Modify: `tortoise/value_extractor.py:196` — the **local `core` dict inside `compile_value_brief`** (there is **no** module-level `_CORE_KIND_DESCRIPTIONS` symbol); add `"core:indicator"`. Also `:128` (stale `(16 kinds each)` comment)
- Modify: `tortoise/extractor.py:379` (`_OBJECT_KIND_VOCAB` — add `"indicator"`)
- Modify: `packs/dev/manifest.yaml:18` (remove `indicator`), `:111` (remove the dev kindDef — its general replacement is the `product-strategy` kindDef in Task 2)
- Modify: `tools/calibration_harness.py:205` (`dev:indicator` → `core:indicator`)
- Modify: `tortoise/migrate_kinds.py:49` (**remove** the `("indicator","dev:indicator","object")` row)
- Modify: `tests/fixtures/core_only_master.golden.txt` (regenerate)
- Modify: `tests/test_extractor_v2.py` (`_hand_built_master`, `test_core_only_render_byte_pinned_golden`)
- Modify: `tests/test_calibration_tools.py:193` (add `"core:indicator"` to the hardcoded core set)
- Modify: `tests/test_vocab_gating_by_graph.py:529` — `test_brief_core_keys_are_exactly_the_canonical_object_kinds` pins `compile_value_brief()`'s `core:*` keys `== set(CORE_OBJECT_KEYS)`. It **reds unless** the local `core` dict gains `core:indicator`; it also belongs in the regression set.
- Behaviour-only (no edit, named so a reviewer sees they were considered): `tortoise/domain_loader.py:82` (`known_kinds("objectKind")` membership grows) and `tortoise/query_suggestions.py` (reads `CANONICAL_OBJECT_KINDS` — `indicator` becomes suggestible).
- Modify: `tests/test_pack_kinds.py` (extend `TestCanonicalObjectKindAlignment` with the Event-block pin)
- Create: `tests/test_7860_indicator_vocab.py`

**Step 1: Write the failing tests**

⚠️ **Resolve every symbol before writing these tests** (review-2 F4): the core description dict is a **local** variable inside `compile_value_brief` (`value_extractor.py:196`) with **no** module accessor, and the migration table is `MIGRATIONS` (`migrate_kinds.py:29`), not `KIND_MIGRATIONS`. The test below uses the real surfaces only — no placeholder names.

```python
# tests/test_7860_indicator_vocab.py
from tortoise.pack_registry import (CANONICAL_EVENT_KINDS, CANONICAL_OBJECT_KINDS,
                                    LEGACY_KIND_ALIASES)
from tortoise.extractor_v2 import CORE_OBJECT_KEYS
from tortoise.commit_schema import EVENT_KINDS
from tortoise.extractor import _OBJECT_KIND_VOCAB
from tortoise.value_extractor import compile_value_brief


def test_indicator_is_a_core_object_kind_on_every_object_surface():
    assert "indicator" in CANONICAL_OBJECT_KINDS
    assert "core:indicator" in CORE_OBJECT_KEYS
    assert "indicator" in _OBJECT_KIND_VOCAB        # keeps the 4-kind exclusion pin (G11)
    brief_core = {k for k in compile_value_brief() if k.startswith("core:")}
    assert "core:indicator" in brief_core          # the real accessor, not a private symbol


def test_measurement_is_a_core_event_kind_on_every_event_surface():
    assert "measurement" in CANONICAL_EVENT_KINDS
    assert "measurement" in EVENT_KINDS


def test_no_metric_kind_was_minted():
    assert "metric" not in CANONICAL_OBJECT_KINDS
    assert "metric" not in CANONICAL_EVENT_KINDS


def test_migrate_kinds_cannot_reverse_the_promotion():
    from tortoise.migrate_kinds import MIGRATIONS
    assert ("indicator", "dev:indicator", "object") not in MIGRATIONS


def test_dev_indicator_is_a_read_only_alias():
    assert LEGACY_KIND_ALIASES.get("indicator") == ("dev:indicator",)
```

And extend `tests/test_pack_kinds.py::TestCanonicalObjectKindAlignment` with an Event-block sibling. The existing parser hard-codes `_OBJECT_KIND_SECTION`; **parameterise it** (e.g. `def _ontology_kinds(section_header: str)`) rather than calling a function that does not exist:

```python
def test_event_block_pins_measurement(self):
    from tortoise.commit_schema import EVENT_KINDS
    ontology = _ontology_kinds("### Event Kind Vocabulary (core)")
    assert "measurement" in ontology
    assert "measurement" in CANONICAL_EVENT_KINDS
    assert "measurement" in EVENT_KINDS
    # The subset is PRE-EXISTING (ONTOLOGY/commit_schema 16 vs canonical 12).
    # Assert the concrete direction — `a <= b or b <= a` cannot fail on a
    # one-directional divergence (review-2 F10).
    assert EVENT_KINDS == ontology, sorted(EVENT_KINDS ^ ontology)
    assert EVENT_KINDS >= set(CANONICAL_EVENT_KINDS)
```

**Step 2: Run to verify it fails** — `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_7860_indicator_vocab.py -q` → FAIL on every assertion.

**Step 3: Implement.** Add `"indicator"` to `CANONICAL_OBJECT_KINDS` (with a `#7860` comment in the existing `milestone` style) and `"core:indicator"` to `CORE_OBJECT_KEYS`. Add `"measurement"` to `CANONICAL_EVENT_KINDS` and `commit_schema.EVENT_KINDS`. Add `"indicator"` to `extractor._OBJECT_KIND_VOCAB` (the legacy prompt default at `extractor.py:866` already lists it — G3). Add the `"core:indicator"` description to the `value_extractor` core dict. Remove `indicator` from `packs/dev/manifest.yaml` (`objectKinds` + the dev `kindDef`) — a pack may not re-declare a canonical kind (`pack_registry.py:1010`). Add `LEGACY_KIND_ALIASES["indicator"] = ("dev:indicator",)` so stored `dev:indicator` nodes stay **readable** while the retired spelling stays non-writable (#7848/#6146 precedent). **Remove** the `migrate_kinds` row. Point `tools/calibration_harness.py` at `core:indicator`.
**Not touched, deliberately (A11):** `extractor_v2.EVENTS` (byte-pinned; `measurement` is never extracted) and `value_extractor.EVENT_KINDS` (dead, already drifted — O9).

**Step 4: Regenerate the golden** — after the `value_extractor` change, run the byte-pinned test, confirm it reports the added `core:indicator` line, update `tests/fixtures/core_only_master.golden.txt` **and** `_hand_built_master` in the same commit (a golden updated without its hand-built twin is the failure mode that test exists to catch).

**Step 5: Run to verify it passes**

Run: `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_7860_indicator_vocab.py tests/test_pack_kinds.py tests/test_pack_content_v3.py tests/test_calibration_tools.py tests/test_vocab_gating_by_graph.py tests/test_extractor_v2.py -q`
Expected: PASS — including `test_three_way_diff_empty` (needs Task 0), `test_legacy_extractor_vocab_is_a_documented_subset`, `test_brief_core_keys_are_exactly_the_canonical_object_kinds`, and `test_core_only_render_byte_pinned_golden`.

**Step 6: Commit** (mandated `-F` form; see `commit-workflow`).

---

## Task 2: Declare the layer-3 verbs and the link helper

**Intent:** Make the link characterisable and warning-free **without** touching `_VALID_EDGE_PREDICATES` or `EntityLinked` (#7852/#2766). Put the general `indicator` kindDef where goals live.
**Acceptance:** each verb's `list_relations()` entry carries `write_verdict == RELATION_DECLARATION_ONLY`. ⚠️ **That entry does NOT copy `declarationOnly`** — re-deriving the verdict from it returns `predicate-not-in-allowlist`, so assert the entry's **own `write_verdict` field** (or the raw relation via `reg.get_pack("product-strategy").relations`) (review-1/2 F1). The pack loads with **zero** errors and **no** `relation-not-writable` warning; `_VALID_EDGE_PREDICATES` is **still exactly 22** and contains neither verb; `link_indicator` is idempotent on a sequential repeat.

**Files:**
- Modify: `packs/product-strategy/manifest.yaml` — add **two** `ontology.relations` entries (old `from:`/`to:` form). ⚠️ **Do NOT add a `kindDefs.indicator` entry** (review-3 F3): `compile_value_brief` emits `f"{ns}:{k}"` for **every** `kindDefs` key regardless of `objectKinds` membership, so it would mint a second brief key `product-strategy:indicator` beside `core:indicator` — the same two-vocabulary shape this plan rejects in A6. Core promotion plus the `core:indicator` entry in the `value_extractor` core dict (Task 1) already supplies the description.
- Modify: `tests/test_pack_content_v3.py:239` — **`TestProductDeliveryChain::test_problem_family_relations_ps` indexes `r["fromKind"]` on every `ps.relations` and reds with `KeyError` on an old-format relation** (verified by loading a candidate manifest). Filter on the new form (`if "fromKind" in r`) or use `.get()`. This is a deliberate test edit, not just a verification command.
- Create: `tortoise/indicators.py` — `link_indicator(sdk, source_id, indicator_id, *, direction="unidirectional")`.
- Test: `tests/test_7860_link_roundtrip.py` (new)

**Step 1: The relation declarations (⛔ **shape is load-bearing — verified, G8/G9**).** Use the **old** `from:`/`to:` form; the new `fromKind`/`toKind` form *requires* a `mechanism` and therefore cannot be `declarationOnly`. Do **not** add `nearMisses` (nothing resolves reliably — G9).

```yaml
relations:
  - predicate: measuredBy      # goal -> indicator (layer-3 label)
    from: Object
    to: Object
    declarationOnly: true
    semantics: supports        # silences the layer-2 warn; does NOT create a write path
  - predicate: measures        # measurement Event -> indicator (layer-3 label)
    from: Event
    to: Object
    declarationOnly: true
    semantics: supports
```

**Step 2: Write the failing test** — assert the **effect**, not the pre-existing state (the link already works, so a label/direction assertion cannot fail — G4):

```python
# tests/test_7860_link_roundtrip.py
import pathlib
import pytest
from tortoise.pack_registry import PackRegistry, RELATION_DECLARATION_ONLY
from tortoise.projection.edges import _VALID_EDGE_PREDICATES
from tortoise.sdk import TortoiseSDK

REPO = pathlib.Path(__file__).resolve().parent.parent


def test_verbs_are_declared_but_bind_no_write_path():
    reg = PackRegistry(REPO / "packs"); reg.load_all()
    assert not reg.errors, reg.errors
    declared = {r["predicate"]: r for r in reg.list_relations()}
    for verb in ("measuredBy", "measures"):
        assert verb in declared, sorted(declared)
        # the entry's OWN verdict — `list_relations()` drops `declarationOnly`
        assert declared[verb]["write_verdict"] == RELATION_DECLARATION_ONLY
        assert declared[verb]["write_paths"] == []
    raw = {r["predicate"]: r for r in reg.get_pack("product-strategy").relations}
    assert raw["measuredBy"]["declarationOnly"] is True


def test_the_label_allowlist_was_not_touched():
    """#7852/#2766: a layer-3 label needs no vocabulary growth. If this reds,
    someone widened the allowlist instead of declaring the label."""
    assert len(_VALID_EDGE_PREDICATES) == 22
    assert "measuredBy" not in _VALID_EDGE_PREDICATES
    assert "measures" not in _VALID_EDGE_PREDICATES


from tortoise.indicators import link_indicator


def test_link_indicator_is_idempotent_on_a_sequential_repeat(tmp_path):
    """Task 2's acceptance names this; without it the helper ships untested
    (review-3 F4)."""
    events = tmp_path / "events"; events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "t.db"), event_log_path=str(events / "e.jsonl"))
    try:
        goal = sdk.create_object("GOAL", objectKind="goal", goalState="active")
        ind = sdk.create_object("IND", objectKind="indicator")
        a = link_indicator(sdk, goal["id"], ind["id"])
        b = link_indicator(sdk, goal["id"], ind["id"])
        assert b.get("already_linked") is True or b["id"] == a["id"]
        n = sdk._get_proj().g.query(
            "MATCH (op:Point {is_operator:true})-[:IMPL]->(:Object {id:$i}) "
            "RETURN count(op)", params={"i": ind["id"]}).result_set[0][0]
        assert n == 1
    finally:
        sdk.close()


def test_operator_link_survives_rebuild(tmp_path):
    events = tmp_path / "events"; events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "t.db"),
                      event_log_path=str(events / "events.jsonl"))
    try:
        goal = sdk.create_object("GOAL - REVENUE", objectKind="goal", goalState="active")
        ind = sdk.create_object("IND - MRR", objectKind="indicator")
        op = sdk.create_operator("IMPL", goal["id"], [ind["id"]],
                                 label="measuredBy", direction="unidirectional")
        sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
        after = sdk.get_entity(op["id"])
        assert after["label"] == "measuredBy"
        assert after["direction"] == "unidirectional"
    finally:
        sdk.close()
```

⚠️ **`TortoiseSDK` has no `_events_dir`** — the attribute is `_event_log_path`, and `rebuild_all` takes the events **directory** (review-2 F3). The pattern above is the working one (mirrors `tests/test_7856_goal_state.py::test_goal_state_survives_rebuild`).

**Step 3: Run to verify it fails** — `test_verbs_are_declared_but_bind_no_write_path` must FAIL (`measuredBy` not declared). If it does not fail, the declaration is wrong, not the test.

**Step 4: Implement** `link_indicator` (thin, idempotent-by-check wrapper over `create_operator`), the manifest entries, **and** the `tests/test_pack_content_v3.py:239` guard fix (its `r["fromKind"]` index reds on the new old-format relations).

**Step 5: Run to verify it passes**

Run: `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_7860_link_roundtrip.py tests/test_pack_content_v3.py -q`
Expected: PASS, zero registry errors, no `relation-not-writable` warning for either verb.

**Step 6: Commit.**

---

## Task 3: The `target` composite — index-aligned scalar group, 0..*

**Intent:** Model a target as the inline, multi-field, repeatable composite the specs converge on, in the only storage shape FalkorDB supports (AMB-1), reusing **WS-C's field vocabulary** so no second numeric vocabulary is minted.
**Acceptance:** a target group written to an indicator Object round-trips **and** survives `rebuild_all`; N targets ⇒ N entries in **every** array; **absent and a wholly-empty `[]` group both read as 0 targets** (a length-0 array beside populated ones is **`"misaligned"`**, not absent — review-6 F2); a **mixed comparator/unit** group round-trips; an unknown comparator, a range comparator with no upper bound, and a **malformed group written directly** (unequal-length arrays, bypassing `set_targets`) are refused.

**Files:**
- Create: `tortoise/indicators.py`
- Test: `tests/test_7860_target_field.py` (new)

**Step 1: Pin the group contract (review-1 F3; made coherent for mixed groups, review-5 F1/F6).** `TARGET_GROUP_KEYS` — **parallel, index-aligned, fixed-arity** arrays on the indicator Object, using WS-C's names, prefixed `target`. **N targets ⇒ every array in the group has exactly N entries, always.** Every key is written for every target, so a position is never ambiguous and a **mixed** group (a `between` money target beside a `gte` count target) is legal and normal — that is what `0..*` is for.

| Key | Type | Per-target input | Written for a target with no such value | Notes |
|---|---|---|---|---|
| `targetAmount` | `int[]` | required | — | **integer minor units** (never a float — WS-C §2.4) |
| `targetMinorUnitExponent` | `int[]` | required | — | stored, self-describing (WS-C OD2) |
| `targetUnit` | `str[]` | required | — | `BRL`, `count`, `ratio`, `percent`, … |
| `targetComparator` | `str[]` | required | — | closed: `lt lte gt gte between outside` |
| `targetWindow` | `str[]` | required | — | **evaluation window** (rolling) — `7d`/`30d`/`90d`; OpenSLO `timeWindow` / Datadog `timeframe`. **Not** an activity/validity bound. |
| `targetUpperAmount` | `int[]` | required iff comparator ∈ {`between`,`outside`} | copied from `targetAmount` | second bound for range comparators; the reader **ignores** it for one-sided comparators |
| `targetCurrency` | `str[]` | optional | `""` (**sentinel**) | a non-empty ISO-4217 code ⇒ the amount is money; `""` ⇒ it is not |
| `targetAsOf` | `str[]` | optional | `""` (**sentinel**) | validity anchor (ISO instant); `""` ⇒ always active |
| `targetValidFor` | `str[]` | optional | `""` (**sentinel**) | validity duration after `targetAsOf` (`Nd`/`Nh`, the `cadence` subset); `""` ⇒ active from `targetAsOf` onward, no expiry |

**One coherent encoding rule (review-5 F1/F2).** Every array is **fixed-arity = N**, and per-target absence is represented **in-band**, never by a short array:

- **Optional values:** a per-target dict may **omit** an optional key; `set_targets` writes the type's sentinel at that position and `read_targets` turns the sentinel back into an **omitted key**, so arrays stay index-aligned while a target's optional facts vary row to row.
- **`targetUpperAmount`:** required in the input dict for `between`/`outside`; for one-sided comparators it is synthesized as a copy of `targetAmount` (ignored on read), so no array is short and no `None`/null ever enters a list.
- **The old ALL-OR-NOTHING *presence* rule is gone; all-or-nothing now describes only WRITING.** `set_targets` writes **every** key in the table, each built by one list comprehension over the same target list — so a misaligned group **cannot be produced by `set_targets`**, and a `"misaligned"` `ValueError` there would be unreachable (review-5 F2). The alignment guarantee rests on (a) that construction and (b) `read_targets` **failing closed** — `ValueError` containing `"misaligned"` — when a group it is handed has arrays of **unequal length**. (b) is reachable: the Object open-set passthrough accepts a caller writing the properties **directly** (`update_entity` → `_upsert_object` → `_persist_extra_props`), bypassing `set_targets`; `read_targets` refuses the result rather than truncating it.

**⛔ `[]` vs `"misaligned"` PRECEDENCE — stated, because the two rules collide on a length-0 array (review-6 F2).** The rule that wins is **`"misaligned"`**: `[]` ≡ absent holds **only when the WHOLE group is empty** (arity 0 ⇒ zero targets). Once any array in the group is non-empty the group arity **N** is fixed, and a length-0 array beside populated ones is a length mismatch — the `"misaligned"` case, **not** a silently-dropped key. Dropping it would return a target missing a required field, which is worse than refusing. Precisely: **let N be the length of the group's populated arrays (they must agree — arrays of differing non-empty length are already `"misaligned"`); N = 0 ⇒ `[]` (zero targets); N > 0 ⇒ every present array must have length N, and any array of length 0 (or otherwise ≠ N) raises `ValueError("misaligned")`.**

**Per-target key vocabulary (pinned — review-4 F5; example corrected — review-5 F6):** the per-target dicts passed to `set_targets` and returned by `read_targets` use the **stored `target*` keys verbatim**, with optional keys **omitted** when absent. There are **no unprefixed aliases** (`amount`/`scale`/`asOf`/`window` are **not** accepted); because the sentinels are normalized away, `read_targets(set_targets(p, t)) == t` holds literally. Canonical money example — the currency is present **because the unit is money** (the earlier draft omitted it, contradicting the table):

```python
{"targetAmount": 1000000, "targetMinorUnitExponent": 2, "targetUnit": "BRL",
 "targetCurrency": "BRL", "targetComparator": "gte", "targetWindow": "30d"}
```

`set_targets(props, targets)` validates the **input dicts**: a required key must be present for every target; `targetUpperAmount` must be present for every `between`/`outside` target (`ValueError` containing `"upper bound"`); an out-of-vocabulary comparator raises `ValueError` containing `"comparator"`. An **explicit `targets=[]` is a legal input and yields zero targets** (G17: an empty array **is** stored, so `read_targets` may receive a group of length-0 arrays — the normalization lives in `read_targets`, which reads the all-empty group identically to an absent one). **In scope** for the issue's Target — exactly the pinned key vocabulary above, no aliases: `targetAmount`, `targetMinorUnitExponent`, `targetCurrency`, `targetUnit`, `targetComparator`, `targetWindow`, `targetAsOf`, `targetValidFor`, `targetUpperAmount` (WS-C's `amount`/`currency`/`minorUnitExponent` under the `target` prefix; the spelling `scale` is **not** used anywhere). **Deliberately deferred** (O6): `baseline`, range forms beyond `between`/`outside`, target *versioning* (A7), and **`valueKind`** — comment 3 §3 declares indicators are **not always quantitative** (`quantity | count | ratio | boolean | ordinal | categorical`), which this numeric target shape cannot express; recorded in O6 with an owner and trigger rather than left unowned.

**Validity anchor, and the two time concepts split (review-3 F5; review-5 F6).** `targetWindow` and `targetValidFor` are **different things** and must not be conflated: `targetWindow` is the **rolling evaluation window** over which observations are assessed (OpenSLO `timeWindow` / Datadog `timeframe`); `targetValidFor` is the target's **validity duration**. A target is **active at `t`** iff `targetAsOf` is absent **or** `t ∈ [targetAsOf, targetAsOf + targetValidFor]` — a closed interval, both bounds inclusive, evaluated on the same normalized instant as the observations — and when `targetValidFor` is absent (present but sentinel) the target is active from `targetAsOf` onward with **no expiry**. `targetWindow` plays **no part** in activity; when `targetAsOf` is absent the target is always active and `targetValidFor` is inert — stated so the required `targetWindow` is not mistaken for an activity filter.

**Step 2: Write the failing tests** — the earlier draft's fixtures omitted `targetAsOf`; the tests now pin the full contract:

```python
def test_zero_targets_is_absent_and_normalises_empty():
    assert read_targets(set_targets({}, [])) == []
    assert read_targets({}) == []                      # absent
    assert read_targets({"targetAmount": []}) == []    # explicit [] (G17: IS stored) — WHOLE group empty ⇒ arity 0

def test_two_targets_round_trip_in_order(): ...        # same keys in and out: read_targets(set_targets(p,t)) == t

def test_mixed_comparators_and_units_round_trip():
    # review-5 F1: the case the old all-or-nothing rule made unrepresentable.
    group = [dict(t_money_between, targetComparator="between", targetUpperAmount=2_000_000),
             dict(t_count_gte, targetUnit="count", targetComparator="gte")]
    assert read_targets(set_targets({}, group)) == group   # each row keeps its own comparator/unit

def test_unknown_comparator_is_refused(): ...          # ValueError match="comparator"
def test_range_comparator_without_upper_bound_is_refused():
    with pytest.raises(ValueError, match="upper bound"):
        set_targets({}, [dict(t0, targetComparator="between")])

def test_misaligned_group_is_refused():
    # review-5 F2: the trigger is a group written DIRECTLY (open-set passthrough),
    # not via set_targets — which cannot produce one by construction.
    raw = {"targetAmount": [1, 2], "targetMinorUnitExponent": [2],   # one array is SHORT
           "targetUnit": ["BRL", "count"], "targetComparator": ["gte", "lte"],
           "targetWindow": ["30d", "7d"]}
    with pytest.raises(ValueError, match="misaligned"):
        read_targets(raw)


def test_empty_array_in_a_populated_group_is_misaligned():
    # review-6 F2: the PRECEDENCE case. `targetAmount` is `[]` while every other
    # array is populated. A rule that dropped `[]` as "absent" would silently
    # return one target MISSING its required amount; the rule that wins is the
    # length mismatch. `test_zero_targets_is_absent_and_normalises_empty` is
    # single-key, so it passes under EITHER rule and cannot catch this.
    raw = {"targetAmount": [], "targetMinorUnitExponent": [2],
           "targetUnit": ["BRL"], "targetComparator": ["gte"], "targetWindow": ["30d"]}
    with pytest.raises(ValueError, match="misaligned"):
        read_targets(raw)

def test_validity_anchor_uses_target_valid_for():
    props = set_targets({}, [dict(t0, targetAsOf="2026-01-01", targetValidFor="30d")])
    assert active_targets(props, t="2026-01-15") == read_targets(props)
    assert active_targets(props, t="2026-03-01") == []   # past targetAsOf + targetValidFor
    # `targetWindow` is the EVALUATION window and never gates activity:
    assert active_targets(set_targets({}, [dict(t0, targetWindow="1d")]), t="2030-01-01") != []

@pytest.mark.parametrize("n", [0, 1, 2, 5])
def test_roundtrip_boundary_examples(n): ...           # enumerated boundaries (review-1 F9)
```

⚠️ **Not property-based** (review-2 F8): this repo has **no `hypothesis` dependency**, `pbt` is not a layer in `config/ci-surfaces.yml`, and this plan adds no third-party dependency. The real guarantee for index alignment is **construction** — `set_targets` builds every array at length N — plus the **fail-closed `read_targets` check** on a directly-written malformed group; the enumerated boundaries are the examples, not the proof. The `pbt` label has been removed from the `test_layers` mapping.

**Step 3: Implement** `tortoise/indicators.py`'s **pure** half (see Task 6 for the split). Module docstring states the three WS-C constraints verbatim plus the **`OVERRIDES:`** note for WS-C §4 (multi-value out of scope — see Task 7).

**Step 4: Run to verify it passes.**

**Step 5: The embedded round-trip test (review-2 F6 — the graph half was untested).** Add, modelled on `test_7856_goal_state.py::test_goal_state_survives_rebuild`:

```python
def test_target_group_survives_rebuild(tmp_path):
    sdk = TortoiseSDK(str(tmp_path / "t.db"), event_log_path=str(tmp_path / "e.jsonl"))
    ind = sdk.create_object("IND - MRR", objectKind="indicator",
                            **set_targets({}, [t1, t2]))
    sdk._get_proj().rebuild_all(str(tmp_path), confirm_destructive=True)
    assert read_targets(sdk.get_entity(ind["id"])) == [t1, t2]
```

**Step 6: Commit.**

---

## Task 4: Derived-on-read readers

**Intent:** Answer "what is the value / is the target met / what is the parent's state" **without storing** a second source of truth (§11 fold doctrine; comment 1).
**Acceptance:** the readers **reuse** the existing validity/staleness predicates (`is_stale_entry`, `validate_validity_window`) rather than re-deriving them; `active_targets` returns 0/1/many honestly; `current_value` orders on **`(at, createdAt, id)`** — **`createdAt` is the record's resultTime field** (shape pinned in Step 1; no `resultTime` key exists) — and defers staleness to `is_stale_entry`; ⚠️ **corrections are out of scope — no writer in this plan sets the staleness props, so the reader is not described as correction support (O10)**; `target_assessment` returns `no-target` / `ambiguous-multi-target`; `derive_goal_state` returns **`None` unless the goal declares `COMPUTED`**, and its vocabulary is **disjoint** from `GOAL_STATE_VALUES`.

**Files:**
- Modify: `tortoise/indicators.py`
- Test: `tests/test_7860_derived_readers.py` (new)

**Step 1: Fix the seam and enumerate the shared predicates to reuse first (review-5 D2, review-2 F6).** The module has **two explicit halves**, and the tests must respect them:
- **Pure** — no graph handle anywhere in the signature: `set_targets(props, targets)`, `read_targets(props)`, `active_targets(indicator_props, t)`, `current_value(observations, t)`, `target_assessment(indicator_props, value, t)`, `derive_goal_state(goal_props, children=(), t=…)`.
- **Graph-facing** — `link_indicator(sdk, …)`, `record_measurement(sdk, …)`, **`read_observations(sdk, indicator_id)`** (returns the records the pure readers consume; this is the N+1 seam — one read, many readers).

⛔ **The observation record shape is PINNED here (review-6 F1 — the Task 4↔6 seam).** `read_observations` returns each observation in the **stored Event-prop spelling, verbatim**: `{id, at, createdAt, amount, minorUnitExponent, unit, currency, method, externalReference}` (absent optional props omitted). **`createdAt` IS the record's resultTime** — it is the repo's own system-managed record instant (`sdk.py:5543-5564`: synthesized to the write instant when absent, kept verbatim when supplied, and it survives `rebuild_all`), so the writer stores `createdAt` and the reader returns `createdAt`. **There is no `resultTime` key**; `resultTime` is the SOSA/FHIR *concept* name only. `current_value` therefore tie-breaks on **`(at, createdAt, id)`**. One spelling end to end is the point: the earlier draft's writer stored `createdAt` while the reader indexed `resultTime`, so an implementation that passed raw Event props through would read `None` — the middle key silently inert and the ordering degraded to `(at, id)`. This matches Task 3's module-wide convention (stored keys, verbatim).

Reuse `time_aware.is_stale_entry` (supersession + validity window + `live.is_terminal_status` — "the SINGLE shared status predicate, never re-declared here") and `commit_schema.validate_validity_window`. If the observation semantics genuinely differ (append-only `:Event` vs a `:Point` window), **state that as the reason in the docstring** and record it — do not silently fork the rule.

**Step 2: Write the failing tests** — cover `active_targets` (validity excludes `t`; `targetAsOf` absent ⇒ active; `targetWindow` does **not** affect activity), `current_value` (latest by **`(at, createdAt, id)`**, where `createdAt` is the resultTime field pinned in Step 1; **zero observations ⇒ an explicit absent result, never `0`**; a record carrying a closed validity window / `superseded_by` is dropped — the reader **defers to `is_stale_entry`**, and that fixture is synthetic: no in-plan writer sets those props, so **corrections are out of scope (O10)** and this is not a correction-support claim), `target_assessment` (`met`, `breached`, **`no-target`** = 0 active targets — *unassessed, never "failed"*, **`ambiguous-multi-target`** = >1 active, and **exactly one active target with no current value ⇒ `notEvaluated`** — an explicit return, never a falsy default read as "breached"), and `derive_goal_state` (`None` for absent/`MANUAL`/`UNDECIDED`; `AND` **without** `SUFFICIENT` ⇒ **not** met — KAOS: an AND-refinement entails the parent only if complete; `temporal_pattern=MAINTAIN` requires evidence inside a fresh window, `ACHIEVE` does not; `conflict`/`unknown` first-class). Every null path gets its own named expectation (review-2 F7). The tie-break key gets a test that **fails if it is null** — a distinct-`at` ordering test cannot catch that:

```python
def test_current_value_tiebreak_key_is_live():
    """review-6 F1: two observations share `at`; the one that must win carries
    the LATER `createdAt` but the EARLIER `id`. An implementation that indexed
    the wrong/absent key and fell back to `(at, id)` returns the OTHER record —
    exactly the inert-middle-key defect. A test over DISTINCT `at` values
    cannot catch it (that is why cycle 3's write-half-only fix left the rule
    inert)."""
    obs = [
        {"id": "aaa", "at": "2026-10-13T00:00:00Z",
         "createdAt": "2026-10-14T00:00:00Z", "amount": 200},   # later resultTime
        {"id": "zzz", "at": "2026-10-13T00:00:00Z",
         "createdAt": "2026-10-13T01:00:00Z", "amount": 100},
    ]
    assert current_value(obs, t="2026-10-15")["amount"] == 200    # not id order
```

**Step 3: Implement** the pure readers. `derivedGoalState` vocabulary, **disjoint from `GOAL_STATE_VALUES` by construction**:

```python
DERIVED_GOAL_STATE_VALUES = frozenset({
    "unsatisfied", "partiallySatisfied", "denied", "partiallyDenied",
    "conflict", "unknown", "notEvaluated",     # comment 7 §2, convergent (GRL/i*/NFR/KAOS)
})
# A consistency assertion belongs in the test file:
#   assert not (DERIVED_GOAL_STATE_VALUES & GOAL_STATE_VALUES), "ambiguous shared token"
```

**Step 4: Run to verify it passes.**

**Step 5: Commit.**

---

## Task 5: Goal-declaration vocabularies at the SDK boundary

**Intent:** Give the declaration set the same fail-closed treatment `goalState` has (#7856), so a qualitative objective declares `MANUAL`/`CONTRIBUTORY` and `AND + SUFFICIENT` is an **option a goal may declare**, never a default.
**Acceptance:** each word is a closed frozenset validated in `_sanitize_props`; an invalid word is refused on `create_object`/`create_point`/`create_entity`/`update_entity`/`update_point` **before** any graph write; `None` clears; **the four keys are in `_POINT_DECLARED_PROPS`** so a goal Point does not log a false drift warning on rebuild (G16).

**Files:**
- Modify: `tortoise/sdk.py:166` (the `GOAL_*_VALUES` frozensets beside `GOAL_STATE_VALUES`), `sdk.py:3013` (checks in `_sanitize_props`)
- Modify: `tortoise/projection/entities.py:1350` (`_POINT_DECLARED_PROPS`)
- Create/Modify: `tests/test_7860_goal_declarations.py`

**Step 1: The declaration set (record the departures from comment 7 §2 explicitly — review-5 D3).**

| # | Stored prop | Values | Comment 7's declaration | Departure |
|---|---|---|---|---|
| 1 | `goalChildRelation` | `AND OR CONTRIBUTE NONE` | `child_relation` | same |
| 2 | `goalSufficiency` | `SUFFICIENT NECESSARY_ONLY CONTRIBUTORY` | `sufficiency` | same |
| 3 | `goalDerivationMode` | `COMPUTED MANUAL UNDECIDED` | `derivation_mode` (+`UNKNOWN`/`CONFLICT` first-class) | `UNKNOWN`/`CONFLICT` are **derived returns**, not stored modes — recorded |
| 4 | *(derived, not stored)* | the graph edges: child cascade operators + `measuredBy` indicator links | `evidence` (child refs **or** indicator/threshold/worst **or nothing**) | declaration 4 **is realised by the edges**, not a prop — recorded; a goal with no measurement simply has no `measuredBy` link |
| 5 | `goalTemporalPattern` | `ACHIEVE CEASE MAINTAIN AVOID OPTIMIZE` | *optional but valuable* | promoted into the stored set — recorded |

**Step 2–4: TDD as in Task 1 of the earlier draft** — one test per write surface mirroring `test_7856_goal_state.py::test_goal_state_invalid_value_refused_on_*`, plus a `_POINT_DECLARED_PROPS` pin mirroring `test_goal_state_declared_for_point_replay`, plus the **default** assertion: a goal created with no `goalDerivationMode` is **not** computed.

**Step 5: Commit.**

---

## Task 6: Measurement Event, the journaled link, and the cadence boundary

**Intent:** The third layer — an append-only observation Event, **durably** attached to its indicator.
**Acceptance:** `create_event(..., eventKind="measurement")` accepted; the observation→indicator link is an **operator** (not `aboutObject`) and **survives `rebuild_all`**; the value rides the WS-C scalar shape; a second observation at the **same** declared cadence boundary is refused **and an observation in the next boundary is accepted**; the module's pure/graph split is explicit; **no correction writer is built here (O10)**.

**Files:**
- Modify: `tortoise/indicators.py` — graph-facing half: `record_measurement(sdk, indicator_id, *, amount, minor_unit_exponent, unit, at, result_time=None, currency=None, method=None, external_reference=None)` (one signature — review-4 F3/F6)
- Test: `tests/test_7860_measurement.py` (new)

**Step 1: Specify the link mechanism and the two timestamps (⛔ the earlier draft left both implicit).** `record_measurement(sdk, indicator_id, *, amount, minor_unit_exponent, unit, at, result_time=None, currency=None, method=None, external_reference=None)`:
0. **The result time is stored as `createdAt`, and that is the field `current_value` reads** — the design's two-time model (SOSA `phenomenonTime`/`resultTime`; FHIR `effective[x]`/`issued`; §4.7 valid-time/transaction-time). `at` is phenomenonTime; `createdAt` is resultTime. **The record shape is pinned in Task 4 Step 1: `read_observations` returns `createdAt` verbatim and there is NO `resultTime` key** — writer→reader is one spelling end to end (review-6 F1). **Measured (review-3 F2): `create_event` does not set `createdAt` by default, but it IS accepted as a prop and survives `rebuild_all`** — so `record_measurement` must pass it explicitly (`result_time or now()`). Without this the tie-break's middle key is permanently null and the ordering rule is inert (the correction clause is doubly so — out of scope, below). The round-trip test asserts the two timestamps **differ** **and that the record `read_observations` returns carries a non-null `createdAt`**.
1. resolves the indicator's declared `cadence` — **the prop is `cadence`, on the indicator Object, holding an ISO-8601 duration in the repo's `Nd`/`Nh` subset (e.g. `"1d"`, `"7d"`)**; absent ⇒ **no cadence ⇒ the write is allowed** (documented). It is **not** part of the target group and is not declared by #2818 yet — O6.
2. computes the cadence boundary for `at` (floor `at` to the cadence's granularity, e.g. `"7d"` → the ISO week containing `at`) and **refuses** if an observation already exists in that boundary, returning `{"recorded": False, "reason": "over_cadence", "boundary": <iso>, "external_reference": external_reference}` — the value is **echoed input**, not something this code invents (review-3 F9, review-4 F6).
3. creates the Event (`eventKind="measurement"`, props `amount`/`minorUnitExponent`/`currency`/`unit`/`at`);
4. links it with `create_operator("IMPL", event_id, [indicator_id], label="measures", direction="unidirectional")` — **journaled** (G7). **Never `aboutObject`** (G6).

**Corrections are OUT OF SCOPE (review-5 F3).** `record_measurement` has **no** `corrects`/`supersedes` parameter, and nothing in this plan writes `superseded_by`/`valid_to` on an observation Event. The design's Layer-3b correction writer (comment 3 §3b) is **not built here**: a correction mutates an already-journaled Event, so it needs its own journaled path and is tracked separately (**O10**). `current_value`'s staleness clause is therefore **defensive reuse of the shared predicate** (G18), not correction support, and is not described as such anywhere.

**Accepted race, stated (review-2 F9):** step 1–2 is check-then-write, so two concurrent writers can both pass the boundary check. As with `link_indicator`, the race is **accepted and documented**: read-time consumers collapse duplicates (the readers take a set/ordered list, not a single value). The **sequential** duplicate refusal is pinned by a test; the concurrent case is not asserted to be impossible, because it is not.

**Step 2: Module contract.** The two halves are defined in Task 4 Step 1 — *pure* (`set_targets`, `read_targets`, `active_targets`, `current_value`, `target_assessment`, `derive_goal_state`) and *graph-facing* (`link_indicator`, `record_measurement`, **`read_observations`**). The earlier draft's "no graph access in this module" is corrected here; **the tests must not pass an `sdk` handle to a pure reader** (review-2 F6).

**Step 3: Write the failing tests** — including the one that would have caught G6:

```python
from tortoise.indicators import read_observations, current_value, record_measurement


def test_measurement_link_survives_rebuild(tmp_path):
    events = tmp_path / "events"; events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "t.db"),
                      event_log_path=str(events / "events.jsonl"))
    try:
        ind = sdk.create_object("IND - MRR", objectKind="indicator")
        out = record_measurement(sdk, ind["id"], amount=1500000, minor_unit_exponent=2,
                                 currency="BRL", unit="BRL", at="2026-10-13T00:00:00Z")
        assert out["recorded"]
        sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
        # graph-facing read, THEN the pure reader (never an sdk handle in a pure fn)
        records = read_observations(sdk, ind["id"])
        assert records and records[0]["createdAt"]   # resultTime field is LIVE, not null
        assert current_value(records, t="2026-10-14")["amount"] == 1500000
    finally:
        sdk.close()


def test_over_cadence_write_is_refused_sequentially(tmp_path):
    """The refusal must actually happen — a body-less stub would pass vacuously
    and the behaviour would ship unproven (review-3/4 F1)."""
    events = tmp_path / "events"; events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "t.db"), event_log_path=str(events / "e.jsonl"))
    try:
        ind = sdk.create_object("IND - MRR", objectKind="indicator", cadence="7d")
        first = record_measurement(sdk, ind["id"], amount=1, minor_unit_exponent=2,
                                   unit="BRL", at="2026-10-13T00:00:00Z")
        second = record_measurement(sdk, ind["id"], amount=2, minor_unit_exponent=2,
                                    unit="BRL", at="2026-10-14T00:00:00Z",
                                    external_reference="s3://sink/2")
        assert first["recorded"] is True
        assert second["recorded"] is False
        assert second["reason"] == "over_cadence"
        assert second["external_reference"] == "s3://sink/2"
        # nothing extra was written: still exactly one measurement Event linked
        n = sdk._get_proj().g.query(
            "MATCH (op:Point {is_operator:true})-[:IMPL]->(:Object {id:$i}) "
            "RETURN count(op)", params={"i": ind["id"]}).result_set[0][0]
        assert n == 1
    finally:
        sdk.close()


def test_next_cadence_boundary_is_accepted(tmp_path):
    """review-5 F4: the guard must be BOUNDARY-scoped, not "refuse any second
    measurement once cadence is set". An over-broad refusal would pass every
    other Task 6 test while the boundary computation — the feature — stays
    unproven."""
    events = tmp_path / "events"; events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "t.db"), event_log_path=str(events / "e.jsonl"))
    try:
        ind = sdk.create_object("IND - MRR", objectKind="indicator", cadence="7d")
        first = record_measurement(sdk, ind["id"], amount=1, minor_unit_exponent=2,
                                   unit="BRL", at="2026-10-13T00:00:00Z")   # ISO week 42
        nxt = record_measurement(sdk, ind["id"], amount=2, minor_unit_exponent=2,
                                 unit="BRL", at="2026-10-20T00:00:00Z")     # ISO week 43
        assert first["recorded"] is True
        assert nxt["recorded"] is True
        assert nxt["boundary"] != first["boundary"]
    finally:
        sdk.close()


def test_indicator_with_no_declared_cadence_accepts_every_observation(tmp_path):
    events = tmp_path / "events"; events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "t.db"), event_log_path=str(events / "e.jsonl"))
    try:
        ind = sdk.create_object("IND - UNCAPPED", objectKind="indicator")  # no `cadence`
        for day in ("13", "14", "15"):
            out = record_measurement(sdk, ind["id"], amount=1, minor_unit_exponent=2,
                                     unit="BRL", at=f"2026-10-{day}T00:00:00Z")
            assert out["recorded"] is True
    finally:
        sdk.close()


def test_operator_link_is_the_observation_mechanism(tmp_path):
    """Positive contract (review-5 F5): the OPERATOR link is the durable one and
    survives a rebuild — that is what this asserts, NOT the `aboutObject` symptom.
    `aboutObject` is not used because #8398 (OPEN) measured the link as lost on
    rebuild; when #8398 is fixed this test stays green and must not be deleted
    as 'obsolete' (nothing here pins the bug)."""
    events = tmp_path / "events"; events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "t.db"), event_log_path=str(events / "e.jsonl"))
    try:
        ind = sdk.create_object("IND - MRR", objectKind="indicator")
        out = record_measurement(sdk, ind["id"], amount=1500000, minor_unit_exponent=2,
                                 currency="BRL", unit="BRL", at="2026-10-13T00:00:00Z")
        assert out["recorded"]

        def linked() -> int:
            return sdk._get_proj().g.query(
                "MATCH (op:Point {is_operator:true})-[:IMPL]->(:Object {id:$i}) "
                "RETURN count(op)", params={"i": ind["id"]}).result_set[0][0]

        assert linked() == 1
        sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
        assert linked() == 1                              # survives rebuild — the contract
        assert read_observations(sdk, ind["id"])          # and is still readable
    finally:
        sdk.close()
```

Plus the sequential over-cadence refusal, the **next-boundary acceptance** (review-5 F4), and the "no cadence declared ⇒ allowed" test.

**Bounded scope:** no time-series store, no raw-sample pipeline, no rollup scheduler. Raw samples stay **outside** the graph (Postgres, Tier 2 cold — `STORAGE-ARCHITECTURE.md` §2.0/§12.3, cited in comment 3). Only observations **at or below the declared cadence** enter the graph.

**Step 4: Run to verify it passes.** **Step 5: Commit.**

---

## Task 7: Register the plan, the `OVERRIDES:` marker, and the DECISION RELAY

**Intent:** Make the plan discoverable and put the owner decisions where the next lane reads them — on the **issue**, not in this file.
**Acceptance:** the plan is registered in `docs/00_index.md`; a single issue comment carries the owner decisions, the ONTOLOGY diff, and the `OVERRIDES:` line.

**Files:**
- Modify: `docs/00_index.md` (one row in the existing `#NUMBER — topic (note) | path · path` format)

**Step 1: The `OVERRIDES:` line (mandatory per `AGENTS.md` — a deliberate departure must be marked where the adopter reads it).**

> **OVERRIDES:** WS-C's *"multi-value points are out of scope"* (`docs/plans/2026-09-10-ws-c-numeric-normalization.md` §4) — a target is `0..*` by construction (FHIR `Goal.target` is `0..*`, OpenSLO `objectives[]`, Datadog `thresholds[]`), so the target group holds N values on ONE indicator Object; this is not the multi-value *claim Point* WS-C excluded.

**Step 2: Post the DECISION RELAY comment** on `#7860` containing: the applied ONTOLOGY diff (Task 0), the `OVERRIDES:` line, the resolved AMB-1/AMB-2/AMB-3 statements, and O1–O9.

**Step 3: Commit; apply the `planned` label; remove `planning`** (`writing-plans` → Execution Handoff).

---

## Verification — how each step is proven done

| Task | Proof | Command |
|---|---|---|
| 0 (owner) | the Object **and** Event §5 blocks parse to include `indicator` / `measurement` | the inline parser in Task 0 Step 2 |
| 1 | six object-kind surfaces agree; legacy-subset pin unchanged; golden regenerated; packs + calibration probe load clean | `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_7860_indicator_vocab.py tests/test_pack_kinds.py tests/test_pack_content_v3.py tests/test_calibration_tools.py -q` |
| 1 | the byte-pinned prompt render | `uv run pytest tests/test_extractor_v2.py -q -k golden` |
| 2 | both verbs `declarationOnly`; allowlist still **22**; link survives rebuild | `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_7860_link_roundtrip.py -q` |
| 3 | fixed-arity group round-trips **live and through rebuild**; a **wholly-empty `[]` group** ≡ absent, while a **length-0 array beside populated ones is refused as `"misaligned"`**; a **mixed comparator/unit** group round-trips; unknown comparator / range-without-upper-bound / directly-written malformed group refused; boundary examples green | `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_7860_target_field.py -q` |
| 4 | `no-target`/`ambiguous-multi-target` returned, never a falsy default; derived vocabulary disjoint | `uv run pytest tests/test_7860_derived_readers.py -q` |
| 5 | invalid word refused on all five write surfaces; absent mode ⇒ not computed; Point-drift pin | `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_7860_goal_declarations.py -q` |
| 6 | measurement **operator** link survives rebuild; over-cadence refused **and the next boundary accepted** | `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_7860_measurement.py -q` |
| 7 | index row present; issue comment posted | `grep -n 7860 docs/00_index.md` |
| all | no regression in the neighbours this design touches | `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_pack_kinds.py tests/test_7856_goal_state.py tests/test_5089_operator_status_parity.py tests/test_5089_operator_prop_parity.py tests/test_3311_multilabel_status_vocab.py tests/test_calibration_tools.py -q` |

**`test_layers` mapping** — `unit`: vocabulary sets, the declaration vocabularies, `_POINT_DECLARED_PROPS`, `tortoise/indicators.py` pure functions. `integration (embedded graph)`: Object prop write, operator write + journal, `rebuild_all` round-trip (target group **and** measurement link), `measurement` Event write. `e2e`: none (no browser surface). `pgTAP`: n/a (no Postgres in this repo). **No `pbt` layer** — the repo has no property-testing dependency and this plan adds none (review-2 F8); the alignment invariant is upheld by construction in `set_targets` and fail-closed in `read_targets`.

**Dependency DAG (review-1 F10; corrected review-2 F5 and review-3):** `0 → 1 → 2 → 3 → 4 → 6`. Tasks 2, 3, 4 and 6 **all write `tortoise/indicators.py`**, so they are **strictly serial**. Task 6's test imports `read_observations`/`current_value` from Task 4, so `4 → 6` is a real code edge — the earlier `2 → 6` was not. Only **Task 5** (the `sdk.py` declaration vocabularies) and **Task 7** (docs + relay) are independent of the module and may run in parallel with it.

---

## Open items / owner decisions

| # | Item | Blocking? | Action |
|---|---|---|---|
| O1 | **`docs/ONTOLOGY.md` §5** — add `indicator` (Object) and `measurement` (Event); update the legacy-extractor prose (exact diff in Task 0). Daniel-only; `indicator` appears **0** times today. | **YES** | Owner applies. Without it Task 1 reds `test_three_way_diff_empty` and ships the #2786 defect. |
| O2 | **Fallback if O1 is declined** — declare `indicator`/`measurement` as product-strategy pack kinds instead (A6). Then O/I/T 1 (`grep -c indicator docs/ONTOLOGY.md` ≠ 0) **cannot** be met and the issue stays open. | — | Owner chooses; do not pick silently. |
| O3 | **MCP / SDK surface.** This plan adds **no** `TOOL_REGISTRY` entry and **no** public `TortoiseSDK` method. Exposing `targetAssessment` / `derivedGoalState` to agents **would** be a surface change needing approval first (`tools/surface_manifest.py`, `config/sdk-surface.json`, `tools/surface-guard.py`). | no (deferred) | Owner decision if/when the readers must be MCP-reachable. |
| O4 | **`goalState` vs `derivedGoalState` (AMB-2).** Making the derived rollup *be* `goalState` reverses #7856 and ONTOLOGY §5 → a **reopen**, argued in its own home. This plan does not do it. | no | Owner reopens if wanted. |
| O5 | **AMB-1 storage shape** — parallel scalar arrays (chosen) vs one JSON scalar (A10, rejected). The issue does not settle it. | no | Reviewer/owner input welcome. |
| O6 | **Deferred, with mechanism.** #2818 (per-kind value-field **declaration**) — *named owner: the value-layer workstream (#2820); trigger: #2818 closing; this plan's target fields must be migrated from the open-set passthrough to the declared surface in that PR*. **`valueKind`** (comment 3 §3: indicators are **not always quantitative** — `quantity|count|ratio|boolean|ordinal|categorical`) — *owner: #2818/#2820; trigger: same; this plan's numeric target shape cannot express a boolean/ordinal/categorical target and says so rather than pretending otherwise*. `baseline`/range forms beyond `between`/`outside`/target versioning — same trigger. #2817/#2820/#2782 (numeric/money), #7853 + #4997 (EP/vector), #7405/#6968 (live writes). | no | Tracked. |
| O7 | **Retroactive scoping signature** — the issue has none (Prerequisite check). | no | Owner decides. |
| O8 | **AMB-3 scope** — the issue body assigns the cascade/state half to #7856 (CLOSED); this plan lands the goal-state **reader** + declarations. Confirm or split. | no | Owner confirms. |
| O9 | **Vocabulary drift (review-5 D4/D6).** The event axis has **four** copies; `value_extractor.EVENT_KINDS` (`:504`) is dead and already missing `milestone`. This plan neither extends nor consolidates it (scope). The object axis has **five** copies plus the golden. | no | File against **#2786** (the class root) — do not file a peer. |
| O10 | **Corrections out of scope (review-5 F3).** No writer sets `superseded_by`/`valid_to` on an observation Event, so `current_value`'s staleness clause is **inert** and the plan must not claim correction support. Layer-3b correction (comment 3 §3b) is **not built here**. *Owner: **unticketed — raised, not pointed** (searched 2026-10-13: `correction`, `corrects`, `observation invalidation`, `measurement correction` → no issue exists). The **lane executing Task 6** files it when it lands the measurement writer (the writer is what creates a correctable Event), cross-referencing the record-axis history layer **#2349** for where record retraction/validity semantics are owned. Trigger: Task 6 landing — `current_value` is already wired to `is_stale_entry` (G18), so it needs no reader change then.* | no | Tracked. |

---

## Plan Review Log

Cycle 1 (2026-10-13) — 3 reviewers (#1 Structural & Efficiency, #2 Integration, #5 Duplication & Architecture), tier Standard. **21 findings** (1 P0, 13 P1, 7 P2). All verified against the tree before fixing; `NO ISSUES FOUND` was **not** returned.

| Finding (source) | Severity | Fix applied |
|---|---|---|
| Relation entry with neither `fromKind`/`toKind` nor `from`/`to` is a **hard load error**; the new form cannot be declaration-only (#1) | **P0** | Task 2 now pins the **old `from:`/`to:`** shape; verified by loading 5 candidate manifests (G8) |
| Task 2's test could not fail pre-change; the actual effect was untested (#1, #2) | P1 | Task 2 Step 2 asserts `relation_write_verdict == DECLARATION_ONLY` + declaration presence + the 22-entry allowlist pin |
| `nearMisses: [targets, conditionsOn]` does not resolve → load error (#1, #2) | P1 | `nearMisses` **dropped** (G9) |
| **Missed vocabulary consumers**: `value_extractor` core dict + byte-pinned golden, `extractor._OBJECT_KIND_VOCAB`, `calibration_harness`, `migrate_kinds` (#1, #2, #5) | P1 | Added to Task 1's files + verification; `LEGACY_KIND_ALIASES` added; `migrate_kinds` row removed (G3, G11–G14) |
| Event-axis pin test promised but never written; event leg understates its copies (#1, #2, #5) | P1 | Task 1 Step 1 adds the Event-block pin; `measurement` deliberately **not** added to the extractor event copies (A11/O9) |
| `TARGET_GROUP_KEYS` never enumerated; `targetAsOf` contradiction between Tasks 3 and 4 (#1) | P1 | Task 3 Step 1 pins the full key table, optionality and the `asOf`/`direction`/`baseline` scope |
| Graph-write half of the target composite untested (#2) | P1 | Task 3 Step 5 adds the embedded `rebuild_all` round-trip |
| Measurement→indicator link unspecified; `aboutObject` assumed journaled (#2, #5) | P1 | **Verified the opposite (G6)** and specified the operator link (G7); Task 6 Step 3 pins it with a regression test |
| Cadence boundary mechanism undefined; module purity contradiction (#1, #2) | P1 | Task 6 Step 1 specifies the mechanism + refusal return; Step 2 splits the module contract |
| Value vocabulary forked from WS-C (`amountMinor`/`scale` vs `amount`/`currency`/`minorUnitExponent`); multi-value contradicts WS-C §4 (#5) | P1 | Task 3 reuses WS-C's names; Task 7 adds the mandatory `OVERRIDES:` line |
| Derived goal-state vocabulary collides with `GOAL_STATE_VALUES` (shared token `met`); `evidence` dropped from the declaration set (#5) | P1 | Task 4 uses comment 7's disjoint vocabulary + a disjointness assertion; Task 5 Step 1 tabulates every departure from comment 7 §2 |
| Readers re-derive existing validity/correction semantics (#5) | P1 | Task 4 Step 1 names `is_stale_entry` / `validate_validity_window` / `is_terminal_status` for reuse (G18) |
| Deferred work (#2818 etc.) had no re-check mechanism (#1) | P1 | O6 now names an owner and a concrete trigger |
| Task 1 acceptance unsatisfiable (`∩ == ONTOLOGY`); Task 0 acceptance not independent (#1) | P2 | Acceptance restated; Task 0 Step 2 gets its own parser check |
| `[]` **is** written by `_persist_extra_props` — the draft's rationale was wrong (#2) | P2 | Corrected in the Checklist Notes (G17) and pinned in Task 3 |
| Goal-declaration props missing from `_POINT_DECLARED_PROPS` → false rebuild warnings (#2) | P2 | Task 5 adds the declaration + a pin test |
| PBT misplaced (Task 4 not Task 3); duplicated verification (#1) | P2 | Moved to Task 3 |
| Parallelizability not stated (#1) | P2 | Dependency DAG added to Verification |
| Task 4/5 may belong to #7856 (#1) | P2 | Recorded as AMB-3 / O8 for the owner |
| `aboutObject` in the surface map modelled the rejected path (#2) | P2 | Surface #6 now records it as **rejected** |
| AMB-1 "only flat shape" over-constrained (#1) | P2 | A10 records the JSON-scalar alternative and why it is rejected |

Cycle 2 (2026-10-13, **fresh reviewers**) — #1 and #2 re-dispatched. **10 findings** (6 P1, 4 P2). Every cycle-1 fix was re-verified against the tree; three were found to be **asserted but not actually working**, and are now fixed:

| Finding | Severity | Fix applied |
|---|---|---|
| `list_relations()` **drops `declarationOnly`** — the cycle-1 assertion would have returned `predicate-not-in-allowlist` and could never pass (#1, #2) | **P1** | Task 2 asserts the entry's own `write_verdict`, plus the raw relation via `get_pack().relations` |
| Undefined symbols in the Task 1 test: `_CORE_KIND_DESCRIPTIONS` (it is a **local** dict in `compile_value_brief`, `value_extractor.py:196`) and `KIND_MIGRATIONS` (real name `MIGRATIONS`) (#1, #2) | **P1** | Test rewritten to use `compile_value_brief()` and `MIGRATIONS`; the `# name per the real symbol` placeholder is gone |
| **`TestProductDeliveryChain::test_problem_family_relations_ps` indexes `r["fromKind"]` on every `ps` relation** → `KeyError` on the new old-format relations; Task 2's "steps pass" was unachievable (#2) | **P1** | Added to Task 2's Files + Step 4 |
| `sdk._events_dir` does not exist (`_event_log_path` does), and `rebuild_all` takes the events **directory** → the durability test would have `AttributeError`d, leaving the journal claim untested (#1, #2) | **P1** | Task 2's test rewritten to the working `test_7856` pattern |
| **Unnamed coupled guard:** `test_vocab_gating_by_graph.py:529` pins `compile_value_brief()` core keys `== CORE_OBJECT_KEYS` — reds unless the local dict gains `core:indicator` (#2) | **P1** | Added to Task 1 Files + Step 5 + the verification table |
| `current_value` was pure in the contract but called with an `sdk` handle in the test; no graph-facing observation reader existed (#1, #2) | **P1** | `read_observations(sdk, id)` added to the graph half; Task 4 Step 1 fixes the seam; Task 6's test uses it |
| ONTOLOGY prose instruction was self-contradictory ("omits five" while the code adds `indicator` to the vocabulary) (#1) | **P1** | Task 0 now says **13-kind subset**, keeps *"omits the four"*, and names `indicator` as **added** |
| `_ontology_event_kinds()` did not exist; `a <= b or b <= a` cannot fail on a one-directional divergence (#1, #2) | P2 | Parser parameterised; concrete `EVENT_KINDS == ontology` + `>= canonical` assertions |
| `pbt` layer claimed with no property-testing dependency in the repo (#1) | P2 | `pbt` label removed; the guarantee is stated as the write-boundary `ValueError` |
| DAG showed `{3, 6}` as parallel while the same sentence said they serialise on one module (#1) | P2 | DAG corrected to a strict chain; only Tasks 5 and 7 are parallel |
| Task 3's in-scope sentence still said `amount`/`scale` while the table pinned `targetAmount`/`targetMinorUnitExponent` (#1) | P2 | Reconciled to the pinned key vocabulary |
| Null paths under-specified (`zero observations`; one active target with no value) (#2) | P2 | Explicit named expectations added to Task 4 Step 2 |
| The cadence boundary is also a check-then-write race, undocumented (#2) | P2 | Accepted race stated in Task 6 Step 1 + a sequential-duplicate test |
| Surface-map rule violations: #2/#3 had one failure mode each, #6 had layer `—` (#2) | P2 | All three fixed |

Cycle 2 (fresh reviewers) — see above.

Cycle 3 (fresh reviewers; the tier cap is **3** for `standard`/Low-Medium) — **8 findings** (2 P1, 6 P2), all fixed:

| Finding | Severity | Fix applied |
|---|---|---|
| The all-or-nothing rule and the "per-target omission" rule were **mutually unsatisfiable**: three groups are optional, and a group present for one target only yields a short array that cannot be positionally attributed | **P1** | Task 3 Step 1 states **one** rule: a mixed group raises `ValueError(match="all-or-nothing")`; never silently dropped, never padded |
| The design's two-time model was inert: nothing set `createdAt` (= resultTime), so the **`(at, createdAt, id)`** tie-break had a permanently-null middle key | **P1** | `record_measurement` takes `result_time` and passes it as `createdAt` (verified: `create_event` accepts it and it survives rebuild); the round-trip asserts the timestamps differ |
| The `pbt` label survived at three sites (Verification Plan, surface 8, the Task 3 verification row) after cycle 2 removed it from `test_layers` only | P2 | All three sites corrected; the guarantee is stated as the write-boundary `ValueError` |
| The DAG's `2 → 6` ignored Task 6's real import dependency on Task 4 | P2 | DAG corrected to `0 → 1 → 2 → 3 → 4 → 6` |
| A `kindDefs.indicator` entry in `product-strategy` would mint a duplicate brief key `product-strategy:indicator` beside `core:indicator` — the exact two-vocabulary shape A6 rejects | P2 | Dropped; core promotion + the `value_extractor` core dict supply the description |
| `link_indicator` was accepted but exercised by no test | P2 | Idempotency test added |
| `targetWindow`'s anchor was undefined | P2 | Pinned: active iff `targetAsOf` absent or `t ∈ [asOf, asOf + window]`, both bounds inclusive. ⚠️ **Superseded in Cycle 5** — the validity duration was split out as **`targetValidFor`**; `targetWindow` is **evaluation-only**. The live rule is Task 3 Step 1 “Validity anchor” (`t ∈ [targetAsOf, targetAsOf + targetValidFor]`); this Cycle-3 row is history, not the contract |
| Step 5's command did not run the golden test it claimed to prove; `valueKind` was unowned | P2 | Command extended; `valueKind` added to O6 with owner + trigger |

Cycle 4 (fresh reviewers; the sanctioned post-cap recovery re-run) — **9 findings** (1 P1, 8 P2). All **fixed**, but note the exit status: every finding was mechanical consistency work (the elided over-cadence test body given real assertions; the `targetAmountMinor` → `targetAmount` spelling at two sites; the per-target key vocabulary pinned to the stored `target*` names; the `record_measurement` signature reconciled with its refusal payload via an explicit `external_reference`; three missing test imports; the last stale `"property-tested"` phrase). **No fresh reviewer has yet confirmed the post-cycle-4 text** — so this is an **escalation exit, not a clean one**, per the skill's exit conditions.

Cycle 5 (2026-10-13, fresh reviewer, tier Standard) — **6 findings** (3 P1, 3 P2), all applied. These were coherence and testability defects in the *contract text*, not new design:

| Finding | Severity | Fix applied |
|---|---|---|
| The pinned group contract was **unsatisfiable for legitimate mixed groups**: a conditional `targetUpperAmount` + “`targetCurrency` iff money” combined with all-or-nothing made a `between` target beside a `gte` target (or money beside count) unrepresentable, and no rule stated the legal encoding | **P1** | Task 3 Step 1: arrays are now **fixed-arity = N** with **in-band sentinels** (`""` for optional strings; `targetUpperAmount` copied from `targetAmount` for one-sided comparators); mixed comparators/units are explicitly legal |
| `test_misaligned_group_is_refused` had **no trigger** — `set_targets` builds arrays itself and cannot emit a misaligned group, so the claimed write-boundary `ValueError` was unreachable and the test could not fail | **P1** | The unreachable write-side check is dropped; the guarantee is stated as **construction** in `set_targets` plus **fail-closed `read_targets`** (`ValueError "misaligned"`) on a directly-written group. AMB-1, surface 8, the verification rows and `test_layers` corrected |
| `current_value` was required to honour correction invalidation via `is_stale_entry`, but **no writer sets `superseded_by`/`valid_to`** on an observation | **P1** | Declared **out of scope (O10)**: `record_measurement` has no `corrects`/`supersedes`; the reader's staleness clause is labelled **defensive reuse**, not correction support. Task 4 acceptance, surface 8, the bug-pattern flags and Task 6 Step 1 updated |
| The cadence test could not catch an **over-broad refusal** — nothing asserted a different-boundary write is accepted — so the boundary computation stayed unproven | P2 | Task 6 Step 3 adds `test_next_cadence_boundary_is_accepted` |
| `test_aboutobject_is_not_the_mechanism` **pinned OPEN bug #8398** as expected behaviour (asserted the `aboutObject` count `1 → 0`), risking deletion of the guard once #8398 is fixed | P2 | Replaced by `test_operator_link_is_the_observation_mechanism` — a **positive** assertion that the operator link survives; #8398 cited as the *reason*, not the contract |
| Task 3's example omitted `targetCurrency` while the table required it for money, and `targetWindow` carried **two meanings** (OpenSLO evaluation window vs validity duration) | P2 | Example corrected; the validity duration split out as `targetValidFor`; `targetWindow` pinned as evaluation-only in a “Validity anchor” rule |

Cycle 6 (2026-10-13, fresh reviewer, tier Standard) — **4 findings** (1 P1, 3 P2), all applied. The cycle-5 fixes were re-verified as genuinely resolved; these four are remaining seams the fixes exposed:

| Finding | Severity | Fix applied |
|---|---|---|
| The Task 4↔6 **naming seam**: `record_measurement` writes the result time as `createdAt` while `current_value` tie-broke on `resultTime`, and `read_observations`' record shape was never pinned — so a raw-props implementation would index `None`, the middle tie-break key would be **silently inert**, and ordering would degrade to `(at, id)` | **P1** | The observation record shape is **pinned in Task 4 Step 1**: `read_observations` returns the stored Event props verbatim, **`createdAt` IS resultTime and there is no `resultTime` key**; `current_value` tie-breaks on `(at, createdAt, id)`. Task 6 Step 1 states the same mapping, and a **null-detecting test** (same `at`, reversed `id` order, the later `createdAt` must win) was added to Task 4 Step 2 |
| `[]` vs `"misaligned"` **precedence unresolved**: an explicit `[]` “normalizes to absent” collided with the unequal-length `"misaligned"` check for a length-0 array beside populated arrays, and the only `[]` test was single-key (passes under either rule) | P2 | **`"misaligned"` wins**: `[]` ≡ absent only when the WHOLE group is empty (arity 0 ⇒ zero targets); otherwise every present array must be length N and a length-0 array is the mismatch. Stated in Task 3 Step 1, the Checklist Notes, surface 8 and the boundary flags; a **mixed-case test** (`test_empty_array_in_a_populated_group_is_misaligned`) added |
| O10's owner was an **unticketed** item (“the correction work named in the dependency list”), while O6 named issues + a trigger | P2 | O10 now names who raises it and when: the **Task 6 lane** files the correction-path issue when Task 6 lands, cross-referencing record-axis **#2349** |
| The Cycle-3 row still read `active iff targetAsOf absent or t ∈ [asOf, asOf + window]` — **stale** after Cycle 5 split `targetValidFor` out of `targetWindow` | P2 | Row annotated **“Superseded in Cycle 5”** with the live rule (`targetValidFor`; `targetWindow` evaluation-only) |

<!-- plan-review: cycles=6, status=capped, version=2.4.0 -->

> **Requires Human Input — status: capped after 6 cycles (tier cap = 3).**
> **Remaining:** none *known* and unfixed — the 48 findings across six cycles were all applied. The cap is on **verification**, not on known defects: cycle 6's fixes have not been re-reviewed by a fresh agent, so they carry reviewer risk the way cycles 1–5's did (each of those cycles found real defects in the previous cycle's “fixes”).
> **Owner action to close the gate:** either (a) dispatch one more fresh review cycle over the current text, or (b) approve the plan as-is. The items that most warrant (a) are the pinned observation record shape + its null-detecting test (Task 4 Steps 1–2), the `[]`/`"misaligned"` precedence and its mixed-case test (Task 3 Step 1 / Step 2), and the cadence-boundary test bodies (Task 6 Step 3) — all written or rewritten at/after the cap.

---
title: "Plan — #8098 the scheduled webhook retry sweep (epic #2080 W4 phase 2, #2081 follow-up)"
type: engineering
domain: capability
doc_status: draft
created: 2026-10-18
subjects.team: epistemic-team
aboutSubjects: epistemic-team
ownedBy: epistemic-team
governingAgreement: "#8098 (epic #2080 W4 phase 2); predecessor #2081 / docs/plans/2026-10-11-2081-webhook-delivery.md"
aboutObjects: "webhook-delivery, graph-event-stream, epistemic-team"
---

# Plan — #8098: the scheduled webhook retry sweep

**Issue:** #8098 · **Branch:** `feat/8098-tm` · **Depends on:** the delivery layer from #2081
(`tortoise/webhook_delivery.py`, merged).

---

## 1. The problem, re-measured (not inherited)

The issue body's diagnosis reproduced exactly, so no correction was needed: the delivery layer
dispatches at the END of `hosted_api._dream_worker` and ONLY there
(`grep -n "_dispatch_webhooks" tortoise/hosted_api.py` → one call site, inside `_dream_worker`).
That is the right cadence for NEW events — a write enqueues the dream that pushes it — but it is a
**write-triggered** cadence. `dispatch_pending` gates on `next_attempt_at` (durable, correct), and
the only process that would notice the backoff elapsed is a dream that may never run for an IDLE
tenant. So a subscriber that was down for 30 seconds could stay undelivered indefinitely.

**The bookkeeping was never the gap. The trigger was.** That distinction shaped every decision
below: nothing in the retry/backoff/cursor state machine needed to change.

## 2. Solution considered, and why this one

| Option | Verdict |
|---|---|
| A timer per subscription ("sleep until `next_attempt_at`") | ❌ Process-local: a restart loses every timer, and the durable `next_attempt_at` is then unread until the tenant writes. The state is in the GRAPH; a trigger that lives in RAM cannot be its authority. |
| A global queue fed at failure time | ❌ Same defect: the enqueue is in RAM. Also splits the state machine across two homes. |
| Make `dispatch_pending` re-armed by any tenant's dream | ❌ Cross-tenant: one busy tenant would deliver for everyone, and an idle FLEET would still retry nothing. |
| **A scheduled sweep: enumerate tenant graphs → one cheap due read per graph → dispatch the graphs that owe a delivery** | ✅ **Chosen.** The graph is the source of truth (`next_attempt_at` decides), so a restart is harmless; cost is bounded and measurable; no state machine change; additive to the dream trigger rather than replacing it. |

## 3. What shipped

| Piece | Home | Contract |
|---|---|---|
| The due predicate | `webhook_delivery._DUE_PREDICATE` | ONE string, read by BOTH the dispatcher (`_due_subscriptions`) and the sweep's gate (`due_subscription_count`). The gate can never admit a graph the dispatcher no-ops on, nor starve one it would dispatch — the parity test drives a 3-state matrix through both. |
| The cheap gate | `webhook_delivery.due_subscription_count(graph)` | Takes a **raw graph handle** (`db.select_graph(name)`), not a projection: no SDK, no journal bind, no embedder warm-up, no index, no node. Measured (embedded, p50/p95 ms): empty graph 1.6/31.7 · +2000 unrelated nodes 1.8/32.7 · 25 subscriptions 2.1/29.6 — **flat in graph size** (a label scan reads the label's node set, and `MAX_SUBSCRIPTIONS` = 25 bounds it). |
| The knobs | `webhook_delivery.sweep_interval_s` / `sweep_concurrency` / `sweep_enabled` | `TORTOISE_WEBHOOK_SWEEP_INTERVAL` (default 60s; `0`/negative REFUSED — `asyncio.sleep(0)` would sweep the fleet in a busy loop), `TORTOISE_WEBHOOK_SWEEP` (tristate, default ON), `TORTOISE_WEBHOOK_SWEEP_CONCURRENCY` (default 4, clamped to 16). The `TORTOISE_WEBHOOK_DISPATCH` kill switch is honoured by the sweep too — an operator who turned egress off must not keep paying a fleet-wide pass. |
| The enumeration | `hosted_api._webhook_retry_targets` (BLOCKING, on a worker) | `backup_sweep.enumerate_orgs` + `_sweep_graph_list` (NOT a bare `enumerate_org_graphs`: `_sweep_graph_list` SYNTHESIZES the default graph from the authoritative name seam, so pre-#2083 orgs — which have no `Graph` nodes at all — are swept instead of silently skipped). Existence-gated by ONE `db.list_graphs()` (#2251: a query against an ABSENT graph MATERIALIZES it). |
| The pass | `hosted_api._webhook_retry_sweep_pass` | Bounded concurrency on its OWN `ThreadPoolExecutor` (never the loop's shared default pool — #3060/#3498), per-graph failure isolation, and an in-flight set so a pass longer than the interval cannot re-enter a graph. |
| The lifecycle | `hosted_api._webhook_retry_sweep_loop` + `_lifespan` | Armed as a BACKGROUND task in the startup half (#2953: never awaited before `lifespan.startup()` returns, or the machine accepts no traffic until a fleet-wide pass finishes), in its OWN `try` (a bad interval disables the sweep only — sharing the retention `try` would take event retention, the purge and the OAuth GC down with it), registered in `_LIVENESS_TASK_ATTRS` so shutdown cancels it and a re-entry cannot orphan a second copy. |
| Control plane on a worker thread | `hosted_api._control_plane_source_on_worker` | A sibling of `_control_plane_source`, differing ONLY in the registry lane's lifetime: `_registry_anchor()` (process-lifetime) instead of a fresh-and-never-closed handle per call — an hourly operator can afford the fresh handle, a 60s pass cannot. The #669 half is unchanged (Supabase mode never constructs the registry). |

## 4. The one refactor this needed

`_dispatch_webhooks` was split into a blocking body (`_dispatch_webhooks_sync`) plus two async
wrappers, because the two triggers must offload to DIFFERENT lanes and must not duplicate the
SDK-build/close discipline. The dream path keeps `asyncio.to_thread` **unchanged** (its historical
lane, and its blast radius is not this issue's); the sweep uses the bounded executor. Both share
one body, so a future change to the SDK-open rules lands once.

## 5. Verification (the issue's own checklist)

| Surface | Layer | Test |
|---|---|---|
| sweep trigger | integration | `test_an_idle_tenant_is_retried_by_the_sweep_and_its_cursor_advances` — fail a delivery at `now-2h`, write NOTHING to the tenant graph, run one pass, read the cursor back from the store (asserts it advanced, attempts reset, and the `:GraphEvent` count is unchanged). |
| cost | unit | `test_the_gate_is_one_read_that_writes_nothing_and_creates_no_index` + `test_a_graph_that_does_not_exist_is_never_selected_or_read` (diffs nodes and `CALL db.indexes()`). |
| shutdown | unit | `test_the_sweep_task_is_in_the_shared_liveness_tuple_and_shutdown_cancels_it` + `test_the_sweep_is_armed_as_a_background_task_not_awaited_at_boot`. |
| the trigger is actually ARMED | unit | `test_the_periodic_loop_arms_the_sweep` (drives the real loop — the #1890/#4241 shape: a definition-only test is green for the life of the bug) and `test_a_raising_pass_cannot_end_the_loop` (#5381). |

**Mutation-checked** (a test that cannot fail is not evidence): removing the existence gate reds
the absent-graph test; dropping the predicate from the gate query reds the parity test.

## 6. Deliberately NOT in scope

* **Cursor monotonicity under two concurrent passes.** The sweep introduces a second trigger, so a
  dream-cycle pass and a sweep pass for the same graph can overlap. The contract already covers it
  (at-least-once + a stable `X-Tortoise-Delivery` id ⇒ the consumer must be idempotent), and the
  in-flight set removes the sweep-vs-sweep overlap. A monotone `cursor` write (`CASE WHEN s.cursor
  < $cursor`) would close the dream-vs-sweep duplicate window too — filed as a follow-up rather
  than smuggled into a trigger PR, because it changes the delivery engine's write, not its schedule.
* **Per-graph time bound.** A pass is bounded by the executor size and `TIMEOUT_S` per POST, not by
  a wall clock; a graph with 25 healthy-but-slow subscriptions can outlive the interval. The
  in-flight set makes that safe (the next pass skips it) rather than queued.

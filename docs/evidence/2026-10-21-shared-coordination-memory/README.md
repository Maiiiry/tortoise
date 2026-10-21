---
title: "Fleet coordination state — the inventory, the measured volume, and the costed hosted Tortoise option"
type: evidence
domain: platform
doc_status: final
created: 2026-10-21
subjects.team: epistemic-team
aboutSubjects: fleet coordination state, claims, dispatch ledger, capacity map
aboutObjects: "tortoise, agent-infra#1709, tortoise#5052, tortoise#7871, product/pricing.json, STORAGE-ARCHITECTURE.md §17"
---

# Fleet coordination state — where it lives, what it costs to host, and the one slice moved into the graph

**Measured on:** `2026-10-21`, on branch `feat/graph-work-claims` (worktree `.worktrees/graph-claims`),
against the committed fleet-map census of `2026-10-17` and a private lane graph for the per-claim probe.
**Owner ruling (relayed, not re-litigated):** the wait for Tortoise's Postgres migration is over — the
hosted cost is to be explored NOW and the hosted version used rather than blocking on the migration.
Recorded on `daniel-ospina/tortoise#7871` (the graph issue that maps this) and
`daniel-ospina/agent-infra#1709` (the claim-work issue).

## 0. What the inventory says, in one sentence

**The fleet has exactly one store two boxes can both read — `refs/claims/*` on a git origin — and it
deletes its own history on release, while every store that keeps history lives on one box's `/tmp`.**

That is not a metaphor. It is the measured reason two boxes were handed the same fix and produced
`#2247` and `#2250`, 31 minutes apart, and nothing noticed. Neither PR has a claim ref: the registry
only stops a duplicate for work someone remembered to claim, and dispatch does not require one.

## 1. Inventory — every piece of coordination state, where it physically lives

Read on `2026-10-21` on cell-3 (`HOME=/tmp/cell-home`); counts are as-of that read, not estimates.

| State | Where it physically lives | Which machine can read it | Durable across a reboot? |
|---|---|---|---|
| **Claim on a unit of work** (issue / PR head / landing) | `refs/claims/*` on the **target repo's git origin**; owner + `lease_until` in the claim commit message; **deleted** (`--force-with-lease`) on release | **Any box with read access to origin** — the only genuinely cross-box store. `git ls-remote 'refs/claims/*'` = **7 refs** in `tortoise`, **7** in `agent-infra` | ✅ the ref is on GitHub · ❌ **the history is not** — a released claim is deleted, so "who held #5052 yesterday" is unanswerable |
| Claim snapshot cache | `$HOME/orchestrator/state/claims-snapshot` (**7 lines**) | that one box only | ❌ `$HOME` is `/tmp/cell-home` — **wiped on reboot** |
| Capacity / lane state | `$HOME/.pi/agent/state/fleet-state.json` (**19,952 B**, `generated_at 2026-10-21T03:20`) + `lane-registry.tsv` (**82 B**) | that one box only; the file is a **12-hour-old snapshot** when read | ❌ under `/tmp`; regenerated, never inherited |
| Dispatch ledger / worklist | `$HOME/orchestrator/state/` — **187 files, 380 KB** (`candidates.txt` 13 lines, `checked.txt`, `fixcount-*`, `collided`, `claim-hosts`, `disk.tsv`) | that one box only | ❌ under `/tmp` |
| WIP ownership (which lane is on which branch/worktree) | `.worktrees/<lane>` on each box + local branches | that box; `tools/who_is_on.py` enumerates **remote refs only**, so a never-pushed branch or a worktree's uncommitted work is invisible (#4256) | ❌ worktree is local disk |
| PR ownership | GitHub assignee/review state **plus** `refs/claims/pr-<N>` (**2 refs** today) | every box (GitHub) | ✅ GitHub · ❌ the claim half is deleted on release |
| Fleet capacity map (in flight) | `refs/boxes/<host>/<record>` — `capacity.sh` in `agent-infra` (`feat/capacity-autonomy`), published on the same origin | every box **once a box publishes**; `git ls-remote 'refs/boxes/*'` = **0 refs today** | ✅ by design · ❌ **nothing has been published yet**, so no box can read a peer's capacity right now |
| The owner's context | a human, one session | the human | ❌ |
| Prose on GitHub | issue bodies, PR bodies, comments | every box | ✅ — but **not machine-readable**: no CAS, no expiry, and a gate that trusted a comment blocked a P1 forever (#8175) |

### 1.1 Which of them are already broken across boxes — stated plainly

- **The claim registry is correct and unread at the point that failed.** The measured duplicate
  (`#2247` / `#2250`) happened on work dispatched **without** a claim. A registry that only stops
  duplicates for claimed work does not stop the unclaimed dispatch that produced the duplicate.
- **The claim store's own history is gone by design.** 7/7 refs in each repo are stale (#2246: "7 refs
  / 25 h / 0 lanes"), and every released claim was deleted — so the store cannot answer the one
  question an inventory asks: *who owned this, and when did they stop?*
- **Every history-keeping store is single-box, and on a cell it is on `/tmp`.** `fleet-state.json` is a
  snapshot of the box that wrote it; `who_is_on.py` sees only what a remote ref happens to name.
- **The capacity map, the one in-flight fix for exactly this, has published nothing yet** (`refs/boxes/*`
  = 0 refs), so today's placement decisions are still local — which is how cell-1 sat at load 29.8/8 cpu
  while cell-4 sat at 0.56/8.

## 2. The costed hosted option — measured volume, then price

### 2.1 The volume, measured (not estimated)

| Quantity | Measured value | Source |
|---|---|---|
| Fleet map graph, as a hosted tenant would hold it | **21,065 nodes** — 10,374 `:Point`, **10,583 `:GraphEvent`**, 60 `:Source`, 1 `:Event` | `docs/evidence/7983-cost-magnitudes/graph-content-census.json` (2026-10-17, live self-hosted map at `:16379`) |
| Resident size of that map | **35 MB graph memory**, **0.1499 GB** at the measured 1,672 B/resident node | same census; `STORAGE-ARCHITECTURE.md` §17.4.6 |
| **Charged** nodes of that map | **≈10,435** (Points + Object/Subject/Event; the `GraphEvent` journal is **not** charged) | `tortoise/quota.py` (the `points` counter IS the pricing `max_graph_nodes` cap) |
| **One claim**, measured on a private lane graph today (`docker://:@127.0.0.1:16393`) | **+2 nodes per claim** (1 `:Point` + 1 `:GraphEvent` journal entry); **+5 MB for 3,000 claims** (incl. 2 MB of index) ⇒ **≈1,748 B resident/claim**; **10.7 ms per create** (3,000 creates in 32.0 s) | this lane's probe, `GRAPH.MEMORY USAGE` + `MATCH (n) RETURN count(n)` |
| Chargeable nodes per claim | **1** (the journal entry is not counted; the Point is) | `tortoise/quota.py` |
| **Writes per day** — lane dispatches ≈ PR creation | **63–118/day** across the two repos: `tortoise` 39/81/52/28 PRs on 10-18…10-21, `agent-infra` 27/37/27/11/18 | `gh pr list --state all` on each repo, run today |
| **Claims per month** | **≈3,000** (100/day × 30) ⇒ **3,000 chargeable nodes**, **6,000 resident nodes**, **≈5 MB**, **≈6,000 write ops** (create + release) | derived from the two rows above |
| **Reads per dispatch pass** | **13** candidates (`candidates.txt`), i.e. ~13 `status` reads per pass; 360 passes/day/box ⇒ **≈4,680 reads/day/box** | `$HOME/orchestrator/candidates.txt`, the 240 s cycle |

### 2.2 The price (every number sourced)

| Line | Rate | Source |
|---|---|---|
| Plan tiers | anon 1k · **free 10k nodes / 10k writes, $0** · **solo 25k / 10k, $9** · **pro 100k / 50k, $25** · **team 600k / 200k, $149** | `product/pricing.json` (`status: current`, `owner_confirmed: 2026-08-07`) |
| Writes beyond the included volume | **$5 per 10k write ops**; **reads are free** | same file (`billing.overage_price_per_10k`, `reads_free: true`) |
| FalkorDB Cloud RAM | **$73 / 1 GB / month** (STARTUP) · **$350 / 8 GB / month** = $43.75/GB (PRO) | `docs/architecture/STORAGE-ARCHITECTURE.md` §17 (the two-store price table) |
| Disk / egress / IOPS | $0.125/GB · $0.09/GB (250 GB incl.) · $0.024/IOPS (3,000 incl.) | same table |
| Compute we already run | Fly app `tortoise-y4mjjq`, `shared` cpu, **2 cpus / 4,096 MB**, `min_machines_running = 1` | `fly.toml` |

### 2.3 The answer

- **Coordination state alone** (claims only, retaining history — the point of moving it): 3,000
  chargeable nodes/month at 100 claims/day. Free's 10,000 nodes holds **~3 months**; **solo ($9/mo,
  25k)** holds **~8 months**; **pro ($25/mo, 100k)** holds **~33 months**. Writes stay inside every
  tier's included volume (**6,000/month** against free's 10,000). Reads are free, and the documented
  per-key ceiling — **100 req/min ≈ 10–20 tool calls/min** — is four orders of magnitude above the
  measured **~0.25 reads/s** this workload needs.
- **Coordination state + the existing fleet map in one graph** (the useful configuration — the map is
  what a lane steers by): **10,435 chargeable nodes today**, which **already exceeds free's 10,000**, so
  the floor is **solo at $9/month**; the combined count crosses solo's 25,000 in **~5 months** at the
  measured claim rate, at which point it is **pro at $25/month**. Per box (3 boxes): **$3–8.33/box/month**.
- **Marginal infrastructure on our own hosted deployment: $0.** The whole state is **0.15 GB** against
  an **8 GB PRO FalkorDB Cloud** instance already on the bill at $350/month (~1.9% of that line), and the
  Fly app it fronts is already running for the product. Upgrading a *plan* is a product decision about
  our own internal tenant; it is not new infrastructure.
- **What is missing to use hosted today** (measured, not assumed): **a credential.** `.mcp.json`'s
  `tortoise` entry declares `https://api.premiselabs.co/mcp/` with **no `Authorization` header**, and
  neither `TORTOISE_API_KEY` nor `TORTOISE_API_URL` is set in a cell environment — so every cell writes
  to its own box-local graph (`:16379`, `docker://`) or to nothing. Second, a plan **≥ solo**, because
  the map alone is past free's cap. **Nothing else**: the pilot's SDK calls (`create_point`, `get_point`,
  `update_point`, `query`) are identical on hosted — the endpoint is the only difference.
- **So the cost to close is one key and $9–25/month — and it does not pass through Postgres.** The claim
  slice needs Point create/read/update only; the relational record store (`#7879`) is not on this path.
  That is the measured answer to "are we waiting on the migration": **no, there is nothing to wait for.**

## 3. The pilot — one slice, the claim, in the graph

`tools/work_claim.py` (this branch), `tests/test_work_claim.py` (18 tests), contract `rc 0/1/2` identical
to `claims.sh` so a caller needs no new branch.

**What it stores.** One `:Point` per held unit, id = `sha256(repo, key)` rendered as the SDK's ULID shape —
**deterministic, and never containing a box** (`claims.sh`'s own recorded lesson: *"a KEY CONTAINING THE
BOX ID IS NOT A CAS"*). Content = one canonical line (`repo`, `key`, `owner`, `session`, `branch`, `head`,
`lease_until`, `epoch`, `purpose`, `claimed_at`, plus the capacity fields); props are the same fields
namespaced (`claim_owner`, `claim_epoch` — the SDK **silently ignores** a client-supplied `owner`, measured
on a live write). The **graph's id uniqueness is the CAS**: `create_point` on an existing id returns the
**existing** point rather than overwriting (verified live), so the second box is handed the winner's record
and refuses with the holder's name.

- **create = CAS**, keyed `issue-<N>` / `pr-<N>` — disjoint prefixes, like `claims.sh`'s two keyings.
- **read-back is the authority**, never the write's exit code.
- **lease is data, not liveness** (`#1709` rec 3) — a remote pid cannot be probed.
- **fencing by epoch** (`#1709` rec 6) — a reclaim bumps it, and a lost reclaim names the winner.
- **release expires in place instead of deleting**, which is the one behaviour deliberately different from
  `refs/claims/*`: the ledger keeps its history, so "who owned this, and when did they stop" is answerable.
- **the capacity fields are stored, never measured** — `capacity.sh` measures (`host`, `ncpu`, `lpc`,
  `lanes`); this tool persists them **with the claim**, so the fleet's claim map and capacity map are one
  record. It is not a second probe, and it does not touch `capacity.sh`.

### 3.1 The failure path — it REFUSES, and says why

`rc 2` is a **refusal**, and the controller must not dispatch:

```text
$ env -u TORTOISE_DB_URI -u TORTOISE_API_URL tools/work_claim.py acquire --repo … --issue 5052
REFUSED: no claim store configured: set TORTOISE_DB_URI (self-hosted) or TORTOISE_API_URL +
TORTOISE_API_KEY (hosted). An unset store is refused rather than answered from a default target
— an empty read from the wrong graph looks exactly like 'nobody holds this' (#7871).
rc=2
```

Every shape of "I cannot answer" is rc 2, and each one names its cause: an **unset** store (refused
*before* any query — the SDK would otherwise answer from a default target, which is #7871's measured
read defect, "an unset URI returning 0 rows silently"), an **unreachable** store, a **write whose
read-back fails**, and a store that **neither creates nor returns** a claim. A reachable store with no
claim is a different, real answer (`status: free`, rc 0) and the two never collapse.

⛔ **This is a deliberate departure from a recorded decision, and it is marked as one:**

> **OVERRIDES:** the fleet claim registry is fail-OPEN when its store is unreachable
> (agent-infra#1709, 2026-10-14 — chosen because review/merge duplicates are idempotent per head) —
> this dispatch-time claim is fail-CLOSED instead, because the measured cost of proceeding blind at
> dispatch is a duplicated lane and PR (#2247/#2250), not a repeated idempotent side effect.

The recorded decision is not overturned here. `claims.sh`'s merge/review path keeps its fail-open
behaviour untouched (this branch does not edit it), and the departure is **scoped to dispatch**, with
the evidence for reopening the broader decision posted on `agent-infra#1709`.

## 4. Contradiction test — the recorded decisions this work had to pass

Two statements were found that could have owned this question, and both are addressed rather than
overridden silently:

1. **`agent-infra#1709` (2026-10-14, scoping comment):** *"a coordination primitive must **fail open**
   when its store is unreachable rather than stall the fleet"* — recorded for the **review/merge** path.
   Addressed by scoping the fail-closed behaviour to **dispatch** and adding the `OVERRIDES:` line above.
2. **`agent-infra#1709` (research, rec 2):** *"Implement the cross-box claim as a **git ref**, with no
   new infrastructure."* Addressed by **not replacing it**: `refs/claims/*` keeps working and stays the
   merge-path authority (its "acquire-time CAS is the authority" design is copied, not contested). What
   the measurement adds is that the ref store **cannot keep the history** this inventory requires
   (released claims are deleted) and that "no new infrastructure" is now a **$9–25/month** line rather
   than an unknown — which is exactly the cost the owner ruling asked to be explored rather than deferred.

No recorded decision was found assigning coordination state to any other store, or restricting the graph
to product memory only. `tortoise#7871` (the graph as the orchestrator's map) and `tortoise#5052` (an
authoritative, expiring claim registry) both point at the same gap this pilot fills for one slice; `#5052`
specifies the record's fields (`repo, issue, session, branch, head-sha, ttl, purpose`) and this pilot
stores all of them.

## 5. What was deliberately NOT done

- **Did not touch `claims.sh`, `capacity.sh`, `orch-loop.sh`, or any controller** — those are live lanes
  (`feat/capacity-autonomy` / PRs `#2247`, `#2250`). This branch ships the store and its contract; wiring
  dispatch to call it is a separate, reviewable change in `agent-infra`.
- **Did not migrate any other slice** — no worklist, no worktree registry, no PR publish pipeline. The
  brief was one slice, and a ledger that grows by one authority at a time is the point.
- **Did not declare the kind in a pack.** `work-claim` is registered in-process
  (`domain_loader.register_kind`); promoting it into `packs/agent-ops/manifest.yaml` is the follow-up
  once a second consumer exists.
- **Did not re-litigate the owner ruling**, and did not treat `#7871`'s status as a blocker: its gap 0
  ("the live graph refuses every write") was measured against the **box-local** graph at `:16379`; every
  write in this pilot was verified against a live graph, and the hosted tenant is a different endpoint.

## 6. Reproducing

```bash
# the per-claim volume probe (private lane graph, so no peer's target is touched)
uri="$(uv run python tools/test_lane.py uri)" && eval "$uri"
export TORTOISE_DB_URI="$uri"
uv run pytest tests/test_work_claim.py -q            # 18 passed, incl. the live SDK path

tools/work_claim.py acquire --repo daniel-ospina/tortoise --issue 5052 \
    --session cell-t1 --branch feat/x --purpose "pilot" --host cell-t1 --ncpu 8 --lpc 372 --lanes 3
tools/work_claim.py status --repo daniel-ospina/tortoise --issue 5052 --json
tools/work_claim.py release --repo daniel-ospina/tortoise --issue 5052 --owner "$(hostname):$$"
```

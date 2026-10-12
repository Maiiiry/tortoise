---
title: "Orchestrator harness — autonomous lane dispatch and merge rail (`~/orchestrator`)"
type: operations
domain: platform
created: 2026-10-12
ownedBy: organisation-design-team
subjects.team: organisation-design-team
aboutSubjects: organisation-design-team
aboutObjects: daniel-ospina/tortoise, tortoise#8246
doc_status: draft
---

# Orchestrator harness

> **What this is.** A self-contained, single-host harness that turns one laptop into an
> autonomous delivery lane: it sweeps the open PR queue, dispatches GitHub issues into
> isolated worktrees as autonomous `pi` lanes, runs a fresh-context review → signed-record
> → merge chain on each result, and repairs the PRs whose reviewer found real problems.
>
> **Where it came from.** Built ad hoc in `~/orchestrator/` on one box during the
> 2026-10-10 → 2026-10-11 run that merged ~99 PRs (77 of them between
> `2026-10-10T23:45Z` and `2026-10-11T21:22Z`). It was never version-controlled. This
> directory is the versioned handover so it can be **evaluated** before being adopted.
>
> **Status: evaluation artifact.** Nothing here installs or runs on its own. `install.sh`
> renders a launchd plist and *prints* the load command; it does not execute it.
> Tracked by tortoise#8246.

---

## 1. Why it exists

The repo's normal path is one agent, one issue, one PR, human-paced. On a box that is left
running overnight that path is idle most of the time. This harness keeps the box saturated
without a human in the loop:

- **Fan-out:** N simultaneous implementation lanes, each in its own git worktree.
- **Fan-in:** a per-PR chain that reviews, records the review as a gate artifact, and merges.
- **Back-pressure:** every cycle is gated on load average and swap, so the box throttles
  itself instead of thrashing.
- **Self-healing:** a PR whose reviewer reports real findings is routed to a *fixer*, fixed,
  and re-reviewed — rather than being left red forever (this is the bug that stranded ~15
  PRs before it was fixed; see §7).

It deliberately does **not** use the `task` sub-agent tool. Workers are separate `pi -p`
processes in tmux sessions, so a killed worker cannot take the controller down with it.

---

## 2. Architecture

```
                      launchd  com.eldato.orchestrator-loop  (RunAtLoad + KeepAlive)
                                        │
                                  orch-loop.sh        ← the controller, one process
                                        │
        ┌───────────────────────────────┼────────────────────────────────┐
        │                               │                                │
   (1) merge sweep              (2) capacity-gated dispatch      (3) backlog refresh
        │                               │                                │
        │  for every open non-draft PR  │  read candidates.txt            │  refresh-candidates.sh
        │  read the 8 required checks   │  skip if in dispatched.txt      │  pre-flight each issue
        │                               │  dispatch-merge.sh:             │  (collision_preflight.py)
        │   GREEN   → gh pr merge       │    git worktree add             │  append CLEAN ones
        │   PENDING → --auto            │    tmux new lane-<N>  ──┐       │
        │   FAILED  → see below         │    tmux new chain-<N> ──┤       │
        └───────────────────────────────┴─────────────────────────┼───────┘
                                                                  │
                        ┌─────────────────────────────────────────┘
                        ▼
        lane-<N>   → lane.sh   : pi -p in .worktrees/tm-<N>, implement → commit → push → open PR
                        │
        chain-<N>  → chain-merge.sh : poll for the PR to appear, then
                        ▼
        merge-<PR> → merge-one.sh :
                        1. fresh-context reviewer → posts its review as a PR comment (the audit artifact)
                        2. CLEAN   → record-review.sh (HMAC-signed marker into the PR body)
                                     → wait for ai-review-gate SUCCESS → gh pr merge --squash
                            ISSUES  → touch state/needsfix-<PR>-<head>   ← hands off to the fixer path
                        ▼
        fix-<PR>   → fix-one.sh  : materialises a worktree for the branch (including foreign
                                   branches), runs a fixer over the reviewer's findings, pushes a
                                   new commit, then re-runs merge-one.sh end-to-end
```

### Worker types

| Session | Script | Writes to | Purpose |
|---|---|---|---|
| `lane-<N>` | `lane.sh` | `.worktrees/tm-<N>` on `feat/<N>-tm` | implement issue #N → commit → push → open PR |
| `chain-<N>` | `chain-merge.sh` | — | wait for the lane's PR, then delegate to `merge-one.sh` |
| `merge-<PR>` | `merge-one.sh` | — | fresh review → signed record → wait green → squash-merge |
| `fix-<PR>` | `fix-one.sh` | the PR's worktree | repair findings, push, re-run the merge chain |

### State files (`$O/`)

| File | Meaning |
|---|---|
| `heartbeat` | epoch seconds, rewritten every cycle — the liveness probe |
| `.orch.lock` | atomic single-instance lock (noclobber create); stale locks are reclaimed |
| `candidates.txt` | the dispatch backlog: issue numbers that passed collision pre-flight |
| `dispatched.txt` | issues already handed to a lane. **Append-only — never expires** (see §7.1) |
| `checked.txt` | issues already pre-flighted; prevents re-running the collision check |
| `state/needsfix-<PR>-<head>` | "a reviewer found real issues at this exact head" |
| `state/fixcount-<PR>` | fixer attempts for that PR, capped by `MAX_FIX_TRIES` |
| `state/rerun-<PR>-<runid>` | CI re-run already requested for this run — prevents loops |
| `state/merged-<PR>.done` | merge confirmation |
| `logs/` | one log per worker: `lane-*.log`, `chain-*.log`, `merge-*.log`, `fix-*.log`, `orch-loop.log` |
| `wip/` | dirty-worktree captures taken before a shutdown (see §6) |

### Why the *reviewer* is a separate process from the *controller*

`merge-one.sh` spawns a fresh-context `pi -p` reviewer that has never seen the implementing
lane's reasoning. It reads the changed files at the PR head (`git show origin/<branch>:<path>`)
rather than only diff hunks, and posts its verdict as a PR comment. That comment is the
evidence the `record-review.sh` signature points at, so the merge gate is auditable after
the fact: the PR body names the comment it was signed against.

---

## 3. Prerequisites

| Dependency | Why | Notes |
|---|---|---|
| `tmux` | every worker is a detached tmux session | `brew install tmux` |
| `gh` (authenticated) | all queue + PR operations | needs push access to the target repo |
| `pi` on `PATH` | the worker runtime (`pi -p --provider deepseek --model deepseek-v4-flash`) | provider/model are literals in `lane.sh`/`merge-one.sh`/`fix-one.sh` — change them there |
| `uv` | `lane.sh` runs `uv sync --extra embeddings --extra parity` in each worktree | tortoise-specific |
| `~/.pi/agent/scripts/record-review.sh` | writes the HMAC-signed review marker the `ai-review-gate` check verifies | shipped by `agent-infra` |
| **`AI_REVIEW_GATE_KEY`** | the merge authority — env var, or `~/.pi/agent/.ai-review-gate-key` | **this harness cannot merge without it.** Treat as a secret; never commit it |
| the target repo's `tools/collision_preflight.py` | `refresh-candidates.sh` refuses overlapping dispatches | tortoise-specific path |
| `.worktrees/` gitignored in the target repo | worktrees are created under it | `git check-ignore .worktrees` |

**Single-host assumption.** Everything is local: tmux sessions, worktrees, logs, the key.
There is no scheduler, no queue service, no shared database. Two boxes running this against
the same repo work fine *because* GitHub is the coordination medium — but they will duplicate
work unless they coordinate (see §5, "broad mode").

---

## 4. Install

```bash
# 1. Put the harness where $O will point (default ~/orchestrator)
mkdir -p ~/orchestrator && cp operations/orchestrator-harness/*.sh ~/orchestrator/
mkdir -p ~/orchestrator/logs ~/orchestrator/state ~/orchestrator/wip

# 2. Point it at your box (all three are overridable; defaults are the original box's values)
export OR=daniel-ospina/tortoise         # owner/repo
export REPO=$HOME/eldato/tortoise        # the local main checkout
export O=$HOME/orchestrator              # the harness state directory

# 3. Render + install the launchd agent (does NOT load it)
./install.sh

# 4. Start it when you mean to
launchctl load -w ~/Library/LaunchAgents/com.eldato.orchestrator-loop.plist

# liveness / stop
cat ~/orchestrator/heartbeat                 # epoch seconds, should be < ~5 min old
tmux ls                                      # lane-*, chain-*, merge-*, fix-*
launchctl unload -w ~/Library/LaunchAgents/com.eldato.orchestrator-loop.plist
```

`install.sh --uninstall` unloads the agent and removes the plist.

### Tuning (8 GB / 8-core box — the values that were actually used)

| Var | Value | Why |
|---|---|---|
| `MAX_LOAD` | `16` | dispatch stops above this 1-min load |
| `MAX_LANES` | `4` | concurrent implementation lanes |
| `MAX_REVIEW` | `4` | concurrent `merge-<PR>` reviewers |
| `MAX_FIX` | `3` | concurrent `fix-<PR>` fixers |
| `MAX_FIX_TRIES` | `3` | per-PR fixer attempts before it is left alone |
| `MIN_SWAP_FREE` | `600` | MB; **the binding gate in practice** |
| `BATCH` | `2` | issues dispatched per cycle |
| `CYC` | `240` | seconds between cycles |

**Swap, not lanes, is what actually throttles this box.** Observed at 15 concurrent workers:
`swap_free=588M` → dispatch correctly refused. Raising `MAX_LANES` alone buys nothing on 8 GB;
it just moves the pressure into the compressor. `swap_total=0` right after boot is treated as
*uninitialised*, not as pressure (that bug silently blocked all dispatch for the first minutes
after a restart until it was fixed).

---

## 5. Merge policy and safety invariants

1. **The gate is never bypassed.** A merge requires `record-review.sh` to have written a
   signed marker for the *exact head SHA* into the PR body, and the `ai-review-gate` check to
   go `SUCCESS`. `merge-one.sh` refuses to merge if the recorded head ≠ the current head, so
   a post-review push invalidates the evidence automatically.
2. **The reviewer must state a plain outcome.** Its final line is exactly `CLEAN <comment-url>`
   or `ISSUES <n>`. A CLEAN comment may not contain "issues remain", "NOT CLEAN", or
   "requires human attention" — forbidden phrases, so an equivocal review cannot be laundered
   into a merge.
3. **Scope adapts to the queue.** `open PRs > 10` → **broad** mode: the sweep covers *every*
   non-draft PR in the repo, not just this box's. `≤ 10` → own branches only
   (`*-tm`, `*-cap`, `fix/*`). Broad mode is how this box drains a peer's queue; it is also
   how two boxes accidentally duplicate work if they both run broad.
4. **Drafts are deliberately excluded** from the sweep — a peer's draft PR is not yours to
   merge. The cost is that our *own* lanes' drafts stall invisibly (see §7.2).
5. **Workers never touch the main checkout.** Every lane runs in `.worktrees/tm-<N>`; the
   controller only reads. `lane.sh` instructs its agent not to create or remove worktrees.
6. **No `task` sub-agents.** Extensions that would gate a nested agent (`review-enforcer`,
   `verification-gate`, `loop-enforcer`, `sequence-enforcer`) are explicitly disabled for
   workers, and the *harness* is the review gate instead.
7. **The key is the crown jewel.** The box that holds `AI_REVIEW_GATE_KEY` can authorise
   merges. It lives at `~/.pi/agent/.ai-review-gate-key`, is never written to logs, and is
   never copied into this bundle.
8. **One controller.** `.orch.lock` is created with `set -o noclobber`; a second start exits
   immediately, and a lock whose PID is dead is reclaimed.

---

## 6. Operating notes

### Deliberate stop (handing the laptop to a human)

The ritual, in order — the point is that **no uncommitted work is lost**:

1. `launchctl unload -w …` — so it does not restart on boot.
2. `pkill -f orch-loop.sh`, then `rm -f $O/.orch.lock`.
3. Freeze workers: `tmux kill-server` (or kill sessions individually) and check for strays.
4. Capture every dirty worktree: `git -C <wt> diff HEAD > $O/wip/<name>.patch`, plus a
   copy of untracked files and a `.status` file recording `git status --porcelain`.
5. Post a note on the coordination issue naming the open PRs, so a peer box keeps draining.

Durable state survives a reboot regardless: the signing key, review records, the worktrees'
`.git`, and every PR on GitHub.

### Restart recovery (what has to be re-derived by hand)

Workers do not survive a reboot and their tmux sessions are gone, so the controller has no
record that they existed. Today this is recovered by reading `git worktree list`, the
`$O/logs/*.log` tails, and the `wip/` patches. **This is the main operational weakness — see
§7.1.**

### Reading the logs

`$O/logs/orch-loop.log` is the controller's decision log; every line is prefixed by a tag:

```
[scope] open=24 -> BROAD (help drain all)
[cap]   lanes=4 swap_free=588M swap_total=4096M load=7
[dispatch] load=7 lanes=4 -> 4429 4489
[refresh] +#4508 CLEAN -> backlog
#8219 FAILED:ai-review-gate          ← per-PR verdict for the required checks
#8192 fixer attempt 1                ← routed to a fixer, not a re-record
[dispatch] skipped (load=7 lanes=4)  ← and why
```

---

## 7. Known gaps (evaluation targets)

These are real, reproduced, and the reason this is an evaluation artifact rather than an
install script. Details and evidence: tortoise#8246. Same failure class in the fleet rail:
agent-infra#1678, #1680, #1683.

### 7.1 Dispatch bookkeeping never expires — **a dead lane blacklists its issue forever**

`orch-loop.sh` prunes `candidates.txt` against `dispatched.txt`, so an issue number is
recorded as "handled" the first time it is handed to a lane. Nothing re-checks whether that
dispatch **delivered**. A lane that dies (reboot, OOM, crash) strands its issue permanently —
invisible to both the loop and to issue-triage, because the issue stays open with no PR.

Observed: **#4114, #4095, #3554** were dispatched 2026-10-10, their lanes died in a shutdown,
and each sat with a dirty worktree, no PR, and no re-attempt for >24 h. Recovery was manual:
capture the patch to `wip/`, remove the number from **both** `dispatched.txt` and
`checked.txt` (the refresh skips anything in `checked.txt`), re-append to `candidates.txt`,
restore the worktree to `origin/main`.

**Fix direction:** a reaper, guarded by *open issue + no PR matching `*<N>*` + no live
`lane-*` session*.

### 7.2 Draft PRs are invisible to the sweep

The selector is `select(.isDraft|not)` and nothing transitions our own lanes' drafts to ready.
A draft runs its checks, fails `ai-review-gate`, and no worker ever touches it. Observed:
**#8219** (`feat/3524-tm`) and **#8085** (`feat/1037-tm`) sat with failed gates and an
already-recorded `ISSUES` verdict, stranded purely by the flag. (Peers' drafts must stay
untouched — #4212 is correctly left alone.)

**Fix direction:** ready our own branch patterns only; leave foreign drafts alone.

### 7.3 The harness is unversioned (this directory is the remedy)

Until now it existed only as `~/orchestrator/*.sh` on one machine: no history, no review,
no way to hand it to another box, and no way to know which of the 14 scripts were live. §9
records which are live and which are retired.

### 7.4 Other rough edges found while writing this up

- The pre-fix sweep logged `#<PR> re-record` even when the concurrency cap suppressed the
  spawn (`{A||B||C;} || D && echo` parses as `({A||B||C} || D) && echo`), so a stalled drain
  looked busy. Fixed in the shipped `orch-loop.sh`.
- `[ -z "$WT" ] && { … }` as the last command of a function/script makes the exit status
  that of the test — a latent trap in two scripts; the shipped `fix-one.sh` ends with a
  positive `echo` so it no longer leaks a non-zero status.
- `state/` and `wip/` grow without bound; nothing prunes `merged-*.done`, `needsfix-*`, or
  `fixcount-*`.

---

## 8. Measured results

Branch-signature proxy: this box opens `*-tm` / `*-cap` / `*-night` / `*-evening` branches;
everything else in these counts is a peer box. Attribution by branch name is a proxy, not a
certificate — but the split is unambiguous.

| Window | This box | Peer | Notes |
|---|---|---|---|
| 2026-10-10 → 2026-10-12 (48 h) | **94 merged** | 41 merged | |
| 2026-10-11T04Z → 2026-10-12T05Z (24 h) | 51 merged | 27 merged | hour-by-hour below |
| Peak hour (2026-10-11T18Z) | **8 merged** | 0 | 8 PRs in 60 minutes |
| The 2026-10-10T23:45Z → 2026-10-11T21:22Z run | 77 → **99 merged** | — | this box's own handover record |

Hourly histogram (00:00Z–23:00Z on 2026-10-11, merged / this-box):

```
04: 1/1   05: 4/3   06: 3/2   07: 5/5   09: 3/3   10: 3/2   11: 3/3
13: 2/1   14: 3/2   15: 4/2   16: 6/5   17: 7/5   18: 8/8   19: 3/3
20: 3/1   21: 3/1   22: 5/4   23: 2/1
```

Observed steady state at 8 GB: 15 concurrent workers (4 lanes + 4 chains + 3 fixers +
4 reviewers), load 7–18, swap 588 M–832 M free, dispatch throttled by the swap gate.

**Honest caveat on the comparison.** Two boxes running this harness against one repo
duplicate work unless they coordinate: both may pick the same issue, and both run the merge
sweep over the *same* PR queue. A large part of the throughput above is the harness keeping
a box saturated for hours with no human, plus the box holding the merge key — not an
algorithmic advantage. The claim worth evaluating is *utilisation*, not *efficiency per PR*.

---

## 9. What is shipped

**Live** (reachable from `orch-loop.sh`):

| Script | Role |
|---|---|
| `orch-loop.sh` | the controller: merge sweep → capacity-gated dispatch → backlog refresh |
| `dispatch-merge.sh` | creates the worktree and starts the `lane-*` + `chain-*` pair for an issue |
| `lane.sh` | the implementation worker (prompt template included) |
| `chain-merge.sh` | waits for the lane's PR, then runs the merge chain |
| `merge-one.sh` | fresh review → signed record → wait green → merge; marks `needsfix-*` |
| `fix-one.sh` | fixes a reviewer's findings, pushes, re-runs the merge chain |
| `refresh-candidates.sh` | pre-flights issues (collision check) into the backlog |

**Manual utility:** `adopt-fix.sh` — run `fix-one.sh` against a specific PR by hand.

**Retired** (`retired/`, kept so nothing is lost): `auto-loop.sh`, `dispatch.sh`,
`do-merge.sh`, `refix.sh`, `sweep.sh`, `watch-one.sh`. These are earlier iterations of the
same idea and are no longer invoked; they are included for the record, not for use.

**Portability edits applied to every shipped script.** Three per-box constants became
overridable with this box's values as defaults, so behaviour is byte-identical here and
configurable elsewhere:

```bash
OR="${OR:-daniel-ospina/tortoise}"
REPO="${REPO:-/Users/home/eldato/tortoise}"
O="${O:-$HOME/orchestrator}"
```

Everything else is still tortoise-shaped and must be changed on another target: the branch
suffix (`tm`), `feat/<N>-tm` naming, `uv sync --extra embeddings --extra parity`, the
`TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest` invocation, the
`tools/collision_preflight.py` path, and the eight required check names
(`ai-review-gate python-ci-gate docs test-isolation license-surface legal-e2e pricing-artifact`).

---

## 10. Open questions for the evaluator

1. Does this belong in `agent-infra` (symlink-distributed via `manifest.json` + `sync.sh`,
   pull-only) rather than in a repo's `operations/`?
2. Should the two boxes coordinate through an explicit claim registry instead of branch-name
   heuristics? tortoise#5052 already proposes "an authoritative, expiring claim registry" —
   §7.1 is the same problem seen from the dispatcher's side.
3. Is a `~/`-rooted bash harness the right shape at all, or should this fold into the fleet
   rail's cmux workspaces (agent-infra#1683)?

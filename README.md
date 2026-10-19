> # ⛔ EVALUATED 2026-10-18 — ALL SIX ARE SUPERSEDED. **DO NOT MERGE.**
>
> Every patch below is a snapshot of work that had **already shipped in `main`**, or a
> snapshot that would **undo shipped work**. None is a merge candidate. Nothing here is
> new; there is nothing to rebase, land, or deploy. Full evidence:
> **issue #9060** · report `/tmp/eldato-reports/REPORT-stashes.md`.
>
> | patch | verdict | superseded by |
> |---|---|---|
> | `stash0` `feat/2654-tm` (`assembly.py`, +206/−20) | OBSOLETE — landed verbatim | `a67d61afc` — duration-state resolution (#2654) **#8050**, 2026-10-11 |
> | `stash1` `feat/3662-capture-consent-gate` (7 files, +271/−7) | OBSOLETE — and its docs text is now **wrong** | `a4b800854` — SDK consent gate (#3662) **#7811**, 2026-10-09; hosted-door question settled later by `b169733a1` (#8535), **Option 1** |
> | `stash2` `feat/167-story-arch` | **NOT WORK** — one `.pyc`, 0 source lines | — |
> | `stash3` `feat/7697-hybrid-search-v2` (+52/−7) | OBSOLETE — 4 of 5 source files comment-only; the 3 behavioural bits are in `main` in better form | `sdk.py:23274`, `projection/__init__.py:10255,10768` |
> | `stash4` same branch (`search_engine.py`, +4/−45) | OBSOLETE **+ REGRESSIVE** — applying it **deletes** the landed index-accelerated vector leg (`db.idx.vector.queryNodes` / `is_embedded`) and reverts Docker/server retrieval to brute force | `main` is strictly ahead (`search_engine.py:1861-1964`) |
> | `stash5` same branch (+55/−25) | OBSOLETE — parallel `degradation_chain` (#7780) and the Point-embedding write (#7778) both landed, journal-aware | `search_engine.py:2497`; `projection/entities.py::_upsert_point_props` |
>
> **Three corrections to the text below.**
>
> 1. **The "risk" column was wrong on the day it was written.** It ranked `stash1` first as
>    "#3662 OPEN, no PR — at risk"; the #3662 SDK gate merged **2026-10-09** (`a4b800854`,
>    PR #7811) — **three days before this export**. It read the *issue tracker's* state, not
>    the *code's* (the merged-but-open-issue pattern, #8469). The issue is now CLOSED.
> 2. **`stash1`'s doc text contradicts a recorded owner decision.** It calls the hosted MCP
>    `tortoise_session_capture` door "an open product question (#3662)". The owner settled it
>    as **Option 1** on 2026-10-13 (`b169733a1`, #8535) with an `OVERRIDES:` marker on #3662.
>    Landing that patch would re-open a closed decision and strip the marker.
> 3. **The application recipe below does not work for 4 of the 6.** `git stash show -p` emits
>    no binary payload for the `__pycache__` entries and those files do not exist in a
>    checkout, so `git apply` refuses at the first hunk (`No such file or directory`). Only
>    `git apply --exclude='*.pyc'` reaches the text hunks — and on current `main` every
>    non-empty patch then fails anyway on context drift. `stash2` "applies" only because,
>    after excluding the `.pyc`, it is empty.
>
> Also note `stash3`/`4`/`5` are **alternate snapshots, not a stack** — `stash4` and `stash5`
> share the same post-image for `search_engine.py` (`d6705c2a3`), so applying the sequence
> conflicts. And the branch name `7697-hybrid-search-v2` mis-attributes issue #7697, which is
> a CI/docs lychee issue none of these patches touch.
>
> Keep this branch as the record. Do not merge it — this file also replaces the repository's
> root `README.md`.

---

# Local git stashes, exported before a machine handover — 2026-10-12

Six `git stash` entries were sitting on the box that had been running the autonomous
dispatch loop. Stashes exist only on local disk, so they were about to become
unrecoverable when the machine was handed over.

Each `.patch` below is the output of `git stash show -p <ref>` — a plain diff that can be
applied with `git apply`, or inspected with `git apply --stat`.

## Index

| File | Origin branch | Size | Issue | Issue state |
|---|---|---|---|---|
| `stash-stash1-feat_3662-capture-consent-gate.patch` | `feat/3662-capture-consent-gate` | +271 / 7 files | **#3662** | **OPEN, no PR** — at risk |
| `stash-stash0-feat_2654-tm.patch` | `feat/2654-tm` | +206 / 1 file | #2654 | CLOSED — superseded |
| `stash-stash5-feat_7697-hybrid-search-v2.patch` | `feat/7697-hybrid-search-v2` | +55 / 3 files | **#7697** | **OPEN, no PR** — at risk |
| `stash-stash3-feat_7697-hybrid-search-v2.patch` | `feat/7697-hybrid-search-v2` | +52 / 6 files | **#7697** | **OPEN, no PR** — at risk |
| `stash-stash4-feat_7697-hybrid-search-v2.patch` | `feat/7697-hybrid-search-v2` | +4 / 2 files | **#7697** | OPEN, no PR — trivial |
| `stash-stash2-feat_167-story-arch.patch` | `feat/167-story-arch` | +0 / 1 file | #167 | CLOSED — empty |

## What is in the substantial ones

**#3662** (`+271 lines, 7 files`) — in-repo MCP `tortoise_session_capture` consent gate:

```
 tortoise/capture_consent.py                     |  42 +++
 tortoise/sdk.py                                 |  37 +-
 tests/test_capture_consent.py                   | 100 ++++++++++
 tests/test_capture_session.py                   |  52 +++++
 docs/research/2026-09-16-3615-capture-consent.md|  22 ++
 docs/quickstart-cloud.md                        |  12 +-
 .env.example                                    |  13 +-
```

**#7697** (`+55` and `+52 lines`) — hybrid-search v2 work on the docs-baseline lychee leg.

## How to use

```bash
# inspect without applying
git apply --stat  stashes/stash-stash1-feat_3662-capture-consent-gate.patch

# apply onto a fresh branch off main
git checkout -b feat/3662-capture-consent-gate origin/main
git apply stashes/stash-stash1-feat_3662-capture-consent-gate.patch
```

**Status: unverified.** These are raw working-tree snapshots taken by an agent mid-task.
They were never reviewed, and their tests were not run to completion. Treat them as a
starting point, not as a candidate for merge — and expect context drift, since the branch
they were stashed on may have moved since.

## Why this file exists

The box running the loop was handed over, and its dispatch reaper treats
*"open issue + no PR"* as an undelivered dispatch that must be re-queued. Without this
export, the work in these stashes would have been either lost with the disk or silently
re-done from scratch on another machine. Exporting them to a branch makes them durable and
visible to whoever picks the issues up next.

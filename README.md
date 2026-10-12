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

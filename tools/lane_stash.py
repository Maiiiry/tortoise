#!/usr/bin/env python3
"""lane_stash — a LANE-SCOPED stash that cannot take a peer lane's work (#8051).

WHY THIS EXISTS (the root cause, measured — not inferred)
---------------------------------------------------------
``refs/stash`` and its reflog live in the COMMON git dir
(``git rev-parse --git-common-dir``), which every linked worktree of a repo
shares; ``HEAD``, the index and the worktree are per-worktree. So ``stash@{0}``
is a repo-GLOBAL register that every lane writes and that **no lane can see is
shared from the command it types**. A bare ``git stash pop`` in lane A applies
and drops whichever entry ANOTHER lane pushed last:

    lane A: git stash push -m A          -> refs/stash = A
    lane B: git stash push -m B          -> refs/stash = B, A renumbered to @{1}
    lane A: git stash pop                -> applies B, DROPS B        <-- the harm

Three recorded occurrences (#2654, #3539, and a 7860/8482 lane that already
KNEW the hazard). The failure presents as a *successful* pop: no conflict, no
non-zero exit, no warning. The near-miss is worse than a lost stash — the
natural next step is ``git add`` + commit, which lands a peer's uncommitted
work under this lane's branch, message and attribution.

Measured on git 2.50.1 (Apple Git-155), in temp repos, for the design below:

  * ``git stash push`` has NO per-worktree, namespaced or ``--ref`` form.
  * ``git stash create -u`` accepts ``-u`` SILENTLY and emits a 2-parent commit
    — untracked files are NOT captured (and it takes no pathspec, and does not
    clean the tree). It is therefore rejected as the creation primitive: it
    loses fidelity and would force a reimplementation of ``stash push``.
  * ``git stash apply <sha>`` works with the sha NOT on top, restores tracked
    AND untracked, and leaves the shared register and the peer's entry alone.
  * ``git stash drop <sha>`` is refused ("is not a stash reference") — there is
    no hash-addressed drop.
  * ``git log --walk-reflogs --format='%H %gs' refs/stash`` still yields OUR
    commit by a unique message nonce even after a peer pushes on top (``git
    stash push`` does not print the sha, so the nonce is how we identify ours).

THE CONTRACT
------------
* **Creation** uses git's own ``git stash push`` (full fidelity) with a uuid
  nonce in the message, then identifies OUR commit by that nonce in the reflog
  — so a peer's push landing *during* ours cannot mis-identify it — and pins it
  at ``refs/lane/<lane-key>/stash/<n>`` (CAS create; retry on collision).
* **Retrieval never resolves ``stash@{N}``.** ``apply`` / ``pop`` / ``show`` /
  ``drop`` read the lane-scoped ref and act on the SHA, so a peer's entry is
  unreachable by construction and a peer's push cannot renumber a lane ref.
* **A lane key is worktree-specific and BRANCH-INDEPENDENT**: sanitized worktree
  directory basename plus ``sha1(worktree root)[:8]``. Two worktrees (even
  forced onto one branch) never share a ref namespace, AND a lane keeps its
  pins when it checks out another branch inside its own worktree — a routine
  baseline move, and the move that produced the incident this tool exists for.
  Keying on the branch was the first draft; it makes a lane's pins look lost
  the moment it checks out another branch.
* **``pop`` never deletes from the shared register.** The deletion would be
  positional (``refs/stash@{i}``, re-resolved at delete time) and the victim of
  a lost race can be a bare-git lane whose entry is pinned nowhere — i.e.
  exactly the silent loss this tool exists to stop. The entry stays; reclaim it
  explicitly with ``prune``, which refuses to remove any entry whose object is
  not pinned by a lane ref (so a delete can never lose work).
* **``check`` is fail-closed**: exit 0 when a bare ``git stash pop`` in this
  worktree would take THIS lane's own entry; exit 1 when it would take a FOREIGN
  entry — a peer lane's (identified by the ``[<lane-key>]`` marker this tool
  writes, so two worktrees forced onto ONE branch are still told apart) or one
  whose origin cannot be read; exit 2 on a state error. Safe to call from a lane
  pre-flight.

THE TARGET IS NOT A PARAMETER
-----------------------------
Like ``tools/test_lane.py``, there is no ``--lane``/``--ref`` override: a peer's
lane key is a computable ``sha1(path)[:8]``, so any override would be a way for
one lane to delete or adopt another lane's pinned entries. ``prune`` and
``recover`` are the only commands that touch the shared register, and they are
explicit by design (`prune` also requires ``--apply``).

USAGE
-----
    python tools/lane_stash.py push -u -m "baseline run" -- tortoise/sdk.py
    python tools/lane_stash.py list
    python tools/lane_stash.py pop 1              # apply by sha, keep the shared entry
    python tools/lane_stash.py check              # 0 = a bare pop here is safe
    python tools/lane_stash.py prune --apply      # reclaim pinned shared-register entries
    python tools/lane_stash.py recover <sha>      # the #2654 repair recipe

Exit codes: 0 ok (including "nothing to stash", matching ``git stash push``);
1 ``check`` found a foreign top entry; 2 environment/state error; 3 a git
operation failed (push failure, apply conflict, missing object).
"""
from __future__ import annotations

import sys

# #5128 shape: refuse a <3.12 interpreter before the imports below — a module-level
# 3.12+-only construct would otherwise fail first, and it would fail with a
# traceback instead of this message. The gate that enforces this shape is
# tests/test_entry_point_python_guard.py, not a style rule.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/lane_stash.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/lane_stash.py`"
    )

import argparse
import hashlib
import json
import os
import re
import subprocess
import uuid
from pathlib import Path

REFS_NS = "refs/lane"
MAX_ID_TRIES = 50
#: Reflog entries scanned when locating our own push by nonce. Ours is the
#: newest, so a deep stack still finds it on the first match; the bound keeps a
#: pathological register from turning `push` into an unbounded walk.
LOCATE_WINDOW = 200

#: ``GIT_*`` variables that RETARGET the CLI at another checkout. Inherited from
#: a hook or wrapper they would silently make this tool write lane refs — or
#: apply a stash — against a peer worktree, with no flag involved. Scrubbed for
#: the same reason `tools/test_lane.py` scrubs them.
_RETARGETING_ENV = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
)


class LaneStashError(Exception):
    """A state/usage error (exit 2) — never raised for a plain git failure."""


class GitFailed(Exception):
    """A git operation failed (exit 3), carrying its own returncode."""

    def __init__(self, rc: int, message: str):
        super().__init__(message)
        self.rc = rc


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _RETARGETING_ENV}
    return env


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    cp = subprocess.run(
        ["git", *args], capture_output=True, text=True, env=_env(),
    )
    if check and cp.returncode != 0:
        raise GitFailed(cp.returncode, (cp.stderr or cp.stdout).strip())
    return cp


def _out(*args: str, check: bool = True) -> str:
    return _git(*args, check=check).stdout.strip()


def repo_root() -> Path:
    cp = _git("rev-parse", "--show-toplevel", check=False)
    if cp.returncode != 0:
        raise LaneStashError("not inside a git work tree — nothing to do")
    return Path(cp.stdout.strip())


def git_dir() -> Path:
    return Path(_out("rev-parse", "--absolute-git-dir"))


def common_dir() -> Path:
    return Path(_out("rev-parse", "--git-common-dir")).resolve()


def current_branch() -> str:
    return _out("branch", "--show-current")


def is_linked_worktree() -> bool:
    """True in a linked worktree; False in the main checkout."""
    return git_dir().resolve() != common_dir()


def lane_key(root: Path | None = None) -> str:
    """A worktree-unique, BRANCH-INDEPENDENT lane key.

    The worktree is the isolation unit this tool exists to protect, so the path
    is the identity and the branch is decoration. Keying on the branch was the
    first draft and it is wrong in a way that matters: a lane routinely checks
    out another branch to take a baseline, and a branch-keyed lane would then
    list NOTHING — its pins would look lost while they sat under the old key.
    (The incident that prompted this tool *was* a baseline stash.)

    The digest is load-bearing for the same reason as before: two worktrees can
    share a directory basename, and two worktrees CAN be forced onto one branch
    — a shared ref namespace would reintroduce the collision this tool removes.
    """
    root = root or repo_root()
    # The slug must be a VALID git ref component, not merely a filesystem-safe
    # name: git refuses `..` inside a component and a trailing `.lock`
    # (case-insensitive), and a component may not begin or end with `.`. A
    # worktree directory such as `a..b` or `lane.lock` is legal on disk, so an
    # unsanitized basename makes `_pin`'s `update-ref` fail AFTER `git stash
    # push` already wrote the entry — leaving the lane's work unpinned in the
    # shared register, the exact un-attributable state this tool exists to
    # prevent (#8051 review P2).
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", root.name)[:60]
    slug = slug.replace("..", "-")
    while slug.lower().endswith(".lock"):
        slug = slug[: -len(".lock")]
    slug = slug.strip("-.") or "worktree"
    digest = hashlib.sha1(str(root.resolve()).encode()).hexdigest()[:8]
    return f"{slug}-{digest}"


def ref_ns(key: str) -> str:
    return f"{REFS_NS}/{key}/stash"


def _parse_entries(key: str) -> list[dict]:
    """This lane's pinned entries, ordered by id.

    The REF IS THE REGISTRY: a ref keeps its object reachable against `gc`, is
    name-ordered (`stash/2` after `stash/1`), and is CAS-allocatable — no JSON
    state file whose lost update would orphan live work.
    """
    cp = _git(
        "for-each-ref",
        "--format=%(refname)%00%(objectname)%00%(creatordate:iso-strict)",
        ref_ns(key),
        check=False,
    )
    if cp.returncode != 0:
        raise LaneStashError(cp.stderr.strip() or "cannot read lane refs")
    entries = []
    for line in cp.stdout.splitlines():
        parts = line.split("\0")
        if len(parts) < 2:
            continue
        ref, sha = parts[0], parts[1]
        seg = ref.rsplit("/", 1)[-1]
        if not seg.isdigit():
            continue
        entries.append({
            "id": int(seg),
            "ref": ref,
            "sha": sha,
            "created": parts[2] if len(parts) > 2 else "",
        })
    entries.sort(key=lambda e: e["id"])
    return entries


def _entry(key: str, entry_id: int | None) -> dict:
    entries = _parse_entries(key)
    if not entries:
        raise LaneStashError(
            f"lane {key} has no pinned entries — nothing to act on "
            "(did you use bare `git stash push`? that writes the SHARED register, "
            "which this tool cannot attribute to a lane)"
        )
    if entry_id is None:
        return entries[-1]
    for e in entries:
        if e["id"] == entry_id:
            return e
    raise LaneStashError(
        f"lane {key} has no entry {entry_id} (have: "
        f"{', '.join(str(e['id']) for e in entries)})"
    )


def _commit_alive(sha: str) -> bool:
    return _git("cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode == 0


def _pin(key: str, sha: str) -> dict:
    """Allocate `refs/lane/<key>/stash/<n>` for `sha` by CAS create.

    The create is `update-ref <ref> <new> ""` — an EMPTY old value means "the
    ref must not exist" (verified: a second create fails with `reference
    already exists`). Two concurrent pushes in one lane therefore cannot
    overwrite each other's pointer; the loser retries the next id.
    """
    entries = _parse_entries(key)
    nxt = (entries[-1]["id"] + 1) if entries else 1
    for _ in range(MAX_ID_TRIES):
        ref = f"{ref_ns(key)}/{nxt}"
        cp = _git("update-ref", ref, sha, "", check=False)
        if cp.returncode == 0:
            return {"id": nxt, "ref": ref, "sha": sha}
        nxt += 1
    raise LaneStashError(f"could not allocate a lane ref under {ref_ns(key)}")


def _pin_or_report(key: str, sha: str) -> dict:
    """Pin `sha` to this lane; on failure say the entry EXISTS and how to recover.

    `_pin` runs AFTER the stash commit is already in the shared register
    (`git stash push` for `push`, `git stash store` for `recover`). Reporting a
    bare allocation failure would read as "the stash was never created" while
    the lane's work sits unpinned and a bare `git stash pop` can take it — so
    the error must name the sha and the recovery recipe (#8051 review P2).
    """
    try:
        return _pin(key, sha)
    except LaneStashError as exc:
        sys.stderr.write(
            f"warning: the stash entry already EXISTS in the shared register as {sha}, "
            f"but it could not be pinned under {ref_ns(key)} ({exc}). A bare "
            "`git stash pop` can take it. Pin it to this lane with:\n"
            f"  python tools/lane_stash.py recover {sha}\n"
        )
        raise


def _own_sha_by_nonce(nonce: str) -> str | None:
    """Our own stash commit, found by the unique nonce in its message.

    `git stash push` does not print the sha, and `refs/stash` may already be a
    peer's entry by the time we look. The reflog retains OURS, so the nonce is
    the only race-free identifier (measured: still found after a peer pushes on
    top).
    """
    cp = _git(
        "log", "--walk-reflogs", "--format=%H%x00%gs",
        "-n", str(LOCATE_WINDOW), "refs/stash", check=False,
    )
    if cp.returncode != 0:
        return None
    for line in cp.stdout.splitlines():
        parts = line.split("\0")
        if len(parts) >= 2 and nonce in parts[1]:
            return parts[0]
    return None


def _subject_branch(subject: str) -> str | None:
    """The branch a stash message names, or None when it cannot be read.

    `git stash push` writes `WIP on <branch>: <sha> <subject>` (or `On <branch>:
    <msg>` for `store`). This is the standard heuristic — the issue's own
    suggestion — and it is ADVISORY evidence in `check`, never the mechanism:
    retrieval never consults it.
    """
    m = re.match(r"^(?:WIP on|On) (.+?):", subject)
    return m.group(1) if m else None


def _subject_lane_key(subject: str) -> str | None:
    """The lane key a tool-created stash message carries, or None.

    `push` writes `lane_stash <uuid4> [<lane-key>] <label>` into the message, so
    every entry this tool creates is ATTRIBUTABLE to a lane even when two
    worktrees are forced onto ONE branch — the case the branch heuristic cannot
    tell apart, because both lanes are on the same branch (#8051 review P2).
    Anchoring on the 32-hex nonce keeps a user's bracket-containing label from
    being read as the key.
    """
    m = re.search(r"\blane_stash [0-9a-f]{32} \[([^\]]+)\]", subject)
    return m.group(1) if m else None


def _stash_head() -> str | None:
    """The sha `refs/stash` resolves to, or None when the ref does not exist.

    `git stash list` reads the REFLOG; `refs/stash` can still resolve after the
    reflog is expired or unreadable while `stash list` returns rc 0 with no
    rows. `check` must cross-check the two, because "no rows" is NOT the same
    state as "no entry" (#8051 review P1).
    """
    cp = _git("rev-parse", "--verify", "-q", "refs/stash", check=False)
    return cp.stdout.strip() or None


def _shared_register() -> list[dict]:
    """The shared, repo-global register: position, sha, subject, branch, pinned.

    A non-zero rc is a STATE error, not an empty register: collapsing "cannot
    read" into "nothing there" is the fail-open path that reports SAFE while a
    bare `git stash pop` would still take a peer's entry (#8051 review P1).
    """
    cp = _git("stash", "list", "--format=%gd%x00%H%x00%gs", check=False)
    if cp.returncode != 0:
        raise LaneStashError(
            "could not read the shared stash register (`git stash list` failed): "
            f"{(cp.stderr or cp.stdout).strip() or 'no output'}"
        )
    pinned = _pinned_shas()
    rows = []
    for line in cp.stdout.splitlines():
        parts = line.split("\0")
        if len(parts) < 3:
            continue
        rows.append({
            "pos": parts[0],
            "sha": parts[1],
            "subject": parts[2],
            "branch": _subject_branch(parts[2]),
            "pinned": parts[1] in pinned,
        })
    return rows


def _pinned_shas() -> set[str]:
    cp = _git(
        "for-each-ref", "--format=%(objectname)", f"{REFS_NS}/*/stash/*", check=False,
    )
    if cp.returncode != 0:
        return set()
    return {ln.strip() for ln in cp.stdout.splitlines() if ln.strip()}


# ── commands ────────────────────────────────────────────────────────────────

def cmd_push(args: argparse.Namespace) -> int:
    root = repo_root()
    key = lane_key(root)
    nonce = uuid.uuid4().hex
    label = args.message or "wip"
    message = f"lane_stash {nonce} [{key}] {label}"

    cmd = ["stash", "push", "-m", message]
    if args.all:
        cmd.append("--all")
    elif args.include_untracked:
        cmd.append("--include-untracked")
    if args.paths:
        cmd.append("--")
        cmd.extend(args.paths)

    cp = _git(*cmd, check=False)
    if cp.returncode != 0:
        sys.stderr.write(cp.stderr or cp.stdout)
        raise GitFailed(cp.returncode, "git stash push failed")

    sha = _own_sha_by_nonce(nonce)
    if sha is None:
        # `git stash push` returns 0 with this message when there was nothing to
        # save. Matching on it (rather than guessing from refs/stash, which a
        # peer may have moved) is what stops this tool from reporting an entry
        # it did not create.
        if "No local changes to save" in cp.stdout:
            print("nothing to stash — worktree clean for the given paths")
            return 0
        raise LaneStashError(
            "git stash push reported success but the entry could not be located "
            "in the shared reflog; run `git stash list` before trusting it"
        )

    entry = _pin_or_report(key, sha)
    print(f"pinned lane entry {entry['id']} [{entry['ref']}]")
    print(f"  sha     {sha}")
    print(f"  subject {_out('log', '-1', '--format=%s', sha)}")
    print(f"  restore python tools/lane_stash.py pop {entry['id']}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    key = lane_key()
    entries = _parse_entries(key)
    scope = "linked worktree" if is_linked_worktree() else "MAIN checkout"
    print(f"lane {key} ({scope})")
    if not entries:
        print("  (no pinned entries)")
    for e in entries:
        alive = "ok" if _commit_alive(e["sha"]) else "MISSING OBJECT"
        subject = _out("log", "-1", "--format=%s", e["sha"]) if _commit_alive(e["sha"]) else "-"
        print(f"  {e['id']:>3}  {e['sha'][:12]}  {alive:<13} {e['created']}  {subject}")

    rows = _shared_register()
    print(f"shared register (refs/stash — repo-global, {len(rows)} entr"
          f"{'y' if len(rows) == 1 else 'ies'})")
    if not rows:
        print("  (empty)")
    branch = current_branch()
    for r in rows:
        if r["pinned"]:
            mark = "pinned  "
        elif r["branch"] == branch:
            mark = "UNPINNED"
        else:
            mark = "FOREIGN "
        star = "  <-- a bare `git stash pop` here would take THIS" if r is rows[0] and (
            r["branch"] != branch or not r["branch"]) else ""
        print(f"  {r['pos']:<11} {r['sha'][:12]}  {mark}  {r['subject']}{star}")
    if rows and (rows[0]["branch"] != branch or not rows[0]["branch"]):
        print("\n⚠️  the top shared entry is not this lane's — use `lane_stash pop <id>`, "
              "never a bare `git stash pop` (see `lane_stash check`).")
    return 0


def cmd_apply(args: argparse.Namespace, pop: bool) -> int:
    key = lane_key()
    entry = _entry(key, args.entry)
    if not _commit_alive(entry["sha"]):
        raise LaneStashError(
            f"entry {entry['id']} ({entry['ref']}) points at {entry['sha']}, which is no "
            "longer in the object database — the pinned ref was the last handle, and "
            "nothing here can restore it"
        )
    cmd = ["stash", "apply"]
    if args.index:
        cmd.append("--index")
    cmd.append(entry["sha"])
    cp = _git(*cmd, check=False)
    if cp.returncode != 0:
        sys.stderr.write(cp.stdout or "")
        sys.stderr.write(cp.stderr or "")
        # The lane ref is deliberately NOT touched on failure: a conflicted apply
        # leaves the pin as the only handle on the work.
        raise GitFailed(cp.returncode, "git stash apply failed (entry kept)")
    if pop:
        # CAS delete: only OUR entry, only if it is still at the sha we applied.
        # `refs/stash` is never touched (see the module docstring).
        cp = _git("update-ref", "-d", entry["ref"], entry["sha"], check=False)
        if cp.returncode != 0:
            raise LaneStashError(
                f"applied {entry['sha'][:12]} but could not unpin {entry['ref']}: "
                f"{cp.stderr.strip()}"
            )
        print(f"applied and unpinned lane entry {entry['id']} ({entry['sha'][:12]})")
    else:
        print(f"applied lane entry {entry['id']} ({entry['sha'][:12]}) — pin kept")
    print("  the shared register was NOT modified: reclaim pinned entries with "
          "`lane_stash prune --apply`, and never run a bare `git stash pop`.")
    return 0


def cmd_pop(args: argparse.Namespace) -> int:
    return cmd_apply(args, pop=True)


def cmd_show(args: argparse.Namespace) -> int:
    key = lane_key()
    entry = _entry(key, args.entry)
    cmd = ["stash", "show"]
    if args.patch:
        cmd.append("-p")
    cmd.append(entry["sha"])
    cp = _git(*cmd, check=False)
    sys.stdout.write(cp.stdout)
    sys.stderr.write(cp.stderr)
    return cp.returncode


def cmd_drop(args: argparse.Namespace) -> int:
    key = lane_key()
    entry = _entry(key, args.entry)
    if not args.yes:
        raise LaneStashError(
            f"refusing to unpin entry {entry['id']} ({entry['sha'][:12]}) without --yes — "
            "the pin may be the only handle on that work once the shared register is "
            "pruned or its entry is dropped by another lane"
        )
    cp = _git("update-ref", "-d", entry["ref"], entry["sha"], check=False)
    if cp.returncode != 0:
        raise LaneStashError(f"could not unpin {entry['ref']}: {cp.stderr.strip()}")
    print(f"unpinned lane entry {entry['id']} ({entry['sha'][:12]}) — shared register untouched")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Fail-closed detector: would a bare `git stash pop` here take foreign work?"""
    key = lane_key()
    branch = current_branch()
    rows = _shared_register()
    entries = _parse_entries(key)
    head = _stash_head()

    verdict = {
        "lane": key,
        "branch": branch,
        "linked_worktree": is_linked_worktree(),
        "lane_entries": len(entries),
        "shared_entries": len(rows),
        "unpinned_shared": sum(1 for r in rows if not r["pinned"]),
        "bare_pop_target": rows[0]["sha"] if rows else None,
        "bare_pop_branch": rows[0]["branch"] if rows else None,
        "safe": True,
        "reasons": [],
    }

    # `git stash list` reads the reflog; `refs/stash` can still resolve when the
    # reflog is expired/unreadable with no rows printed. An empty `rows` is then
    # NOT "nothing to take": a bare `git stash pop` would apply and drop that
    # entry. Fail closed rather than answer from the absence of data (#8051 P1).
    if head is not None and (not rows or rows[0]["sha"] != head):
        verdict["safe"] = False
        verdict["reasons"].append(
            f"refs/stash resolves to {head[:12]} but the shared reflog does not "
            "enumerate it — the register cannot be read, so a bare `git stash pop` "
            "here cannot be shown to be safe (it would apply and drop that entry)"
        )

    if rows:
        top = rows[0]
        top_lane = _subject_lane_key(top["subject"])
        # Our own pinned entry is ours whatever its subject says: a cross-lane
        # `recover` re-pins a peer's dropped entry to THIS lane, and a bare pop
        # here then takes this lane's own (recovered) work.
        if not any(e["sha"] == top["sha"] for e in entries):
            if top_lane is not None and top_lane != key:
                # The branch heuristic cannot see this: `lane_key` is
                # worktree-scoped, and two worktrees can be forced onto one
                # branch, so a matching branch is NOT evidence of ownership.
                verdict["safe"] = False
                verdict["reasons"].append(
                    f"the top shared entry ({top['sha'][:12]}) was pushed by lane "
                    f"{top_lane!r} (branch {top['branch']!r}), not this lane {key!r} — "
                    "two worktrees can be forced onto ONE branch, so a matching branch "
                    "does not make a bare `git stash pop` HERE safe: it would apply and "
                    "drop that lane's entry"
                )
            elif top_lane is None and not top["branch"]:
                verdict["safe"] = False
                verdict["reasons"].append(
                    f"the top shared entry ({top['sha'][:12]}) has an unreadable subject "
                    f"({top['subject']!r}) — its origin cannot be established, so a bare "
                    "`git stash pop` cannot be shown to be safe"
                )
            elif top_lane is None and top["branch"] != branch:
                verdict["safe"] = False
                verdict["reasons"].append(
                    f"the top shared entry ({top['sha'][:12]}) was pushed on branch "
                    f"{top['branch']!r}, not the current branch {branch!r} — a bare "
                    "`git stash pop` HERE would apply and drop another lane's work"
                )

    if args.json:
        print(json.dumps(verdict, indent=2))
    else:
        where = "linked worktree" if verdict["linked_worktree"] else "MAIN checkout"
        print(f"lane {key} ({where}), branch {branch!r}")
        if rows:
            print(f"top shared entry: {rows[0]['pos']} {rows[0]['sha'][:12]}  "
                  f"branch={rows[0]['branch']!r}  {rows[0]['subject']}")
        else:
            print("shared register: empty")
        if verdict["unpinned_shared"]:
            print(f"  note: {verdict['unpinned_shared']} shared entr"
                  f"{'y is' if verdict['unpinned_shared'] == 1 else 'ies are'} pinned by no "
                  "lane ref (bare-git debris) — prune will refuse to touch "
                  f"{'it' if verdict['unpinned_shared'] == 1 else 'them'}")
        if verdict["safe"]:
            print("VERDICT: SAFE — a bare `git stash pop` here would take this branch's own entry")
        else:
            print("VERDICT: HAZARD — do not run a bare `git stash pop`/`git stash drop` here")
            for r in verdict["reasons"]:
                print(f"  - {r}")
            print("  use: python tools/lane_stash.py list ; python tools/lane_stash.py pop <id>")
    return 0 if verdict["safe"] else 1


def cmd_recover(args: argparse.Namespace) -> int:
    """The #2654 repair recipe: restore a dropped entry AND pin it this time."""
    if _git("cat-file", "-t", args.sha, check=False).returncode != 0:
        raise LaneStashError(f"{args.sha} is not an object in this repository")
    if _git("cat-file", "-t", args.sha, check=False).stdout.strip() != "commit":
        raise LaneStashError(f"{args.sha} is not a commit — `git stash store` needs a stash commit")
    parents = _out("rev-list", "--parents", "-n", "1", args.sha).split()
    subject = _out("log", "-1", "--format=%s", args.sha)
    # A stash commit has 2 parents (HEAD + index) or 3 with `-u`/`-a` (the
    # untracked tree). A plain commit has 1; a 2-parent merge has 2 and would
    # pass a parent count alone, so the SUBJECT must also look like a stash
    # unless the operator asserts it with -m (an explicit human claim, which is
    # the only thing that lets a store of a rescued entry proceed).
    if len(parents) not in (3, 4):
        raise LaneStashError(
            f"{args.sha} has {len(parents) - 1} parent(s); a stash commit has 2 (HEAD + "
            "index) or 3 (with -u/-a), so this is not a stash entry"
        )
    if not args.message and not _subject_branch(subject):
        raise LaneStashError(
            f"{args.sha}'s subject ({subject!r}) does not name a branch, so it cannot be "
            "shown to be a stash entry — pass -m <message> to assert it explicitly"
        )
    subject = args.message or subject or f"recovered {args.sha}"
    cp = _git("stash", "store", "-m", subject, args.sha, check=False)
    if cp.returncode != 0:
        sys.stderr.write(cp.stderr)
        raise GitFailed(cp.returncode, "git stash store failed")
    key = lane_key()
    entry = _pin_or_report(key, args.sha)
    print(f"restored {args.sha} to the shared register (subject: {subject!r})")
    print(f"pinned lane entry {entry['id']} [{entry['ref']}] for lane {key}")
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    """Reclaim shared-register entries that a lane ref pins (so deletion is lossless)."""
    rows = _shared_register()
    doomed = [r for r in rows if r["pinned"]]
    kept = [r for r in rows if not r["pinned"]]
    print(f"shared register: {len(rows)} entr{'y' if len(rows) == 1 else 'ies'}; "
          f"{len(doomed)} pinned by a lane ref (removable), {len(kept)} unpinned (refused)")
    for r in doomed:
        print(f"  remove {r['pos']} {r['sha'][:12]}  {r['subject']}")
    for r in kept:
        print(f"  keep   {r['pos']} {r['sha'][:12]}  {r['subject']}  (no lane ref pins it)")
    if not doomed:
        return 0
    if not args.apply:
        print("dry run — re-run with --apply to remove the pinned entries")
        return 0

    # Positions are walked HIGH -> LOW so an earlier delete cannot renumber a
    # later specifier. `reflog delete` re-resolves `refs/stash@{i}` at DELETE
    # time, so the sha at that position is re-verified immediately before each
    # delete; if a peer pushed in between, the verification fails and this prune
    # stops instead of removing an entry that would not be lossless to remove.
    order = sorted(doomed, key=lambda r: int(r["pos"].rsplit("@{", 1)[-1].rstrip("}")))
    removed = 0
    for r in reversed(order):
        spec = r["pos"]
        here = _out("rev-parse", spec, check=False)
        if here != r["sha"]:
            print(f"STOP: {spec} is now {here or 'gone'} (expected {r['sha']}) — a peer "
                  "pushed during the prune; re-run when the register is quiet")
            return 0
        cp = _git("reflog", "delete", "--updateref", spec, check=False)
        if cp.returncode != 0:
            print(f"could not remove {spec}: {cp.stderr.strip()}")
            continue
        removed += 1
    print(f"removed {removed} pinned shared-register entr"
          f"{'y' if removed == 1 else 'ies'}; {len(kept)} unpinned left untouched")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lane_stash",
        description="A lane-scoped stash: never applies or drops a peer lane's entry (#8051).",
        epilog="Exit codes: 0 ok (incl. 'nothing to stash'); 1 check found a foreign top "
               "entry; 2 state/usage error; 3 a git operation failed.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("push", help="stash in this worktree and pin the entry to this lane")
    sp.add_argument("-m", "--message", help="human label for the entry")
    sp.add_argument("-u", "--include-untracked", action="store_true")
    sp.add_argument("-a", "--all", action="store_true", help="include ignored files")
    sp.add_argument("paths", nargs="*", help="optional pathspec (after `--`)")
    sp.set_defaults(func=cmd_push)

    sp = sub.add_parser("list", help="this lane's pinned entries + the shared register")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("apply", help="apply a lane entry by sha, keeping the pin")
    sp.add_argument("entry", nargs="?", type=int, help="lane entry id (default: newest)")
    sp.add_argument("--index", action="store_true", help="also restore the index")
    sp.set_defaults(func=lambda a: cmd_apply(a, pop=False))

    sp = sub.add_parser("pop", help="apply a lane entry by sha and unpin it (shared register untouched)")
    sp.add_argument("entry", nargs="?", type=int, help="lane entry id (default: newest)")
    sp.add_argument("--index", action="store_true", help="also restore the index")
    sp.set_defaults(func=cmd_pop)

    sp = sub.add_parser("show", help="git stash show for a lane entry")
    sp.add_argument("entry", nargs="?", type=int, help="lane entry id (default: newest)")
    sp.add_argument("-p", "--patch", action="store_true", help="show the full patch")
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("drop", help="unpin a lane entry WITHOUT applying it")
    sp.add_argument("entry", nargs="?", type=int)
    sp.add_argument("--yes", action="store_true", help="required: the pin may be the only handle")
    sp.set_defaults(func=cmd_drop)

    sp = sub.add_parser("check", help="would a bare `git stash pop` here take foreign work?")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("recover", help="restore a dropped stash commit to the register AND pin it")
    sp.add_argument("sha")
    sp.add_argument("-m", "--message", help="subject for the restored entry (default: the commit's own)")
    sp.set_defaults(func=cmd_recover)

    sp = sub.add_parser("prune", help="reclaim shared-register entries pinned by a lane ref")
    sp.add_argument("--apply", action="store_true", help="actually delete (default: dry run)")
    sp.set_defaults(func=cmd_prune)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.paths = getattr(args, "paths", None) or []
    try:
        return args.func(args)
    except GitFailed as exc:
        sys.stderr.write(f"git failed (rc={exc.rc}): {exc}\n")
        return 3
    except LaneStashError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

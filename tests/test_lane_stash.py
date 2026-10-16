"""#8051 — `refs/stash` is SHARED across worktrees: a lane must never take a peer's entry.

Hermetic: a real temp git repo with two REAL linked worktrees, real `git stash`
calls, no network, no Docker, no FalkorDB. Nothing is monkeypatched — the
property under test IS git's behaviour plus the tool's contract around it.

The load-bearing test is :func:`test_acceptance_lane_a_never_applies_lane_bs_entry`:
lane A stashes, lane B stashes, lane A restores. Before the fix, A applies and
DROPS B's entry (B's untracked file appears in A's worktree) and B's stash is
gone from the register. The test asserts the opposite of all three points, plus
that the shared register itself was not modified.

:func:`test_premise_the_raw_hazard_still_reproduces` is the CONTROL: it drives the
same two worktrees with BARE `git stash push`/`pop` and asserts the incident
still happens. If a future git made the stash worktree-scoped, that test fails
and tells us the premise (and this tool's reason to exist) has changed — the
hazard must not be asserted only against our own wrapper.

Run:
    TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest tests/test_lane_stash.py -q
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "lane_stash.py"
PYTHON = sys.executable


def _load_tool():
    """Import `tools/lane_stash.py` in-process (for the paths no subprocess can force)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("lane_stash_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _env() -> dict[str, str]:
    """A hermetic git environment.

    Every ``GIT_*`` variable is dropped and the global/system config is
    /dev/null'd: a host with ``commit.gpgsign = true`` (or an inherited
    ``GIT_DIR``) would otherwise make a real ``git stash push`` fail or write
    into a peer checkout — the very class of accident these tests model.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = "lane-stash test"
    env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = "lane-stash@test.invalid"
    return env


class Repo:
    """A temp repo with two linked worktrees, `a` and `b`, on their own branches."""

    def __init__(self, tmp_path: Path):
        self.root = tmp_path / "repo"
        self.a = tmp_path / "wtA"
        self.b = tmp_path / "wtB"
        self.root.mkdir()
        self.git("init", "-q", "-b", "main", str(self.root))
        self.git("config", "user.name", "lane-stash test")
        self.git("config", "user.email", "lane-stash@test.invalid")
        (self.root / "f.txt").write_text("base\n")
        self.git("add", "f.txt")
        self.git("commit", "-qm", "base")
        self.git("worktree", "add", "-q", "-b", "feat/a", str(self.a))
        self.git("worktree", "add", "-q", "-b", "feat/b", str(self.b))

    def git(self, *args: str, cwd: Path | None = None, check: bool = True):
        cp = subprocess.run(
            ["git", *args], cwd=str(cwd or self.root), capture_output=True,
            text=True, env=_env(),
        )
        if check and cp.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {cp.stderr or cp.stdout}")
        return cp

    def lane(self, cwd: Path, *args: str):
        """Invoke tools/lane_stash.py inside `cwd` (a real worktree)."""
        return subprocess.run(
            [PYTHON, str(TOOL), *args], cwd=str(cwd), capture_output=True,
            text=True, env=_env(),
        )

    def status(self, cwd: Path) -> str:
        return self.git("status", "--porcelain", cwd=cwd).stdout

    def stash_shas(self) -> list[str]:
        """The shared register's shas, newest first (position order)."""
        out = self.git("stash", "list", "--format=%H").stdout
        return [ln.strip() for ln in out.splitlines() if ln.strip()]

    def stash_sha(self) -> str | None:
        cp = self.git("rev-parse", "--verify", "-q", "refs/stash", check=False)
        return cp.stdout.strip() or None

    def lane_refs(self) -> dict[str, str]:
        out = self.git(
            "for-each-ref", "--format=%(refname) %(objectname)", "refs/lane"
        ).stdout
        refs = {}
        for line in out.splitlines():
            ref, _, sha = line.partition(" ")
            refs[ref] = sha
        return refs

    def dirty(self, cwd: Path, name: str, text: str, untracked: bool = False) -> None:
        (cwd / name).write_text(text)
        if not untracked:
            self.git("add", name, cwd=cwd)


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path)


def _fail(cp) -> str:
    return f"rc={cp.returncode}\nstdout:\n{cp.stdout}\nstderr:\n{cp.stderr}"


# ── the acceptance evidence ────────────────────────────────────────────────

def test_acceptance_lane_a_never_applies_lane_bs_entry(repo):
    """A pushes, B pushes, A pops: A gets ONLY A's work, B's entry survives."""
    # lane A: tracked + untracked change, stashed through the tool
    repo.dirty(repo.a, "f.txt", "base\na-from-A\n")
    (repo.a / "uA.txt").write_text("A untracked\n")
    push_a = repo.lane(repo.a, "push", "-u", "-m", "lane A work")
    assert push_a.returncode == 0, _fail(push_a)
    sha_a = repo.stash_sha()
    assert sha_a
    assert repo.status(repo.a) == "", "a lane push must leave the worktree clean"

    # lane B does the same; its push is what renumbers A's entry
    repo.dirty(repo.b, "f.txt", "base\nb-from-B\n")
    (repo.b / "uB.txt").write_text("B untracked\n")
    push_b = repo.lane(repo.b, "push", "-u", "-m", "lane B work")
    assert push_b.returncode == 0, _fail(push_b)
    sha_b = repo.stash_sha()
    assert sha_b and sha_b != sha_a
    assert repo.stash_shas() == [sha_b, sha_a], (
        "the register is repo-global: B's entry is now @{0} and A's renumbered to @{1}"
    )

    # the detector names the hazard in A — a bare pop THERE would take B's work
    check = repo.lane(repo.a, "check")
    assert check.returncode == 1, _fail(check)
    assert "feat/b" in check.stdout

    # lane A restores ITS OWN entry, by lane-scoped sha, with B's entry on top
    pop_a = repo.lane(repo.a, "pop")
    assert pop_a.returncode == 0, _fail(pop_a)

    status = repo.status(repo.a)
    assert " M f.txt" in status and "?? uA.txt" in status, status
    assert "uB.txt" not in status, f"lane A took lane B's work:\n{status}"
    assert not (repo.a / "uB.txt").exists()
    assert "a-from-A" in (repo.a / "f.txt").read_text()

    # B's entry is STILL in the shared register, at its original sha
    assert repo.stash_shas() == [sha_b, sha_a], "lane A dropped or moved a peer's entry"

    # and B can still restore its own work
    pop_b = repo.lane(repo.b, "pop")
    assert pop_b.returncode == 0, _fail(pop_b)
    status_b = repo.status(repo.b)
    assert "?? uB.txt" in status_b and "uA.txt" not in status_b, status_b
    assert "b-from-B" in (repo.b / "f.txt").read_text()


def test_premise_the_raw_hazard_still_reproduces(repo):
    """CONTROL: bare git still steals the peer's entry — the premise of #8051.

    If this ever stops reproducing, git has made the stash worktree-scoped and
    `tools/lane_stash.py` should be re-evaluated rather than silently kept.
    """
    repo.dirty(repo.a, "f.txt", "base\na-from-A\n")
    repo.git("stash", "push", "-q", "-m", "A bare", cwd=repo.a)
    sha_a = repo.stash_sha()

    repo.dirty(repo.b, "f.txt", "base\nb-from-B\n")
    (repo.b / "uB.txt").write_text("B untracked\n")
    repo.git("stash", "push", "-q", "-u", "-m", "B bare", cwd=repo.b)
    sha_b = repo.stash_sha()
    assert sha_b != sha_a

    # A's unqualified pop takes B's entry and drops it
    repo.git("stash", "pop", cwd=repo.a)
    status = repo.status(repo.a)
    assert "?? uB.txt" in status, "the raw hazard no longer reproduces — re-evaluate the tool"
    assert (repo.a / "uB.txt").exists()
    assert repo.stash_shas() == [sha_a], "the bare pop dropped the peer's entry (the incident)"


# ── the detector ───────────────────────────────────────────────────────────

def test_check_is_safe_when_the_top_entry_is_this_branch(repo):
    repo.dirty(repo.a, "f.txt", "base\na\n")
    assert repo.lane(repo.a, "push", "-m", "A").returncode == 0
    check = repo.lane(repo.a, "check")
    assert check.returncode == 0, _fail(check)
    assert "SAFE" in check.stdout


def test_check_is_safe_on_an_empty_register(repo):
    check = repo.lane(repo.a, "check")
    assert check.returncode == 0, _fail(check)
    assert "empty" in check.stdout


def test_check_fails_closed_on_an_unattributable_top_entry(repo):
    """A hand-written rescue note (no `On <branch>:` subject) is not 'safe'.

    `git stash push -m` always prefixes `On <branch>:`, but `git stash store -m`
    — how the real register's `RESCUED by <lane>: …` notes were written — does
    not, and an entry whose origin cannot be read must never be reported safe.
    """
    repo.dirty(repo.a, "f.txt", "base\nx\n")
    repo.git("stash", "push", "-q", "-m", "work", cwd=repo.a)
    sha = repo.stash_sha()
    repo.git("stash", "drop", cwd=repo.a)
    repo.git("stash", "store", "-m", "RESCUED by some-other-lane", sha)
    check = repo.lane(repo.a, "check")
    assert check.returncode == 1, _fail(check)
    assert "unreadable subject" in check.stdout


def test_check_fails_closed_when_a_peer_lane_is_on_the_same_branch(repo, tmp_path):
    """Two worktrees can be FORCED onto ONE branch — the lane key must decide.

    `lane_key` is worktree-scoped by design, so two worktrees on one branch are
    two distinct lanes. The branch heuristic cannot tell them apart and reports
    SAFE while a bare `git stash pop` takes the peer's entry (#8051 review P2).
    Pre-fix this fails with exit 0.
    """
    c = tmp_path / "wtC"
    repo.git("worktree", "add", "-q", "--force", str(c), "feat/a")
    for wd in (repo.a, c):
        assert repo.git("rev-parse", "--abbrev-ref", "HEAD", cwd=wd).stdout.strip() == "feat/a"

    repo.dirty(repo.a, "f.txt", "base\na-A\n")
    assert repo.lane(repo.a, "push", "-m", "A work").returncode == 0
    repo.dirty(c, "f.txt", "base\nc-C\n")
    assert repo.lane(c, "push", "-m", "C work").returncode == 0
    shas = repo.stash_shas()
    assert len(shas) == 2 and shas[0] != shas[1]
    assert len(repo.lane_refs()) == 2, repo.lane_refs()  # distinct lanes, one branch

    check = repo.lane(repo.a, "check")
    assert check.returncode == 1, _fail(check)
    assert "SAFE" not in check.stdout, check.stdout

    # the hazard the detector refused to clear is real: a bare pop in A takes C's work
    bare = repo.git("stash", "pop", cwd=repo.a, check=False)
    assert bare.returncode == 0, _fail(bare)
    assert "c-C" in (repo.a / "f.txt").read_text(), (
        "the state must actually be unsafe for the fail-closed assertion to mean anything"
    )


def test_check_is_safe_for_a_recovered_entry_pinned_to_this_lane(repo):
    """A `recover -m` entry carries no lane marker but the pin makes it ours.

    Without checking the lane's own pinned shas, the unreadable subject would
    make `check` report a false HAZARD on work this lane explicitly recovered.
    """
    repo.dirty(repo.a, "f.txt", "base\nrescued\n")
    repo.git("stash", "push", "-q", "-m", "On feat/a: note", cwd=repo.a)
    dropped = repo.stash_sha()
    repo.git("stash", "drop", cwd=repo.a)
    cp = repo.lane(repo.a, "recover", "-m", "rescued note", dropped)
    assert cp.returncode == 0, _fail(cp)
    assert repo.stash_shas() == [dropped]

    check = repo.lane(repo.a, "check")
    assert check.returncode == 0, _fail(check)
    assert "SAFE" in check.stdout


def test_check_fails_closed_on_a_markerless_entry_naming_this_branch(repo, tmp_path):
    """A bare-git entry on THIS branch is not evidence of ownership.

    The register predates this tool, so it is full of marker-less bare-git
    entries whose subjects always name a branch, and two worktrees can be forced
    onto ONE branch. `On feat/a:` names the BRANCH, never the lane, so the
    branch-only fallthrough reported SAFE while a bare `git stash pop` took the
    peer's entry (#8051 review P2). Pre-fix this exits 0.
    """
    c = tmp_path / "wtC"
    repo.git("worktree", "add", "-q", "--force", str(c), "feat/a")
    # a BARE `git stash push` in the peer worktree: no lane marker, no lane pin
    repo.dirty(c, "f.txt", "base\nmarkerless-peer\n")
    repo.git("stash", "push", "-q", "-m", "peer unpinned work", cwd=c)
    assert repo.stash_shas(), "precondition: the peer's entry is in the register"
    assert repo.lane_refs() == {}, "precondition: neither lane pinned anything"
    assert "On feat/a:" in repo.git("stash", "list", "--format=%gs").stdout

    check = repo.lane(repo.a, "check")
    assert check.returncode == 1, _fail(check)
    assert "SAFE" not in check.stdout, check.stdout

    # and the state the detector refused to clear is genuinely unsafe
    bare = repo.git("stash", "pop", cwd=repo.a, check=False)
    assert bare.returncode == 0, _fail(bare)
    assert "markerless-peer" in (repo.a / "f.txt").read_text()


def test_check_json_exposes_the_verdict(repo):
    import json

    repo.dirty(repo.a, "f.txt", "base\na\n")
    repo.lane(repo.a, "push", "-m", "A")
    repo.dirty(repo.b, "f.txt", "base\nb\n")
    repo.lane(repo.b, "push", "-m", "B")
    cp = repo.lane(repo.a, "check", "--json")
    assert cp.returncode == 1, _fail(cp)
    verdict = json.loads(cp.stdout)
    assert verdict["safe"] is False
    assert verdict["bare_pop_branch"] == "feat/b"
    assert verdict["branch"] == "feat/a"
    assert verdict["lane"] != json.loads(repo.lane(repo.b, "check", "--json").stdout)["lane"]


# ── lane scoping ───────────────────────────────────────────────────────────

def test_lane_keys_are_worktree_scoped(repo):
    repo.dirty(repo.a, "f.txt", "base\na\n")
    repo.lane(repo.a, "push", "-m", "A")
    assert repo.lane(repo.b, "list").stdout.count("(no pinned entries)") == 1, (
        "lane B must not see lane A's pinned entry"
    )
    refs = repo.lane_refs()
    assert len(refs) == 1
    assert "/wtA-" in next(iter(refs)), refs


def test_a_branch_switch_does_not_orphan_a_lanes_pins(repo):
    """The key is the WORKTREE, not the branch: a baseline checkout keeps the pins.

    A branch-keyed lane ref namespace would make `list` empty after
    `git checkout other-branch` while the entry sat under the old key — and
    taking a baseline on another branch is exactly how the #2654-style incidents
    start.
    """
    repo.dirty(repo.a, "f.txt", "base\na\n")
    assert repo.lane(repo.a, "push", "-m", "baseline work").returncode == 0
    before = repo.lane_refs()

    repo.git("checkout", "-q", "-b", "baseline-check", cwd=repo.a)
    listing = repo.lane(repo.a, "list")
    assert listing.returncode == 0, _fail(listing)
    assert "(no pinned entries)" not in listing.stdout, listing.stdout
    assert repo.lane_refs() == before, "a checkout must not move the lane ref"

    # and the entry is still restorable from the new branch
    assert repo.lane(repo.a, "pop").returncode == 0
    assert "a" in (repo.a / "f.txt").read_text()


def test_lane_ids_are_monotonic_across_pushes(repo):
    for i in range(2):
        repo.dirty(repo.a, "f.txt", f"base\na{i}\n")
        cp = repo.lane(repo.a, "push", "-m", f"push {i}")
        assert cp.returncode == 0, _fail(cp)
    ids = sorted(r.rsplit("/", 1)[-1] for r in repo.lane_refs())
    assert ids == ["1", "2"]


def test_push_with_a_clean_worktree_pins_nothing(repo):
    cp = repo.lane(repo.a, "push", "-m", "nothing here")
    assert cp.returncode == 0, _fail(cp)
    assert "nothing to stash" in cp.stdout
    assert repo.lane_refs() == {}
    assert repo.stash_sha() is None


# ── the contract has no lane override ──────────────────────────────────────

def test_there_is_no_lane_or_ref_override(repo):
    """A peer's lane key is a computable sha1(path)[:8] — naming it must not be possible."""
    for flag in ("--lane", "--ref"):
        cp = repo.lane(repo.a, "list", flag, "someone-elses-lane")
        assert cp.returncode != 0
        assert "unrecognized arguments" in cp.stderr


# ── failure modes ──────────────────────────────────────────────────────────

def test_pop_that_conflicts_keeps_the_pin_and_the_work(repo):
    repo.dirty(repo.a, "f.txt", "base\nstashed-A\n")
    assert repo.lane(repo.a, "push", "-m", "A").returncode == 0

    # a divergent local edit to the same line makes the apply conflict
    (repo.a / "f.txt").write_text("base\nlocal-divergent\n")
    cp = repo.lane(repo.a, "pop")
    assert cp.returncode == 3, _fail(cp)
    assert "entry kept" in cp.stderr, cp.stderr
    assert len(repo.lane_refs()) == 1, "a conflicted apply must not release the pin"

    # the pin is the only handle on that work: recover by staging away the conflict
    repo.git("checkout", "--", "f.txt", cwd=repo.a)
    retry = repo.lane(repo.a, "pop")
    assert retry.returncode == 0, _fail(retry)
    assert "stashed-A" in (repo.a / "f.txt").read_text()
    assert repo.lane_refs() == {}


def test_drop_requires_yes(repo):
    repo.dirty(repo.a, "f.txt", "base\na\n")
    repo.lane(repo.a, "push", "-m", "A")
    cp = repo.lane(repo.a, "drop", "1")
    assert cp.returncode == 2, _fail(cp)
    assert "without --yes" in cp.stderr
    assert len(repo.lane_refs()) == 1
    assert repo.lane(repo.a, "drop", "1", "--yes").returncode == 0
    assert repo.lane_refs() == {}


def test_pop_unknown_id_is_an_error(repo):
    repo.dirty(repo.a, "f.txt", "base\na\n")
    repo.lane(repo.a, "push", "-m", "A")
    cp = repo.lane(repo.a, "pop", "7")
    assert cp.returncode == 2, _fail(cp)
    assert "no entry 7" in cp.stderr


# ── recovery + reclamation ─────────────────────────────────────────────────

def test_recover_restores_a_dropped_entry_and_pins_it(repo):
    repo.dirty(repo.a, "f.txt", "base\npeer-work\n")
    (repo.a / "peer.txt").write_text("peer untracked\n")
    repo.git("stash", "push", "-q", "-u", "-m", "On feat/a: peer rescue note", cwd=repo.a)
    dropped = repo.stash_sha()
    repo.git("stash", "drop", cwd=repo.a)
    assert repo.stash_shas() == []

    cp = repo.lane(repo.a, "recover", dropped)
    assert cp.returncode == 0, _fail(cp)
    assert repo.stash_shas() == [dropped], "the recovered entry must be back in the register"
    assert len(repo.lane_refs()) == 1, "a recovered entry must be pinned this time"

    # and it is restorable by the lane it was recovered into
    assert repo.lane(repo.a, "pop").returncode == 0
    assert "peer-work" in (repo.a / "f.txt").read_text()


def test_recover_refuses_a_non_stash_commit(repo):
    head = repo.git("rev-parse", "HEAD").stdout.strip()
    cp = repo.lane(repo.a, "recover", head)
    assert cp.returncode == 2, _fail(cp)
    assert "not a stash entry" in cp.stderr
    assert repo.stash_shas() == []


def test_prune_refuses_unpinned_entries_and_removes_pinned_ones(repo):
    # a bare-git lane's entry (pinned nowhere) + a pinned lane entry
    repo.dirty(repo.a, "f.txt", "base\nbare\n")
    repo.git("stash", "push", "-q", "-m", "bare-git work", cwd=repo.a)
    bare_sha = repo.stash_sha()
    repo.dirty(repo.b, "f.txt", "base\npinned\n")
    assert repo.lane(repo.b, "push", "-m", "pinned work").returncode == 0
    pinned_sha = repo.stash_sha()
    assert repo.stash_shas() == [pinned_sha, bare_sha]

    dry = repo.lane(repo.a, "prune")
    assert dry.returncode == 0, _fail(dry)
    assert "refused" in dry.stdout and "dry run" in dry.stdout
    assert repo.stash_shas() == [pinned_sha, bare_sha], "a dry run must not delete anything"

    applied = repo.lane(repo.a, "prune", "--apply")
    assert applied.returncode == 0, _fail(applied)
    assert repo.stash_shas() == [bare_sha], "only the pinned entry may be reclaimed"
    assert pinned_sha not in repo.stash_shas()

    # the object is still reachable and the lane can still restore its work
    refs = repo.lane_refs()
    assert refs and pinned_sha in set(refs.values())
    assert repo.lane(repo.b, "pop").returncode == 0
    assert "pinned" in (repo.b / "f.txt").read_text()


def test_prune_of_the_last_entry_drops_refs_stash_and_unwedges_check(repo):
    """Reclaiming the LAST entry must drop `refs/stash`, not only its reflog.

    `git reflog delete --updateref` empties the reflog but git does NOT drop the
    ref when it empties (measured — the plan's row 8 asserted the opposite), so
    the "removed" entry stays live for bare git (`git stash pop` applies AND
    drops it) and `check` fails closed in every worktree with no lane_stash
    command able to clear it (#8051 review P2). Pre-fix: `refs/stash` still
    resolves and `check` exits 1.
    """
    repo.dirty(repo.a, "f.txt", "base\na\n")
    assert repo.lane(repo.a, "push", "-m", "A").returncode == 0
    assert repo.stash_sha()

    cp = repo.lane(repo.a, "prune", "--apply")
    assert cp.returncode == 0, _fail(cp)
    assert repo.stash_shas() == [], "the reflog must be empty"
    assert repo.stash_sha() is None, (
        "refs/stash must be dropped with the last entry, as `git stash drop` does — "
        "otherwise the reclaimed entry is still live for bare git"
    )

    # the detector the guidance tells lanes to trust must no longer be wedged
    check = repo.lane(repo.a, "check")
    assert check.returncode == 0, _fail(check)
    assert "SAFE" in check.stdout

    bare = repo.git("stash", "pop", cwd=repo.a, check=False)
    assert bare.returncode != 0, f"the reclaimed entry is still poppable:\n{_fail(bare)}"


def test_prune_clears_an_already_orphaned_refs_stash(repo):
    """A register emptied by an EARLIER prune can still be cleared by prune.

    `reflog delete --updateref` on the last entry leaves `refs/stash` resolving
    while the register enumerates nothing; before the fix that state made
    `check` exit 1 in every worktree and no lane_stash command could clear it
    (the register read returned 0 rows and prune returned early).
    """
    repo.dirty(repo.a, "f.txt", "base\na\n")
    assert repo.lane(repo.a, "push", "-m", "A").returncode == 0
    # reproduce the wedge the pre-fix prune left behind
    repo.git("reflog", "delete", "--updateref", "refs/stash@{0}")
    assert repo.stash_shas() == [] and repo.stash_sha() is not None
    assert repo.lane(repo.a, "check").returncode == 1

    assert repo.lane(repo.a, "prune").returncode == 0  # dry run, no-op or reported
    cp = repo.lane(repo.a, "prune", "--apply")
    assert cp.returncode == 0, _fail(cp)
    assert repo.stash_sha() is None, "prune must clear the orphaned refs/stash"
    check = repo.lane(repo.a, "check")
    assert check.returncode == 0, _fail(check)
    assert "SAFE" in check.stdout


def test_prune_refuses_an_unpinned_refs_stash_orphan(repo):
    """An emptied reflog over a PEER's bare entry must survive `prune --apply`.

    `git reflog expire` (routine maintenance) leaves `refs/stash` pointing at an
    entry no lane ref pins. That ref is the peer's ONLY handle — dropping it makes
    the commit gc-eligible — and the command's own line above it reports the entry
    as refused. Pre-fix `prune --apply` dropped the ref anyway, destroying a
    peer's stash with no lane_stash recovery path (#8051 review P1).
    """
    # the peer's BARE stash: marker-less, pinned by no lane ref
    repo.dirty(repo.b, "f.txt", "base\npeer-only\n")
    repo.git("stash", "push", "-q", "-m", "peer unpinned work", cwd=repo.b)
    sha = repo.stash_sha()
    assert sha and repo.lane_refs() == {}, "precondition: unpinned peer entry"

    # routine maintenance empties the reflog; the ref (and the peer's handle) live
    repo.git("reflog", "expire", "--expire=now", "--all")
    assert repo.stash_shas() == [] and repo.stash_sha() == sha

    cp = repo.lane(repo.a, "prune", "--apply")
    assert cp.returncode == 0, _fail(cp)
    assert repo.stash_sha() == sha, (
        "prune destroyed an unpinned peer's last handle — the object is now gc-eligible"
    )
    assert "recover" in cp.stdout, cp.stdout

    # the peer can still restore its own work
    bare = repo.git("stash", "pop", cwd=repo.b, check=False)
    assert bare.returncode == 0, _fail(bare)
    assert "peer-only" in (repo.b / "f.txt").read_text()


def test_prune_refuses_a_foreign_lanes_unpinned_entry(repo):
    """Only THIS lane's own marker is reclaimable — a peer's popped entry is kept.

    `pop` leaves the shared entry behind, so another lane's `prune` sees a
    marker-less-from-its-perspective (foreign) unpinned entry. Reclaiming it would
    be as lossy as dropping an orphan (#8051 review P1).
    """
    repo.dirty(repo.b, "f.txt", "base\nb-work\n")
    assert repo.lane(repo.b, "push", "-m", "B").returncode == 0
    assert repo.lane(repo.b, "pop").returncode == 0
    sha = repo.stash_sha()
    assert sha and repo.lane_refs() == {}, "precondition: B's entry is unpinned"

    cp = repo.lane(repo.a, "prune", "--apply")
    assert cp.returncode == 0, _fail(cp)
    assert repo.stash_sha() == sha, "prune dropped a peer lane's entry"
    assert repo.stash_shas() == [sha]


def test_prune_reclaims_this_lanes_own_popped_entries(repo):
    """`push` then `pop` leaves an unpinned entry; prune must reclaim this lane's own.

    `pop` deliberately leaves the shared entry in place and releases the lane pin
    (see the module's OVERRIDES note), so the register grows with every push/pop
    cycle. Pre-fix `prune` refused all of it as "unpinned", leaving no lane_stash
    command able to reclaim the tool's own entries (#8051 review P2).
    """
    for i in range(3):
        repo.dirty(repo.a, "f.txt", f"base\na{i}\n")
        assert repo.lane(repo.a, "push", "-m", f"cycle {i}").returncode == 0
        assert repo.lane(repo.a, "pop").returncode == 0
    assert len(repo.stash_shas()) == 3, repo.stash_shas()
    assert repo.lane_refs() == {}, "precondition: pop released every lane pin"

    cp = repo.lane(repo.a, "prune", "--apply")
    assert cp.returncode == 0, _fail(cp)
    assert repo.stash_shas() == [], "this lane's own popped entries were not reclaimed"
    assert repo.stash_sha() is None
    check = repo.lane(repo.a, "check")
    assert check.returncode == 0, _fail(check)
    assert "SAFE" in check.stdout


def test_pop_never_modifies_the_shared_register(repo):
    """`pop` is deliberately not `git stash pop` (see the module's OVERRIDES note)."""
    repo.dirty(repo.a, "f.txt", "base\na\n")
    repo.lane(repo.a, "push", "-m", "A")
    before = repo.stash_shas()
    assert before
    assert repo.lane(repo.a, "pop").returncode == 0
    assert repo.stash_shas() == before, (
        "the shared entry must survive `pop`: its positional reflog delete is racy and "
        "can drop a bare-git peer's unpinned entry"
    )
    assert repo.lane_refs() == {}, "the lane's own pin is released"


# ── fail-closed detection when the register cannot be read (#8051 review P1) ──

def test_check_fails_closed_when_refs_stash_is_not_in_the_reflog(repo):
    """`refs/stash` resolving but `git stash list` empty is UNSAFE, not SAFE.

    Reflog expiry is ordinary git maintenance. It can remove every reflog entry
    while `refs/stash` still points at a real (here: FOREIGN) stash, and then a
    bare `git stash pop` applies AND drops that entry. The detector must not
    answer "safe" from the absence of rows (#8051 review P1).
    """
    # lane B's entry becomes the shared register's top entry
    repo.dirty(repo.b, "f.txt", "base\nb-from-B\n")
    (repo.b / "uB.txt").write_text("B untracked\n")
    assert repo.lane(repo.b, "push", "-u", "-m", "lane B work").returncode == 0
    sha_b = repo.stash_sha()
    assert sha_b

    # routine maintenance: the reflog is gone, but the ref (and B's entry) live
    repo.git("reflog", "expire", "--expire=now", "--all")
    assert repo.stash_shas() == [], "precondition: the reflog must be empty after expiry"
    assert repo.stash_sha() == sha_b, "precondition: refs/stash must still resolve"

    # from lane A the detector must NOT clear a bare `git stash pop`
    check = repo.lane(repo.a, "check")
    assert check.returncode != 0, _fail(check)
    assert "SAFE" not in check.stdout, check.stdout

    # and the hazard the detector just refused to clear is real: bare pop takes B
    bare = repo.git("stash", "pop", cwd=repo.a, check=False)
    assert bare.returncode == 0, _fail(bare)
    assert (repo.a / "uB.txt").exists(), (
        "the state must actually be unsafe for the fail-closed assertion to mean anything"
    )
    assert repo.stash_sha() is None, "the bare pop dropped B's entry (the incident)"


def test_check_read_failure_is_a_state_error(repo, monkeypatch):
    """An unreadable `git stash list` exits non-zero, never `SAFE` (exit 0)."""
    mod = _load_tool()
    monkeypatch.chdir(repo.a)
    real_git = mod._git

    def fake_git(*args, check=True):
        if args[:2] == ("stash", "list"):
            return subprocess.CompletedProcess(
                ["git", *args], 1, "", "fatal: cannot read refs/stash"
            )
        return real_git(*args, check=check)

    monkeypatch.setattr(mod, "_git", fake_git)
    rc = mod.main(["check", "--json"])
    assert rc == 2, f"an unreadable register is a state error (exit 2), got {rc}"


# ── a valid lane ref for a git-hostile worktree name (#8051 review P2) ──

def test_lane_key_is_a_valid_git_ref_for_hostile_directory_names(tmp_path):
    """`lane_key` must emit a git-VALID ref component, not merely a safe filename.

    `..`, a leading/trailing `.`, and a trailing `.lock` are all legal in a
    directory basename and all rejected by `git check-ref-format`. Left in the
    slug they make `_pin` fail after the stash already exists (#8051 review P2).
    """
    mod = _load_tool()
    for name in ("a..b", "lane.lock", "x.lock.lock", ".hidden", "trailing.", "weird@{}name"):
        d = tmp_path / name
        d.mkdir()
        key = mod.lane_key(d)
        ref = f"{mod.REFS_NS}/{key}/stash/1"
        cp = subprocess.run(["git", "check-ref-format", ref], capture_output=True, text=True)
        assert cp.returncode == 0, f"{name!r} -> {ref!r} is not a valid ref: {cp.stderr}"


def test_push_pins_in_a_hostile_named_worktree(repo, tmp_path):
    """End to end: a `a..b.lock` worktree can push and its pin is a valid ref."""
    hostile = tmp_path / "a..b.lock"
    repo.git("worktree", "add", "-q", "-b", "feat/hostile", str(hostile))
    repo.dirty(hostile, "f.txt", "base\nhostile-work\n")
    cp = repo.lane(hostile, "push", "-m", "hostile lane")
    assert cp.returncode == 0, _fail(cp)
    refs = repo.lane_refs()
    assert len(refs) == 1, refs
    ref = next(iter(refs))
    assert subprocess.run(["git", "check-ref-format", ref]).returncode == 0, ref
    # the pin is real: the lane can restore its own work
    assert repo.lane(hostile, "pop").returncode == 0
    assert "hostile-work" in (hostile / "f.txt").read_text()


def test_push_reports_the_orphan_when_pinning_fails(repo, monkeypatch, capsys):
    """A `_pin` failure must say the stash EXISTS and how to recover it.

    The stash commit is already in the shared register by the time `_pin` runs;
    an error that reads like "nothing was created" hides an unpinned entry a
    bare `git stash pop` can take (#8051 review P2).
    """
    mod = _load_tool()
    monkeypatch.chdir(repo.a)
    repo.dirty(repo.a, "f.txt", "base\norphan-me\n")

    def boom(key, sha):
        raise mod.LaneStashError("could not allocate a lane ref under refs/lane/x")

    monkeypatch.setattr(mod, "_pin", boom)
    rc = mod.main(["push", "-m", "W"])
    assert rc == 2, rc
    err = capsys.readouterr().err
    assert "EXISTS" in err and "recover" in err, err
    assert repo.stash_sha() is not None, "the entry must actually be in the register"

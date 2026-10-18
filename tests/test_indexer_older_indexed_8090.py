"""#8090 — the DIFF→DRAIN ping-pong when the NEWEST second block exceeds the cap.

#1990 fixed the DIFF-mode pre-boundary probe and made ``truncated`` a property
of the READABLE REGION. It did NOT fix its declared residual: a composite
cursor carried no claim that items OLDER than its boundary second are indexed,
so when the NEWEST second's block holds more items than ``cap`` the cursor
ping-ponged DIFF → DRAIN forever, re-projecting the whole older backlog on
every cycle:

    {S,2025} → {S1,500,t} → {S,1000,t} → {S,1500,t} → {S,2000,t} → {S,2025} → …

This module pins the fix:

* the cursor carries an ``older_indexed`` claim, set only by a run whose walk
  applied no ``since`` and completed clean (fail-closed: absent unless proven),
* DRAIN may skip the older-than-boundary region only when that claim is
  present (so an un-proven cursor keeps draining — #1895 must stay green),
* the clean mint no longer regresses the boundary below the input cursor's own.

Fixture shapes below mirror the issue's verified cycle: 1425 items at the OLD
second ``S`` + 600 at the NEWEST second ``S1``, ``page_size`` 100, ``cap`` 500
— so the newest block alone exceeds the cap and no single DIFF probe can
complete it.
"""
from __future__ import annotations

import os

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import httpx
import pytest

from tests._embedded import _wipe_or
from tests._github_mock import MockGitHubTransport, gh_issue
from tortoise.indexer.github_indexer import GitHubIndexer

S = "2026-08-18T02:49:35Z"
S1 = "2026-08-18T02:49:36Z"

#: The issue's verified cycle, for the assertion messages.
_CYCLE = ("{S,2025} → {S1,500,t} → {S,1000,t} → {S,1500,t} → {S,2000,t} → "
          "{S,2025} → {S1,500,t} → …")


@pytest.fixture(autouse=True)
def _no_embeddings(monkeypatch):
    monkeypatch.setattr("tortoise.embeddings.compute_embedding",
                        lambda *a, **k: None)


@pytest.fixture(scope="module")
def sdk(tmp_path_factory):
    from tortoise.sdk import TortoiseSDK
    store = TortoiseSDK(str(tmp_path_factory.mktemp("idx8090") / "idx.db"))
    yield store
    store.close()


@pytest.fixture(autouse=True)
def _clean_graph(sdk):
    _wipe_or(sdk._get_proj())


def _run(sdk, transport: MockGitHubTransport, repo: str = "acme/repo1",
         **kw) -> dict:
    """One index run against a fresh event loop + client (hermetic)."""
    import asyncio
    client = httpx.AsyncClient(transport=transport)
    indexer = GitHubIndexer("fake-token", httpx_client=client)
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(indexer.index_repo(sdk, repo, **kw))
    finally:
        loop.close()


def _production_shape() -> list[dict]:
    """1425 items at the OLD second + 600 at the NEWEST (issue's shape)."""
    issues = [gh_issue(n, updated_at=S) for n in range(601, 2026)]   # 1425
    issues += [gh_issue(n, updated_at=S1) for n in range(1, 601)]    # 600
    return issues


def _objects(sdk) -> int:
    return int(sdk._get_proj().g.query(
        "MATCH (n:Object) RETURN count(n)").result_set[0][0])


def test_first_run_establishes_the_older_indexed_claim(sdk):
    """A first run applies no ``since`` and enumerates the WHOLE stream, so a
    CLEAN completion proves every item below its boundary is indexed — the
    claim rides the minted cursor."""
    t = MockGitHubTransport(issues=_production_shape(), page_size=100)
    stats = _run(sdk, t, cap=100_000)
    assert stats["processed"] == 2025
    assert stats["cursor"] == {"updated_at": S1, "number": 600,
                               "older_indexed": True}, \
        "a clean full walk must mint the older-than-boundary claim"
    assert _objects(sdk) == 2025


def test_cap_cut_does_not_claim_older_indexed(sdk):
    """FAIL-CLOSED: a cap cut defers real work, so the claim must be absent —
    only the *inherited* claim (the input cursor's) may ride a truncated mint."""
    t = MockGitHubTransport(issues=_production_shape(), page_size=100)
    stats = _run(sdk, t, cap=500)
    assert stats["processed"] == 500
    assert stats["cursor"] == {"updated_at": S1, "number": 500,
                               "truncated": True}
    assert "older_indexed" not in stats["cursor"], (
        "a first-run cap cut has proven nothing about the region below its "
        "boundary — claiming it would let the next DRAIN skip un-indexed "
        "items (#1895 loss class)")


def test_newest_block_over_cap_reaches_steady_state(sdk):
    """THE ISSUE'S ACCEPTANCE: from an invariant-consistent clean cursor whose
    NEWEST block exceeds ``cap``, the steady state is 0 processed / 0 mints /
    cursor stable — the verified 5-poll DIFF↔DRAIN cycle is gone."""
    t = MockGitHubTransport(issues=_production_shape(), page_size=100)
    # An invariant-consistent clean cursor: everything at/older than S is
    # indexed (number 2025 is the block's top) and nothing older exists.
    seed = {"updated_at": S, "number": 2025, "older_indexed": True}
    _run(sdk, t, cap=100_000)          # index the graph first (idempotent after)

    cursor = seed
    history: list[dict] = []
    for _ in range(4):                 # bounded: it must converge, not cycle
        stats = _run(sdk, t, cursor=cursor, cap=500)
        history.append({"processed": stats["processed"],
                        "cursor": dict(stats["cursor"] or {})})
        cursor = stats["cursor"]
    # 2 polls (one DIFF cap cut that inherits the claim, one DRAIN that skips
    # the provably-indexed older region and finishes the newest block).
    assert [(h["processed"], h["cursor"]) for h in history] == [
        (500, {"updated_at": S1, "number": 500, "truncated": True,
               "older_indexed": True}),
        (100, {"updated_at": S1, "number": 600, "older_indexed": True}),
        (0, {"updated_at": S1, "number": 600, "older_indexed": True}),
        (0, {"updated_at": S1, "number": 600, "older_indexed": True}),
    ], f"expected convergence, not the verified cycle {_CYCLE}: {history}"
    # exact-once: no re-minted lifecycle events for the re-probed items
    assert history[0]["cursor"]["older_indexed"] is True, \
        "the DIFF cap cut must INHERIT the input cursor's claim (else the " \
        "next DRAIN re-projects all 1425 older items — the cycle)"
    assert _objects(sdk) == 2025


def test_legacy_cursor_without_claim_self_heals_then_stabilises(sdk):
    """A cursor minted BEFORE the claim existed (or by an older build) must not
    be trusted: DRAIN keeps draining the backlog (#1895 behaviour), the first
    clean FULL walk establishes the claim, and from then on the shape is
    stable. The point: no silent loss on the transition, and the cycle ends."""
    t = MockGitHubTransport(issues=_production_shape(), page_size=100)
    _run(sdk, t, cap=100_000)          # index everything (idempotent after)
    # A legacy clean cursor: correct, but it carries no claim.
    legacy = {"updated_at": S, "number": 2025}

    cursor, seen, converged_at = legacy, [], None
    for poll in range(10):
        stats = _run(sdk, t, cursor=cursor, cap=500)
        seen.append((stats["processed"], dict(stats["cursor"] or {})))
        cursor = stats["cursor"]
        if stats["processed"] == 0:
            converged_at = poll
            break
    assert converged_at is not None, \
        f"the shape must converge (no infinite cycle): {seen}"
    assert cursor == {"updated_at": S1, "number": 600,
                      "older_indexed": True}, \
        f"the transition must end on the claimed cursor (saw {cursor})"
    # and it is a genuine steady state, not another cycle entry
    for _ in range(3):
        steady = _run(sdk, t, cursor=cursor, cap=500)
        assert steady["processed"] == 0
        assert steady["cursor"] == cursor
        assert steady["events_minted"] == 0
    assert _objects(sdk) == 2025, "exact-once + complete census across the transition"


def test_claim_does_not_skip_the_deferred_backlog(sdk):
    """THE #1895 LOSS CLASS, restated as a guard: the claim may only skip
    items that a full clean walk proved indexed. A backlog that was DEFERRED
    (never fetched) must still be reached — with or without the claim on the
    cursor, since a cap-cut mint cannot carry it."""
    t = MockGitHubTransport(issues=_production_shape(), page_size=100)
    # A cap cut over a first run leaves the deferred older backlog unfetched.
    first = _run(sdk, t, cap=500)
    assert first["processed"] == 500
    cursor = dict(first["cursor"])
    assert "older_indexed" not in cursor
    # Even if an operator hand-patches the claim onto it, the walk must not
    # lose the deferred backlog items: they sit ABOVE the boundary second's
    # number prefix, so the boundary rule still reaches them.
    cursor["older_indexed"] = True
    stats = _run(sdk, t, cursor=cursor, cap=500)
    assert stats["processed"] == 100, \
        "the deferred tail of the boundary block must still be drained"
    assert _objects(sdk) == 600

"""Epic 903-C8 (#1246) — hosted dream wiring: mode selection, #329 budget
accounting, 429+Retry-After, selfhost forwarding (DE2E-11).

The hosted handlers accept `team` as an explicit parameter (the Depends
dependency is satisfied by FastAPI at request time) — the tests drive the
handler functions directly with an explicit team dict, pinning the budget +
mode logic without a live server.
"""
from __future__ import annotations

import asyncio
import random
import time as _t

import pytest

from tests.epic903_fixtures import FIXED_SEED, f1_corpus


class TestBudgetAccounting:
    def test_full_override_consumes_bucket(self):
        """mode='full' via the override counts against the #329 bucket;
        exhaustion raises 429 with Retry-After."""
        import tortoise.hosted_api as ha
        from tortoise.quota import MAX_DREAM_FULL_PER_HOUR

        tid = "team-budget-test"
        # Recent timestamps (within the 1h window) — epoch values would be
        # pruned by the handler's freshness sweep.
        ha._DREAM_FULL_BUCKETS[tid] = [_t.time() - 1.0] * MAX_DREAM_FULL_PER_HOUR
        try:
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as he:
                asyncio.run(ha.dream(full=True, org={"org_id": tid}))
            assert he.value.status_code == 429
            assert "Retry-After" in (he.value.headers or {}), (
                "the 429 must carry Retry-After (seconds until window reset)")
        finally:
            ha._DREAM_FULL_BUCKETS.pop(tid, None)

    def test_window_passes_do_not_consume_bucket(self):
        """mode='stale-first' passes are bounded by their per-pass operator
        budget — they do NOT consume the #329 full bucket."""
        import tortoise.hosted_api as ha
        tid = "team-window-test"
        ha._DREAM_FULL_BUCKETS.pop(tid, None)
        try:
            asyncio.run(ha.dream(mode="stale-first", budget=2,
                                  org={"org_id": tid}))
            assert tid not in ha._DREAM_FULL_BUCKETS, (
                "window passes must not consume the #329 bucket")
        finally:
            ha._DREAM_FULL_BUCKETS.pop(tid, None)

    def test_full_mode_via_override_consumes_bucket(self):
        """mode='full' (the I1 override) consumes the bucket EVEN when
        full=False — the budget rule counts full passes incl. override."""
        import tortoise.hosted_api as ha
        from tortoise.quota import MAX_DREAM_FULL_PER_HOUR
        tid = "team-override-test"
        # Bucket at capacity → the override-full request (full=False but
        # mode='full') must hit the 429, proving the override is counted.
        ha._DREAM_FULL_BUCKETS[tid] = (
            [_t.time() - 1.0] * MAX_DREAM_FULL_PER_HOUR)
        try:
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as he:
                asyncio.run(ha.dream(mode="full", org={"org_id": tid}))
            assert he.value.status_code == 429
        finally:
            ha._DREAM_FULL_BUCKETS.pop(tid, None)


class TestModeWiring:
    def test_worker_routes_write_burst_to_local(self):
        """The _dream_worker drains write bursts in LOCAL mode (W1) — never
        silently full (I1 precedence)."""
        f = f1_corpus()
        try:
            f.sdk._mark_dirty([f.claims["p1"]])
            random.seed(FIXED_SEED)
            r = f.sdk.dream(dirty_only=True, mode="local")
            assert r["mode"] == "local"
        finally:
            f.sdk.close()

    def test_selfhost_forwards_mode_and_budget(self):
        """selfhost /dream forwards mode/budget transparently (no #329)."""
        import inspect  # noqa: I001
        from tortoise.selfhost_api import dream as selfhost_dream
        sig = inspect.signature(selfhost_dream)
        assert "mode" in sig.parameters, (
            "selfhost /dream must forward mode")
        assert "budget" in sig.parameters, (
            "selfhost /dream must forward budget")


class TestDefaultArmPinsLocal:
    """#8152: both `/v1/dream` default arms are PINNED to LOCAL.

    `dirty_only=True` is a DEPRECATED no-op (epic 903-C6), so passing it asked
    the SDK's context auto-selector for a mode instead of selecting one: an
    empty/small graph resolves to `full`. That (a) made one request's response
    shape depend on a graph statistic — `converged_all`/`batches` here vs
    `converged`/`iterations` on a large graph — and (b) let an auto-selected
    full pass run outside the #329 bucket, because the handler's budget
    predicate reads the REQUEST (`mode`/`full`), not the graph.

    Both arms must therefore pass an explicit mode, and the pass that runs must
    be the pass the budget counted.
    """

    def test_default_arm_returns_the_local_shape(self):
        """Default request ⇒ I1 local key-set, `mode` local — the shape
        `tests/test_hosted_api.py::TestDreamEndpoint::test_dream_incremental`
        pins. The org graph here is empty, which is exactly the size that used
        to auto-select `full`."""
        import tortoise.hosted_api as ha
        tid = "team-default-arm-shape"
        ha._DREAM_FULL_BUCKETS.pop(tid, None)
        try:
            r = asyncio.run(ha.dream(org={"org_id": tid}))
        finally:
            ha._DREAM_FULL_BUCKETS.pop(tid, None)
        assert r["mode"] == "local", f"default arm ran {r['mode']!r}, not local"
        assert "converged" in r and "iterations" in r, r
        assert "converged_all" not in r and "batches" not in r, (
            "the default arm returned the FULL key-set")

    def test_default_arm_is_not_counted_against_the_full_bucket(self):
        """The budget already at capacity must NOT reject a default request.

        This is the #329 bypass itself: with the bucket seeded full, a
        default request that (auto-)selects a full pass 429s, and the tenant's
        whole-graph passes never consume the bucket it is measured against.
        A local pass is uncounted (I4), so it answers normally and the bucket
        is untouched.
        """
        import tortoise.hosted_api as ha
        from tortoise.quota import MAX_DREAM_FULL_PER_HOUR

        tid = "team-default-arm-budget"
        seeded = [_t.time() - 1.0] * MAX_DREAM_FULL_PER_HOUR
        ha._DREAM_FULL_BUCKETS[tid] = list(seeded)
        try:
            r = asyncio.run(ha.dream(org={"org_id": tid}))
            assert r["mode"] == "local", r
            assert len(ha._DREAM_FULL_BUCKETS[tid]) == MAX_DREAM_FULL_PER_HOUR, (
                "a local (default) pass appended to the #329 full bucket")
        finally:
            ha._DREAM_FULL_BUCKETS.pop(tid, None)

    def test_budget_counts_exactly_the_mode_that_runs(self):
        """Invariant behind the fix: the bucket grows IFF the pass is full.

        Table form so a future arm added to the handler is checked against the
        same rule rather than only its own mode. `mode` wins over the
        `full=True` sugar (I1 rule 1).
        """
        import tortoise.hosted_api as ha
        from tortoise.quota import MAX_DREAM_FULL_PER_HOUR

        cases = [
            ({"full": True}, "full"),
            ({"mode": "full"}, "full"),
            ({"mode": "full", "full": True}, "full"),
            ({"mode": "local"}, "local"),
            ({"mode": "stale-first", "budget": 2}, "stale-first"),
            ({}, "local"),
        ]
        for kwargs, expected_mode in cases:
            tid = "team-budget-table-" + expected_mode + "-" + str(len(kwargs))
            ha._DREAM_FULL_BUCKETS.pop(tid, None)
            try:
                r = asyncio.run(ha.dream(org={"org_id": tid}, **kwargs))
                assert r["mode"] == expected_mode, (kwargs, r["mode"])
                counted = tid in ha._DREAM_FULL_BUCKETS and bool(
                    ha._DREAM_FULL_BUCKETS[tid])
                assert counted == (expected_mode == "full"), (
                    f"{kwargs}: ran {expected_mode!r} but "
                    f"{'consumed' if counted else 'did not consume'} the "
                    f"#329 bucket (max {MAX_DREAM_FULL_PER_HOUR}/h)")
            finally:
                ha._DREAM_FULL_BUCKETS.pop(tid, None)


class TestSelfhostDefaultArm:
    def test_default_arm_is_local_and_drops_dirty_only(self, monkeypatch):
        """selfhost `/dream` mirrors hosted: explicit mode on every arm.

        Pinned on the CALL, not on a pass result: the defect is the kwargs the
        handler sends (an auto-selecting `dream(dirty_only=True)`), and a fake
        SDK shows them without depending on graph size — the very statistic
        that made the real bug intermittent.
        """
        import tortoise.selfhost_api as sha

        calls: list[dict] = []

        class _RecordingSDK:
            def dream(self, **kwargs):
                calls.append(kwargs)
                return {"mode": kwargs.get("mode"), "converged": True,
                        "iterations": 0}

        monkeypatch.setattr(sha, "_sdk", lambda: _RecordingSDK())

        out = asyncio.run(sha.dream())
        assert out["result"]["mode"] == "local", out
        assert calls == [{"mode": "local"}], (
            "the default arm must pass an explicit mode and must NOT pass the "
            f"deprecated dirty_only no-op — got {calls[-1]}")

        asyncio.run(sha.dream(full=True))
        assert calls[-1] == {"mode": "full"}, calls[-1]

        asyncio.run(sha.dream(mode="stale-first", budget=7))
        assert calls[-1] == {"mode": "stale-first", "budget": 7}, calls[-1]

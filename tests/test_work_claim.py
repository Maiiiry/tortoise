"""The claim CAS on the graph — and the fail-closed refusal that makes it a gate.

The headline property is NOT "acquire works". It is:

    a claim store that cannot answer must REFUSE the dispatch, never report it free.

Every other test here is a CAS property copied from what `claims.sh` proved on git refs
(atomic create, read-back, lease-as-data, fencing). The one that is deliberately DIFFERENT
is the refusal: the merge path is fail-open on purpose (agent-infra#1709, 2026-10-14),
while dispatch is fail-closed because proceeding blind at dispatch is what produced the
duplicate PRs #2247/#2250. `tools/work_claim.py` carries the `OVERRIDES:` line for it.

Store seams: a `FakeStore` exercises the CAS and refusal logic with no graph, and one
URI-guarded test drives the real SDK end-to-end so the shipped path is not merely assumed.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_MODULE_PATH = REPO_ROOT / "tools" / "work_claim.py"


def _load_module():
    """Load `tools/work_claim.py` by path — `tools/` is not a package, and the module must
    be importable exactly as the CLI runs it (same file, same import-time `register_kind`)."""
    spec = importlib.util.spec_from_file_location("_work_claim_under_test", _MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


wc = _load_module()

NOW = 1_800_000_000


class FakeStore:
    """In-memory stand-in for the graph. `create` NEVER overwrites an existing id and hands
    the caller back the EXISTING content — verified behaviour of `create_point` against a
    live FalkorDB (a second create with the same id returns the first point, content and
    all), and the reason the read-back is the authority rather than the write's exit code."""

    def __init__(self) -> None:
        self.points: dict[str, str] = {}
        self.writes = 0
        self.unanswerable = False
        self.lose_next_write = False  # simulate a peer writing between our write and read-back
        self.on_replace: str | None = None

    def _check(self) -> None:
        if self.unanswerable:
            raise wc.StoreUnanswerable("connection refused (simulated)")

    def read(self, cid: str) -> str | None:
        self._check()
        return self.points.get(cid)

    def create(self, cid: str, record) -> str:
        self._check()
        self.writes += 1
        if cid not in self.points:
            self.points[cid] = record.content()
        got = self.points[cid]
        if self.lose_next_write:
            self.lose_next_write = False
            # A peer overwrote between our create and the read-back.
            self.points[cid] = got
        return got

    def replace(self, cid: str, record) -> str:
        self._check()
        self.writes += 1
        if self.on_replace is not None:
            # A peer's reclaim lands first; ours is overwritten immediately.
            self.points[cid] = self.on_replace
            return self.points[cid]
        self.points[cid] = record.content()
        return self.points[cid]

    def replace_if_epoch(self, cid: str, record, *, expected_epoch: int) -> str | None:
        """The fenced reclaim: set ONLY if the stored epoch is still `expected_epoch`.
        `None` means the precondition failed — the caller lost the race."""
        self._check()
        self.writes += 1
        if self.on_replace is not None:
            self.points[cid] = self.on_replace
            return None
        current = self.points.get(cid)
        if current is None or wc._int_field(current, "epoch") != expected_epoch:
            return None
        self.points[cid] = record.content()
        return self.points[cid]

    def query_repo(self, repo: str) -> list[dict]:
        self._check()
        rows: list[dict] = []
        for content in self.points.values():
            rec = wc._parse(content)
            if rec.repo == repo:
                rows.append(
                    {
                        "key": rec.key,
                        "owner": rec.owner,
                        "lease_until": rec.lease_until,
                        "epoch": rec.epoch,
                        "session": rec.session,
                    }
                )
        return rows


def _record(owner: str = "box-a:111", key: str = "issue-5052", **kw):
    return wc.ClaimRecord(
        repo="daniel-ospina/tortoise",
        key=key,
        owner=owner,
        session=kw.pop("session", "cell-t1"),
        lease_until=kw.pop("lease_until", NOW + 3600),
        purpose=kw.pop("purpose", "pilot"),
        claimed_at=kw.pop("claimed_at", NOW),
        **kw,
    )


# ── the headline property: unanswerable is a REFUSAL, not a default ───────────────


def test_unset_store_is_refused_before_any_query():
    """An UNSET store URI must refuse. The SDK falls back to a default target when
    `TORTOISE_DB_URI` is unset, and an empty read from the wrong graph is indistinguishable
    from "nobody holds this" — #7871 gap 1 measured exactly that as the real read defect.
    The guard takes an explicit environment, so it is asserted directly rather than by
    mutating the session's os.environ."""
    with pytest.raises(wc.StoreUnanswerable) as excinfo:
        wc._require_store_configured({})
    msg = str(excinfo.value)
    assert "TORTOISE_DB_URI" in msg
    assert "refused" in msg.lower()


def test_api_url_alone_does_not_satisfy_the_store_guard():
    """Review of #9487 (P0), reproduced live: `TORTOISE_API_URL=… work_claim.py check` printed
    `ok: claim store reachable` and exited 0 while the SDK had silently resolved the BOX-LOCAL
    embedded database — the claim would be confirmed by its own read-back here and read FREE
    on every peer. The API URL is not a store this tool can reach, so it must not arm the
    guard; the message has to say WHY, or the next reader re-adds it as an obvious fix."""
    with pytest.raises(wc.StoreUnanswerable) as excinfo:
        wc._require_store_configured({"TORTOISE_API_URL": "https://api.premiselabs.co"})
    msg = str(excinfo.value)
    assert "TORTOISE_API_URL" in msg
    assert "embedded" in msg.lower()


def test_sdk_store_refuses_a_target_that_resolved_to_the_embedded_file():
    """Belt and braces: even if a caller reaches `SDKClaimStore` directly, an embedded target
    (`_db_uri is None`) is not a shared claim store and must refuse rather than write locally."""

    class EmbeddedSDK:
        _db_uri = None

        def query(self, **_kw):
            return []

    with pytest.raises(wc.StoreUnanswerable) as excinfo:
        wc.SDKClaimStore(sdk=EmbeddedSDK())
    assert "EMBEDDED" in str(excinfo.value).upper()


def test_main_refuses_with_rc2_when_the_store_is_unset(monkeypatch):
    """The CLI contract: rc 2, and the reason says which variable is missing. This is the
    value a controller branches on — rc 0 here would be the fail-open this tool removes."""
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    rc = wc.main(["acquire", "--repo", "o/r", "--issue", "5052"])
    assert rc == wc.EXIT_UNANSWERABLE == 2


def test_unanswerable_store_refuses_rather_than_proceeding():
    store = FakeStore()
    store.unanswerable = True
    out = wc.acquire(store, _record())
    assert out.rc == 2
    assert out.status == "REFUSED"
    assert "dispatch refused" in out.reason
    assert "simulated" in out.reason  # the reason names WHY, not just that it failed


def test_read_back_failure_after_a_write_refuses():
    """A write that "succeeded" but cannot be read back is UNKNOWN, not acquired."""

    class ReadBackDies(FakeStore):
        def __init__(self):
            super().__init__()
            self.reads = 0

        def read(self, cid):
            # `acquire` now reads BEFORE it creates, so the first read is the pre-check. Let
            # that one answer "free"; the SECOND read is the confirmation read-back, and that
            # is the one that must fail here.
            self.reads += 1
            if self.reads == 1:
                return None
            raise wc.StoreUnanswerable("read timed out")

    out = wc.acquire(ReadBackDies(), _record())
    assert out.rc == 2
    assert "read-back failed" in out.reason


def test_a_store_that_neither_creates_nor_returns_refuses():
    class Void(FakeStore):
        def create(self, cid, record):
            return ""

    out = wc.acquire(Void(), _record())
    assert out.rc == 2
    assert "neither created nor returned" in out.reason


# ── the CAS ────────────────────────────────────────────────────────────────────────


def test_first_acquire_wins_and_second_is_refused_naming_the_holder():
    store = FakeStore()
    first = wc.acquire(store, _record(owner="cell-1:100"), now=NOW)
    assert first.rc == 0 and first.status == "acquired"

    second = wc.acquire(store, _record(owner="cell-4:200"), now=NOW)
    assert second.rc == 1
    assert second.status == "held"
    assert second.owner == "cell-1:100"
    assert "cell-1:100" in second.reason
    # The loser's create attempt did not change the stored claim: the winner still holds it.
    stored = wc._parse(store.points[wc.claim_id("daniel-ospina/tortoise", "issue-5052")])
    assert stored.owner == "cell-1:100"


def test_claim_id_is_content_addressed_and_never_names_a_box():
    """Two boxes must compute the SAME id, or this is a ledger and not a lock — the property
    `claims.sh` spells out ("a KEY CONTAINING THE BOX ID IS NOT A CAS")."""
    a = wc.claim_id("daniel-ospina/tortoise", "issue-5052")
    b = wc.claim_id("daniel-ospina/tortoise", "issue-5052")
    c = wc.claim_id("daniel-ospina/tortoise", "issue-5053")
    d = wc.claim_id("daniel-ospina/agent-infra", "issue-5052")
    assert a == b
    assert a != c and a != d  # the repo is part of the identity, not just the number
    # Pinned to a LITERAL (review of #9487 P3): the old assertions compared two calls inside
    # one process, so folding the hostname into the digest still passed — every box would
    # compute a different id and the "same id on every box" property would be silently gone.
    assert a == "d0ae66a64d3a8688208f-20ed8abdbc54"
    # the SDK's ULID shape, so an explicit id does not warn on every write
    import re

    assert re.match(r"^[0-9a-f]+-[0-9a-f]{12}$", a)


def test_issue_and_pr_keys_are_disjoint():
    assert wc.normalise_key("issue", 5052) == "issue-5052"
    assert wc.normalise_key("pr", 5052) == "pr-5052"
    with pytest.raises(ValueError):
        wc.normalise_key("branch", 1)
    with pytest.raises(ValueError):
        wc.normalise_key("issue", 0)


def test_lost_race_reported_as_held_not_acquired():
    """If the store hands back a payload that is not ours, we lost — even when the write
    reported success. Trusting the write here is the whole defect class."""
    store = FakeStore()
    store.points[wc.claim_id("daniel-ospina/tortoise", "issue-5052")] = _record(
        owner="cell-4:999", lease_until=NOW + 60
    ).content()
    out = wc.acquire(store, _record(owner="cell-1:100"), now=NOW)
    assert out.rc == 1
    assert out.owner == "cell-4:999"


def test_expired_claim_is_reclaimed_and_the_epoch_fences():
    store = FakeStore()
    cid = wc.claim_id("daniel-ospina/tortoise", "issue-5052")
    store.points[cid] = _record(owner="cell-1:100", lease_until=NOW - 1).content()

    out = wc.acquire(store, _record(owner="cell-4:200"), now=NOW)
    assert out.rc == 0
    assert out.status == "acquired"
    assert out.epoch == 2  # fenced: the stale holder's epoch-1 record is superseded
    assert "reclaimed" in out.reason
    # the old holder can no longer release it
    released = wc.release(store, "daniel-ospina/tortoise", "issue-5052", owner="cell-1:100", now=NOW)
    assert released.rc == 1


def test_reclaim_through_a_store_without_a_fence_is_refused_not_blindly_written():
    """Review of #9487 (P1): the shipped `SDKClaimStore` cannot express "set only if the epoch
    is still E" — `update_point` is a blind write — so the interleaving A.replace → A.read →
    B.replace → B.read lets both boxes report `acquired`. A store without the fence must make
    `acquire` REFUSE (rc 2), never fall back to the blind write that reintroduces it."""

    class BlindStore(FakeStore):
        replace_if_epoch = None

    store = BlindStore()
    store.points[wc.claim_id("daniel-ospina/tortoise", "issue-5052")] = _record(
        owner="cell-1:100", lease_until=NOW - 1
    ).content()
    out = wc.acquire(store, _record(owner="cell-4:200"), now=NOW)
    assert out.rc == 2 and out.status == "REFUSED"
    assert "cannot express the reclaim precondition" in out.reason
    # and the expired holder's record is untouched — no half-reclaim
    assert wc._field(store.points[wc.claim_id("daniel-ospina/tortoise", "issue-5052")], "owner") == "cell-1:100"


def test_reclaim_is_lost_when_the_fence_observes_a_moved_epoch():
    """The CAS itself: a peer reclaimed between our read and our fenced write, so the write is
    declined and we are told we lost — naming the new holder, never retrying into it."""
    store = FakeStore()
    cid = wc.claim_id("daniel-ospina/tortoise", "issue-5052")
    store.points[cid] = _record(owner="cell-1:100", lease_until=NOW - 1).content()
    store.on_replace = _record(owner="cell-5:555", lease_until=NOW + 3600, epoch=2).content()
    out = wc.acquire(store, _record(owner="cell-4:200"), now=NOW)
    assert out.rc == 1 and out.status == "held"
    assert out.owner == "cell-5:555"
    assert "lost the reclaim race" in out.reason


# ── listing: an unread store is not an empty ledger ───────────────────────────────


def test_listing_refuses_when_the_store_cannot_list():
    """Review of #9487 (P1): `hasattr(store, "query_repo")` turned "cannot list" into "nothing
    to list" and answered rc 0 with `0 claim record(s)` over a store that held claims. A fleet
    reader would conclude the board was empty."""

    class NoListStore(FakeStore):
        query_repo = None

    out = wc.status(NoListStore(), "daniel-ospina/tortoise", None, now=NOW)
    assert out.rc == 2 and out.status == "REFUSED"
    assert "cannot list" in out.reason


def test_listing_returns_the_claims_the_store_holds():
    store = FakeStore()
    wc.acquire(store, _record(owner="cell-1:100"), now=NOW)
    wc.acquire(store, _record(owner="cell-1:100", key="issue-5053"), now=NOW)
    out = wc.status(store, "daniel-ospina/tortoise", None, now=NOW)
    assert out.rc == 0 and out.status == "listed"
    keys = sorted(row["key"] for row in out.extra["claims"])
    assert keys == ["issue-5052", "issue-5053"]


def test_cli_status_without_a_number_lists_and_acquire_without_one_is_a_usage_error():
    """Review of #9487 (P2): `status --repo` (no number) used to flow into `normalise_key(0)`
    and return rc 2 — which a controller reads as "the store is unanswerable" and retries.
    A malformed invocation is rc 3; a store that cannot answer stays rc 2."""
    store = FakeStore()
    wc.acquire(store, _record(owner="cell-1:100"), now=NOW)
    rc = wc.main(["status", "--repo", "daniel-ospina/tortoise"], store=store)
    assert rc == 0
    rc = wc.main(["acquire", "--repo", "daniel-ospina/tortoise"], store=store)
    assert rc == 3 == wc.EXIT_USAGE


# ── release identity, force, re-entrancy ───────────────────────────────────────────


def test_release_accepts_the_session_identity_the_acquire_used():
    """Review of #9487 (P1): acquire and release are DIFFERENT processes, so a `box:<pid>`
    default made every claim unreleasable — the reported holder was always another pid. A
    session id is the identity the caller already has and that survives a retry."""
    store = FakeStore()
    owner = wc._owner_token("cell-t1")
    wc.acquire(store, _record(owner=owner, session="cell-t1"), now=NOW)
    out = wc.release(
        store, "daniel-ospina/tortoise", "issue-5052", owner=wc._owner_token("cell-t1"), session="cell-t1", now=NOW + 5
    )
    assert out.rc == 0 and out.status == "released"


def test_forced_release_by_a_non_holder_records_the_reason_in_the_claim():
    """The operator route for a crashed holder: legal, but AUDITED — the reason and the
    releasing identity live in the tombstone, so the next reader sees an override rather than
    a claim that quietly changed hands. Without a reason the CLI refuses the invocation."""
    store = FakeStore()
    wc.acquire(store, _record(owner="cell-1:100"), now=NOW)
    out = wc.release(
        store,
        "daniel-ospina/tortoise",
        "issue-5052",
        owner="cell-9:999",
        force=True,
        reason="holder crashed, lease expired 2h ago",
        now=NOW + 5,
    )
    assert out.rc == 0, out.reason
    cid = wc.claim_id("daniel-ospina/tortoise", "issue-5052")
    stored = wc._parse(store.points[cid])
    assert "holder crashed, lease expired 2h ago" in stored.purpose
    assert "forced by cell-9:999" in stored.purpose
    assert stored.lease_until == NOW + 5

    rc = wc.main(
        ["release", "--repo", "daniel-ospina/tortoise", "--issue", "5052", "--force"],
        store=FakeStore(),
    )
    assert rc == wc.EXIT_USAGE


def test_reentrant_acquire_is_already_held_rather_than_held_by_a_peer():
    """Review of #9487 (P2): a retry by the SAME caller must not be told a peer holds the
    unit. `claims.sh` never expressed "already mine", so a resumed lane refused work it owned."""
    store = FakeStore()
    wc.acquire(store, _record(owner="cell-1:100"), now=NOW)
    again = wc.acquire(store, _record(owner="cell-1:100"), now=NOW + 10)
    assert again.rc == 0 and again.status == "already-held"
    assert again.epoch == 1
    assert again.lease_until == NOW + 3600


def test_quoted_value_without_a_space_roundtrips():
    """Review of #9487 (P3): the conditional quote left a value containing a `"` but no space
    unbalanced, and `_parse` then swallowed every field after it."""
    rec = _record(purpose='says "hi"')
    back = wc._parse(rec.content())
    assert back.purpose == 'says "hi"'
    assert back.owner == rec.owner and back.lease_until == rec.lease_until
    assert back.content() == rec.content()


# ── release + status ──────────────────────────────────────────────────────────────


def test_release_by_the_holder_expires_the_claim_and_keeps_the_history():
    store = FakeStore()
    wc.acquire(store, _record(owner="cell-1:100"), now=NOW)
    out = wc.release(store, "daniel-ospina/tortoise", "issue-5052", owner="cell-1:100", now=NOW + 5)
    assert out.rc == 0
    assert out.status == "released"

    cid = wc.claim_id("daniel-ospina/tortoise", "issue-5052")
    assert cid in store.points, "the record must survive the release — a deleted claim has no history"
    assert wc.status(FakeStoreWith(store.points), "daniel-ospina/tortoise", "issue-5052", now=NOW + 10).status == "expired"

    # ...and the next box can take it
    again = wc.acquire(store, _record(owner="cell-4:200"), now=NOW + 20)
    assert again.rc == 0


class FakeStoreWith(FakeStore):
    def __init__(self, points):
        super().__init__()
        self.points = points


def test_release_by_a_non_holder_is_refused_not_ignored():
    """Releasing someone else's claim is how a peer's lane gets duplicated, so it is a
    refusal with the real holder named — never a silent no-op."""
    store = FakeStore()
    wc.acquire(store, _record(owner="cell-1:100"), now=NOW)
    out = wc.release(store, "daniel-ospina/tortoise", "issue-5052", owner="cell-4:200", now=NOW)
    assert out.rc == 1
    assert out.owner == "cell-1:100"
    assert wc.status(store, "daniel-ospina/tortoise", "issue-5052", now=NOW).status == "held"


def test_status_free_is_a_real_answer_and_unanswerable_is_not():
    """The distinction the whole tool rests on: a reachable store with no claim is FREE
    (rc 0); a store that cannot answer is rc 2. They must never collapse into one."""
    free = wc.status(FakeStore(), "daniel-ospina/tortoise", "issue-5052", now=NOW)
    assert free.rc == 0 and free.status == "free"

    dead = FakeStore()
    dead.unanswerable = True
    unknown = wc.status(dead, "daniel-ospina/tortoise", "issue-5052", now=NOW)
    assert unknown.rc == 2 and unknown.status == "REFUSED"


# ── the content line is the identity ───────────────────────────────────────────────


def test_content_roundtrips_through_fields_including_quoted_values():
    rec = _record(purpose="claim registry pilot, phase one")
    reparsed = wc._parse(rec.content())
    assert reparsed.content() == rec.content()
    assert reparsed.purpose == "claim registry pilot, phase one"
    assert "\x00" not in rec.content()


def test_capacity_fields_are_persisted_with_the_claim_not_measured():
    """`capacity.sh` measures; this tool only stores what it was handed — so the fleet's
    capacity map and claim map are one record, not two probes."""
    store = FakeStore()
    rec = _record(host="cell-1", ncpu=8, lpc=372, lanes=3)
    out = wc.acquire(store, rec, now=NOW)
    assert out.rc == 0
    back = wc._parse(store.points[wc.claim_id("daniel-ospina/tortoise", "issue-5052")])
    assert (back.host, back.ncpu, back.lpc, back.lanes) == ("cell-1", 8, 372, 3)


def test_json_outcome_carries_the_decision_a_controller_logs():
    store = FakeStore()
    wc.acquire(store, _record(owner="cell-1:100"), now=NOW)
    out = wc.acquire(store, _record(owner="cell-4:200"), now=NOW)
    payload = json.loads(json.dumps(out.as_dict(), sort_keys=True))
    assert payload["status"] == "held"
    assert payload["owner"] == "cell-1:100"
    assert payload["lease_until"] == NOW + 3600


# ── the shipped store, end to end ─────────────────────────────────────────────────


@pytest.mark.skipif(
    not os.environ.get("TORTOISE_DB_URI"),
    reason="needs a live graph (TORTOISE_DB_URI) — the fake-store tests above cover the CAS",
)
def test_live_sdk_store_acquire_status_release():
    """The real path: `SDKClaimStore.create` → `get_point` read-back → `update_point`
    release, on whatever graph the lane is pointed at. Skips visibly (never green) with no
    URI, so an embedded/carve-out run cannot claim this was exercised."""
    store = wc.SDKClaimStore()
    key = "issue-999999"
    repo = "daniel-ospina/tortoise"
    cid = wc.claim_id(repo, key)
    record = wc.ClaimRecord(
        repo=repo,
        key=key,
        owner="pytest:1",
        session="pytest",
        lease_until=NOW + 3600,
        purpose="live store smoke",
        claimed_at=NOW,
    )
    try:
        acquired = wc.acquire(store, record)
        assert acquired.rc == 0, acquired.reason

        # the second box must be refused, naming the holder
        held = wc.acquire(
            store,
            wc.ClaimRecord(
                repo=repo, key=key, owner="pytest:2", lease_until=NOW + 3600, claimed_at=NOW
            ),
        )
        assert held.rc == 1, held.reason
        assert held.owner == "pytest:1"

        released = wc.release(store, repo, key, owner="pytest:1")
        assert released.rc == 0, released.reason
    finally:
        # The suite's own residue: best-effort, and never a test failure.
        with contextlib.suppress(Exception):
            store._sdk.delete_point(cid)

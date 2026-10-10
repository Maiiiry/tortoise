"""#3644 (§4.6 *Versioning*, the WINDOW half) — a `:Source` version has a window.

THE GAP THIS PINS
-----------------
`ONTOLOGY.md` §4.6 declares the Source temporal model: identity is `url`,
`contentHash` identifies a **version**, there is **one node per `url`**, and::

    The graph is a projection of the journal (§3), so the Source node carries
    one version — the current one — with its contentHash, its version ordinal,
    and the current version's valid-time window (validFrom/validTo). A version
    transition appends a journal record that closes the previous version's
    window and records the new one.

    Closing the interval is part of the write. An unclosed validTo reads as
    "still true" indefinitely — the failure mode the whole model exists to
    prevent — so the journal record that opens a new version closes the old one
    in the same act; it is never a later repair.

`#5024` landed the **version-anchor** half (a hash transition journals a
`SourceVersioned` record naming `previousContentHash`; `rebuild_all` reproduces
the node field for field) and deliberately left the **window** half open. Before
this change `_upsert_source` wrote no `validFrom`/`validTo`/`expiredAt` at all
(`docs/durability-posture.md` → R2 read *"declared, never written"*), so:

  * the node had no window start;
  * the transition record named the superseded *version* but closed no window —
    "closes the previous window" was an intention, not a recorded act.

WHAT IS IMPLEMENTED HERE
------------------------
  * `create_source` mints the window instant ONCE, on the same clock as
    `updatedAt` (`_version_instant`), so the live node and every record it
    journals cannot disagree;
  * `_upsert_source` writes the node's `validFrom` through the SAME hash-diff
    gate as `version`/`updatedAt` (create, stub completion, real transition) —
    never on a no-op re-check, never on a hash-less write;
  * the `SourceVersioned` record carries **both** window facts: `validFrom`
    (the new version's open) and `previousValidTo` (the prior version's close),
    equal by construction — §4.6's "closes the old one in the same act";
  * the fold is the live writer (`_fold_source_versioned` delegates to
    `_upsert_source`), so `derived = replay(journal)` holds by construction.

WHAT IS DELIBERATELY *NOT* DONE HERE (`Refs`, not `Closes`)
-----------------------------------------------------------
  * the node's `validTo` — absent while the current version is current, and an
    open end IS the model value (Cypher cannot write a NULL property). It is
    written by nothing because no `:Source` REMOVAL path exists yet; the
    removal-field decision is `#3590` S2's (`expiredAt`), so declaring or
    writing either now would open a surface with nothing behind it;
  * `Subject`/`Object` windows — the issue's other rows (§4.2/§4.3), still
    `❌` in §4.7's matrix; Object supersession additionally needs the successor
    window on the `ObjectSuperseded` payload, which is a separate design;
  * a caller-asserted valid-time input (a known publication date). The window
    start here is an OBSERVATION boundary, which is definitional for a version
    ("the content as read at an instant") and is what §4.6 requires to be able
    to close the prior window at all. A caller-asserted start needs the
    inversion guards the Point window needed (`#3980`, `#4021`, `#5358`,
    `#5360`); until it exists, no inversion is reachable and no guard is needed.

Run (embedded carve-out lane):
    TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \
        tests/test_source_version_window_3644.py -q
"""
from __future__ import annotations

import itertools
import json

import pytest

from tortoise.sdk import TortoiseSDK

_URL = "https://example.test/doc/version-window-3644"


def _props(sdk, url: str = _URL) -> dict:
    rows = sdk._get_proj().g.query(
        "MATCH (n:Source {url:$u}) RETURN properties(n)", params={"u": url}
    ).result_set
    assert rows, f"source {url!r} missing from the graph"
    return rows[0][0]


def _records(events_dir) -> list[dict]:
    out = []
    for path in sorted(events_dir.glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def _of_type(events_dir, type_) -> list[dict]:
    return [r for r in _records(events_dir) if r.get("type") == type_]


@pytest.fixture
def src(tmp_path):
    """(events_dir, sdk) with the journal wired."""
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "src3644.db"),
                      event_log_path=str(events / "events.jsonl"))
    yield events, sdk
    sdk.close()


# ── 1. the node's window is written, and it is the RECORDED instant ──────

def test_a_new_source_records_its_window_start(src):
    """A created `:Source` carries `validFrom` — the current version's open.

    FAILS BEFORE: `_upsert_source` wrote no `validFrom` on any path, so the
    node had no window start and §4.6's "the current version's valid-time
    window" had nothing behind it.
    """
    events, sdk = src
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0")

    p = _props(sdk, _URL)
    assert "validFrom" in p, "the created source carries no window start"
    # ABSENT is the open end — and it is the only spelling of it (Cypher
    # cannot write a NULL property).
    assert "validTo" not in p
    assert "expiredAt" not in p

    create = _of_type(events, "SourceCreated")[0]
    assert create["validFrom"] == p["validFrom"], (
        "the recorded window start and the node's must be the same instant — "
        "the record is what a replay will read")


def test_the_window_start_is_the_recorded_instant_not_the_replay_clock(src):
    """One clock: a rebuild reproduces the window, byte for byte.

    FAILS BEFORE (as a live != replay divergence) on any field written from
    the fold's own `_now_iso()` — the #5024 `updatedAt`/`ingestedAt` class.
    """
    events, sdk = src
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0")
    live = _props(sdk, _URL)["validFrom"]

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    rebuilt = _props(sdk, _URL)

    assert rebuilt["validFrom"] == live, (
        "the window start moved across a rebuild — it came from the replay "
        "clock rather than the record")
    assert rebuilt["validFrom"] == _of_type(
        events, "SourceCreated")[0]["validFrom"]


# ── 2. a transition opens the new window AND closes the prior one ────────

def test_a_transition_opens_the_new_window_and_closes_the_prior_one(src):
    """§4.6's "closes the previous version's window and records the new one".

    FAILS BEFORE: the `SourceVersioned` record named the superseded version but
    carried no window facts, and the node's `validFrom` never moved.
    """
    events, sdk = src
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0")
    sdk.create_source(_URL, "document", title="v1", contentHash="h-1")

    t = _of_type(events, "SourceVersioned")
    assert len(t) == 1
    t = t[0]
    p = _props(sdk, _URL)

    # The NEW window's open is the node's window start ...
    assert t["validFrom"] == p["validFrom"], (
        "the transition record's window start is not the node's")
    # ... and the PRIOR window's close is the same instant (contiguity by
    # construction — one minted clock, two names for the act).
    assert t["previousValidTo"] == t["validFrom"], (
        "the prior version's window close and the new version's open must be "
        "the same instant — a gap is covered by neither version and an "
        "overlap reads as two current versions")
    assert "validTo" not in p, (
        "the CURRENT version's window is open — the node must not be closed "
        "by a transition")


def test_a_noop_recheck_moves_neither_the_window_nor_the_journal(src):
    """Repeat-safety extends to the window (#5024's §9.6 cost bound).

    The window instant is minted on EVERY call, so the write gate (not the
    payload comparison) must be what protects the node: without the hash-diff
    gate a re-check would move `validFrom` live while journalling nothing and
    a rebuild would revert it — the exact `rawStateAt` divergence #5196 P2
    found.
    """
    events, sdk = src
    sdk.create_source(_URL, "document", title="doc", contentHash="h-0",
                      summary="s-0")
    before = _props(sdk, _URL)
    n_before = len(_records(events))

    for _ in range(5):
        sdk.create_source(_URL, "document", title="doc", contentHash="h-0",
                          summary="s-0")

    assert _props(sdk, _URL) == before, (
        "a no-op re-check moved the live node (the window start is the "
        "usual suspect: it is minted per call)")
    assert len(_records(events)) == n_before, (
        "a no-op re-check appended a journal record")


def test_a_same_hash_write_with_new_extras_does_not_move_the_window(src):
    """The gate is the VERSION, not the record cadence.

    A same-hash write that changes an extra IS journalled (it must be — the
    extras ride the payload into `_persist_extra_props`), so the window gate
    cannot be "did we journal"; it must be the stored-hash difference.
    """
    _events, sdk = src
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0",
                      summary="before")
    before = _props(sdk, _URL)["validFrom"]
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0",
                      summary="after")
    assert _props(sdk, _URL)["summary"] == "after"
    assert _props(sdk, _URL)["validFrom"] == before, (
        "a same-hash write moved the window — no new version was observed")


def test_a_hashless_write_does_not_move_the_window(src):
    """The preserve-everything arm must preserve the window too."""
    _events, sdk = src
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0")
    before = _props(sdk, _URL)["validFrom"]
    # No contentHash: the ON MATCH clause preserves hash/version/updatedAt —
    # and the window.
    sdk.create_source(_URL, "document", title="retitle")
    p = _props(sdk, _URL)
    assert p["contentHash"] == "h-0"
    assert p["validFrom"] == before, (
        "a hash-less write moved the window — it observed no new version")


# ── 3. the prior window is a JOURNAL fact, recoverable without the graph ──

def test_the_window_timeline_is_recoverable_from_the_journal_alone(src):
    """The whole point of §4.6's "a prior version's window is a journal fact".

    There is ONE node per `url`, so the only place the history can live is the
    record chain. Every window must be reconstructible from the journal with
    the graph closed — and the chain must be exactly contiguous.
    """
    events, sdk = src
    for i in range(4):
        sdk.create_source(_URL, "document", title=f"v{i}",
                          contentHash=f"h-{i}")
    live_from = _props(sdk, _URL)["validFrom"]
    sdk.close()  # the graph is not consulted below

    create = _of_type(events, "SourceCreated")[0]
    transitions = _of_type(events, "SourceVersioned")
    assert [t["contentHash"] for t in transitions] == ["h-1", "h-2", "h-3"]

    # Rebuild the windows: each version's window is [its open, the next open).
    opens = [create["validFrom"]] + [t["validFrom"] for t in transitions]
    windows = list(itertools.pairwise(opens))
    assert len(windows) == 3, f"expected 3 closed windows, got {windows}"
    for (prev, cur), t in zip(windows, transitions, strict=True):
        assert t["previousValidTo"] == cur, (
            "the record's prior-window close must BE the next version's open")
        assert prev < cur, (
            f"the window chain is not strictly increasing — {prev!r} -> "
            f"{cur!r}; a non-monotone chain leaves instants covered by two "
            f"versions or by none")
    assert live_from == opens[-1], (
        "the node's window start is not the last version's open")


def test_a_chain_of_transitions_stays_contiguous(src):
    """Pairwise contiguity across a longer chain, live and replayed."""
    events, sdk = src
    for i in range(6):
        sdk.create_source(_URL, "document", title=f"v{i}",
                          contentHash=f"h-{i}")
    transitions = _of_type(events, "SourceVersioned")
    # Each record closes the window it opens (one minted instant, two names) ...
    for t in transitions:
        assert t["previousValidTo"] == t["validFrom"], (
            "a transition closed a window at an instant other than the instant "
            "it opened its own — the windows gapped or overlapped")
    # ... and the version opens are strictly increasing, so no instant is
    # covered by two versions and none falls between two windows.
    opens = [t["validFrom"] for t in transitions]
    for prev, cur in itertools.pairwise(opens):
        assert prev < cur, f"the version opens are not monotone: {prev} -> {cur}"

    live = _props(sdk, _URL)["validFrom"]
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _props(sdk, _URL)["validFrom"] == live == transitions[-1]["validFrom"]


# ── 4. back-compat: an unrecorded window is an OPEN start, never a stamp ──

def test_a_legacy_record_without_a_window_replays_to_an_open_start(src,
                                                                  tmp_path):
    """The write wall clock is never borrowed as a valid-time start.

    Every journal written before this change, and every record from a writer
    that mints no window, must replay to a source with NO `validFrom` —
    `coalesce($vf, $now)` here would stamp the REPLAY clock on an absent
    window, i.e. invent a version start that no observation supports, and
    would disagree with the live graph the same journal was written from.
    """
    _events, sdk = src
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    lines = []
    for i in range(3):
        lines.append(json.dumps({
            "type": "SourceCreated", "url": _URL, "id": _URL,
            "contentHash": f"h-{i}", "sourceKind": "document",
            "title": f"v{i}",
            "ingestedAt": f"2026-01-0{i + 1}T00:00:00+00:00",
            "updatedAt": f"2026-02-0{i + 1}T00:00:00+00:00",
        }))
    (legacy / "legacy.jsonl").write_text("\n".join(lines) + "\n")

    sdk._get_proj().rebuild_all(str(legacy), confirm_destructive=True)
    p = _props(sdk, _URL)
    assert (p["contentHash"], p["version"]) == ("h-2", 3)
    assert "validFrom" not in p, (
        "the replay synthesized a window start for a record that carries "
        "none — an absent start is an OPEN start (§4.1/#3654)")


def test_a_legacy_hash_change_does_not_inherit_the_prior_window_start(src,
                                                                    tmp_path):
    """A transition whose record carries NO window leaves the start UNKNOWN.

    The gate sets `$vf` itself, never `coalesce($vf, s.validFrom)`: after a
    versioned create, a hash-changing legacy record (a foreign/pre-#3644
    journal line) must not leave the node claiming the CURRENT version started
    when the PREVIOUS one did. `SET … = null` clears the property, and absent
    is the model's own honest value for an unrecorded start (open/unbounded).
    """
    _events, sdk = src
    legacy = tmp_path / "mixed"
    legacy.mkdir()
    (legacy / "mixed.jsonl").write_text("\n".join([
        json.dumps({"type": "SourceCreated", "url": _URL, "id": _URL,
                    "contentHash": "h-0", "sourceKind": "document",
                    "title": "v0", "validFrom": "2026-01-01T00:00:00+00:00"}),
        # A hash change with no window fact at all.
        json.dumps({"type": "SourceVersioned", "url": _URL, "id": _URL,
                    "contentHash": "h-1", "previousContentHash": "h-0",
                    "sourceKind": "document", "title": "v1"}),
    ]) + "\n")

    sdk._get_proj().rebuild_all(str(legacy), confirm_destructive=True)
    p = _props(sdk, _URL)
    assert (p["contentHash"], p["version"]) == ("h-1", 2)
    assert "validFrom" not in p, (
        "the new version inherited the PRIOR version's window start — an "
        "unrecorded start must be absent (open), never a value from the "
        "version it replaced")


def test_a_stub_minted_source_has_an_open_start(src):
    """The ⚠️ half of the matrix: coverage is the `create_source` path.

    `_mint_source_stub` (the `_link_source` capture path) writes no window —
    there is no observation to record on a stub — so a stub-minted `:Source`
    reads as an open start. Pinned because the ontology's matrix must not claim
    more coverage than the writers deliver.
    """
    from tortoise.projection.edges import _mint_source_stub
    _events, sdk = src
    key = _mint_source_stub(sdk._get_proj().g, _URL)
    assert key == _URL
    p = _props(sdk, _URL)
    assert p["contentHash"] == ""
    assert "validFrom" not in p, (
        "the stub writer minted a window start it cannot know")


# ── 5. the record's window facts are journal-only ────────────────────────

def test_the_transition_payload_does_not_leak_onto_the_node(src):
    """`previousContentHash`/`previousValidTo` describe the TRANSITION.

    The fold delegates to the live writer (`_fold_source_versioned` →
    `_upsert_source`), so a payload key not in `_SOURCE_HANDLED` would be
    persisted through `_persist_extra_props` — the one-sided-property class
    (#330/#3312). Asserted live AND after a rebuild (the replay passes the
    record's own payload).
    """
    events, sdk = src
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0")
    sdk.create_source(_URL, "document", title="v1", contentHash="h-1")
    leaked = {"previousContentHash", "previousValidTo"}
    assert not (leaked & set(_props(sdk, _URL))), (
        f"journal-only keys leaked onto the node: "
        f"{sorted(leaked & set(_props(sdk, _URL)))}")
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert not (leaked & set(_props(sdk, _URL))), (
        "journal-only keys leaked onto the node through the replay")


def test_the_transition_record_still_carries_no_redundant_derivations(src):
    """#5024's polarity, re-asserted for the window keys.

    `previousValidTo` is NOT a second derivation: it is the SAME minted instant
    as `validFrom` (one clock, two names for §4.6's act). The keys #5024
    deliberately withheld (`at`, `version`) stay withheld.
    """
    events, sdk = src
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0")
    sdk.create_source(_URL, "document", title="v1", contentHash="h-1")
    t = _of_type(events, "SourceVersioned")[0]
    assert "at" not in t
    assert "version" not in t
    assert t["validFrom"] == t["updatedAt"] == t["previousValidTo"]


# ── 6. the window is not a caller-assertable property ────────────────────

def test_the_window_start_cannot_be_forged_through_the_entity_surface(src):
    """`validFrom` is minted by the writer and journalled — never asserted.

    Declaring it in `_SOURCE_NODE_PROPS` makes it READABLE (the read filter
    drops undeclared keys); listing it in `_SOURCE_SERVER_MANAGED_PROPS` is
    what stops the generic entity surface from writing a window that no
    transition produced — the trap #5196 round 3 found for `__runId`.
    """
    _events, sdk = src
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0")
    minted = _props(sdk, _URL)["validFrom"]
    with pytest.raises(ValueError, match="server-managed"):
        sdk.update_entity(_URL, validFrom="2020-01-01T00:00:00+00:00")
    assert _props(sdk, _URL)["validFrom"] == minted, (
        "the forged window start landed on the node")

"""#7936 — the name-stub id must be a pure function of the journal.

`_event_plain_merge` (`tortoise/projection/entities.py`) mints the `:Object` /
`:Subject` name stubs an `EventRecorded` implies with a **random** `ulid()`.
The graph fold is MERGE-by-name, so the *node* is stable — but its `id`
property is not: replaying ONE journal twice mints a different stub id each
time.

That is the measured blocker on this issue's remaining `(label, id)` re-key.
The re-key widens the reference fold to one `(label, id)` index deriving
`log_points` / `db_points` / `_compare_views` / `_graph_fingerprint`
(`tortoise/consistency.py`). `_graph_fingerprint` feeds `db_hash`, which is
compared against the STORED baseline (`graph_moved = stored["content_hash"] !=
db_hash`). A random stub id makes that digest a function of WHEN the graph was
built rather than of the JOURNAL — so every legitimate `rebuild_all` would read
`journal_moved=False, graph_moved=True` → a false `unrecorded-mutation` whose
`action` sends the operator hunting an unjournalled writer that does not
exist. The issue's own last comment records this measurement.

The fix is that the stub id is the SAME identity the canonical entity path
already mints — `tortoise.ids.entity_name_id(label, name)` (the helper
`tortoise/sdk.py` has carried since #452/#1315, here promoted to the neutral
`tortoise/ids.py` so the fold can read it without importing the SDK). It is
deterministic, so the entity view becomes a pure function of the journal; and
it EQUALS the id a generic `ObjectRegistered`/`SubjectAdded` for that name
carries, so the canonical registration is a no-op rather than a re-key.

#1155-P1 is preserved unchanged and is NOT about the randomness: the ruling is
that the stub MERGE writes `id` **ON CREATE only**, so a late stub cannot
clobber a canonical id (e.g. a connector's `github-issue-{repo}-{n}`). The
regression guards below pin both orderings.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tortoise.consistency import _graph_entities
from tortoise.sdk import TortoiseSDK, _entity_name_id

#: A journal whose ONLY record is the event — no `ObjectRegistered` /
#: `SubjectAdded` ever registers the names, so the stubs are the whole
#: non-Point node set. This is the shape the issue's last comment measures.
_STUB_EVENT = {
    "type": "EventRecorded",
    "id": "evt-7936-stub",
    "name": "stub matrix event",
    "eventKind": "test",
    "startedAt": "2026-01-01T00:00:00Z",
    "subject": "stub-subject",
    "object": "stub-object",
    "uses": [{"name": "stub-use", "kind": "tool"}],
}

#: The three stub mint sites in `_event_plain_merge`: the Subject of
#: `EventRecorded.subject`, the Object of `EventRecorded.object`, and one
#: Object per `EventRecorded.uses[]`.
_STUB_TARGETS = (
    ("Subject", "stub-subject"),
    ("Object", "stub-object"),
    ("Object", "stub-use"),
)


def _journal(tmp_path: Path, *records: dict) -> tuple[TortoiseSDK, Path]:
    """An SDK plus a hand-written journal dir holding exactly ``records``."""
    events = tmp_path / "events"
    events.mkdir(parents=True, exist_ok=True)
    log_path = events / "events.jsonl"
    log_path.write_text("".join(json.dumps(r) + "\n" for r in records))
    sdk = TortoiseSDK(str(tmp_path / "t.db"), event_log_path=str(log_path))
    return sdk, events


def _stub_ids(sdk: TortoiseSDK) -> dict:
    """``{(label, name): id}`` for every Object/Subject node in the graph."""
    rows = sdk._get_proj().g.query(
        "MATCH (n) WHERE n:Object OR n:Subject "
        "RETURN labels(n)[0], n.name, n.id"
    ).result_set or []
    return {(r[0], r[1]): r[2] for r in rows}


def _rebuild(sdk: TortoiseSDK, events: Path) -> None:
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)


@pytest.mark.parametrize("label,name", _STUB_TARGETS)
def test_a_name_stub_id_is_a_pure_function_of_the_journal(
        tmp_path: Path, label: str, name: str):
    """RED before the fix: replaying ONE journal re-mints a random stub id.

    Three consecutive `rebuild_all`s of the SAME journal must yield the SAME
    id for the same name. Randomness here is what makes a digest over the
    entity view unstable, which is the measured `(label, id)` re-key blocker.
    """
    sdk, events = _journal(tmp_path, _STUB_EVENT)
    try:
        seen: list = []
        for _ in range(3):
            _rebuild(sdk, events)
            ids = _stub_ids(sdk)
            assert (label, name) in ids, ids
            seen.append(ids[(label, name)])
        assert len(set(seen)) == 1, (
            f"the {label} stub id for {name!r} MOVED across replays of ONE "
            f"journal ({seen}): the mint is not a function of the journal, so "
            f"a digest over the entity view would report a false "
            f"`unrecorded-mutation` on a graph `rebuild_all` itself produced "
            f"(#7936)"
        )
    finally:
        sdk.close()


def test_the_graph_entity_view_is_a_pure_function_of_the_journal(
        tmp_path: Path):
    """RED before the fix: the exact index a widened digest would consume.

    `consistency._graph_entities` is what `_graph_fingerprint` would fold into
    the digest under the `(label, id)` re-key. It must be equal for two
    replays of the same journal — otherwise the baseline moves for a reason
    the journal does not contain.
    """
    sdk, events = _journal(tmp_path, _STUB_EVENT)
    try:
        views: list = []
        for _ in range(2):
            _rebuild(sdk, events)
            views.append(_graph_entities(sdk._get_proj())[0])
        assert views[0] == views[1], (
            "the graph's entity view is not a pure function of the journal: "
            f"{views[0]} != {views[1]} (#7936)"
        )
    finally:
        sdk.close()


@pytest.mark.parametrize("label,name", _STUB_TARGETS)
def test_a_stub_carries_the_canonical_entity_name_id(
        tmp_path: Path, label: str, name: str):
    """The stub id IS the id the canonical entity path mints for that name.

    Owning one identity is what makes the later generic registration a no-op
    (rather than a re-key), and it is the shape `_check_refs` already reserves
    for entity ids.
    """
    sdk, events = _journal(tmp_path, _STUB_EVENT)
    try:
        _rebuild(sdk, events)
        ids = _stub_ids(sdk)
        assert ids[(label, name)] == _entity_name_id(label, name), ids
    finally:
        sdk.close()


def test_a_later_canonical_id_still_wins_over_the_stub(tmp_path: Path):
    """#1155-P1, kept green: a foreign canonical id re-keys the stub node.

    Regression guard — this passes before and after the fix. The canonical id
    (a connector's scheme, not `entity_name_id`) must win when it arrives
    AFTER the stub, via `_upsert_object`'s ON MATCH
    ``o.id=coalesce($id, o.id)``.
    """
    canonical = "github-issue-test/repo-42"
    sdk, events = _journal(
        tmp_path, _STUB_EVENT,
        {"type": "ObjectRegistered", "id": canonical, "name": "stub-object"},
    )
    try:
        _rebuild(sdk, events)
        ids = _stub_ids(sdk)
        assert ids[("Object", "stub-object")] == canonical, ids
    finally:
        sdk.close()


def test_a_late_stub_does_not_clobber_a_canonical_id(tmp_path: Path):
    """#1155-P1, kept green: the stub MERGE writes `id` ON CREATE only.

    The canonical registration comes FIRST; the later `EventRecorded` matches
    the node by NAME and must NOT rewrite its id. This is the hazard the
    ON CREATE-only clause exists for — and it is orthogonal to whether the
    stub id is random (it is unchanged by this fix).
    """
    canonical = "github-issue-test/repo-42"
    sdk, events = _journal(
        tmp_path,
        {"type": "ObjectRegistered", "id": canonical, "name": "stub-object"},
        _STUB_EVENT,
    )
    try:
        _rebuild(sdk, events)
        ids = _stub_ids(sdk)
        assert ids[("Object", "stub-object")] == canonical, ids
    finally:
        sdk.close()


def test_a_late_subject_stub_does_not_clobber_a_foreign_id(tmp_path: Path):
    """#1155-P1 (Subject leg): the stub MERGE writes `id` ON CREATE only.

    The Subject companion to the Object guard above, and the guard that keeps
    the clause MEASURABLE now that the stub mints the canonical id: the
    pre-existing Subject carries a FOREIGN id (`subject:existing-agent`, not
    `entity_name_id`), so replacing the Subject stub's `ON CREATE SET` with an
    unconditional `SET` turns this red. Without it,
    `test_event_provenance.py::test_stub_preserves_existing_id_on_merge` —
    whose pre-existing id is now the SAME canonical id the stub would mint —
    could no longer fail on the regression it names.
    """
    foreign = "subject:existing-agent"
    sdk, events = _journal(
        tmp_path,
        {"type": "SubjectAdded", "id": foreign, "name": "stub-subject",
         "subject_kind": "person"},
        _STUB_EVENT,
    )
    try:
        _rebuild(sdk, events)
        ids = _stub_ids(sdk)
        assert ids[("Subject", "stub-subject")] == foreign, ids
    finally:
        sdk.close()

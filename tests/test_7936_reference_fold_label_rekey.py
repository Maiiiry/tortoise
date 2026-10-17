"""#7936 — the reference fold's GRAPH index is ``(label, id)``, not ``:Point``.

The reference fold (``consistency``) reads the graph to answer "does the
projection still equal ``replay(journal)``?". Its CONTENT leg was widened in
#8269/#7853 to fold and compare a belief on a ``:Subject``/``:Object``/
``:Event`` (``_fold_journal_entities`` + ``_compare_entities``). Its graph-side
index and its recorded BASELINE were not: ``db_hash`` was a digest over
``MATCH (n:Point)``, and the entity leg runs journal→graph only.

So the duplication this file pins is the remaining half of the issue's own
"Root (one sentence)" — *"``_apply_one``'s in-memory index is ``{id: point}`` —
it has no representation for a ``:Subject``/``:Object``/``:Event``"* — and it
is the **false GREEN** side of it: a non-Point node the journal cannot
reconstruct (a graph-only ``:Object``/``:Subject``/``:Event`` node, or a belief
or ``status`` write on one with no journal record) left ``db_hash`` unmoved and
was invisible to every leg, while the SAME mutation on a ``:Point`` was
``unrecorded-mutation``. Measured on ``origin/main``:

    baseline recorded on a graph `rebuild_all` itself produced
    graph-only `confidence` write on an :Object   -> ok=True, divergence=None
    graph-only :Event node                        -> ok=True, divergence=None
    the SAME write on a :Point                    -> unrecorded-mutation

The fix is the ``(label, id)`` re-key of the graph index — ``_graph_view`` is
ONE index over every label the reference fold models, and ``_graph_fingerprint``
folds ALL of it — which is why ``_PROJECTION_STATE_FORMAT`` is 3: the widened
digest moves ``content_hash`` for every existing baseline, and the previous
format is a KNOWN PREDECESSOR the next healthy recording run MIGRATES
(#8268/#8644) instead of stranding.

What is deliberately NOT re-keyed here (stated in the PR body, and on
``check_consistency``): ``log_points``/``db_points``/``_compare_views`` remain
the POINT leg. They are the Point SLICE of this same index, the entity content
question is decided by ``_compare_entities``, and changing a published verdict
field's meaning (the count of POINTS) is a separate decision.
"""
from __future__ import annotations

import json
from pathlib import Path

from tortoise.consistency import (
    _PROJECTION_STATE_FORMAT,
    _PROJECTION_STATE_FORMAT_HISTORY,
    check_consistency,
    projection_state_path,
)
from tortoise.projection import FalkorProjection

#: A journal with a Point, an Object registration, and an Event — but NO
#: ``ConfidenceChanged``. The Object's belief is therefore journal-silent, which
#: is the shape whose graph-only write used to be invisible (the entity leg is
#: presence-conditional and journal→graph).
_OBJECT_ID = "obj-7936-rekey"
_POINT_ID = "pt-7936-rekey"
_EVENT_ID = "evt-7936-rekey"
_JOURNAL = [
    {"type": "ObjectRegistered", "id": _OBJECT_ID, "name": "rekey-object"},
    {"type": "PointAdded",
     "point": {"id": _POINT_ID, "content": "rekey point",
               "pointKind": "statement", "status": "live"}},
    {"type": "EventRecorded", "id": _EVENT_ID, "name": "rekey event",
     "eventKind": "test", "startedAt": "2026-01-01T00:00:00Z",
     "subject": "rekey-subject", "object": "rekey-object"},
]


def _journal_dir(tmp_path: Path, records=None) -> Path:
    events = tmp_path / "events"
    events.mkdir(parents=True, exist_ok=True)
    (events / "events.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in (records or _JOURNAL)))
    return events


def _built(tmp_path: Path, tag: str = "g", records=None):
    """``(projection, log_path, events_dir)`` — a graph ``rebuild_all`` built."""
    events = _journal_dir(tmp_path, records)
    proj = FalkorProjection(path=str(tmp_path / f"{tag}.db"),
                            skip_health_check=True)
    proj.rebuild_all(str(events), confirm_destructive=True)
    return proj, str(events / "events.jsonl"), events


def _verdict(log_path: str, proj, *, record_state: bool) -> dict:
    return check_consistency(log_path, proj, record_state=record_state)


# ═══════════════════════════════════════════════════════════════════════
# The bug — a graph-only NON-POINT node is invisible without the re-key
# ═══════════════════════════════════════════════════════════════════════

def test_a_graph_only_non_point_node_is_an_unrecorded_mutation(tmp_path):
    """RED on ``origin/main``: ``ok=True, divergence=None``.

    A ``:Event`` node that exists in the graph and is materialized by NO journal
    record cannot be produced by ``replay(journal)`` — the #4240
    unjournalled-writer class. The ``:Point`` analogue is reported as
    ``unrecorded-mutation``; the non-Point one used to pass silently because the
    baseline digest was a ``MATCH (n:Point)`` view and the entity leg only walks
    journal→graph. FAILS IF: the graph-only node leaves ``db_hash`` unmoved.
    """
    proj, log_path, _events = _built(tmp_path)
    try:
        first = _verdict(log_path, proj, record_state=True)
        assert first["ok"] is True, first
        assert first["state_recorded"] is True, first

        proj.g.query(
            "CREATE (e:Event {eventId:'ghost-event-7936', "
            "id:'ghost-event-7936', name:'ghost', eventKind:'test', "
            "startedAt:'2026-01-01T00:00:00Z'})")

        second = _verdict(log_path, proj, record_state=False)
    finally:
        proj.close()

    assert second["db_hash"] != first["db_hash"], (
        "a graph-only :Event node did not move the baseline digest — the "
        "reference fold's graph index is Point-only again (#7936)")
    assert second["ok"] is False, second
    assert second["divergence"] == "unrecorded-mutation", second["divergence"]


def test_a_graph_only_non_point_belief_write_is_an_unrecorded_mutation(tmp_path):
    """RED on ``origin/main``: the journal is SILENT about this belief.

    The Object's belief is written straight to the graph with no
    ``ConfidenceChanged`` anywhere in the journal, so the (presence-conditional,
    journal→graph) entity leg has nothing to compare it against. On ``main``
    this measured ``ok=True, divergence=None``; the Point analogue was already
    ``unrecorded-mutation``. FAILS IF: the digest ignores a non-Point node's
    belief properties.
    """
    proj, log_path, _events = _built(tmp_path)
    try:
        first = _verdict(log_path, proj, record_state=True)
        assert first["ok"] is True, first

        proj.g.query(
            "MATCH (o:Object {id:$oid}) SET o.confidence=0.4, "
            "o.posterior_alpha=2.0, o.posterior_beta=1.0",
            params={"oid": _OBJECT_ID})

        second = _verdict(log_path, proj, record_state=False)
    finally:
        proj.close()

    assert second["db_hash"] != first["db_hash"], (
        "a graph-only belief write on an :Object did not move the baseline "
        "digest (#7936)")
    assert second["ok"] is False, second
    assert second["divergence"] == "unrecorded-mutation", second["divergence"]


def test_a_graph_only_non_point_status_write_is_an_unrecorded_mutation(tmp_path):
    """The same blind spot on ``status``, on the label the COMPARISON bounds out.

    ``status`` is a field the entity leg compares for Object/Subject ONLY
    (``entity_parity_bounds["status_compared"]``; ``Event`` carries
    ``eventStatus`` under a different name and has no such lifecycle fold), so
    on an ``:Event`` NEITHER half decided it before this change — the
    comparison skips a key the journal never stated for that node, and the
    Point-only digest could not move. Only the widened baseline can see it, and
    that asymmetry is stated in ``_entity_node_view``.
    FAILS IF: the digest ignores a non-Point node's ``status``.
    """
    proj, log_path, _events = _built(tmp_path)
    try:
        first = _verdict(log_path, proj, record_state=True)
        assert first["ok"] is True, first

        proj.g.query("MATCH (e:Event {eventId:$eid}) SET e.status='completed'",
                     params={"eid": _EVENT_ID})

        second = _verdict(log_path, proj, record_state=False)
    finally:
        proj.close()

    assert second["db_hash"] != first["db_hash"], (
        "a graph-only `status` write on an :Event did not move the baseline "
        "digest (#7936)")
    assert second["ok"] is False, second
    assert second["divergence"] == "unrecorded-mutation", second["divergence"]


# ═══════════════════════════════════════════════════════════════════════
# The index's LABEL SET — one home (the drift that would reopen the bug)
# ═══════════════════════════════════════════════════════════════════════

def test_the_graph_view_label_set_has_one_home():
    """The index's labels are DERIVED from the entity fold's declaration.

    A hand-copied label literal is a fail-open with no pin: a fourth creation
    row in ``_ENTITY_CREATION`` grows ``_REFERENCE_FOLD_ENTITY_LABELS`` through
    ``_ENTITY_CREATION_EVENT_LABEL``, the label-set pins all stay green, and
    ``_graph_view`` keeps indexing the old labels — so a graph-only node of the
    new label leaves ``db_hash`` unmoved, which is exactly the bug this file
    exists for, returned for a new label. ``_ENTITY_CREATION`` is the declared
    single home; this pins ``_graph_view``'s set to it.
    """
    from tortoise.consistency import _GRAPH_VIEW_LABELS
    from tortoise.projection import _REFERENCE_FOLD_ENTITY_LABELS

    assert set(_GRAPH_VIEW_LABELS) == {"Point"} | set(
        _REFERENCE_FOLD_ENTITY_LABELS), _GRAPH_VIEW_LABELS
    assert _GRAPH_VIEW_LABELS[0] == "Point"
    assert tuple(sorted(_GRAPH_VIEW_LABELS))
    # A duplicate would emit a redundant `n:X OR n:X` predicate and hide an
    # accidental subtraction from the tuple.
    assert len(set(_GRAPH_VIEW_LABELS)) == len(_GRAPH_VIEW_LABELS)


def test_a_graph_only_id_only_event_is_digest_covered(tmp_path):
    """An ``:Event`` carrying ``id`` but no ``eventId`` is still indexed.

    The entity WRITERS address an ``:Event`` by ``eventId``, but the BELIEF
    fold matches every epistemic label with ``MATCH (n:{label} {id:$id})``
    (``epistemic_label_queries``) — so an ``id``-only Event has a writable
    belief while an ``eventId``-only index key finds nothing. Keyed on
    ``eventId`` alone, the narrowing this test would guard is silent: the node
    is dropped from the digest and a graph-only belief write on it leaves
    ``db_hash`` unmoved. RED on a build whose ``_graph_view`` keys Event by
    ``eventId`` only.
    """
    proj, log_path, _events = _built(tmp_path)
    try:
        first = _verdict(log_path, proj, record_state=True)
        assert first["ok"] is True, first
        proj.g.query(
            "CREATE (e:Event {id:'id-only-event-7936', name:'id-only', "
            "eventKind:'test', startedAt:'2026-01-01T00:00:00Z'})")
        second = _verdict(log_path, proj, record_state=False)
        proj.g.query(
            "MATCH (e:Event {id:'id-only-event-7936'}) "
            "SET e.confidence=0.31")
        third = _verdict(log_path, proj, record_state=False)
    finally:
        proj.close()

    assert second["db_hash"] != first["db_hash"], (
        "a graph-only id-only :Event is not in the baseline index (#7936)")
    assert second["divergence"] == "unrecorded-mutation", second["divergence"]
    assert third["db_hash"] != second["db_hash"], (
        "a belief write on an id-only :Event did not move the baseline — the "
        "index keys Event by `eventId` alone, while the belief fold matches it "
        "by `id` (#7936)")


# ═══════════════════════════════════════════════════════════════════════
# The hazard the widening must NOT introduce — a false RED on a rebuild
# ═══════════════════════════════════════════════════════════════════════
def test_the_widened_digest_is_stable_across_a_rebuild(tmp_path):
    """A digest over the entity view must be a pure function of the journal.

    ``_event_plain_merge`` mints ``:Object``/``:Subject`` name stubs a journal
    record never registers explicitly. Their ids were a RANDOM ``ulid()`` until
    #8733 (now ``entity_name_id``), which is exactly why that landed first: a
    random id would make ``db_hash`` a function of WHEN the graph was built, so
    every legitimate rebuild before a still journal would read
    ``journal_moved=False, graph_moved=True`` → a false
    ``unrecorded-mutation``. This pins the whole consequence end to end.
    """
    proj, log_path, events = _built(tmp_path)
    try:
        first = _verdict(log_path, proj, record_state=True)
        assert first["ok"] is True, first
        proj.rebuild_all(str(events), confirm_destructive=True)
        after_rebuild = _verdict(log_path, proj, record_state=False)
    finally:
        proj.close()

    assert after_rebuild["db_hash"] == first["db_hash"], (
        "`rebuild_all` of ONE journal moved the baseline digest — the entity "
        "view is not a function of the journal (#7936)")
    assert after_rebuild["ok"] is True, after_rebuild
#: A journal where the Event lands FIRST, so `_event_plain_merge` really does
#: mint the `:Object` name stub (deterministic id, `_stub=true`) and the later
#: `ObjectRegistered` really does ADOPT it via `_upsert_object`'s `ON MATCH
#: o.id=coalesce($id, o.id)` / `o._stub=NULL`. In `_JOURNAL` the registration
#: precedes the Event, so no stub is ever minted for that name and adoption is
#: not what that fixture exercises.
_STUB_THEN_ADOPT_JOURNAL = [
    {"type": "EventRecorded", "id": _EVENT_ID, "name": "rekey event",
     "eventKind": "test", "startedAt": "2026-01-01T00:00:00Z",
     "subject": "rekey-subject", "object": "rekey-object"},
    {"type": "ObjectRegistered", "id": _OBJECT_ID, "name": "rekey-object"},
    {"type": "PointAdded",
     "point": {"id": _POINT_ID, "content": "rekey point",
               "pointKind": "statement", "status": "live"}},
]


def test_an_unjournalled_node_ADOPTED_by_the_rebuild_is_not_a_mutation(tmp_path):
    """The pre-wipe/adoption half: a stub the journal then ADOPTS is faithful.

    ``EventRecorded`` mints an ``:Object`` name stub first (a node NO record
    creates by its canonical id), then the ``ObjectRegistered`` adopts it
    (``ON MATCH o.id=coalesce($id, o.id)``, ``o._stub=NULL``). The node's
    presence and identity are therefore journal-derived, so replaying the same
    journal must reproduce the same digest. FAILS IF: the index keys a node by
    something the adoption legitimately changes — ``_stub`` (cleared by the
    adoption) is deliberately NOT covered, and
    ``test_the_fingerprint_ignores_the_stub_discriminator`` pins that exclusion
    directly.

    The verdict is NOT asserted ``ok``: this journal order hits a SEPARATE,
    PRE-EXISTING false ``content`` divergence on the adopted node's ``status``
    (``_upsert_object`` writes ``status`` ON CREATE only — the #1350 clobber
    guard — so adoption leaves it absent while the reference fold applies the
    ``live`` creation default). Measured identically on ``459f22912`` with the
    re-key absent; filed as #8872 rather than absorbed here.
    """
    events = _journal_dir(tmp_path, _STUB_THEN_ADOPT_JOURNAL)
    proj = FalkorProjection(path=str(tmp_path / "adopt.db"),
                            skip_health_check=True)
    log_path = str(events / "events.jsonl")
    try:
        proj.rebuild_all(str(events), confirm_destructive=True)
        rows = proj.g.query(
            "MATCH (o:Object {name:'rekey-object'}) RETURN o.id, o._stub"
        ).result_set
        # The stub WAS minted and WAS adopted: the canonical id landed and the
        # discriminator was cleared.
        assert rows and rows[0][0] == _OBJECT_ID, rows
        assert rows[0][1] is None, rows
        first = _verdict(log_path, proj, record_state=True)
        # The BASELINE must not be what complains. (The separate, pre-existing
        # `status` false divergence is asserted only as NOT this class.)
        assert first["divergence"] != "unrecorded-mutation", first
        proj.rebuild_all(str(events), confirm_destructive=True)
        second = _verdict(log_path, proj, record_state=False)
    finally:
        proj.close()
    assert first["db_hash"] == second["db_hash"], (
        "adoption of a name stub moved the baseline digest (#7936)")
    assert second["divergence"] != "unrecorded-mutation", second


def test_the_fingerprint_ignores_the_stub_discriminator(tmp_path):
    """``_stub`` is cleared by adoption, so it must not be in the digest.

    Pinned on the FINGERPRINT VIEW rather than through a rebuild: two builds of
    one journal clear the flag identically, so a rebuild comparison cannot
    discriminate whether ``_stub`` is covered — only the view can.
    FAILS IF: ``_entity_node_view`` starts copying an undeclared prop.
    """
    from tortoise.consistency import _graph_fingerprint, _graph_view

    proj, _log_path, _events = _built(tmp_path, records=_STUB_THEN_ADOPT_JOURNAL)
    try:
        view = _graph_view(proj)
        stub_keys = [k for k, p in view.items()
                     if k[0] in ("Object", "Subject") and p.get("_stub")]
        assert stub_keys, (
            "the fixture minted no stub — the exclusion pin would be vacuous")
        finger = _graph_fingerprint(view)
    finally:
        proj.close()
    for label, nid in stub_keys:
        assert "_stub" not in finger[label][nid], finger[label][nid]


def test_a_live_graph_and_its_rebuild_share_a_digest(tmp_path):
    """A LIVE-built graph and ``rebuild_all`` of its own journal must hash equal.

    This is the realistic shape the widening could break: the live writer and
    the replay writer are different code (`_upsert_*` vs the fold's ordered
    replay), and the entity view is now inside the digest. If they disagree on
    a node's identity or on a covered field, every rebuild would read
    ``journal_moved=False, graph_moved=True`` → a false
    ``unrecorded-mutation``.
    """
    from tortoise.sdk import TortoiseSDK

    events = tmp_path / "live-events"
    events.mkdir()
    log_path = str(events / "events.jsonl")
    sdk = TortoiseSDK(str(tmp_path / "live.db"), event_log_path=log_path)
    try:
        proj = sdk._get_proj()
        src = sdk.create_point("statement", "strong source", status="live")["id"]
        proj.g.query(
            "MATCH (n:Point {id:$id}) SET n.ep_alpha=12.0, n.ep_beta=1.0, "
            "n.baseline_set=true", params={"id": src})
        event = sdk.create_entity("event", "a live event",
                                  eventKind="test")["node"]["id"]
        sdk.create_operator("IMPL", src, [event], direction="bidirectional")
        sdk._get_ep().run([src, event], max_hops=2, evidence={src: (12.0, 1.0)})
        live = _verdict(log_path, proj, record_state=True)
        assert live["ok"] is True, live

        proj.rebuild_all(str(events), confirm_destructive=True)
        rebuilt = _verdict(log_path, proj, record_state=False)
    finally:
        sdk.close()

    assert rebuilt["db_hash"] == live["db_hash"], (
        "a live graph and `rebuild_all` of its own journal produced different "
        "baseline digests — the entity view is not replay-faithful (#7936)")
    assert rebuilt["ok"] is True, rebuilt


# ═══════════════════════════════════════════════════════════════════════
# The bounds this change deliberately keeps
# ═══════════════════════════════════════════════════════════════════════

def test_log_points_and_db_points_remain_POINT_counts(tmp_path):
    """The re-key is of the graph INDEX, not of the published count fields.

    ``log_points``/``db_points`` are read by the CLI and the status surfaces as
    the number of POINTS. Widening them to every reference-fold label would
    change a published verdict's meaning, which this change does not do — the
    graph-only non-Point node is caught by the BASELINE, not by this count.
    FAILS IF: the count starts including Object/Subject/Event.
    """
    proj, log_path, _events = _built(tmp_path)
    try:
        first = _verdict(log_path, proj, record_state=True)
        assert first["ok"] is True, first
        proj.g.query(
            "CREATE (e:Event {eventId:'ghost-count-7936', "
            "id:'ghost-count-7936', name:'ghost-count'})")
        verdict = _verdict(log_path, proj, record_state=False)
    finally:
        proj.close()

    assert verdict["log_points"] == 1, verdict["log_points"]
    assert verdict["db_points"] == 1, verdict["db_points"]
    assert verdict["delta"] == 0
    # …while the ghost is still caught, by the digest.
    assert verdict["divergence"] == "unrecorded-mutation", verdict["divergence"]


def test_the_reported_bounds_name_the_digest_coverage(tmp_path):
    """The verdict states what the baseline does and does not cover."""
    proj, log_path, _events = _built(tmp_path)
    try:
        bounds = _verdict(log_path, proj, record_state=False)["entity_parity_bounds"]
    finally:
        proj.close()

    assert "(label, id)" in bounds["baseline_digest_covers"]
    for label in ("Point", "Object", "Subject", "Event"):
        assert label in bounds["baseline_digest_covers"], bounds
    assert "name" in bounds["baseline_digest_does_not_cover"], bounds
    assert "POINT leg" in bounds["point_counts_cover"], bounds


# ═══════════════════════════════════════════════════════════════════════
# The format bump the widened digest requires
# ═══════════════════════════════════════════════════════════════════════

def test_the_digest_widening_carries_a_format_bump():
    """A digest widening MUST bump the format, or every baseline misreads.

    ``content_hash`` moves for every pre-existing baseline when the digest's
    coverage widens; without a bump, a v2 sidecar over a healthy graph reads
    ``journal_moved=False, graph_moved=True`` → ``unrecorded-mutation`` whose
    ``action`` tells the operator to hunt an unjournalled writer that does not
    exist (#2897's lesson, #8268/#7936). Asserted as a RELATIONSHIP (the
    re-key's format is in the history and is newer than 2) so a future bump
    cannot silently drop it.
    """
    assert 3 in _PROJECTION_STATE_FORMAT_HISTORY, _PROJECTION_STATE_FORMAT_HISTORY
    assert _PROJECTION_STATE_FORMAT >= 3
    assert tuple(sorted(_PROJECTION_STATE_FORMAT_HISTORY)) == (
        _PROJECTION_STATE_FORMAT_HISTORY)


def test_a_predecessor_format_baseline_is_migrated_not_a_mutation(tmp_path):
    """The upgrade path: a v(N-1) sidecar is MIGRATED, never a false RED.

    Derived from the current constant (not hard-coded) so it tracks a future
    bump: whichever format was current before this change is a KNOWN
    PREDECESSOR, and a still-journal run must re-baseline it rather than report
    ``unrecorded-mutation`` on a graph nothing moved.
    """
    proj, log_path, _events = _built(tmp_path)
    predecessor = _PROJECTION_STATE_FORMAT - 1
    try:
        with open(projection_state_path(log_path), "w", encoding="utf-8") as fh:
            json.dump({"format_version": predecessor, "last_applied_seq": 3,
                       "projection_hash_sha256": "0" * 64}, fh)
        verdict = _verdict(log_path, proj, record_state=True)
    finally:
        proj.close()

    assert verdict["divergence"] != "unrecorded-mutation", verdict
    assert verdict["ok"] is True, verdict
    assert verdict["state_superseded_format"] == predecessor, verdict
    assert verdict["state_recorded"] is True, (
        "a known-predecessor baseline was NOT re-baselined — the baseline half "
        "stays inert for ever (#8268/#7936)")
    stored = json.loads(Path(projection_state_path(log_path)).read_text())
    assert stored["format_version"] == _PROJECTION_STATE_FORMAT, stored

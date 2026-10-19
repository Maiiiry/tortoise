"""#8166 — an undeclared `status` written on a `:Object` must be REFUSED at the
door, the Object twin of the #1423/#8087 Subject guard.

**The defect, re-measured on this lane's base (58d28af0d, embedded engine).**
An Object `status` had no write-side vocabulary at all:

    sdk.create_entity("object", "Obj Dead", objectKind="widget",
                      status="dead")          # stored 'dead', journaled
    sdk.update_entity(<object_id>, status="dead")   # stored 'dead'
    run_structural_query(g, "widget", entity_type="object")
    # -> BOTH returned. 'dead' reads as CURRENT on the default view.

The four retrieval legs exclude ``commit_ops.OBJECT_TERMINAL_STATUSES`` — a
DENYLIST of KNOWN terminal states — so a status the vocabulary has never heard
of is served as current BY CONSTRUCTION. No read-side rule can hide a value the
vocabulary does not know, which is why #8087 closed the Subject hole on WRITE
and why this file pins the same shape for the Object family.

**The vocabulary is the canonical set ∪ the kind's pack-declared statuses.**
``commit_ops.declared_object_statuses(kind)``: ``OBJECT_STATUS_VALUES``
(live / in_progress / completed / the four terminal members the read surfaces
already exclude) plus ``kindDefs[<kind>].statuses`` for the Object's own kind
(#2729 — a pack-declared kind OWNS its Object state, so a closed static set
would refuse the shipped venture pack's own lifecycle). It is PER-KIND on
purpose: a flat union across installed packs would accept ``status="met"`` on a
goal Object merely because the venture pack declares ``met`` for its condition
kind — re-opening the #7856 defect where a GOAL's achievement state was
indistinguishable from its lifecycle status.

**What this file does NOT claim.** Indicator 3 of the issue ("a raw-Cypher
`:Object {status:'dead'}` is excluded from the four legs") is NOT implemented,
and deliberately so — see
``test_a_raw_producer_status_still_reads_as_current_the_net_doctrine``: the
Object read vocabulary is a NET (`OBJECT_TERMINAL_STATUSES` = "which STORED
states are dead"), and an Object carrying a status it does not know reads as
CURRENT, which is a PINNED behaviour of this repo (``tests/test_capture_session.py``
::test_apply_supersessions_outdated_carrier_folds, plus
``assembly._RECALL_OBJECT_EXCLUDED_STATUSES``' load-bearing ``outdated``
member). A fail-closed read would contradict that recorded decision; the write
door is what closes the class.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import commit_ops
from tortoise.exceptions import BundleValidationError
from tortoise.sdk import TortoiseSDK

#: Hard-coded expectation (an expectation imported from the code under test is
#: not an expectation). Every member is one this repo already declares: the
#: mint default, the two values the built-in connector fold writes
#: (`status_folds.BUILTIN_CONNECTOR_FOLDS`) and the four terminal members the
#: read surfaces exclude (`OBJECT_TERMINAL_STATUSES`).
CANONICAL_OBJECT_STATUSES = frozenset({
    "live", "in_progress", "completed",
    "superseded", "deprecated", "archived", "retracted",
})

#: Values NO declared source knows. `outdated` and `draft` are here on purpose:
#: both belong to the POINT vocabulary (a legacy dead marker / a staged claim),
#: not to any Object lifecycle, so the Object door refuses them.
UNDECLARED = ("dead", "outdated", "draft", "nonsense", "met")


@pytest.fixture()
def sdk(tmp_path):
    s = TortoiseSDK(str(tmp_path / "obj_status_8166.db"))
    yield s
    s.close()


def _object_row(sdk, name):
    return sdk._get_proj().g.query(
        "MATCH (o:Object {name:$n}) RETURN o.id, o.status",
        params={"n": name}).result_set


def _status(sdk, oid):
    rows = sdk._get_proj().g.query(
        "MATCH (n {id:$i}) RETURN n.status", params={"i": oid}).result_set
    return rows[0][0] if rows else None


# ── 1. the DOOR: an undeclared Object status is refused before any write ───


class TestObjectStatusWriteIsValidated:
    """MUTATION: reverting either guard reds every case here — the write
    succeeds where a ``ValueError`` is required."""

    @pytest.mark.parametrize("bad", UNDECLARED)
    def test_update_entity_refuses_an_undeclared_object_status(
            self, sdk, bad):
        sdk.create_entity("object", "Obj Live", objectKind="widget")
        oid = _object_row(sdk, "Obj Live")[0][0]
        with pytest.raises(ValueError, match="Invalid status"):
            sdk.update_entity(oid, status=bad)
        # refused BEFORE the write: the stored value is untouched.
        assert _status(sdk, oid) == "live", _status(sdk, oid)

    @pytest.mark.parametrize("bad", UNDECLARED)
    def test_create_entity_refuses_an_undeclared_object_status(
            self, sdk, bad):
        """The SECOND door: ``**props`` overrides the ``'live'`` literal, so a
        guard on ``_update_entity`` alone leaves this writable — and unlike a
        no-op re-mention, this one is JOURNALED (``ObjectRegistered`` carries
        the state), so bad state also became DURABLE through replay."""
        with pytest.raises(ValueError, match="Invalid status"):
            sdk.create_entity("object", "Obj New", objectKind="widget",
                              status=bad)
        assert _object_row(sdk, "Obj New") == [], "the node was minted anyway"

    def test_a_non_string_status_is_refused_too(self, sdk):
        """An array on an Object is stored verbatim by FalkorDB and then makes
        ``create_direct_edge``'s frozenset membership test raise mid-write
        (#7925). The Object door now refuses it before it is stored."""
        with pytest.raises(ValueError, match="Invalid status"):
            sdk.create_entity("object", "Obj Arr", objectKind="widget",
                              status=["retracted"])

    def test_the_declared_object_lifecycle_still_writes(self, sdk):
        """Anti-vacuity: the guard refuses only UNDECLARED values."""
        sdk.create_entity("object", "Obj Life", objectKind="widget")
        oid = _object_row(sdk, "Obj Life")[0][0]
        for st in sorted(CANONICAL_OBJECT_STATUSES):
            sdk.update_entity(oid, status=st)
            assert _status(sdk, oid) == st, (st, _status(sdk, oid))

    def test_the_declared_lifecycle_is_writable_at_create(self, sdk):
        for st in ("archived", "in_progress", "completed", "retracted"):
            sdk.create_entity("object", f"Obj {st}", objectKind="widget",
                              status=st)
            assert _object_row(sdk, f"Obj {st}")[0][1] == st

    def test_an_explicit_none_is_accepted(self, sdk):
        """NULL is the legacy/LIVE encoding of the property (§4.3: the property
        is a cache; the ``:Object`` writer seeds ``coalesce($st,'live')``), and
        ``create_entity("object", ..., status=None)`` is a working call —
        refusing it would be an unrelated breaking change. (A Subject refuses
        ``None`` because its closed §4.2 lifecycle is seeded by the writer; the
        Object family has no such seed contract.) """
        node = sdk.create_entity("object", "Obj None", objectKind="widget",
                                 status=None)
        assert node
        # the writer's own coalesce: an explicit None lands as 'live'.
        assert _object_row(sdk, "Obj None")[0][1] == "live"


# ── 2. the VOCABULARY: canonical ∪ the kind's pack-declared statuses ───────


class TestDeclaredVocabularyIsPerKind:
    def test_the_canonical_set_is_the_pinned_seven(self):
        assert commit_ops.OBJECT_STATUS_VALUES == CANONICAL_OBJECT_STATUSES
        # derived, not a second literal: the terminal half IS the read net.
        assert commit_ops.OBJECT_TERMINAL_STATUSES <= (
            commit_ops.OBJECT_STATUS_VALUES)
        # the Point family's words are NOT Object words.
        assert "outdated" not in commit_ops.OBJECT_STATUS_VALUES
        assert "draft" not in commit_ops.OBJECT_STATUS_VALUES

    def test_declared_statuses_is_canonical_for_an_undeclared_kind(self):
        assert commit_ops.declared_object_statuses("widget") == (
            CANONICAL_OBJECT_STATUSES)
        assert commit_ops.declared_object_statuses(None) == (
            CANONICAL_OBJECT_STATUSES)

    def test_a_pack_kind_gets_its_own_declared_statuses_only(self):
        """#2729: a kind that declares a lifecycle OWNS its Object state. The
        shipped venture pack's ``tranche`` statuses are the CASE that makes a
        static closed set untenable — ``sdk.create_object(..., objectKind=
        "venture:tranche", status="released")`` is a supported call
        (``tests/test_venture_pack.py``)."""
        tranche = commit_ops.declared_object_statuses("venture:tranche")
        assert {"pending", "released", "denied"} <= tranche
        assert CANONICAL_OBJECT_STATUSES <= tranche
        # both spellings resolve — the extraction commit strips the namespace
        assert commit_ops.declared_object_statuses("tranche") == tranche
        # …and the words stay SCOPED to that kind.
        assert "released" not in commit_ops.declared_object_statuses("widget")

    def test_a_pack_status_writes_on_a_pack_kind(self, sdk):
        sdk.create_object("Tranche T2", objectKind="venture:tranche",
                          status="released")
        assert _object_row(sdk, "Tranche T2")[0][1] == "released"
        oid = _object_row(sdk, "Tranche T2")[0][0]
        sdk.update_entity(oid, status="conditions-met")
        assert _status(sdk, oid) == "conditions-met"

    def test_a_pack_status_is_refused_on_another_kind(self, sdk):
        """THE per-kind point (#7856): ``met`` is declared by the venture pack
        for its condition/tranche kinds — a FLAT union would therefore accept
        ``update_entity(<goal object>, status="met")``, the exact defect that
        made a goal's achievement state indistinguishable from its lifecycle
        status. Scoped per kind, it is refused."""
        sdk.create_object("Goal G", objectKind="core:goal")
        oid = _object_row(sdk, "Goal G")[0][0]
        with pytest.raises(ValueError, match="Invalid status"):
            sdk.update_entity(oid, status="met")
        assert _status(sdk, oid) == "live"
        # the separate axis remains the way to say it (#7856).
        sdk.update_entity(oid, goalState="met")

    def test_the_update_door_uses_the_stored_kind(self, sdk):
        """A pack Object written by another producer (no ``objectKind`` in the
        update props) is still judged by the kind it STORES."""
        sdk.create_object("Cond C1", objectKind="venture:condition",
                          status="unmet")
        oid = _object_row(sdk, "Cond C1")[0][0]
        sdk.update_entity(oid, status="met")
        assert _status(sdk, oid) == "met"


# ── 3. the INGEST door: Phase 1 must refuse what the writer refuses ────────


class TestIngestMirrorsTheWriter:
    """#7925: a bundle whose writer raises in step 3 leaves the earlier items
    committed. The Object vocabulary refusal is therefore a Phase-1 violation,
    refused bundle-wide once — the same authority the ``:Subject`` loop has."""

    def test_an_undeclared_object_status_is_a_phase1_violation(self, sdk):
        bundle = {
            "points": [{"kind": "statement", "content": "must-not-commit"}],
            "entities": [{"type": "object", "name": "Obj X", "status": "dead"}],
        }
        msgs = [v["message"] for v in sdk._validate_bundle(bundle)]
        assert any("declares status 'dead' for an :Object" in m for m in msgs), \
            msgs
        with pytest.raises(BundleValidationError):
            sdk.ingest(bundle)
        # no partial mutation: the Point item never committed.
        assert sdk._get_proj().g.query(
            "MATCH (n:Point {content:'must-not-commit'}) RETURN count(n)"
        ).result_set == [[0]]

    def test_the_nested_props_spelling_is_caught_at_the_door_depth(self, sdk):
        bundle = {"entities": [{"type": "object", "name": "Obj N",
                               "props": {"status": "nonsense"}}]}
        msgs = [v["message"] for v in sdk._validate_bundle(bundle)]
        assert any("declares status 'nonsense' for an :Object" in m
                   for m in msgs), msgs

    def test_a_declared_object_status_still_ingests(self, sdk):
        bundle = {"entities": [{"type": "object", "name": "Obj Y",
                               "status": "archived"}]}
        assert sdk._validate_bundle(bundle) == []
        sdk.ingest(bundle)
        assert _object_row(sdk, "Obj Y")[0][1] == "archived"

    def test_a_pack_declared_status_still_ingests(self, sdk):
        bundle = {"entities": [{"type": "object", "name": "Tranche Q",
                               "objectKind": "venture:tranche",
                               "status": "released"}]}
        assert sdk._validate_bundle(bundle) == []
        sdk.ingest(bundle)
        assert _object_row(sdk, "Tranche Q")[0][1] == "released"

    def test_an_explicit_null_status_still_ingests(self, sdk):
        bundle = {"entities": [{"type": "object", "name": "Obj Null",
                               "status": None}]}
        assert sdk._validate_bundle(bundle) == []
        sdk.ingest(bundle)
        # the writer's coalesce: an explicit None lands as 'live'.
        assert _object_row(sdk, "Obj Null")[0][1] == "live"


# ── 4. durability: the declared write still round-trips through replay ─────


class TestDeclaredStatusIsDurable:
    """Checklist row 4: an Object status written through the generic surface
    still round-trips byte-identically through ``rebuild_all`` — the guard
    changed which VALUES are accepted, not the journaling mechanism."""

    def test_an_object_restatus_replays(self, tmp_path):
        events = tmp_path / "events"
        events.mkdir()
        log = str(events / "events.jsonl")
        sdk = TortoiseSDK(str(tmp_path / "replay.db"), event_log_path=log)
        try:
            sdk.create_entity("object", "Obj R", objectKind="widget")
            oid = _object_row(sdk, "Obj R")[0][0]
            sdk.update_entity(oid, status="archived")
            assert _status(sdk, oid) == "archived"
            sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
            assert _status(sdk, oid) == "archived", "live ≠ replay"
        finally:
            sdk.close()


# ── 5. the families that must NOT be touched by an Object-scoped guard ─────


class TestOtherFamiliesUnaffected:
    def test_a_point_object_node_stays_point_governed(self, sdk):
        """#3311 Point-label precedence: ``draft``/``outdated`` are POINT words,
        absent from the Object vocabulary, and a ``:Point:Object`` node follows
        its Point contract (the Point arm validates and drops the key first)."""
        g = sdk._get_proj().g
        g.query("CREATE (:Point:Object {id:'ml1', name:'ML1', "
                "status:'live'})")
        sdk.update_entity("ml1", status="draft")
        assert _status(sdk, "ml1") == "draft"
        sdk.update_entity("ml1", status="outdated")
        assert _status(sdk, "ml1") == "outdated"

    def test_a_url_keyed_source_status_is_still_writable(self, tmp_path):
        """The guard is ``:Object``-scoped, NOT "anything that is not a Point".
        A ``:Source`` status travels the same generic surface (#4649) and its
        vocabulary (``retired``) is neither a Point nor an Object word."""
        sdk = TortoiseSDK(str(tmp_path / "src.db"))
        try:
            url = "https://example.com/report"
            sdk.create_point("statement", "the claim", extractedFrom=url)
            returned = sdk.update_entity(url, status="retired")
            assert returned.get("status") == "retired", returned
        finally:
            sdk.close()


# ── 6. what indicator 3 asks for, and why it is NOT implemented ────────────


def test_a_raw_producer_status_still_reads_as_current_the_net_doctrine(sdk):
    """⚠️ THE RECORDED DECISION THIS LANE DID NOT OVERRIDE.

    Issue #8166's indicator 3 asks the four retrieval legs to EXCLUDE a
    raw-Cypher ``:Object {status:'dead'}``. That is a fail-closed READ: an
    Object whose status the vocabulary does not know would stop being served.

    It contradicts a decision this repo already recorded and pinned:

      * ``commit_ops.OBJECT_TERMINAL_STATUSES`` is documented as the read
        vocabulary — "which STORED states are dead", NOT "which states a writer
        may mint" (#2977/#1423), i.e. a NET that keeps a state a legacy/pre-fix
        SDK or a raw producer already stored;
      * ``projection/entities.py::_fold_object_status`` folds a connector event
        on an Object in ANY status that is not the canonical terminal set;
      * ``assembly._RECALL_OBJECT_EXCLUDED_STATUSES`` carries an ``outdated``
        member that is LOAD-BEARING precisely because an Object may hold a
        status the Object vocabulary does not know;
      * ``tests/test_capture_session.py::
        test_apply_supersessions_outdated_carrier_folds`` asserts an
        ``outdated``-status Object IS visible to the default view.

    So the DOOR closes (this file's other tests) and the NET stays a net. A
    fail-closed read is a separate decision for its own home, not something to
    adopt quietly inside a write-guard fix.
    """
    from tortoise.search_engine import run_structural_query

    sdk.create_entity("object", "Obj Raw", objectKind="widget")
    oid = _object_row(sdk, "Obj Raw")[0][0]
    # a raw producer (no write door) says the object is dead.
    sdk._get_proj().g.query(
        "MATCH (o:Object {id:$i}) SET o.status='dead'", params={"i": oid})
    rows = run_structural_query(sdk._get_proj().g, "widget",
                                entity_type="object", limit=50)
    assert oid in {str(r[0]) for r in rows}, (
        "the Object read vocabulary is a NET: an unknown stored status is "
        "served as CURRENT (see this test's docstring — indicator 3 is a "
        "separate, contradicting decision)")
    # …and the new write door still refuses to MINT such a state.
    with pytest.raises(ValueError, match="Invalid status"):
        sdk.update_entity(oid, status="dead")

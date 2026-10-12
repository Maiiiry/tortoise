"""The record projection's contract, made executable (#7969, epic #7879).

WHAT THIS PINS. Interface A — *"journal → Postgres, replayable and idempotent"* —
is four claims, and each one is a test here rather than a sentence in a design
doc:

1. **TOTAL.** Every record type the journal can carry has a declared mutation,
   and a type with no handler is a **failure, not a skip** (`#5285`'s rule
   applied to the record's destination). Two halves: the registry covers the
   vocabulary the code can emit (checked against the graph fold's own dispatch
   and the journal's own type list — a NEW producer reds this suite), and an
   undeclared type raises.
2. **IDEMPOTENT.** Structural, not sampled: every node mutation is an
   `INSERT … ON CONFLICT … DO UPDATE`, every patch writes absolute values, every
   delete is a `DELETE`, and the journal position is `ON CONFLICT DO NOTHING`.
   A second replay therefore writes the same values onto the same rows. The
   end-to-end half (replay twice against a real Postgres) runs in the PGlite
   harness — `supabase/tests/pglite/validate.mjs` — because this suite is
   deliberately DB-free so it runs in every Python lane.
3. **A FIELD IS PRIMARY IFF THE JOURNAL CARRIES IT.** The property→column
   decision is read from `#7968`'s schema **on disk**, never restated here, and
   §17.5's placement table is the expected set: every PRIMARY property is
   storable, no DERIVED/VOLATILE property is ever stored as authority.
4. Exact-SQL evidence for the shapes above (the emitted statements are asserted,
   not merely executed).

WHY THE PLACEMENT TABLE IS IMPORTED, NOT RE-DECLARED. `#7968`'s
`tests/test_record_schema_placement.py` already parses §17.5 and resolves its
eight rows (`RESOLUTIONS`); a second parser here would be a second declaration
of the placement, and the second declaration is the one that goes stale.

NO DATABASE IS NEEDED. The DB-level half of S1 is the PGlite harness, whose
subject is the same statement set this module emits.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from tests.test_record_schema_placement import parse_placement
from tortoise import record_projection as rp

REPO_ROOT = Path(__file__).resolve().parent.parent

#: section in §17.5's table → the record table the projection materializes it to
SECTION_TABLE = {
    "Event": "event",
    "Source": "source",
    "Session": "session",
    "Object": "object",
    "Subject": "subject",
    "Point": "point",
}


# ════════════════════════════════════════════════════════════════════════════
# 1. TOTALITY
# ════════════════════════════════════════════════════════════════════════════


def _fold_dispatch_vocabulary() -> set[str]:
    """Every record type `tortoise/projection/` dispatches on.

    The graph fold's dispatch IS the journal's consumed vocabulary — a type the
    fold handles and the record does not is exactly the silent-skip defect.
    """
    found: set[str] = set()
    for name in ("__init__.py", "entities.py", "edges.py"):
        text = (REPO_ROOT / "tortoise" / "projection" / name).read_text(
            encoding="utf-8"
        )
        # `(?<![A-Za-z_])` — without it `unit == "TiB"` (a byte-unit
        # comparison in the fold's own formatting helper) matches as a record
        # type named `TiB`. A word boundary is the whole fix; a denylist of
        # non-record words would hide a real type the day one is named that way.
        found |= set(re.findall(r'(?<![A-Za-z_])t\s*==\s*"([A-Z][A-Za-z]+)"', text))
        found |= set(re.findall(r'ev\.get\("type"\)\s*==\s*"([A-Z][A-Za-z]+)"', text))
        found |= set(re.findall(r'"type":\s*"([A-Z][A-Za-z]+)"', text))
    return found


def _journal_type_registry_vocabulary() -> set[str]:
    """`tortoise/log.py`'s own enumeration of journal record types."""
    text = (REPO_ROOT / "tortoise" / "log.py").read_text(encoding="utf-8")
    block = re.search(
        r"TORN_TAIL_HARMLESS_EVENT_TYPES\s*=\s*frozenset\(\{(.*?)\n\}\)",
        text,
        re.S,
    )
    assert block is not None, "TORN_TAIL_HARMLESS_EVENT_TYPES not found in log.py"
    return set(re.findall(r'"([A-Z][A-Za-z]+)"', block.group(1)))


def test_registry_and_vocabulary_agree():
    """The import-time assert, pinned as a test so a reader sees the expectation."""
    assert set(rp.RECORD_MUTATIONS) == set(rp.JOURNAL_EVENT_TYPES)


def test_every_type_the_fold_dispatches_on_has_a_record_mutation():
    missing = sorted(_fold_dispatch_vocabulary() - set(rp.JOURNAL_EVENT_TYPES))
    assert not missing, (
        f"{missing} are folded by tortoise/projection/ but have no declared "
        "record mutation — Interface A is total, and an event the graph fold "
        "consumes but the record skips is a silent hole"
    )


def test_every_type_the_journal_lists_has_a_record_mutation():
    missing = sorted(_journal_type_registry_vocabulary() - set(rp.JOURNAL_EVENT_TYPES))
    assert not missing, (
        f"{missing} are named by tortoise/log.py as journal record types but "
        "have no declared record mutation"
    )


def test_unknown_record_type_is_a_failure_not_a_skip():
    with pytest.raises(rp.UnknownJournalRecordType) as excinfo:
        rp.mutations_for(
            {"type": "PointTeleported", "id": "p1"}, org_id="o", graph_id="g"
        )
    assert "PointTeleported" in str(excinfo.value)
    # The failure must NAME the remedy, not only the symptom.
    assert "RECORD_MUTATIONS" in str(excinfo.value)


# ── one minimal, realistic record per type, so totality is exercised, not read ─

_SNAPSHOT = {
    "id": "p1",
    "content": "c",
    "pointKind": "observation",
    "createdAt": "2026-10-12T00:00:00Z",
}
_MINIMAL_RECORDS: dict[str, dict] = {
    "PointAdded": {"id": "p1", "point": dict(_SNAPSHOT)},
    "OperatorAdded": {"id": "p1", "point": dict(_SNAPSHOT, is_operator=True, op_type="IMPL")},
    "PointPromoted": {"id": "p1", "point": dict(_SNAPSHOT)},
    "OperatorPromoted": {"id": "p1", "point": dict(_SNAPSHOT)},
    "DedupeRecorded": {"id": "p1", "point": dict(_SNAPSHOT)},
    "DedupeRejected": {"id": "p1", "point": dict(_SNAPSHOT)},
    "EntityBindingRefused": {"id": "p1", "point": dict(_SNAPSHOT)},
    "BatchIdStamped": {"id": "p1", "point": dict(_SNAPSHOT)},
    "PointRevised": {"id": "p1", "new_content": "c2"},
    "OperatorAnnotated": {"id": "p1", "annotator_bias": 0.5},
    "PointRetracted": {"id": "p1"},
    "PointSuperseded": {"id": "p1", "new_id": "p2"},
    "PointInvalidated": {"id": "p1", "corrected_by": "p2"},
    "ConfidenceChanged": {"id": "p1", "confidence": 0.4},
    "ObjectRegistered": {"id": "o1", "name": "n1", "objectKind": "document"},
    "ObjectSuperseded": {"id": "o1", "name": "n1", "supersedes_by": "o2"},
    "SubjectAdded": {"id": "s1", "name": "sub1", "subjectKind": "other"},
    "EventRecorded": {"id": "e1", "eventId": "e1", "eventKind": "x"},
    "SourceCreated": {"id": "u1", "url": "u1", "sourceKind": "web"},
    "SourceVersioned": {"id": "u1", "url": "u1", "contentHash": "h2",
                        "previousContentHash": "h1", "previousValidTo": "t0"},
    "DocumentCreated": {"id": "u1", "url": "u1"},
    "SessionRecorded": {"id": "sess1", "turn_count": 2, "is_episodic": True},
    "SessionDeleted": {"id": "sess1"},
    "EntityLinked": {"id": "p1", "source_label": "Point", "source_id": "p1",
                     "target_label": "Object", "target_id": "n1",
                     "edge_type": "aboutObject"},
    "DirectEdgeCreated": {"id": "p1->p2:IMPL", "src": "p1", "tgt": "p2",
                          "edge_type": "IMPL"},
    "DirectEdgeRepoint": {"id": "p1->p2:IMPL", "src": "p1", "tgt": "p2",
                          "edge_type": "IMPL"},
    "IngestStarted": {"run_id": "r1"},
    "CalibrationRecorded": {"value": 0.5},
}


def test_there_is_a_minimal_record_for_every_declared_mutation():
    """The fixture below must not silently lag the registry."""
    assert set(_MINIMAL_RECORDS) == set(rp.JOURNAL_EVENT_TYPES) - {
        "EntityMutated", "PointsMerged",
    }


@pytest.mark.parametrize("record_type", sorted(_MINIMAL_RECORDS))
def test_each_declared_type_materializes_or_declares_a_no_effect(record_type):
    record = {"type": record_type, "ts": "2026-10-12T00:00:00Z",
              **_MINIMAL_RECORDS[record_type]}
    statements = rp.mutations_for(record, org_id="o", graph_id="g")
    effect = rp.RECORD_MUTATIONS[record_type].effect
    if isinstance(effect, rp.NoRecordEffect):
        assert statements == []
        # A declared no-op must say WHY. "No reason" is the silent skip.
        assert len(effect.reason) > 40, (
            f"{record_type} declares NoRecordEffect with a reason too thin to "
            "be a decision a reviewer can check"
        )
    else:
        assert statements, f"{record_type} produced no mutation and no reason"


def test_entity_mutated_dispatches_on_op_and_refuses_undeclared_ops():
    base = {"type": "EntityMutated", "ts": "T", "id": "p1", "label": "Point"}
    delete = rp.mutations_for({**base, "op": "delete"}, org_id="o", graph_id="g")
    assert len(delete) == 1 and delete[0].sql.startswith("DELETE FROM public.point")

    patch = rp.mutations_for(
        {**base, "op": "restatus", "state": {"status": "retracted"}},
        org_id="o", graph_id="g",
    )
    assert len(patch) == 1 and patch[0].sql.startswith("UPDATE public.point")
    assert "status" in patch[0].sql and "retracted" in patch[0].params

    with pytest.raises(rp.RefusedJournalOperation) as excinfo:
        rp.mutations_for({**base, "op": "rename", "name": "n2"},
                         org_id="o", graph_id="g")
    assert "#3377" in str(excinfo.value)

    with pytest.raises(rp.RecordProjectionError):
        rp.mutations_for({**base, "op": "levitate"}, org_id="o", graph_id="g")


def test_points_merged_is_refused_not_approximated():
    with pytest.raises(rp.RefusedJournalOperation) as excinfo:
        rp.mutations_for(
            {"type": "PointsMerged", "ts": "T", "op": "merge", "keep_id": "p1",
             "merge_ids": ["p2"]},
            org_id="o", graph_id="g",
        )
    assert "REFUSED" in str(excinfo.value)


# ════════════════════════════════════════════════════════════════════════════
# 2. IDEMPOTENCE — structural
# ════════════════════════════════════════════════════════════════════════════


def _plan_for_everything() -> list[rp.Statement]:
    records = [
        {"type": t, "ts": "2026-10-12T00:00:00Z", **payload}
        for t, payload in _MINIMAL_RECORDS.items()
    ]
    records.append({"type": "EntityMutated", "ts": "T", "id": "p1",
                    "label": "Point", "op": "restatus",
                    "state": {"status": "live"}})
    return rp.replay_plan(records, org_id="o", graph_id="g")


def test_replay_plan_is_deterministic():
    """The same journal twice ⇒ the same statements, byte for byte."""
    first = [(s.sql, s.params) for s in _plan_for_everything()]
    second = [(s.sql, s.params) for s in _plan_for_everything()]
    assert first == second


def test_every_node_write_is_an_upsert_and_every_patch_is_absolute():
    """Idempotence by construction — no record type appends or increments.

    An `INSERT` without an `ON CONFLICT` clause is the double-apply defect
    (S1's *"a non-idempotent projection ⇒ a second replay double-applies"*),
    so the assertion is over the WHOLE plan, not a sample.
    """
    for statement in _plan_for_everything():
        sql = statement.sql
        if sql.startswith("INSERT "):
            assert "ON CONFLICT" in sql, f"non-idempotent insert: {sql}"
        elif sql.startswith("UPDATE "):
            assert "= %s" in sql and "||" not in sql.replace("props ||", ""), (
                f"a patch must write absolute values, not accumulate: {sql}"
            )
        elif sql.startswith("DELETE "):
            pass
        else:  # pragma: no cover - the vocabulary has four shapes, no fifth
            raise AssertionError(f"unclassified statement shape: {sql}")


def test_the_journal_position_is_recorded_do_nothing():
    plan = rp.replay_plan([{"type": "IngestStarted", "ts": "T", "run_id": "r1"}],
                          org_id="o", graph_id="g")
    assert len(plan) == 1
    assert plan[0].sql.startswith("INSERT INTO public.journal_event")
    assert "ON CONFLICT (org_id, graph_id, seq) DO NOTHING" in plan[0].sql
    assert plan[0].params[2] == 1  # seq


def test_identity_row_precedes_its_node_row():
    """`node_identity` is the FK parent — a node row without it is refused."""
    plan = rp.replay_plan(
        [{"type": "PointAdded", "ts": "T", "id": "p1", "point": dict(_SNAPSHOT)}],
        org_id="o", graph_id="g",
    )
    kinds = ["identity" if "public.node_identity" in s.sql else
             "point" if "public.point" in s.sql else "journal" for s in plan]
    assert kinds == ["identity", "point", "journal"]


def test_dollar_placeholder_rewrite_is_faithful():
    for statement in _plan_for_everything():
        sql, params = rp.to_dollar_placeholders(statement)
        assert "%s" not in sql
        assert sql.count("$") == len(params)
        for index in range(1, len(params) + 1):
            assert f"${index}" in sql


def test_placeholder_count_mismatch_is_loud():
    with pytest.raises(rp.RecordProjectionError):
        rp.to_dollar_placeholders(rp.Statement("SELECT %s, %s", ("one",)))


# ════════════════════════════════════════════════════════════════════════════
# 3. "A FIELD IS PRIMARY IFF THE JOURNAL CARRIES IT"
# ════════════════════════════════════════════════════════════════════════════


def _placement_by_class() -> dict[str, dict[str, set[str]]]:
    out: dict[str, dict[str, set[str]]] = {}
    for section, prop, klass in parse_placement():
        bucket = out.setdefault(section, {"P": set(), "D": set(), "U": set()})
        bucket.setdefault(klass, set()).add(prop)
    return out


def _stored_names(statements: list[rp.Statement]) -> set[str]:
    """Property names the statements actually persist (columns or `props`)."""
    import json as _json

    stored: set[str] = set()
    for statement in statements:
        if "public.node_identity" in statement.sql or "public.journal_event" in statement.sql:
            continue
        for param in statement.params:
            if isinstance(param, str) and param.startswith("{") and param.endswith("}"):
                try:
                    stored |= set(_json.loads(param))
                except (ValueError, TypeError):  # pragma: no cover
                    continue
        match = re.search(r"public\.(\w+) \(([^)]*)\)", statement.sql)
        if match:
            columns = {c.strip() for c in match.group(2).split(",")}
            stored |= columns - {"org_id", "graph_id", "node_key", "node_label",
                                 "props", "rel_type", "src_label", "src_key",
                                 "tgt_label", "tgt_key", "idx"}
        match = re.search(r"UPDATE public\.(\w+) SET (.*?) WHERE", statement.sql)
        if match:
            stored |= {part.split("=")[0].strip() for part in match.group(2).split(",")}
    return {rp.snake(name) for name in stored}


def test_no_derived_or_volatile_property_is_ever_stored():
    """Capstone S5's record half: a derived value must not be stored as authority.

    Proven on the projection's own output, over every DERIVED/VOLATILE name in
    §17.5, carried by the journal on purpose. The schema's
    `chk_*_props_no_derived` CHECK is the backstop; this is the first line.
    """
    for section, classes in _placement_by_class().items():
        table = SECTION_TABLE.get(section)
        if table is None:
            continue  # the Edges section's grouped predicate rows
        derived = sorted(classes.get("D", set()))
        if not derived:
            continue
        payload = {name: "sentinel" for name in derived}
        record = _synthetic_record_for(table, payload)
        statements = rp.mutations_for(record, org_id="o", graph_id="g")
        stored = _stored_names(statements)
        leaked = sorted(stored & {rp.snake(n) for n in derived})
        assert not leaked, (
            f"{section}: the projection stored DERIVED/VOLATILE {leaked} as "
            "authority — those are recomputed from the structure and must have "
            "no column and no props key (§2.0/§17.5)"
        )


def test_every_primary_property_the_journal_carries_is_storable():
    """Interface A's failure mode: the record must not be poorer than the history."""
    for section, classes in _placement_by_class().items():
        table = SECTION_TABLE.get(section)
        if table is None:
            continue
        primary = sorted(classes.get("P", set()))
        payload = {name: "sentinel" for name in primary}
        record = _synthetic_record_for(table, payload)
        statements = rp.mutations_for(record, org_id="o", graph_id="g")
        stored = _stored_names(statements)
        dropped = sorted({rp.snake(n) for n in primary} - stored)
        assert not dropped, (
            f"{section}: the projection silently dropped PRIMARY {dropped} — a "
            "field the journal carries must be storable (column or the "
            "declared props tail)"
        )


def _synthetic_record_for(table: str, payload: dict[str, Any]) -> dict[str, Any]:
    """A record of the type that materializes `table`, carrying `payload`."""
    key = {"point": "id", "object": "name", "subject": "name",
           "event": "eventId", "source": "url", "session": "id"}[table]
    snapshot = {**payload, key: "k1"}
    record_type = {
        "point": "PointAdded", "object": "ObjectRegistered",
        "subject": "SubjectAdded", "event": "EventRecorded",
        "source": "SourceCreated", "session": "SessionRecorded",
    }[table]
    record: dict[str, Any] = {"type": record_type, "ts": "T", "id": "k1",
                              key: "k1"}
    if record_type in ("PointAdded",):
        record["point"] = snapshot  # the snapshot carrier
    else:
        record.update(snapshot)  # the flat carrier
    return record


def test_the_schema_columns_are_read_from_disk_not_restated():
    """A hand-copied column list is the second declaration that goes stale."""
    columns = rp.table_columns()
    assert set(columns) == set(rp.THE_RECORD_TABLES)
    for table in rp.THE_RECORD_TABLES:
        assert "org_id" in columns[table], f"{table} lost its tenancy column"
    # The carve-out names come from the SQL too.
    refused = rp.refused_props_names()
    assert "confidence" in refused["point"]
    assert "ep_alpha" in refused["point"]
    assert "status" in refused["object"]  # projection-owned cache (§2.6)


def test_a_record_with_no_usable_key_is_refused():
    with pytest.raises(rp.RecordProjectionError) as excinfo:
        rp.mutations_for({"type": "ObjectRegistered", "ts": "T"},
                         org_id="o", graph_id="g")
    assert "resolves to nothing, in silence" in str(excinfo.value)


def test_a_snapshot_carrying_record_without_its_snapshot_is_refused():
    with pytest.raises(rp.RecordProjectionError) as excinfo:
        rp.mutations_for({"type": "PointAdded", "ts": "T", "id": "p1"},
                         org_id="o", graph_id="g")
    assert "materialize a row the journal never wrote" in str(excinfo.value)


# ════════════════════════════════════════════════════════════════════════════
# 3b. The §2.6 split — the two paths the schema's own CHECKs do not cover
# ════════════════════════════════════════════════════════════════════════════


def test_the_projection_owned_status_cache_is_never_stored_by_a_mutation():
    """`EntityMutated` may not write `status` into Object/Subject `props`.

    `Object.status` and `Subject.status` are the **projection-owned lifecycle
    cache** (§2.6, `_fold_object_superseded`'s own docstring; `ONTOLOGY.md`
    §2/§4.2/§4.3), so a stored copy is a derived value made authority — capstone
    S5. `Object.props` refuses the name by CHECK; `Subject.props` does NOT
    (filed as a schema gap in the PR body), so the projection carries the
    refusal on its own side rather than relying on the backstop.

    `Point.status` is the counter-example that keeps the scope honest: a Point's
    lifecycle IS journaled and `point.status` is a PRIMARY column, so
    `PointRetracted`/`PointSuperseded` still write it.
    """
    for label, table in (("Subject", "subject"), ("Object", "object")):
        statements = rp.mutations_for(
            {"type": "EntityMutated", "ts": "T", "id": "k1", "label": label,
             "op": "restatus",
             "state": {"status": "superseded", "supersededBy": "k2",
                       "supersededAt": "2026-10-12T00:00:00Z"}},
            org_id="o", graph_id="g",
        )
        sql, params = statements[0].sql, statements[0].params
        assert sql.startswith(f"UPDATE public.{table}")
        set_clause = sql.split(" SET ", 1)[1].split(" WHERE ")[0]
        assert "status" not in {part.split("=")[0].strip()
                                 for part in set_clause.split(",")}
        assert not any(isinstance(p, str) and p.startswith("{") and "superseded" in p
                       for p in params), (
            f"{label}.status reached the props tail — a projection-owned cache "
            "stored as authority"
        )
        # …and the PRIMARY half of the same record IS stored.
        assert "superseded_by" in sql
        assert "2026-10-12T00:00:00Z" in params

    point = rp.mutations_for(
        {"type": "PointSuperseded", "ts": "T", "id": "p1", "new_id": "p2"},
        org_id="o", graph_id="g",
    )
    assert "status" in point[0].sql and "superseded" in point[0].params


def test_document_created_does_not_store_the_retired_props():
    """§9.5 Q3 retired `content`/`doc_status`/`objectKind` — the fold scrubs them."""
    statements = rp.mutations_for(
        {"type": "DocumentCreated", "ts": "T", "id": "u1", "url": "u1",
         "content": "raw", "doc_status": "captured", "objectKind": "document"},
        org_id="o", graph_id="g",
    )
    insert = statements[-1]
    assert "content" not in insert.sql
    assert not any(isinstance(p, str) and ("doc_status" in p or "objectKind" in p)
                   for p in insert.params), (
        "a retired prop landed in the source row's props tail — the record would "
        "hold state the node scrubbed"
    )


def test_edge_record_only_carries_the_callers_attributes():
    """`confidence`/`weight` are PRIMARY on the EDGE and DERIVED on the Point."""
    statements = rp.mutations_for(
        {"type": "EntityLinked", "ts": "T", "id": "p1", "source_label": "Point",
         "source_id": "p1", "target_label": "Point", "target_id": "p2",
         "edge_type": "IMPL", "confidence": 0.7, "weight": 0.3},
        org_id="o", graph_id="g",
    )
    insert = statements[-1]
    assert "confidence" in insert.sql and "weight" in insert.sql
    assert 0.7 in insert.params and 0.3 in insert.params

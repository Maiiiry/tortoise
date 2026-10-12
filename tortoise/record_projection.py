"""Interface A — the record projection: journal → Postgres, total and idempotent.

WHY THIS MODULE EXISTS
======================
`docs/architecture/STORAGE-ARCHITECTURE.md` §2.0 (owner, 2026-10-08) makes
**Postgres the system of record for every layer** and FalkorDB a **derived hot
cache**. §3 states the invariant that makes that safe:

    derived tables  =  replay(the journal)

`tortoise/projection/` already *is* a replay of the journal — into FalkorDB.
It is therefore the **derived cache's** fold, and a cache rebuilt from a cache
proves nothing. This module is the **record's own** fold (Interface A, epic
`#7879` item 3, issue **#7969**): the same journal, replayed into Postgres.

The issue states four properties, and each is a property of this module rather
than an aspiration:

1. **Every journal event type has a defined Postgres mutation.** Every record
   type in :data:`RECORD_MUTATIONS` carries one — a node materialization, a
   targeted patch, a delete, a relation upsert, or an explicit
   :class:`NoRecordEffect` **with a written reason**.
2. **Idempotent** — a second replay of the same journal produces the same
   record. Every mutation is written so that re-applying it is a no-op: node
   upserts are `ON CONFLICT … DO UPDATE SET` (same values ⇒ same row), patches
   are `UPDATE … SET` to absolute values, deletes are `DELETE`. The journal
   position is *also* recorded (`public.journal_event`, keyed on
   `(org_id, graph_id, seq)`) so the record can say which records it consumed.
3. **Total** — an event with no handler is a **failure, not a skip**. This is
   `#5285`'s rule, applied to the record's destination. `#3585`'s non-folded
   collector made the *graph* fold fail closed; this module is the record's
   equivalent, and it fails at the same place for the same reason: **two
   equally incomplete projections compare equal**, so a fold that silently
   skips an event manufactures a vacuous green.
4. **A field is PRIMARY iff the journal carries it** (§3's per-write test).
   The projection is where that rule becomes executable, so the property→column
   decision is **read from the schema on disk**, never restated here: a property
   with a column is written, a property the schema refuses (the EP-scalar
   carve-out) is **never** written, and a property with neither a column nor a
   carve-out lands in the table's declared `props` tail. A DERIVED value can
   therefore not become authoritative through this door — the schema's
   `chk_*_props_no_derived` CHECK is the backstop, and this module does not rely
   on it being the first line.

WHAT THIS MODULE DELIBERATELY IS NOT
====================================
* **Not the write chokepoint.** §2.3/Interface C is `#7951` — one live path that
  appends the journal, applies the record projection and applies the cache
  projection. This module is the *second* step of that path; it does not route
  live writes.
* **Not the cache projection.** Record → FalkorDB is `#7971` (Interface B).
* **Not a second declaration of the placement table.** `#7968`'s
  `tests/test_record_schema_placement.py` pins §17.5's table to the SQL; this
  module consumes the SQL.
* **No migration-safety apparatus.** Owner scope ruling 2026-10-10: the only
  customer is the dogfood instance. No dual-write, backfill, cutover or
  rollback-parity surface appears here, and none should be added.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "JOURNAL_EVENT_TYPES",
    "RECORD_MUTATIONS",
    "THE_RECORD_TABLES",
    "RecordProjectionError",
    "RefusedJournalOperation",
    "ReplayReport",
    "UnknownJournalRecordType",
    "mutations_for",
    "refused_props_names",
    "replay",
    "replay_plan",
    "snake",
    "table_columns",
    "to_dollar_placeholders",
]

REPO_ROOT = Path(__file__).resolve().parent.parent

RECORD_MIGRATIONS: tuple[str, ...] = (
    "20261012000001_record_event_stream.sql",
    "20261012000002_record_semantic.sql",
    "20261012000003_record_epistemic.sql",
    "20261012000004_record_procedural.sql",
)

#: The record's own tables (the procedural layer is views over these).
THE_RECORD_TABLES: tuple[str, ...] = (
    "node_identity",
    "journal_event",
    "source",
    "event",
    "session",
    "object",
    "subject",
    "point",
    "relation",
)

#: The per-layer node tables, and the canonical key column each one pins
#: `node_key` to (W1/Interface E: `(label, key)`, never a bare id).
NODE_TABLES: dict[str, tuple[str, str]] = {
    # table: (node_label, key column)
    "source": ("Source", "url"),
    "event": ("Event", "event_id"),
    "session": ("Session", "id"),
    "object": ("Object", "name"),
    "subject": ("Subject", "name"),
    "point": ("Point", "id"),
}

#: ``_emit_event`` owns these envelope keys; they are never node properties.
ENVELOPE_KEYS: frozenset[str] = frozenset({
    "type", "ts", "event_id", "initiated_by", "projection_version", "seq",
})

#: The journal's record-type vocabulary. Sourced from the graph fold's own
#: dispatch (`tortoise/projection/`) and the producers' emit sites, and pinned
#: by `tests/test_record_projection.py` against both. A type that appears in a
#: producer but not here is exactly the silent-skip defect totality forbids.
JOURNAL_EVENT_TYPES: frozenset[str] = frozenset({
    "PointAdded",
    "OperatorAdded",
    "PointPromoted",
    "OperatorPromoted",
    "PointRevised",
    "PointRetracted",
    "PointSuperseded",
    "PointInvalidated",
    "PointsMerged",
    "OperatorAnnotated",
    "ConfidenceChanged",
    "EntityMutated",
    "ObjectRegistered",
    "ObjectSuperseded",
    "SubjectAdded",
    "EventRecorded",
    "SourceCreated",
    "SourceVersioned",
    "DocumentCreated",
    "SessionRecorded",
    "SessionDeleted",
    "EntityLinked",
    "DirectEdgeCreated",
    "DirectEdgeRepoint",
    "BatchIdStamped",
    "DedupeRecorded",
    "DedupeRejected",
    "EntityBindingRefused",
    "IngestStarted",
    "CalibrationRecorded",
})


class RecordProjectionError(Exception):
    """Base class — a record projection failure is loud, never a warning."""


class UnknownJournalRecordType(RecordProjectionError):
    """Interface A's *total* clause: an event with no handler is a FAILURE.

    Raised for a record type this projection does not know at all. `#5285`
    asserts `rebuild == live` for the graph; the record's half is that a
    replay which cannot account for every record it consumed cannot claim to
    be the record.
    """


class RefusedJournalOperation(RecordProjectionError):
    """A record this projection knows but will not silently materialize.

    Distinct from :class:`UnknownJournalRecordType` on purpose: a **declared**
    refusal (e.g. an `EntityMutated` op no writer produces yet) is a different
    fact from an **undeclared** type, and collapsing the two would hide which
    one a new producer tripped.
    """


# ════════════════════════════════════════════════════════════════════════════
# The mutation vocabulary. One small algebra, declared per record type.
# ════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Node:
    """A node table, its canonical label, and the column holding the node key."""

    table: str
    label: str
    key_column: str


@dataclass(frozen=True)
class NodeSnapshot:
    """Materialize (upsert) a node row from a property dict.

    ``path`` names the sub-dict that carries the node's properties (`"point"`
    for the snapshot-carrying record types); ``None`` means the record's own
    flat payload *is* the property dict. ``key_from`` names the payload field
    that holds the canonical key when it is not itself inside the snapshot.
    """

    node: Node
    path: str | None
    key_from: str
    drop: frozenset[str] = frozenset()


@dataclass(frozen=True)
class NodePatch:
    """Apply absolute values to an EXISTING row (never creates one).

    A patch on a row the record does not hold is a 0-row no-op — the same
    verdict the graph fold reaches for a revision of an unknown id, and the
    reason a patch must not be an upsert: registering a node from a *mutation*
    record would materialize a row the journal never created.
    """

    node: Node
    key_from: str
    literals: tuple[tuple[str, Any], ...] = ()
    #: Property names taken verbatim from the flat payload. ``None`` means
    #: "every flat payload key that is not record-only" (the `PointRevised`
    #: extras / the `annotator_*` dims); an empty set means "none".
    from_payload: frozenset[str] | None = None
    #: ``(column, payload key)`` renames when the journal's key differs.
    renames: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class NodeDelete:
    """Remove an existing row (a re-delete is a 0-row no-op)."""

    node: Node
    key_from: str


@dataclass(frozen=True)
class RelationUpsert:
    """One row per edge, per `ONTOLOGY.md` §8's reification rule."""

    rel_type_from: str = "edge_type"
    src_from: str = "src"
    tgt_from: str = "tgt"
    attrs: tuple[str, ...] = ("direction", "confidence", "weight", "label",
                              "batch_id", "source_version")


@dataclass(frozen=True)
class RelationDelete:
    """Remove a relation row (the `delete_only` leg of a repoint)."""

    rel_type_from: str = "edge_type"
    src_from: str = "src"
    tgt_from: str = "tgt"


@dataclass(frozen=True)
class NoRecordEffect:
    """A record type that legitimately produces NO record row.

    ``reason`` is mandatory and is the whole point: a declared no-op is a
    decision a reviewer can check, while an undeclared one is the silent skip
    Interface A forbids. Every entry here must be justified by the *record's*
    semantics — not by "we did not get to it".
    """

    reason: str


@dataclass(frozen=True)
class OpDispatch:
    """`EntityMutated` — one record type with an ``op`` discriminator.

    `#3299` chose one record type over one-per-label×operation; the replay
    dispatches on ``op``. Ops not produced by any writer are declared and
    REFUSED rather than guessed (`#3377`/`#4769`).

    ``never_store`` is the record's own half of the §2.6 split: `Object.status`
    and `Subject.status` are **projection-owned caches** (the fold's docstring) —
    `Object.status` is refused by the schema's props CHECK, `Subject.status` is
    not (filed as a schema gap, #7969's PR body) — and the projection must not
    make either authoritative through the props tail. `Point.status` is NOT in
    this set: a Point's lifecycle IS journaled (`PointRetracted`/`…Superseded`)
    and `point.status` is a PRIMARY column.
    """

    table_for_label: Mapping[str, str]
    patch_ops: frozenset[str]
    delete_ops: frozenset[str]
    refused_ops: Mapping[str, str]
    never_store: Mapping[str, frozenset[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class Mutation:
    """The declared mutation for one record type (a documented union)."""

    effect: (
        NodeSnapshot | NodePatch | NodeDelete
        | RelationUpsert | RelationDelete | OpDispatch | NoRecordEffect
    )
    note: str = ""


#: **The registry.** Every member of :data:`JOURNAL_EVENT_TYPES` has an entry;
#: `tests/test_record_projection.py` asserts that in both directions, so a new
#: producer fails this suite rather than disappearing at replay time.
RECORD_MUTATIONS: dict[str, Mutation] = {
    # ── Epistemic layer: Points and operator Points ──────────────────────────
    # The `…Added` / `…Promoted` / dedup record types all carry a FULL point
    # snapshot (`payload["point"]`), which is what the graph fold indexes too
    # (`_apply_one`'s `points[p["id"]] = p`), so the record projects the same
    # dict the fold does. `id` rides the envelope, not the snapshot.
    "PointAdded": Mutation(
        NodeSnapshot(Node("point", "Point", "id"), path="point", key_from="id"),
        note="full snapshot under `point`; identity keys are record-only and "
             "are dropped by `drop`",
    ),
    "OperatorAdded": Mutation(
        NodeSnapshot(Node("point", "Point", "id"), path="point", key_from="id"),
    ),
    "PointPromoted": Mutation(
        NodeSnapshot(Node("point", "Point", "id"), path="point", key_from="id"),
    ),
    "OperatorPromoted": Mutation(
        NodeSnapshot(Node("point", "Point", "id"), path="point", key_from="id"),
    ),
    "DedupeRecorded": Mutation(
        NodeSnapshot(Node("point", "Point", "id"), path="point", key_from="id"),
        note="#784 — the payload is the point snapshot the audit inspected",
    ),
    "DedupeRejected": Mutation(
        NodeSnapshot(Node("point", "Point", "id"), path="point", key_from="id"),
    ),
    "EntityBindingRefused": Mutation(
        NodeSnapshot(Node("point", "Point", "id"), path="point", key_from="id"),
    ),
    # #902/A10: batch_id (+ content_hash / mitigates_operator_id /
    # mitigation_strength) restore, replayed from the batch snapshot.
    "BatchIdStamped": Mutation(
        NodeSnapshot(Node("point", "Point", "id"), path="point", key_from="id"),
    ),
    # ⚠️ `new_content` is the journal's own name for the revised `content`; it
    # is NOT a node property of that name, so it is renamed rather than written
    # into `props`. The fold writes `p["content"] = new_content`.
    "PointRevised": Mutation(
        NodePatch(
            Node("point", "Point", "id"),
            key_from="id",
            renames=(("content", "new_content"),),
        ),
        note="extras ride the flat payload verbatim (an `annotator_*` key here "
             "is a node property of that exact name, never aliased)",
    ),
    "OperatorAnnotated": Mutation(
        NodePatch(Node("point", "Point", "id"), key_from="id"),
        note="#3689 — the JSONL line carries the canonical `annotator_*` names; "
             "the `:GraphEvent` payload's SHORT aliases never reach this door",
    ),
    "PointRetracted": Mutation(
        NodePatch(Node("point", "Point", "id"), key_from="id",
                  literals=(("status", "retracted"),),
                  from_payload=frozenset()),
        note="tombstone — the node is KEPT (the hard delete is EntityMutated)",
    ),
    "PointSuperseded": Mutation(
        NodePatch(Node("point", "Point", "id"), key_from="id",
                  literals=(("status", "superseded"),),
                  from_payload=frozenset(),
                  renames=(("superseded_by", "new_id"),)),
    ),
    "PointInvalidated": Mutation(
        NodePatch(Node("point", "Point", "id"), key_from="id",
                  literals=(("outdated", True),),
                  from_payload=frozenset(),
                  renames=(("superseded_by", "corrected_by"),)),
        note="#2488 — `outdated` flag + CORRECTS; no status change",
    ),
    # ⚠️ THE EP-SCALAR CARVE-OUT, in its executable form: `confidence` is
    # DERIVED (§2.0/§17.5 — recomputed from the structure, "never stored as
    # authority"), and the record has no column for it. The record's mutation
    # for this record type is therefore, correctly, *none* — and it is declared
    # rather than dropped, so the day a writer starts using it for something
    # else the decision is visible.
    "ConfidenceChanged": Mutation(
        NoRecordEffect(
            "EP carve-out (STORAGE-ARCHITECTURE §2.0/§17.5): confidence is "
            "recomputed from the structure and is never stored as authority. "
            "`public.point` has no `confidence` column and its props CHECK "
            "refuses the name, so the record's correct mutation is none. The "
            "`outdated=true` terminal leg rides PointInvalidated's flag, and "
            "`_graph_id`/`_nid`/`reason` are replay bookkeeping (DERIVED)."
        ),
    ),
    "PointsMerged": Mutation(
        OpDispatch(
            table_for_label={"Point": "point"},
            patch_ops=frozenset(),
            delete_ops=frozenset({"merge"}),
            refused_ops={
                "merge": "#331 merge is not materialized yet: the record's "
                         "merge is survivor-update + loser-delete + edge "
                         "re-point, and no unit owns that shape (the journal "
                         "carries `keep_id`/`merge_ids`/`corrects`, not the "
                         "post-merge edge set). Declared and REFUSED rather "
                         "than approximated.",
            },
        ),
        note="refused, not skipped — an approximate merge would silently "
             "re-point edges the journal does not describe",
    ),
    "EntityMutated": Mutation(
        OpDispatch(
            table_for_label={
                "Point": "point",
                "Object": "object",
                "Subject": "subject",
                "Event": "event",
                "Source": "source",
                "Session": "session",
            },
            patch_ops=frozenset({"revise", "restatus", "supersede"}),
            delete_ops=frozenset({"delete"}),
            refused_ops={
                "rename": "#3377/#4769 — the fold supports `rename` but no "
                          "writer produces it, deliberately: a name-bearing "
                          "write withholds its record because journalling it "
                          "drops a legacy name-keyed ObjectSuperseded on "
                          "replay. On the record side a rename MOVES the "
                          "canonical key (Object/Subject key = `name`), so it "
                          "is a key migration, not a patch.",
            },
            never_store={
                "object": frozenset({"status"}),
                "subject": frozenset({"status"}),
            },
        ),
        note="#3299/#3300/#3311/#3312/#1423 — one record type, dispatched on "
             "`op`; `state` carries absolute values the graph STORED",
    ),
    # ── Semantic layer ───────────────────────────────────────────────────────
    "ObjectRegistered": Mutation(
        NodeSnapshot(Node("object", "Object", "name"), path=None, key_from="name"),
    ),
    "ObjectSuperseded": Mutation(
        NodePatch(
            Node("object", "Object", "name"),
            key_from="name",
            from_payload=frozenset(),
            renames=(("superseded_by", "supersedes_by"),
                     ("superseded_at", "ts")),
        ),
        note="#1350 — `supersededBy` is journal-carried verbatim (PRIMARY); "
             "`Object.status` is NOT written (projection-owned cache, §2.6) "
             "and `supersededAt` comes from the record's own envelope `ts`",
    ),
    "SubjectAdded": Mutation(
        NodeSnapshot(Node("subject", "Subject", "name"), path=None, key_from="name"),
    ),
    # ── The event stream, materialized (the episodic layer) ──────────────────
    "EventRecorded": Mutation(
        NodeSnapshot(Node("event", "Event", "event_id"), path=None,
                     key_from="event_id", drop=frozenset({"eventId"})),
        note="the key column is `event_id`; a flat `eventId` is the same key "
             "under the record column's name, never a second property",
    ),
    "SourceCreated": Mutation(
        NodeSnapshot(Node("source", "Source", "url"), path=None, key_from="url"),
    ),
    "DocumentCreated": Mutation(
        NodeSnapshot(Node("source", "Source", "url"), path=None, key_from="url",
                     drop=frozenset({"content", "doc_status", "docStatus",
                                     "objectKind", "object_kind"})),
        note="§9.3/§9.5 Q3 — a document is a `:Source`; the retired "
             "`content`/`doc_status`/`objectKind` props have no column AND are "
             "scrubbed by the graph fold (`_upsert_document`), so storing them "
             "would leave the record holding state the node removed",
    ),
    "SourceVersioned": Mutation(
        NodeSnapshot(Node("source", "Source", "url"), path=None, key_from="url",
                     drop=frozenset({"previousContentHash", "previousValidTo"})),
        note="#5024/T6 — the prior version's hash and window are RECORD-ONLY "
             "keys (`_SOURCE_HANDLED` keeps them off the node; the single "
             "`:Source` row holds only the current version)",
    ),
    "SessionRecorded": Mutation(
        NodeSnapshot(Node("session", "Session", "id"), path=None, key_from="id"),
        note="four records per capture, merged onto one row — see the "
             "first-writer-wins / last-writer-wins split below",
    ),
    "SessionDeleted": Mutation(
        NodeDelete(Node("session", "Session", "id"), key_from="id"),
        note="#7951 — the hard-delete carrier; the session's agentSession "
             "`:Source` stub is a `Source` row keyed `session:{id}` and is "
             "removed with it",
    ),
    # ── Edges ────────────────────────────────────────────────────────────────
    "EntityLinked": Mutation(
        RelationUpsert(),
        note="#3664 — flat logical endpoints; `edge_type` and both labels are "
             "validated against a frozen vocabulary by the graph fold",
    ),
    "DirectEdgeCreated": Mutation(
        RelationUpsert(),
        note="A10/#1048 — §4.4's canonical flat descriptor, shared with "
             "DirectEdgeRepoint",
    ),
    "DirectEdgeRepoint": Mutation(
        RelationUpsert(),
        note="A10 pass-2b — same flat descriptor; a `delete_only` leg removes "
             "the old edge and is carried by the record's own key",
    ),
    # ── Bookkeeping: declared no-ops, with the reason each one is one ────────
    "IngestStarted": Mutation(
        NoRecordEffect(
            "audit-only run marker (`_NO_PROJECTION_FOLD`): no graph effect, "
            "and the journal position itself is already in "
            "`public.journal_event`, which is the record of what happened"
        ),
    ),
    "CalibrationRecorded": Mutation(
        NoRecordEffect(
            ":Meta milestone marker (audit) — `_NO_PROJECTION_FOLD`; the value "
            "it records is a calibration constant recomputed from the corpus, "
            "not a node property"
        ),
    ),
}

#: Asserted at import time so a registry edit cannot silently lose a type.
assert set(RECORD_MUTATIONS) == set(JOURNAL_EVENT_TYPES), (
    "RECORD_MUTATIONS and JOURNAL_EVENT_TYPES must agree in both directions"
)


# ════════════════════════════════════════════════════════════════════════════
# The schema is read from disk, never restated. (§3: "a field is PRIMARY iff
# the journal carries it" — the projection is where that becomes executable.)
# ════════════════════════════════════════════════════════════════════════════

_COLUMN_RE = re.compile(r'^\s*("[a-z_][a-z0-9_]*"|[a-z_][a-z0-9_]*)\s+'
                        r'(?:text|jsonb|integer|boolean|bigint|double precision|'
                        r'timestamptz)\b', re.M)
_DERIVED_CHECK_RE = re.compile(
    r"chk_[a-z_]+_props_no_derived\s+CHECK\s*\(\s*NOT\s*\(\s*props\s*\?\|"
    r"\s*ARRAY\[(.*?)\]\s*::text\[\]\s*\)\s*\)",
    re.S,
)
_TABLE_RE = re.compile(
    r"CREATE TABLE IF NOT EXISTS public\.([a-z_]+)\s*\((.*?)\n\);\s*\n", re.S
)


def _migrations_dir() -> Path:
    return REPO_ROOT / "supabase" / "migrations"


def _read_migrations(migrations_dir: Path | None = None) -> str:
    base = migrations_dir or _migrations_dir()
    return "\n".join(
        (base / name).read_text(encoding="utf-8")
        for name in RECORD_MIGRATIONS
        if (base / name).exists()
    )


def _parse_record_tables(sql: str) -> dict[str, frozenset[str]]:
    tables: dict[str, frozenset[str]] = {}
    for name, body in _TABLE_RE.findall(sql):
        if name not in THE_RECORD_TABLES:
            continue
        columns = {m.group(1).strip('"') for m in _COLUMN_RE.finditer(body)}
        # `props` is spelled `props jsonb NOT NULL DEFAULT …` — matched above.
        tables[name] = frozenset(columns)
    return tables


def table_columns(migrations_dir: Path | None = None) -> dict[str, frozenset[str]]:
    """`{table: {column}}` for the record's tables, parsed from the migrations.

    Parsed rather than restated: a hand-copied column list is a second
    declaration of the schema, and the second declaration is the one that goes
    stale. `#7968`'s own pin does the same thing for the placement table.
    """
    tables = _parse_record_tables(_read_migrations(migrations_dir))
    missing = [t for t in THE_RECORD_TABLES if t not in tables]
    if missing:
        raise RecordProjectionError(
            "the record schema is not on disk — no CREATE TABLE found for "
            f"{missing}. The record projection targets `#7968`'s migrations "
            f"under {_migrations_dir()}."
        )
    return tables


def refused_props_names(migrations_dir: Path | None = None) -> dict[str, frozenset[str]]:
    """`{table: {name refused as a props key}}` — the carve-out, from the SQL.

    These are the names the schema itself refuses: the EP scalars (§17.5) and
    the projection-owned caches. The record must not store them as authority,
    and the projection drops them **by name** rather than relying on the CHECK
    to catch it later.
    """
    sql = _read_migrations(migrations_dir)
    out: dict[str, frozenset[str]] = {}
    for block in re.split(r"CREATE TABLE IF NOT EXISTS public\.", sql):
        table = block.split("(", 1)[0].strip()
        if table not in THE_RECORD_TABLES:
            continue
        names: set[str] = set()
        for arr in _DERIVED_CHECK_RE.findall(block):
            names |= {n.strip().strip("'") for n in arr.split(",") if n.strip()}
        out[table] = frozenset(n for n in names if n)
    return out


def snake(prop: str) -> str:
    """camelCase → snake_case, preserving leading underscores.

    The SAME rule `#7968`'s schema↔placement pin uses, and preserving the
    leading underscore matters for the same reason: `_nid`/`_graph_id` are
    replay bookkeeping (DERIVED), while `graph_id` is the record's tenancy
    column — collapsing the underscore would let a DERIVED name look satisfied.
    """
    return re.sub(r"(?<!^)(?=[A-Z])", "_", prop).lower()


#: Journal keys that name the record itself rather than a node property: the
#: `_emit_event` envelope, the snapshot carrier, and the JSONL-only link/batch
#: markers. They are never written as columns and never fall into `props`.
#:
#: ⚠️ Deliberately NARROW. `id`, `name`, `label`, `key`, `op` and `state` are
#: NOT here even though `EntityMutated`/`EntityLinked` treat them as record
#: coordinates: for a `Point` snapshot `label` is a real PRIMARY property
#: (operator label), for `Object`/`Subject` `name` is written as the key, and
#: for `Event` `id` mirrors `eventId` (#122). Those two dispatch paths read
#: their coordinates directly, so a global drop would silently lose a PRIMARY
#: field from the layer rows — exactly the "record poorer than the history"
#: failure Interface A forbids.
_RECORD_ONLY_KEYS: frozenset[str] = frozenset({
    "type", "ts", "initiated_by", "projection_version", "seq",
    "point", "contains_session", "keep_id", "merge_ids", "corrects",
    # Creating-record embedding identity (a DESCRIPTION of the record, never a
    # node property — the fold's `_POINT_HANDLED` drops them too).
    "embedding_model", "embedding_revision", "embedding_text_hash",
    "embedding_preserved",
})


# ════════════════════════════════════════════════════════════════════════════
# Statement emission
# ════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Statement:
    """One parameterized SQL statement plus its parameters."""

    sql: str
    params: tuple[Any, ...]

    def render(self) -> str:
        """A readable one-liner for logs and hover-less debugging."""
        return f"{self.sql} -- {self.params!r}"


def _jsonb_or_scalar(column: str, value: Any) -> Any:
    """`jsonb` columns take serialized JSON; everything else passes through.

    The caller supplies the value; this module does not coerce, because a
    journal carrying a string where the record's column is `boolean` is a
    journal/record disagreement and coercing it here would hide it.
    """
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return value


def _split_properties(
    table: str,
    props: Mapping[str, Any],
    *,
    columns: Mapping[str, frozenset[str]],
    refused: Mapping[str, frozenset[str]],
    drop: frozenset[str] = frozenset(),
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split a journal property dict into `(columns, props_tail)`.

    Three destinations, and no fourth:
      * a property with a column is written to that column;
      * a property the schema refuses as a props key is **dropped** (it is
        DERIVED/VOLATILE — recomputed from the structure, never authority);
      * everything else lands in the declared `props` tail.

    An explicit ``drop`` set carries the RECORD-ONLY keys — journal keys that
    describe the record rather than the node (`previousContentHash`,
    `previousValidTo`, a snapshot's identity keys).
    """
    cols = columns[table]
    refused_here = refused.get(table, frozenset())
    col_values: dict[str, Any] = {}
    tail: dict[str, Any] = {}
    for prop, value in props.items():
        if prop in drop:
            continue
        column = snake(prop)
        if column in cols:
            col_values[column] = value
        elif prop in refused_here or column in refused_here:
            # DERIVED/VOLATILE — the carve-out. Dropping is the correct
            # mutation; storing it would make a recomputed value authority.
            continue
        else:
            tail[prop] = value
    return col_values, tail


def _node_table(node: Node, columns: Mapping[str, frozenset[str]]) -> str:
    if node.table not in columns:
        raise RecordProjectionError(
            f"unknown record table {node.table!r} — not one of {sorted(columns)}"
        )
    return node.table


def _key_value(node: Node, payload: Mapping[str, Any], key_from: str) -> str:
    """The node key, and the schema's own pin that it is the label's key.

    A record whose key column is absent cannot be projected: the schema's
    `chk_*_node_key_is_contract_key` would refuse the write anyway, and a
    `node_key = <NULL>` comparison is SQL NULL (not FALSE), so the failure
    would be silent. Fail here instead, where the record is known.
    """
    value = payload.get(key_from)
    if value is None and key_from == "event_id":
        value = payload.get("eventId")
    if not isinstance(value, str) or not value:
        raise RecordProjectionError(
            f"record has no usable {key_from!r} key for {node.label} "
            f"(got {value!r}) — a row whose node_key is not the label's key "
            "resolves to nothing, in silence"
        )
    return value


def _identity_statement(org_id: str, graph_id: str, node: Node, key: str) -> Statement:
    """The `(label, key)` FK parent. Written first, always.

    `public.node_identity` is why a relation's endpoints are real composite
    foreign keys (S6). Every node write is preceded by its identity row, and
    the insert is `DO NOTHING` on conflict so a re-replay is a no-op.
    """
    return Statement(
        "INSERT INTO public.node_identity "
        "(org_id, graph_id, node_label, node_key) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (org_id, graph_id, node_label, node_key) DO NOTHING",
        (org_id, graph_id, node.label, key),
    )


def _upsert_statement(
    org_id: str,
    graph_id: str,
    node: Node,
    key: str,
    col_values: Mapping[str, Any],
    tail: Mapping[str, Any],
) -> Statement:
    columns = ["org_id", "graph_id", "node_label", "node_key", *col_values, "props"]
    params: list[Any] = [org_id, graph_id, node.label, key,
                         *col_values.values(), json.dumps(tail)]
    placeholders = ", ".join(["%s"] * len(columns))
    updates = ", ".join(
        f"{c} = EXCLUDED.{c}" for c in [*col_values, "props"]
    )
    # IDEMPOTENCE, stated once: re-applying the same record writes the same
    # absolute values onto the same row. A second replay of the journal
    # therefore produces the same record (Interface A's second clause).
    sql = (
        f"INSERT INTO public.{node.table} ({', '.join(columns)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT (org_id, graph_id, node_key) DO UPDATE SET {updates}"
    )
    return Statement(sql, tuple(params))


def _patch_statement(
    org_id: str,
    graph_id: str,
    node: Node,
    key: str,
    col_values: Mapping[str, Any],
    tail: Mapping[str, Any],
) -> Statement:
    sets: list[str] = []
    params: list[Any] = []
    for column, value in col_values.items():
        sets.append(f"{column} = %s")
        params.append(_jsonb_or_scalar(column, value))
    if tail:
        sets.append("props = props || %s::jsonb")
        params.append(json.dumps(tail))
    if not sets:
        # A patch with nothing to write is a 0-row no-op by construction; make
        # it a real statement so the caller still sees the record was consumed.
        sets.append("node_key = node_key")
    params.extend([org_id, graph_id, key])
    sql = (
        f"UPDATE public.{node.table} SET {', '.join(sets)} "
        "WHERE org_id = %s AND graph_id = %s AND node_key = %s"
    )
    return Statement(sql, tuple(params))


def _delete_statement(org_id: str, graph_id: str, node: Node, key: str) -> Statement:
    return Statement(
        f"DELETE FROM public.{node.table} "
        "WHERE org_id = %s AND graph_id = %s AND node_key = %s",
        (org_id, graph_id, key),
    )


def _relation_upsert_statement(
    org_id: str,
    graph_id: str,
    rel: RelationUpsert,
    payload: Mapping[str, Any],
    columns: Mapping[str, frozenset[str]],
    refused: Mapping[str, frozenset[str]],
) -> list[Statement]:
    rel_type = payload.get(rel.rel_type_from) or payload.get("type_of_edge")
    src = payload.get(rel.src_from) or payload.get("source_id")
    tgt = payload.get(rel.tgt_from) or payload.get("target_id")
    if not isinstance(rel_type, str) or not rel_type:
        raise RecordProjectionError(
            f"edge record has no usable edge type (got {rel_type!r}) — an "
            "edge with no predicate is not a relation"
        )
    src_label = payload.get("source_label") or payload.get("src_label") or "Point"
    tgt_label = payload.get("target_label") or payload.get("tgt_label") or "Point"
    if not isinstance(src, str) or not isinstance(tgt, str) or not src or not tgt:
        raise RecordProjectionError(
            f"edge record has no usable endpoints (src={src!r}, tgt={tgt!r})"
        )
    col_values, tail = _split_properties(
        "relation", {k: payload[k] for k in rel.attrs if k in payload},
        columns=columns, refused=refused,
    )
    statements = [
        _identity_statement(org_id, graph_id, Node("point", str(src_label), "id"), src),
        _identity_statement(org_id, graph_id, Node("point", str(tgt_label), "id"), tgt),
    ]
    columns_ = ["org_id", "graph_id", "rel_type", "src_label", "src_key",
                "tgt_label", "tgt_key", "idx", *col_values, "props"]
    idx = payload.get("idx", 0)
    params: list[Any] = [org_id, graph_id, rel_type, src_label, src, tgt_label,
                         tgt, idx, *col_values.values(), json.dumps(tail)]
    placeholders = ", ".join(["%s"] * len(columns_))
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in [*col_values, "props"])
    statements.append(Statement(
        f"INSERT INTO public.relation ({', '.join(columns_)}) "
        f"VALUES ({placeholders}) "
        "ON CONFLICT (org_id, graph_id, rel_type, src_label, src_key, "
        f"tgt_label, tgt_key, idx) DO UPDATE SET {updates}",
        tuple(params),
    ))
    return statements


def _op_dispatch_statements(
    org_id: str,
    graph_id: str,
    dispatch: OpDispatch,
    payload: Mapping[str, Any],
    columns: Mapping[str, frozenset[str]],
    refused: Mapping[str, frozenset[str]],
) -> list[Statement]:
    op = payload.get("op")
    label = payload.get("label")
    if not isinstance(op, str):
        raise RecordProjectionError("EntityMutated record carries no `op`")
    if op in dispatch.refused_ops:
        raise RefusedJournalOperation(f"EntityMutated op={op!r}: {dispatch.refused_ops[op]}")
    table = dispatch.table_for_label.get(str(label))
    if table is None:
        raise RecordProjectionError(
            f"EntityMutated names label {label!r}, which is not a record node "
            f"label ({sorted(dispatch.table_for_label)})"
        )
    node = Node(table, str(label), NODE_TABLES[table][1])
    state = payload.get("state")
    state = state if isinstance(state, Mapping) else {}
    key = payload.get("key") or payload.get("id") or state.get("name")
    if not isinstance(key, str) or not key:
        raise RecordProjectionError(f"EntityMutated op={op!r} carries no key")
    if op in dispatch.delete_ops:
        return [_delete_statement(org_id, graph_id, node, key)]
    if op in dispatch.patch_ops:
        col_values, tail = _split_properties(
            table, {k: v for k, v in state.items() if k != "name"},
            columns=columns, refused=refused,
            drop=dispatch.never_store.get(table, frozenset()),
        )
        return [_patch_statement(org_id, graph_id, node, key, col_values, tail)]
    raise RecordProjectionError(
        f"EntityMutated op={op!r} is neither patched nor deleted nor declared "
        "refused — an undeclared op is the silent-skip defect"
    )


def mutations_for(
    record: Mapping[str, Any],
    *,
    org_id: str,
    graph_id: str,
    columns: Mapping[str, frozenset[str]] | None = None,
    refused: Mapping[str, frozenset[str]] | None = None,
) -> list[Statement]:
    """The Postgres mutations for ONE journal record. **Total by construction.**

    Raises :class:`UnknownJournalRecordType` for a record type the registry
    does not declare, and :class:`RefusedJournalOperation` for a declared
    refusal. Both are failures — neither is a skip.
    """
    columns = columns if columns is not None else table_columns()
    refused = refused if refused is not None else refused_props_names()
    record_type = record.get("type")
    mutation = RECORD_MUTATIONS.get(record_type) if isinstance(record_type, str) else None
    if mutation is None:
        raise UnknownJournalRecordType(
            f"{record_type!r} has no declared record mutation — Interface A is "
            "TOTAL: an event with no handler is a failure, not a skip (#5285's "
            "rule applied to the record). Declare it in RECORD_MUTATIONS."
        )
    effect = mutation.effect
    if isinstance(effect, NoRecordEffect):
        return []
    if isinstance(effect, OpDispatch):
        return _op_dispatch_statements(org_id, graph_id, effect, record,
                                       columns, refused)
    if isinstance(effect, RelationUpsert):
        return _relation_upsert_statement(org_id, graph_id, effect, record,
                                          columns, refused)
    if isinstance(effect, RelationDelete):
        raise RefusedJournalOperation(
            "relation deletes ride the repoint's own record; no record type "
            "carries a bare relation delete today"
        )
    if isinstance(effect, NodeSnapshot):
        node = effect.node
        _node_table(node, columns)
        key = _key_value(node, record, effect.key_from)
        props: dict[str, Any] = {}
        if effect.path is None:
            props = {k: v for k, v in record.items() if k not in _RECORD_ONLY_KEYS}
            props.setdefault(effect.key_from, key)
        else:
            snapshot = record.get(effect.path)
            if not isinstance(snapshot, Mapping):
                raise RecordProjectionError(
                    f"{record_type!r} carries no `{effect.path}` snapshot to "
                    "project — registering from a record that does not carry "
                    "the node would materialize a row the journal never wrote"
                )
            props = {k: v for k, v in snapshot.items() if k not in _RECORD_ONLY_KEYS}
            props[effect.key_from] = key
        col_values, tail = _split_properties(node.table, props, columns=columns,
                                            refused=refused, drop=effect.drop)
        if node.key_column not in col_values:
            col_values[node.key_column] = key
        return [
            _identity_statement(org_id, graph_id, node, key),
            _upsert_statement(org_id, graph_id, node, key, col_values, tail),
        ]
    if isinstance(effect, NodePatch):
        node = effect.node
        key = _key_value(node, record, effect.key_from)
        props: dict[str, Any] = dict(effect.literals)
        if effect.from_payload:
            props.update({k: v for k, v in record.items()
                          if k not in _RECORD_ONLY_KEYS and k in effect.from_payload})
        elif effect.from_payload is None:
            # `PointRevised` / `OperatorAnnotated`: the extras ride the flat
            # payload VERBATIM, so every non-record key is a node property —
            # except a key that is the rename's own source (`new_content`
            # names the revised `content`; it is not a property of its own).
            renamed_sources = {src for _, src in effect.renames}
            props.update({k: v for k, v in record.items()
                          if k not in _RECORD_ONLY_KEYS
                          and k not in renamed_sources})
        for column, source_key in effect.renames:
            if source_key in record:
                props[column] = record[source_key]
        col_values, tail = _split_properties(node.table, props, columns=columns,
                                            refused=refused)
        return [_patch_statement(org_id, graph_id, node, key, col_values, tail)]
    if isinstance(effect, NodeDelete):
        node = effect.node
        key = _key_value(node, record, effect.key_from)
        return [_delete_statement(org_id, graph_id, node, key)]
    raise RecordProjectionError(
        f"{record_type!r} declares an effect this engine does not implement: "
        f"{type(effect).__name__}"
    )


# ════════════════════════════════════════════════════════════════════════════
# The journal-position record, and the replay driver
# ════════════════════════════════════════════════════════════════════════════


def _journal_row(record: Mapping[str, Any], seq: int, org_id: str,
                 graph_id: str) -> Statement:
    """The record of WHAT the record consumed — `public.journal_event`.

    Keyed on `(org_id, graph_id, seq)`, which the schema calls *"the
    idempotency key: applying a position twice is a no-op"*. `ON CONFLICT DO
    NOTHING` because the table is append-only by TRIGGER: an update would be
    refused, and correctly so — a consumed position is not revisable.
    """
    return Statement(
        "INSERT INTO public.journal_event "
        "(org_id, graph_id, seq, event_type, payload, recorded_at) "
        "VALUES (%s, %s, %s, %s, %s, COALESCE(%s::timestamptz, now())) "
        "ON CONFLICT (org_id, graph_id, seq) DO NOTHING",
        (
            org_id, graph_id, seq, record.get("type"),
            json.dumps({k: v for k, v in record.items() if k not in ENVELOPE_KEYS}),
            record.get("ts"),
        ),
    )


@dataclass
class ReplayReport:
    """What a replay consumed. A report, never a substitute for the failure."""

    events: int = 0
    statements: int = 0
    no_effects: list[str] = field(default_factory=list)

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return (f"replayed {self.events} journal records → {self.statements} "
                f"record mutations ({len(self.no_effects)} declared no-op)")


def replay(records: Sequence[Mapping[str, Any]], conn: Any, *, org_id: str,
           graph_id: str) -> ReplayReport:
    """Replay a journal into the record, in ONE transaction.

    `§3`: *"One transaction covers the journal and its projection — no dual
    write between them."* The connection is any DB-API 2.0 connection; this
    module does not choose the driver (the chokepoint, `#7951`, does).

    IDEMPOTENCE. Every emitted mutation is absolute: an upsert writes the same
    values onto the same row, a patch writes the same values, a delete finds
    nothing, and the journal position is `DO NOTHING`. A second replay of the
    same journal therefore produces the same record — and the assertion the
    caller should make is exactly `#5285`'s, moved to the record: **replay
    twice and compare the tables, not the code paths.**

    TOTALITY. The whole journal is materialized into statements *before* the
    first one executes, so an undeclared record type fails the replay with the
    transaction untouched rather than leaving a half-applied record.
    """
    plan = replay_plan(records, org_id=org_id, graph_id=graph_id)
    cursor = conn.cursor()
    try:
        for statement in plan:
            cursor.execute(statement.sql, statement.params)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    report = ReplayReport(events=len(records), statements=0)
    for statement in plan:
        if _JOURNAL_INSERT_PREFIX in statement.sql:
            continue
        report.statements += 1
    return report


#: Distinguishes the journal-position write from the record's own mutations,
#: so a report can count what the projection produced.
_JOURNAL_INSERT_PREFIX = "INSERT INTO public.journal_event"


def replay_plan(records: Sequence[Mapping[str, Any]], *, org_id: str,
                graph_id: str) -> list[Statement]:
    """Every statement a full replay emits, in order, WITHOUT executing any.

    The totality gate lives here: an undeclared record type raises before a
    single statement has run, so a replay either materializes the whole journal
    or leaves the record exactly as it found it. It is also the seam the PGlite
    harness (`supabase/tests/pglite/validate.mjs`) and the tests use to look at
    the *whole* statement set rather than one record at a time.
    """
    columns = table_columns()
    refused = refused_props_names()
    plan: list[Statement] = []
    for seq, record in enumerate(records, start=1):
        plan.extend(mutations_for(record, org_id=org_id, graph_id=graph_id,
                                  columns=columns, refused=refused))
        plan.append(_journal_row(record, seq, org_id, graph_id))
    return plan


def to_dollar_placeholders(statement: Statement) -> tuple[str, list[Any]]:
    """Rewrite DB-API `%s` placeholders to Postgres `$n`.

    The engine emits DB-API (`%s`) parameters because that is what the Python
    driver on the write path takes. A harness that speaks the *native* protocol
    (PGlite, `psql`) needs `$n`, and this is the one converter for both — a
    second hand-written copy is how the two drift.
    """
    parts = statement.sql.split("%s")
    if len(parts) - 1 != len(statement.params):
        raise RecordProjectionError(
            f"statement has {len(parts) - 1} placeholders for "
            f"{len(statement.params)} parameters: {statement.sql}"
        )
    out = [parts[0]]
    for index, tail in enumerate(parts[1:], start=1):
        out.append(f"${index}")
        out.append(tail)
    return "".join(out), list(statement.params)

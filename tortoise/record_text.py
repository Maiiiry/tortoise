"""§9.4's verbatim-evidence fetch, served from the RECORD — R7 (#9555, epic #7879).

§9.4 (`docs/architecture/STORAGE-ARCHITECTURE.md`) makes the link between a
claim and its source **load-bearing**, not a citation nicety:

> **At answer time, send the source's VERBATIM text, not the extracted claim.**
> *(Measured: verbatim source beats LLM-extracted artefacts by 15.9 / 22.0 pts —
> extractor §2.4; `#3011` would settle it on our stack.)*

§9.1 places that text in Postgres — *"the narrative is not something we're
suggesting to store in the graph (same as raw) but store in supabase"* — and
`#9638` landed its home (`public.session_narrative`) plus the record-only reader
(`tortoise/record_read.py`). Measured for #9555, that reader had **no production
caller**: `git grep -n 'record_read import' -- tortoise/ tests/` found its own
test and nothing else. A fetch the answer path cannot reach is not a path.

This module is the caller, and ``python -m tortoise record-text`` is its
operator verb (a sibling of ``record-reconcile`` and ``record-restore``: same
DSN resolution, same exit-code discipline, same *"no verdict is not a pass"*
rule). It reads the **record only** — not the journal, not the graph — which is
what makes it usable in the one situation §9.4 exists for: *the graph is gone
and the evidence still has to come back*.

THE REFUSAL THAT MATTERS: "absent" is not "unreadable"
======================================================
A tenancy-filtered read returns **0 rows silently**. Under the app role (#9600,
R6) with no tenant GUC — or behind a transaction-mode pooler that dropped it —
the narrative table reads as empty for a session that HAS one (§9555's
integration note G1). Reporting that as *"the record holds no text for this
session"* would be the worst answer this verb could give: it is a false absence
about a customer's evidence, and it is indistinguishable from a mistyped
``--org``/``--graph``.

So a miss is PROVEN before it is reported: the verb reads the scope row itself
(``public.graphs``) and returns :data:`EXIT_UNAVAILABLE` — *no verdict* — when
the scope cannot be read. ``EXIT_ABSENT`` means the record was READABLE and held
nothing, which is the only shape in which an absence is an answer.

WHAT THIS IS NOT
================
It does not write, reconcile, or restore — the write path stays
``record_writer``'s, the comparison ``record-reconcile``, the recovery
``record-restore``. It exposes no MCP/SDK surface: the hosted answer path's own
verbatim fetch is a surface change and belongs to whoever holds that decision
(`AGENTS.md` → *MCP/SDK Surface Approval*), not to a read verb landing here.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from tortoise import record_read
from tortoise.record_read import SessionText

__all__ = [
    "EXIT_ABSENT",
    "EXIT_SERVED",
    "EXIT_UNAVAILABLE",
    "LOG_NAME",
    "TextOutcome",
    "log_result",
    "record_scope_readable",
    "serve_from_record",
]

LOG_NAME = "tortoise.record_text"

#: The text was read from the record and is being served.
EXIT_SERVED = 0
#: No verdict was produced: no/invalid DSN, ``psycopg2`` absent, an unreadable
#: record, or a scope this role cannot see. Never reported as an absence.
EXIT_UNAVAILABLE = 2
#: The record was READABLE for this scope and holds no §9.1 text for the key.
#: An honest absence — the distinction from :data:`EXIT_UNAVAILABLE` is the
#: whole point of :func:`record_scope_readable`.
EXIT_ABSENT = 3

#: How the key was resolved, so a served answer names its own door.
BY_SESSION = "session"
BY_SOURCE = "source-url"


@dataclass(frozen=True)
class TextOutcome:
    """What the record served for one key — the measurement, not a plan.

    ``turns`` is the count actually read (and is NOT capped by a display bound:
    ``shown`` says how many were rendered, so ``--limit`` can never make a
    served answer understate the transcript it holds).
    """

    key: str
    resolved_by: str
    session: SessionText | None = None
    shown: int = 0
    limit: int | None = None

    @property
    def served(self) -> bool:
        return self.session is not None

    @property
    def turns(self) -> int:
        return len(self.session.turns) if self.session is not None else 0

    def as_dict(self) -> dict[str, Any]:
        sess = self.session
        return {
            "key": self.key,
            "resolved_by": self.resolved_by,
            "served": self.served,
            "session_id": sess.session_id if sess is not None else None,
            "narrative": sess.narrative if sess is not None else None,
            "summary": sess.summary if sess is not None else None,
            # §3: the column keeps the JOURNAL key's name (`capture_redactions`)
            # and this is R4's count (the one R7's indicator (b) must reproduce).
            "redactions": sess.redactions if sess is not None else None,
            "turn_count": sess.turn_count if sess is not None else None,
            "turns": self.turns,
            "shown": self.shown,
            "verbatim_available": bool(sess and sess.verbatim_available),
        }

    def render(self) -> str:
        """The human form: the narrative, then the verbatim turns.

        Deliberately the ORDER §9.4 asks for — the narrative is §9.1's prose
        form and the transcript is what it was derived from, so a reader gets
        the claim and then the evidence, in one screenful.
        """
        if self.session is None:
            return (f"no §9.1 text in the record for {self.resolved_by} "
                    f"{self.key!r} (session {self.key!r})")
        sess = self.session
        head = [
            f"session: {sess.session_id}",
            f"redactions: {sess.redactions if sess.redactions is not None else '—'}",
            f"turn_count: {sess.turn_count if sess.turn_count is not None else '—'}",
            f"turns in record: {self.turns}",
        ]
        body = ["", "── narrative (§9.1) ──", sess.narrative or "(none recorded)"]
        if sess.summary:
            body += ["", "── summary ──", sess.summary]
        body += ["", "── transcript (§9.4 verbatim) ──"]
        turns = sess.turns[: self.shown]
        body += [t.line for t in turns] or ["(none recorded)"]
        if self.limit is not None and self.turns > self.shown:
            body.append(f"… {self.turns - self.shown} more turn(s) not shown "
                        f"(--limit {self.limit})")
        return "\n".join(head + body)


def record_scope_readable(conn: Any, *, org_id: str, graph_id: str) -> bool:
    """Can this connection SEE the scope at all — i.e. is a miss an answer?

    The scope row is ``public.graphs``, the tenancy parent every record table
    cascades from. It is read with the SAME ``(org_id, graph_id)`` binding as
    the text lookups, so it is filtered by exactly the predicate that could
    have hidden the text: a role that cannot see the scope cannot be trusted
    to report its absence.

    A read that RAISES propagates: a broken record is not an answer either, and
    :func:`serve_from_record`'s caller classifies it as ``unavailable``.
    """
    cursor = conn.cursor()
    # ⚠️ The scope COLUMN is `graphs.id`, not `graphs.graph_id` — every record
    # table calls it `graph_id` and keys it to `graphs(org_id, id)`, so the two
    # names are one fact spelled twice. A `graph_id` here is an
    # `UndefinedColumn`, which this function's caller reports as *"the scope
    # could not be read"* — a true statement about a typo, and a false one about
    # the record.
    cursor.execute(
        "SELECT 1 FROM public.graphs WHERE org_id = %s AND id = %s",
        (org_id, graph_id),
    )
    return bool(cursor.fetchall())


def serve_from_record(
    conn: Any,
    *,
    org_id: str,
    graph_id: str,
    session_id: str | None = None,
    source_url: str | None = None,
    limit: int | None = None,
) -> TextOutcome:
    """Read §9.1's text for ONE key from the record.

    Exactly one of ``session_id`` / ``source_url`` is the key. ``source_url``
    is §9.4's own shape — *"show me the verbatim text behind this claim"* — and
    resolves through ``public.source.session_id``, a COLUMN, so the link
    survives the graph being destroyed (`record_read.source_narrative`).

    Raises :class:`tortoise.record_read.RecordReadError` for a structurally
    broken record (a turn key that cannot be ordered, a malformed turn row):
    those are failures, never an empty answer.
    """
    if bool(session_id) == bool(source_url):
        raise ValueError(
            "serve_from_record needs exactly one of session_id / source_url — "
            "two keys make the answer's meaning ambiguous and neither makes it "
            "unanswerable"
        )
    if session_id:
        session = record_read.session_text(
            conn, org_id=org_id, graph_id=graph_id, session_id=session_id)
        key, resolved_by = session_id, BY_SESSION
    else:
        session = record_read.source_narrative(
            conn, org_id=org_id, graph_id=graph_id, url=str(source_url))
        key, resolved_by = str(source_url), BY_SOURCE
    shown = len(session.turns) if session is not None else 0
    if limit is not None and session is not None:
        shown = min(shown, limit)
    return TextOutcome(key=key, resolved_by=resolved_by, session=session,
                       shown=shown, limit=limit)


def exit_code_for(outcome: TextOutcome) -> int:
    """``0`` served · ``3`` readable-and-absent · ``2`` no verdict produced."""
    return EXIT_SERVED if outcome.served else EXIT_ABSENT


def log_result(outcome: TextOutcome,
               logger: logging.Logger | None = None) -> None:
    """One structured line per fetch — a served answer is an EVENT.

    The counts are the ones R7's indicator (b) measures: the narrative's
    presence, the redaction count and the transcript size, all read from the
    record. A fetch that served nothing logs at INFO with ``served=False``;
    the operator's verdict comes from the exit code, never from a log level.
    """
    log = logger or logging.getLogger(LOG_NAME)
    payload = outcome.as_dict()
    payload.pop("narrative", None)   # never put a customer's prose in a log line
    payload.pop("summary", None)
    log.info("record-text %s", json.dumps(payload, sort_keys=True, default=str))

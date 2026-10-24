"""#9555 / R7 — §9.1's narrative and the raw turn text have a home in the WRITE PATH.

THE ITEM, AND THE TWO HALVES THAT WERE STILL MISSING
====================================================
`docs/architecture/STORAGE-ARCHITECTURE.md` §9.1 places the S1 narrative *"as
text in Postgres, together with the raw agent-session transcript it was derived
from — not in the graph, not a `Document` node, not a `Point`"* (§4.1's owner
quote: *"the narrative is not something we're suggesting to store in the graph
(same as raw) but store in supabase"*). §9.4 makes the link load-bearing: *"at
answer time, send the source's VERBATIM text, not the extracted claim"*
(measured 15.9 / 22.0 pts).

`#9638` landed the HOME (`public.session_narrative`), the shape-keyed projection
and the record-only reader, and stated its own remainder. Measured at
`f86ae965b` — this file's head — that remainder was exactly two things:

1. **(b) no journal carrier.** The narrative was written to the graph by RAW
   CYPHER (`hosted_api._execute_commit_writes`, `s.story_arc=$arc`), and raw
   Cypher is not a journal record (§17.1's unjournalled-writer ledger), so a live
   capture landed `session_narrative` with its two COUNTS filled and its two TEXT
   columns NULL. A record-sourced rebuild could reproduce the rows, not the
   narrative.
2. **(c) no production caller.** `tortoise/record_read.py` existed and
   `git grep -n 'record_read import' -- tortoise/ tests/` found its own test and
   nothing else, so §9.4's fetch had a reader and no path.

WHAT THIS FILE PINS, AND THE ONE THING EACH PIN IS FOR
=====================================================
§1 the CARRIER exists in the write path, carries the right keys, and carries
   NOTHING when there is no narrative to carry. A producer that emits an empty
   record per capture is a no-op line on the journal forever.
§2 the RECORD EFFECT: the carrier's payload lands §9.1's narrative in
   `public.session_narrative` through the SHAPE-KEYED projection, the two
   carriers (narrative vs counts) cannot clobber each other in EITHER arrival
   order, and the type is dispatched by no graph fold — which is the §9.1 point,
   not an omission (§1's `_NO_PROJECTION_FOLD` membership is the declaration).
§3 the READ PATH is record-only, and the verb's 3-versus-2 split is real: an
   unreadable SCOPE can never be reported as an absent SESSION, because a
   tenancy-filtered read returns 0 rows SILENTLY (§9555's G1, #9600's app role).
§4 the LIVE legs, against a real Postgres (`TEST_RECORD_DB_URI`, armed in CI by
   the `test-record-postgres` job, #9551): a journal replay produces the
   narrative + the redaction count in the record, the record serves §9.4's text,
   and — (b)'s own words — **deleting the derived cache and rebuilding from the
   record alone reproduces the same narrative.**

⛔ WHAT THIS DOES NOT CLAIM. The record's DSN is still unarmed in production
(R1, #9548), so "a live hosted capture lands the text" is proven here against a
journal the producer's own payload shape builds, not against a deployed
capture. The hosted answer path's verbatim fetch (an MCP/SDK SURFACE verb) is
deliberately NOT changed: `AGENTS.md` → *MCP/SDK Surface Approval* puts that
behind Daniel's sign-off, so this lands the operator verb
(`python -m tortoise record-text`) instead, and says so rather than implying the
surface moved.
"""
from __future__ import annotations

import ast
import json
import os
import uuid
from pathlib import Path

import pytest

from tortoise import hosted_api, record_read, record_text
from tortoise import record_projection as rp

REPO_ROOT = Path(__file__).resolve().parents[1]
RECORD_DB_ENV = "TEST_RECORD_DB_URI"

#: The narrative this file carries end to end. Deliberately prose with a
#: newline and a quote in it: a carrier that round-trips through a JSON envelope
#: and a text column must not normalise, escape or truncate it, and a
#: single-line fixture cannot tell.
NARRATIVE = (
    "The owner opened with the pricing question, then ruled that a source is\n"
    "a Source — \"not a Document node\" — and the capture followed."
)
SUMMARY = "owner rules sources are Sources"
SESSION = "sess-9555-narrative"


def _carrier(**over):
    kwargs = dict(session_id=SESSION, event_id="evt-9555", captured_at="2026-10-24T00:00:00Z")
    kwargs.update(over)
    return hosted_api._narrative_carrier(NARRATIVE, SUMMARY, **kwargs)


def _narrative_record():
    return {"type": "SessionNarrated", "ts": "2026-10-24T00:00:00Z", **_carrier()}


def _counts_record(redactions=3, turns=2):
    return {"type": "SessionRecorded", "id": SESSION, "ts": "2026-10-24T00:00:00Z",
            "capture_redactions": redactions, "turn_count": turns, "is_episodic": True}


# ── §1 the carrier in the write path ────────────────────────────────────────

def test_the_carrier_names_its_session_and_carries_the_narrative():
    """The payload the shape-keyed projection reads, key by key.

    `sessionId` is one of the two spellings the projection accepts (the commit's
    own; the capture's is `session_id`), and `story_arc` is §9.1's narrative
    under the graph's own name for it — the SAME key the raw Cypher writes one
    statement earlier, so the record and the cache state one fact.
    """
    carrier = _carrier()
    assert carrier is not None
    assert carrier["sessionId"] == SESSION
    assert carrier["story_arc"] == NARRATIVE
    assert carrier["summary"] == SUMMARY
    assert carrier["id"] == "evt-9555"


@pytest.mark.parametrize("empty", ["", "   ", "\n\t ", None])
def test_an_empty_narrative_EMITS_NOTHING(empty):
    """`story_arc` is `""` for a capture whose distillation produced no narrative.

    Emitting then would journal a record whose entire payload the narrative home
    REFUSES (a blank narrative is not §9.1 prose) — one no-op line per such
    commit, forever, plus a `journal_event` row claiming a capture's narrative
    that does not exist.
    """
    assert hosted_api._narrative_carrier(
        empty, SUMMARY, session_id=SESSION, event_id="e", captured_at="t") is None


def test_the_commit_path_emits_the_carrier_AFTER_the_write_it_describes():
    """§17.1's APPLY-FIRST lane, pinned structurally.

    The raw `MERGE (s:Source …) SET s.story_arc=$arc` is not journalled, so the
    record states the write that ALREADY landed — it must therefore appear after
    it. Emitting before the write would state a narrative the commit may never
    have stored (the write is fail-closed below), i.e. a record that outlives its
    own fact. Line ordering is the checkable half of "apply-first"; the direction
    is what a reviewer cannot otherwise see.
    """
    src = (REPO_ROOT / "tortoise" / "hosted_api.py").read_text()
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_execute_commit_writes"),
              None)
    assert fn is not None, "the commit write phase is gone — re-measure #9555"
    merge_line = min((n.lineno for n in ast.walk(fn)
                      if isinstance(n, ast.Constant) and isinstance(n.value, str)
                      and "s.story_arc=$arc" in n.value), default=None)
    emit_line = min((n.lineno for n in ast.walk(fn)
                     if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Attribute)
                     and n.func.attr == "_emit_event"
                     and n.args
                     and isinstance(n.args[0], ast.Name)
                     and n.args[0].id == "NARRATIVE_RECORD_TYPE"), default=None)
    assert merge_line is not None, (
        "the transcript `:Source` MERGE (`s.story_arc=$arc`) is gone — the "
        "carrier's anchor moved and this pin must be re-derived, not deleted")
    assert emit_line is not None, (
        "the commit path no longer emits the narrative carrier — that is "
        "exactly the #9555 (b) gap returning: the record keeps the COUNTS and "
        "loses the TEXT")
    assert emit_line > merge_line, (
        f"the carrier is emitted at line {emit_line}, BEFORE the write it "
        f"describes (line {merge_line}) — §17.1's apply-first lane states the "
        "write's own result, so the record must follow it")


# ── §2 the record effect, through the shape-keyed projection ────────────────

def _narrative_statements(records):
    return [s for s in rp.replay_plan(records, org_id="org", graph_id="graph")
            if rp.NARRATIVE_TABLE in s.sql]


def test_the_carrier_lands_the_narrative_and_its_summary():
    stmts = _narrative_statements([_narrative_record()])
    assert len(stmts) == 1, "the secondary projection did not fire for the carrier"
    stmt = stmts[0]
    assert "INSERT INTO public.session_narrative" in stmt.sql
    assert NARRATIVE in stmt.params, "the narrative text did not reach the row"
    assert SUMMARY in stmt.params, "`summary` is projected only alongside the narrative"
    # §9.1: the narrative has no graph home, so the type materialises NO node —
    # declared, not merely unobserved.
    assert not any("node_identity" in s.sql for s in
                   rp.replay_plan([_narrative_record()], org_id="o", graph_id="g"))


def _set_clause(stmt) -> str:
    """The COLUMN half of one upsert's `DO UPDATE SET` — the columns it OWNS.

    The `props` merge is cut off: every upsert names the table, so a bare
    substring search for a column name is answered by the table name on the
    statement that does NOT own it (a false pass of the exact clobber test this
    helper exists for).
    """
    tail = stmt.sql.split("DO UPDATE SET", 1)[1]
    return tail.split(", props =", 1)[0]


def test_the_two_carriers_cannot_clobber_each_other_in_EITHER_order():
    """The narrative record owns the TEXT, `SessionRecorded` owns the COUNTS.

    Each upsert names only its own columns, so a replay's result cannot depend
    on arrival order. The failure this prevents is silent and total: a
    whole-column write would erase whichever half landed first, and the reader
    would answer "no narrative" for a session the record holds one for.
    """
    for order in ([_narrative_record(), _counts_record()],
                  [_counts_record(), _narrative_record()]):
        stmts = _narrative_statements(order)
        assert len(stmts) == 2, f"{len(stmts)} statement(s) for the two carriers"
        owns_text = [s for s in stmts
                     if "narrative = EXCLUDED.narrative" in _set_clause(s)]
        owns_counts = [s for s in stmts
                       if "capture_redactions = EXCLUDED.capture_redactions"
                       in _set_clause(s)]
        assert len(owns_text) == 1, "exactly one carrier owns the narrative column"
        assert len(owns_counts) == 1, "exactly one carrier owns the count columns"
        # The clobber test itself: neither upsert may touch the other's half.
        assert "capture_redactions" not in _set_clause(owns_text[0])
        assert "narrative" not in _set_clause(owns_counts[0])
        assert "turn_count" not in _set_clause(owns_text[0])


def test_a_count_the_column_cannot_hold_writes_NO_row_and_no_raise():
    """#9264's shape refusal, one table over — and it must stay fail-open.

    `session.capture_redactions` was `jsonb` (an object with a `total`, not an
    integer), and `_NARRATIVE_INT_KEYS` are `INTEGER` here. A producer that
    still emits the object shape must not be able to FAIL the narrative home's
    write — the secondary projection is additive, so a raise here would turn it
    into a new way for a live capture to die. It is an ABSENCE instead.
    """
    unrepresentable = {"type": "SessionRecorded", "id": SESSION,
                       "capture_redactions": {"total": 7}, "turn_count": 2}
    # The representable half still lands (`turn_count` fits) — the shape half
    # is dropped, never coerced and never raised.
    stmts = _narrative_statements([unrepresentable])
    assert len(stmts) == 1
    # ⚠️ Written as the RESET the projection documents ("carried but
    # unrepresentable ⇒ no count stated"), never as an absent column and never
    # as a coerced 7.
    assert "capture_redactions = NULL" in _set_clause(stmts[0])
    assert 7 not in stmts[0].params
    # A record whose WHOLE payload is unrepresentable contributes nothing at
    # all: an all-NULL insert would read as "session known, no text", a claim
    # no record made.
    assert _narrative_statements([{"type": "SessionRecorded", "id": SESSION,
                                   "capture_redactions": {"total": 7}}]) == []


# ── §3 the read path: record-only, and a miss is PROVEN ─────────────────────

def test_the_read_path_touches_no_journal_and_no_graph():
    """§9.4's fetch must survive the graph, so it may not read it.

    An import of `tortoise.log` or the cache reader here would be a slow,
    invisible coupling: it would work in every test and fail exactly when the
    graph is gone — the one situation the path exists for.
    """
    for module in (record_text, record_read):
        tree = ast.parse(Path(module.__file__).read_text())
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        forbidden = {m for m in imported
                     if m in {"tortoise.log", "tortoise.cache_projection",
                              "tortoise.projection", "tortoise.rebuild"}
                     or m.startswith("tortoise.projection.")}
        assert not forbidden, (
            f"{module.__name__} imports {sorted(forbidden)} — §9.4's fetch would "
            "then depend on the store it is supposed to outlive")


def test_the_verb_is_WIRED_to_the_composition():
    """An import-only pin is satisfied by a body that returns a constant.

    The `record-reconcile` lesson: the verb must CALL `serve_from_record`, and
    the dispatcher must route `record-text` to it. Both are pinned on the AST,
    not on a substring of the source.
    """
    tree = ast.parse((REPO_ROOT / "tortoise" / "__main__.py").read_text())
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_cmd_record_text"),
              None)
    assert fn is not None, "`record-text`'s command function is gone"
    called = {n.func.id for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "serve_from_record" in called, (
        "`_cmd_record_text` does not call `serve_from_record` — the verb would "
        "be an import with a story")
    assert "record_scope_readable" in called, (
        "the verb lost its scope proof — a tenancy-filtered read would then be "
        "reported as an honest absence (the G1 defect)")
    routes = [n for n in ast.walk(tree)
              if isinstance(n, ast.Compare)
              and isinstance(n.left, ast.Attribute) and n.left.attr == "cmd"
              and any(isinstance(c, ast.Constant) and c.value == "record-text"
                      for c in n.comparators)]
    assert routes, "`record-text` is not dispatched in main()"


class _Cursor:
    def __init__(self, rows):
        self._rows = rows
        self.queries = []

    def execute(self, sql, params=None):
        self.queries.append((sql, params))

    def fetchall(self):
        return list(self._rows)


class _Conn:
    """The smallest record connection: answers per statement substring."""

    def __init__(self, answers):
        self.answers = answers
        self.seen = []

    def cursor(self):
        conn = self

        class _C(_Cursor):
            def execute(self, sql, params=None):
                conn.seen.append((sql, params))
                for needle, rows in conn.answers:
                    if needle in sql:
                        self._rows = rows
                        return
                self._rows = []
        return _C([])


def test_a_tenancy_filtered_scope_is_NOT_an_absent_session():
    """The G1 refusal: 0 rows silently is indistinguishable from "none written".

    `serve_from_record` on a readable scope with no text is an ABSENCE (exit 3).
    The SCOPE read is what makes that an answer: without it, the same empty
    result would be reported for a mistyped `--org` and for a role the tenant
    policy filtered — a false absence about a customer's evidence.
    """
    empty = _Conn([("session_narrative", []), ("public.point", []), ("public.graphs", [])])
    assert record_text.record_scope_readable(empty, org_id="o", graph_id="g") is False
    assert record_text.serve_from_record(
        empty, org_id="o", graph_id="g", session_id=SESSION).session is None
    # The composition itself reports the absence; the VERB turns an unreadable
    # scope into `unavailable` (2) — pinned by the wiring test above, and by
    # `record_text.EXIT_UNAVAILABLE` being a distinct code from EXIT_ABSENT.
    assert record_text.EXIT_ABSENT != record_text.EXIT_UNAVAILABLE


def test_the_exit_code_for_a_served_and_an_absent_answer():
    served = record_text.TextOutcome(
        key=SESSION, resolved_by=record_text.BY_SESSION,
        session=record_read.SessionText(session_id=SESSION, narrative=NARRATIVE))
    absent = record_text.TextOutcome(key=SESSION, resolved_by=record_text.BY_SESSION)
    assert record_text.exit_code_for(served) == record_text.EXIT_SERVED
    assert record_text.exit_code_for(absent) == record_text.EXIT_ABSENT
    assert served.as_dict()["narrative"] == NARRATIVE
    assert served.as_dict()["served"] is True
    assert absent.as_dict()["served"] is False


def test_both_keys_or_neither_is_refused():
    conn = _Conn([])
    for kwargs in ({}, {"session_id": SESSION, "source_url": "u"}):
        with pytest.raises(ValueError):
            record_text.serve_from_record(conn, org_id="o", graph_id="g", **kwargs)


# ── §4 LIVE: the record-sourced rebuild reproduces the narrative ────────────

def _record_db():
    """A connection to a Postgres carrying the record migrations, or skip.

    The SAME arming surface as the record's other Postgres legs
    (`tests/test_9078_record_written_by_production_path.py`): the
    `test-record-postgres` CI job provisions a throwaway Postgres and exports
    `TEST_RECORD_DB_URI`, and its leg gate fails the job when a leg SKIPS
    (#9551) — an unarmed job and an armed-but-skipping one are otherwise
    indistinguishable from outside.
    """
    dsn = os.environ.get(RECORD_DB_ENV)
    if not dsn:
        pytest.skip(f"{RECORD_DB_ENV} not set — no Postgres for the R7 proof")
    psycopg2 = pytest.importorskip("psycopg2",
                                   reason="psycopg2 is the record's driver")
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    return conn


def _ensure_schema(conn) -> None:
    from test_9078_record_written_by_production_path import _ensure_record_schema
    _ensure_record_schema(conn)


def _replay_db():
    """The connection the REPLAY runs on — the app role, inside a transaction.

    Two properties, both enforced rather than conventional (`#9600` review
    cycles 1/2, R6 #9554), and both of which `_record_db()` deliberately lacks:

    * the APPLICATION role (`tortoise_record_app`, NOBYPASSRLS) — `replay()`
      REFUSES a connection whose `current_user` can bypass RLS, and the
      provisioning superuser is every bypass at once;
    * NOT autocommit — the tenant GUC is transaction-local and read back before
      the plan, so on an autocommit connection the set is gone by the read-back
      and the replay is refused. `replay()` states the CALLER owns the
      transaction; a non-autocommit connection is its supported shape.

    Reused from the record's other Postgres legs rather than re-derived: the
    `options=-c role=…` construction has its own measured gotcha (`quote_via`,
    because libpq does not decode `+` as a space), and a second copy would
    eventually miss it.
    """
    from test_9078_record_written_by_production_path import _record_replay_db
    return _record_replay_db()


def _tenant() -> tuple[str, str]:
    """A FRESH `(org_id, graph_id)` pair for this run.

    Both halves are unique, and that is not tidiness: `graphs.id` is the
    PRIMARY KEY, so a constant `graph_id` collides across runs with a different
    `org_id` and an `ON CONFLICT DO NOTHING` then leaves the new scope with NO
    graph row — which the narrative's FK reports as *"Key is not present in
    table graphs"*, i.e. a confusing failure in a fixture rather than in the
    thing under test.
    """
    tag = uuid.uuid4().hex[:8]
    return f"org-{tag}", f"g-{tag}"


def _seed(conn, *, org_id: str, graph_id: str) -> None:
    """Journal-replay the capture's records into a FRESH tenant scope.

    A NEW `org_id`/`graph_id` pair per run, so a re-run (or a sibling lane on
    the same container) cannot inherit a previous narrative and pass on it —
    the isolation is the tenant, which is what the record's own key shape gives.
    """
    # The record's tenancy parents: `organizations(id)` and `graphs(org_id, id)`.
    # `session_narrative`'s FK is `(org_id, graph_id) -> graphs(org_id, id)`, so
    # the scope row must exist before a narrative can land — the contract
    # #9638's migration chose (the graph is the anchor, deliberately NOT the
    # session row).
    #
    # The tenant rows go in on the SEEDING connection (the provisioning
    # superuser must see every tenant); the REPLAY goes in on the app role, in
    # one transaction — see `_replay_db`.
    conn.cursor().execute(
        "INSERT INTO public.organizations (id, name) VALUES (%s, %s) "
        "ON CONFLICT DO NOTHING", (org_id, org_id))
    conn.cursor().execute(
        "INSERT INTO public.graphs (id, org_id, name) VALUES (%s, %s, %s) "
        "ON CONFLICT DO NOTHING", (graph_id, org_id, graph_id))
    replay_conn = _replay_db()
    try:
        records = [
            # The turn rows §9.4's verbatim text comes from (`{session}_t{i}`),
            # as the capture's own `_write_capture_turns` journals them.
            {"type": "PointAdded", "id": f"{SESSION}_t0",
             "ts": "2026-10-24T00:00:00Z",
             "point": {"id": f"{SESSION}_t0",
                       "content": "[user] what does a source hold?",
                       "pointKind": "event", "speaker": "user",
                       "is_episodic": True, "status": "draft",
                       "createdAt": "2026-10-24T00:00:00Z", "embedding": None}},
            {"type": "PointAdded", "id": f"{SESSION}_t1",
             "ts": "2026-10-24T00:00:01Z",
             "point": {"id": f"{SESSION}_t1",
                       "content": "[assistant] the narrative, plus its raw turns.",
                       "pointKind": "event", "speaker": "assistant",
                       "is_episodic": True, "status": "draft",
                       "createdAt": "2026-10-24T00:00:01Z", "embedding": None}},
            _narrative_record(),
            _counts_record(redactions=4, turns=2),
        ]
        rp.replay(records, replay_conn, org_id=org_id, graph_id=graph_id)
        replay_conn.commit()
    except Exception:
        replay_conn.rollback()
        raise
    finally:
        replay_conn.close()


def test_the_public_capture_path_lands_the_narrative_in_the_record():
    """(b), first half: a journal replay puts §9.1's text in the record.

    Measured pre-fix: the same run left `narrative` NULL, because no record
    carried `story_arc` at all. The redaction count is the second indicator —
    R4's `capture_redactions`, reproduced from the record, not asserted from the
    journal.
    """
    conn = _record_db()
    try:
        _ensure_schema(conn)
        org_id, graph_id = _tenant()
        _seed(conn, org_id=org_id, graph_id=graph_id)

        outcome = record_text.serve_from_record(
            conn, org_id=org_id, graph_id=graph_id, session_id=SESSION)
        assert outcome.served, "the record holds no §9.1 text after a replay"
        assert outcome.session.narrative == NARRATIVE
        assert outcome.session.redactions == 4
        assert outcome.session.turn_count == 2
        assert outcome.session.summary == SUMMARY
        # §9.1's "raw transcript" comes back from the record's OWN turn rows —
        # never a second copy of the text.
        assert [t.body for t in outcome.session.turns] == [
            "what does a source hold?", "the narrative, plus its raw turns."]
        assert outcome.session.transcript.startswith("user: what does a source hold?")
    finally:
        conn.close()


def test_the_public_capture_path_serves_S9_4_text_by_SOURCE_URL():
    """(c): §9.4's shape — "show me the verbatim text behind this claim".

    The link is `public.source.session_id`, a COLUMN, so it resolves with the
    graph destroyed. This is the arm the ask path needs and did not have.
    """
    conn = _record_db()
    try:
        _ensure_schema(conn)
        org_id, graph_id = _tenant()
        _seed(conn, org_id=org_id, graph_id=graph_id)
        url = f"session:{SESSION}"
        # The `:Source` row with the link §9.4 resolves through. Written here
        # rather than by the projection on purpose: this leg is about the READ
        # arm, and the WRITE arm for a source row already exists
        # (`SourceCreated`), so re-proving it would blur what failed.
        # `public.source` is a NODE table: its row is anchored by
        # `node_identity (org_id, graph_id, node_label, node_key)`, so the
        # identity row comes first.
        conn.cursor().execute(
            "INSERT INTO public.node_identity (org_id, graph_id, node_label, "
            "node_key) VALUES (%s, %s, 'Source', %s) "
            "ON CONFLICT DO NOTHING", (org_id, graph_id, url))
        conn.cursor().execute(
            "INSERT INTO public.source (org_id, graph_id, node_label, node_key, "
            "id, url, session_id) VALUES (%s, %s, 'Source', %s, %s, %s, %s) "
            "ON CONFLICT (org_id, graph_id, node_key) DO UPDATE "
            "SET session_id = EXCLUDED.session_id",
            (org_id, graph_id, url, url, url, SESSION))

        outcome = record_text.serve_from_record(
            conn, org_id=org_id, graph_id=graph_id, source_url=url)
        assert outcome.served
        assert outcome.resolved_by == record_text.BY_SOURCE
        assert outcome.session.narrative == NARRATIVE
        assert outcome.session.redactions == 4
    finally:
        conn.close()


def test_the_public_capture_path_serves_S9_4_text_through_the_VERB(tmp_path):
    """(c), end to end: `python -m tortoise record-text` IS the fetch path.

    The library legs above prove the reader; this one proves an OPERATOR can
    reach it, which is the whole difference between a reader and a path (the
    measured #9555 defect was a reader with no caller). Exit codes are asserted
    explicitly, because they are the operator's next step:

    * `0` and the text for a session the record holds,
    * `3` for a session it does not — a READABLE scope, so an honest absence,
    * `2` for a scope this connection cannot see — never an absence.

    The `--json` shape is asserted on the keys R7's indicators are measured
    from (`narrative`, `redactions`, `turns`), so a rendering change cannot
    quietly drop one of them.
    """
    import subprocess
    import sys

    dsn = os.environ.get(RECORD_DB_ENV)
    if not dsn:
        pytest.skip(f"{RECORD_DB_ENV} not set — no Postgres for the R7 verb proof")
    pytest.importorskip("psycopg2", reason="psycopg2 is the record's driver")
    conn = _record_db()
    try:
        _ensure_schema(conn)
        org_id, graph_id = _tenant()
        _seed(conn, org_id=org_id, graph_id=graph_id)
    finally:
        conn.close()

    def _run(*extra):
        return subprocess.run(
            [sys.executable, "-m", "tortoise", "record-text",
             "--record-dsn", dsn, "--org", org_id, "--graph", graph_id, *extra],
            capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=180)

    served = _run("--session", SESSION, "--json")
    assert served.returncode == 0, served.stderr
    payload = json.loads(served.stdout)
    assert payload["served"] is True
    assert payload["narrative"] == NARRATIVE
    assert payload["redactions"] == 4
    assert payload["turn_count"] == 2
    assert payload["turns"] == 2
    assert payload["verbatim_available"] is True

    # A human-run form: the narrative and the verbatim turns, in one screenful.
    rendered = _run("--session", SESSION)
    assert rendered.returncode == 0, rendered.stderr
    assert NARRATIVE.splitlines()[0] in rendered.stdout
    assert "user: what does a source hold?" in rendered.stdout

    # `--limit` bounds the RENDERING only: the reported count is the record's.
    bounded = _run("--session", SESSION, "--limit", "1", "--json")
    assert bounded.returncode == 0, bounded.stderr
    assert json.loads(bounded.stdout)["turns"] == 2
    assert json.loads(bounded.stdout)["shown"] == 1

    absent = _run("--session", "no-such-session", "--json")
    assert absent.returncode == 3, absent.stderr
    assert json.loads(absent.stdout)["served"] is False

    # A scope that does not exist is NOT reported as an absent session: this
    # verb cannot tell a mistyped scope from a role the tenancy policy filtered,
    # and it says so instead of answering (the #9555 G1 hazard).
    unseen = subprocess.run(
        [sys.executable, "-m", "tortoise", "record-text", "--record-dsn", dsn,
         "--org", "org-does-not-exist", "--graph", "g", "--session", SESSION],
        capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=180)
    assert unseen.returncode == record_text.EXIT_UNAVAILABLE, unseen.stderr
    assert "not visible" in unseen.stderr

    # Both keys (or neither) is refused before anything is read.
    both = _run("--session", SESSION, "--source-url", "u")
    assert both.returncode == record_text.EXIT_UNAVAILABLE
    assert "exactly one" in both.stderr

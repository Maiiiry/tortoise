"""#1727 Slice 2 (Task 15) — per-harness session-store parsers (T2 backfill).

Each parser reads a harness's session file and returns the canonical
conversation shape POSTed to ``/v1/sessions``:

    [{"role": "user" | "assistant", "content": "<text>"}, ...]

Parsers are PURE functions over the file — no network, no writes — so a
re-parse of the same file is byte-identical (idempotency is a property of
the parser; the CLI's content-hash idempotency key + 2xx-only local receipt
are the idempotency/durability layer on top).

Supported stores:
  - ``codex``: Codex CLI session JSONL (``~/.codex/sessions/*.jsonl``).
    Records are ``{"type": "response_item", "payload": {"type": "message",
    "role": ..., "content": [{"type": "input_text"/"output_text", "text":
    ...}]}}``; legacy shapes (``user_message`` / ``assistant_message`` with a
    string ``content``) are tolerated. Tool calls / results are skipped.
  - ``pi``: Pi session JSONL (v2/v3 tree). Records are ``{"type":
    "message", "message": {"role": ..., "content": [...]}}``; every
    non-message entry (session header, ``model_change``,
    ``thinking_level_change``, compaction, …) is skipped. #3667: this is a
    DEDICATED shape branch, not the codex parser the plan aliased it to —
    the alias matched codex's ``payload`` shape and returned 0 turns for
    every real Pi session.
  - ``claude-desktop``: Claude project JSONL (``~/.claude/projects/*/
    *.jsonl`` — the same store Claude Desktop and Claude Code share).
    Records are ``{"message": {"role": ..., "content": <str | parts>}}`` —
    the same shape session-end.sh converts, ported to a shared parser.
  - ``cursor``: Cursor agent transcript JSONL
    (``~/.cursor/projects/<mangled-workspace>/agent-transcripts/<id>/<id>.jsonl``
    or the legacy ``agent-transcripts/<id>.jsonl``).  Records are
    ``{"role": ..., "message": {"content": [{"type": "text", "text": …}]}}``
    — the shape Cursor's ``Tgd``/``ECf`` emit (verified against the installed
    bundle, Cursor 3.20.21).  The optional ``metadata`` header and the
    ``turn_ended`` markers carry no ``role`` and are skipped.
  - ``hermes``: Hermes (NousResearch/hermes-agent) ``$HERMES_HOME/state.db``
    — SQLite ``sessions`` + ``messages`` tables, MANY sessions per file (the
    only many-per-file store here).  ``<state.db>`` selects the most recent
    session; ``<state.db>::<session-id>`` selects one explicitly.  Read
    ``mode=ro`` — the store is never mutated (#2121).
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path

from tortoise.quota import MAX_SESSION_TURNS

_logger = logging.getLogger("tortoise.session_import")

# Roles we keep. Anything else (system, tool, function, model-rollout,
# …) is context noise for the capture surface — skipped, never coerced.
_KEEP_ROLES = {"user", "assistant"}

# Hosted POST /v1/sessions turn cap. This is the bound the HANDLER
# enforces — ``hosted_api._capture_session_impl`` raises HTTP 400 when
# ``len(conversation) > MAX_SESSION_TURNS`` (``tortoise/quota.py``). It is
# NOT ``SessionRequest.conversation``'s ``max_length=1000``: that is only the
# Pydantic boundary, so a payload the model accepts (500 < n <= 1000) still
# 400s at the handler and the backfill writes NO receipt. The live Pi capture
# extension caps at the same bound (``MAX_TURNS`` in
# ``tortoise/pi-hooks/tortoise-capture.ts``), pinned to this constant by
# ``tests/test_pi_capture_hooks.py`` so the two legs cannot drift. Measured on
# the real local Pi corpus (2026-09, 374 files with >=1 turn): 45 files
# (12.0%) exceed 500 turns; 21 (5.6%) exceed 1000 (max 2555).
MAX_TURNS = MAX_SESSION_TURNS


def window_turns(turns: list[dict]) -> tuple[list[dict], int]:
    """Cap a parsed conversation at the hosted turn cap (``MAX_SESSION_TURNS``).

    Keeps the **LAST** ``MAX_TURNS`` turns — recent context is what memory
    wants — and returns ``(windowed, dropped)`` so the caller can report the
    truncation instead of losing turns silently.
    """
    if len(turns) <= MAX_TURNS:
        return turns, 0
    return turns[-MAX_TURNS:], len(turns) - MAX_TURNS


def _text_from_parts(parts) -> str:
    """Flatten an LLM content array (codex input_text/output_text, Claude
    text blocks) into one string. Non-text parts are skipped.

    The STRING branch trims: every caller treats the result as an utterance
    and skips it on falsiness (``if not text``), so an untrimmed
    whitespace-only string was a phantom turn — while the list branch above
    already trimmed each part (#2121 caught it through the Hermes store, but
    it reached the legacy string-shaped codex records too).
    """
    if isinstance(parts, str):
        return parts.strip()
    if not isinstance(parts, list):
        return ""
    out = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = (part.get("text") or part.get("input_text")
                or part.get("output_text"))
        if text:
            out.append(str(text).strip())
    return " ".join(t for t in out if t).strip()


def _walk_codex_records(path: Path, *, role_key: str) -> list[dict]:
    """Shared JSONL walk for the JSONL session stores.

    ``role_key`` selects where the message role lives (codex: the payload's
    ``type == "message"`` record; claude-desktop: the ``message`` sub-object
    of a user/assistant-typed record; pi: the ``message`` sub-object of a
    ``type == "message"`` record).
    Tolerant of malformed lines (skipped, logged at debug) — a single broken
    line must not fail the whole backfill.
    """
    turns: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError(f"cannot read session file {path}: {e}") from e
    for lineno, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            _logger.debug("skip unparsable line %d in %s", lineno, path)
            continue
        role, content = _role_content(rec, role_key)
        if role not in _KEEP_ROLES:
            continue
        text = _text_from_parts(content)
        if not text:
            continue
        turns.append({"role": role, "content": text})
    return turns


def _role_content(rec: dict, role_key: str) -> tuple[str | None, object]:
    """Extract (role, content) from one record.

    codex (role_key="payload"): ``{"type": "response_item", "payload":
    {"type": "message", "role": ..., "content": ...}}``; legacy
    ``{"type": "user_message", "content": ...}`` / ``{"type":
    "assistant_message", ...}`` carry the role in the record type.

    claude-desktop (role_key="message"): ``{"message": {"role": ...,
    "content": ...}}``.
    """
    if role_key == "message":
        # Claude store: the record-level `type` is authoritative when
        # present — only user/assistant records become turns (summary,
        # system, tool-result records are noise). Type ABSENT ⇒ trust the
        # message role (tolerant of minimal hand-written fixtures).
        rtype = rec.get("type") or ""
        if rtype and rtype not in ("user", "assistant"):
            return None, None
        msg = rec.get("message") or {}
        return msg.get("role"), msg.get("content")
    if role_key == "pi":
        # Pi session store (#3667): the message role lives in the `message`
        # sub-object and the record type is the literal "message".
        if (rec.get("type") or "") != "message":
            return None, None
        msg = rec.get("message") or {}
        return msg.get("role"), msg.get("content")
    if role_key == "cursor":
        # Cursor agent transcript (#3819): the ROLE is at the record level and
        # the content parts live under `message`.  The write path emits
        # exactly ``{"role": …, "message": {"content": [...]}}`` (bundle:
        # ``Tgd``), plus a `metadata` header and `turn_ended` markers that
        # carry no role.  Unlike the claude/pi branches the record has no
        # ``type`` to key on, so the role itself is the discriminator.
        role = rec.get("role")
        if not isinstance(role, str) or role not in _KEEP_ROLES:
            return None, None
        msg = rec.get("message")
        if not isinstance(msg, dict):
            return None, None
        return role, msg.get("content")
    # codex path
    rtype = rec.get("type") or ""
    if rtype == "response_item":
        payload = rec.get("payload") or {}
        if payload.get("type") != "message":
            return None, None
        return payload.get("role"), payload.get("content")
    if rtype in ("user_message", "assistant_message"):
        role = rtype.split("_")[0]
        payload = rec.get("payload")
        content = rec.get("content")
        if isinstance(payload, dict) and "content" in payload:
            content = payload["content"]
        return role, content
    return None, None


def parse_codex(path: str | Path) -> list[dict]:
    """Parse a Codex CLI session JSONL into conversation turns."""
    return _walk_codex_records(Path(path), role_key="payload")


def parse_pi(path: str | Path) -> list[dict]:
    """Parse a Pi session JSONL (v2/v3 tree) into conversation turns.

    #3667: Pi's shape is ``{"type": "message", "message": {"role": ...,
    "content": [...]}}`` — a DEDICATED branch, not the codex parser this used
    to be aliased to (the codex parse matched the ``payload`` shape instead
    and silently returned 0 turns for every real Pi session)."""
    return _walk_codex_records(Path(path), role_key="pi")


def parse_claude_desktop(path: str | Path) -> list[dict]:
    """Parse a Claude (Desktop/Code) project JSONL into conversation turns."""
    return _walk_codex_records(Path(path), role_key="message")


def parse_cursor(path: str | Path) -> list[dict]:
    """Parse a Cursor agent transcript JSONL into conversation turns.

    #3819: Cursor's agent transcripts use their own shape
    (``{"role": …, "message": {"content": [parts]}}``, with ``metadata`` and
    ``turn_ended`` markers interleaved) — a DEDICATED branch, not the Claude
    parser, whose ``message.role`` lookup returns nothing for Cursor records
    and would silently file 0 turns for every session."""
    return _walk_codex_records(Path(path), role_key="cursor")


# Hermes `state.db` selector separator: ``<state.db>::<session-id>``.
#
# Hermes is the FIRST harness whose store holds MANY sessions in ONE file — a
# SQLite ``$HERMES_HOME/state.db`` with ``sessions`` + ``messages`` tables
# (verified against hermes-agent @ main, 2026-10-11: `hermes_state.py`
# ``DEFAULT_DB_PATH = get_hermes_home() / "state.db"``; the DDL lives in
# `hermes_state_common.py`). Every other parser here reads a transcript that
# already IS one session (codex/pi/claude/cursor: one file per session), so
# the shared ``parse_transcript(path, harness)`` signature has no session
# argument to carry. The selector keeps that signature intact for the common
# case (``state.db`` alone → the most recent session) and gives the escape
# hatch for a specific one without inventing a new CLI flag or changing the
# parser dispatch contract.
_HERMES_SELECTOR = "::"


def _hermes_split_selector(path: str | Path) -> tuple[Path, str | None]:
    """Split ``<state.db>::<session-id>`` into (db path, session id or None)."""
    raw = str(path)
    db, sep, session_id = raw.rpartition(_HERMES_SELECTOR)
    if not sep or not db:
        return Path(raw), None
    return Path(db), (session_id.strip() or None)


def _hermes_rows(conn, sql: str, params: tuple = ()):
    """Run one read against the store, converting every sqlite failure into
    an honest ``ValueError`` (the CLI surfaces it as a parse failure and
    writes a breadcrumb — never a silent 0-turn import)."""
    import sqlite3
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.DatabaseError as e:
        raise ValueError(f"cannot read Hermes session store: {e}") from e


def parse_hermes(path: str | Path) -> list[dict]:
    """Parse a Hermes `state.db` session into conversation turns.

    Hermes (NousResearch/hermes-agent) keeps its sessions in ONE SQLite file
    — ``$HERMES_HOME/state.db`` (default ``~/.hermes/state.db``), tables
    ``sessions`` (``id``, ``started_at``, ...) and ``messages`` (``session_id``,
    ``role``, ``content``, ``active``, ``_compressed_summary``, ordered by
    ``id`` — ``timestamp`` is NOT monotonic, the store's own reads page by
    ``id``: `hermes_state_messages.py::get_messages`).

    Selection:
      * ``<state.db>`` — the most recently started session
        (``started_at DESC, id DESC``; deterministic ties);
      * ``<state.db>::<session-id>`` — that explicit session.

    Row filter — the LIVE model-visible transcript, which is what a memory
    backfill should capture:
      * ``active = 1`` (``active = 0`` rows are rewound/taken-back turns);
      * ``_compressed_summary = 0`` (post-compaction summary rows are
        GENERATED text, not an utterance — capturing them would file the
        summariser as a user);
      * ``role IN ('user', 'assistant')`` (tool/system/session_meta rows are
        context noise, skipped — never coerced).

    Reads ONLY: the connection is opened ``mode=ro`` so a live/recent Hermes
    store is never mutated (Hermes runs WAL + a cross-process write guard; a
    writable open could create ``-wal``/``-shm`` under it). A WAL store whose
    ``-shm`` has been reaped needs a writable directory to init read-only, so
    quit Hermes before backfilling if SQLite refuses the open — the refusal
    is surfaced honestly, never worked around by opening writable.

    NOT walked: Hermes' compression lineage (``sessions.parent_session_id``,
    the ``include_ancestors`` read in ``get_messages_as_conversation``). A
    rotation after compaction puts the pre-rotation transcript in a parent
    session; this parser reads the one selected session. Filed as #8113 rather
    than silently absorbed.
    """
    import sqlite3

    db_path, wanted_session = _hermes_split_selector(path)
    if not db_path.exists():
        raise ValueError(f"Hermes session store not found: {db_path}")
    if db_path.is_dir():
        candidate = db_path / "state.db"
        if not candidate.exists():
            raise ValueError(
                f"Hermes session store is a directory without a state.db: "
                f"{db_path}")
        db_path = candidate

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as e:
        raise ValueError(
            f"cannot open Hermes session store {db_path} read-only: {e}") from e
    conn.row_factory = sqlite3.Row
    try:
        if wanted_session:
            session_id = wanted_session
            known = _hermes_rows(
                conn, "SELECT 1 FROM sessions WHERE id = ? LIMIT 1",
                (session_id,))
            if not known:
                raise ValueError(
                    f"no Hermes session {session_id!r} in {db_path}")
        else:
            newest = _hermes_rows(
                conn,
                "SELECT id FROM sessions ORDER BY started_at DESC, id DESC "
                "LIMIT 1")
            if not newest:
                raise ValueError(
                    f"no sessions in Hermes session store {db_path}")
            session_id = str(newest[0]["id"])
        rows = _hermes_rows(
            conn,
            "SELECT role, content FROM messages WHERE session_id = ? "
            "AND active = 1 AND _compressed_summary = 0 "
            "AND role IN ('user', 'assistant') ORDER BY id ASC",
            (session_id,))
    finally:
        conn.close()

    turns: list[dict] = []
    for row in rows:
        text = _text_from_parts(row["content"])
        if not text:
            continue
        turns.append({"role": row["role"], "content": text})
    return turns


PARSERS: dict[str, Callable[[str | Path], list[dict]]] = {
    "codex": parse_codex,
    "claude-desktop": parse_claude_desktop,
    "cursor": parse_cursor,
    "hermes": parse_hermes,
    "pi": parse_pi,
}


def parse_transcript(path: str | Path, harness: str) -> list[dict]:
    """Dispatch a session file to its harness parser.

    Raises ValueError for an unknown harness (the CLI surfaces it as an
    honest parse failure — never a silent no-op).
    """
    parser = PARSERS.get(harness)
    if parser is None:
        raise ValueError(
            f"no parser for harness {harness!r} "
            f"(supported: {sorted(PARSERS)})")
    return parser(path)

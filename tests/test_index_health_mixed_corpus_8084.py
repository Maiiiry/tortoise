"""#8084 — the session-index health/sweep surfaces must be CLASSIFICATION-AWARE.

The defect this file pins (measured on the real corpus, 2026-10-11: 5,411 files,
3,040 of them reported `unindexed` after ONE cold `index_directory` run — 56% of
a fully indexed corpus):

* `index_directory` is the canonical corpus writer (epic #900 T8) and it asks
  `classify_file` (§6.2) which identity a file gets. A legacy `type:
  conversation` file with no `sessionId` is a `document` (rule 5), so the writer
  stores a D10 document `:Source` (`doc_<rel>`, `documentKind` set) — NEVER an
  AgentSession `Event`.
* `session_index_health` scored every file against AgentSession Events only, so
  such a file was `unindexed` forever.
* The only documented remedy (`tortoise index sessions` → `reconcile_sessions` →
  the deprecated `ingest_corpus` AgentSession writer) derived
  `session_file_<stem>` and minted an AgentSession Event with **no** inbound
  `references` edge — the same file then carried a corpus `Source` + a document
  `Source` + an orphan Event: two identities for one file.

Both indicators from the issue are asserted here: `unindexed == 0` on a corpus
the canonical writer reports as fully indexed, and no file left with two
identities by the remedy.

Harness conventions (§7): fresh embedded DB per test, no network
(`extract_metadata=False`), graph assertions via raw Cypher on
`sdk._get_proj().g`.
"""
from __future__ import annotations

import os
from pathlib import Path

from tortoise.file_indexer import compute_file_hash
from tortoise.sdk import TortoiseSDK

from tests import concurrency_harness as harness

#: A legacy-shaped conversation file: `type: conversation`, `created`/`updated`,
#: and NO `sessionId` — §6.2 rule 5 declares it a `document` (`created` is not
#: `date`, so the meeting rule does not fire either).
LEGACY_DOC = """\
---
title: {title}
type: conversation
created: "2026-01-01"
updated: "2026-01-02"
---
User: hi
Assistant: hello
"""

SESSION = """\
---
sessionId: {sid}
agent: pi
title: "Session {sid}"
---
## Summary
A real session file.
"""


def _sdk() -> TortoiseSDK:
    return TortoiseSDK(harness.make_db(), namespace="e2e-8084")


def _corpus(tmp_path: Path, docs: int = 2, sessions: int = 1) -> Path:
    c = tmp_path / "corpus"
    c.mkdir()
    for i in range(docs):
        (c / f"legacy-{i}.md").write_text(LEGACY_DOC.format(title=f"Legacy {i}"))
    for i in range(sessions):
        (c / f"sess-{i}.md").write_text(SESSION.format(sid=f"s{i}"))
    return c


def _agent_session_events(g) -> set[str]:
    return {r[0] for r in g.query(
        "MATCH (e:Event {eventKind:'AgentSession'}) RETURN e.eventId"
    ).result_set}


def _orphan_agent_session_events(g) -> int:
    """AgentSession Events no `Source` references — the split-identity shape."""
    return g.query(
        "MATCH (e:Event {eventKind:'AgentSession'}) "
        "WHERE NOT (e)<-[:references]-(:Source) RETURN count(e)"
    ).result_set[0][0]


def _doc_ids(g) -> set[str]:
    return {r[0] for r in g.query(
        "MATCH (s:Source) WHERE s.documentKind IS NOT NULL RETURN s.url"
    ).result_set}


# ── Indicator 1: a fully indexed mixed corpus reports unindexed == 0 ──


def test_health_reports_fully_indexed_mixed_corpus(tmp_path):
    """The issue's target: `index_directory` says everything is indexed, so
    health must agree. Before #8084 this reported `unindexed == 2` (the two
    sessionId-less documents) and stayed there across runs."""
    c = _corpus(tmp_path)
    sdk = _sdk()
    try:
        run = sdk.index_directory(str(c), extract_metadata=False)
        assert run["indexed"] == 3 and run["failed"] == 0, run
        assert run["by_kind"] == {"document": 2, "agentSession": 1}, run

        h = sdk.session_index_health(str(c))
        assert h["unindexed"] == [], h
        assert h["stale"] == [], h
        assert h["matched"] == 3
        assert h["file_count"] == 3
        assert h["by_kind"] == {"document": 2, "agentSession": 1}
        assert h["indexed_documents"] == 2
        assert h["indexed_events"] == 1          # corpus-scoped AgentSessions
        assert h["unindexed_documents"] == []
        assert h["stale_documents"] == []
        # ...and it STAYS clean (the old verdict was permanent)
        assert sdk.session_index_health(str(c))["unindexed"] == []
    finally:
        sdk.close()


def test_health_names_documents_without_any_identity(tmp_path):
    """The mirror case: a document the canonical writer has NOT written is
    genuinely unindexed — and is reported in BOTH the whole-corpus bucket
    (so the doctor arithmetic stays honest) and the document subset (so the
    remedy can name `index directory` instead of minting an Event)."""
    c = tmp_path / "corpus"
    c.mkdir()
    for i in range(2):
        (c / f"legacy-{i}.md").write_text(LEGACY_DOC.format(title=f"Legacy {i}"))
    sdk = _sdk()
    try:
        h = sdk.session_index_health(str(c))
        assert sorted(Path(p).name for p in h["unindexed"]) == [
            "legacy-0.md", "legacy-1.md"]
        assert h["unindexed"] == h["unindexed_documents"]
        assert h["indexed_documents"] == 0
        assert h["by_kind"] == {"document": 2}
        assert h["indexed_events"] == 0
        assert h["matched"] == 0
    finally:
        sdk.close()


def test_health_reports_stale_document_after_edit(tmp_path):
    """A document whose content drifted from its recorded hash is `stale`, not
    `unindexed` — the document Source exists, the hash is what moved. The
    recorded hash lives on the `corpus://` provenance row written in the same
    unit (the D10 document Source carries no hash)."""
    c = _corpus(tmp_path, docs=1, sessions=0)
    sdk = _sdk()
    try:
        sdk.index_directory(str(c), extract_metadata=False)
        target = c / "legacy-0.md"
        target.write_text(LEGACY_DOC.format(title="Legacy 0") + "\nedited after capture\n")
        h = sdk.session_index_health(str(c))
        assert h["unindexed"] == []
        assert [Path(p).name for p in h["stale"]] == ["legacy-0.md"]
        assert h["stale"] == h["stale_documents"]
        assert h["matched"] == 0
    finally:
        sdk.close()


# ── Indicator 2: the remedy leaves no file with two identities ──────


def test_remedy_mints_no_second_identity(tmp_path):
    """The measured reproducer: `index_directory` a mixed corpus, then run the
    documented remedy. Before #8084 the remedy minted `session_file_legacy-*`
    Events with no inbound `references` edge (`Sources 5, Events 3` on the
    3-file corpus) — one file, two identities."""
    c = _corpus(tmp_path)
    sdk = _sdk()
    try:
        sdk.index_directory(str(c), extract_metadata=False)
        g = sdk._get_proj().g
        events_before = _agent_session_events(g)
        docs_before = _doc_ids(g)
        assert events_before == {"session_s0"}
        assert docs_before == {"doc_legacy-0.md", "doc_legacy-1.md"}

        rep = sdk.reconcile_sessions(str(c))
        assert rep["unindexed"] == [] and rep["stale"] == []
        assert rep["session_delta"] == 0 and rep["document_delta"] == 0
        assert rep["reindex"] == {}      # nothing to replay — already converged

        assert _agent_session_events(g) == events_before     # no new Events
        assert _doc_ids(g) == docs_before                    # docs untouched
        assert _orphan_agent_session_events(g) == 0
        # the sweep's own writer is not even asked to walk: no per-file skips
        assert sdk.session_index_health(str(c))["unindexed"] == []
    finally:
        sdk.close()


def test_remedy_indexes_documents_through_the_canonical_writer(tmp_path):
    """A genuinely unindexed DOCUMENT must get its OWN identity from the
    remedy, never a legacy `session_file_<stem>` Event. Before #8084 the
    replay walked the directory through `ingest_corpus(eventKind=
    "AgentSession")` and minted one orphan Event per document — after which
    `index_directory` added the doc Source and the file carried TWO
    identities."""
    c = tmp_path / "corpus"
    c.mkdir()
    (c / "legacy-0.md").write_text(LEGACY_DOC.format(title="Legacy 0"))
    (c / "sess-0.md").write_text(SESSION.format(sid="s0"))
    sdk = _sdk()
    try:
        g = sdk._get_proj().g
        rep = sdk.reconcile_sessions(str(c))
        assert rep["session_delta"] == 1
        assert rep["document_delta"] == 1
        assert rep["reindex"]["ingested"] == 2 and rep["reindex"]["failed"] == 0
        # each file got the identity its classifier asked for, and only that
        assert _agent_session_events(g) == {"session_s0"}
        assert not any(e.startswith("session_file_")
                       for e in _agent_session_events(g))
        assert _doc_ids(g) == {"doc_legacy-0.md"}
        assert _orphan_agent_session_events(g) == 0

        h = sdk.session_index_health(str(c))
        assert h["unindexed"] == [] and h["stale"] == []
        assert h["matched"] == 2
        # ...and a second sweep is a no-op (converged)
        rep2 = sdk.reconcile_sessions(str(c))
        assert rep2["reindex"] == {}
    finally:
        sdk.close()


def test_remedy_keeps_the_corpus_authority_it_found(tmp_path):
    """The replay runs the canonical writer, whose `corpus_name` defaults to
    the directory BASENAME. A corpus indexed with a custom name must not gain
    a second `corpus://` provenance url per file when the sweep re-indexes it
    — that would be another one-file-two-identities split."""
    c = _corpus(tmp_path, docs=1, sessions=1)
    sdk = _sdk()
    try:
        g = sdk._get_proj().g
        sdk.index_directory(str(c), corpus_name="t11_clean",
                            extract_metadata=False)
        urls_before = {r[0] for r in g.query(
            "MATCH (s:Source) WHERE s.url STARTS WITH 'corpus://' "
            "RETURN s.url").result_set}
        assert urls_before == {"corpus://t11_clean/legacy-0.md",
                               "corpus://t11_clean/sess-0.md"}

        # a NEW file arrives → the sweep replays the corpus
        (c / "legacy-1.md").write_text(LEGACY_DOC.format(title="Legacy 1"))
        rep = sdk.reconcile_sessions(str(c))
        assert rep["document_delta"] == 1
        urls_after = {r[0] for r in g.query(
            "MATCH (s:Source) WHERE s.url STARTS WITH 'corpus://' "
            "RETURN s.url").result_set}
        assert urls_after == urls_before | {"corpus://t11_clean/legacy-1.md"}, \
            urls_after
        assert sdk.session_index_health(str(c))["unindexed"] == []
    finally:
        sdk.close()


def test_deprecated_index_sessions_skips_canonical_owned_files(tmp_path):
    """The issue's explicit ask: `index_sessions` (the deprecated sweep over
    `ingest_corpus(eventKind="AgentSession")`) must stop minting AgentSession
    Events for files the canonical path already owns. Legacy behavior for a
    file nothing owns is UNCHANGED (frozen branch, SC4) — the guard is keyed on
    the canonical document identity, not on the classifier."""
    c = _corpus(tmp_path)
    sdk = _sdk()
    try:
        g = sdk._get_proj().g
        sdk.index_directory(str(c), extract_metadata=False)
        events_before = _agent_session_events(g)

        res = sdk.index_sessions(str(c), extract_metadata=False)
        assert res["ingested"] == 0 and res["failed"] == 0, res
        assert _agent_session_events(g) == events_before
        owned = [e for e in res["errors"] if "#8084" in e["error"]]
        assert len(owned) == 2 and not any(e["retryable"] for e in owned)
        assert _orphan_agent_session_events(g) == 0
    finally:
        sdk.close()


def test_index_sessions_unchanged_for_files_nothing_owns(tmp_path):
    """Frozen-legacy guard rail: with NO canonical identity present, the
    deprecated sweep behaves exactly as it always did (it is the legacy
    contract, not a classification gate)."""
    c = tmp_path / "corpus"
    c.mkdir()
    (c / "legacy-0.md").write_text(LEGACY_DOC.format(title="Legacy 0"))
    sdk = _sdk()
    try:
        res = sdk.index_sessions(str(c), extract_metadata=False)
        assert res["ingested"] == 1, res
        assert _agent_session_events(sdk._get_proj().g) == {"session_file_legacy-0"}
    finally:
        sdk.close()


# ── the hash surface the document check rides on ─────────────────────


def test_document_hash_matches_the_writer(tmp_path):
    """The document staleness verdict must use the SAME hash derivation the
    writer stored (§4.5 text mode) — a raw-bytes read would report every
    document stale forever (the #330 non-convergence class)."""
    c = _corpus(tmp_path, docs=1, sessions=0)
    (c / "legacy-0.md").write_bytes(
        LEGACY_DOC.format(title="Legacy 0").replace("\n", "\r\n").encode())
    sdk = _sdk()
    try:
        sdk.index_directory(str(c), extract_metadata=False)
        row = sdk._get_proj().g.query(
            "MATCH (s:Source) WHERE s.contentHash IS NOT NULL "
            "RETURN s.contentHash").result_set
        assert row and row[0][0] == compute_file_hash(str(c / "legacy-0.md"))
        h = sdk.session_index_health(str(c))
        assert h["stale"] == [] and h["unindexed"] == []
        assert h["matched"] == 1
        assert os.path.basename(h["up_to_date"][0]) == "legacy-0.md"
    finally:
        sdk.close()

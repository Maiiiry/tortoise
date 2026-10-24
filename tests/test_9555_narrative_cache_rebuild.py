"""#9555 / R7 — the narrative SURVIVES the derived cache being deleted (b).

WHY THIS IS A SEPARATE FILE FROM `test_9555_narrative_carrier.py`, and it is not
tidiness. (b) has two halves with two different arming requirements:

* the CARRIER + the READ PATH need a Postgres (`TEST_RECORD_DB_URI`) and are
  armed in CI's `test-record-postgres` job — whose leg gate FAILS the job on ANY
  skip (`.github/scripts/record-postgres-leg-gate.py`, #9551), because an
  unarmed job and an armed-but-skipping one are indistinguishable from outside;
* the CACHE SURVIVAL half needs a FalkorDB as well (`TORTOISE_DB_URI`), which
  that job does not have. Keeping it in the same file would therefore make the
  armed job red on a skip it cannot prevent — or, worse, push a reviewer to
  delete the skip and ship a leg that asserts against a cache that was never
  touched.

So: this file costs one skip wherever a FalkorDB is absent, and it is where the
claim "deleting the derived cache and rebuilding from the record alone produces
the same narrative" is measured against a REAL graph that is really destroyed.
"""
from __future__ import annotations

import os
import uuid

import pytest
from test_9555_narrative_carrier import (
    NARRATIVE,
    SESSION,
    _ensure_schema,
    _record_db,
    _replay_db,
    _seed,
    _tenant,
)

from tortoise import record_text


def test_the_public_capture_path_REBUILDS_THE_CACHE_FROM_THE_RECORD_and_the_narrative_survives():
    """(b), the whole sentence: delete the derived cache, rebuild from the
    record alone, and the narrative is the SAME one.

    The narrative lives in the record (§9.1), so the assertion has two halves
    and both matter: the CACHE is genuinely destroyed and genuinely rebuilt from
    the record (otherwise "survives" is a claim about a cache that never went
    away), and the served text is byte-identical across the wipe.

    ⚠️ Needs a FalkorDB as well as the record's Postgres, so it skips unless BOTH
    are armed — the record half runs in CI's `test-record-postgres` job, the
    cache half on a lane with `TORTOISE_DB_URI`.
    """
    conn = _record_db()
    if not os.environ.get("TORTOISE_DB_URI"):
        conn.close()
        pytest.skip("TORTOISE_DB_URI not set — no cache to destroy and rebuild")
    try:
        _ensure_schema(conn)
        org_id, graph_id = _tenant()
        _seed(conn, org_id=org_id, graph_id=graph_id)
        before = record_text.serve_from_record(
            conn, org_id=org_id, graph_id=graph_id, session_id=SESSION)
        assert before.served

        from tortoise.cache_projection import read_record_view
        from tortoise.projection import FalkorProjection
        from tortoise.record_restore import restore_from_record_view

        # The RECORD→CACHE reader runs on the app role too: since #9600 it
        # scopes the connection with the tenant GUC and REFUSES a role that
        # could bypass RLS, so the provisioning superuser cannot read a view.
        # The view is materialised in memory, so the transaction ends here and
        # the restore below is a pure function of what was read.
        view_conn = _replay_db()
        try:
            view = read_record_view(view_conn, org_id=org_id, graph_id=graph_id)
            view_conn.commit()
        finally:
            view_conn.close()

        # The cache graph is minted EMPTY, which is what makes the first
        # restore a pure record→cache write; it is then DESTROYED and rebuilt a
        # second time, so "the narrative survived the cache being deleted" is a
        # statement about a cache that really went away (`test_` is required by
        # the projection's own L2 destructive-op guard).
        cache_graph = f"test_9555_narr_{uuid.uuid4().hex[:8]}"
        proj = FalkorProjection.from_uri(os.environ["TORTOISE_DB_URI"],
                                        graph_name=cache_graph)
        try:
            assert view, "the record holds no rows — nothing to rebuild FROM"
            first = restore_from_record_view(
                view, org_id=org_id, graph_id=graph_id, cache_graph=cache_graph,
                project=proj)
            assert first.nodes_written > 0, (
                "the rebuild wrote nothing — 'the narrative survived' would then "
                "be a statement about a cache that was never restored")

            # ── DELETE THE DERIVED CACHE ──────────────────────────────────
            proj.g.query("MATCH (n) DETACH DELETE n")
            rows = proj.g.query("MATCH (n) RETURN count(n)")
            remaining = int(rows.result_set[0][0]) if rows.result_set else 0
            assert remaining == 0, (
                f"the cache still holds {remaining} node(s) — the rebuild below "
                "would be adding to a cache that was never destroyed")

            # ── REBUILD FROM THE RECORD ALONE ─────────────────────────────
            second = restore_from_record_view(
                view, org_id=org_id, graph_id=graph_id, cache_graph=cache_graph,
                project=proj)
            assert second.nodes_written > 0, (
                "the rebuild from the record alone wrote nothing")
        finally:
            proj.close()

        after = record_text.serve_from_record(
            conn, org_id=org_id, graph_id=graph_id, session_id=SESSION)
        assert after.served
        assert after.session.narrative == before.session.narrative == NARRATIVE
        assert after.session.redactions == before.session.redactions == 4
        assert [t.body for t in after.session.turns] == [
            t.body for t in before.session.turns]
    finally:
        conn.close()

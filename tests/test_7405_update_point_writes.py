"""#7405 — the point-mutation verbs ride the bounded write retry.

``tortoise/retry.py``'s header names the residue itself. Before this change it
listed ``update_point`` (and therefore ``annotate_operator``, which delegates to
it) among the UNWIRED surfaces, alongside ``invalidate_point``'s two statements,
which fell under "the remaining direct ``proj.g.query`` write sites". This file
pins each of those statements as wired, one test per SURFACE, so the header can
stay the single source of truth without over-claiming.

``update_point`` is the SDK's most common mutation door, so the pre-fix
behaviour is the #7405 asymmetry in its sharpest form: while a rebuild/replace
is in flight the engine aborts every write query with *"graph was deleted or
replaced while the query is running, aborting"*, reads keep succeeding, and the
revision was REJECTED with no retry — the caller's edit is lost from the map
while ``get_point`` still answers.

Why re-issuing each statement is safe (reasoned from the statement's SHAPE, not
measured here — these tests pin that the aborted statement is re-issued and
applied exactly once):

1. the plain arms — an ABSOLUTE ``SET n += $props`` / ``SET n.version = ...``
   whose every value (``_props_param``, ``now``, ``_embed_params``) was fixed
   before the first attempt, so a re-issue converges on exactly the single-issue
   state and cannot double-increment;
2. the promote arms — the guard lives INSIDE the statement
   (``WHERE n.status IS NULL OR n.status = 'draft'``), so a re-issue re-reads the
   graph, cannot promote twice, and a legitimately-refused promote still returns
   an empty ``result_set`` (the caller's raise path is unchanged);
3. the per-key loop — one key per statement, each an absolute ``SET``;
4. the derived-embedding clear+set — one statement, absolute;
5. ``_sync_tags`` — ``MERGE`` per tag plus DELETEs by an explicit name, so the
   same tag list converges (``create_point`` already rides this same wrapper);
6. ``invalidate_point`` — an absolute terminal stamp whose values were fixed
   before the attempt (so the window END cannot move) and a ``CORRECTS`` MERGE
   on its endpoints.

⛔ The retry must NOT mask a deletion. The engine reports the SAME abort for a
rebuild and for a plain deletion, so the graph-abort family is retried only
while the graph KEY is present (``_graph_exists``). That loud arm is a shared
property of ``_graph_write_with_retry`` and is pinned here as well, because the
point-mutation verbs are new callers of it.

Lane: runs on the live graph when ``TORTOISE_DB_URI`` is set (the docker lane)
and on embedded FalkorDBLite otherwise — the same dual-lane convention as
``test_7209_write_landing.py`` and ``test_7405_remaining_writes.py``.
"""
from __future__ import annotations

import contextlib
import os
import uuid

import pytest
import redis.exceptions as redis_exc

from tortoise.sdk import TortoiseSDK

# The MEASURED v6 literal (see #7685): one sentence for two realities — a
# rebuild and a plain deletion. Which one it was is decided by the graph KEY's
# presence (`EXISTS`), never by this text.
_DELETED_OR_REPLACED = (
    "graph was deleted or replaced while the query was running, aborting")

# The statement each surface issues, matched on a stable fragment.
_POINT_PROP_SET_NEEDLE = "MATCH (n:Point {id:$id}) SET n += $props"
_POINT_PROMOTE_NEEDLE = (
    "n.status = 'live', n.updatedAt = $now, n += $props")
_OBJECT_PROP_SET_NEEDLE = ":Point:Object {id:$id}) SET n += $props"
_TAGGED_NEEDLE = "MERGE (t:Tag {name:$tag}) "
_INVALIDATE_STAMP_NEEDLE = "SET n.outdated = true"
_INVALIDATE_CORRECTS_NEEDLE = "MERGE (a)-[:CORRECTS]->(b)"


class _AbortOnceGraph:
    """Delegate every statement, aborting the first *aborts* matching ones.

    Models the measured engine race (#7405 / #7685): the statement is refused
    with ``ResponseError('graph was deleted or replaced …')`` while the graph
    key is still present (a rebuild), so a correct write path RE-ISSUES it.

    ``present`` answers the SDK's own ``EXISTS <name>`` probe — the
    discriminator the retry is gated on. ``applied`` counts matching statements
    that actually reached the real graph, so "retried and applied exactly once"
    is MEASURED rather than assumed.
    """

    def __init__(self, real, needle: str, *, aborts: int = 1,
                 present: bool = True) -> None:
        self._real = real
        self._needle = needle
        self._aborts = aborts
        self._present = present
        self.matching = 0
        self.applied = 0

    def query(self, cypher, params=None, timeout=None):
        if self._needle in cypher:
            self.matching += 1
            if self.matching <= self._aborts:
                raise redis_exc.ResponseError(_DELETED_OR_REPLACED)
            self.applied += 1
        return self._real.query(cypher, params=params, timeout=timeout)

    def execute_command(self, *a, **k):
        if a and a[0] == "EXISTS":
            return 1 if self._present else 0
        return self._real.execute_command(*a, **k)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _sdk(tmp_path, tag: str):
    """A real SDK on the configured lane (a live URI, else embedded)."""
    uri = os.environ.get("TORTOISE_DB_URI")
    return TortoiseSDK(
        None if uri else str(tmp_path / f"{tag}.db"),
        namespace=f"test_7405_up_{tag}_{uuid.uuid4().hex[:8]}",
    )


def _pid(sdk) -> str:
    return sdk.create_point("evidence", f"claim for {uuid.uuid4().hex}")["id"]


# ── 1. the plain property arm (the per-key ``SET`` loop) ─────────────────


def test_a_rebuild_abort_on_the_property_set_is_retried_and_applies_once(
        tmp_path):
    """A rebuild abort on ``update_point``'s property ``SET`` must not lose it.

    Pre-fix the statement was a bare ``proj.g.query``: the per-query abort
    raised straight out of ``update_point``, the revision never landed, and the
    caller's edit survived only in its own transcript — while ``get_point``
    kept answering, so nothing looked broken. That is the #7405 loss.
    """
    sdk = _sdk(tmp_path, "propset")
    try:
        pid = _pid(sdk)
        proj = sdk._get_proj()
        proxy = _AbortOnceGraph(proj.g, _POINT_PROP_SET_NEEDLE, present=True)
        proj.g = proxy

        sdk.update_point(pid, confidence=0.42)

        assert proxy.matching >= 2, "the aborted SET was never re-issued"
        assert proxy.applied == 1, (
            "exactly one application — a re-issued absolute SET must not "
            "double-increment")
        proj.g = proxy._real
        assert sdk.get_point(pid)["confidence"] == pytest.approx(0.42), (
            "the revision must be readable after the retry")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── 2. the promote arm (``status`` given, guarded WHERE) ─────────────────


def test_a_rebuild_abort_on_the_promote_set_is_retried(tmp_path):
    """The guarded ``draft → live`` ``SET`` is retried, and promotes once.

    The guard rides inside the statement, so re-issuing it re-evaluates the same
    predicate: it cannot promote twice, and a promote the graph legitimately
    refuses still yields an empty result set (the caller's raise path).
    """
    sdk = _sdk(tmp_path, "promote")
    try:
        pid = sdk.create_point(
            "evidence", f"draft claim {uuid.uuid4().hex}", status="draft")["id"]
        proj = sdk._get_proj()
        proxy = _AbortOnceGraph(proj.g, _POINT_PROMOTE_NEEDLE, present=True)
        proj.g = proxy

        sdk.update_point(pid, status="live")

        assert proxy.matching >= 2, "the aborted promote SET was never re-issued"
        assert proxy.applied == 1, "exactly one application"
        proj.g = proxy._real
        assert sdk.get_point(pid)["status"] == "live"
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── 3. the ``:Point:Object`` arm ────────────────────────────────────────


def test_a_rebuild_abort_on_the_object_arm_set_is_retried(tmp_path):
    """The ``:Object``-labelled arm is a SEPARATE statement and is retried too.

    ``update_point`` branches on whether the node carries ``:Object`` (the
    entity arm, which also bumps ``version``). Wrapping only the ``:Point`` arm
    would leave every entity revision losing its write.
    """
    sdk = _sdk(tmp_path, "objarm")
    try:
        pid = _pid(sdk)
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) SET n:Object", params={"id": pid})
        proj = sdk._get_proj()
        proxy = _AbortOnceGraph(proj.g, _OBJECT_PROP_SET_NEEDLE, present=True)
        proj.g = proxy

        sdk.update_point(pid, confidence=0.31)

        assert proxy.matching >= 2, "the aborted :Object SET was never re-issued"
        assert proxy.applied == 1, "exactly one application"
        proj.g = proxy._real
        assert sdk.get_point(pid)["confidence"] == pytest.approx(0.31)
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── 4. the TAGGED edges ``update_point`` shares with ``create_point`` ────


def test_a_rebuild_abort_on_the_tag_sync_is_retried(tmp_path):
    """The tag half of an update is retried on the UPDATE door as well.

    ``create_point`` already rode the retry for this same helper
    (``"create_point TAGGED edges"``); the update door did not, so the same
    mutation behaved differently depending on which verb carried it. A lost tag
    write leaves ``n.tags`` set while ``query_points_by_tag`` cannot see the
    point — the property and its derived edges disagree.
    """
    sdk = _sdk(tmp_path, "tagsync")
    try:
        pid = _pid(sdk)
        proj = sdk._get_proj()
        proxy = _AbortOnceGraph(proj.g, _TAGGED_NEEDLE, present=True)
        proj.g = proxy

        sdk.update_point(pid, tags=["7405alpha", "7405beta"])

        assert proxy.matching >= 3, (
            "the aborted TAGGED MERGE was never re-issued")
        assert proxy.applied == 2, (
            "both tags applied exactly once — a re-issued MERGE cannot double")
        proj.g = proxy._real
        rows = proj.g.query(
            "MATCH (:Point {id:$id})-[:TAGGED]->(t:Tag) RETURN t.name "
            "ORDER BY t.name",
            params={"id": pid}).result_set
        assert [r[0] for r in rows] == ["7405alpha", "7405beta"]
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── 5. ``annotate_operator`` — transitive, it delegates to ``update_point`` ──


def test_a_rebuild_abort_during_annotate_operator_is_retried(tmp_path):
    """``annotate_operator`` inherits the retry from ``update_point``.

    It emits ``OperatorAnnotated`` and then delegates to ``update_point``, so
    pre-fix the event landed in the journal while the annotation never reached
    the graph: the replay applies the rating, live does not, and the operator's
    EP weight differs between the live graph and a rebuilt one — a divergence
    rebuild parity is supposed to exclude.
    """
    sdk = _sdk(tmp_path, "annotate")
    try:
        src = _pid(sdk)
        tgt = _pid(sdk)
        op = sdk.create_operator("IMPL", src, [tgt])["id"]
        proj = sdk._get_proj()
        proxy = _AbortOnceGraph(proj.g, _POINT_PROP_SET_NEEDLE, present=True)
        proj.g = proxy

        sdk.annotate_operator(op, bias=0.2, precision=0.8,
                              consistency=0.7, directness=0.9)

        assert proxy.matching >= 2, (
            "the aborted annotation SET was never re-issued")
        proj.g = proxy._real
        point = sdk.get_point(op)
        assert point["annotator_precision"] == pytest.approx(0.8)
        assert point["annotator_bias"] == pytest.approx(0.2)
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── 6. ``invalidate_point`` — the terminal stamp ────────────────────────


def test_a_rebuild_abort_on_the_invalidate_stamp_is_retried(tmp_path):
    """The terminal ``outdated`` stamp is retried, so the window END holds.

    Pre-fix the abort raised AFTER ``PointInvalidated`` had been appended: the
    journal recorded an invalidation the live graph never applied, and the point
    stayed live-and-readable while every replay's fold treated it as terminal.
    """
    sdk = _sdk(tmp_path, "invstamp")
    try:
        pid = _pid(sdk)
        corrector = _pid(sdk)
        proj = sdk._get_proj()
        proxy = _AbortOnceGraph(proj.g, _INVALIDATE_STAMP_NEEDLE, present=True)
        proj.g = proxy

        sdk.invalidate_point(pid, corrector)

        assert proxy.matching >= 2, "the aborted stamp SET was never re-issued"
        assert proxy.applied == 1, "exactly one application"
        proj.g = proxy._real
        assert sdk.get_point(pid)["outdated"] is True
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── 7. ``invalidate_point`` — the ``CORRECTS`` edge ─────────────────────


def test_a_rebuild_abort_on_the_corrects_edge_is_retried(tmp_path):
    """The ``CORRECTS`` ``MERGE`` is retried (and cannot double)."""
    sdk = _sdk(tmp_path, "invcorrects")
    try:
        pid = _pid(sdk)
        corrector = _pid(sdk)
        proj = sdk._get_proj()
        proxy = _AbortOnceGraph(
            proj.g, _INVALIDATE_CORRECTS_NEEDLE, present=True)
        proj.g = proxy

        sdk.invalidate_point(pid, corrector)

        assert proxy.matching >= 2, "the aborted MERGE was never re-issued"
        assert proxy.applied == 1, (
            "exactly one application — a re-issued MERGE cannot double")
        proj.g = proxy._real
        rows = proj.g.query(
            "MATCH (:Point {id:$new})-[:CORRECTS]->(:Point {id:$old}) "
            "RETURN count(*)",
            params={"new": corrector, "old": pid}).result_set
        assert rows and rows[0][0] == 1
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── the loud arm: a DELETED graph is never re-issued into ───────────────


def test_a_deleted_graph_abort_on_the_property_set_stays_loud(tmp_path):
    """The retry must not MASK a deletion — the #7685 guarantee, extended.

    With the graph key ABSENT the engine's sentence means a plain deletion, and
    re-issuing would write into a graph FalkorDB auto-created EMPTY and report
    SUCCESS against a graph the caller never wrote to (#6666 class). The abort
    therefore surfaces unchanged, and ``update_point`` raises instead of
    returning a point dict that claims the edit.
    """
    sdk = _sdk(tmp_path, "deleted")
    try:
        pid = _pid(sdk)
        proj = sdk._get_proj()
        proxy = _AbortOnceGraph(proj.g, _POINT_PROP_SET_NEEDLE, present=False)
        proj.g = proxy

        with pytest.raises(redis_exc.ResponseError, match="deleted or replaced"):
            sdk.update_point(pid, confidence=0.9)

        assert proxy.applied == 0, (
            "a deleted graph must not be re-issued into — nothing may be "
            "reported as applied")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()

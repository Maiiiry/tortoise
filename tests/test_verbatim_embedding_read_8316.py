"""#8316 — a VERBATIM (caller-supplied) embedding must not poison the dense read.

THE DEFECT
----------
``create_point(content, embedding=[…])``, ``update_point(id, embedding=[…])``
and ``update_entity(id, embedding=[…])`` all store the caller's vector
**VERBATIM** as a plain Cypher ``List`` — that is PR #3018's recorded decision
(the TYPE CHANGE GUARD: a caller-owned float64 must not be silently narrowed
through ``vecf32``), reaffirmed by #5004 and pinned by
``test_update_point_derived_embedding_4520.py::test_a_caller_supplied_vector_stays_verbatim``.
``typeof(n.embedding)`` therefore reads ``List`` after ONE such call, and the
dense read's per-row expression ``vec.euclideanDistance(n.embedding, _qv)``
raises::

    Type mismatch: expected Null or VectorF32 but was List

for the WHOLE ``MATCH`` — so ONE caller-vectored node degraded the ENTIRE dense
leg, every other node included, to FTS-only recall. ``run_vector_query``
swallows the raise as ``query_failed`` and returns ``[]`` (measured on the
embedded lane AND on the docker lane).

WHY THE FIX IS AT THE READ BOUNDARY, NOT THE WRITE BOUNDARY
-----------------------------------------------------------
Routing the caller's vector through the shared write seam
(``tortoise/vector_write.py``) would narrow a caller-owned float64 to float32 —
a REVERSAL of the recorded PR #3018 decision, not a fix; its route is the
#5046/#5048 reopen, never a quiet re-route. Both shapes the write side
legitimately produces are therefore normalised at the READ boundary instead, in
ONE place: ``vector_write.stored_embedding_vector_expr`` — the expression the
dense read uses for a stored embedding. The stored value is untouched.

RED before / GREEN after
------------------------
``test_a_verbatim_embedding_does_not_poison_the_dense_leg`` fails on the
pre-fix tree (``run_vector_query`` returns ``[]`` — the whole ``MATCH`` raised,
leg-trace reason ``query_failed``) and passes once the read normalises the
shape.

*Mutations that re-redden it* (each must be reverted to pass):

* restore ``<alias>.embedding`` as the distance argument in
  ``search_engine.run_vector_query`` — the ``MATCH`` raises and the dense leg
  returns ``[]``; or
* "simplify" ``stored_embedding_vector_expr`` to
  ``CASE WHEN typeof(…) = 'Vectorf32' THEN … ELSE vecf32(…) END`` — both engines
  evaluate BOTH ``CASE`` arms, so every already-typed row raises
  (``… but was Vectorf32``). ``test_the_typed_shape_is_still_readable`` pins
  that, so the obvious simplification reddens here rather than in production.

Hermetic: the embedder is stubbed, the store is a per-test embedded file. No
provider keys, no network.

Run (embedded lane)::

    env -u TORTOISE_DB_URI TORTOISE_TEST_CARVE_OUT=1 \\
        uv run pytest tests/test_verbatim_embedding_read_8316.py -q
"""
from __future__ import annotations

import math
from unittest import mock

import pytest

from tortoise.embeddings import EMBEDDING_DIM
from tortoise.sdk import TortoiseSDK

# The store is a real embedded file (the defect reproduces there first), so the
# module is module-level `embedded_only` and MUST be registered in
# config/ci-surfaces.yml `carve_out:` AND in tests/_embedded.py
# TEST_NO_REDIRECT_STEMS. Without that registration a URI-set selection collects
# the file and SKIPS every case via the marker hook — a permanently green,
# permanently unexecuted gate (the #4047 shape).
pytestmark = pytest.mark.embedded_only

_EMBED_PATCH = "tortoise.embeddings.compute_embedding"
DIM = EMBEDDING_DIM if isinstance(EMBEDDING_DIM, int) else 384

#: A float64 value that does NOT round-trip through float32 — so the stored
#: value proves the caller's precision was preserved (the recorded decision).
_CALLER_VALUE = 0.1234567890123


def _vector(seed: float) -> list[float]:
    """A smooth, embedder-shaped vector (no axis a synthetic probe could hit)."""
    return [math.sin(i * 0.017) * 0.4 + seed for i in range(DIM)]


class _StubEmbedder:
    def __init__(self, mapping: dict[str, list[float]]) -> None:
        self._mapping = mapping
        self.calls: list[str] = []

    def __call__(self, text: str, *_a, **_k) -> list[float]:
        self.calls.append(text)
        return list(self._mapping[text])


def _typeof(sdk: TortoiseSDK, pid: str) -> str | None:
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN typeof(n.embedding)",
        params={"id": pid}).result_set
    return rows[0][0] if rows else None


def _stored(sdk: TortoiseSDK, pid: str) -> list[float] | None:
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.embedding",
        params={"id": pid}).result_set
    return None if not rows or rows[0][0] is None else list(rows[0][0])


def _dense(sdk: TortoiseSDK, query: list[float]) -> tuple[list[str], str | None]:
    """The shared dense read, with its leg-trace reason (#8316's own symptom).

    Returns ``(hit ids, reason)``. Pre-fix the whole ``MATCH`` raises inside
    ``run_vector_query``, which records ``query_failed`` and returns ``[]``.

    The strategy circuit breaker is reset first: three consecutive failures
    open it (30 s cooldown, process-global), and a later test would then record
    ``breaker_open`` instead of the real ``query_failed`` — a green/red for the
    wrong reason.
    """
    from tortoise.search_engine import reset_circuit_breakers, run_vector_query
    reset_circuit_breakers()
    trace: list[dict] = []
    hits = [pid for pid, _score in run_vector_query(
        sdk._get_proj().g, query, is_embedded=True, leg_trace=trace)]
    return hits, (trace[-1].get("reason") if trace else None)


# ── acceptance: one verbatim node must not poison the whole dense leg ────────

def test_a_verbatim_embedding_does_not_poison_the_dense_leg(tmp_path):
    """RED before: the whole ``MATCH`` raises and NO Point is returned."""
    embedder = _StubEmbedder({"alpha text": _vector(0.0)})
    with mock.patch(_EMBED_PATCH, embedder):
        sdk = TortoiseSDK(str(tmp_path / "verbatim_read.db"))
        try:
            typed_id = sdk.create_point("statement", "alpha text")["id"]
            assert _typeof(sdk, typed_id) == "Vectorf32", (
                "premise: create_point stores a typed vector")

            verbatim_id = sdk.create_point("statement", "beta text")["id"]
            caller_vec = [_CALLER_VALUE + i * 0.0 for i in range(DIM)]
            sdk.update_point(verbatim_id, embedding=caller_vec)  # THE write
            assert _typeof(sdk, verbatim_id) == "List", (
                "premise: a caller-supplied embedding is stored VERBATIM "
                "(PR #3018) — this test is about the READ, not the write")

            # (1) the OTHER Point's dense read must survive a neighbour's shape
            hits, reason = _dense(sdk, _vector(0.0))
            assert reason == "ok", (
                f"the dense leg recorded {reason!r} instead of 'ok' — ONE "
                "verbatim (`List`) embedding makes `vec.euclideanDistance` "
                "raise for the WHOLE MATCH ('Type mismatch: expected Null or "
                "VectorF32 but was List'), degrading every Point to FTS-only "
                "recall (#8316)")
            assert typed_id in hits, (
                "the typed Point vanished from the dense leg because a "
                "neighbour holds a verbatim List embedding (#8316)")

            # (2) the verbatim node is itself dense-readable
            hits, reason = _dense(sdk, caller_vec)
            assert reason == "ok", reason
            assert verbatim_id in hits, (
                "the caller-vectored Point is missing from its own dense read — "
                "the read boundary did not normalise the stored shape (#8316)")
        finally:
            sdk.close()


def test_the_caller_value_is_still_stored_verbatim(tmp_path):
    """The fix must not narrow the caller's value (the recorded decision).

    A read-side fix touches no write, so the exact float64 must still be on the
    node and ``typeof`` must still read ``List``. If this ever reads
    ``Vectorf32``/``0.12345679…``, the fix was implemented at the WRITE boundary
    — a reversal of PR #3018 that belongs to the #5046/#5048 reopen.
    """
    embedder = _StubEmbedder({"alpha text": _vector(0.0)})
    with mock.patch(_EMBED_PATCH, embedder):
        sdk = TortoiseSDK(str(tmp_path / "verbatim_value.db"))
        try:
            pid = sdk.create_point("statement", "alpha text")["id"]
            sdk.update_point(pid, embedding=[_CALLER_VALUE] * DIM)
            assert _typeof(sdk, pid) == "List"
            stored = _stored(sdk, pid)
            assert stored is not None and stored[0] == _CALLER_VALUE, (
                "the caller's float64 was silently narrowed to float32 — the "
                "recorded verbatim decision (PR #3018) was reversed")
        finally:
            sdk.close()


def test_the_typed_shape_is_still_readable(tmp_path):
    """An already-typed row must pass through the normaliser UNCHANGED.

    ``vecf32()`` REJECTS an already-typed vector ("expected List or Null but
    was Vectorf32"), and both engines evaluate BOTH arms of a ``CASE`` — so a
    "simplification" to ``CASE WHEN typeof(…)=… THEN … ELSE vecf32(…) END``
    raises on every normal row. This is what reddens that edit.
    """
    from tortoise.vector_write import stored_embedding_vector_expr

    expr = stored_embedding_vector_expr("n")
    assert "CASE WHEN" not in expr, (
        "the normaliser uses a CASE — both engines evaluate BOTH arms, so the "
        "vecf32 arm runs on already-typed rows and the whole query raises")

    embedder = _StubEmbedder({"alpha text": _vector(0.0)})
    with mock.patch(_EMBED_PATCH, embedder):
        sdk = TortoiseSDK(str(tmp_path / "typed_read.db"))
        try:
            pid = sdk.create_point("statement", "alpha text")["id"]
            assert _typeof(sdk, pid) == "Vectorf32", "premise: typed vector"
            rows = sdk._get_proj().g.query(
                "MATCH (n:Point {id:$id}) WITH vecf32($q) AS _qv, n "
                f"RETURN vec.euclideanDistance({expr}, _qv)",
                params={"id": pid, "q": _vector(0.0)}).result_set
            assert rows and rows[0][0] == pytest.approx(0.0, abs=1e-6), (
                "the normaliser did not return the typed vector unchanged: "
                f"{rows!r} (#8316)")
        finally:
            sdk.close()


# ── every writer on the verbatim branch is covered by the ONE read seam ──────

def test_create_point_verbatim_embedding_is_dense_readable(tmp_path):
    """``create_point(embedding=…)`` reaches the same branch — and the seam."""
    with mock.patch(_EMBED_PATCH, _StubEmbedder({})):
        sdk = TortoiseSDK(str(tmp_path / "create_verbatim.db"))
        try:
            caller_vec = [_CALLER_VALUE] * DIM
            pid = sdk.create_point(
                "statement", "no encoder needed", embedding=caller_vec)["id"]
            assert _typeof(sdk, pid) == "List", (
                "premise: create_point stores a caller embedding VERBATIM")
            hits, reason = _dense(sdk, caller_vec)
            assert reason == "ok", reason
            assert pid in hits, (
                "a create_point caller-vectored Point is unreadable by the "
                "dense leg (#8316)")
        finally:
            sdk.close()


def test_update_entity_point_branch_verbatim_embedding_is_dense_readable(tmp_path):
    """``update_entity(id, embedding=…)`` — the other reported reachable surface."""
    embedder = _StubEmbedder({"alpha text": _vector(0.0)})
    with mock.patch(_EMBED_PATCH, embedder):
        sdk = TortoiseSDK(str(tmp_path / "entity_verbatim.db"))
        try:
            pid = sdk.create_point("statement", "alpha text")["id"]
            caller_vec = [_CALLER_VALUE] * DIM
            sdk.update_entity(pid, embedding=caller_vec)
            assert _typeof(sdk, pid) == "List", (
                "premise: update_entity's Point branch stores verbatim")
            hits, reason = _dense(sdk, caller_vec)
            assert reason == "ok", reason
            assert pid in hits, (
                "an update_entity caller-vectored Point is unreadable by the "
                "dense leg (#8316)")
        finally:
            sdk.close()

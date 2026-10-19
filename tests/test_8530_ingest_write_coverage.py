"""#8530 — the write response must declare a degraded/EP-absent graph.

Two write outcomes used to be invisible to the caller (both hit in ONE 49-point
/ 68-connection re-file, and both reported a clean success):

1. **Unembedded Points.** The embedder failed to load, 49 of 49 Points were
   written with ``embedding IS NULL``, and ``created.points: 49`` read exactly
   like 49 embedded ones. ``semantic search will never return them`` was known
   to the process (the warm-up path had already decided it was degraded) and
   never reached the response.
2. **EP-absent relations.** Under ``promotion_policy='auto'`` a connection
   TARGET point stays ``draft`` (the #131/#1088 source-only lifecycle), so the
   operator behind the written connection has 1 participating input and EP
   drops it as degenerate (#780/#2422) — while ``created.connections`` counted
   it as written. The only trace was a later EP ``skipped_factors`` diagnostic
   (#6105) that nothing consumes at write time.

The fix is a POST-WRITE MEASUREMENT, not an inference: ``coverage`` reports what
the graph actually holds. These tests pin each claim against an INDEPENDENT
oracle (a direct graph read, or EP's own skip record), because a report that
merely echoes the writer's intent would reproduce the defect it fixes.

Measured on this tree before the change (embedded lane):
``MISSING EMBEDDINGS: 3 of 3`` with an empty ``warnings`` list, and
``skipped_factors: [{operator_id, op_type: IMPL, reason: degenerate,
participating_inputs: 1, inputs: [live, draft]}]`` alongside
``created.connections: 1``.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time

import pytest

from tortoise.embeddings import EMBEDDING_DIM, EmbeddingModel
from tortoise.live import _live_only
from tortoise.sdk import TortoiseSDK


@pytest.fixture(autouse=True)
def _hermetic_embedder(monkeypatch):
    """Every test STATES the embedder's state — no model load, no network, and
    no 90 s load timeout per test. Tests that need degradation call
    :func:`_degrade_embedder`, which overrides this."""
    _stub_healthy_embedder(monkeypatch)


@pytest.fixture
def sdk():
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_8530_"), "t.db")
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


def _bundle(n_points=2, contents=None):
    contents = contents or [f"#8530 claim number {i}." for i in range(n_points)]
    points = [{"ref": f"p{i}", "kind": "statement", "content": c}
              for i, c in enumerate(contents)]
    return {"points": points,
            "connections": [{"from": "p0", "to": "p1", "operator": "IMPL"}]}


def _stub_healthy_embedder(monkeypatch):
    """A self-consistent encoder at :data:`EMBEDDING_DIM`, so a Point really
    does land with a vector (a store-width drop is a different, already-pinned
    failure — #4280)."""
    monkeypatch.setattr(
        "tortoise.embeddings.compute_embedding",
        lambda text, *a, **k: [0.01] * EMBEDDING_DIM)

    class _Loaded:  # `status()` reads `_instance._model` only
        _model = object()

    monkeypatch.setattr(EmbeddingModel, "_instance", _Loaded())
    monkeypatch.setattr(EmbeddingModel, "_last_failed_at", None)
    monkeypatch.setattr(EmbeddingModel, "_last_failure_kind", None)


def _degrade_embedder(monkeypatch, kind="not_installed"):
    """The real degrade: ``get()`` consults the negative cache and returns
    ``None``, the write seam stores no vector, and ``status()`` reports the
    failure kind — the same three facts #8530's reporter measured."""
    monkeypatch.setattr("tortoise.embeddings.compute_embedding",
                        lambda text, *a, **k: None)
    monkeypatch.setattr(EmbeddingModel, "_instance", None)
    monkeypatch.setattr(EmbeddingModel, "_last_failed_at", time.monotonic())
    monkeypatch.setattr(EmbeddingModel, "_last_failure_kind", kind)
    monkeypatch.setattr(EmbeddingModel, "_last_error", kind)


def _missing_embeddings(sdk, ids):
    """INDEPENDENT oracle: what the graph holds, not what the report says."""
    rows = sdk._get_proj().g.query(
        "MATCH (p:Point) WHERE p.id IN $ids RETURN p.id, p.embedding IS NULL",
        params={"ids": list(ids)},
    ).result_set
    return [pid for pid, no_vec in rows if no_vec]


def _participating_operators(sdk, op_ids):
    """INDEPENDENT oracle for derived liveness (§11.1), built from the SHARED
    ``_live_only`` predicate — an operator participates iff it is itself live
    and has ≥2 live inputs."""
    rows = sdk._get_proj().g.query(
        "MATCH (o:Point)-[:IMPL|NAND]->(c) "
        "WHERE o.id IN $ops "
        f"AND ({_live_only('o.status')}) "
        f"AND ({_live_only('c.status')}) "
        "RETURN o.id, count(c)",
        params={"ops": list(op_ids)},
    ).result_set
    return {op for op, live_inputs in rows if live_inputs >= 2}


# ── Finding 1 — embedding coverage is declared, not silent ───────────

def test_degraded_embedder_is_declared_in_the_response(sdk, monkeypatch):
    """The #8530 shape: 3 of 3 Points unembedded, and the response SAYS so."""
    _degrade_embedder(monkeypatch)
    res = sdk.ingest(_bundle(3))
    cov = res["coverage"]["embedding"]
    assert cov["points"] == 3
    assert cov["embedded"] == 0
    assert cov["missing"] == 3
    assert cov["degraded"] is True
    assert cov["reason"] == "no_embedder"
    assert cov["embedder_state"] in {"cooldown", "unavailable"}
    assert cov["failure_kind"] == "not_installed"
    # The report is TRUE of the graph: measured independently.
    assert len(_missing_embeddings(sdk, res["ids"]["points"])) == 3


def test_healthy_embedder_reports_full_coverage(sdk):
    """No false alarm: with the embedder live the report is clean and the
    vectors really are there."""
    res = sdk.ingest(_bundle(3))
    cov = res["coverage"]["embedding"]
    assert (cov["points"], cov["embedded"], cov["missing"]) == (3, 3, 0)
    assert cov["degraded"] is False
    assert cov["reason"] == "ok"
    assert "embedder_state" not in cov  # only reported when degraded
    assert _missing_embeddings(sdk, res["ids"]["points"]) == []


def test_partial_coverage_is_counted_not_rounded(sdk, monkeypatch):
    """A pre-existing unembedded Point that a resubmission DEDUPS onto is still
    counted as missing — the count is of the store, not of this call's intent."""
    _degrade_embedder(monkeypatch)
    first = sdk.ingest(_bundle(2))
    assert first["coverage"]["embedding"]["missing"] == 2
    # A NEW bundle that only references one already-written (unembedded) point.
    second = sdk.ingest({"points": [
        {"ref": "q0", "kind": "statement", "content": "#8530 claim number 0."},
        {"ref": "q1", "kind": "statement", "content": "#8530 fresh claim."},
    ]})
    assert second["created"]["points"] + second["deduped"]["points"] == 2
    cov = second["coverage"]["embedding"]
    assert cov["points"] == 2
    assert cov["missing"] == 2  # the dedup hit is counted honestly, no optimism


# ── Finding 2 — EP participation is asserted, not assumed ────────────

def test_auto_write_names_the_draft_endpoint_and_the_inert_operator(sdk):
    """The reported scenario: under ``auto`` the TARGET stays draft, so the
    operator is degenerate — and the response now says which point and which
    operator (#780/#2422/#6105)."""
    res = sdk.ingest(_bundle(2), promotion_policy="auto")
    ep = res["coverage"]["ep"]
    target = res["ids"]["refs"]["p1"]
    assert ep["points"]["not_live"] == 1
    assert ep["points"]["not_live_ids"] == [target]
    assert ep["operators"]["written"] == 1
    assert ep["operators"]["assessed"] is True
    assert ep["operators"]["participating"] == 0
    assert ep["operators"]["inert"] == 1
    assert ep["operators"]["unmet"] is True
    (rec,) = ep["operators"]["inert_operators"]
    assert rec["reason"] == "degenerate"
    assert rec["participating_inputs"] == 1
    # Independent oracle agrees with the report.
    op_id = res["ids"]["connections"][0]
    assert _participating_operators(sdk, [op_id]) == set()


def test_promoting_the_endpoint_moves_the_operator_into_participation(sdk):
    """The remedy the coverage report implies actually works — promote the
    non-live ids it named, re-file, and the operator participates."""
    first = sdk.ingest(_bundle(2), promotion_policy="auto")
    op_id = first["ids"]["connections"][0]
    for pid in first["coverage"]["ep"]["points"]["not_live_ids"]:
        sdk.update_point(pid, status="live")
    second = sdk.ingest(_bundle(2), promotion_policy="auto")
    ep = second["coverage"]["ep"]
    assert ep["points"]["not_live"] == 0
    assert ep["points"]["not_live_ids"] == []
    assert ep["operators"]["participating"] == 1
    assert ep["operators"]["inert"] == 0
    assert ep["operators"]["unmet"] is False
    assert ep["operators"]["inert_operators"] == []
    assert _participating_operators(sdk, [op_id]) == {op_id}


def test_gated_write_reports_inert_data_but_never_calls_it_unmet(sdk):
    """Under ``gated`` a draft operator is the CONTRACTED state (§11.1): the
    counts are reported, the verdict is not raised."""
    res = sdk.ingest(_bundle(2))  # gated is the default
    ep = res["coverage"]["ep"]
    assert ep["points"]["live"] == 0
    assert ep["points"]["not_live"] == 2
    assert ep["operators"]["inert"] == 1
    assert ep["operators"]["unmet"] is False
    assert ep["operators"]["inert_operators"][0]["reason"] \
        == "operator_not_live"


def test_a_mitigated_operator_participates_and_coverage_agrees(sdk):
    """Re-measurement of #8530's second claim: ``mitigate_operator`` creates its
    Point WITHOUT a status property, which EP treats as LIVE (`_live_only`
    admits NULL) — so the mitigation does NOT knock the operator out of belief
    propagation. The drop the report saw came from a draft CLAIM endpoint."""
    a = sdk.create_point("statement", "#8530 mitigation A.", status="live")
    b = sdk.create_point("statement", "#8530 mitigation B.", status="live")
    op = sdk.create_operator("IMPL", a["id"], [b["id"]], promote_source=False)
    sdk.update_point(op["id"], status="live")
    mitigation = sdk.mitigate_operator(op["id"], "one pilot is not proof",
                                       strength=0.5)
    assert mitigation.get("status") is None  # status-less, EP-live (#5089 class)
    # Re-filing the same connection dedups onto that operator and reports it.
    res = sdk.ingest({"points": [
        {"ref": "a", "kind": "statement", "content": "#8530 mitigation A."},
        {"ref": "b", "kind": "statement", "content": "#8530 mitigation B."}],
        "connections": [{"from": "a", "to": "b", "operator": "IMPL"}]},
        promotion_policy="auto")
    ep = res["coverage"]["ep"]
    assert ep["operators"]["written"] == 1
    assert ep["operators"]["participating"] == 1
    assert ep["operators"]["inert"] == 0
    assert _participating_operators(sdk, [op["id"]]) == {op["id"]}


# ── Honesty under failure ────────────────────────────────────────────

def test_an_unassessable_participation_is_null_not_zero(sdk, monkeypatch):
    """If the assessment cannot run, ``participating``/``inert`` are ``None``
    and ``assessed`` is False — a fabricated ``0`` is the #8530 defect again."""
    def _boom(*a, **k):
        raise RuntimeError("selector exploded")

    monkeypatch.setattr(type(sdk._get_ep()), "_affected_factors", _boom)
    res = sdk.ingest(_bundle(2), promotion_policy="auto")
    ops = res["coverage"]["ep"]["operators"]
    assert ops["assessed"] is False
    assert ops["participating"] is None
    assert ops["inert"] is None
    assert ops["unmet"] is False
    assert res["created"]["connections"] == 1  # the write itself still succeeded


def test_coverage_rides_the_mcp_surface_too(sdk, monkeypatch):
    """Same declaration through the handler layer — the surface an agent
    actually calls."""
    import tortoise.mcp_server as mcp_mod  # noqa: I001
    from tortoise.mcp_auth import (_current_org_id, _current_org_limits,
                                   _transport_mode)
    _degrade_embedder(monkeypatch)
    _transport_mode.set("stdio")
    _current_org_id.set(None)
    _current_org_limits.set(None)
    orig = mcp_mod._get_org_sdk
    mcp_mod._get_org_sdk = lambda: sdk
    try:
        res = mcp_mod.tortoise_ingest(bundle=_bundle(2), promotion_policy="auto")
        assert res["coverage"]["embedding"]["degraded"] is True
        assert res["coverage"]["embedding"]["missing"] == 2
        assert res["coverage"]["ep"]["operators"]["unmet"] is True
    finally:
        _transport_mode.set(None)
        _current_org_id.set(None)
        _current_org_limits.set(None)
        mcp_mod._get_org_sdk = orig

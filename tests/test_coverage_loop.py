"""#2567 (C3-1 #2519) — the evidence-completeness retrieval loop (docker lane).

The multi-session partial-evidence lever (docs/scoping/2026-09-08-2519-
coverage-loop.md §3 mechanism (a)): a one-shot top-k cannot enumerate ALL
sessions that carry an aggregation question's subject — k<N stays 0 on the
official all-or-nothing ``recall_all@5`` binary. The loop (product rules in
tortoise/coverage_loop.py, composed by tools/longmem_eval/retrieve.py::
retrieve_for_question) runs:

  CENSUS → CHECK → EXPAND → MERGE

  * census: the rule-based facet census resolves the query's own entity
    anchors through the Object-name spine (#2518); each anchor's span = the
    distinct sessions carrying its ``aboutObject``-linked points,
  * check: the deduped pool is facet-incomplete when a census facet is
    seeded in the guard window yet its span is not fully covered (partial
    evidence — open-ended questions never fire),
  * expand: ONE targeted second sparse pass for the missing facet (hard
    ≤1-extra-pass bound; the A4/C2 reserved-slot OR contract),
  * merge: additive union in second-pass relevance order + the session-
    diverse window discipline (a same-session flood never crowds the
    guard window).

Hermetic contract proven here (default OFF; no LLM, no network):

  * (a) end-to-end: plain top-k misses a same-subject point in another
        session; the loop's targeted second pass surfaces it inside the
        top-k (session_recall@5 0.5 → 1.0),
  * (b) OFF == byte-identical (default no-kwarg == explicit False; ranked
        ids and pool identical; loop markers all-zero),
  * (c) the eval env gate is fail-safe OFF (unset/garbage → OFF; only
        1/true/yes/on arms; explicit flags beat the env),
  * (d) the hard ≤1-extra-pass bound (two missing facets → exactly ONE
        expansion pass),
  * (e) the never-fire rules: open-ended questions (no countable facet)
        and complete single-session spans never fire; an out-of-window
        date-qualified span never fires,
  * (f) fail-open: any loop failure keeps the ORIGINAL pool byte-identical.

Runs against a live FalkorDB (docker lane) on a DEDICATED per-test graph
(fresh indexes, zero cross-test contamination). Requires the docker lane's
FTS backend — skips when unavailable (embedded has no fulltext index).
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

# import the eval ingest first so its register_kind("event") / register_kind(
# "session-transcript") apply (turn points in the eval's graphs are Point
# nodes with pointKind "event" — the fixture mirrors that shape).
import tools.longmem_eval.ingest  # noqa: F401
from tests import _live_utils
from tortoise.sdk import TortoiseSDK


# ── Live-FalkorDB availability (the FTS backend the loop needs) ────────
def _falkordb_available() -> bool:
    """Probe a live FalkorDB; reads TORTOISE_DB_URI at CALL time so the
    module never captures it at import (#221 test-isolation lint)."""
    uri = os.environ.get(
        "TORTOISE_DB_URI",  # empty-uri-default-ok: #2815 skip declared; converting flips the tier-2 lane
        _live_utils.docker_uri("tortoise_test_matrix")).rstrip("/")
    old = os.environ.get("TORTOISE_DB_URI")
    try:
        os.environ["TORTOISE_DB_URI"] = f"{uri}_probe"
        from tortoise.sdk import TortoiseSDK as _ProbeSDK
        _probe = _ProbeSDK()
        _probe._get_proj().g.query("RETURN 1")
        _probe.close()
        return True
    except Exception:
        return False
    finally:
        if old is not None:
            os.environ["TORTOISE_DB_URI"] = old
        else:
            os.environ.pop("TORTOISE_DB_URI", None)


FALKORDB_AVAILABLE = _falkordb_available()


def _uri() -> str:
    """Current TORTOISE_DB_URI (or the default), read at CALL time."""
    return os.environ.get(
        "TORTOISE_DB_URI",  # empty-uri-default-ok: #2815 skip declared; converting flips the tier-2 lane
        _live_utils.docker_uri("tortoise_test_matrix")).rstrip("/")

pytestmark = pytest.mark.skipif(
    not FALKORDB_AVAILABLE, reason="requires TORTOISE_DB_URI (live FalkorDB FTS lane — tier-2 embedded legs skip)")

#: The question tokens (after the shared stopword drop): the sparse OR leg
#: matches these against content ∪ search_keys.
QUESTION = "how much did the road bike repairs cost me in total"
ENTITY_ANCHOR = "road bike repairs"
SEED_ID = "p2567sessA0"     # session A turn point — matches the question
JOIN_ID = "p2567sessB1"     # session B turn point — search_keys-only join
SEED_SESSION = "sessA2567"
JOIN_SESSION = "sessB2567"
#: The cross-session point's E3 keys: zero overlap with the question's
#: tokens (plain FTS cannot match it) — its OWN alias vocabulary joins the
#: second pass via the shared aboutObject anchor (the C2 miss class).
JOIN_KEYS = "extra charge for wheels saturday service fee"
DISTRACTOR_TOPIC = "cooking pasta recipes with tomato basil sauce"


def _fresh_uri() -> str:
    """A dedicated per-test graph on the docker server — fresh indexes and
    an empty graph make the differential hermetic (no leftovers from other
    tests in the shared matrix can seed anchors or pollute the pool)."""
    return f"{_uri()}_{uuid.uuid4().hex[:10]}"


@pytest.fixture(autouse=True)
def _no_embedder(monkeypatch):
    """Pin the dense leg OUT (hermetic): create_point stops embedding
    (``compute_embedding`` → None) and the query path sees no embedder
    (``EmbeddingModel.get`` → None), so the vector strategy never runs and
    the differential isolates the sparse loop mechanics. No model load, no
    network — deterministic on any machine with the docker lane."""
    import tortoise.embeddings as _emb
    monkeypatch.setattr(_emb, "compute_embedding",
                        staticmethod(lambda content: None))
    monkeypatch.setattr(_emb.EmbeddingModel, "get",
                        staticmethod(lambda: None))


def _question(question: str = QUESTION, *,
              answer_sessions: list[str] | None = None,
              haystack_dates: list[str] | None = None) -> dict:
    """The retrieve_for_question question shape (the fixture's session ids
    are plain strings — retrieve matches the stored ``session_id`` props).
    ``haystack_session_ids`` + ``haystack_dates`` are aligned so the loop's
    date-range facet qualification has a session→date map."""
    return {
        "question_id": "q2567test",
        "question": question,
        "question_type": "multi-session-reasoning",
        "answer_session_ids": (answer_sessions
                               if answer_sessions is not None
                               else [SEED_SESSION, JOIN_SESSION]),
        "haystack_session_ids": [SEED_SESSION, JOIN_SESSION],
        "haystack_dates": (haystack_dates
                           if haystack_dates is not None
                           else ["2026-09-01", "2026-09-05"]),
        "haystack_sessions": [],
        "answer": "",
    }


@pytest.fixture
def seeded_sdk(monkeypatch):
    """A fresh dedicated graph with the two-session fixture:

    * Object anchor ``road bike repairs`` (the entity spine node),
    * session A turn point (kind "event" — the eval's turn shape) matching
      the question's own tokens,
    * session B turn point about the SAME anchor whose content shares NO
      query token and whose E3 ``search_keys`` are its only join surface —
      absent from the one-shot pool (turn points enter the eval pool only
      via fts/vector; only kind-"statement" extraction points are
      structural-scanned),
    * 12 statement distractors with zero token overlap with the question or
      the keys (they rank only via the structural kind scan).
    """
    monkeypatch.setenv("TORTOISE_DB_URI", _fresh_uri())
    sdk = TortoiseSDK()
    try:
        proj = sdk._get_proj()
        sdk.create_entity("object", ENTITY_ANCHOR, objectKind="core:other",
                          is_episodic=True)
        sdk.create_point(
            "event",
            "the road bike repairs cost 120 dollars at the shop",
            id=SEED_ID, session_id=SEED_SESSION, lme_session_index=0,
            search_keys="road bike repairs bill paid 120 dollars",
            status="draft")
        sdk.create_point(
            "event",
            "on saturday morning the weather was sunny so i went for a "
            "long walk instead",
            id=JOIN_ID, session_id=JOIN_SESSION, lme_session_index=1,
            search_keys=JOIN_KEYS, status="draft")
        for i in range(12):
            sdk.create_point(
                "statement",
                f"{DISTRACTOR_TOPIC} number {i}",
                id=f"p2567dist{i}", session_id=f"dist2567{i}",
                status="draft")
        proj.g.query(
            "MATCH (p:Point), (o:Object {name:$name}) "
            "WHERE p.id IN $ids MERGE (p)-[:aboutObject]->(o)",
            params={"name": ENTITY_ANCHOR,
                    "ids": [SEED_ID, JOIN_ID]})
        yield sdk
    finally:
        sdk.close()


# ── (a) the end-to-end contract ───────────────────────────────────────────

def test_terminal_object_anchor_still_resolves(seeded_sdk):
    """#3301: the facet census anchor resolution is a RESOLUTION path, never a
    surfaced result, so a terminal Object anchor must still resolve. Without
    the ``excluded_statuses=()`` opt-out the widened Object exclusion leaves
    the census with no anchor and the loop silently no-ops."""
    from tortoise.coverage_loop import facet_census

    proj = seeded_sdk._get_proj()
    proj.g.query("MATCH (o:Object {name:$n}) SET o.status='superseded'",
                 params={"n": ENTITY_ANCHOR})
    facets = facet_census(proj, QUESTION)
    assert any(f.key == f"entity:{ENTITY_ANCHOR}" for f in facets), (
        "a terminal Object anchor must still resolve for the census "
        "(resolution is not a surfaced search)")


def test_loop_surfaces_missing_session_evidence_inside_topk(seeded_sdk):
    """Plain top-k misses the same-subject point from session B (its
    content shares no query token and it is not structural-scanned); the
    coverage loop's targeted second pass surfaces it INSIDE the top-k, and
    the official session_recall@5 moves 0.5 → 1.0."""
    from tools.longmem_eval.retrieve import retrieve_for_question
    q = _question()
    off = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                                pool_size=60)
    off_ids = [h["id"] for h in off["hits"]]
    assert SEED_ID in off_ids[:5]
    assert JOIN_ID not in off_ids[:5], (
        "plain top-k must MISS the session B point (the partial-evidence "
        "miss class #2513 targets)")
    assert JOIN_ID not in off_ids, (
        "session B's point must be absent from the ONE-SHOT pool entirely")
    assert off["session_recall@k"]["5"] == 0.5
    # off-arm markers are zeros (reconstructable 2×2 with #2518)
    assert off["coverage_loop"] is False
    assert off["coverage_loop_stats"]["loop_iterations"] == 0
    assert off["coverage_loop_stats"]["loop_fired_facet"] is None
    assert off["coverage_loop_stats"]["loop_merged_added"] == 0

    on = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                               pool_size=60, coverage_loop=True)
    on_ids = [h["id"] for h in on["hits"]]
    assert JOIN_ID in on_ids, (
        "the loop's targeted second pass must surface the session B point")
    assert JOIN_ID in on_ids[:5], (
        "the surfaced point must land INSIDE the top-k reader window")
    assert SEED_ID in on_ids[:5], "the pass-1 seed is never displaced"
    assert on["session_recall@k"]["5"] == 1.0
    stats = on["coverage_loop_stats"]
    assert on["coverage_loop"] is True
    assert stats["loop_iterations"] == 1
    assert stats["loop_fired_facet"] == f"entity:{ENTITY_ANCHOR}"
    assert stats["loop_merged_added"] >= 1
    # #2517 driver-level annotation golden: the C3-1 driver's INJECTED hit
    # must carry the WHOLE annotated key set (18), not a subset —
    # ``session_date`` is derived from the question's ``haystack_dates``
    # via ``lme_session_index`` and silently emptied in the pre-review
    # extraction, which a 4-field golden could not see. The base hit is
    # the reference shape.
    base_hit = next(h for h in on["hits"] if h["id"] == SEED_ID)
    inj_hit = next(h for h in on["hits"] if h["id"] == JOIN_ID)
    assert set(inj_hit) == set(base_hit)
    assert len(inj_hit) == 18  # +status (C6 #2520)
    # The C6 ``status`` key is asserted BY NAME (a count alone would pass a
    # rename that silently disables the status-only stale clause). Its value
    # is the search payload's ``status`` — absent from the payload for this
    # undecorated non-terminal draft, so it lands as ``""`` here (verified
    # by direct execution; the eval's terminal rows carry the real status).
    assert "status" in inj_hit, inj_hit
    # session_date is derived from the QUESTION's haystack_dates, indexed by
    # the point's lme_session_index — a driver that drops ``dates`` (or the
    # props) leaves it empty. Both sessions are pinned.
    assert inj_hit["session_date"] == "2026-09-05", inj_hit
    assert base_hit["session_date"] == "2026-09-01", base_hit
    assert inj_hit["match_source"] == "fts"


# ── (b) default OFF: byte-identical no-op ─────────────────────────────────

def test_loop_disabled_is_default_byte_identical(seeded_sdk):
    """Default (no kwarg) == explicit ``coverage_loop=False`` == no flag:
    identical ranked ids in the same order — the arm changes NOTHING
    without opt-in (the #1745 fail-safe default decision), and the loop
    stats stay all-zero."""
    import inspect

    from tools.longmem_eval.retrieve import retrieve_for_question
    sig = inspect.signature(retrieve_for_question)
    assert sig.parameters["coverage_loop"].default is None
    q = _question()
    default = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                                    pool_size=60)
    off = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                                pool_size=60, coverage_loop=False)
    assert [h["id"] for h in default["hits"]] == [h["id"] for h in off["hits"]]
    assert default["coverage_loop"] is False
    assert default["coverage_loop_stats"] == off["coverage_loop_stats"]
    assert default["coverage_loop_stats"]["loop_iterations"] == 0
    assert JOIN_ID not in [h["id"] for h in default["hits"]], (
        "default behavior must not run the loop")


def test_eval_env_gate_failsafe_off(seeded_sdk, monkeypatch):
    """The eval A/B gate (``retrieve_for_question`` / env
    ``TORTOISE_LME_COVERAGE_LOOP``) is fail-safe OFF: unset and garbage
    resolve False, only explicit truthy (1/true/yes/on) arms the loop — a
    typo never flips the knob. The outcome records the resolved arm so the
    2×2 A/B with #2518 is reconstructable."""
    from tools.longmem_eval.retrieve import retrieve_for_question
    q = _question()
    monkeypatch.delenv("TORTOISE_LME_COVERAGE_LOOP", raising=False)
    assert retrieve_for_question(
        seeded_sdk, q, ks=(5,), top_k=10,
        pool_size=60)["coverage_loop"] is False
    monkeypatch.setenv("TORTOISE_LME_COVERAGE_LOOP", "garbage")
    assert retrieve_for_question(
        seeded_sdk, q, ks=(5,), top_k=10,
        pool_size=60)["coverage_loop"] is False
    monkeypatch.setenv("TORTOISE_LME_COVERAGE_LOOP", "1")
    on = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                               pool_size=60)
    assert on["coverage_loop"] is True
    assert JOIN_ID in [h["id"] for h in on["hits"][:5]]
    # explicit flag beats the env in both directions
    monkeypatch.setenv("TORTOISE_LME_COVERAGE_LOOP", "1")
    assert retrieve_for_question(
        seeded_sdk, q, ks=(5,), top_k=10, pool_size=60,
        coverage_loop=False)["coverage_loop"] is False
    monkeypatch.delenv("TORTOISE_LME_COVERAGE_LOOP", raising=False)
    assert retrieve_for_question(
        seeded_sdk, q, ks=(5,), top_k=10, pool_size=60,
        coverage_loop=True)["coverage_loop"] is True


# ── (c) the hard iteration bound ──────────────────────────────────────────

def test_loop_hard_one_extra_pass_bound(seeded_sdk, monkeypatch):
    """The ≤1-extra-pass hard bound (§7 iteration-cost creep guard): even
    with TWO census facets left incomplete by the one-shot pool, the loop
    runs exactly ONE targeted second pass (the bound is structural, and the
    per-outcome iteration census records it)."""
    import tortoise.coverage_loop as _cl
    from tools.longmem_eval.retrieve import retrieve_for_question

    calls: list[list] = []
    real_pass = _cl.loop_expansion_pass

    def _counting_pass(proj, query, facets, **kw):
        calls.append([f.key for f in facets])
        return real_pass(proj, query, facets, **kw)

    monkeypatch.setattr(_cl, "loop_expansion_pass", _counting_pass)
    # a second anchor with its own two-session span (seed in session C via
    # a content-matching point, a keys-only join in session D) so the census
    # resolves TWO seeded-but-incomplete facets.
    proj = seeded_sdk._get_proj()
    seeded_sdk.create_entity("object", "kitchen renovation",
                             objectKind="core:other", is_episodic=True)
    seeded_sdk.create_point(
        "event",
        "the kitchen renovation cost 2500 dollars at the end",
        id="p2567sessC0", session_id="sessC2567",
        search_keys="kitchen renovation total paid 2500 dollars",
        status="draft")
    seeded_sdk.create_point(
        "event",
        "the oven light stays on after closing the door",
        id="p2567sessD1", session_id="sessD2567",
        search_keys="tile backsplash labour charge friday invoice",
        status="draft")
    proj.g.query(
        "MATCH (p:Point), (o:Object {name:$name}) "
        "WHERE p.id IN $ids MERGE (p)-[:aboutObject]->(o)",
        params={"name": "kitchen renovation",
                "ids": ["p2567sessC0", "p2567sessD1"]})
    q2 = _question(
        "how much did the road bike repairs and kitchen renovation cost "
        "in total",
        answer_sessions=[SEED_SESSION, JOIN_SESSION, "sessC2567",
                         "sessD2567"])
    on = retrieve_for_question(seeded_sdk, q2, ks=(5,), top_k=10,
                               pool_size=60, coverage_loop=True)
    assert calls, "the loop must fire (two seeded-but-incomplete facets)"
    assert len(calls) == 1, (
        "two missing facets must still run exactly ONE expansion pass "
        "(the hard ≤1-extra-pass bound)")
    assert on["coverage_loop_stats"]["loop_iterations"] == 1
    # the single pass carried BOTH facets' vocabulary (union, not a per-
    # facet iteration) and the missing sessions surfaced inside the top-k.
    assert "p2567sessD1" in [h["id"] for h in on["hits"][:5]]
    assert on["session_recall@k"]["5"] == 1.0


# ── (d) the never-fire rules ──────────────────────────────────────────────

def test_open_ended_question_never_fires(seeded_sdk):
    """Open-ended questions (no countable entity facet — the query's tokens
    resolve no Object-spine anchor) NEVER fire: the census is empty, so ON
    == OFF byte-identical and the markers stay zero."""
    from tools.longmem_eval.retrieve import retrieve_for_question
    q = _question("summarize what happened in the whole conversation",
                  answer_sessions=[SEED_SESSION])
    off = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                                pool_size=60)
    on = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                               pool_size=60, coverage_loop=True)
    assert [h["id"] for h in off["hits"]] == [h["id"] for h in on["hits"]]
    stats = on["coverage_loop_stats"]
    assert on["coverage_loop"] is True
    assert stats["loop_iterations"] == 0
    assert stats["loop_fired_facet"] is None
    assert stats["loop_merged_added"] == 0


def test_complete_single_session_span_never_fires(seeded_sdk):
    """A facet whose census span is FULLY covered by the pool's top window
    never fires — the single-session-user regression shape (the loop must
    not touch questions whose evidence is already complete)."""
    from tools.longmem_eval.retrieve import retrieve_for_question
    # query that resolves the SAME anchor but whose only matching evidence
    # (session A's seed) fully covers the census span restricted to the
    # sessions the fixture's date map knows… here the span has TWO sessions
    # but session B's point cannot match ANY plain query — instead fire the
    # rule with the SPAN single-session shape: an anchor linked to ONE
    # session only.
    proj = seeded_sdk._get_proj()
    seeded_sdk.create_entity("object", "garden herb patch",
                             objectKind="core:other", is_episodic=True)
    seeded_sdk.create_point(
        "event",
        "we grew basil and rosemary in the garden herb patch",
        id="p2567solo0", session_id="soloS2567",
        search_keys="garden herb patch basil rosemary", status="draft")
    proj.g.query(
        "MATCH (p:Point), (o:Object {name:$name}) "
        "WHERE p.id IN $ids MERGE (p)-[:aboutObject]->(o)",
        params={"name": "garden herb patch", "ids": ["p2567solo0"]})
    q = _question("what did we grow in the garden herb patch",
                  answer_sessions=["soloS2567"])
    off = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                                pool_size=60)
    assert "p2567solo0" in [h["id"] for h in off["hits"][:5]]
    on = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                               pool_size=60, coverage_loop=True)
    assert [h["id"] for h in off["hits"]] == [h["id"] for h in on["hits"]], (
        "a fully-covered single-session span must keep the loop a no-op")
    stats = on["coverage_loop_stats"]
    assert stats["loop_iterations"] == 0
    assert stats["loop_fired_facet"] is None


def test_date_qualified_out_of_window_span_never_fires(seeded_sdk):
    """The date-range facet rule: when the query bounds the window and the
    census date map places a span session OUTSIDE it, that session is not
    part of the countable span — a span fully inside the window never
    fires."""
    from tools.longmem_eval.retrieve import retrieve_for_question
    # session A is dated 2026-09-01 (in the [09-01, 09-03] interval); the
    # aboutObject-linked session B is dated 2026-09-05 (OUT of the window).
    q = _question(
        "between 2026-09-01 and 2026-09-03 how much did the road bike "
        "repairs cost me",
        answer_sessions=[SEED_SESSION])
    on = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                               pool_size=60, coverage_loop=True,
                               )
    assert on["coverage_loop"] is True
    assert on["coverage_loop_stats"]["loop_iterations"] == 0, (
        "an out-of-window date-qualified span must not fire the loop")
    assert on["coverage_loop_stats"]["loop_fired_facet"] is None


# ── (e) fail-open: any failure keeps the ORIGINAL pool ────────────────────

def test_loop_fail_open_keeps_original_result(seeded_sdk, monkeypatch):
    """Any loop failure (census graph fault, expansion fault) keeps the
    ORIGINAL result byte-identical — the loop can never turn a working lane
    into a broken one (the A4/C2 fail-open posture)."""
    import tortoise.coverage_loop as _cl
    from tools.longmem_eval.retrieve import retrieve_for_question
    q = _question()
    baseline = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                                     pool_size=60, coverage_loop=False)

    def _boom(*a, **k):
        raise RuntimeError("injected census failure")

    monkeypatch.setattr(_cl, "facet_census", _boom)
    on = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                               pool_size=60, coverage_loop=True)
    assert [h["id"] for h in baseline["hits"]] == [h["id"] for h in on["hits"]]
    assert [h["id"] for h in baseline["hits"][:5]] == \
        [h["id"] for h in on["hits"][:5]]
    assert on["coverage_loop"] is True
    assert on["coverage_loop_stats"]["loop_iterations"] == 0
    assert on["coverage_loop_stats"]["loop_fired_facet"] is None
    assert on["coverage_loop_stats"]["loop_merged_added"] == 0


# ══ C3-3 (#2569): coverage-signal routing through the #2521 detector ════════
#
# The routed check fires where the proxy cannot: an entity-scoped AGGREGATION
# question whose anchor is NOT seeded in the guard window (k = 0 — the §1
# "never reached" 13% miss class). The proxy deliberately skips that shape
# (an anchor the pool never touches is index noise to a SESSION-span census);
# the #2521 detector's per-facet check has no seeded precondition, so routing
# turns that miss class into a fire. The routed facets are UNIONED with the
# proxy's (never swapped), so the interim cannot regress.

#: the starved-anchor fixture (the never-reached class): the anchor's ONLY
#: linked point sits in a session absent from the one-shot pool — its content
#: shares NO query token, so only its E3 search_keys can join it back.
STARVED_ID = "p2569starved"
STARVED_SESSION = "sessC2569"
STARVED_KEYS = "extra charge for wheels saturday service fee"
STARVED_BODY = ("on saturday morning the weather was sunny so i went for a "
                "long walk instead")


def _starved_question(question: str = QUESTION) -> dict:
    return _question(question,
                     answer_sessions=[STARVED_SESSION],
                     haystack_dates=["2026-09-01", "2026-09-05"])


@pytest.fixture
def seeded_starved_sdk(monkeypatch):
    """The C3-3 starved-anchor fixture: ONE Object anchor linked to ONE point
    whose session the one-shot pool never reaches, plus the same 12
    zero-overlap statement distractors (so the pool is non-empty and the
    guard window is filled by sessions that carry no census facet)."""
    monkeypatch.setenv("TORTOISE_DB_URI", _fresh_uri())
    sdk = TortoiseSDK()
    try:
        proj = sdk._get_proj()
        sdk.create_entity("object", ENTITY_ANCHOR, objectKind="core:other",
                          is_episodic=True)
        sdk.create_point("event", STARVED_BODY, id=STARVED_ID,
                         session_id=STARVED_SESSION, lme_session_index=1,
                         search_keys=STARVED_KEYS, status="draft")
        for i in range(12):
            sdk.create_point(
                "statement",
                f"{DISTRACTOR_TOPIC} number {i}",
                id=f"p2569dist{i}", session_id=f"dist2569{i}",
                status="draft")
        proj.g.query(
            "MATCH (p:Point {id:$id}), (o:Object {name:$name}) "
            "MERGE (p)-[:aboutObject]->(o)",
            params={"id": STARVED_ID, "name": ENTITY_ANCHOR})
        yield sdk
    finally:
        sdk.close()


def test_routed_check_fires_where_the_proxy_is_silent(seeded_starved_sdk):
    """The acceptance differential (§5, MSR session_recall@5): the starved
    session is absent from the one-shot pool AND from the guard window, so
    the rule proxy does NOT fire (loop-ON/detector-OFF == baseline). Routing
    the check through the #2521 detector fires it (k = 0 is a miss, not
    noise), the anchor spine's E3 vocabulary recovers the point, and the
    official window moves 0 → 1. The detector arm ALONE stays
    measurement-only (the #2521 contract)."""
    from tools.longmem_eval.retrieve import retrieve_for_question
    q = _starved_question()

    base = retrieve_for_question(seeded_starved_sdk, q, ks=(5,), top_k=10,
                                 pool_size=60)
    assert STARVED_ID not in [h["id"] for h in base["hits"]]
    assert base["session_recall@k"]["5"] == 0.0

    # the rule-proxy interim: armed loop, detector OFF — no fire, no change
    interim = retrieve_for_question(seeded_starved_sdk, q, ks=(5,), top_k=10,
                                    pool_size=60, coverage_loop=True)
    assert [h["id"] for h in interim["hits"]] == \
        [h["id"] for h in base["hits"]], "the interim must not regress"
    st = interim["coverage_loop_stats"]
    assert st["loop_proxy_missing"] == 0, "the proxy cannot see k = 0"
    assert st["loop_iterations"] == 0
    assert st["loop_check_authority"] == "rule-proxy"
    assert st["loop_detector_signal"] == "none"

    # the #2521 detector arm ALONE is measurement-only (no retrieval change)
    det_only = retrieve_for_question(seeded_starved_sdk, q, ks=(5,), top_k=10,
                                     pool_size=60, aggregative_flag=True)
    assert [h["id"] for h in det_only["hits"]] == \
        [h["id"] for h in base["hits"]]

    # loop ON + detector arm ON ⇒ the routed check fires and recovers
    routed = retrieve_for_question(seeded_starved_sdk, q, ks=(5,), top_k=10,
                                   pool_size=60, coverage_loop=True,
                                   aggregative_flag=True)
    ids = [h["id"] for h in routed["hits"]]
    assert STARVED_ID in ids[:5], (
        "the routed check must surface the never-reached session's evidence "
        "inside the guard window")
    assert routed["session_recall@k"]["5"] == 1.0
    st = routed["coverage_loop_stats"]
    assert st["loop_check_authority"] == "detector"
    assert st["loop_routed_dimension"] == "session"
    assert st["loop_detector_signal"] == "partial"
    assert st["loop_detector_missing"] == 1
    assert st["loop_proxy_missing"] == 0
    assert st["loop_iterations"] == 1
    assert st["loop_fired_facet"] == f"session:{STARVED_SESSION}"
    assert st["loop_merged_added"] >= 1


def test_routed_union_keeps_the_proxy_interim_byte_stable(seeded_sdk):
    """No-regression, structurally: on the C3-1 fixture BOTH checks fire on
    the same anchor, the proxy's facet keeps its slot (``loop_fired_facet``
    is still the proxy's ``entity:`` key), and the routed facet contributes
    no new vocabulary (it reuses the same harvested anchor aliases), so the
    expanded pool is IDENTICAL to the interim's — the routed check adds
    reach, never a reordering."""
    from tools.longmem_eval.retrieve import retrieve_for_question
    q = _question()
    interim = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                                    pool_size=60, coverage_loop=True)
    routed = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                                   pool_size=60, coverage_loop=True,
                                   aggregative_flag=True)
    assert interim["coverage_loop_stats"]["loop_proxy_missing"] == 1
    assert routed["coverage_loop_stats"]["loop_proxy_missing"] == 1
    assert routed["coverage_loop_stats"]["loop_check_authority"] == "detector"
    assert routed["coverage_loop_stats"]["loop_detector_missing"] == 1
    assert routed["coverage_loop_stats"]["loop_fired_facet"] == \
        interim["coverage_loop_stats"]["loop_fired_facet"] == \
        f"entity:{ENTITY_ANCHOR}"
    assert [h["id"] for h in routed["hits"]] == \
        [h["id"] for h in interim["hits"]]


def test_routed_check_parity_with_the_detector_one_call_seam(seeded_sdk):
    """Single-source pin: the routing composes the #2521 primitives
    (``collect_anchor_census`` + ``compute_facet_coverage``), so its verdict
    must agree with the detector's own one-call seam (``aggregative_verdict``)
    on the same pool window — a drift here means the routing re-derived a
    facet, a key or a coverage number of its own."""
    from tools.longmem_eval.retrieve import retrieve_for_question
    from tortoise.aggregate import aggregative_verdict
    from tortoise.coverage_loop import detector_coverage_gap, facet_census

    q = _question()
    out = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                                pool_size=60)
    pool = list(out["hits"])
    missing, report = detector_coverage_gap(
        seeded_sdk._get_proj(), QUESTION, pool, window=5)
    verdict = aggregative_verdict(
        query=QUESTION, proj=seeded_sdk._get_proj(),
        retrieved_points=pool[:5])
    assert report["signal"] == verdict["signal"] == "partial"
    assert report["n_facets"] == verdict["n_facets"] == 2
    assert report["retrieved_facets"] == verdict["retrieved_facets"] == 1
    assert report["missing_facets"] == verdict["missing_facets"]
    assert report["anchors"] == verdict["anchors"]
    # the routed facet names the missing session
    assert [f.key for f in missing] == [
        f"session:{s}" for s in verdict["missing_facets"]]
    assert missing[0].span == frozenset({JOIN_SESSION})
    # the vocabulary is the ALREADY-HARVESTED anchor spine: with the driver's
    # ``anchor_aliases`` the routed facet carries EXACTLY the proxy census
    # facet's aliases (one harvest — the routing never re-fetches E3 keys);
    # without it, the token-overlap-gated anchor-name fallback.
    proxy_facet = next(
        f for f in facet_census(seeded_sdk._get_proj(), QUESTION)
        if f.name == ENTITY_ANCHOR)
    assert missing[0].aliases == (ENTITY_ANCHOR,)
    enriched, _ = detector_coverage_gap(
        seeded_sdk._get_proj(), QUESTION, pool, window=5,
        anchor_aliases={proxy_facet.name: proxy_facet.aliases})
    assert enriched[0].aliases == proxy_facet.aliases
    assert enriched[0].aliases[0] == ENTITY_ANCHOR


def test_routed_temporal_facet_counts_the_month_axis(seeded_sdk):
    """The temporal facet at v1 depth: a date-cued aggregation ("per month")
    routes to the detector's ``date_month`` axis, whose facets are CALENDAR
    MONTHS rather than sessions — the census months come from the graph's
    ``session_date`` prop, the retrieved months from the annotated pool. A
    month known to the census but absent from the window is the fired
    facet."""
    from tools.longmem_eval.retrieve import retrieve_for_question
    from tortoise.coverage_loop import detector_coverage_gap
    proj = seeded_sdk._get_proj()
    # a SECOND anchor whose linked points live in two different months
    seeded_sdk.create_entity("object", "studio rent",
                             objectKind="core:other", is_episodic=True)
    seeded_sdk.create_point(
        "event", "the studio rent payment went through on time",
        id="p2569rentSep", session_id="sessSep2569",
        search_keys="studio rent payment september", status="draft")
    seeded_sdk.create_point(
        "event", "another month another desk plant",
        id="p2569rentOct", session_id="sessOct2569",
        search_keys="studio rent payment october", status="draft")
    proj.g.query(
        "MATCH (p:Point), (o:Object {name:'studio rent'}) "
        "WHERE p.id IN $ids MERGE (p)-[:aboutObject]->(o)",
        params={"ids": ["p2569rentSep", "p2569rentOct"]})
    proj.g.query(
        "MATCH (p:Point) WHERE p.id IN $ids "
        "SET p.session_date = CASE p.id "
        "  WHEN 'p2569rentSep' THEN '2026-09-03' ELSE '2026-10-07' END",
        params={"ids": ["p2569rentSep", "p2569rentOct"]})
    q = {
        "question_id": "q2569month",
        "question": "how much did the studio rent cost me per month",
        "question_type": "multi-session-reasoning",
        "answer_session_ids": ["sessSep2569", "sessOct2569"],
        "haystack_session_ids": ["sessSep2569", "sessOct2569"],
        "haystack_dates": ["2026-09-03", "2026-10-07"],
        "haystack_sessions": [],
        "answer": "",
    }
    out = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                                pool_size=60, coverage_loop=True,
                                aggregative_flag=True)
    st = out["coverage_loop_stats"]
    assert st["loop_check_authority"] == "detector"
    assert st["loop_routed_dimension"] == "date_month"
    assert st["loop_detector_signal"] == "partial"
    assert st["loop_detector_missing"] == 1
    # the MONTH axis is the routed check's: the census knows two calendar
    # months, the pool window holds one — the missing month is the detector's
    # missing facet (the proxy's own entity facet fires first in the union by
    # design, so the axis claim is asserted on the product seam).
    missing, report = detector_coverage_gap(
        seeded_sdk._get_proj(), q["question"], list(out["hits"]), window=5)
    assert report["dimension"] == "date_month"
    assert report["n_facets"] == 2
    assert report["retrieved_facets"] == 1
    assert report["missing_facets"] == ["2026-10"]
    assert [f.key for f in missing] == ["date_month:2026-10"]
    # the temporal facet's vocabulary = harvested anchor keys + the month key
    assert "2026-10" in missing[0].aliases


def test_routed_recency_window_filters_the_census(seeded_sdk):
    """#8710, end-to-end: a recency-bounded aggregation question no longer
    raises through the R5 seam — the census is date-qualified on the point
    grain, so the out-of-window session can never be named as a missing
    facet (and the loop does not fail open)."""
    from tools.longmem_eval.retrieve import retrieve_for_question
    from tortoise.coverage_loop import detector_coverage_gap
    proj = seeded_sdk._get_proj()
    proj.g.query(
        "MATCH (p:Point) WHERE p.id IN $ids "
        "SET p.session_date = CASE p.id "
        "  WHEN $seed THEN '2026-09-01' ELSE '2025-01-01' END",
        params={"ids": [SEED_ID, JOIN_ID], "seed": SEED_ID})
    q = {
        "question_id": "q2569recency",
        "question": ("how much did the road bike repairs cost me in the "
                     "last 7 days"),
        "question_type": "multi-session-reasoning",
        "answer_session_ids": [SEED_SESSION],
        "haystack_session_ids": [SEED_SESSION, JOIN_SESSION],
        "haystack_dates": ["2026-09-01", "2025-01-01"],
        "haystack_sessions": [],
        "answer": "",
    }
    out = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                                pool_size=60, coverage_loop=True,
                                aggregative_flag=True)
    st = out["coverage_loop_stats"]
    assert st["loop_detector_signal"] == "complete", (
        "the out-of-window census session must not be a missing facet")
    assert st["loop_iterations"] == 0
    # and the product seam itself reports the measurement (never a fail-open
    # reason): the R5 qualification ran instead of raising
    missing, report = detector_coverage_gap(
        proj, q["question"], list(out["hits"]), window=5,
        session_dates={SEED_SESSION: "2026-09-01",
                       JOIN_SESSION: "2025-01-01"})
    assert report["reason"] == "measured"
    assert report["n_facets"] == 1
    assert missing == []

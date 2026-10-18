"""C3-1 (#2519/#2567): the evidence-completeness retrieval loop — product side.

The multi-session partial-evidence lever (docs/scoping/2026-09-08-2519-
coverage-loop.md §3 mechanism (a), program C3): a one-shot top-k retrieve
cannot enumerate ALL sessions that carry an aggregation question's subject —
k<N stays 0 on the official all-or-nothing ``recall_all@5`` binary. This
module ships the **retrieve → check → expand → merge** completeness loop as
hermetic product primitives (the product-cohesion rule — the loop lands in
``tortoise/`` and the LongMemEval harness measures it):

1. **CENSUS** — :func:`facet_census`: a RULE-BASED facet census over the
   query + the graph (NO model, bounded): countable entity-scoped facets
   only. The query's own entities resolve through the Object-name spine
   (the same anchor resolution #2518 uses); each anchor's census span = the
   distinct sessions carrying points ``-[:aboutObject]->``-linked to it,
   optionally date-qualified by an interval/recency bound detected in the
   query text (the R5 ``detect_time_constraint`` seam's shapes).
2. **CHECK** — :func:`coverage_gap`: is the retrieved set facet-incomplete
   against the census? Fires only for an ENTITY-SCOPED facet whose span is
   seeded in the top window yet not fully covered by it (partial evidence
   — the §7 "facet-based, never re-rank the pool that missed" rule).
   Open-ended queries (no countable facet) never fire.
3. **EXPAND** — :func:`loop_expansion_pass`: ONE targeted second sparse pass
   for the missing facet (hard ``LOOP_MAX_EXTRA_PASSES`` = 1 bound), reusing
   the A4/C2 reserved-slot OR contract (original query tokens keep their
   slots; the missing facet's alias vocabulary fills the bounded expansion
   tail) — a re-QUERY for the missing facet, never a re-rank of the pool
   that missed.
4. **MERGE** — :func:`merge_expansion_order` + :func:`session_diverse_order`
   (pure): the additive union in second-pass relevance order (base hits the
   sparse re-query re-finds keep their slots — the A4 leg-merge contract at
   pool level) followed by the mandatory session-diverse window discipline
   (§3(d)) — no session may hold more than the per-session cap of the guard
   window, so a same-session flood (points were never session-capped)
   cannot crowd the cross-session recovery evidence out of the window the
   official binary grades.
5. **ROUTE** (C3-3 #2569) — :func:`route_coverage_signal` + 
   :func:`detector_coverage_gap` + :func:`combine_missing_facets`: the CHECK
   is routed through the #2521 aggregative-intent detector
   (``tortoise/aggregate.py``) for entity-scoped aggregation/spread
   questions, which name the facet AXIS to count (``session`` /``kind`` /
   ``date_month``) and — unlike the proxy — fire at ``k = 0`` (the
   never-reached miss class). The routed check is UNIONED with the proxy's
   (never a swap): non-aggregative questions keep the interim untouched and
   the proxy's facets keep the expansion tail's slots, so the routed path
   can only ADD a fire opportunity — never regress the interim. The
   temporal facet rides the same R5 time seam
   (:func:`facet_window_bounds`).

Every graph step is bounded and fail-open (any fetch/query failure keeps
the ORIGINAL pool — byte-identical, the A4/C2 posture); the loop is OFF by
default (the #1745 fail-safe decision) and the eval arms it via
``--coverage-loop`` / ``TORTOISE_LME_COVERAGE_LOOP`` (2×2 covariate with the
#2518 entity-key expansion arm) and the routing via the #2521 detector arm
``--aggregative-flag`` / ``TORTOISE_LME_AGGREGATIVE_FLAG`` (loop-ON +
aggregative-OFF is the rule-proxy interim).

Stage ownership: this module owns the product rules + graph passes. The
harness's ``retrieve_for_question`` (tools/longmem_eval/retrieve.py) composes
census → gap → expand → merge on the ANNOTATED pool (session linkage lives
there) and records the per-outcome markers (``loop_iterations`` /
``loop_fired_facet`` / ``loop_merged_added``). The ask lane adopts the same
seams when the sealed A/B gate lands.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# ── loop budget + merge-discipline constants (the hard, documented bounds) ──
#: Hard iteration bound: the loop may run at most ONE extra retrieval pass
#: beyond the one-shot pass (§7 iteration-cost-creep guard; §8 census).
LOOP_MAX_EXTRA_PASSES = 1

#: The top-k guard window the loop protects (the all-or-nothing recall
#: window the official binary grades; the eval's ``ks`` starts at 5).
DEFAULT_LOOP_GUARD_WINDOW = 5

#: Merge-discipline per-session cap INSIDE the guard window: a session may
#: hold at most this many of the window's ranks, so one session can never
#: starve the other N−1 answer sessions out of the window (§3(d)/§7).
DEFAULT_LOOP_SESSION_CAP = 2

#: Census span cap — the distinct-session harvest per anchor is bounded so
#: a high-degree anchor cannot unboundedly feed the check (like the #2518
#: alias caps; the span is a coverage CENSUS, never a full enumeration).
DEFAULT_LOOP_SPAN_CAP = 64

#: Date-qualified facet window shape (the R5 seam's interval shapes) —
#: mirrored from tools/longmem_eval/retrieve.py::detect_time_constraint so
#: the product census can qualify an entity facet's span WITHOUT importing
#: the harness (the eval's function stays authoritative for TR filtering).
_DATE_INTERVAL_ISO_RE = re.compile(
    r"between\s+(\d{4}-\d{2}-\d{2})\s+and\s+(\d{4}-\d{2}-\d{2})")
_DATE_RECENCY_RE = re.compile(
    r"(?:\b(\d+)\s+(days?|weeks?|months?)\s+ago\b"
    r"|\blast\s+(\d+)\s+(days?|weeks?|months?)\b)")
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30}

#: Fallback date (mirrors the eval seam) — anchors month-day interval bounds
#: whose year the query does not carry (never used by the eval which passes
#: question dates; kept for product-side determinism).
_DEFAULT_YEAR = 2026

#: C3-3 (#2569) routing authorities — which check owns the coverage decision
#: for one query. The detector authority is the #2521 per-facet check; the
#: rule-proxy authority is C3-1's session-span census (the interim).
ROUTE_AUTHORITY_DETECTOR = "detector"
ROUTE_AUTHORITY_PROXY = "rule-proxy"


@dataclass(frozen=True)
class LoopFacet:
    """One countable, entity-scoped facet of the query.

    ``kind``: "entity" (an Object-spine anchor named by the query) —
    date-range bounds QUALIFY an entity facet's span; a date alone never
    enumerates a facet (open-ended never fires, §3(a) rule proxy).
    ``key``: stable facet identity ("entity:<name>") for the fired-facet
    marker. ``span``: the census-known session ids carrying the facet
    (bounded). ``aliases``: the additive expansion vocabulary (the anchor's
    name + linked points' E3 ``search_keys`` — the vocabulary that lets a
    same-subject point from a starved session match a re-query).
    """
    kind: str
    key: str
    name: str
    span: frozenset[str]
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class CoverageRoute:
    """The C3-3 (#2569) facet-router decision for one query.

    ``authority`` — who owns the coverage check: ``"detector"`` (the #2521
    per-facet check) or ``"rule-proxy"`` (C3-1's session-span census, the
    interim). ``dimension`` — the facet axis the check enumerates
    (``session``/``kind``/``date_month``; the proxy's is always
    ``session``). ``routed`` — True iff the detector owns the check.
    ``quantifier``/``scope`` — the detector's own classification
    (audit/marker vocabulary, never re-derived here). ``reason`` — why the
    route fell where it did (``routed``/``not_aggregative``/``open_ended``/
    ``unscoped``), so a per-outcome marker can name the non-fire cause.
    """
    authority: str
    dimension: str
    routed: bool
    is_aggregative: bool
    quantifier: str | None = None
    scope: str | None = None
    reason: str = "not_aggregative"


def facet_date_constraint(text: str) -> tuple[str | None, str | None, str | None]:
    """Detect a countable date bound in the query (pure; the R5 seam's
    interval/recency shapes). Returns (kind, start, end) — kind is
    "interval" | "recency" | None. Mirrors the eval seam's regexes so the
    two can never drift on the shapes the census qualifies on. An interval
    bound is an inclusive ISO window; a recency bound carries the day count
    in ``start`` (the caller applies it against its session-date source).
    """
    t = " ".join(text.lower().split())
    m = _DATE_INTERVAL_ISO_RE.search(t)
    if m:
        return "interval", m.group(1), m.group(2)
    m = _DATE_RECENCY_RE.search(t)
    if m:
        n = int(m.group(1) or m.group(3))
        unit = (m.group(2) or m.group(4)).rstrip("s")
        return "recency", str(n * _UNIT_DAYS[unit]), None
    return None, None, None


def facet_window_bounds(kind: str | None, start: str | None,
                        end: str | None,
                        session_dates: dict[str, str] | None,
                        ) -> tuple[str | None, str | None]:
    """Resolve the R5-shaped date constraint into an INCLUSIVE ISO
    ``[lo, hi]`` window (pure). ``(None, None)`` when no bound applies.

    interval: the explicit ISO bounds. recency: ``[qdate − N_days, qdate]``
    where the question date is the max session date available (the eval's
    haystack dates; the graph has no global clock).

    The recency bound is resolved through ``date.fromisoformat`` — the
    session dates the caller passes ARE ISO strings (the eval's
    ``haystack_dates``), and a raw ``str − timedelta`` raises ``TypeError``
    (#8710: the pre-fix ``_span_in_window`` recency branch raised on every
    recency-bounded query, so the loop's temporal facet was dead for the
    whole "last N …" class and the eval's fail-open swallowed it). An
    unparseable latest date degrades to no bound (never a hard filter that
    could starve the pool).
    """
    if kind == "interval":
        return (start, end) if start and end else (None, None)
    if kind == "recency":
        dates = [str(v).strip() for v in (session_dates or {}).values() if v]
        if not dates:
            return None, None
        qdate = max(dates)
        from datetime import date, timedelta
        try:
            qd = date.fromisoformat(qdate[:10])
            n_days = int(start or 0)
        except ValueError:
            return None, None
        return (qd - timedelta(days=n_days)).isoformat(), qdate
    return None, None


def _span_in_window(span: frozenset[str],
                    session_dates: dict[str, str] | None,
                    kind: str | None, start: str | None,
                    end: str | None) -> frozenset[str]:
    """Date-qualify a facet's session span (pure). interval: keep sessions
    whose date ∈ [start, end]; recency: keep sessions whose date ∈
    [qdate − N_days, qdate] where the question date is the max session date
    available (the eval's haystack dates; the graph has no global clock).
    Unknown dates are kept when no bound applies; when a bound applies an
    undated session cannot be proven in-window and is dropped (the span
    stays honest — a facet whose whole span filters out is dropped by the
    caller).
    """
    if kind is None or not session_dates:
        return span
    lo, hi = facet_window_bounds(kind, start, end, session_dates)
    if lo is None or hi is None:
        return span
    return frozenset(
        s for s in span
        if (session_dates.get(s) or "") and lo <= session_dates[s] <= hi)


def points_in_window(points: list[dict],
                     session_dates: dict[str, str] | None,
                     kind: str | None, start: str | None,
                     end: str | None) -> list[dict]:
    """Date-qualify a CENSUS POINT list with the SAME R5 seam
    (:func:`facet_window_bounds`) the span qualification uses (pure) — the
    routed detection path's temporal facet (C3-3 #2569). A point whose
    ``session_date`` cannot be proven in-window is dropped when a bound
    applies, exactly as :func:`_span_in_window` drops an undated session.
    """
    if kind is None:
        return points
    lo, hi = facet_window_bounds(kind, start, end, session_dates)
    if lo is None or hi is None:
        return points
    out: list[dict] = []
    for p in points:
        d = str(p.get("session_date") or "").strip()
        if d and lo <= d[:10] <= hi:
            out.append(p)
    return out


def _token_overlap(query: str, name: str, *, keep_numeric: bool = False) -> bool:
    """Quality gate (pure): the anchor's name must share a sparse token with
    the query (mirrors #2518's "a question that names its subject maps to
    the Object nodes whose names share its tokens" — an Object that scored
    on the FTS index without token overlap is index noise, not the query's
    facet)."""
    from tortoise.sparse import tokenize_sparse_query
    q_toks = set(tokenize_sparse_query(query, keep_numeric=keep_numeric))
    if not q_toks:
        return False
    n_toks = set(tokenize_sparse_query(name, keep_numeric=True))
    return bool(q_toks & n_toks)


def facet_census(proj: Any, query: str, *,
                 session_dates: dict[str, str] | None = None,
                 keep_numeric: bool = False,
                 max_anchors: int | None = None,
                 max_span_sessions: int | None = None) -> list[LoopFacet]:
    """The rule-based facet census (step 1): resolve the query's own entity
    anchors THROUGH the Object-name index (the #2518 spine) and harvest each
    anchor's session span + expansion vocabulary from its ``aboutObject``-
    linked points (bounded, one batched query). Fail-open: any graph failure
    yields [] (no facets → the check can never fire → the caller keeps the
    ORIGINAL pool byte-identical).

    ``session_dates``: {session_id: ISO-date} over the query's haystack —
    when provided AND the query text carries an interval/recency bound, each
    anchor's span is date-qualified to the in-window sessions (the §4 P0
    date-range facet). ``max_anchors``/``max_span_sessions`` override the
    documented bounds (tests pin the defaults).
    """
    if not query or not query.strip():
        return []
    # C2 (#2518) anchor numbers are single-sourced from the SDK (lazy import
    # — this module must never create an import cycle with tortoise.sdk).
    from tortoise.sdk import (
        _ENTITY_ALIAS_MAX,
        _ENTITY_ALIAS_PER_ANCHOR,
        _ENTITY_ANCHOR_CANDIDATES,
        _ENTITY_ANCHOR_LIMIT,
    )
    anchors_n = max_anchors if max_anchors is not None else _ENTITY_ANCHOR_LIMIT
    span_cap = (max_span_sessions if max_span_sessions is not None
                else DEFAULT_LOOP_SPAN_CAP)
    date_kind, date_start, date_end = facet_date_constraint(query)
    try:
        from tortoise.search_engine import run_fts_query
        anchor_rows = run_fts_query(
            proj.g, query, entity_type="object",
            limit=_ENTITY_ANCHOR_CANDIDATES,
            keep_numeric=keep_numeric,
            # #3301: anchor RESOLUTION only — the census harvests names,
            # never surfaces these Objects, so terminal Objects stay
            # resolvable (the assembly-resolver principle).
            excluded_statuses=())
    except Exception:  # noqa: BLE001, RUF100
        logger.warning(
            "C3-1 facet census anchor resolution failed — loop no-ops "
            "(the original pool is kept)", exc_info=True)
        return []
    if not anchor_rows:
        return []
    # deterministic anchor order: (score desc, id asc) — the C2 tiebreak.
    anchors = sorted(anchor_rows, key=lambda r: (-r[1], r[0]))[:anchors_n]
    anchor_ids = [aid for aid, _score in anchors]
    try:
        rows = proj.g.query(
            "MATCH (o:Object) WHERE o.id IN $ids "
            "OPTIONAL MATCH (o)<-[:aboutObject]-(p:Point) "
            "RETURN o.id, o.name, "
            "       collect(DISTINCT coalesce(p.session_id, '')), "
            "       collect(coalesce(p.search_keys, ''))",
            params={"ids": anchor_ids},
        ).result_set
    except Exception:  # noqa: BLE001, RUF100
        logger.warning(
            "C3-1 facet census harvest failed — loop no-ops (the original "
            "pool is kept)", exc_info=True)
        return []
    facets: list[LoopFacet] = []
    for row in rows or []:
        _oid, name = row[0], str(row[1] or "")
        if not name or not _token_overlap(query, name, keep_numeric=keep_numeric):
            continue
        sessions: list[str] = []
        aliases: list[str] = []
        per_anchor = 0
        for v in (row[3] or []):
            if per_anchor >= _ENTITY_ALIAS_PER_ANCHOR:
                break
            values = ([str(x) for x in v if x]
                      if isinstance(v, (list, tuple)) else [str(v)] if v else [])
            for x in values:
                x = x.strip()
                if x and per_anchor < _ENTITY_ALIAS_PER_ANCHOR:
                    aliases.append(x)
                    per_anchor += 1
        seen_sess: set[str] = set()
        for v in (row[2] or []):
            s = str(v or "").strip()
            if s and s not in seen_sess:
                seen_sess.add(s)
                sessions.append(s)
            if len(sessions) >= span_cap:
                break
        if not sessions:
            # no session-linked content → nothing countable to cover
            continue
        span = _span_in_window(frozenset(sessions), session_dates,
                               date_kind, date_start, date_end)
        if not span:
            continue
        # deterministic alias pool: sorted, deduped, capped (C2's ordering —
        # DB collect() order is not cross-run stable).
        ordered: list[str] = []
        seen_alias: set[str] = set()
        for a in sorted(aliases):
            if a in seen_alias:
                continue
            seen_alias.add(a)
            ordered.append(a)
            if len(ordered) >= _ENTITY_ALIAS_MAX:
                break
        aliases = ordered
        if name not in aliases:
            aliases = [name, *aliases]
        facets.append(LoopFacet(
            kind="entity", key=f"entity:{name}", name=name,
            span=span, aliases=tuple(aliases[: _ENTITY_ALIAS_MAX])))
    return facets


def _session_of(hit: dict, session_key: Callable[[dict], str] | None = None) -> str:
    """A hit's session identity — delegates to the pool's AUTHORITY
    (``retrieval.session_key_of``: ``session_id`` when present, else the
    ``idx:{lme_session_index}`` bucket) so the loop's window census and the
    pool's dedup never disagree on what a session is. The import is
    FUNCTION-LOCAL: ``coverage_loop`` stays a stdlib-only leaf at import
    time and the ``retrieval → coverage_loop`` dependency stays one-way
    (no cycle under either import order).
    """
    if session_key is not None:
        return session_key(hit)
    from tortoise.retrieval import session_key_of
    return session_key_of(hit)


def coverage_gap(facets: list[LoopFacet], pool: list[dict], *,
                 window: int = DEFAULT_LOOP_GUARD_WINDOW,
                 session_key: Callable[[dict], str] | None = None,
                 ) -> list[LoopFacet]:
    """The completeness CHECK (step 2, pure): which census facets is the
    retrieved pool facet-INCOMPLETE against?

    Returns every census facet F where:
      * F is SEEDED — at least one of F's span sessions is represented in
        the pool's top-``window`` ranks (the pool IS about this subject —
        an anchor the pool never touches is index noise, not a miss), and
      * F is UNDER-COVERED — F's span is not fully inside the window's
        sessions (a countable span session is missing from the window the
        official binary grades: partial evidence).

    Open-ended queries produce no facets → no fire, by construction. A span
    fully inside the window (single-session subjects, complete coverage)
    never fires. Deterministic: the returned list keeps the census order
    (the driver fires on the first entry; the ONE expand pass unions the
    missing facets' vocabulary — the hard ≤1-extra-pass bound).
    """
    if not facets or not pool:
        return []
    window = max(1, min(window, len(pool)))
    win_sessions = {_session_of(h, session_key) for h in pool[:window]}
    # A synthetic ``idx:*`` key names no graph session — the ``lme_session_
    # index`` bucket, and since #3591 the per-hit ``idx:point:{id}`` bucket
    # of a hit with no session identity — so it can never be a session a
    # census ``span`` names. Drop the whole namespace rather than the one
    # literal ``idx:-1``: after #3591 the per-hit bucket would otherwise
    # read as a session PRESENT in the window, which is the opposite of
    # what it means.
    win_sessions = {k for k in win_sessions
                    if k and not k.startswith("idx:")}
    missing: list[LoopFacet] = []
    for facet in facets:
        span = set(facet.span)
        if not span:
            continue
        if span & win_sessions and not span <= win_sessions:
            missing.append(facet)
    return missing


def _aliases_add_recall(query: str, aliases: tuple[str, ...],
                        *, keep_numeric: bool = False) -> bool:
    """The no-op guard (pure): an alias pool that tokenizes to ONLY the
    original query's tokens cannot add recall (the reserved-slot OR contract
    would drop it all) — skip the wasted second pass (C2's guard)."""
    from tortoise.sparse import tokenize_sparse_query
    orig = set(tokenize_sparse_query(query, keep_numeric=keep_numeric))
    if not orig:
        return False
    new_toks: set[str] = set()
    for alias in aliases:
        new_toks.update(tokenize_sparse_query(alias, keep_numeric=True))
    return bool(new_toks - orig)


# ── C3-3 (#2569): the detector-owned coverage-signal routing ───────────────
#
# The #2521 detector (``tortoise/aggregate.py``) owns the facet vocabulary:
# ``detect_aggregative_intent`` classifies the query + names the aggregation
# axis, ``collect_anchor_census`` enumerates the entity spine's linked
# points, ``compute_facet_coverage`` is the pure k-of-N per-facet math and
# ``facet_key_for_point`` the facet-key extraction. This section is WIRING
# ONLY: it routes the C3-1 loop's CHECK through those primitives when the
# detector is armed, and leaves the rule proxy exactly where it was
# otherwise. Nothing here re-derives a facet, a key or a coverage verdict.
#
# Two properties the routing must preserve (they are the reason it is a
# UNION and not a swap):
#
#   * **no regression on the rule-proxy interim** — a non-aggregative (or
#     open-ended, or un-anchored) query keeps the proxy's check untouched, so
#     the routed path can only ever ADD a fire opportunity for an
#     aggregation question, never remove one. That matters because the proxy
#     carries the E3 ``search_keys`` expansion vocabulary, which the routed
#     census does not harvest;
#   * **one bounded pass either way** — the union feeds the SAME single
#     ``loop_expansion_pass`` (the hard ``LOOP_MAX_EXTRA_PASSES`` = 1 bound),
#     so routing cannot raise the iteration census (§7 cost-creep guard).


def route_coverage_signal(query: str | None) -> CoverageRoute:
    """The facet router (C3-3 #2569, pure): decide which check owns the
    coverage decision for this query.

    The #2521 detector owns it for an **entity-scoped aggregation/spread**
    surface ("how many/how much/how often/total/count/every") — the measured
    MSR miss shape — and names the axis it aggregates on
    (``session``/``kind``/``date_month``). Everything else (not aggregative,
    open-ended corpus-self-reference, no quantifier) routes back to C3-1's
    rule proxy, so the interim is never dropped.

    Surface-only and hermetic: the AUTHORITATIVE entity-scope gate stays the
    detector's anchor RESOLUTION (see :func:`detector_coverage_gap`) — an
    entity-scoped surface that resolves no Object anchor emits no signal
    (fail-open, never a false flag).
    """
    from tortoise.aggregate import (
        DEFAULT_FACET_DIMENSION,
        FACET_DIMENSIONS,
        detect_aggregative_intent,
    )
    intent = detect_aggregative_intent(query)
    if not intent.is_aggregative:
        return CoverageRoute(
            authority=ROUTE_AUTHORITY_PROXY,
            dimension=DEFAULT_FACET_DIMENSION,
            routed=False,
            is_aggregative=False,
            quantifier=intent.quantifier,
            scope=intent.scope,
            reason="not_aggregative",
        )
    if intent.scope != "entity-scoped":
        # the never-flag rule's text gate (open-ended corpus self-reference)
        return CoverageRoute(
            authority=ROUTE_AUTHORITY_PROXY,
            dimension=DEFAULT_FACET_DIMENSION,
            routed=False,
            is_aggregative=True,
            quantifier=intent.quantifier,
            scope=intent.scope,
            reason=("open_ended" if intent.scope == "open-ended"
                    else "unscoped"),
        )
    # the detector's dimension hint IS the coverage facet vocabulary
    # (FACET_DIMENSIONS); an unknown/absent hint falls back to the wired
    # default (defense-in-depth — the router must never raise).
    dimension = intent.facet_dimension or DEFAULT_FACET_DIMENSION
    if dimension not in FACET_DIMENSIONS:
        dimension = DEFAULT_FACET_DIMENSION
    return CoverageRoute(
        authority=ROUTE_AUTHORITY_DETECTOR,
        dimension=dimension,
        routed=True,
        is_aggregative=True,
        quantifier=intent.quantifier,
        scope=intent.scope,
        reason="routed",
    )


def _routed_aliases(query: str, dimension: str,
                    anchors: tuple[str, ...],
                    missing_facets: tuple[str, ...],
                    anchor_aliases: dict[str, tuple[str, ...]] | None = None,
                    ) -> tuple[str, ...]:
    """The routed facets' expansion vocabulary (pure).

    The vocabulary is the ALREADY-HARVESTED anchor-spine vocabulary: the
    C3-1 census (``facet_census``) enumerates the SAME Object spine the
    detector's census does and harvests each anchor's E3 ``search_keys`` —
    including the keys of the starved sessions' own points — so
    ``anchor_aliases`` (``{anchor name: aliases}``) hands the routed facet
    the exact vocabulary the proxy would have used had it fired. This is the
    half the routing must NOT duplicate: no second E3 harvest, no second
    graph round-trip.

    Fallbacks, in order: the anchor-alias map; else the anchor NAME, gated by
    the SAME ``_token_overlap`` quality rule the proxy census applies (an
    Object that scored on the FTS index without sharing a query token is
    index noise, and its name cannot add sparse recall either). The TEMPORAL
    facet additionally contributes its own month key (the R5 time seam: a
    missing ``2026-09`` facet re-queries with its date tokens), because a
    month key is query-relevant content vocabulary. Session ids and
    point-KIND names never contribute (neither is content vocabulary — a
    kind name is schema, not evidence).
    """
    out: list[str] = []

    def _add(term: str) -> None:
        term = str(term or "").strip()
        if term and term not in out:
            out.append(term)

    for name in anchors:
        n = str(name or "").strip()
        if not n:
            continue
        vocab = (anchor_aliases or {}).get(n)
        if vocab:
            for a in vocab:
                _add(a)
        elif _token_overlap(query, n):
            _add(n)
    if dimension == "date_month":
        for key in missing_facets:
            _add(key)
    return tuple(out)


def detector_coverage_gap(proj: Any, query: str, pool: list[dict], *,
                          window: int = DEFAULT_LOOP_GUARD_WINDOW,
                          route: CoverageRoute | None = None,
                          session_dates: dict[str, str] | None = None,
                          anchor_aliases: dict[str, tuple[str, ...]]
                          | None = None,
                          session_key: Callable[[dict], str] | None = None,
                          ) -> tuple[list[LoopFacet], dict]:
    """The detector-owned per-facet coverage check (C3-3 #2569) — the
    counting-facet census + the k-of-N check, composed from #2521's
    primitives.

    For a ROUTED query: resolve the entity anchors through the Object-name
    spine (``collect_anchor_census``), date-qualify the census to the query's
    own R5 bound (``points_in_window`` — the temporal facet), count the
    facets under the routed dimension and emit a :class:`LoopFacet` per
    MISSING facet (``compute_facet_coverage``). The facet's ``span`` is the
    set of census sessions carrying it; its ``aliases`` are the
    already-harvested anchor-spine vocabulary (``anchor_aliases`` from
    ``facet_census``, with a token-overlap-gated anchor-name fallback) — see
    :func:`_routed_aliases`.

    Unlike the proxy's ``coverage_gap``, the detector's check has NO
    "seeded" precondition: ``k == 0`` (the anchor's evidence never reached
    the window at all) is the **never-reached** miss class and MUST fire (the
    #2513 §1 13% class — this is the +official-metric link #2521 carries).

    Returns ``(missing_facets, report)``. ``report`` is the per-outcome
    marker dict (§8): ``authority``/``dimension``/``routed``/``signal``/
    ``n_facets``/``retrieved_facets``/``missing_facets``/``anchors``/
    ``capped``/``reason`` — the same vocabulary the #2521 measurement arm
    emits, so the routing's signal-to-flag mapping stays auditable.

    Fail-open (the A4/C2 posture, never raises): a non-routed query, an
    unresolvable anchor, a truncated census, an out-of-window census or any
    graph failure yields ``([], report)`` and the caller keeps the ORIGINAL
    pool byte-identical.
    """
    from tortoise.aggregate import (
        collect_anchor_census,
        compute_facet_coverage,
        facet_key_for_point,
    )
    if route is None:
        route = route_coverage_signal(query)
    report: dict = {
        "authority": route.authority,
        "dimension": route.dimension,
        "routed": route.routed,
        "quantifier": route.quantifier,
        "scope": route.scope,
        "signal": "none",
        "n_facets": None,
        "retrieved_facets": None,
        "missing_facets": [],
        "anchors": [],
        "capped": False,
        "reason": route.reason,
        "window": max(1, min(window, len(pool))) if pool else 0,
    }
    if not route.routed:
        return [], report
    try:
        census = collect_anchor_census(proj, query)
    except Exception:  # noqa: BLE001, RUF100
        # collect_anchor_census is contractually fail-open; this is the
        # never-raises belt to its braces (the routing must not be the one
        # place that turns a graph hiccup into a broken lane).
        logger.warning("C3-3 anchor census failed — loop no-ops",
                       exc_info=True)
        report["reason"] = "census_failed"
        return [], report
    if census is None:
        report["reason"] = "no_anchor"
        return [], report
    anchors, points, truncated = census
    report["anchors"] = [str(a.get("name") or "") for a in anchors]
    if truncated or not points:
        report["reason"] = "truncated" if truncated else "no_linked_points"
        report["capped"] = bool(truncated)
        return [], report
    # the R5 time seam, on the point grain: the query's own interval/recency
    # bound qualifies which census evidence counts (the same rule the proxy's
    # ``_span_in_window`` applies to session spans).
    date_kind, date_start, date_end = facet_date_constraint(query)
    qualified = points_in_window(points, session_dates, date_kind,
                                 date_start, date_end)
    if not qualified:
        report["reason"] = "out_of_window"
        return [], report
    retrieved = pool[:report["window"]] if report["window"] else []
    verdict = compute_facet_coverage(
        census_points=qualified,
        retrieved_points=retrieved,
        dimension=route.dimension,
        anchors=[str(a.get("name") or "") for a in anchors],
    )
    report.update({
        "signal": verdict.signal,
        "n_facets": verdict.n_facets,
        "retrieved_facets": verdict.retrieved_facets,
        "missing_facets": list(verdict.missing_facets),
        "capped": bool(verdict.capped),
        "reason": "measured",
    })
    if verdict.signal != "partial":
        return [], report
    missing_keys = tuple(verdict.missing_facets)
    # the counting-facet census, inverted: which census SESSION carries each
    # missing facet (the loop's expand target + the marker's frozen identity).
    sessions_by_key: dict[str, set[str]] = {}
    for p in qualified:
        key = facet_key_for_point(p, route.dimension)
        if key is None or key not in missing_keys:
            continue
        s = str(p.get("session_id") or "").strip()
        if s:
            sessions_by_key.setdefault(key, set()).add(s)
    aliases = _routed_aliases(query, route.dimension, tuple(report["anchors"]),
                              missing_keys, anchor_aliases)
    facets = [
        LoopFacet(
            kind=f"facet:{route.dimension}",
            key=f"{route.dimension}:{key}",
            name=str(key),
            span=frozenset(sessions_by_key.get(key, set())),
            aliases=aliases,
        )
        for key in missing_keys
    ]
    return facets, report


def combine_missing_facets(proxy_missing: list[LoopFacet],
                           routed_missing: list[LoopFacet]) -> list[LoopFacet]:
    """The additive union of the two checks (pure, C3-3 #2569).

    Order is the contract, not a detail: the PROXY's facets come first, so
    the proxy's E3 ``search_keys`` keep their slots in the single bounded
    expansion tail (``loop_expansion_pass`` fills it in facet order) and the
    routed facets can only ever ADD vocabulary — ``loop_fired_facet`` and
    the proxy's fire behaviour stay byte-stable, which is what "no
    regression on the rule-proxy interim" means operationally. Dedupes by
    facet key (the two checks name disjoint key namespaces —
    ``entity:<name>`` vs ``<dimension>:<facet>`` — so a collision here would
    itself be a defect worth dropping rather than expanding twice).
    """
    out: list[LoopFacet] = []
    seen: set[str] = set()
    for facet in [*proxy_missing, *routed_missing]:
        if facet.key in seen:
            continue
        seen.add(facet.key)
        out.append(facet)
    return out


def loop_expansion_pass(proj: Any, query: str, facets: list[LoopFacet], *,
                        limit: int,
                        keep_numeric: bool = False,
                        excluded_statuses: tuple | None = None,
                        leg_trace: list[dict] | None = None,
                        ) -> dict:
    """The EXPAND step (step 3 over the graph): ONE targeted second sparse
    pass for the missing facets' vocabulary (hard ``LOOP_MAX_EXTRA_PASSES``
    = 1 bound — however many facets the check found incomplete, the loop
    runs at most ONE extra retrieval pass; their alias pools union into that
    single pass's expansion tail).

    Mirrors the A4/C2 second-pass machinery exactly: the facets' alias pools
    fill ONLY ``build_or_query``'s bounded expansion tail (the original
    query tokens keep their slots — the reserved-slot contract), the pass
    is additive recall only, and any fetch/query failure returns
    ``expanded_ids=None`` so the caller keeps the ORIGINAL pool
    byte-identical (the fail-open posture).

    Returns a report dict (never raises):
      ``facet_keys``    — the facets the pass targeted (census order).
      ``expanded_ids`` — the second pass's ranked point ids, None when no
                         pass ran (no additive vocabulary / failure / empty
                         result).
      ``iterations``   — 1 iff an extra retrieval pass actually ran, else 0
                         (the §8 iteration census).
    """
    if limit < 1 or not facets:
        return {"facet_keys": [f.key for f in facets],
                "expanded_ids": None, "iterations": 0}
    from tortoise.sdk import _ENTITY_ALIAS_MAX
    # union the missing facets' vocabulary (anchor names first, then keys)
    # — one deterministic, capped alias pool for the single pass.
    aliases: list[str] = []
    seen: set[str] = set()
    for facet in facets:
        for a in facet.aliases:
            if a in seen:
                continue
            seen.add(a)
            aliases.append(a)
            if len(aliases) >= _ENTITY_ALIAS_MAX:
                break
        if len(aliases) >= _ENTITY_ALIAS_MAX:
            break
    if not aliases or not _aliases_add_recall(
            query, tuple(aliases), keep_numeric=keep_numeric):
        # an alias pool that tokenizes to only the query's own tokens cannot
        # add recall — no wasted identical second pass (C2's guard).
        return {"facet_keys": [f.key for f in facets],
                "expanded_ids": None, "iterations": 0}
    from tortoise.search_engine import run_fts_query
    _trace: list[dict] = []
    try:
        expanded = run_fts_query(
            proj.g, query, entity_type="point", limit=limit,
            excluded_statuses=excluded_statuses,
            keep_numeric=keep_numeric,
            expansion_terms=aliases,
            leg_trace=_trace,
        )
    except Exception:  # noqa: BLE001, RUF100
        logger.warning(
            "C3-1 loop expansion pass failed — keeping the original pool "
            "(fail-open)", exc_info=True)
        return {"facet_keys": [f.key for f in facets],
                "expanded_ids": None, "iterations": 0}
    if leg_trace is not None:
        leg_trace.extend(_trace)
    if not expanded:
        return {"facet_keys": [f.key for f in facets],
                "expanded_ids": None, "iterations": 1}
    return {"facet_keys": [f.key for f in facets],
            "expanded_ids": [pid for pid, _s in expanded],
            "iterations": 1}


def merge_expansion_order(pool: list[dict], added: list[dict],
                          expanded_ids: list[str]) -> list[dict]:
    """The additive union in second-pass relevance order (pure, step 4).

    Mirrors the A4/C2 leg-merge contract at the pool level: the second
    pass is a re-QUERY of the same question with the missing facet's
    vocabulary — its ranked members (base hits re-found by the pass AND
    newly surfaced recovery hits) carry the loop's best relevance signal
    and lead the merged pool in pass order; base hits the pass did not
    re-find (legs the sparse re-query cannot see) keep their base ranks
    appended after. Additive by construction: every base hit survives; ids
    dedupe across the union. The caller applies the session-diverse window
    discipline (:func:`session_diverse_order`) on top.
    """
    by_id = {h["id"]: h for h in pool}
    by_id.update({h["id"]: h for h in added})
    merged: list[dict] = []
    seen: set[str] = set()
    for pid in expanded_ids:
        h = by_id.get(pid)
        if h is not None and pid not in seen:
            merged.append(h)
            seen.add(pid)
    merged.extend(h for h in pool if h["id"] not in seen)
    return merged


def session_diverse_order(items: list[dict], *,
                          window: int = DEFAULT_LOOP_GUARD_WINDOW,
                          per_session_cap: int = DEFAULT_LOOP_SESSION_CAP,
                          session_key: Callable[[dict], str] | None = None,
                          ) -> list[dict]:
    """The merge discipline (step 4, pure): reorder ``items`` so the top
    ``window`` ranks are session-diverse — no session holds more than
    ``per_session_cap`` of the window while an underrepresented session's
    evidence remains available below it.

    Same-session near-dupes are deferred past the window (a monopolizing
    session's points were never capped — §1(2)); the cap yields to
    completeness only when no other session can fill the window (a pool
    that is one session's alone still surfaces its own evidence; such pools
    never fire the loop anyway). Additive by construction: reordering never
    drops an item — the windowed prefix + the deferred tail partition the
    input. Stable: within a session, original order is preserved.
    """
    if not items:
        return []
    window = max(1, min(window, len(items)))
    counts: dict[str, int] = {}
    windowed: list[dict] = []
    deferred: list[dict] = []
    for h in items:
        s = _session_of(h, session_key)
        if len(windowed) >= window or counts.get(s, 0) >= per_session_cap:
            deferred.append(h)
        else:
            windowed.append(h)
            counts[s] = counts.get(s, 0) + 1
    # Under-filled window (every remaining item belongs to a capped session):
    # fill from the deferred tail in original order — the cap is a diversity
    # guard, never a completeness blocker.
    while len(windowed) < window and deferred:
        windowed.append(deferred.pop(0))
    return windowed + deferred

"""#5009 — §12.4's SUFFICIENCY ROUTER and §12.5's ESCALATION WRITE-BACK.

§12.4's rule is *"escalate on SUFFICIENCY, not on query type"*: *"what kind of
query is this?"* is a classifier that must be trained, drifts, and is wrong at
the boundary; *"did the cheap tier answer it?"* is a **measured property of the
answer**. §12.5 names the half that compounds — the escalation must WRITE BACK
what it verified, or the same escalation is paid for forever.

These tests pin the decision at the point where it is made:

* the verdict is **three-state** — ``undetermined`` is never flattened into a
  neighbouring state at either end (a boolean seam is how an unmeasured read
  becomes a false negative or a false green);
* **residency outranks sufficiency** (§17.3) — a read the store could not see
  has not established insufficiency, so it may never escalate;
* the trigger is **bounded** — one walk, and a still-insufficient verdict after
  it is reported rather than re-walked (§12.7 *router overhead*);
* the router takes **no query class** — pinned structurally, so the classifier
  §12.4 rejects cannot be reintroduced without reddening a test;
* the write-back carries its **span**, never promotes a class, never carries the
  raw payload, and **dedupes by identity** (the attribution loop, without which
  the escalation is a permanent tax).

MUTATION PROOF — the guard each test exists to hold, and the edit that turns it
RED:

* **Flatten the third state** — make ``SufficiencyVerdict.sufficient`` return
  ``False`` for ``undetermined`` → ``test_an_unassessed_verdict_is_neither_true_nor_false``
  and ``test_an_unassessed_read_serves_rather_than_escalates`` RED.
* **Let a residency miss escalate** — drop the ``resident`` guard at the top of
  ``assess_sufficiency`` → ``test_a_residency_miss_is_never_an_insufficiency``
  RED (and ``route_residency`` receives a sufficiency verdict it must not have).
* **Score an unfetched provenance field as zero** — count every row instead of
  the ``assessable`` subset → ``test_a_provenance_measure_over_unfetched_rows_is_unassessed``
  RED (a flag-off read becomes "insufficient evidence").
* **Default a threshold** — give ``required_anchor_coverage`` a default →
  ``test_there_is_no_default_threshold`` RED (a target nobody chose).
* **Unbound the trigger** — drop the ``escalated``/``budget`` guard →
  ``test_a_second_insufficient_verdict_is_reported_not_re_walked`` RED.
* **Turn the router into a classifier** — add a ``query_type`` parameter →
  ``test_the_router_takes_no_query_class`` RED.
* **Give the router a clock / a model / a graph** — import ``time`` or
  ``tortoise.embeddings`` → ``test_the_router_is_pure_no_io_no_model_no_clock`` RED.
* **Collapse the two routers** — emit ``fetch-record`` from the sufficiency
  route → ``test_a_sufficiency_route_is_never_the_residency_repair`` RED.
* **Write back without a span** — drop the ``span is None`` refusal →
  ``test_a_finding_without_a_span_is_refused_not_downgraded`` RED.
* **Promote an episodic fact** — drop the ``promote_to`` refusal →
  ``test_the_write_back_refuses_to_promote_a_claim_class`` RED.
* **Drop the attribution loop** — remove the ``seen`` dedupe →
  ``test_the_same_verified_finding_writes_back_once`` RED.
* **Let a unit carry the raw payload** — add a ``raw``/``supersedes`` field to
  ``SummaryUnit`` → ``test_a_summary_unit_can_neither_carry_the_raw_payload_nor_supersede_it``
  RED.
* **Silently drop a refusal** — return only ``units`` from ``plan_writeback`` →
  ``test_an_unverified_finding_is_refused_and_reported`` RED.
* **Read provenance from the attribute key only** — drop the nested
  ``provenance.source`` path (the shape ``SearchResult.to_dict()`` emits) →
  ``test_the_provenance_link_is_read_from_the_wire_shape_the_read_path_emits``
  and ``test_a_real_search_result_row_is_assessed_for_provenance`` RED (the
  measure then has no producer on the only shape a caller holds).
* **Match an anchor as an infix** — return to a bare substring test →
  ``test_a_short_anchor_does_not_match_inside_an_unrelated_word`` RED.
* **Count a blank anchor as covered** — drop the blank-anchor refusal →
  ``test_an_empty_anchor_is_refused_not_counted_as_covered`` RED.
* **Let an empty anchor set disable a supplied threshold** — drop the
  ``required_anchor_coverage``/``anchors`` mismatch guard →
  ``test_an_anchor_threshold_with_no_anchors_is_refused_not_silently_unassessed``
  RED.
* **Accept any falsy ``resident``** — return to ``resident is None or resident is
  False`` with no type check →
  ``test_a_non_bool_resident_is_refused_not_treated_as_established`` RED.
* **Report an exhausted budget that is not exhausted** — drop the
  ``already-escalated`` reason →
  ``test_an_already_escalated_verdict_does_not_claim_an_exhausted_budget`` RED.
* **Default the budget** — restore ``budget: int = 1`` →
  ``test_there_is_no_default_budget`` RED (§12.3's tier count is OPEN).
* **Bypass a ``SummaryUnit`` invariant** — drop any check in its
  ``__post_init__`` →
  ``test_a_summary_unit_cannot_be_constructed_without_its_invariants`` RED.

Hermetic: no DB, no graph, no network (runs on the fast ``core`` lane).
"""
from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path
from typing import ClassVar

import pytest

from tortoise import status_vocabulary
from tortoise.residency import RESIDENCY_HOT, RESIDENCY_NOT_HOT
from tortoise.residency_router import (
    ROUTE_ESCALATE_TIER,
    ROUTE_FETCH_RECORD,
    ROUTE_SERVE,
    ROUTES,
    route_residency,
)
from tortoise.search_engine import SearchResult
from tortoise.sufficiency_router import (
    INSUFFICIENT,
    MEASURE_ANCHOR_COVERAGE,
    MEASURE_PROVENANCE_COVERAGE,
    REASON_ALREADY_ESCALATED,
    REASON_ANCHORS_UNCOVERED,
    REASON_BUDGET_EXHAUSTED,
    REASON_EVIDENCE_EMPTY,
    REASON_MEASURES_MET,
    REASON_NO_MEASURE,
    REASON_PROVENANCE_THIN,
    REASON_RESIDENT_MISS,
    REASON_RESIDENT_UNASSESSED,
    REFUSAL_NO_CLAIM,
    REFUSAL_NO_PROVENANCE,
    REFUSAL_PROMOTION,
    REFUSAL_UNVERIFIED,
    SUFFICIENT,
    UNDETERMINED,
    VERDICTS,
    SourceSpan,
    SufficiencyRoute,
    SufficiencyVerdict,
    SummaryUnit,
    VerifiedFinding,
    assess_sufficiency,
    evidence_provenance,
    evidence_record,
    plan_writeback,
    route_sufficiency,
    unit_key,
)

MODULE_PATH = Path(__file__).resolve().parent.parent / "tortoise" / "sufficiency_router.py"


def _rows(*contents: str, provenance: bool = False) -> list[dict]:
    """Assembled evidence rows in the shape the read path emits.

    The provenance-carrying rows are built from a real
    ``SearchResult(...).to_dict()`` rather than hand-written, because the
    hand-written version drifted: it invented a TOP-LEVEL ``source_ref`` key,
    which no producer emits, and so every provenance test was green against a
    shape that does not exist on the wire while the real one went unread.
    """
    out = []
    for i, content in enumerate(contents):
        if provenance:
            out.append(SearchResult(
                id=f"p{i}", content=content, point_kind="fact",
                source_ref=f"https://example.test/{i}",
            ).to_dict())
            continue
        out.append({"id": f"p{i}", "content": content})
    return out


# ── the verdict: three states, never two ───────────────────────────────────


def test_the_verdict_vocabulary_has_three_states() -> None:
    assert VERDICTS == (SUFFICIENT, INSUFFICIENT, UNDETERMINED)
    assert len(set(VERDICTS)) == 3


def test_an_unassessed_verdict_is_neither_true_nor_false() -> None:
    """``undetermined`` is a real state, not a softened boolean.

    The ``bool | None`` bridge is what the residency router already accepts; a
    third state flattened here becomes a false escalation (``False``) or a
    silent "ok" (``True``).
    """
    verdict = assess_sufficiency([], resident=None)
    assert verdict.verdict == UNDETERMINED
    assert verdict.sufficient is None
    assert verdict.is_insufficient is False


def test_an_unassessed_read_serves_rather_than_escalates() -> None:
    """No measurement, no escalation — and the reason says which absence it was."""
    verdict = assess_sufficiency(_rows("anything at all"), resident=True)
    assert verdict.verdict == UNDETERMINED
    assert verdict.reasons == (REASON_NO_MEASURE,)

    route = route_sufficiency(verdict, budget=1)
    assert route.route == ROUTE_SERVE
    assert route.reason == REASON_NO_MEASURE


def test_a_measure_that_met_its_threshold_is_sufficient() -> None:
    evidence = _rows("acme corp signed the contract", "unrelated filler")
    verdict = assess_sufficiency(
        evidence, anchors=["ACME Corp"], required_anchor_coverage=1.0, resident=True)
    assert verdict.verdict == SUFFICIENT
    assert verdict.sufficient is True
    assert verdict.reasons == (REASON_MEASURES_MET,)
    assert verdict.measures == ((MEASURE_ANCHOR_COVERAGE, 1.0),)


def test_an_uncovered_anchor_is_insufficient_and_names_the_measure() -> None:
    evidence = _rows("unrelated filler", "more filler")
    verdict = assess_sufficiency(
        evidence,
        anchors=["ACME Corp", "northwind"],
        required_anchor_coverage=0.5,
        resident=True,
    )
    assert verdict.verdict == INSUFFICIENT
    assert verdict.reasons == (REASON_ANCHORS_UNCOVERED,)
    assert verdict.measures == ((MEASURE_ANCHOR_COVERAGE, 0.0),)


def test_a_partially_covered_anchor_set_is_measured_not_guessed() -> None:
    evidence = _rows("acme corp signed the contract")
    verdict = assess_sufficiency(
        evidence,
        anchors=["acme corp", "northwind"],
        required_anchor_coverage=0.5,
        resident=True,
    )
    assert verdict.measures == ((MEASURE_ANCHOR_COVERAGE, 0.5),)
    assert verdict.verdict == SUFFICIENT


def test_the_anchor_match_is_case_insensitive_and_can_hit_the_row_id() -> None:
    evidence = [{"id": "entity:acme", "content": "signed"}]
    verdict = assess_sufficiency(
        evidence, anchors=["ENTITY:ACME"], required_anchor_coverage=1.0, resident=True)
    assert verdict.verdict == SUFFICIENT


def test_a_short_anchor_does_not_match_inside_an_unrelated_word() -> None:
    """A substring test over-reports coverage in the fail-open direction.

    ``"ai" in "he said the main thing"`` is ``True``, so a short entity name
    (``AI``, ``PR``, ``Q3``, ``Art``) scored COVERED on text that never mentions
    it — a confident ``sufficient`` on evidence that does not answer the
    question, which is the §14.1 V4 silent green arriving through the measure's
    front door. The comparison beside it (``row.id``) is exact, so the weaker
    signal was also the looser one.
    """
    verdict = assess_sufficiency(
        _rows("He said the main thing"),
        anchors=["AI"], required_anchor_coverage=1.0, resident=True)
    assert verdict.verdict == INSUFFICIENT
    assert verdict.reasons == (REASON_ANCHORS_UNCOVERED,)
    assert verdict.measures == ((MEASURE_ANCHOR_COVERAGE, 0.0),)

    # "Art" must not be covered by "start the process" either.
    assert assess_sufficiency(
        _rows("start the process"),
        anchors=["Art"], required_anchor_coverage=1.0, resident=True,
    ).verdict == INSUFFICIENT


def test_a_short_anchor_still_matches_its_own_word() -> None:
    """The boundary is a boundary, not a length heuristic."""
    verdict = assess_sufficiency(
        _rows("we shipped an AI model in Q3"),
        anchors=["AI", "q3"], required_anchor_coverage=1.0, resident=True)
    assert verdict.verdict == SUFFICIENT
    assert verdict.measures == ((MEASURE_ANCHOR_COVERAGE, 1.0),)


def test_thin_provenance_is_insufficient() -> None:
    evidence = _rows("acme corp signed", "filler", provenance=True)
    evidence[1].pop("provenance", None)  # the second row fetched no link
    verdict = assess_sufficiency(
        evidence, required_provenance_coverage=1.0, resident=True)
    assert verdict.verdict == INSUFFICIENT
    assert verdict.reasons == (REASON_PROVENANCE_THIN,)
    assert verdict.measures == ((MEASURE_PROVENANCE_COVERAGE, 0.5),)


def test_the_provenance_link_is_read_from_the_wire_shape_the_read_path_emits() -> None:
    """``SearchResult.to_dict()`` nests the link at ``provenance.source``.

    The module read only the top-level ``source_ref`` ATTRIBUTE key, which no
    producer emits on the wire — so on every real assembled-evidence row
    (``sdk.search``/``tortoise_fts_query`` return ``[r.to_dict() …]``, and
    ``ask_lane`` consumes exactly those) the provenance measure was permanently
    unassessed: a declared measure with no producer, one layer down. Built from
    a REAL ``SearchResult`` so the fixture cannot drift from the wire again.
    """
    row = SearchResult(
        id="p1", content="acme corp signed", point_kind="fact",
        source_ref="https://example.test/acme",
    ).to_dict()
    assert row["provenance"] == {"source": "https://example.test/acme"}
    assert evidence_provenance(row) == "https://example.test/acme"
    assert evidence_record(row).provenance == "https://example.test/acme"


def test_a_real_search_result_row_is_assessed_for_provenance() -> None:
    """The measure has a PRODUCER on the only shape a caller actually holds."""
    rows = _rows("acme corp signed", "netherlands renewal", provenance=True)
    verdict = assess_sufficiency(
        rows, required_provenance_coverage=1.0, resident=True)
    assert verdict.verdict == SUFFICIENT
    assert verdict.measures == ((MEASURE_PROVENANCE_COVERAGE, 1.0),)


def test_a_partially_provenanced_real_wire_read_is_measured_not_unassessed() -> None:
    rows = _rows("acme corp signed", provenance=True) + _rows("filler")
    verdict = assess_sufficiency(
        rows, required_provenance_coverage=0.9, resident=True)
    assert verdict.verdict == INSUFFICIENT
    assert verdict.reasons == (REASON_PROVENANCE_THIN,)
    assert verdict.measures == ((MEASURE_PROVENANCE_COVERAGE, 0.5),)


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_an_empty_anchor_is_refused_not_counted_as_covered(blank: str) -> None:
    """``"" in "anything"`` is ``True``, so a blank anchor is FULL coverage.

    ``anchors=[""]`` with ``required_anchor_coverage=1.0`` measured ``1.0`` and
    returned ``sufficient`` — a confident green from an anchor that names no
    entity at all. Every other input in this module is refused when it is
    unusable; an anchor element is no different, and an empty element is a
    realistic residue of anchor resolution (``extract_subject_terms`` returns
    ``[]`` for shapes with no resolvable subject), not a hypothetical.
    """
    with pytest.raises(ValueError):
        assess_sufficiency(
            _rows("unrelated filler"), anchors=[blank],
            required_anchor_coverage=1.0, resident=True)


def test_a_blank_anchor_cannot_hide_among_valid_ones() -> None:
    with pytest.raises(ValueError):
        assess_sufficiency(
            _rows("acme corp signed"), anchors=["acme corp", " "],
            required_anchor_coverage=1.0, resident=True)
    with pytest.raises(TypeError):
        assess_sufficiency(
            _rows("acme corp signed"), anchors=["acme corp", 7],  # type: ignore[list-item]
            required_anchor_coverage=1.0, resident=True)


def test_an_anchor_threshold_with_no_anchors_is_refused_not_silently_unassessed() -> None:
    """A configured measure cannot be disabled by an empty resolver result.

    ``anchors=()`` with a supplied threshold skipped the measure entirely and
    reported ``no-measure`` — blaming a configuration that DOES exist, and
    converting "the caller could not even resolve what the question is about"
    into an unassessed serve. Disabling the measure is the caller's explicit
    act (``required_anchor_coverage=None``), not a side effect of an empty list.
    """
    with pytest.raises(ValueError):
        assess_sufficiency(
            _rows("acme corp signed"), anchors=(),
            required_anchor_coverage=0.9, resident=True)

    # Opting out explicitly is still legitimate, and still says `no-measure`.
    opted_out = assess_sufficiency(
        _rows("acme corp signed"), anchors=(),
        required_anchor_coverage=None, resident=True)
    assert opted_out.verdict == UNDETERMINED
    assert opted_out.reasons == (REASON_NO_MEASURE,)

    """A flag-off read must not be scored as insufficient evidence.

    ``search_provenance_enabled()`` off means ``source_ref`` is never fetched, so
    no row carries the key. Scoring that as ``0.0`` would red the cheap tier for
    an unfetched field — the §17.3 "unassessed is not negative" discipline, one
    layer down. The measure is therefore NOT ASSESSED, and a verdict resting on
    it alone is ``undetermined``.
    """
    evidence = _rows("acme corp signed", "filler")  # no `source_ref` anywhere
    verdict = assess_sufficiency(
        evidence, required_provenance_coverage=0.9, resident=True)
    assert verdict.verdict == UNDETERMINED
    assert verdict.reasons == (REASON_NO_MEASURE,)
    assert verdict.measures == ()


def test_an_unfetched_provenance_field_does_not_mask_an_assessed_measure() -> None:
    """The assessed measure still decides; the unassessed one neither helps nor hurts."""
    evidence = _rows("acme corp signed", "filler")  # no provenance at all
    verdict = assess_sufficiency(
        evidence,
        anchors=["acme corp"],
        required_anchor_coverage=1.0,
        required_provenance_coverage=0.9,
        resident=True,
    )
    assert verdict.verdict == SUFFICIENT
    assert verdict.measures == ((MEASURE_ANCHOR_COVERAGE, 1.0),)


def test_there_is_no_default_threshold() -> None:
    """Every threshold is the caller's, and there are none to omit.

    A default here would be a target nobody chose (§14.1 O2), wearing a
    constant's clothes — so the signature has no default for either threshold.
    """
    params = inspect.signature(assess_sufficiency).parameters
    assert params["required_anchor_coverage"].default is None
    assert params["required_provenance_coverage"].default is None

    # Omitting both is not "pass" — it is `no-measure`.
    verdict = assess_sufficiency(_rows("acme corp"), anchors=["acme corp"], resident=True)
    assert verdict.verdict == UNDETERMINED


@pytest.mark.parametrize("bad", [-0.1, 1.1, "0.9", True, object()])
def test_an_unusable_threshold_is_refused_not_defaulted(bad: object) -> None:
    with pytest.raises(ValueError):
        assess_sufficiency(_rows("x"), required_anchor_coverage=bad)  # type: ignore[arg-type]


# ── §17.3: residency outranks sufficiency ─────────────────────────────────


def test_a_non_bool_resident_is_refused_not_treated_as_established() -> None:
    """§17.3's guard is an ALLOW-LIST, and its input is type-checked.

    ``resident is None or resident is False`` are IDENTITY tests: every falsy
    non-``False`` value (``0`` from a JSON boolean column, ``""``, ``[]``,
    ``0.0``) satisfied neither, so the assessment proceeded as if residency had
    been ESTABLISHED — and a read the cache never saw then walked a tier, the
    one wrong answer §17.3 says is indistinguishable from a right one.
    """
    for bad in (0, 1, 0.0, "", [], {}, object()):
        with pytest.raises(TypeError):
            assess_sufficiency(_rows("filler"), resident=bad)  # type: ignore[arg-type]


def test_a_residency_miss_is_never_an_insufficiency() -> None:
    """The collapse guard, at the sufficiency end of the seam.

    A node the cache cannot see makes *"insufficient"* unestablished (§17.3), so
    the assessment REFUSES to judge — even over evidence that would otherwise be
    a clear failure.
    """
    miss = assess_sufficiency(_rows("filler"), resident=False)
    assert miss.verdict == UNDETERMINED
    assert miss.reasons == (REASON_RESIDENT_MISS,)
    assert miss.is_insufficient is False

    unassessed = assess_sufficiency(_rows("filler"), resident=None)
    assert unassessed.reasons == (REASON_RESIDENT_UNASSESSED,)


def test_a_residency_miss_routes_to_the_record_before_any_tier_is_walked() -> None:
    """The two routers COMPOSE, and the precedence holds across the seam."""
    miss = assess_sufficiency(_rows("filler"), resident=False)
    # The sufficiency router alone would serve; the residency router owns the repair.
    assert route_sufficiency(miss, budget=1).route == ROUTE_SERVE
    assert route_residency(
        marker=RESIDENCY_NOT_HOT, sufficient=miss.sufficient).route == ROUTE_FETCH_RECORD
    # And a MEASURED insufficiency on a RESIDENT read still reaches the tier walk.
    thin = assess_sufficiency(
        _rows("filler"), anchors=["acme corp"], required_anchor_coverage=1.0, resident=True)
    assert thin.verdict == INSUFFICIENT
    assert route_residency(
        marker=RESIDENCY_HOT, sufficient=thin.sufficient).route == ROUTE_ESCALATE_TIER


def test_the_verdict_feeds_the_residency_router_three_distinctly() -> None:
    """``True`` / ``False`` / ``None`` must not collapse on the way across."""
    sufficient = assess_sufficiency(
        _rows("acme corp"), anchors=["acme corp"],
        required_anchor_coverage=1.0, resident=True)
    insufficient = assess_sufficiency(
        _rows("filler"), anchors=["acme corp"],
        required_anchor_coverage=1.0, resident=True)
    undetermined = assess_sufficiency(_rows("filler"), resident=True)

    seen = [
        route_residency(marker=RESIDENCY_HOT, sufficient=v.sufficient).route
        for v in (sufficient, insufficient, undetermined)
    ]
    assert seen == [ROUTE_SERVE, ROUTE_ESCALATE_TIER, ROUTE_SERVE]
    assert len({v.sufficient for v in (sufficient, insufficient, undetermined)}) == 3


# ── the bounded trigger ───────────────────────────────────────────────────


def _insufficient() -> SufficiencyVerdict:
    return assess_sufficiency(
        _rows("filler"), anchors=["acme corp"],
        required_anchor_coverage=1.0, resident=True)


def test_empty_evidence_on_a_resident_read_escalates_once() -> None:
    verdict = assess_sufficiency([], resident=True)
    assert verdict.reasons == (REASON_EVIDENCE_EMPTY,)

    route = route_sufficiency(verdict, budget=1)
    assert route.route == ROUTE_ESCALATE_TIER
    assert route.route in ROUTES
    assert route.escalated is True
    assert route.budget_remaining == 0


def test_a_second_insufficient_verdict_is_reported_not_re_walked() -> None:
    """§12.7 "router overhead": an unbounded repair loop costs more than the read.

    The second look at the same answer is REPORTED (``budget-exhausted``), never
    re-walked — and the verdict still says the evidence was short, so a caller
    cannot mistake the serve for a green.
    """
    verdict = _insufficient()
    first = route_sufficiency(verdict, budget=1)
    assert first.route == ROUTE_ESCALATE_TIER

    second = route_sufficiency(verdict, escalated=first.escalated, budget=first.budget_remaining)
    assert second.route == ROUTE_SERVE
    assert second.reason == REASON_BUDGET_EXHAUSTED
    assert second.verdict == INSUFFICIENT


def test_an_already_escalated_verdict_does_not_claim_an_exhausted_budget() -> None:
    """The reason is a CLAIM, and ``budget_remaining`` must not contradict it.

    ``route_sufficiency(insufficient, escalated=True, budget=3)`` reported
    ``escalation-budget-exhausted`` with ``budget_remaining=3`` — two causes
    ("already walked" and "nothing left to spend") sharing one reason, so the
    record asserted an exhaustion that had not happened. ``residency_router.py``
    states the rule this broke: *"the reason must name the truth too, or a
    `reason`-keyed consumer reads a claim."*
    """
    route = route_sufficiency(_insufficient(), escalated=True, budget=3)
    assert route.route == ROUTE_SERVE
    assert route.reason == REASON_ALREADY_ESCALATED
    assert route.reason != REASON_BUDGET_EXHAUSTED
    assert route.budget_remaining == 3          # nothing was actually spent
    assert route.verdict == INSUFFICIENT        # still not a green

    # ... and a genuinely empty budget still says so, with nothing left.
    spent = route_sufficiency(_insufficient(), escalated=True, budget=0)
    assert spent.reason == REASON_BUDGET_EXHAUSTED
    assert spent.budget_remaining == 0


def test_there_is_no_default_budget() -> None:
    """§12.3's tier count is OPEN — so it cannot be settled by an omission.

    ``budget: int = 1`` IS the one-step/two-tier policy, hard-coded as a
    default for exactly the callers who did not choose it, while the docstring
    claimed it was not hard-coded here. Same class of un-chosen target
    ``test_there_is_no_default_threshold`` refuses for the thresholds.
    """
    budget = inspect.signature(route_sufficiency).parameters["budget"]
    assert budget.default is inspect.Parameter.empty
    assert budget.kind is inspect.Parameter.KEYWORD_ONLY
    with pytest.raises(TypeError):
        route_sufficiency(_insufficient())  # type: ignore[call-arg]


def test_a_zero_budget_never_escalates() -> None:
    route = route_sufficiency(_insufficient(), budget=0)
    assert route.route == ROUTE_SERVE
    assert route.reason == REASON_BUDGET_EXHAUSTED


def test_rescinding_a_sufficiency_verdict_is_refused_not_coerced() -> None:
    """A bare boolean cannot carry WHY, and ``None`` is a real state."""
    with pytest.raises(TypeError):
        route_sufficiency(False, budget=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        route_sufficiency(None, budget=1)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [-1, True, 1.0, "1"])
def test_an_unusable_budget_is_refused(bad: object) -> None:
    with pytest.raises(ValueError):
        route_sufficiency(_insufficient(), budget=bad)  # type: ignore[arg-type]


# ── the route is a directive, not a status, and never the other router ────


def test_a_sufficiency_route_is_never_the_residency_repair() -> None:
    with pytest.raises(ValueError):
        SufficiencyRoute(route=ROUTE_FETCH_RECORD, reason=REASON_MEASURES_MET,
                         verdict=SUFFICIENT)


@pytest.mark.parametrize("verdict", [
    assess_sufficiency([], resident=True),
    _insufficient(),
    assess_sufficiency(_rows("x"), resident=True),
])
def test_route_sufficiency_only_ever_emits_two_of_the_three_routes(
        verdict: SufficiencyVerdict) -> None:
    emitted = route_sufficiency(verdict, budget=1).route
    assert emitted in (ROUTE_SERVE, ROUTE_ESCALATE_TIER)
    assert emitted in ROUTES


def test_the_route_names_are_not_status_terms() -> None:
    """§17.3: the read-status vocabulary is four terms and mints no fifth."""
    statuses = set(status_vocabulary.CLIENT_STATUS_TERMS)  # the recorded four
    assert set(ROUTES).isdisjoint(statuses), (ROUTES, statuses)


def test_the_decision_itself_refuses_an_unrecognised_verdict_or_route() -> None:
    with pytest.raises(ValueError):
        SufficiencyVerdict(verdict="maybe")
    with pytest.raises(ValueError):
        SufficiencyVerdict(verdict=SUFFICIENT, reasons=("because-i-said-so",))
    with pytest.raises(ValueError):
        SufficiencyVerdict(verdict=SUFFICIENT, measures=(("vibes", 1.0),))
    with pytest.raises(ValueError):
        SufficiencyVerdict(verdict=SUFFICIENT, measures=((MEASURE_ANCHOR_COVERAGE, 1.5),))
    with pytest.raises(ValueError):
        SufficiencyRoute(route="fetch", reason=REASON_MEASURES_MET, verdict=SUFFICIENT)
    with pytest.raises(ValueError):
        SufficiencyRoute(route=ROUTE_SERVE, reason=REASON_MEASURES_MET,
                         verdict=SUFFICIENT, budget_remaining=-1)


# ── the two structural pins that keep it from becoming the rejected design ─


def test_the_router_takes_no_query_class() -> None:
    """§12.4 rejects the query-type classifier — so it cannot be typed into the API.

    ``anchors`` is the ONE query-derived input and it is a set of referenced
    ENTITIES (a coverage measurement over the answer), not a question type.
    """
    forbidden = {"query", "query_type", "query_class", "complexity", "intent",
                 "kind", "question_type", "question_class", "difficulty"}
    for fn in (assess_sufficiency, route_sufficiency, plan_writeback):
        names = {p.casefold() for p in inspect.signature(fn).parameters}
        assert names.isdisjoint(forbidden), (fn.__name__, names & forbidden)


def test_the_router_is_pure_no_io_no_model_no_clock() -> None:
    """§12.7 "router overhead": the router must not cost more than the retrieval.

    Pinned on the module's own imports rather than on behaviour, because the
    failure mode is an ADDITION (a clock, a model call, a graph read) that no
    behavioural test would notice.
    """
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # a relative in-package import
                imported.add(f".{node.module}")
            elif node.module:
                imported.add(node.module.split(".")[0])
    # ``re`` is here because the anchor match must be WORD-bounded: a bare
    # substring test scores a short entity name (``AI``, ``Art``) as covered by
    # unrelated text. It is a pure text operation — no I/O, no clock, no model.
    allowed_stdlib = {"__future__", "collections", "hashlib", "dataclasses",
                      "typing", "re"}
    # The ONLY in-package import is the route vocabulary's single home.
    allowed_local = {".residency_router"}
    assert imported <= allowed_stdlib | allowed_local, imported - (allowed_stdlib | allowed_local)
    assert not {"time", "socket", "subprocess", "os", "uuid", "random"} & imported


# ── §12.5 the escalation write-back ────────────────────────────────────────


def _span() -> SourceSpan:
    return SourceSpan(source_id="source:raw-1", turn=3, start=10, end=24)


def _finding(**overrides: object) -> VerifiedFinding:
    base = dict(claim="acme corp signed the contract in March",
                span=_span(), claim_class="semantic", verified=True)
    base.update(overrides)
    return VerifiedFinding(**base)  # type: ignore[arg-type]


def test_a_verified_finding_with_a_span_writes_back_as_a_derived_unit() -> None:
    plan = plan_writeback([_finding()])
    assert plan.ok is True
    assert plan.refusals == ()
    (unit,) = plan.units
    assert isinstance(unit, SummaryUnit)
    assert unit.derived is True            # never authoritative
    assert unit.claim == "acme corp signed the contract in March"  # verbatim
    assert unit.claim_class == "semantic"  # the finding's class, unchanged
    assert unit.span == _span()            # the ADDRESS, not the payload
    assert unit.unit_key == unit_key(_span(), unit.claim)


def test_a_finding_without_a_span_is_refused_not_downgraded() -> None:
    """*"a written-back unit carries its own source link, or it is an
    unattributable belief"* (#5009)."""
    plan = plan_writeback([_finding(span=None)])
    assert plan.units == ()
    assert [r.reason for r in plan.refusals] == [REFUSAL_NO_PROVENANCE]
    assert plan.ok is False


def test_an_unverified_finding_is_refused_and_reported() -> None:
    plan = plan_writeback([_finding(verified=False)])
    assert plan.units == ()
    assert [r.reason for r in plan.refusals] == [REFUSAL_UNVERIFIED]
    assert plan.refusals[0].claim == "acme corp signed the contract in March"


def test_an_empty_claim_is_refused() -> None:
    plan = plan_writeback([_finding(claim="   ")])
    assert [r.reason for r in plan.refusals] == [REFUSAL_NO_CLAIM]


def test_the_write_back_refuses_to_promote_a_claim_class() -> None:
    """§12.7 "incorrect promotion": an episodic fact must not become a rule."""
    episodic = _finding(claim_class="episodic")
    plan = plan_writeback([episodic], promote_to="semantic")
    assert plan.units == ()
    assert [r.reason for r in plan.refusals] == [REFUSAL_PROMOTION]

    # Naming the finding's OWN class is not a promotion.
    same = plan_writeback([episodic], promote_to="episodic")
    assert same.ok is True
    assert same.units[0].claim_class == "episodic"


def test_the_same_verified_finding_writes_back_once() -> None:
    """The attribution loop: a repeated escalation must not pay for a duplicate."""
    first = plan_writeback([_finding()])
    again = plan_writeback([_finding()])
    both = plan_writeback([_finding(), _finding()])
    assert len(first.units) == 1
    assert len(again.units) == 1
    assert len(both.units) == 1
    assert both.units[0].unit_key == first.units[0].unit_key


def test_identity_is_the_span_plus_the_claim_not_the_span_alone() -> None:
    other_claim = _finding(claim="acme corp renewed in April")
    plan = plan_writeback([_finding(), other_claim])
    assert len(plan.units) == 2
    assert len({u.unit_key for u in plan.units}) == 2
    # A different address for the same claim is a different unit too.
    elsewhere = _finding(span=SourceSpan(source_id="source:raw-2", turn=7, start=1, end=9))
    assert unit_key(elsewhere.span, elsewhere.claim) != unit_key(_span(), _finding().claim)


def test_whitespace_and_case_do_not_mint_a_second_unit() -> None:
    variant = _finding(claim="Acme Corp   signed the contract in March")
    plan = plan_writeback([_finding(), variant])
    assert len(plan.units) == 1
    # ... and the unit keeps the FIRST occurrence verbatim.
    assert plan.units[0].claim == "acme corp signed the contract in March"


def test_a_summary_unit_can_neither_carry_the_raw_payload_nor_supersede_it() -> None:
    """Extractor §2.4.2: the raw fact behind a written-back finding is immutable.

    Pinned as a REFUSAL OF FIELDS, because the failure is a future addition: a
    ``raw``/``payload`` field turns the summary tier into a second divergent copy
    of the evidence, and a ``supersedes`` field lets it overwrite the log it is
    supposed to summarise.
    """
    names = {f.name for f in dataclasses.fields(SummaryUnit)}
    assert names == {"unit_key", "claim", "claim_class", "span", "derived"}
    forbidden = {"raw", "raw_text", "payload", "source_text", "content",
                 "supersedes", "superseded_by", "replaces", "overwrites"}
    assert names.isdisjoint(forbidden), names & forbidden


def test_planning_a_write_back_mutates_nothing_it_was_given() -> None:
    """The write-back is a PLAN over verified findings; the findings are untouched."""
    findings = (_finding(), _finding(claim="", verified=True), _finding(verified=False))
    before = tuple(dataclasses.astuple(f) for f in findings)
    plan_writeback(list(findings), promote_to=None)
    assert tuple(dataclasses.astuple(f) for f in findings) == before


@pytest.mark.parametrize("kwargs", [
    {"source_id": ""},
    {"source_id": "s", "start": 1},            # all-or-nothing offsets
    {"source_id": "s", "start": 5, "end": 5},  # end must be past start
    {"source_id": "s", "turn": -1},
    {"source_id": "s", "start": True, "end": 4},
])
def test_an_unaddressable_span_is_refused(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        SourceSpan(**kwargs)


def test_a_summary_unit_cannot_be_constructed_without_its_invariants() -> None:
    """The TYPE holds its own invariants, not only ``plan_writeback``'s output.

    ``SummaryUnit`` is public (``__all__``) and §12.5's persistence seam is the
    caller's, so a hand-built unit is the realistic route by which an
    unattributable belief reaches the store. ``span``/``derived``/``unit_key``
    were type HINTS and defaults only — ``span=None``, ``span="not-a-span"`` and
    ``derived=False`` all constructed fine, so §12.5's "no unattributable
    belief" and "always derived" were true of one function, not of the type.
    """
    good = dict(unit_key=unit_key(_span(), "acme corp signed the contract in March"),
                claim="acme corp signed the contract in March",
                claim_class="semantic", span=_span())
    assert SummaryUnit(**good).derived is True  # the valid shape still works

    with pytest.raises(TypeError):
        SummaryUnit(**{**good, "span": None})  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        SummaryUnit(**{**good, "span": "not-a-span"})  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        SummaryUnit(**{**good, "derived": False})
    with pytest.raises(ValueError):
        SummaryUnit(**{**good, "unit_key": ""})
    with pytest.raises(ValueError):
        SummaryUnit(**{**good, "unit_key": "hand-written-key"})


def test_a_unit_key_requires_an_address() -> None:
    with pytest.raises(TypeError):
        unit_key(None, "claim")  # type: ignore[arg-type]


def test_a_write_back_plan_reports_refusals_rather_than_dropping_them() -> None:
    """A partial write-back must be VISIBLE: silently dropping a finding is how a
    belief becomes evidence nobody can audit."""
    plan = plan_writeback([_finding(), _finding(claim="no address", span=None)], promote_to=None)
    assert len(plan.units) == 1
    assert [r.reason for r in plan.refusals] == [REFUSAL_NO_PROVENANCE]
    assert plan.ok is False


def test_a_write_back_refusal_reason_must_be_declared() -> None:
    from tortoise.sufficiency_router import WritebackRefusal

    with pytest.raises(ValueError):
        WritebackRefusal(claim="x", reason="looked-suspicious")


# ── the evidence normaliser ───────────────────────────────────────────────


def test_evidence_rows_are_read_from_the_wire_shape_and_from_attributes() -> None:
    class Row:
        id = "p9"
        content = "acme corp signed"
        source_ref: ClassVar[dict] = {"url": "https://example.test/9"}

    wire = evidence_record({"id": "p1", "content": "signed", "source_ref": {"url": "u"}})
    obj = evidence_record(Row())
    text = evidence_record("just a string")

    assert (wire.id, wire.text, wire.provenance) == ("p1", "signed", {"url": "u"})
    assert (obj.id, obj.text, obj.provenance) == ("p9", "acme corp signed",
                                                  {"url": "https://example.test/9"})
    assert text.text == "just a string"


def test_provenance_absent_is_none_and_provenance_present_is_the_link() -> None:
    """``None`` = the field was never fetched; that is NOT "no source"."""
    assert evidence_provenance({"id": "p1", "content": "x"}) is None
    assert evidence_provenance({"source_ref": {"url": "u"}}) == {"url": "u"}
    assert evidence_provenance({"provenance": {"source_ref": "s1"}}) == "s1"
    assert evidence_provenance({"source_ref": None}) is None


def test_the_snippet_is_read_last_so_a_truncated_preview_never_wins() -> None:
    row = evidence_record({"id": "p1", "content": "full text here", "snippet": "full"})
    assert row.text == "full text here"
    assert evidence_record({"id": "p1", "snippet": "preview"}).text == "preview"


def test_anchors_cannot_be_a_bare_string() -> None:
    with pytest.raises(TypeError):
        assess_sufficiency(_rows("x"), anchors="acme", resident=True)

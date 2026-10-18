"""§12.4 the SUFFICIENCY ROUTER + §12.5 the ESCALATION WRITE-BACK (#5009).

WHY THIS MODULE EXISTS
======================
``docs/architecture/STORAGE-ARCHITECTURE.md`` §12.4 makes one rule the
load-bearing answer to *"how do we keep the RAM bill from exploding?"*:

    Escalate on SUFFICIENCY, not on query type.

*"What kind of query is this?"* is a classifier that must be trained, drifts,
and is wrong at the boundary. *"Did the cheap tier answer it?"* is a **measured
property of the answer.** §12.8 item 3 names the artifact (§12.4's router) and
item 4 names the half that compounds (§12.5's write-back):

    "Build the write-back — otherwise escalation is a permanent tax rather
     than an investment."

This module is BOTH halves' decision layer, and it is deliberately the
*decision* only. ``tortoise/residency_router.py`` (interface D, #7953) is the
sibling — a second, distinct router over a different fact — and it already
CONSUMES a sufficiency verdict (``route_residency(sufficient=...)``) that
nothing produced until this module. The two compose; they do not overlap.

WHAT IS DECIDED HERE, AND WHAT IS NOT
=====================================
DECIDED (pure, no I/O, no clock, no model, no graph):

* **the sufficiency verdict** — a three-state fact read off the evidence
  already in hand (``assess_sufficiency``);
* **the bounded escalation trigger** — one route, and a budget that cannot be
  walked twice (``route_sufficiency``);
* **the write-back plan** — verified findings become derived summary units that
  carry their own span, cannot promote a claim's class, and dedupe by identity
  so a repeat escalation is not paid for twice (``plan_writeback``).

NOT DECIDED (deliberately, and each has a named owner):

* **the tier count.** §12.3 records TWO tiers, but that fold was made by the
  design and *not* ruled on by the owner — this issue carries it as OPEN. So
  this module never names a tier: it names the §12.4 repair (``escalate-tier``,
  the same route word the residency router already uses) and takes the budget
  as a caller parameter. Committing "Tier 2" here would settle an open ruling
  by accident.
* **the thresholds.** Every measure takes its threshold from the CALLER and
  there are no defaults. The design's method is draft → run → look → refine
  (§14.1 O5) and its numbers are hand-set on real sessions — a default here
  would be a target nobody chose (§14.1 O2) wearing a constant's clothes.
* **the walk itself, and the Tier-1 store.** No tier store exists and no
  reader calls this yet. §12.5's persistence seam is the caller's; this module
  emits the plan and performs no I/O, so it cannot become a second writer.
* **the premise measurement.** #5009's own evidence discipline says *"whether
  our queries are actually answered badly by a cheap tier … precedes the
  build"*. That measurement needs this verdict to exist (you cannot measure
  sufficiency before it has a definition), which is why the decision layer is
  the right first slice — and why the read path is NOT wired here.

THE THREE-STATE VERDICT — why not a boolean
===========================================
``undetermined`` is a first-class member, never a default:

* an **unassessed** read (no threshold supplied, or a provenance field the
  caller never fetched) must not score as ``insufficient`` — that would
  escalate on a measurement that was never taken, and re-pay forever;
* it must not score as ``sufficient`` either — that is the "silent ok" that
  §14.1 V4 exists to kill.

So ``undetermined`` yields ``sufficient=None`` — the exact shape
``route_residency`` already accepts — and the router serves, which is the
design's default answer (§12.3: Tier 1 is the DEFAULT, the deeper tier is
escalation-only). Same polarity as §17.3's ``marker=None`` → *not assessed*,
never ``hot``.

⛔ **RESIDENCY OUTRANKS SUFFICIENCY (§17.3), enforced here too.** A read the
cache could not see makes *"insufficient"* unestablished, so
``assess_sufficiency`` REFUSES to judge when the caller has not established
residency (``resident`` is ``None`` or ``False``). A residency miss is repaired
by fetching the record, never by walking a tier. The guard is the collapse
guard, and it is pinned by test.

⛔ **NO QUERY-CLASS INPUT — pinned structurally.** The router takes no
``query``, ``query_type``, ``complexity`` or ``intent`` argument, and a test
greps the signature so a future lane cannot turn this into the classifier
§12.4 rejects without reddening. ``anchors`` are **the entities the question
references**, already resolved — a coverage measurement over the answer, not a
prediction about the question.

THE WRITE-BACK'S PROVENANCE DISCIPLINE (§12.5 · extractor §2.4.2)
================================================================
A summary unit written back to the cheap tier:

* **carries its own span** (``SourceSpan``) or it is an unattributable belief —
  a finding with no span is REFUSED, never downgraded to a summary without one;
* **never replaces the raw fact.** There is no field on ``SummaryUnit`` that can
  name a superseded/replaced source and no field that carries the raw payload:
  the unit holds an ADDRESS, and a test refuses a future field named like a
  payload or a supersession. The raw log stays immutable.
* **never promotes a class** — §12.7's *"incorrect promotion: an episodic fact
  promoted into a semantic rule"* is refused by construction, because the unit's
  class is the finding's class and ``promote_to`` must either be ``None`` or
  equal to it.
* **dedupes by identity** — the key is content-addressed over (span, normalised
  claim), so the SAME verified finding written back twice is ONE unit. That is
  what makes the escalation an investment rather than a repeating tax; its
  absence is the published *"no attribution loop"* failure (§12.7).

WHAT THIS MODULE DELIBERATELY IS NOT
====================================
* **Not the residency router.** A cache miss is not an insufficiency (§17.3):
  that decision is ``tortoise/residency_router.py`` and this module never emits
  its ``fetch-record`` route.
* **Not a status term.** §17.3 fixes the read-status vocabulary at four terms
  (``tortoise/status_vocabulary.py``); a verdict and a route are *directives*,
  not statuses, and nothing here mints a fifth.
* **Not a writer.** No graph handle, no store, no ``record_*`` — the plan is
  the output.
* **No public surface change.** No MCP tool and no public ``TortoiseSDK``
  method is added, removed or renamed.
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .residency_router import ROUTE_ESCALATE_TIER, ROUTE_FETCH_RECORD, ROUTE_SERVE, ROUTES

__all__ = [
    "EVIDENCE_PROVENANCE_KEY",
    "INSUFFICIENT",
    "MEASURE_ANCHOR_COVERAGE",
    "MEASURE_PROVENANCE_COVERAGE",
    "REASONS",
    "REASON_ANCHORS_UNCOVERED",
    "REASON_BUDGET_EXHAUSTED",
    "REASON_EVIDENCE_EMPTY",
    "REASON_MEASURES_MET",
    "REASON_NOT_ESTABLISHED",
    "REASON_NO_MEASURE",
    "REASON_PROVENANCE_THIN",
    "REASON_RESIDENT_MISS",
    "REASON_RESIDENT_UNASSESSED",
    "REFUSALS",
    "REFUSAL_NO_CLAIM",
    "REFUSAL_NO_PROVENANCE",
    "REFUSAL_PROMOTION",
    "REFUSAL_UNVERIFIED",
    "ROUTES",
    "SUFFICIENT",
    "UNDETERMINED",
    "VERDICTS",
    "SourceSpan",
    "SufficiencyRoute",
    "SufficiencyVerdict",
    "SummaryUnit",
    "VerifiedFinding",
    "WritebackPlan",
    "WritebackRefusal",
    "assess_sufficiency",
    "evidence_provenance",
    "evidence_record",
    "plan_writeback",
    "route_sufficiency",
    "unit_key",
]

# ── the verdict vocabulary ──────────────────────────────────────────────────

#: The answer to *"does the evidence already in hand answer this?"* — a
#: MEASURED property of the answer, never a predicted property of the question.
SUFFICIENT = "sufficient"
INSUFFICIENT = "insufficient"
#: Not established. NEVER a synonym for either neighbour: an unassessed measure
#: is neither a negative finding (§17.3's discipline) nor a green one.
UNDETERMINED = "undetermined"

VERDICTS: tuple[str, ...] = (SUFFICIENT, INSUFFICIENT, UNDETERMINED)

#: The measure names a verdict reports. Each is a fraction in ``[0, 1]`` read
#: off the evidence itself, and each is named in the verdict so a reader can
#: audit WHY a route fired.
MEASURE_ANCHOR_COVERAGE = "anchor-coverage"
MEASURE_PROVENANCE_COVERAGE = "provenance-coverage"
MEASURES: tuple[str, ...] = (MEASURE_ANCHOR_COVERAGE, MEASURE_PROVENANCE_COVERAGE)

#: Reasons. A verdict carries every reason it found, not only the decisive one.
REASON_EVIDENCE_EMPTY = "evidence-empty"
REASON_ANCHORS_UNCOVERED = "anchors-uncovered"
REASON_PROVENANCE_THIN = "provenance-thin"
REASON_MEASURES_MET = "measures-met"
REASON_NO_MEASURE = "no-measure"
#: The residency branch: a miss is the RECORD's repair, and an unassessed read
#: has not established insufficiency at all (§17.3).
REASON_RESIDENT_MISS = "resident-miss"
REASON_RESIDENT_UNASSESSED = "resident-unassessed"
#: Serve reasons: a measured-sufficient answer, a verdict never established, and
#: an insufficiency that has no budget left to repair.
REASON_NOT_ESTABLISHED = "sufficiency-not-established"
REASON_BUDGET_EXHAUSTED = "escalation-budget-exhausted"

REASONS: tuple[str, ...] = (
    REASON_EVIDENCE_EMPTY,
    REASON_ANCHORS_UNCOVERED,
    REASON_PROVENANCE_THIN,
    REASON_MEASURES_MET,
    REASON_NO_MEASURE,
    REASON_RESIDENT_MISS,
    REASON_RESIDENT_UNASSESSED,
    REASON_NOT_ESTABLISHED,
    REASON_BUDGET_EXHAUSTED,
)

#: Write-back refusals (§12.5 / extractor §2.4.2). A refusal is RECORDED, never
#: a silent drop: a finding that cannot be written back honestly is not quietly
#: rewritten into one that can.
REFUSAL_NO_PROVENANCE = "no-provenance"
REFUSAL_UNVERIFIED = "unverified"
REFUSAL_PROMOTION = "promotion"
REFUSAL_NO_CLAIM = "no-claim"

REFUSALS: tuple[str, ...] = (
    REFUSAL_NO_PROVENANCE,
    REFUSAL_UNVERIFIED,
    REFUSAL_PROMOTION,
    REFUSAL_NO_CLAIM,
)

#: The key an evidence row carries its provenance link under (the same
#: ``source_ref`` the ``SearchResult`` wire uses for ``Point.extractedFrom``).
EVIDENCE_PROVENANCE_KEY = "source_ref"

#: The text keys an evidence row may carry its assembled content under, best
#: first. ``snippet`` is the 200-char wire preview; it is read LAST because a
#: truncated preview is a weaker coverage signal than the full content.
_TEXT_KEYS = ("content", "summary", "text", "snippet")
_PROVENANCE_KEYS = (EVIDENCE_PROVENANCE_KEY, "sourceRef")


# ── the evidence, normalised once ──────────────────────────────────────────


@dataclass(frozen=True)
class _Evidence:
    """One assembled evidence row, read as the three things the measures need.

    ``provenance`` is ``None`` when the row does not CARRY the field at all —
    which is a different fact from "the row carries no link". That distinction
    is the whole reason the provenance measure can be *unassessed* rather than
    zero: a caller reading with ``search_provenance_enabled()`` off never
    fetches ``source_ref``, and scoring that as insufficient would punish a
    flag, not the evidence.
    """

    id: str
    text: str
    provenance: Any


def _as_mapping(row: object) -> Mapping[str, Any]:
    if isinstance(row, Mapping):
        return row
    if isinstance(row, str):
        return {"content": row}
    as_dict = getattr(row, "to_dict", None)
    if callable(as_dict):
        try:
            out = as_dict()
        except Exception:  # pragma: no cover — a row that cannot serialise
            out = None
        if isinstance(out, Mapping):
            return out
    return {}


def evidence_record(row: object) -> _Evidence:
    """Read ONE assembled evidence row into the shape the measures read.

    Accepts a mapping (``SearchResult.to_dict()``), an object carrying the same
    attributes, or a bare string (its own content). Nothing here interprets the
    row's meaning — it reads the fields the read path already emits.
    """
    data = _as_mapping(row)
    if not data and not isinstance(row, Mapping):
        data = {k: getattr(row, k) for k in _TEXT_KEYS if hasattr(row, k)}
        data.setdefault("id", getattr(row, "id", ""))
        for k in _PROVENANCE_KEYS:
            if hasattr(row, k):
                data[k] = getattr(row, k)
    text = ""
    for key in _TEXT_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            text = value
            break
    return _Evidence(
        id=str(data.get("id") or ""),
        text=text,
        provenance=evidence_provenance(data),
    )


def evidence_provenance(data: object) -> Any:
    """The provenance LINK a row carries, or ``None`` when the row lacks it.

    ``None`` means **the field was not fetched** (a flag-off read, or a row kind
    that has none) — not *"this fact has no source"*. The measure that consumes
    it treats the two differently on purpose.
    """
    mapping = _as_mapping(data) or (data if isinstance(data, Mapping) else {})
    for key in _PROVENANCE_KEYS:
        if mapping.get(key):
            return mapping[key]
    nested = mapping.get("provenance")
    if isinstance(nested, Mapping):
        for key in _PROVENANCE_KEYS:
            if nested.get(key):
                return nested[key]
    return None


# ── the verdict ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SufficiencyVerdict:
    """The §12.4 answer, with the measures and reasons that produced it.

    ``sufficient`` is the ``bool | None`` bridge to ``route_residency``:
    ``True`` / ``False`` / ``None`` (undetermined). Carrying it as a property
    rather than a loose boolean is what keeps a third state from being
    flattened at the seam.
    """

    verdict: str
    reasons: tuple[str, ...] = ()
    measures: tuple[tuple[str, float], ...] = ()

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise ValueError(
                f"verdict must be one of {VERDICTS}, got {self.verdict!r} — an "
                f"unrecognised verdict is refused, not defaulted"
            )
        for reason in self.reasons:
            if reason not in REASONS:
                raise ValueError(f"unknown reason {reason!r}; expected one of {REASONS}")
        for name, value in self.measures:
            if name not in MEASURES:
                raise ValueError(f"unknown measure {name!r}; expected one of {MEASURES}")
            if not isinstance(value, float) or not (0.0 <= value <= 1.0):
                raise ValueError(f"measure {name!r} must be a fraction in [0, 1], got {value!r}")

    @property
    def sufficient(self) -> bool | None:
        """The verdict as ``route_residency``'s ``sufficient`` input."""
        if self.verdict == SUFFICIENT:
            return True
        if self.verdict == INSUFFICIENT:
            return False
        return None

    @property
    def is_insufficient(self) -> bool:
        return self.verdict == INSUFFICIENT


def _threshold(name: str, value: object) -> float:
    """A caller-supplied threshold. There is deliberately NO default."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number in [0, 1], got {value!r}")
    out = float(value)
    if not (0.0 <= out <= 1.0):
        raise ValueError(f"{name} must be in [0, 1], got {value!r}")
    return out


def _mentions(needle: str, row: _Evidence) -> bool:
    folded = needle.casefold()
    return folded in row.text.casefold() or folded == row.id.casefold()


def assess_sufficiency(
    evidence: Iterable[object],
    *,
    anchors: Sequence[str] = (),
    required_anchor_coverage: float | None = None,
    required_provenance_coverage: float | None = None,
    resident: bool | None = None,
) -> SufficiencyVerdict:
    """Measure *"does the evidence I already have actually answer this?"*

    ``evidence``   the assembled rows, already in hand — the check runs on them
                   and costs no extra retrieval (§12.4: one call, one pass).
    ``anchors``    the entities the question references, ALREADY RESOLVED by the
                   caller. Not a query class: the measure is the evidence's
                   coverage of them, not a prediction about the question.
    ``required_anchor_coverage`` / ``required_provenance_coverage``
                   caller-supplied thresholds. Omit one and that measure is NOT
                   ASSESSED (see ``REASON_NO_MEASURE``) — it never silently
                   counts as met.
    ``resident``   §17.3's precedence: ``None`` = not established, ``False`` = a
                   residency miss. Either way this function REFUSES to call the
                   evidence insufficient — that is the record's repair, not a
                   tier's.

    FAIL-CLOSED POLARITY: ``sufficient`` requires at least one measure to have
    been ASSESSED and every assessed measure to have met its threshold. A
    verdict reached from nothing is ``undetermined``.
    """
    if required_anchor_coverage is not None:
        required_anchor_coverage = _threshold(
            "required_anchor_coverage", required_anchor_coverage)
    if required_provenance_coverage is not None:
        required_provenance_coverage = _threshold(
            "required_provenance_coverage", required_provenance_coverage)
    if isinstance(anchors, (str, bytes)):
        raise TypeError("anchors must be a sequence of strings, not a single string")

    # §17.3, the collapse guard. Residency outranks sufficiency: a read the
    # store could not see has not ESTABLISHED insufficiency, so an escalation
    # here would walk a tier of a store that never held the node.
    if resident is None or resident is False:
        reason = REASON_RESIDENT_MISS if resident is False else REASON_RESIDENT_UNASSESSED
        return SufficiencyVerdict(UNDETERMINED, (reason,), ())

    rows = tuple(evidence_record(row) for row in evidence)

    if not rows:
        # Only reachable with residency ESTABLISHED, so an empty assembly is a
        # genuine insufficiency rather than a miss read as a negative finding.
        return SufficiencyVerdict(INSUFFICIENT, (REASON_EVIDENCE_EMPTY,), ())

    measures: list[tuple[str, float]] = []
    failures: list[str] = []

    if anchors and required_anchor_coverage is not None:
        covered = sum(1 for a in anchors if any(_mentions(a, r) for r in rows))
        value = float(covered) / float(len(anchors))
        measures.append((MEASURE_ANCHOR_COVERAGE, value))
        if value < required_anchor_coverage:
            failures.append(REASON_ANCHORS_UNCOVERED)

    # The provenance measure is assessed ONLY when the rows actually carry the
    # field — see `_Evidence.provenance`. An unfetched field is unassessed, not
    # zero: a flag-off read must not be scored as insufficient evidence.
    if required_provenance_coverage is not None:
        assessable = [r for r in rows if r.provenance is not None]
        if assessable:
            value = float(len(assessable)) / float(len(rows))
            measures.append((MEASURE_PROVENANCE_COVERAGE, value))
            if value < required_provenance_coverage:
                failures.append(REASON_PROVENANCE_THIN)

    if failures:
        return SufficiencyVerdict(INSUFFICIENT, tuple(failures), tuple(measures))
    if not measures:
        return SufficiencyVerdict(UNDETERMINED, (REASON_NO_MEASURE,), ())
    return SufficiencyVerdict(SUFFICIENT, (REASON_MEASURES_MET,), tuple(measures))


# ── the bounded trigger ────────────────────────────────────────────────────


@dataclass(frozen=True)
class SufficiencyRoute:
    """One canonical route — a DIRECTIVE about where to look (§17.3).

    Never a status term, and never ``fetch-record``: that is the residency
    router's repair and emitting it here would collapse the two events.
    """

    route: str
    reason: str
    verdict: str
    escalated: bool = False
    budget_remaining: int = 0

    def __post_init__(self) -> None:
        if self.route not in ROUTES:
            raise ValueError(f"route must be one of {ROUTES}, got {self.route!r}")
        if self.route == ROUTE_FETCH_RECORD:
            raise ValueError(
                "a sufficiency route may never be `fetch-record` — that is the "
                "RESIDENCY repair (§17.3), and collapsing the two events either "
                "escalates unnecessarily or treats a cache miss as a finding"
            )
        if self.verdict not in VERDICTS:
            raise ValueError(f"verdict must be one of {VERDICTS}, got {self.verdict!r}")
        if self.reason not in REASONS:
            raise ValueError(f"unknown reason {self.reason!r}; expected one of {REASONS}")
        if isinstance(self.budget_remaining, bool) or self.budget_remaining < 0:
            raise ValueError(
                f"budget_remaining must be a non-negative int, got {self.budget_remaining!r}")


def route_sufficiency(
    verdict: SufficiencyVerdict,
    *,
    escalated: bool = False,
    budget: int = 1,
) -> SufficiencyRoute:
    """The §12.4 trigger, BOUNDED — escalate once, never walk a tier loop.

    ``escalated``   this answer has already been walked deeper once, so a still
                    insufficient verdict is reported, not re-walked. That is the
                    bound on §12.7's *"router overhead"*: an unbounded repair
                    loop costs more than the retrieval it routes.
    ``budget``      remaining escalation steps the caller will pay for (the tier
                    count is OPEN per §12.3, so it is NOT hard-coded here).

    The escalation fires ONLY on a measured ``insufficient``. ``undetermined``
    serves: it is not a negative finding, and §12.3's default answer is the
    cheap tier.
    """
    if not isinstance(verdict, SufficiencyVerdict):
        raise TypeError("route_sufficiency takes a SufficiencyVerdict, not a bool — "
                        "a bare False cannot say WHY, and `None` is a real state")
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0:
        raise ValueError(f"budget must be a non-negative int, got {budget!r}")

    if verdict.verdict == INSUFFICIENT and not escalated and budget > 0:
        return SufficiencyRoute(
            route=ROUTE_ESCALATE_TIER,
            reason=verdict.reasons[0] if verdict.reasons else REASON_EVIDENCE_EMPTY,
            verdict=verdict.verdict,
            escalated=True,
            budget_remaining=budget - 1,
        )
    if verdict.verdict == INSUFFICIENT:
        # Known short, and nothing left to spend: report it rather than loop.
        return SufficiencyRoute(
            route=ROUTE_SERVE,
            reason=REASON_BUDGET_EXHAUSTED,
            verdict=verdict.verdict,
            escalated=escalated,
            budget_remaining=budget,
        )
    return SufficiencyRoute(
        route=ROUTE_SERVE,
        reason=verdict.reasons[0] if verdict.reasons else REASON_NOT_ESTABLISHED,
        verdict=verdict.verdict,
        escalated=escalated,
        budget_remaining=budget,
    )


# ── §12.5 the escalation write-back ───────────────────────────────────────


@dataclass(frozen=True)
class SourceSpan:
    """An ADDRESS into the immutable raw log (extractor §2.4.2).

    Mirrors ``commit_schema``'s span polarity: the offsets are all-or-nothing
    and ``end`` must be past ``start``. The span TEXT is never stored here — a
    written-back unit that carried a copy of the raw payload would stop being a
    summary and start being a second, divergent copy of the evidence.
    """

    source_id: str
    turn: int | None = None
    start: int | None = None
    end: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, str) or not self.source_id.strip():
            raise ValueError("a span needs the id of the raw source it addresses")
        if (self.start is None) != (self.end is None):
            raise ValueError(
                "span start and end are all-or-nothing — one offset is not an address")
        for name, value in (("turn", self.turn), ("start", self.start), ("end", self.end)):
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"span {name} must be a non-negative int, got {value!r}")
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ValueError(
                f"span end ({self.end}) must be greater than span start ({self.start})")

    def canonical(self) -> str:
        """The identity of the address, used by ``unit_key``."""
        turn = "-" if self.turn is None else str(self.turn)
        start = "-" if self.start is None else str(self.start)
        end = "-" if self.end is None else str(self.end)
        return f"{self.source_id}#{turn}:{start}-{end}"


@dataclass(frozen=True)
class VerifiedFinding:
    """What an escalation VERIFIED, plus the address that proves it.

    ``claim_class`` is the class the finding HAS. Nothing on this type lets a
    caller ask for a different one; ``plan_writeback(promote_to=...)`` is the
    only door, and it refuses anything but the same class (§12.7).
    """

    claim: str
    span: SourceSpan | None
    claim_class: str
    verified: bool = False


@dataclass(frozen=True)
class SummaryUnit:
    """A derived summary written back to the cheap tier — never authoritative.

    ``derived`` is always ``True``: a summary unit is a *finding about* the raw
    log, never a replacement for it. The type deliberately has no field naming a
    superseded/replaced source and none carrying the raw payload — see
    ``tests/test_sufficiency_router.py``, which refuses such a field, because
    "the write-back must respect extractor §2.4.2: a finding written back
    carries the SPAN, and the raw fact behind it stays immutable" (§12.5).
    """

    unit_key: str
    claim: str
    claim_class: str
    span: SourceSpan
    derived: bool = True


@dataclass(frozen=True)
class WritebackRefusal:
    """A finding that could NOT be written back honestly, and why."""

    claim: str
    reason: str

    def __post_init__(self) -> None:
        if self.reason not in REFUSALS:
            raise ValueError(f"unknown refusal {self.reason!r}; expected one of {REFUSALS}")


@dataclass(frozen=True)
class WritebackPlan:
    """The write-back's OUTPUT: units to persist, and refusals to report.

    A plan performs no I/O. Persistence is the caller's seam — no Tier-1 store
    exists yet, and §12.3's tier count is OPEN, so this module must not become
    the second place that decides where a summary unit lands.
    """

    units: tuple[SummaryUnit, ...] = ()
    refusals: tuple[WritebackRefusal, ...] = ()

    @property
    def ok(self) -> bool:
        """True when nothing was refused (an empty plan is not `ok` by itself)."""
        return bool(self.units) and not self.refusals


def _normalise_claim(claim: str) -> str:
    """Identity normalisation ONLY — the unit keeps the claim verbatim."""
    return " ".join(claim.split()).casefold()


def unit_key(span: SourceSpan, claim: str) -> str:
    """Content-addressed identity for a summary unit.

    Over ``(span, normalised claim)`` so that the SAME verified finding written
    back twice is ONE unit. This is the attribution loop's whole mechanism: the
    second escalation of the same question must not pay for a duplicate, or
    §12.5's "escalation is a permanent tax" is exactly what we ship.
    """
    if not isinstance(span, SourceSpan):
        raise TypeError("unit_key needs a SourceSpan — a unit without an address is unattributable")
    payload = f"{span.canonical()}\x00{_normalise_claim(claim)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def plan_writeback(
    findings: Iterable[VerifiedFinding],
    *,
    promote_to: str | None = None,
) -> WritebackPlan:
    """Plan §12.5's write-back: verified findings → derived summary units.

    A finding is written back only when it is VERIFIED and CARRIES A SPAN. Every
    other finding is REFUSED with a named reason and reported — never silently
    dropped, and never rewritten into a shape that passes.

    ``promote_to`` is the §12.7 *"incorrect promotion"* door, and it is bolted
    shut: it may be ``None`` or the finding's own class, never a different one
    (an episodic fact must not become a semantic rule by being written back).
    """
    units: list[SummaryUnit] = []
    refusals: list[WritebackRefusal] = []
    seen: dict[str, SummaryUnit] = {}

    for finding in findings:
        claim = finding.claim if isinstance(finding.claim, str) else ""
        if not claim.strip():
            refusals.append(WritebackRefusal(claim="", reason=REFUSAL_NO_CLAIM))
            continue
        if finding.verified is not True:
            # TierMem writes back VERIFIED findings; an unverified one is a
            # belief, and a belief written into the cheap tier is served as if
            # it were evidence next time.
            refusals.append(WritebackRefusal(claim=claim, reason=REFUSAL_UNVERIFIED))
            continue
        if finding.span is None:
            refusals.append(WritebackRefusal(claim=claim, reason=REFUSAL_NO_PROVENANCE))
            continue
        if promote_to is not None and promote_to != finding.claim_class:
            refusals.append(WritebackRefusal(claim=claim, reason=REFUSAL_PROMOTION))
            continue
        key = unit_key(finding.span, claim)
        if key in seen:
            # Idempotent: the same verified finding is ONE unit.
            continue
        unit = SummaryUnit(
            unit_key=key,
            claim=claim,
            claim_class=finding.claim_class,
            span=finding.span,
        )
        seen[key] = unit
        units.append(unit)

    return WritebackPlan(units=tuple(units), refusals=tuple(refusals))

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
by fetching the record, never by walking a tier. The guard is an ALLOW-LIST
(``resident is not True``) and a non-``bool`` ``resident`` is refused outright:
an identity test against ``False`` lets a JSON column's ``0`` through as if
residency had been established, which turns the strongest guard here into a
no-op in the expensive direction. Both are pinned by test.

⛔ **NO QUERY-CLASS INPUT — pinned structurally.** The router takes no
``query``, ``query_type``, ``complexity`` or ``intent`` argument, and a test
greps the signature so a future lane cannot turn this into the classifier
§12.4 rejects without reddening. ``anchors`` are **the entities the question
references**, already resolved — a coverage measurement over the answer, not a
prediction about the question. Each is matched as a WORD, not an infix: a bare
substring test scores ``AI`` covered on *"he said the main thing"*, which is a
confident ``sufficient`` produced by a measure that never measured.

**Provenance is read from the shape the read path emits.** ``SearchResult``
carries ``source_ref`` as an ATTRIBUTE and nests it as ``provenance.source``
on the wire (``to_dict()``, the #3837 additive contract) — both are read, so
the measure has a producer on the one shape every caller actually holds.

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
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .residency_router import ROUTE_ESCALATE_TIER, ROUTE_FETCH_RECORD, ROUTE_SERVE, ROUTES

__all__ = [
    "EVIDENCE_PROVENANCE_KEY",
    "EVIDENCE_PROVENANCE_WIRE_KEY",
    "INSUFFICIENT",
    "MEASURES",
    "MEASURE_ANCHOR_COVERAGE",
    "MEASURE_PROVENANCE_COVERAGE",
    "REASONS",
    "REASON_ALREADY_ESCALATED",
    "REASON_ANCHORS_UNCOVERED",
    "REASON_BUDGET_EXHAUSTED",
    "REASON_EVIDENCE_EMPTY",
    "REASON_MEASURES_MET",
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
#: Serve reasons: a measured-sufficient answer, an insufficiency that has no
#: budget left to repair, and one whose walk is already behind it. The last two
#: are DISTINCT on purpose: a ``reason`` is a claim, and claiming an exhausted
#: budget while ``budget_remaining`` is three is the same class of false record
#: §17.3 refuses (``residency_router``: *"the reason must name the truth too"*).
REASON_BUDGET_EXHAUSTED = "escalation-budget-exhausted"
REASON_ALREADY_ESCALATED = "already-escalated"

REASONS: tuple[str, ...] = (
    REASON_EVIDENCE_EMPTY,
    REASON_ANCHORS_UNCOVERED,
    REASON_PROVENANCE_THIN,
    REASON_MEASURES_MET,
    REASON_NO_MEASURE,
    REASON_RESIDENT_MISS,
    REASON_RESIDENT_UNASSESSED,
    REASON_BUDGET_EXHAUSTED,
    REASON_ALREADY_ESCALATED,
)

#: Which reasons a VERDICT may carry. An allow-list, because the route reads
#: ``verdict.reasons[0]`` and reports it as WHY it fired: an ``insufficient``
#: verdict justified by ``measures-met`` would escalate on a claim about a
#: measurement that PASSED. (The two budget reasons live on the ROUTE, not on a
#: verdict — they describe the caller's policy, not the evidence.)
_VERDICT_REASONS: dict[str, tuple[str, ...]] = {
    SUFFICIENT: (REASON_MEASURES_MET,),
    INSUFFICIENT: (REASON_EVIDENCE_EMPTY, REASON_ANCHORS_UNCOVERED, REASON_PROVENANCE_THIN),
    UNDETERMINED: (REASON_NO_MEASURE, REASON_RESIDENT_MISS, REASON_RESIDENT_UNASSESSED),
}

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

#: The ATTRIBUTE an evidence row / ``SearchResult`` carries its provenance
#: under. On the WIRE it is nested — ``SearchResult.to_dict()`` emits
#: ``{"provenance": {"source": …}}`` (``search_engine.py``, the #3837 additive
#: contract, pinned by ``tests/test_search_promoted_fields.py``) — so the key
#: here alone cannot read the shape the read path actually emits; see
#: ``EVIDENCE_PROVENANCE_WIRE_KEY``.
EVIDENCE_PROVENANCE_KEY = "source_ref"
#: The key the provenance link lives under INSIDE the wire's ``provenance``
#: block — the shape ``sdk.search`` / ``ask_lane`` hand a caller.
EVIDENCE_PROVENANCE_WIRE_KEY = "source"

#: The text keys an evidence row may carry its assembled content under, best
#: first. ``snippet`` is the 200-char wire preview; it is read LAST because a
#: truncated preview is a weaker coverage signal than the full content.
_TEXT_KEYS = ("content", "summary", "text", "snippet")
#: The keys a row carries the ENTITIES it names under, OUTSIDE its content text.
#: Anchor coverage is a measurement of whether the answer references the
#: question's entities, and a hit that names an entity in its ``subject``
#: (``{id, name, kind}``) or its ``topics`` (a list) is that evidence — reading
#: only ``content`` scored it ``0.0`` and escalated a tier for the very row that
#: answered the question.
_ENTITY_KEYS = ("subject", "topics")
#: Top-level (attribute-shape) aliases, then the nested wire path.
_PROVENANCE_KEYS = (EVIDENCE_PROVENANCE_KEY, "sourceRef")
_PROVENANCE_WIRE_KEYS = (EVIDENCE_PROVENANCE_WIRE_KEY, EVIDENCE_PROVENANCE_KEY)


def _entity_terms(value: Any) -> list[str]:
    """The READABLE names in a ``subject``/``topics`` value — never its kind.

    ``subject`` is ``{id, name, kind}`` and ``kind`` is a label class
    (``subjectKind``): reading it would let an anchor like ``point`` match every
    row. Only names/ids are evidence of *which entity* a row is about.
    """
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        out: list[str] = []
        for key in ("name", "label", "id"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                out.append(item)
        return out
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        nested: list[str] = []
        for item in value:
            nested.extend(_entity_terms(item))
        return nested
    return []


# ── the evidence, normalised once ──────────────────────────────────────────


@dataclass(frozen=True)
class _Evidence:
    """One assembled evidence row, read as the things the measures need.

    ``text`` is the row's assembled content; ``entities`` is the names it
    carries OUTSIDE that content (``subject``/``topics``) — both are evidence of
    what the row is about, so anchor coverage reads both.

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
    entities: str = ""


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
        for k in _PROVENANCE_KEYS + _ENTITY_KEYS:
            if hasattr(row, k):
                data[k] = getattr(row, k)
    text = ""
    for key in _TEXT_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            text = value
            break
    entities = " ".join(
        term for key in _ENTITY_KEYS for term in _entity_terms(data.get(key)))
    return _Evidence(
        id=str(data.get("id") or ""),
        text=text,
        provenance=evidence_provenance(data),
        entities=entities,
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
    # The shape the read path actually emits: ``SearchResult.to_dict()`` nests
    # the link as ``provenance.source``. Reading only the top-level attribute
    # key left the provenance measure permanently unassessed on the one shape
    # every real producer emits — a declared measure with no producer, one layer
    # down from the defect #5009 exists to kill.
    nested = mapping.get("provenance")
    if isinstance(nested, Mapping):
        for key in _PROVENANCE_WIRE_KEYS:
            if nested.get(key):
                return nested[key]
    # The ATTRIBUTE shape (``evidence_record`` accepts an object as well as its
    # ``to_dict()``), so the exported helper answers the same question for a row
    # it was handed directly. Returning ``None`` here would read as "the field
    # was never fetched" for a row that carries it — the one distinction this
    # function exists to preserve.
    if not isinstance(data, Mapping):
        for key in _PROVENANCE_KEYS:
            if hasattr(data, key) and getattr(data, key):
                return getattr(data, key)
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
        if not self.reasons:
            raise ValueError(
                f"a {self.verdict!r} verdict must carry its reason(s) — the route "
                f"reports one as WHY it fired, so a reasonless verdict would make "
                f"the route assert a cause nothing measured"
            )
        allowed = _VERDICT_REASONS[self.verdict]
        for reason in self.reasons:
            if reason not in REASONS:
                raise ValueError(f"unknown reason {reason!r}; expected one of {REASONS}")
            if reason not in allowed:
                raise ValueError(
                    f"reason {reason!r} does not belong to a {self.verdict!r} "
                    f"verdict; allowed: {allowed}"
                )
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
    """Does ``row`` MENTION ``needle`` as a word, in its text or its entities?

    A bare substring test is unbounded, and the whole verdict rests on this one
    relevance check: ``"ai" in "he said the main thing"`` is ``True``, so a
    short entity name (``AI``, ``PR``, ``Q3``, ``Art``) scored COVERED on text
    that never mentions it — a confident ``sufficient`` in the fail-open
    direction §14.1 V4 exists to kill. The comparison beside it (``row.id``) is
    exact, so the weaker signal was the looser one. Word boundaries make this
    the same KIND of test as the id check.

    BOTH SIDES are whitespace-normalised before matching: a phrase's internal
    spacing is not part of its identity (same rule as ``_normalise_claim``), and
    a wrapped ``content``/``summary`` must not make a multi-word entity
    unmatchable. The row's ``entities`` are read too — a hit that names the
    entity in its ``subject``/``topics`` IS the evidence for that entity.

    Empty/blank anchors are refused by ``assess_sufficiency`` long before they
    reach here; the guard remains as defence, since ``"" in …`` is universal.
    """
    folded = " ".join(needle.split()).casefold()
    if not folded:
        return False
    if folded == row.id.casefold():
        return True
    pattern = rf"(?<!\w){re.escape(folded)}(?!\w)"
    return any(
        re.search(pattern, " ".join(candidate.split()).casefold()) is not None
        for candidate in (row.text, row.entities) if candidate
    )


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
                   coverage of them, not a prediction about the question. Each
                   element must be a non-blank string: an empty anchor matches
                   everything, so counting it would report ``1.0`` coverage for
                   an entity nobody named.
    ``required_anchor_coverage`` / ``required_provenance_coverage``
                   caller-supplied thresholds. Omit one and that measure is NOT
                   ASSESSED (see ``REASON_NO_MEASURE``) — it never silently
                   counts as met. Supplying the anchor threshold while
                   resolving NO anchors is refused rather than silently
                   unassessed: the caller configured a measure, and reporting
                   ``no-measure`` would blame a configuration that exists.
    ``resident``   §17.3's precedence: ``None`` = not established, ``False`` = a
                   residency miss. Either way this function REFUSES to call the
                   evidence insufficient — that is the record's repair, not a
                   tier's. Anything that is not a real ``bool``/``None`` (``0``,
                   ``""``, an int flag from a JSON payload) is REFUSED: an
                   allow-list guard is what keeps this from failing open.

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
    for anchor in anchors:
        if not isinstance(anchor, str):
            raise TypeError(f"each anchor must be a string, got {anchor!r}")
        if not anchor.strip():
            raise ValueError(
                "an anchor must name something — an empty/blank anchor matches "
                "every row, so counting it reports full coverage for an entity "
                "nobody named (the fail-open direction §14.1 V4 forbids)"
            )
    if required_anchor_coverage is not None and not anchors:
        raise ValueError(
            "required_anchor_coverage was supplied but no anchors were resolved — "
            "an empty anchor set cannot disable a measure the caller configured; "
            "pass required_anchor_coverage=None to leave it unassessed"
        )
    if resident is not None and not isinstance(resident, bool):
        # An ALLOW-LIST, not a truthiness test: ``resident = row.get("resident")``
        # from a JSON column is ``0``/``1``, and identity tests against ``False``
        # let ``0`` through as if residency had been ESTABLISHED — the guard would
        # then be a no-op in the one direction §17.3 forbids (an unassessed read
        # walking a tier). Refused loudly so a caller converts deliberately.
        raise TypeError(
            f"resident must be True, False or None, got {resident!r} — a "
            f"non-bool (an int flag, \"\", 0) is not an established residency"
        )

    # §17.3, the collapse guard. Residency outranks sufficiency: a read the
    # store could not see has not ESTABLISHED insufficiency, so an escalation
    # here would walk a tier of a store that never held the node.
    if resident is not True:
        reason = REASON_RESIDENT_MISS if resident is False else REASON_RESIDENT_UNASSESSED
        return SufficiencyVerdict(UNDETERMINED, (reason,), ())

    rows = tuple(evidence_record(row) for row in evidence)

    if not rows:
        # Only reachable with residency ESTABLISHED, so an empty assembly is a
        # genuine insufficiency rather than a miss read as a negative finding.
        return SufficiencyVerdict(INSUFFICIENT, (REASON_EVIDENCE_EMPTY,), ())

    measures: list[tuple[str, float]] = []
    failures: list[str] = []

    if required_anchor_coverage is not None:  # anchors is non-empty: see the guard
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
        if not isinstance(self.escalated, bool):
            raise ValueError(f"escalated must be a bool, got {self.escalated!r}")
        if isinstance(self.budget_remaining, bool) or not isinstance(
                self.budget_remaining, int):
            raise ValueError(
                f"budget_remaining must be a non-negative int, got {self.budget_remaining!r}")
        if self.budget_remaining < 0:
            raise ValueError(
                f"budget_remaining must be a non-negative int, got {self.budget_remaining!r}")


def route_sufficiency(
    verdict: SufficiencyVerdict,
    *,
    budget: int,
    escalated: bool = False,
) -> SufficiencyRoute:
    """The §12.4 trigger, BOUNDED — escalate once, never walk a tier loop.

    ``budget``      remaining escalation steps the caller will pay for. **The
                    tier count is OPEN per §12.3, so it is NOT hard-coded here —
                    not even as a default**: a default of ``1`` would settle the
                    two-tier fold for every caller who omits it, which is the
                    same class of un-chosen target ``_threshold`` refuses. Every
                    caller states the policy it is implementing.
    ``escalated``   this answer has already been walked deeper once, so a still
                    insufficient verdict is reported, not re-walked. That is the
                    bound on §12.7's *"router overhead"*: an unbounded repair
                    loop costs more than the retrieval it routes.

    The escalation fires ONLY on a measured ``insufficient``. ``undetermined``
    serves: it is not a negative finding, and §12.3's default answer is the
    cheap tier.

    The two bounds report DISTINCTLY — ``escalation-budget-exhausted`` only when
    the budget is actually gone, ``already-escalated`` when the walk is simply
    behind this verdict — because ``reason`` is a claim a reason-keyed consumer
    acts on, and ``budget_remaining`` must not contradict it.
    """
    if not isinstance(verdict, SufficiencyVerdict):
        raise TypeError("route_sufficiency takes a SufficiencyVerdict, not a bool — "
                        "a bare False cannot say WHY, and `None` is a real state")
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0:
        raise ValueError(f"budget must be a non-negative int, got {budget!r}")

    if verdict.verdict == INSUFFICIENT and not escalated and budget > 0:
        return SufficiencyRoute(
            route=ROUTE_ESCALATE_TIER,
            # The verdict GUARANTEES at least one reason and that it names an
            # insufficiency (``_VERDICT_REASONS``), so this is the cause the
            # verdict actually stated — never a fabricated fallback.
            reason=verdict.reasons[0],
            verdict=verdict.verdict,
            escalated=True,
            budget_remaining=budget - 1,
        )
    if verdict.verdict == INSUFFICIENT:
        # Known short, and either nothing left to spend OR the walk is already
        # behind this verdict: report it rather than loop. The two are DISTINCT
        # reasons — the budget one is only true when the budget is actually gone,
        # and `budget_remaining` below must not contradict what `reason` claims.
        reason = (REASON_BUDGET_EXHAUSTED if budget == 0
                  else REASON_ALREADY_ESCALATED)
        return SufficiencyRoute(
            route=ROUTE_SERVE,
            reason=reason,
            verdict=verdict.verdict,
            escalated=escalated,
            budget_remaining=budget,
        )
    return SufficiencyRoute(
        route=ROUTE_SERVE,
        reason=verdict.reasons[0],
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

    def __post_init__(self) -> None:
        """The type holds its own invariants, like every sibling in this module.

        ``plan_writeback`` refuses a finding without a span, but the TYPE is
        public (``__all__``) and §12.5's persistence seam is the caller's — so a
        hand-built unit is the realistic way an unattributable belief reaches the
        store. A field-name pin cannot see a value-level bypass; this can.
        """
        if not isinstance(self.span, SourceSpan):
            raise TypeError(
                f"a summary unit needs a SourceSpan, got {self.span!r} — a unit "
                f"without an address is an unattributable belief (§12.5)"
            )
        if not isinstance(self.claim, str) or not self.claim.strip():
            # ``plan_writeback`` REFUSES `no-claim`; the type must not accept what
            # the planner refuses, and a non-str claim would otherwise reach
            # ``_normalise_claim`` and raise AttributeError instead of a refusal.
            raise ValueError(
                f"a summary unit needs a non-empty claim, got {self.claim!r}")
        if self.derived is not True:
            raise ValueError(
                "a summary unit is always derived: it is a finding ABOUT the raw "
                "log and can never be authoritative (extractor §2.4.2)"
            )
        if not isinstance(self.unit_key, str) or not self.unit_key.strip():
            raise ValueError(
                f"unit_key must be a non-empty string, got {self.unit_key!r}")
        expected = unit_key(self.span, self.claim)
        if self.unit_key != expected:
            raise ValueError(
                f"unit_key {self.unit_key!r} is not the identity of (span, claim) "
                f"— expected {expected!r}; the attribution loop dedupes on this key, "
                f"so a hand-written one silently mints a duplicate"
            )


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

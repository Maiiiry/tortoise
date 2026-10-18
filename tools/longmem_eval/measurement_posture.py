"""The measurement POSTURE of a LongMemEval run — #8486.

A LongMemEval number is only readable when it says WHICH configuration
produced it. #8486's comment thread pins two facts that must ride every
surface a number appears on:

* the **ingest mode** (``deterministic`` = raw-content graph, no extraction;
  ``v2`` = the production extractor, real Layer-1 payload), and
* **whether the belief layer was activated** (``ep_activation.activate_beliefs``
  — promotion of the draft extraction points/operators + a real EP pass).

The second one matters because ``ingest_v2`` writes every extraction point
``status="draft"`` and mints its operators with ``promote_source=False``,
while ``tortoise/ep.py`` filters the factor graph with ``_live_only(...)``
(``include_draft`` defaults to ``False``) — so an un-activated eval graph is
EP-INERT and every claim reads the neutral ``0.50`` of a Beta(1,1) prior that
was NEVER measured (#2598). A ``v2`` run without activation therefore has the
structure but no belief: its number must never be described as measuring
Tortoise's reasoning (``docs/benchmarks/longmemeval-500q/run-report.md`` §3).

This module is the ONE place that derives the posture, so the runner (which
records it), the report methodology (which carries it) and the published
receipt (which REFUSES a completed run without it) can never disagree.

Hermetic by construction: stdlib only, no DB, no network — the callers own the
mechanics (which outcomes exist, whether the arm was requested).
"""
from __future__ import annotations

from typing import Any

#: The belief layer WAS activated for the run (every question's EP pass ran).
BELIEF_LAYER_ACTIVATED = "activated"
#: Activation was requested but did not complete cleanly for every question —
#: the run mixes a measured belief layer with an EP-inert one, so it may not
#: be described as a belief-activated measurement.
BELIEF_LAYER_PARTIAL = "partial"
#: Activation was NOT requested: the belief layer contributes nothing (for
#: ``deterministic`` graphs no operators exist at all; for ``v2`` graphs the
#: draft points are EP-inert). The report must say so next to the number.
BELIEF_LAYER_INERT = "inert"
#: No activation decision is recorded (a pre-#8486 report, or a caller that
#: never resolved the arm). NOT a publishable posture — the receipt gate
#: refuses it (#8486).
BELIEF_LAYER_UNKNOWN = "unknown"

BELIEF_LAYER_VALUES: frozenset[str] = frozenset({
    BELIEF_LAYER_ACTIVATED, BELIEF_LAYER_PARTIAL, BELIEF_LAYER_INERT,
    BELIEF_LAYER_UNKNOWN,
})

#: Manifest keys summed over the run by :func:`summarize_belief_activation`.
_SUMMED_KEYS = (
    "points_promoted",
    "operators_promoted",
    "points_with_ep",
    "points_unmeasured",
)


def _as_int(value: Any) -> int:
    """Non-negative int from a manifest cell; junk counts as 0."""
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def summarize_belief_activation(
    outcomes: list[dict[str, Any]],
    *,
    requested: bool,
) -> dict[str, Any]:
    """Aggregate the per-outcome activation manifests into ONE run block.

    Each outcome may carry ``belief_activation`` — the manifest returned by
    ``ep_activation.activate_beliefs`` (``points_promoted``,
    ``operators_promoted``, ``ep_ran``, ``ep_engine``, ``points_with_ep``,
    ``points_unmeasured``) or the runner's fail-open shape
    ``{"ep_ran": False, "error": "..."}``.

    Deterministic, order-independent, and pure: ``requested`` comes from the
    caller's resolved arm, never inferred from the outcomes (a requested arm
    that produced no manifest is a FAILED activation, not an absent arm).
    """
    manifests = [o.get("belief_activation") for o in outcomes or ()
                 if isinstance(o.get("belief_activation"), dict)]
    summary: dict[str, Any] = {
        "requested": bool(requested),
        "questions": len(manifests),
        "ep_ran": sum(1 for m in manifests if m.get("ep_ran") is True),
        "failed": sum(1 for m in manifests if m.get("error")),
        "engine": next((m.get("ep_engine") for m in manifests
                        if m.get("ep_engine")), None),
        "errors": [str(m.get("error")) for m in manifests if m.get("error")][:5],
    }
    for key in _SUMMED_KEYS:
        summary[key] = sum(_as_int(m.get(key, 0)) for m in manifests)
    return summary


def belief_layer_state(*, requested: bool,
                       activation: dict[str, Any] | None) -> str:
    """The belief-layer state the posture reports.

    ``activated`` requires a POSITIVE measurement for every question — a
    request alone is never consent (an arm that raised, or a run with no
    questions, must not read as activated).
    """
    if not requested:
        return BELIEF_LAYER_INERT
    if not isinstance(activation, dict):
        return BELIEF_LAYER_UNKNOWN
    questions = _as_int(activation.get("questions", 0))
    if questions == 0:
        # Armed, but not one question produced a manifest: unproven, never
        # "activated" on the strength of the request alone.
        return BELIEF_LAYER_PARTIAL
    if (_as_int(activation.get("failed", 0)) == 0
            and _as_int(activation.get("ep_ran", 0)) == questions):
        return BELIEF_LAYER_ACTIVATED
    return BELIEF_LAYER_PARTIAL


def build(*, ingest_mode: str,
          activation: dict[str, Any] | None = None) -> dict[str, Any]:
    """The run's measurement-posture block.

    ``beliefs_activated`` is the boolean the receipt gate requires. ``None`` —
    no decision ever reached this run (``activation is None``: a report
    produced before #8486 wired the arm) — is deliberately NOT ``False``: the
    gate must refuse an unrecorded posture rather than claim one nobody took.
    """
    if activation is None:
        return {
            "ingest_mode": str(ingest_mode or ""),
            "beliefs_activated": None,
            "belief_layer": BELIEF_LAYER_UNKNOWN,
            "activation": None,
        }
    requested = bool(activation.get("requested"))
    layer = belief_layer_state(requested=requested, activation=activation)
    beliefs_activated: bool | None = None
    if layer == BELIEF_LAYER_INERT:
        beliefs_activated = False
    elif layer == BELIEF_LAYER_UNKNOWN:
        beliefs_activated = None
    else:
        beliefs_activated = True
    return {
        "ingest_mode": str(ingest_mode or ""),
        "beliefs_activated": beliefs_activated,
        "belief_layer": layer,
        "activation": activation,
    }


def of_report(report: dict[str, Any] | None) -> dict[str, Any] | None:
    """Read the posture block a report carries (``methodology`` section)."""
    if not isinstance(report, dict):
        return None
    methodology = report.get("methodology") or {}
    if not isinstance(methodology, dict):
        return None
    posture = methodology.get("measurement_posture")
    return posture if isinstance(posture, dict) else None

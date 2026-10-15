"""Semantic change feed — fold raw ``:GraphEvent`` rows into per-claim deltas (#2353).

WHY THIS EXISTS
---------------
``poll_events`` returns the ``:GraphEvent`` stream verbatim: a flat, typed event
list. Answering *"what changed for claim X since cursor C?"* therefore costs the
consumer a client-side replay across event types — re-deriving intent the graph
already recorded. This module answers it in one call by AGGREGATING the events
the poll already returns into per-claim deltas, each with ``before``/``after``.

WHAT IT IS NOT
--------------
* It is **not** a second store and **not** a new read surface. It is a pure fold
  over the rows a poll already produced, computed in the SDK assembly layer, and
  emitted as an **off-by-default additive response field** (``changes``) on the
  existing ``poll_events`` response — the recorded ``response_fields`` carve-out
  in ``docs/product/mcp-sdk-surface.md`` (no new tool, no new SDK method).
* It does **not** change what the ``:GraphEvent`` store carries by default. The
  store's contract is the *claim-transition* stream (``_GRAPH_EVENT_TYPES``), a
  decision recorded in ``docs/plans/2026-08-08-432-subscriptions-plan.md`` §Task 3
  and pinned by ``tests/test_event_store.py::test_content_edit_emits_nothing``.
  ``PointRevised`` reaches the store **only** when this feature is switched on,
  so the default stream stays byte-identical (see ``_emit_event``).

``before`` IS OBSERVED; A PRECONDITION IS NOT A ``before``
-------------------------------------------------------
A delta's ``before`` carries ONLY values the SAME window already observed for
that field (the fold keeps the last observed ``after`` per claim). It is omitted
when the window observed none — a content revision reports content
before→after because the previous revision is in the window, and a claim's FIRST
event in the window reports no ``before`` rather than a fabricated one.

A transition whose write path enforces a precondition carries that precondition
in its OWN key, ``precondition``, which is a property of the EVENT TYPE and
never of the observation. Keeping the two apart is load-bearing: a consumer must
be able to tell "we saw the old value" (``before``) from "the write path
refuses this transition unless the old state held" (``precondition``).

Shape (``changes`` in the ``poll_events`` response)::

    [
      {"id": "<claim id>",
       "changes": [
         {"seq": 4, "ts": "...", "event_id": "...", "event": "PointSuperseded",
          "change": "superseded",
          "precondition": {"terminal": False},
          "after": {"status": "superseded", "supersededBy": "..."}},
         {"seq": 5, "ts": "...", "event_id": "...", "event": "PointRevised",
          "change": "revised",
          "before": {"content": "v1"}, "after": {"content": "v2"}}
       ]}
    ]

Groups are ordered by their first ``seq``; deltas inside a group are in ``seq``
order. An event naming several claims (``OperatorAdded`` names its operator, its
``source_id`` and each of its ``target_ids``) yields one delta per named claim —
"evidence added" is news for the point it supports, not only for the operator.
"""
from __future__ import annotations

import os
from typing import Any

from .env_truthy import is_truthy

#: The single switch for the whole feature. Unset/blank/garbage -> OFF, so the
#: default surface (response shape AND event-store contents) is byte-identical.
CHANGE_FEED_ENV = "TORTOISE_CHANGE_FEED"

#: Payload keys that NAME claims rather than describe a changed value.
_SUBJECT_KEYS = ("id", "new_id", "source_id", "target_ids")

#: Keys a `PointRevised` record may carry that are PLUMBING, not news: the
#: vector-machinery keys `update_point` journals (`#4208`/`#5004` — derived from
#: the content this same delta already reports) and the derived `content_hash`.
#: Catching whether content changed is what the delta is for; carrying a vector
#: would both bloat the feed and read as a second, drifting statement of it.
_PLUMBING_KEYS = frozenset({
    "embedding", "embedding_verbatim", "embedding_model",
    "embedding_revision", "embedding_text_hash", "content_hash",
})

#: The write-path precondition a transition IMPLIES — a property of the event
#: type, reported under its own key (never merged into an observed ``before``).
#: ``terminal`` is the predicate the shared lifecycle guard enforces
#: (``_terminal_excluded``): retract/supersede/invalidate refuse an already-
#: terminal claim. ``status: draft`` is ``promote_point``'s CAS.
_PRECONDITIONS: dict[str, dict[str, Any]] = {
    "PointSuperseded": {"terminal": False},
    "PointRetracted": {"terminal": False},
    "PointInvalidated": {"terminal": False, "outdated": False},
    "PointPromoted": {"status": "draft"},
    "OperatorPromoted": {"status": "draft"},
}


def enabled() -> bool:
    """Is the semantic change feed switched on for this process?"""
    return is_truthy(os.environ.get(CHANGE_FEED_ENV))


def _subjects(payload: dict[str, Any]) -> list[str]:
    """The claim ids this event is news ABOUT (ordered, de-duplicated).

    ``id`` first, then the ids the event names as related — so an
    ``OperatorAdded`` is reported against the operator, the point it supports
    and every target it relates. Deliberately NOT ``new_id``: a supersede is news
    about the claim that was superseded, and reporting the SUCCESSOR as
    ``superseded`` would state the opposite of what happened. The successor is
    named in the delta's ``after.supersededBy`` instead.
    """
    ids: list[str] = []
    for key in ("id", "source_id"):
        val = payload.get(key)
        if isinstance(val, str) and val and val not in ids:
            ids.append(val)
    targets = payload.get("target_ids")
    if isinstance(targets, (list, tuple)):
        for t in targets:
            if isinstance(t, str) and t and t not in ids:
                ids.append(t)
    return ids


def _after(type_: str, payload: dict[str, Any]) -> dict[str, Any]:
    """The semantic "after" state this event establishes.

    Derived from the event's own meaning — never a ``properties(n)`` snapshot
    (a snapshot would report unchanged fields as "changed").
    """
    after: dict[str, Any] = {}
    if type_ == "PointAdded":
        for k in ("kind", "pointKind", "content_hash"):
            if k in payload:
                after[k] = payload[k]
    elif type_ == "PointRevised":
        for k, v in payload.items():
            if k in _SUBJECT_KEYS or k in _PLUMBING_KEYS:
                continue
            # `new_content` is the journal spelling; `content` is the claim's
            # canonical field name, so the delta speaks the canonical one.
            after["content" if k == "new_content" else k] = v
    elif type_ == "PointSuperseded":
        after["status"] = "superseded"
        if payload.get("new_id"):
            after["supersededBy"] = payload["new_id"]
        for src, dst in (("valid_to", "validTo"), ("expired_at", "expiredAt")):
            if payload.get(src) is not None:
                after[dst] = payload[src]
    elif type_ == "PointRetracted":
        after["status"] = "retracted"
    elif type_ == "PointInvalidated":
        after["outdated"] = True
        if payload.get("corrected_by"):
            after["correctedBy"] = payload["corrected_by"]
        for src, dst in (("valid_to", "validTo"), ("expired_at", "expiredAt")):
            if payload.get(src) is not None:
                after[dst] = payload[src]
    elif type_ in ("PointPromoted", "OperatorPromoted"):
        after["status"] = "live"
    elif type_ == "OperatorAdded":
        for k in ("op_type", "source_id", "target_ids"):
            if payload.get(k) is not None:
                after[k] = payload[k]
    elif type_ == "OperatorAnnotated":
        for k in ("bias", "precision", "consistency", "directness"):
            if payload.get(k) is not None:
                after[k] = payload[k]
    elif type_ in ("DedupeRecorded", "DedupeRejected"):
        for k in ("candidate_type", "wired", "deferred_to_promotion",
                  "target_id", "temporal_target_id"):
            if payload.get(k) is not None:
                after[k] = payload[k]
    elif type_ == "ObjectSuperseded":
        after["status"] = "superseded"
        for k in ("supersedes_by", "session_id"):
            if payload.get(k) is not None:
                after[k] = payload[k]
    return after


#: event type -> the semantic change name the delta carries.
_CHANGE_NAME = {
    "PointAdded": "created",
    "PointRevised": "revised",
    "PointSuperseded": "superseded",
    "PointRetracted": "retracted",
    "PointInvalidated": "invalidated",
    "PointPromoted": "promoted",
    "OperatorPromoted": "promoted",
    "OperatorAdded": "evidence-added",
    "OperatorAnnotated": "annotated",
    "DedupeRecorded": "dedupe-recorded",
    "DedupeRejected": "dedupe-rejected",
    "ObjectSuperseded": "superseded",
}


def _observed_before(after: dict[str, Any],
                     observed: dict[str, Any]) -> dict[str, Any]:
    """The fields this delta changes FOR WHICH the window already saw a value.

    Observed-only by construction: an unobserved field is absent from the
    result, never back-filled from a precondition (that has its own key) and
    never invented.
    """
    return {field: observed[field] for field in after if field in observed}


def aggregate_changes(events: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Fold ``poll_events`` rows into per-claim semantic deltas.

    ``events`` is the list ``read_after`` returns: dicts carrying ``seq``,
    ``ts``, ``type``, ``event_id`` and a decoded ``payload`` mapping. An
    unknown/unshaped row is skipped rather than raising — the feed describes
    what the stream holds, and a stream is allowed to grow types this build
    does not know.
    """
    observed: dict[str, dict[str, Any]] = {}
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for ev in sorted(events or [], key=lambda e: e.get("seq") or 0):
        type_ = ev.get("type")
        name = _CHANGE_NAME.get(type_)
        if name is None:
            continue
        payload = ev.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        after = _after(type_, payload)
        for subject in _subjects(payload):
            seen = observed.get(subject, {})
            delta: dict[str, Any] = {
                "seq": ev.get("seq"),
                "ts": ev.get("ts"),
                "event_id": ev.get("event_id"),
                "event": type_,
                "change": name,
            }
            before = _observed_before(after, seen)
            if before:
                delta["before"] = before
            if _PRECONDITIONS.get(type_):
                delta["precondition"] = dict(_PRECONDITIONS[type_])
            delta["after"] = after
            group = grouped.get(subject)
            if group is None:
                group = {"id": subject, "changes": []}
                grouped[subject] = group
                order.append(subject)
            group["changes"].append(delta)
            # The fold advances on EVERY subject the event named: the same
            # observed state is what the next delta for that subject compares to.
            observed[subject] = {**seen, **after}

    return [grouped[s] for s in order]

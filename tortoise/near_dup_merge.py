"""#5006 / D12·O4 — near-duplicate MERGE: the FUSION half (union of attachments).

THE GAP THIS CLOSES, IN ONE SENTENCE
------------------------------------
Design of record `EXTRACTOR-V4-ARCHITECTURE.md` §4.2 puts the near-duplicate
concern at **three** sites and says so explicitly — *"so no operation has two
homes, and no passage in this document places a fusion before the gate"* —
and the third site had no implementation:

| operation | site | who | state before this module |
|---|---|---|---|
| **DETECT** the pair + apply the §16.4 never-across guard | `S2.2a-β` | mechanical | ✅ `extractor_v2._boundary` (`#5080`) |
| record the **VERDICT** `MERGE` for a pair that survives the guard | `S2.2b` VET | the Jev arbiter | ✅ `vet_gate.OUTCOMES` (`#5005`) — *"nothing consumes it yet"* |
| execute the **FUSION** — the union of both sides' attachments | `S3` | the resolve/consolidation tail | ✅ **this module** |

The verdict had no consumer and the action had no implementation; that is
exactly what `#5006` names and what `config/v4-mechanisms.yml` recorded as
`state: not-implemented`.

WHAT THE RULING SAYS (D12/O4 — §16.4, and `STORAGE-ARCHITECTURE.md` §14.1 O4)
------------------------------------------------------------------------------
*"yes we can merge near duplicate claims. maybe we can have jev with the
claim and the narrative/raw data/both arbiter that."* — and the two rules
that bound it:

- **`UNION THE ATTACHMENTS, NEVER FUSE THE CLAIM`** (§S2.2). The fusion keeps
  the PRIOR's claim verbatim and adds the candidate's attachments to it. It
  never writes a new claim built from both. `FUSE` is FORBIDDEN (§16.3·O4).
- **A merge never crosses a difference in a number, a name, a negation, a
  condition, a date, a scope, or a role inversion** — the §16.4 never-across
  list, which `_boundary` already enumerates mechanically.

WHY THE BAR IS HIGH (and it is evidence, not caution)
-----------------------------------------------------
*sic* — *"a false merge costs MORE than keeping near-duplicates"*; multiple
independent sources find over-aggressive consolidation *"destroys specific
details needed for factual QA"*; practical thresholds cluster at **~0.95**.
⇒ `MERGE_MIN_OVERLAP = 0.95`. ⚠️ **This is NOT a volume optimisation** — O2
withdraws every *target*, and the honest caveat in §16.4 is that *"if the
draft-and-run loop shows merges losing detail, THAT is the signal to raise the
bar"*. Volume reduction is a side effect, never the reason.

WHY THE VERDICT IS STILL REQUIRED (the `flagged` operand)
--------------------------------------------------------
The mechanical guard answers *"is anything distinguishing lost?"*. It cannot
answer *"are these the same claim?"* — that is the semantic question D12 gives
the arbiter. So a pair below the never-across guard is **eligible**, and only a
`MERGE` verdict from `S2.2b VET` makes it **admissible**. `flagged=False` is
the default and it means: no verdict, no fusion. A mechanical bar alone is not
authorised to merge, and this module does not let it.

WHAT THE FUSION WRITES, AND WHERE THE LOSING SIDE'S EVIDENCE GOES
-----------------------------------------------------------------
A `NOOP` fold emits no payload point at all, so it stamps nothing: that is why
`#9424` had to *refuse* a fold that would drop an entity the candidate names.
This module is the authorised alternative for exactly that case — the fold
becomes admissible because nothing is dropped:

1. **ONE node survives** — the prior's `Point`, its claim byte-identical. No
   second point is created and the prior's text is not rewritten.
2. **Its attachment set becomes the UNION** — every `Object` either claim
   named is linked to the survivor (`apply_merge_union`).
3. **The losing document's provenance survives as a `(Session)-[:CONTAINS]->
   (Point)` edge** — the second session's evidence is a member of the claim's
   memory, which is the same channel the exact-fold path (`#2104`) uses.
4. **The merge itself is recorded** — `execute_embed` returns it in
   `result["merges"]` (never inside the payload, exactly like `noops`), with
   the losing candidate's `quote`, its content, and the union it wrote. A
   merge a reader cannot find is indistinguishable from a silent drop.

FAIL-CLOSED HERE — the opposite direction to `_attachments_preserved`
---------------------------------------------------------------------
`extractor_v2._attachments_preserved` (the `#9424` guard) is fail-OPEN: an
absent attachment set on either side PERMITS a fold, because refusing on an
unknown set would silently become *"never fold"* wherever the S3 enrichment is
off — a volume regression with no safety gain. **This module is the opposite,
deliberately**, because it does not merely decline to act: it WRITES. An
unreadable attachment set on either side means the union cannot be computed,
so no union can be written, so the merge is refused and both claims survive
(`reason="unreadable_attachments"`). A wrong keep is noise; a wrong merge is
memory loss.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: The verdict token. Spelled identically to `vet_gate.MERGE` — the S2.2b
#: vocabulary is the authority and this module consumes it, so a drift here is
#: a bug in this line. `extractor_v2` imports `vet_gate` (never the reverse), so
#: the constant is duplicated rather than imported to keep this module
#: importable on its own; `tests/test_near_dup_merge_5006.py` pins the two
#: spellings equal.
MERGE = "MERGE"

#: **The high bar** (§16.4): *"Practical thresholds cluster at ~0.95"*, and the
#: direction of error is asymmetric — a false merge costs more than a kept
#: near-duplicate. Lowering this is a decision, not a tuning knob: it re-opens
#: the "merging can lose information" caveat §16.4 records.
MERGE_MIN_OVERLAP = 0.95


@dataclass(frozen=True)
class MergePlan:
    """One pair's fusion verdict: whether to merge, and the union to write.

    ``merge=False`` is a REFUSAL and it is the common case — every operand of
    the ladder below can produce it, and a refused pair falls through to `ADD`
    in ``classify_consolidation`` (both claims survive).
    """

    merge: bool
    prior_id: str = ""
    overlap: float = 0.0
    #: The whole attachment set the survivor must carry after the fusion.
    union_entities: tuple[str, ...] = ()
    #: What the candidate contributes that the prior does not already carry —
    #: the evidence the fold would have dropped, which is the reason this
    #: mechanism exists. Empty on a merge means `NOOP` was already lossless.
    added_entities: tuple[str, ...] = ()
    #: Machine-readable why: one of the ladder's refusal reasons, or
    #: ``"near_identical"``.
    reason: str = ""
    #: Human-readable why, recorded on the merge record.
    evidence: str = ""


def _entity_ids(value: object) -> frozenset[str] | None:
    """The normalised attachment ids in an ``about_entities``-shaped value, or
    ``None`` when the value is not a usable attachment set.

    ``None`` — not an empty set — for anything that is not a sequence of
    strings: *"this row names no entities"* and *"this row's entities could not
    be read"* are different facts, and only the FIRST may be merged across
    (see the module docstring's fail-closed note).
    """
    if not isinstance(value, (list, tuple, set, frozenset)):
        return None
    ids: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            return None
        if item.strip():
            ids.add(item.strip().lower())
    return frozenset(ids)


def near_dup_merge(candidate: dict, prior: dict, *, overlap: float,
                   difference: str | None,
                   identity: frozenset[str] | set[str],
                   flagged: bool) -> MergePlan:
    """Decide ONE near-duplicate fusion: merge, or refuse and say why.

    ``candidate`` / ``prior`` are S3 point rows (``{id, content,
    about_entities}``-shaped). ``overlap``, ``difference`` and ``identity`` are
    the values the caller ALREADY computed for this pair — `_boundary`'s two
    returns and the length-guarded token overlap — so the never-across guard is
    evaluated once and cannot disagree with itself here.

    The ladder, in order (each rung is a REFUSAL and each has its own reason):

    1. ``flagged`` — no `MERGE` verdict from `S2.2b`, no fusion (D12: the
       verdict is the arbiter's; a mechanical bar cannot stand in for it).
    2. ``identity`` non-empty — a distinguishing difference (negation,
       condition, scope, language, substituted content). Absolute.
    3. ``difference`` not None — a value difference (number/quantity or date).
    4. ``overlap < MERGE_MIN_OVERLAP`` — not near-identical enough.
    5. an unreadable attachment set on either side — the union is unknown, so
       nothing can be written (fail-CLOSED; see the module docstring).
    6. nothing to union — the prior already carries every entity the candidate
       names. That pair is `NOOP`'s, which is lossless and cheaper; merging it
       would do the same work on a riskier path for no gain.
    """
    if not flagged:
        return MergePlan(False, reason="not_flagged",
                         evidence="no MERGE verdict from S2.2b VET")

    differences = sorted(identity) if identity else []
    if differences:
        # The §16.4 never-across list. Absolute, and reported by its own
        # dimension so the refusal is auditable rather than a bare false.
        return MergePlan(
            False, reason="distinguishing_difference",
            evidence=f"refused across {', '.join(differences)} "
                     "(§16.4 never-across)")

    if difference is not None:
        return MergePlan(
            False, reason="value_difference",
            evidence=f"refused across a value difference ({difference}) "
                     "(§16.4 never-across)")

    try:
        ov = float(overlap)
    except (TypeError, ValueError):
        ov = 0.0
    if ov < MERGE_MIN_OVERLAP:
        return MergePlan(
            False, overlap=ov, reason="below_bar",
            evidence=f"overlap {ov:.3f} < {MERGE_MIN_OVERLAP} merge bar")

    # Fail-closed on an unknown set: `_attachments_preserved` is fail-OPEN
    # because it only DECLINES to fold; this writes, so unknown means refuse.
    prior_ids = _entity_ids(prior.get("about_entities"))
    cand_ids = _entity_ids(candidate.get("about_entities"))
    if prior_ids is None or cand_ids is None:
        return MergePlan(
            False, overlap=ov, reason="unreadable_attachments",
            evidence="an attachment set could not be read on this pair — "
                     "nothing can be unioned, so nothing is merged")

    union = prior_ids | cand_ids
    added = cand_ids - prior_ids
    if not added:
        return MergePlan(
            False, overlap=ov, reason="nothing_to_union",
            evidence="the prior already carries every entity the candidate "
                     "names — this pair is NOOP's (lossless, no fusion needed)")

    return MergePlan(
        True,
        prior_id=str(prior.get("id") or ""),
        overlap=round(ov, 3),
        union_entities=tuple(sorted(union)),
        added_entities=tuple(sorted(added)),
        reason="near_identical",
        evidence=(f"near-identical (overlap {ov:.3f} >= {MERGE_MIN_OVERLAP}) "
                  f"with no distinguishing difference; attachments unioned "
                  f"({len(prior_ids)} -> {len(union)}, +"
                  f"{len(added)} from the candidate); the claim is the "
                  f"prior's, unchanged — never fused"),
    )


def apply_merge_union(proj: Any, record: dict, *, session_id: str,
                      sdk: Any = None) -> dict:
    """Execute one fusion against a live graph: union the attachments, keep the
    losing document's provenance. Returns an evidence dict; NEVER raises.

    ``record`` is one entry of ``execute_embed``'s ``result["merges"]``:
    ``{point_id (the SURVIVOR), about_entities (the union), ...}``.

    What is written:

    - ``(Session {session_id})-[:CONTAINS]->(Point {point_id})`` — the losing
      document is not dropped: its session now contains the claim it argued
      for. This is the exact channel the identical-fold path uses (#2104), so
      a merged claim and a folded claim have the same provenance shape.
    - one ``aboutObject`` edge per unioned entity name, resolved by name and
      routed through :func:`tortoise.session_link.link_entity` so it REPLAYS
      (`EntityLinked`) when the SDK carries a journal — the #2296 live-only
      hazard, and the reason `#3664` routes capture's edges the same way.

    **Never raises and never blocks capture** (§4.2's failure policy for the
    write path: a failed attachment is a warning, not a lost session). The
    returned dict is the merge's record of what actually landed, so a reader
    can tell a written union from an attempted one.
    """
    out: dict = {"point_id": "", "contained": 0, "linked": 0,
                 "unresolved": [], "warnings": []}
    point_id = str((record or {}).get("point_id") or "")
    out["point_id"] = point_id
    if not point_id:
        out["warnings"].append("merge record names no surviving point")
        return out
    try:
        # ⚠️ COUNT the edge, do not count the attempt. A bare `MERGE` after a
        # `MATCH` that resolves nothing is a silent no-op — returning 1 for it
        # would report a containment that does not exist, which is the
        # false-PASS shape this whole mechanism has to avoid. `RETURN count(*)`
        # is 1 only when both endpoints matched and the edge is there.
        rows = proj.g.query(
            "MATCH (s:Session {id:$sid}), (p:Point {id:$pid}) "
            "MERGE (s)-[:CONTAINS]->(p) RETURN count(*)",
            params={"sid": session_id, "pid": point_id}).result_set
        out["contained"] = int(rows[0][0]) if rows and rows[0] else 0
        if not out["contained"]:
            out["warnings"].append(
                f"merge survivor {point_id} or session {session_id} is "
                "absent — no provenance edge written")
    except Exception as e:  # noqa: BLE001, RUF100 — additive, never blocking
        out["warnings"].append(
            f"merge CONTAINS edge failed ({type(e).__name__}: {e})")

    # Lazy import: `session_link` pulls the projection package, and this module
    # is imported by `extractor_v2` (whose import cost sits on every capture).
    # A module-level import here would put that on every extractor import.
    try:
        from .session_link import link_entity
    except Exception as e:  # noqa: BLE001, RUF100
        out["warnings"].append(
            f"merge attachment skipped, link_entity unavailable "
            f"({type(e).__name__}: {e})")
        return out

    names = record.get("about_entities") or []
    if not isinstance(names, (list, tuple, set, frozenset)):
        out["warnings"].append("merge record's about_entities is unreadable")
        return out
    for name in names:
        if not isinstance(name, str) or not name.strip():
            continue
        _n = name.strip()
        try:
            rows = proj.g.query(
                "MATCH (o:Object {name:$n}) RETURN o.id",
                params={"n": _n}).result_set
        except Exception as e:  # noqa: BLE001, RUF100
            out["warnings"].append(
                f"merge attachment lookup failed for {_n!r} "
                f"({type(e).__name__}: {e})")
            continue
        idless = False
        for row in rows:
            oid = row[0] if row else None
            if isinstance(oid, str) and oid:
                try:
                    out["linked"] += link_entity(proj, "Point", point_id, oid,
                                                 sdk=sdk)
                except Exception as e:  # noqa: BLE001, RUF100
                    out["warnings"].append(
                        f"merge attachment failed for {_n!r} "
                        f"({type(e).__name__}: {e})")
            else:
                idless = True
        if idless:
            # An id-less name stub cannot be addressed by `link_entity` (it
            # MERGEs on {id:...} and would mint a DIFFERENT node). Same
            # fallback as capture's own attachment pass (#1727): keep the
            # edge, live-only, honestly unjournaled.
            try:
                proj.g.query(
                    "MATCH (p:Point {id:$pid}), (o:Object {name:$n}) "
                    "WHERE o.id IS NULL OR o.id = '' "
                    "MERGE (p)-[:aboutObject]->(o)",
                    params={"pid": point_id, "n": _n})
                out["linked"] += 1
            except Exception as e:  # noqa: BLE001, RUF100
                out["warnings"].append(
                    f"merge id-less attachment failed for {_n!r} "
                    f"({type(e).__name__}: {e})")
        elif not rows:
            out["unresolved"].append(_n)
    return out


__all__ = [
    "MERGE",
    "MERGE_MIN_OVERLAP",
    "MergePlan",
    "apply_merge_union",
    "near_dup_merge",
]

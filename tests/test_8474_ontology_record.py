"""#8474 — the ONTOLOGY §4.6 raw-availability record vs the code (doc↔code pin).

Split out of ``tests/test_3998_raw_absent_state.py`` so this half can be
registered in ``core`` + ``tier1`` (the ``test_5142_ontology_record.py`` /
``test_7818_ontology_record.py`` shape). ``docs/`` is in the selector's
``NON_PYTHON_PREFIXES``, so a ``docs/ONTOLOGY.md``-only diff selects no surface
and runs **tier1 only** — a pin that lives inside the docker-lane #3998 file
never runs on the exact change it exists to guard. This file reads one doc and
asserts text anchors — no fixture, no DB, no network.

The defect it closes was a *record* gap: §4.6 had no row for ``rawState`` /
``rawStateAt`` at all (measured on ``origin/main`` ``c377fea26``: ``rg -c
rawState docs/ONTOLOGY.md`` → 0), so the availability axis the code had carried
since #3998 was unrecorded and the next lane would re-derive it. This pin keeps
the record from being tidied away, and binds both directions: the row's claims
about the vocabulary must match the declared write surface, so neither half can
move alone.

Class-B test doctrine:
  (1) *What value makes this test fail?*  Deleting the row, or any of the named
      anchors from it — the recorded-act ruling, the expiry bound, the read-time
      precedence, the caller-supplied-and-never-persisted observation, the
      ``SourceVersioned`` version-transition half, or the fail-closed read of a
      stored condition.
  (2) *Does the test reach it?*  Yes — the §4.6 section text is read from the
      committed ONTOLOGY and each anchor is asserted, and the stored properties
      are checked against the real declared write surface.

WHAT IT DOES NOT PIN, deliberately: issue numbers, issue state, internal function
names or module constants. ``ONTOLOGY.md`` states the model we want; whether a
reader wires a seam yet is build history (``ONTOLOGY-CHANGELOG.md``), and a doc
pin that demands issue state forces the model into a status report — the defect
owner-ruled out on 2026-10-21 (#8745). The assertions below carry a NEGATIVE arm
so that state cannot creep back in.
"""
from __future__ import annotations

from pathlib import Path

from tortoise.raw_state import (
    RAW_DELETED,
    RAW_PRESENT,
    RAW_STATE_AT_PROP,
    RAW_STATE_PROP,
    WRITABLE_RAW_STATES,
)

REPO = Path(__file__).resolve().parent.parent
ONTOLOGY = REPO / "docs" / "ONTOLOGY.md"


def test_the_canonical_ontology_states_the_availability_axis() -> None:
    """#8474 — the §4.6 row the code has needed since #3998, pinned against
    drift so the canonical vocabulary cannot go silent about two stored
    properties.

    (1) FAILS if the row is removed, or if it stops naming the ruling that
        decides the behaviour (record the act / observe the condition), or if
        the read-time contract the code implements is not stated. It also fails
        if the code and the row disagree about what is stored.
    (2) REACHABLE: the doc is read from this repo, and the properties really
        are in the declared write surface — the row is a claim about a live
        write path, not about an aspiration.
    """
    doc = ONTOLOGY.read_text(encoding="utf-8")
    assert "### §4.6 Source" in doc, "the §4.6 Source section moved"
    section = doc[doc.index("### §4.6 Source"):]
    section = section[:section.index("\n### ", 1)]
    assert "`rawState` / `rawStateAt`" in section, (
        "§4.6 has no raw-availability row (#8474)"
    )
    for claim in ("deletion is an ACT", "observed live", "NEVER stored",
                  "deletion-marker TTL", "caller-supplied", "recorded act WINS"):
        assert claim in section, f"the §4.6 row does not state {claim!r}"
    # NEGATIVE arm: the model row states no issue state and names no internal
    # symbol. A lane reading it as specification must never take a build status
    # for a rule (owner ruling 2026-10-21, #8745).
    for banned in ("#8740", "#8739", "no reader supplies", "the tracked follow-up",
                   "the tracked residual", "RAW_DELETION_MARKER_TTL",
                   "resolve_raw_availability", "_fold_source_versioned"):
        assert banned not in section, (
            f"§4.6 carries {banned!r} — that is build state or an internal symbol, "
            "not a model rule; the build record belongs in ONTOLOGY-CHANGELOG.md"
        )
    # F5: the stamp is defined generally (every state-carrying write), not only
    # as the deletion marker's expiry input.
    assert "minted on **every** state-carrying write" in section, (
        "§4.6 must define `rawStateAt` generally — the writer stamps it on "
        "every state-carrying write, including `rawState='present'` (F5)"
    )
    # F6: the observation is caller-supplied at read time and never persisted —
    # the MODEL rule. NOT "no reader supplies one today" and NOT the
    # `resolve_raw_availability(…)` signature: both are build state.
    assert "never persisted" in section and "a read holding no observation" in section, (
        "§4.6 must state that an observation is caller-supplied at read time, is "
        "never persisted, and is absent from a read that supplies none (F6)"
    )
    # F7: a version transition carries the state on the `SourceVersioned` record.
    # The fold path (`_fold_source_versioned` → `_upsert_source`) is build record.
    assert "SourceVersioned" in section and "version transition" in section, (
        "§4.6's coverage clause must name the `SourceVersioned` record — a state "
        "change riding a version transition is journalled there (F7)"
    )
    # F10: the fail-closed claim is scoped — a condition found in stored data is
    # not part of the axis and reads `unrecognised`. (What the `:Source` read bag
    # does with a legacy stored condition is build state, tracked at #8739.)
    assert "fail-closed as" in section and "unrecognised" in section, (
        "§4.6 must state that a stored condition reads fail-closed as "
        "`unrecognised` (F10)"
    )
    # The row's claim is true of the code, and the vocabulary is the one the
    # row names — so neither half can move alone.
    from tortoise.projection.entities import _SOURCE_NODE_PROP_NAMES
    assert {RAW_STATE_PROP, RAW_STATE_AT_PROP} <= set(_SOURCE_NODE_PROP_NAMES)
    # …and the DOC half of the same binding, pinned VERBATIM rather than parsed
    # for backticked tokens: a token scan misses a value appended UNBACKTICKED
    # (§4.6's own `sourceKind` row writes its enumeration unbackticked), and a
    # shorter-substring pin survives an append entirely. A second claim is
    # refused too, so a value cannot be added in a new sentence beside the
    # clause this pin reads.
    assert (
        "`rawState` is **recordable** as `present` (the documented way back) or "
        "`deleted` — **a deletion is an ACT**"
    ) in section, (
        "§4.6's recordable clause must be exactly this text — the declared write "
        f"surface is {sorted(WRITABLE_RAW_STATES)}; `offline` / `access_revoked` "
        "are conditions observed live, never recorded"
    )
    # SCOPE, stated because the assertion above is a substring check: this pin
    # binds the clause text and the section's single `recordable` CLAIM. A
    # widening phrased elsewhere in the row, or in another section, without the
    # word `recordable` ("`offline` is also stored on the record.") is NOT
    # covered — the read predicate for that is the code half below, and the doc
    # is owner-gated (#8745), so a model widening arrives through the owner
    # rather than through a silent edit this pin would have to out-guess.
    assert section.lower().count("recordable") == 1, (
        "§4.6 must make exactly one recordable-vocabulary claim: a second one can "
        "widen the stored vocabulary without touching the clause pinned above"
    )
    assert WRITABLE_RAW_STATES == (RAW_PRESENT, RAW_DELETED), (
        "the declared write surface must remain exactly `present` / `deleted` — "
        "the code half of the binding the doc clause above pins from the other side"
    )

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
      anchors from it — the ruling, the expiry constant, the read-time
      precedence, the caller-supplied-observation disclosure (and its #8740
      follow-up), the ``SourceVersioned`` coverage half, or the #8739
      read-bag residual.
  (2) *Does the test reach it?*  Yes — the §4.6 section text is read from the
      committed ONTOLOGY and each anchor is asserted, and the stored properties
      are checked against the real declared write surface.
"""
from __future__ import annotations

from pathlib import Path

from tortoise.raw_state import RAW_STATE_AT_PROP, RAW_STATE_PROP

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
                  "RAW_DELETION_MARKER_TTL", "resolve_raw_availability",
                  "recorded act WINS"):
        assert claim in section, f"the §4.6 row does not state {claim!r}"
    # F5: the stamp is defined generally (every state-carrying write), not only
    # as the deletion marker's expiry input.
    assert "minted on **every** state-carrying write" in section, (
        "§4.6 must define `rawStateAt` generally — the writer stamps it on "
        "every state-carrying write, including `rawState='present'` (F5)"
    )
    # F6: the observation is caller-supplied and has NO shipped reader yet.
    assert "#8740" in section, (
        "§4.6 must name #8740 — the tracked follow-up for wiring the "
        "`observed=` seam into a shipped reader (F6)"
    )
    assert "no shipped reader supplies one today" in section, (
        "§4.6 must not imply a read derives the conditions: no shipped reader "
        "supplies an observation today (F6)"
    )
    # F7: a version transition's state rides `SourceVersioned`, folded back
    # through `_fold_source_versioned` -> `_upsert_source`.
    assert "SourceVersioned" in section and "_fold_source_versioned" in section, (
        "§4.6's coverage clause must name the `SourceVersioned` record and "
        "`_fold_source_versioned` — a state change riding a version transition "
        "is journalled there, not only on `SourceCreated` (F7)"
    )
    # F10: the fail-closed claim is scoped to the availability resolver, and the
    # `:Source` read-bag residual is named.
    assert "#8739" in section, (
        "§4.6 must name #8739 — the `:Source`-property read bag still serves a "
        "legacy stored `rawState` verbatim, so the fail-closed claim is not "
        "universal (F10)"
    )
    # The row's claim is true of the code, and the vocabulary is the one the
    # row names — so neither half can move alone.
    from tortoise.projection.entities import _SOURCE_NODE_PROP_NAMES
    assert {RAW_STATE_PROP, RAW_STATE_AT_PROP} <= set(_SOURCE_NODE_PROP_NAMES)

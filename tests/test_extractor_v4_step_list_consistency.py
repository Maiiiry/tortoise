"""#5092 — the v4 design must not place one extraction operation in two sites.

THE DEFECT THIS PINS
--------------------
`docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md` carried three passages that
placed the arbitrated near-duplicate MERGE at two different points, and its
canonical step list placed it nowhere:

  * §4.2's "resulting order" put a merge stage BEFORE the gate
    (`S2.2a-β NEAR-DUP  detect; MERGE only when nothing distinguishing is lost`);
  * §S2.2 said the opposite — *"Merging is S3's decision, made after the gate"*;
  * §4.2's constraint 2 forbids a non-exact stage before VET, while the table it
    sits above listed one there;
  * §4.1's canonical list (`S0 · S1 · S2.1 · S2.2 · S2.3 · S3 · S5 · S6`)
    contained no `S2.2a` at all, so the two lists disagreed on whether the stage
    exists.

That is load-bearing, not cosmetic: `#5006` is chartered to implement the merge,
and a later lane could cite §S2.2's plain text to revert the work — while an
implementer reading §4.2 would build a stage §S2.2 excludes.

THE RECONCILIATION (three operations, one site each)
----------------------------------------------------
`S2.2a-β` **detects** the pair and applies the §16.4 never-across guard;
`S2.2b VET` records the **verdict** `MERGE` (`tortoise/vet_gate.py:OUTCOMES` —
a flag, nothing more); `S3` executes the **fusion**. The token split is what the
shipped `vet_gate.py` already draws (`MERGE` is an admissible outcome;
`MERGE-INTO-EXISTING` is deliberately not emitted), and §S2.2 already reserved
the *action* to S3. So no new placement is created here — a pre-gate FUSION
claim is removed, because it was the only passage asserting one.

WHAT THESE GUARDS PIN, AND WHAT THEY DO NOT PROMISE
---------------------------------------------------
Pinned: (1) every step id §4.2's ordering table uses appears in §4.1's canonical
list — the drift that hid the stage; (2) the two blocks order their common ids
identically; (3) NO row before the gate row claims a fusion — the placement, in
both blocks; (4) §S2.2's passage separates the verdict token from the action
token.

**#5198 — the same defect class, in the PROSE the block read cannot reach.**
This file's own limit 1 below ("a STRUCTURAL read of two fenced blocks plus one
paragraph … cannot detect a contradiction stated in prose elsewhere") is not
hypothetical: it is `#5198`. Two recorded decisions were contradicted by prose
this guard could not see, and both are load-bearing the same way — a lane can
cite either and revert the decision:

  * §15's issue map read *"the mechanical DISCARD outcome on E7"* — the site
    `#4899`'s **DEC-1** (2026-09-25) reversed to **`S2.2b`**, and the only line
    in the document coupling the DISCARD with `E7`;
  * §4.2 still required *"Any reduction target must be paired with a measured
    retention floor"*, which §16.3's **O2** (owner, 2026-09-24) withdrew
    outright — *"no volume target, no recall floor, no node budget"*;
  * the review's **D6** was still listed as an open decision-grade question
    (`REVIEW-CONSOLIDATED-2026-09-23.md`) with no disposition.

So the class this file guards is wider than one MERGE: **the v4 design must not
assert two conflicting things about one operation or one recorded decision.**
Pinned additionally: (5) no passage sites the mechanical DISCARD on `E7` unless
it is the reversal record (naming `S2.2b` *and* negating `E7` in the same line);
(6) §15's issue-map row names `S2.2b` before any mention of `E7`; (7) the
retention-floor obligation appears only inside its own withdrawal, and the
recall-arithmetic paragraph carries O2's withdrawal verbatim; (8) the review's
`D6` carries a disposition naming the decided site and the shipped commit.

NOT promised:

1. This is a STRUCTURAL read of two fenced blocks plus one paragraph, **widened
   by `#5198` to four PROSE shapes** (see 5–8 above). What it still cannot do is
   find a contradiction stated in prose it was not told to look for — it pins
   the claims `#5092` and `#5198` name, not the whole document (nor
   `STORAGE-ARCHITECTURE.md` §8, which the issues track separately). Rules 5–7
   are token-level, so a restatement in different words is a NAMED gap, not a
   caught one: the exact obligation `"must be paired with a measured retention
   floor"` reds, a newly-worded equivalent an editor invents does not (rule 7's
   second half — the recall paragraph must carry O2's withdrawal verbatim — is
   the structural half that survives rewording).
2. Rule 3 is deliberately FAIL-CLOSED and slightly over-broad: a pre-gate row
   that merely *mentions* `MERGE` in a parenthetical reds too. That is the safe
   direction — the flag belongs to the gate's row — but a legitimate future
   edit that wants to name it there must move the mention to the gate row.
3. The id regex accepts the document's own spellings (`S2.2a-α`, `S2.2b`,
   `S2.3`). A THIRD spelling of the gate step (today: `S2.2` in §4.0 and the
   `### S2.2 — VET` heading, `S2.2b` in §4.1/§4.2/§4.3) is not caught — the
   blocks are the pinned artifacts.

Static, stdlib-only, no network / DB / graph.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_DOCS = Path(__file__).resolve().parent.parent / "docs" / "architecture"
DOC = _DOCS / "EXTRACTOR-V4-ARCHITECTURE.md"
#: `#5198`: the review whose D6 listed the site as an open decision-grade
#: question. It is a historical record — the disposition is added, never
#: rewritten, so a lane reading the table does not act on a settled question.
REVIEW = _DOCS / "REVIEW-CONSOLIDATED-2026-09-23.md"

CANONICAL_HEADING = "### 4.1 The step list"
ORDERING_HEADING = "#### The resulting order"

#: The step spellings this document uses: `S0`, `S0a`, `S2.1`, `S2.2a-α`,
#: `S2.2a-β`, `S2.2b`, `S3` … The `-α`/`-β` suffix is the dedup leg's, not a
#: sub-step of its own.
_STEP_ID = re.compile(r"^(S\d+(?:\.\d+)?[a-z]?(?:-[αβ])?)\s")

#: The gate row. Both blocks name it `S2.2b`; §4.0 and the `### S2.2 — VET`
#: heading call the family `S2.2`. Pinned here so a rename reds loudly.
GATE_ID = "S2.2b"


def _document() -> str:
    return DOC.read_text(encoding="utf-8")


def _fenced_rows(heading: str) -> list[tuple[str, str]]:
    """The (id, row) pairs of the first ```text block after `heading`."""
    text = _document()
    assert heading in text, f"heading not found in {DOC.name}: {heading!r}"
    start = text.index(heading)
    fence = text.index("```text", start)
    body = text[fence + len("```text") : text.index("```", fence + len("```text"))]
    rows: list[tuple[str, str]] = []
    for line in body.splitlines():
        match = _STEP_ID.match(line)
        if match:
            rows.append((match.group(1), line))
    return rows


@pytest.fixture(scope="module")
def canonical() -> list[tuple[str, str]]:
    return _fenced_rows(CANONICAL_HEADING)


@pytest.fixture(scope="module")
def ordering() -> list[tuple[str, str]]:
    return _fenced_rows(ORDERING_HEADING)


def test_both_blocks_were_actually_parsed(canonical, ordering):
    """A guard over an empty parse passes vacuously — the #2386 failure shape."""
    assert len(canonical) >= 8, canonical
    assert len(ordering) >= 8, ordering
    assert GATE_ID in [i for i, _ in canonical]
    assert GATE_ID in [i for i, _ in ordering]


def test_canonical_step_list_carries_every_id_the_ordering_table_uses(canonical, ordering):
    """§4.1 and §4.2 must agree that a stage EXISTS.

    The #5092 drift: §4.2 gained `S2.2a-α`/`S2.2a-β` and §4.1 never did — so the
    canonical list read as if the near-duplicate stage did not exist, which is
    exactly how "MERGE is authorised" became "MERGE has no settled site".
    """
    have = {i for i, _ in canonical}
    missing = [i for i, _ in ordering if i not in have]
    assert not missing, (
        "the §4.2 ordering table uses step id(s) the §4.1 canonical list never "
        f"declares: {missing}. A stage that exists in one list and not the other "
        "re-opens #5092 — add it to §4.1 (`tests/test_extractor_v4_step_list_"
        "consistency.py`)."
    )


def test_both_blocks_order_their_common_ids_identically(canonical, ordering):
    """Same set, same sequence — presence is not enough if the order moved."""
    common = {i for i, _ in ordering}
    assert [i for i, _ in ordering if i in common] == [
        i for i, _ in canonical if i in common
    ], "§4.1 and §4.2 disagree on the ORDER of the steps they share"


def test_no_stage_before_the_gate_claims_the_fusion(canonical, ordering):
    """The placement itself: nothing upstream of the gate fuses.

    `S3` is where the union executes (§S2.2 — *"merging is S3's decision, made
    after the gate"*), and `S2.2b VET` is the only place the `MERGE` verdict is
    recorded. A row above the gate row that names `MERGE` is asserting a second
    site for one of those operations, in whichever direction (#5092).
    """
    for label, rows in ((CANONICAL_HEADING, canonical), (ORDERING_HEADING, ordering)):
        ids = [i for i, _ in rows]
        gate = ids.index(GATE_ID)
        offenders = [row for _, row in rows[:gate] if "MERGE" in row]
        assert not offenders, (
            f"{label}: a step BEFORE the {GATE_ID} gate row names `MERGE`: "
            f"{offenders}. Detection belongs at `S2.2a-β`, the VERDICT at the "
            "gate row, the FUSION at S3 — one site each (§S2.2, #5092)."
        )


def test_the_gate_passage_separates_the_verdict_from_the_action():
    """§S2.2 must keep BOTH tokens and the prohibition.

    The issue's second reading was enabled by one word doing two jobs: `MERGE`
    the gate VERDICT (admissible — `vet_gate.py:OUTCOMES`) and
    `MERGE-INTO-EXISTING` the ACTION (deliberately not emitted). A passage that
    keeps only one of them re-creates the conflation.
    """
    text = _document()
    marker = "**`MERGE-INTO-EXISTING` is deliberately NOT a VET output"
    assert marker in text, (
        "§S2.2's verdict/action passage is gone — the gate's admissible action "
        "set is no longer stated (#5092)"
    )
    passage = text[text.index(marker) :][:1600].lower()
    assert "merge-into-existing" in passage and "`merge`" in passage, passage
    assert "verdict" in passage and "action" in passage, passage
    assert "vet_gate.py" in passage, (
        "the passage must cite the shipped vocabulary (`tortoise/vet_gate.py`) "
        "it is reconciled against — otherwise it reads as a proposal, not as "
        "the code's own behaviour (#5092)"
    )


# ══════════════════════════════════════════════════════════════════════════════
# #5198 — the SITE decision, and the WITHDRAWN objective (prose, not blocks)
# ══════════════════════════════════════════════════════════════════════════════
#
# #5092 (above) pinned one operation's site INSIDE the two fenced blocks. #5198
# is the same class surviving in the prose a block read cannot reach — the
# document's own limit 1 above. Two recorded decisions were still contradicted
# by text a lane reads:
#
#   * `#4899`'s **DEC-1** (2026-09-25) moved the mechanical DISCARD to `S2.2b`
#     and reversed the issue's own `E7` title. §15's issue map kept saying
#     `E7` — the LAST line in the document coupling the two tokens, and the
#     only prose a citing lane would find there.
#   * §16.3's **O2** (owner, 2026-09-24) withdrew the volume framing outright.
#     §4.2 kept *requiring* a retention floor, and §16.3's own cell claimed
#     "§4.2's objective is re-stated accordingly" — it was not.
#
# Why this is load-bearing rather than cosmetic: both surviving sentences are
# quotable as authority. A lane holding §15 reverts DEC-1; a lane holding §4.2
# re-imposes a metric the owner rejected. Neither change would say so.

#: The decided site (`#4899` DEC-1; shipped in PR `#5237`). The predicate is
#: `tortoise/value_gate.py:370` (`mechanical_verdict`), called from
#: `tortoise/vet_gate.py:466` — Level-2 of `vet_candidates`, before the arbiter
#: and before S3 RESOLVE (`tortoise/extractor_v2.py:7343`).
DISCARD_SITE = "S2.2b"

#: The site DEC-1 reversed. The TOKEN is still legitimate — `E7`
#: (`classify_consolidation`, `tortoise/extractor_v2.py`) remains the home of
#: *against-priors consolidation* after S3 (§4.2 constraint 5). Only the
#: mechanical DISCARD must never be sited there.
REJECTED_SITE = "E7"

#: §16.3 O2's withdrawal, verbatim. Its presence is what makes the §4.2
#: sentence a contradiction rather than the document's settled position.
O2_WITHDRAWAL = (
    "No volume target, no recall floor, no node budget anywhere in this document."
)

#: The withdrawn OBLIGATION (not the words around it), so the same requirement
#: re-worded in the same shape still reds. It may appear in the document only
#: as a quoted, withdrawn record — see `_WITHDRAWAL_MARK`.
_FLOOR_OBLIGATION = re.compile(
    r"\b(?:must|shall|has to|needs to)\s+be\s+paired\s+with\s+a\s+measured\s+"
    r"retention\s+floor",
    re.IGNORECASE,
)

#: How a withdrawal is marked at the point of quotation. A line carrying the
#: obligation AND one of these is the reversal record, not the requirement.
_WITHDRAWAL_MARK = re.compile(r"withdrawn|superseded|no longer", re.IGNORECASE)

#: `DISCARD` and `E7` on one line is only legitimate as the reversal record:
#: it must also name the decided site and negate the rejected one. That is the
#: form `#5198` left in §15 (*"THE SITE IS `S2.2b`, NOT `E7`"*).
_NEGATES_REJECTED = re.compile(r"\bnot\b[^.\n]{0,12}`?E7", re.IGNORECASE)


def _couples_discard_and_e7(line: str) -> bool:
    return bool(re.search(r"discard", line, re.IGNORECASE)) and REJECTED_SITE in line


def test_no_passage_sites_the_mechanical_discard_on_e7():
    """One site per operation — in PROSE too (the #5092 class, widened).

    The scan is over the whole document, not the two blocks: §15's issue map
    was where the losing site survived, and a block read cannot see it.
    """
    offenders = []
    for number, line in enumerate(_document().splitlines(), 1):
        if not _couples_discard_and_e7(line):
            continue
        if DISCARD_SITE in line and _NEGATES_REJECTED.search(line):
            continue  # the reversal record: it names the rejected site on purpose
        offenders.append(f"{DOC.name}:{number}: {line.strip()[:200]}")
    assert not offenders, (
        "a passage couples the mechanical DISCARD with E7 without being the "
        "reversal record (it must name `S2.2b` AND negate `E7` on the same "
        "line). `#4899`'s DEC-1 moved the gate to S2.2b; E7 stays the home of "
        "against-priors consolidation only (§4.2 constraint 5). A lane citing "
        "this line reverts the decision (#5198):\n" + "\n".join(offenders)
    )


def test_the_issue_map_row_names_the_decided_site():
    """§15 is the index a lane holding an issue reads — it must state the site.

    It said *"the mechanical DISCARD outcome on E7"* while §4.1/§4.2/§4.3 and
    the shipped code all said `S2.2b`, so the document asserted both again
    (#5198) — exactly what #5092 eliminated inside the blocks.
    """
    rows = [
        line
        for line in _document().splitlines()
        if "the mechanism (`^the <X>`" in line
    ]
    assert len(rows) == 1, (
        "§15's issue-map row for §1 is not uniquely identifiable — the guard "
        "cannot pin a row it cannot find (#5198)"
    )
    row = rows[0]
    assert DISCARD_SITE in row, (
        "§15's issue-map row does not name the decided gate site "
        f"({DISCARD_SITE!r}). It is the index a lane holding `#4899` reads "
        "(#5198):\n" + row
    )
    assert row.index(DISCARD_SITE) < row.index(REJECTED_SITE), (
        "§15's issue-map row names E7 BEFORE the decided site — so the row "
        "still asserts the losing site (`#4899` DEC-1). Order it the other way "
        "or drop the E7 mention (#5198):\n" + row
    )


def test_the_withdrawn_floor_requirement_is_never_reasserted():
    """§16.3 O2 withdrew the floor framing; §4.2 must not keep requiring one.

    The obligation is ALLOWED only inside its own withdrawal — the document
    records the reversal by quoting what it no longer says. Anywhere else it
    is the contradiction `#5198` is filed for.
    """
    text = _document()
    assert O2_WITHDRAWAL in text, (
        "§16.3 O2's withdrawal sentence is gone. It is the authority the "
        "recall-arithmetic paragraph is reconciled against — without it, a "
        "retention floor reads as the documented rule again (#5198)"
    )
    offenders = [
        f"{DOC.name}:{number}: {line.strip()[:200]}"
        for number, line in enumerate(text.splitlines(), 1)
        if _FLOOR_OBLIGATION.search(line) and not _WITHDRAWAL_MARK.search(line)
    ]
    assert not offenders, (
        "a line requires a measured retention floor without recording that the "
        "requirement is withdrawn — O2 (owner, 2026-09-24) withdrew it "
        "outright. Quote it as withdrawn, or drop it (#5198):\n"
        + "\n".join(offenders)
    )


def test_the_recall_arithmetic_paragraph_carries_o2s_withdrawal():
    """The reconciliation SITE, not just the absence of the old phrasing.

    Rule 3 above would pass on a document that deleted the sentence and said
    nothing — leaving the arithmetic looking like a floor nobody has stated.
    The paragraph that makes the multiplicative-recall claim must carry O2's
    withdrawal verbatim, so the next reader is told which governs (#5198).
    """
    text = _document()
    marker = "recall compounds MULTIPLICATIVELY"
    assert marker in text, (
        "§4.2's multiplicative-recall paragraph is gone. It is a fact about "
        "composing stages and it is NOT a floor this pipeline owes anyone — if "
        "it is removed, remove it with the O2 note, not silently (#5198)"
    )
    # The paragraph, read by its blank-line boundaries rather than a fixed
    # character window — a window silently truncates the sentence it is asked
    # to find, which is the guard failing rather than the document (#5198).
    index = text.index(marker)
    paragraph = text[text.rfind("\n\n", 0, index) + 2 : text.find("\n\n", index)]
    assert O2_WITHDRAWAL in paragraph, (
        "the recall-arithmetic paragraph does not carry O2's withdrawal. §16.3 "
        "O2 (owner, 2026-09-24) rejected the volume framing outright, so the "
        "paragraph must say so where the arithmetic is read — otherwise the "
        "next lane cites it to demand a retention floor (#5198):\n"
        + paragraph[:600]
    )
    assert _WITHDRAWAL_MARK.search(paragraph), (
        "the paragraph does not mark the old requirement as withdrawn — it "
        "reads as a live obligation again (#5198)"
    )


def test_the_reviews_d6_question_carries_a_disposition():
    """D6 asked where the gate lives; the answer is recorded, not pending.

    `REVIEW-CONSOLIDATED-2026-09-23.md` is a historical review, so the row is
    NOT rewritten — but a lane reading its open-question table must not act on
    a question the record has closed (#5198).
    """
    text = REVIEW.read_text(encoding="utf-8")
    assert "**D6**" in text, "the review's D6 row is gone (#5198)"
    row_window = text[text.index("| **D6**") :][:400]
    assert "ANSWERED" in row_window, (
        "the D6 row does not point at its disposition — a lane reading only the "
        "table takes the site as still open (#5198):\n" + row_window[:200]
    )
    assert "D6 — ANSWERED" in text, (
        "the D6 disposition block is gone. It must state the decided site and "
        "cite the shipped commit, so the disposition is verifiable rather than "
        "claimed — the failure §E of that review warns about (#5198)"
    )
    disposition = text[text.index("D6 — ANSWERED") :][:3000]
    assert DISCARD_SITE in disposition, (
        "the D6 disposition does not name the decided site (#5198)"
    )
    assert "ea70025aa" in disposition, (
        "the D6 disposition does not cite the shipped commit (`#5237`, "
        "`ea70025aa`) — an unverified disposition is the class that review's "
        "§E was written about (#5198)"
    )

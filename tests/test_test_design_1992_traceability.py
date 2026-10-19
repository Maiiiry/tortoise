"""#1992 — the Test Design surface map is checked against itself, not by eye.

WHAT THIS PINS
--------------
`docs/epics/2026-08-29-agent-driven-onboarding-1976/04-test-design.md` is the
canonical surface map every child issue's verification checklist is cut from,
so a row and the prose beside it giving two different answers for one
capability is a defect that ships. Three review cycles on PR #9013 found the
SAME class of bug three times (the table describing the document in its own
words instead of quoting it, and a row disagreeing with its own rationale), so
the invariants that were being checked by hand are checked here instead:

1. **The `Verified at` column is a COPY, not a source.** Every quoted segment is
   character-identical to the cited surface's own `Test Layer` cell.
2. **Both directions hold.** Every in-scope capability has a surface, and every
   surface is reachable from one.
3. **Capability names are the scope's** — one row per `03-scope.md` §2 row, same
   names, same order (W10 is out of scope and must stay absent).
4. **A row cites every surface its own rationale names.** The pre-fix defect:
   the `W5 setup card` row said `9, 17` while the bullet three lines below
   placed the card's counted checklist on surface 10 (`05-ux-decisions.md`
   "Layout: Setup guide"). Row ↔ prose is the reconciliation a reader does, and
   it produced two answers. The rationale must cite in the explicit
   **`surface N`** form: the first version of this check read *any* standalone
   1-17 integer, so `05-ux-decisions.md` #5 in a bullet was read as surface 5
   and a future "step 3"/"§5"/"3 of the 5 doors" would fail with a misleading
   message (and a number >17 would trip the surfaces sanity assert).
5. **An E2E row cites every surface that owns a door it exercises.** The
   pre-fix defect: `E2E-6 graph-held resumption` was `1, 2, 10` while the scope
   defines that re-entry as "the Settings Setup guide **or the Continue-setup
   card**" — the card being surface 9's next-action element, which the same
   document had just written into surface 9's contract. The door set is
   ANCHORED in `03-scope.md` §4 E2E-6 (the authority, which the document under
   test cannot reword) and only *extended* from the map's own Contract column;
   the first version derived it from the contracts alone and asserted only
   surface 10, so a legal reword of surface 9's contract plus a reverted E2E-6
   row passed it — demonstrated in
   `test_the_e2e6_door_guard_rejects_a_reworded_contract_plus_a_reverted_row`.
6. **The counted/dismissible checklist belongs to surface 10, not surface 9.**
   The pre-fix defect: surface 9's contract claimed "a dismissible checklist
   rendered from `OnboardingState`.completed_steps" — but the shipped Overview
   next-action element is ONE calm next step plus a CTA into the Settings Setup
   guide (`website/apps/dashboard/src/main.jsx`, `OverviewNextActionCard`), the
   counted N-of-M checklist is `SetupGuideCard` on the Settings tab, and
   neither card carries a dismissal (the only `dismiss` hits are the recovery,
   claim and billing banners). Surfaces 9/10 shipped under #2000/#2001, both
   CLOSED, so a map that contradicts the shipped dashboard is silent drift.
   `05-ux-decisions.md` row 5 — the row the map cites as its authority — carried
   the same superseded claim, so it is pinned here too.
7. **No orphan `)` after a code span** — the review reported
   `rendered from `OnboardingState`).completed_steps`; the reviewed head had
   already dropped the stray paren, so this is a standing GUARD with the
   detector's non-blindness asserted rather than a pre-fix defect pin.
8. **The checklist child issues consume carries the obligation the surface table
   states** — a projection obligation that lives only in the table is one a card
   implementation can never fail.
9. **The surface-table parser rejects a second inflated row instead of shifting
   columns.** Exactly one cell in the document is baselined as inflated (surface
   4's unescaped pipe, MD056); the table parser asserts that shape, so a future
   unescaped pipe in Contract/Failure Modes fails loudly instead of moving those
   cells into the wrong fields.
10. **Every dismissal claim in the row the map cites names its DOOR.** The
   reviewed head's row 5 said the Settings card "collapses to compact status /
   dismissible-hide with re-open path when complete" AND "neither card carries a
   dismissal" — one claim, two opposite answers, in one sentence. The previous
   guard asserted the absence of one literal phrase, so it passed a row that
   stated both. The guard now requires the corrected claim to still be PRESENT and
   rejects the enumerated denial shapes below. Its contract is stated exactly, because
   eight review rounds measured that a stronger one cannot be delivered:
     MUST   both claims are present VERBATIM (a reworded or dropped claim fails);
     MUST   no enumerated denial shape is present (see `_DENIED_SHAPES` /
            `_OVERVIEW_SHAPES`), including in a comma clause that does not repeat the
            door name and across a comma in either door order;
     LIMIT  a denial that REUSES a required claim's exact wording with a pronoun — "…
            dismissible-hide with a re-open path when complete, but it carries no
            dismissal" — is NOT caught. Resolving "it" to the Settings door is
            anaphora, not a pattern; and the wording has to be masked out of the
            residual so that the door-agnostic `no … dismiss` shape can exist at all.
            `_STATED_LIMIT_ROWS` pins this in an executable test so no reader can
            mistake the guard for a truth oracle;
     LIMIT  `non-dismissible` / `undismissable` on its own, and any unenumerated
            phrasing. This is a regression guard for one documentation row — it
            proves the corrected claim is still there and the measured contradictions
            are gone, NOT that arbitrary English is classified.
   The Overview door is matched under
   both the prose word `Overview` and the component name its own docs use,
   `OverviewNextActionCard` — a row naming the door only by component was previously
   read as claiming nothing about it. The Settings door does not need to be named: the
   denial shapes key on the dismissal vocabulary, and the Settings claim is required
   as a verbatim sentence.
   This is NOT a natural-language polarity classifier, and six review rounds of one
   are why. Two versions were written and each was wrong in the direction that
   matters, measured on real sentences: a clause-wide negation scan read "never a
   counted checklist … and it is dismissible once complete" as a DENIAL, so a row
   AFFIRMING an Overview dismissal passed the guard whose whole purpose is that
   claim; and the broad "any `dismiss*` token affirms" rule — added so that correct
   prose was not rejected — read every unenumerated denial ("… whose dismissal is
   disabled", "has neither a dismiss button …") as an affirmation, and it made the
   fail-closed branch unreachable, because every clause containing "dismiss"
   matched it. Distance-bounding the negation scan did not help: it only required
   the unrelated negation to be NEARER. There is no bounded keyword rule that
   classifies arbitrary English.
   So the guard is DECIDABLE: it requires the two claims the row must carry (the
   Overview's "no dismissal", the Settings door's recorded "dismissible-hide with a
   re-open path") and a denylist of the shapes that contradict them — the
   superseded wording, a dismissal asserted for the Overview, a denial of the
   Settings record in any of its measured forms, and a door-less denial. Widening
   that vocabulary is a deliberate edit here, never a silent pass; the limits of the
   denylist are the LIMIT items above. Every enumerated shape is a `must_fail` row in
   `test_the_dismissal_door_guard_rejects_the_one_sentence_contradiction`, and a row
   that keeps the claim but states a dismissal in a form the denylist does not list is
   a disclosed LIMIT, not a silent pass that is claimed as a catch.
11. **The superseded mirror claim is reconciled where it survived, not only
   where it was first noticed.** `03-scope.md` §4 E2E-6 — the AUTHORITY this
   module's door pin anchors to — still read "mirrored as a dismissible
   checklist card", as did the same claim in §2's W5 bullet, in
   `00-align-and-scope.md` (both places) and in `05-ux-decisions.md`'s `Visual
   affordances` row. Live text states the corrected reading; wording kept as
   history carries a supersession note beside it.
12. **The PLAN is a third copy of the E2E → surface map, and it is checked.**
   `06-plan.md`'s `### DE2E-6 … [1,2,10]` heading still gave the pre-fix answer
   after the map, the capstone and the scope were corrected — and no guard read
   the plan, so the drift was invisible. Two copies drift; a third drifts
   unguarded. Every `DE2E-N` heading must cite the surface set the map's `E2E-N`
   row cites.
13. **…and of the door ROLES, so the plan's `Assert` line is pinned too.** The
   plan's DE2E-6 `Assert` is the spec a child issue implements. It named both
   doors' claims in one `and`-joined clause, which reads as attaching the
   Overview's role (ONE next step + a CTA) to the Settings guide; the authority
   assigns them the other way round (the guide IS the counted checklist). Roles
   are asserted per door, and the section's dismissal claims must reconcile.

Every surface is also exercised by at least one high-level E2E row (the E2E-side
reverse direction) — a surface no scenario touches has no scenario that would
catch its regression. The whole-journey catch-all row (`E2E-12` = `1-17 (all)`)
is EXCLUDED from that union: including it makes the union equal the surface set
for every state of the document, which is the vacuity the check replaced. The one
surface exercised only there (14, W11 telemetry — asserted by E2E-12's dedupe
clause and by no specific scenario) is baselined by name, so a second one fails.

NON-VACUITY
-----------
Checks 4, 5, 6, 8, 10, 11, 12 and 13 fail on the reviewed head's document/text
(each docstring names the pre-fix text; check 10 additionally replays the exact
self-contradicting sentence AND a battery of measured counterexamples through the
guard in-suite — an unrelated nearby negation, an idiomatic denial, a door named
by its component, a component named in the other door's clause — and the
E2E-coverage check drives a mutated row set to prove it fails). Checks 1-3 are the
standing invariants the earlier cycles established, check 9 is a parser-shape
guard and check 7 is a guard whose detector is proved non-blind — guard rails,
not defect pins. The pre-fix blobs are replayed by pointing this module's pure
functions at them (`git show <sha>:<path>`), which is how the non-vacuity claims
were measured; reverting the four docs to `fe06e3a98` fails checks 10 and 11
(five tests) and nothing else.
Static: doc-text scans plus one file read per doc. No network, DB or graph.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EPIC = ROOT / "docs" / "epics" / "2026-08-29-agent-driven-onboarding-1976"
DOC = EPIC / "04-test-design.md"
SCOPE = EPIC / "03-scope.md"
UX = EPIC / "05-ux-decisions.md"
PLAN = EPIC / "06-plan.md"

# `03-scope.md` §4 E2E-6 is the AUTHORITY for which re-entry doors exist:
# "**When:** they re-enter via the Settings Setup guide or the Continue-setup
# card". Binding a door name to the surface that owns it is the one thing this
# guard states for itself — the surfaces themselves come from the scope, never
# from the document under test. Deriving the set from the map's own contracts
# alone is what made the first version of this check FAIL-OPEN: a legal reword
# of surface 9's contract emptied the door set and `E2E-6 | 1, 2, 10` passed
# (only surface 10 was asserted).
SCOPE_E2E6_DOOR_SURFACES = {
    "the Settings Setup guide": 10,
    "the Continue-setup card": 9,
}

# ` · ` separates the per-surface segments of a multi-surface `Verified at` cell.
_SEGMENT = " · "


def _cells(line: str) -> list[str]:
    """Split a markdown table row into stripped cells on UNESCAPED pipes.

    Two pre-existing shapes in this document need the care: escaped pipes
    inside a cell (surface 1's `'failed'\\|'dismissed'\\|null` enum) and the
    one deliberately unescaped pipe in surface 4 (`fork: 'self'|'build'` — the
    file's baselined MD056). The escaped form must not split a cell; the
    unescaped one does split and is re-joined by `_surfaces` so the row still
    parses.
    """
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", body)]


@lru_cache(maxsize=1)
def _doc_lines() -> tuple[str, ...]:
    return tuple(DOC.read_text(encoding="utf-8").splitlines())


@lru_cache(maxsize=1)
def _surfaces() -> dict[int, dict[str, str]]:
    """#1..17 surface table row → its named columns. The table is the first one
    in the document (`| # | Surface | Type | Data Flow | Test Layer | Contract |
    Key Failure Modes |`)."""
    out: dict[int, dict[str, str]] = {}
    for line in _doc_lines():
        m = re.match(r"^\|\s*(\d{1,2})\s*\|", line)
        if not m:
            continue
        cells = _cells(line)
        if len(cells) != 7:
            # Exactly ONE cell in this whole document is baselined as inflated:
            # the unescaped pipe in surface 4's NAME (`fork: 'self'|'build'`,
            # the file's MD056). Both halves of that shape are asserted — one
            # extra cell, and the split landing inside the Surface column — so
            # a future unescaped pipe in Contract / Failure Modes / Test Layer
            # fails HERE instead of silently shifting those cells into the
            # wrong fields and pinning a contract that is really half a name.
            assert len(cells) == 8, (
                f"a surface row has {len(cells)} cells, expected 7: at most one "
                "unescaped pipe is baselined (surface 4's `fork: 'self'|'build'`); "
                f"a second one SHIFTS the columns — {line[:90]}")
            # Locate the Data Flow column by its closed vocabulary and rebuild
            # the row around it rather than assuming a fixed index.
            flow = next(
                (i for i, c in enumerate(cells)
                 if i >= 2 and c in {"Both", "Write", "Out"}),
                None,
            )
            assert flow == 4, (
                "the extra cell is not inside the Surface name (Data Flow found "
                f"at index {flow}) — columns are shifted, not inflated: {line[:90]}")
            cells = [
                cells[0],                       # #
                "|".join(cells[1 : flow - 1]),  # Surface (re-joined)
                cells[flow - 1],                # Type
                cells[flow],                    # Data Flow
                cells[flow + 1],                # Test Layer
                "|".join(cells[flow + 2 : -1]),  # Contract (re-joined)
                cells[-1],                      # Key Failure Modes
            ]
            assert cells[5] and cells[6], (
                f"the rebuilt row lost its Contract or Failure Modes cell: {line[:90]}")
        if len(cells) != 7:  # not the surface table
            continue
        out[int(m.group(1))] = {
            "name": cells[1],
            "layer": cells[4],
            "contract": cells[5],
            "failure_modes": cells[6],
        }
    return out


@lru_cache(maxsize=1)
def _capabilities() -> dict[str, tuple[set[int], str]]:
    """Capability → (declared surfaces, raw `Verified at` cell)."""
    out: dict[str, tuple[set[int], str]] = {}
    for line in _doc_lines():
        m = re.match(r"^\|\s*(W\d+ [^|]+?)\s*\|\s*([\d,\s]+?)\s*\|", line)
        if not m:
            continue
        cells = _cells(line)
        if len(cells) != 3:
            continue
        out[m.group(1)] = (
            {int(n) for n in re.findall(r"\d+", cells[1])},
            cells[2],
        )
    return out


@lru_cache(maxsize=1)
def _e2e_rows() -> dict[str, set[int]]:
    """`E2E-N` → the surfaces its row cites (ranges like `1-17` are expanded)."""
    out: dict[str, set[int]] = {}
    for line in _doc_lines():
        m = re.match(r"^\|\s*(E2E-\d+)\s*[^|]*\|", line)
        if not m:
            continue
        cells = _cells(line)
        if len(cells) != 2:
            continue
        numbers: set[int] = set()
        for chunk in re.findall(r"\d+\s*-\s*\d+|\d+", cells[1]):
            if "-" in chunk:
                lo, hi = (int(p) for p in chunk.split("-"))
                numbers.update(range(lo, hi + 1))
            else:
                numbers.add(int(chunk))
        out[m.group(1)] = numbers
    return out


def _section_text(prefix: str, *, stop_at_blank_after: bool = True) -> str:
    """The lines of one `- **<prefix>**` bullet in the rationale list."""
    lines = list(_doc_lines())
    start = next(
        (i for i, ln in enumerate(lines) if ln.startswith(f"- **{prefix}**")), None
    )
    if start is None:
        raise AssertionError(f"no `- **{prefix}**` bullet in {DOC.name}")
    out = [lines[start]]
    for ln in lines[start + 1 :]:
        if ln.startswith("- **") or (stop_at_blank_after and not ln.strip()):
            break
        out.append(ln)
    return "\n".join(out)


def _standalone_numbers(text: str) -> set[int]:
    """DEPRECATED — kept only so the guard cannot silently resurrect it.

    Reading every small standalone integer as a surface citation misfires on
    ordinary prose: at `1758bbe59` the W5 bullet cited `05-ux-decisions.md` #5
    and this function reported surface 5, and a bullet reading "step 3", "§5"
    or "3 of the 5 doors" fails with a misleading message (and a number >17
    trips the surfaces sanity assert). Use `_cited_surfaces`.
    """
    raise AssertionError(
        "`_standalone_numbers` must not be used: it reads prose integers as "
        "surface citations — use `_cited_surfaces` (explicit `surface N`)")


#: The citation form the rationale bullets must use: `surface 9`,
#: `surfaces 1, 3 and 10`. Deliberately NOT any bare small integer.
_SURFACE_CITATION = re.compile(
    r"\bsurfaces?\s+(\d{1,2}(?:\s*(?:,|\band\b|/)\s*\d{1,2})*)",
    re.IGNORECASE,
)


def _cited_surfaces(text: str) -> set[int]:
    """Surface numbers cited in the explicit `surface N` / `surfaces N, M` form.

    A section reference (`§5`), a numbered step ("step 3") or an arithmetic
    aside ("3 of the 5 doors") is not a surface citation; only the explicit form
    is. The rationale bullets in the document are written in that form for this
    reason, and the doc says so next to them.
    """
    out: set[int] = set()
    for match in _SURFACE_CITATION.finditer(text):
        out.update(int(n) for n in re.findall(r"\d{1,2}", match.group(1)))
    return out


def _scope_section(heading: str) -> str:
    """The body of one `### <heading>` section of `03-scope.md`, up to the next
    `### ` — the authority the map cannot reword."""
    text = SCOPE.read_text(encoding="utf-8")
    start = text.index(heading)
    end = text.find("\n### ", start + len(heading))
    return text[start:] if end == -1 else text[start:end]


def _missing_e2e6_doors(
    cited: set[int], *, scope_text: str, contract_doors: set[int]
) -> set[int]:
    """Surfaces E2E-6 must cite but does not.

    Two sources, and the first is the one that cannot be reworded by the
    document under test: the doors `03-scope.md` §4 E2E-6 names (bound to their
    surfaces in `SCOPE_E2E6_DOOR_SURFACES`), plus — supplementary — every
    surface whose own Contract column names a door today, so a door that MOVES
    surfaces is followed instead of hard-coded.
    """
    required = {
        surface
        for door, surface in SCOPE_E2E6_DOOR_SURFACES.items()
        if door in scope_text
    }
    return (required | contract_doors) - cited


# ── dismissal claims: the row must still CARRY the corrected claim ──────
# The row `05-ux-decisions.md` "Affordance: setup card" is CITED by the map as the
# authority for the corrected claim, so it must still state that claim and must not
# state its negation.
#
# ⛔ This is NOT a natural-language polarity classifier, and six review rounds are
# why. Four versions of one were written; each was measured wrong in BOTH
# directions, and each fix opened the side the previous one had closed:
#   * a clause-wide negation scan read "never a counted checklist … and it is
#     dismissible once complete" as a DENIAL, so a row AFFIRMING an Overview
#     dismissal passed the guard whose whole purpose is that claim;
#   * distance-bounding that scan only required the unrelated negation to be
#     NEARER ("has no toggle and is dismissible" still passed);
#   * a broad "any `dismiss*` token affirms" rule (added so correct prose was not
#     rejected) read every unenumerated denial as an affirmation and made the
#     fail-closed branch unreachable;
#   * requiring the recorded-decision copula made PRE-phrase negations fall
#     through, but a post-phrase negation ("… is a dismissible-hide that never
#     shipped") still matched, and a retirement-window rule then rejected correct
#     prose that mentioned "later"/"no data loss" after the claim.
# There is no bounded keyword rule that classifies arbitrary English, and the next
# wording was always one sentence away.
#
# So the guard is DECIDABLE instead: it requires the two claims VERBATIM. A denial
# cannot contain the sentence — "does not have a dismissible-hide with a re-open
# path" does not contain "dismissible-hide with a re-open path" — so a reworded
# denial REMOVES the claim and fails, whatever its grammar. `_DENIED_SHAPES` is
# defence in depth for a row that keeps the claim and contradicts it elsewhere.
#
# The contract, stated so it cannot be over-claimed: a row must (a) still contain
# both claims verbatim, and (b) contain none of the enumerated contradicting
# shapes. Rewording a claim is a FAILURE that names what must be restored; adding a
# newly-invented contradicting shape to `_DENIED_SHAPES` is a deliberate edit.

#: The two claims the cited row must still carry — matched verbatim (whitespace
#: normalised, case-insensitive), because a fragment is satisfiable by a denial.
_REQUIRED_CLAIMS = (
    ("the Settings door's recorded decision", "dismissible-hide with a re-open path"),
    ("the Overview door's answer", "carries no dismissal"),
)

#: Shapes that contradict the corrected claim. They are matched over the WHOLE row,
#: because a denial contradicts the claim it qualifies wherever it sits — including in
#: the comma clause AFTER the door name ("… dismissible-hide with a re-open path when
#: complete, and it has no dismissal").
#:
#: The ONE sentence that is legitimately negative — the Overview door's own answer,
#: `carries no dismissal` — is removed from the text before these run (see
#: `_dismissal_claim_conflicts`). That removal is what lets a door-agnostic `no …
#: dismiss` rule exist at all: without it, the required Overview claim matched it and
#: the live row was rejected. An earlier version instead scoped the door shapes to
#: comma SEGMENTS, which let a denial in the FOLLOWING clause escape (each such shape
#: requires the door name) — a measured fail-open on four rows, so segmentation was
#: removed rather than layered over.
_DENIED_SHAPES = (
    (re.compile(r"dismissible\s+checklist", re.I),
     "the superseded wording — the Overview next action is not a dismissible "
     "checklist"),
    (re.compile(r"\b(?:neither|no)\s+(?:of\s+the\s+)?(?:both\s+)?(?:cards?|doors?)\b"
                r"[^.;]{0,80}?dismiss", re.I),
     "a door-LESS denial (\"neither card …\" / \"no door …\") contradicts the "
     "Settings door's dismissible-hide"),
    (re.compile(r"\b(?:does\s+not|doesn't|do\s+not|never|no\s+longer|not|without)\b"
                r"[^.;]{0,40}?dismissible[\s-]*hide", re.I),
     "the Settings door's recorded dismissible-hide is DENIED before it is named "
     "(\"does not have a dismissible-hide\", \"never had a dismissible-hide\")"),
    (re.compile(r"dismiss\w*\b[^.;]{0,80}?\b(?:not\s+implemented|never\s+shipped"
                r"|not\s+what\s+shipped|rejected|reversed|superseded|withdrawn"
                r"|disabled|removed|dropped|absent|does\s+not\s+exist)", re.I),
     "the Settings door's recorded dismissible-hide is stated and then "
     "retired/denied"),
    # Door-agnostic, so it also catches a denial in a clause that never repeats the
    # door name. It must require an actual `dismiss` token: keying on a bare
    # `without`/`lacks` was measured rejecting correct prose ("the CTA opens Settings
    # without a full reload", "later steps proceed without blocking", "the CTA lacks a
    # tooltip today"). `non-dismissible` alone remains a STATED LIMIT — it cannot be
    # told apart from the billing banner this module's own notes describe as
    # legitimately non-dismissible.
    (re.compile(r"\b(?:lacks?|without|nothing|neither|no|zero)\b[^.;]{0,40}?"
                r"\bdismiss", re.I),
     "a dismissal is denied, contradicting the Settings door's recorded "
     "dismissible-hide"),
)

#: The Overview door's answer asserted POSITIVELY. Matched on a text with BOTH required
#: claims masked out, because the shape has to span a comma to catch a denial that
#: follows the correct claim ("… carries no dismissal, but it is dismissible once
#: complete") — and across that comma it would otherwise reach the Settings claim's own
#: `dismissible-hide` in the reverse-order row. `(?!al\b)` keeps "dismissal" itself out
#: of the shape; the verbs are affirmative only.
_OVERVIEW_SHAPES = (
    (re.compile(r"\b(?:overview|overviewnextactioncard)\w*\b[^.;]{0,120}?"
                r"\b(?:is|are|was|were|remains?|shows?|has|have|provides?|offers?"
                r"|carries|renders|exposes|includes|contains|adds)\b[^.;]{0,30}?"
                r"\bdismiss(?!al\b)", re.I),
     "the Overview door is asserted to have a dismissal (it has none)"),
)


def _dismissal_claim_conflicts(live: str) -> list[str]:
    """The ways the cited row can fail to carry ONE answer per door.

    Empty list = both claims are present verbatim and no contradicting shape is.
    """
    text = " ".join(live.split())
    # Drop the one legitimately negative sentence — the Overview door's own answer —
    # before the denial shapes run, so a door-agnostic `no … dismiss` rule cannot read
    # it as a denial of the Settings door. Its presence is required just below.
    residual = text.lower().replace(_REQUIRED_CLAIMS[1][1], " ")
    # Both claims masked: the Overview shape must span a comma to catch a denial that
    # follows the correct claim, and across that comma it would otherwise reach the
    # Settings claim's own `dismissible-hide` in the reverse-order row.
    both_masked = residual.replace(_REQUIRED_CLAIMS[0][1], " ")
    out: list[str] = []
    for label, claim in _REQUIRED_CLAIMS:
        if claim not in text.lower():
            out.append(f"the row no longer states {label} — it must carry "
                       f'{claim!r} verbatim; a reworded or negated version drops '
                       "the claim, which is the point of matching it whole")
    for rx, why in _DENIED_SHAPES:
        if rx.search(residual):
            out.append(why)
    for rx, why in _OVERVIEW_SHAPES:
        if rx.search(both_masked):
            out.append(why)
    return out


#: Rows that DO contradict the corrected claim and are deliberately NOT caught, so the
#: limitation stated in the module docstring is executable rather than prose. Two of
#: these reuse a required claim verbatim with a pronoun; one asserts the Overview
#: dismissal in the very words of the Settings claim. Closing them needs anaphora
#: resolution, and deleting them from this tuple would silently overclaim the guard.
_STATED_LIMIT_ROWS = (
    "the Settings Setup guide is the counted checklist card, and the decision recorded "
    "for that door is dismissible-hide with a re-open path when complete, but it "
    "carries no dismissal; the Overview next action carries no dismissal",
    "the Settings card's recorded decision is dismissible-hide with a re-open path, and "
    "the Settings card carries no dismissal; the Overview next action carries no "
    "dismissal",
    "the Overview card is dismissible-hide with a re-open path; the Settings Setup guide "
    "is the counted checklist card, and the decision recorded for that door is "
    "dismissible-hide with a re-open path when complete; the Overview next action "
    "carries no dismissal",
    "the Settings Setup guide is the counted checklist card, and the decision recorded "
    "for that door is dismissible-hide with a re-open path when complete; the Overview "
    "next action carries no dismissal; the Settings guide is nonetheless "
    "non-dismissible",
)


def _orphan_close_parens(text: str) -> list[str]:
    """`)` clumps that follow a code span at paren depth 0 — the shape a missing
    `(` leaves behind (`rendered from `OnboardingState`).completed_steps`)."""
    found: list[str] = []
    balance = 0
    for i, ch in enumerate(text):
        if ch == "(":
            balance += 1
        elif ch == ")":
            if balance == 0 and i and text[i - 1] == "`":
                found.append(text[max(0, i - 40) : i + 20])
            else:
                balance = max(0, balance - 1)
    return found


# ═══════════════════════════════════════════════════════════════════════
# 1-3: the standing invariants (guard rails, green before and after)
# ═══════════════════════════════════════════════════════════════════════

def test_every_capability_quotes_its_surfaces_test_layer_verbatim():
    surfaces = _surfaces()
    assert len(surfaces) == 17, sorted(surfaces)
    for capability, (cited, verified_at) in _capabilities().items():
        assert cited, f"{capability}: no surface cited"
        assert cited <= set(surfaces), f"{capability}: cites {cited - set(surfaces)}"
        if len(cited) == 1:
            (only,) = cited
            assert verified_at == surfaces[only]["layer"], (
                f"{capability}: `Verified at` is not surface {only}'s Test Layer")
            continue
        quoted = {}
        for segment in verified_at.split(_SEGMENT):
            m = re.match(r"^(\d{1,2}): (.+)$", segment)
            assert m, f"{capability}: segment without a surface prefix — {segment!r}"
            quoted[int(m.group(1))] = m.group(2)
        assert set(quoted) == cited, (
            f"{capability}: quotes {sorted(quoted)} but cites {sorted(cited)}")
        for number, layer in quoted.items():
            assert layer == surfaces[number]["layer"], (
                f"{capability}: surface {number} quoted as {layer!r}, but its "
                f"Test Layer is {surfaces[number]['layer']!r}")


def test_every_surface_is_reachable_from_a_capability():
    cited = set().union(*(c[0] for c in _capabilities().values()))
    assert cited == set(_surfaces()), (
        f"surfaces cited by no capability: {sorted(set(_surfaces()) - cited)}")


def _uncovered_surfaces(rows: dict[str, set[int]]) -> set[int]:
    """Surfaces no SPECIFIC E2E row walks.

    The whole-journey catch-all row (`E2E-12` = `1-17 (all)`) is EXCLUDED. Unioning
    over every row — the previous form — made `exercised` equal to the surface set
    by construction whenever a 1-17 catch-all exists, so the assertion could never
    fail for the reason it documents: a surface no concrete scenario walks (no
    scenario that would catch its regression) was reported as covered.
    """
    all_surfaces = set(_surfaces())
    specific = [s for s in rows.values() if s != all_surfaces]
    return all_surfaces - (set().union(*specific) if specific else set())


#: Surfaces exercised ONLY through the whole-journey catch-all row. Baselined,
#: not ignored: the check fails if a SECOND surface joins this set, which is the
#: drift that matters. Surface 14 (onboarding telemetry, W11) is asserted by
#: `03-scope.md` §4 E2E-12 — "W11 fires seed_complete + decide_complete once per
#: org (deduped)" — and by no SPECIFIC scenario; its own `Test Layer` is
#: "Integration + regression (analytics.py existing tests)", so no journey row
#: owns it. Recorded here rather than silently absorbed by the catch-all.
_CATCH_ALL_ONLY_SURFACES = {14}


def test_every_surface_is_exercised_by_at_least_one_e2e_row():
    """The E2E-side reverse direction: a surface no high-level scenario walks has
    no scenario that would catch its regression.

    This replaces an assertion that was implied by the capability-direction check
    above (`{n for c in ...} <= set(_surfaces())` holds whenever the union equals
    the surface set) and could never fail independently while reading as a second
    direction. It is written against `_uncovered_surfaces`, which excludes the
    whole-journey catch-all: including it re-introduced the same vacuity, because
    `E2E-12` is `1-17 (all)` and the union then equals the surface set for EVERY
    state of the document. The one surface that genuinely survives only in the
    catch-all is baselined (`_CATCH_ALL_ONLY_SURFACES`), so a SECOND one fails.
    """
    rows = _e2e_rows()
    assert set(_surfaces()) in rows.values(), (
        "the whole-journey catch-all E2E row is gone — this check assumes one "
        "row cites the full surface set")
    uncovered = _uncovered_surfaces(rows)
    assert uncovered == _CATCH_ALL_ONLY_SURFACES, (
        f"surfaces no specific E2E row exercises: {sorted(uncovered)} — expected "
        f"only the baselined catch-all-only set {sorted(_CATCH_ALL_ONLY_SURFACES)}. "
        "A new surface here has no scenario of its own to catch its regression")
    # Non-vacuity: the check must FAIL when a surface survives only in the
    # catch-all. Drop surfaces 15/16 from every specific row of a mutated copy.
    mutated = {n: (s - {15, 16}) for n, s in rows.items()}
    assert _uncovered_surfaces(mutated) - _CATCH_ALL_ONLY_SURFACES == {15, 16}, (
        "the E2E-coverage check passes a document where two surfaces survive "
        "only in the catch-all row")


@lru_cache(maxsize=1)
def _plan_de2e_rows() -> dict[str, set[int]]:
    """`E2E-N` → the surfaces `06-plan.md`'s `DE2E-N` heading cites.

    The plan is the THIRD copy of the same mapping: its `### DE2E-6 … [1,2,10]`
    heading still gave the pre-fix answer after `04-test-design.md`,
    `capstone-report.md` and `03-scope.md` were corrected — the exact defect
    class this module exists for, one document further out. `DE2E-N` and
    `E2E-N` are the same numbered scenario (DE2E-5 and DE2E-8 already agree
    with the map), so the numbers are comparable directly.
    """
    out: dict[str, set[int]] = {}
    for line in PLAN.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^###\s+(DE2E-\d+)\b.*?\[(?:surfaces\s+)?([^\]]+)\]", line)
        if not m:
            continue
        numbers: set[int] = set()
        for chunk in re.findall(r"\d+\s*-\s*\d+|\d+", m.group(2)):
            if "-" in chunk:
                lo, hi = (int(p) for p in chunk.split("-"))
                numbers.update(range(lo, hi + 1))
            else:
                numbers.add(int(chunk))
        out["E2E-" + m.group(1).split("-")[1]] = numbers
    return out


def test_the_plan_cites_the_same_surfaces_for_every_e2e_row():
    """The plan is a third copy of the E2E → surface map, and it had drifted.

    PRE-FIX: `06-plan.md` read `### DE2E-6 — Graph-held resumption [1,2,10]`
    while the map said `1, 2, 9, 10` — and none of the guards read the plan, so
    the drift was invisible. Two copies drift; a third copy drifts unguarded.
    The map is the source here (this PR's whole point), and the comparison is
    set-based so ordering and spacing cannot mask a difference.
    """
    plan = _plan_de2e_rows()
    mapped = _e2e_rows()
    assert plan, "could not parse any `### DE2E-N … [surfaces]` heading in 06-plan.md"
    assert set(plan) == set(mapped), (
        "the plan's DE2E rows drifted from the map's E2E rows: "
        f"plan-only {sorted(set(plan) - set(mapped))}, "
        f"map-only {sorted(set(mapped) - set(plan))}")
    drift = {k: (sorted(plan[k]), sorted(mapped[k])) for k in plan if plan[k] != mapped[k]}
    assert not drift, (
        "06-plan.md gives a different surface set than 04-test-design.md for: "
        + "; ".join(f"{k} plan={p} map={m}" for k, (p, m) in sorted(drift.items())))


def _plan_role_violations(block: str) -> list[str]:
    """Ways the plan's DE2E-6 section can get the two door roles wrong.

    Both directions matter, and the FIRST version of this check only did the
    first: presence alone is blind to a section that gives BOTH doors BOTH roles
    ("the Overview next action is also the counted checklist card, and the Settings
    Setup guide is one next step") — the "two answers for one capability" defect
    this module exists for. So each door's role is asserted positively AND the
    other door's role is asserted absent.
    """
    block = block.lower()
    out: list[str] = []
    if "the settings setup guide is the counted checklist card" not in block:
        out.append("the plan must state that the Settings Setup guide IS the "
                   "counted checklist card (its role), not only that it renders "
                   "the same state")
    if "the overview next action is one next step" not in block:
        out.append("the plan must state the Overview next action's role (ONE next "
                   "step + a CTA), not only that it mirrors the state")
    for forbidden, why in (
        ("the overview next action is the counted checklist card",
         "the counted checklist belongs to the Settings door"),
        ("the overview next action is also the counted checklist card",
         "the counted checklist belongs to the Settings door"),
        ("the settings setup guide is one next step",
         "ONE next step + a CTA belongs to the Overview door"),
    ):
        if forbidden in block:
            out.append(f"the plan gives both doors the same role ({forbidden!r}) — "
                       f"{why}")
    out.extend(_dismissal_claim_conflicts(block))
    return out


def test_the_plan_assigns_each_door_its_own_role():
    """The plan's DE2E-6 `Assert` is the spec a child issue implements, and it is
    the third copy of the door ROLES as well as of the surface set.

    PRE-FIX: the Assert line named both doors' claims in one `and`-joined clause
    ("the Settings Setup guide renders the same graph-held state as the Overview
    next action — ONE next step + a CTA into the guide, never a counted checklist
    of its own"), which reads as attaching the Overview's role to the guide. The
    authority (`03-scope.md` §4 E2E-6, `05-ux-decisions.md` row "Affordance: setup
    card") assigns them the other way round: the GUIDE is the counted checklist,
    the OVERVIEW is one next step + a CTA. Nothing checked it, which is why the
    ambiguity shipped.
    """
    text = PLAN.read_text(encoding="utf-8")
    m = re.search(r"^### DE2E-6\b.*?(?=^### |\Z)", text, re.M | re.S)
    assert m, "the `### DE2E-6` section is gone from 06-plan.md"
    block = m.group(0)
    assert _plan_role_violations(block) == [], (
        "the plan's DE2E-6 gives a wrong or doubled door role:\n- "
        + "\n- ".join(_plan_role_violations(block)))
    # Non-vacuity: a section that hands BOTH roles to BOTH doors must fail. The
    # presence-only form of this check passed exactly this mutation.
    doubled = block + ("\nnote: the overview next action is also the counted "
                       "checklist card, and the settings setup guide is one "
                       "next step + a cta.\n")
    assert _plan_role_violations(doubled), (
        "the door-role check passes a section that gives both doors both roles")
    assert any("same role" in v for v in _plan_role_violations(doubled)), (
        "the doubled-role mutation is caught, but not by the role check: "
        + str(_plan_role_violations(doubled)))


def test_capability_rows_are_the_scopes_rows_in_order():
    scope_names = [
        _cells(line)[0]
        for line in SCOPE.read_text(encoding="utf-8").splitlines()
        if re.match(r"^\|\s*(W\d+) [^|]*\|", line)
    ]
    doc_names = list(_capabilities())
    assert scope_names, "could not parse 03-scope.md §2"
    assert doc_names == scope_names, (
        "the map's capability rows drifted from the scope — "
        f"missing {sorted(set(scope_names) - set(doc_names))}, "
        f"extra {sorted(set(doc_names) - set(scope_names))}, "
        f"order {doc_names} vs {scope_names}")
    assert not any(name.startswith("W10 ") for name in doc_names), (
        "W10 is out of scope (03-scope.md §1) and must not have a row")


# ═══════════════════════════════════════════════════════════════════════
# 4-8: the defect pins (each fails on the pre-fix document)
# ═══════════════════════════════════════════════════════════════════════

def test_a_capability_row_cites_every_surface_its_rationale_names():
    """PRE-FIX: `W5 setup card` cited `9, 17` while its bullet placed the counted
    checklist and the re-open path on surface 10 — two answers for one
    capability. Post-fix the row is `9, 10, 17`.

    Citations must use the explicit `surface N` form (see `_cited_surfaces`), and
    `assert named` keeps the guard from passing vacuously on a bullet with no
    explicit citation at all."""
    capabilities = _capabilities()
    for prefix in ("W5 setup card", "W9 trigger/entry", "W7 fusion",
                   "W2 universal command"):
        cited = capabilities[prefix][0]
        named = _cited_surfaces(_section_text(prefix))
        assert named, (
            f"{prefix}: the rationale cites no surface in the explicit "
            "`surface N` form — the row/prose guard would pass vacuously")
        assert named <= set(_surfaces()), (
            f"{prefix}: rationale names {sorted(named - set(_surfaces()))}, "
            "which are not surfaces")
        assert named <= cited, (
            f"{prefix}: the rationale names surface(s) {sorted(named - cited)} "
            f"but the row cites only {sorted(cited)} — a reader reconciling "
            "row and prose gets two answers")


def _contract_doors() -> set[int]:
    """Surfaces whose own Contract cell names a Setup-guide re-entry door."""
    return {
        n for n, s in _surfaces().items()
        if "Setup guide" in s["contract"] or "Continue-setup" in s["contract"]
    }


def test_e2e6_cites_every_surface_that_owns_a_reentry_door():
    """PRE-FIX: `E2E-6 graph-held resumption | 1, 2, 10` omitted surface 9, the
    Overview element the document itself called the Continue-setup card — the
    scope's second door in "via the Settings Setup guide **or the Continue-setup
    card**" (`03-scope.md` E2E-6). Post-fix surface 9 is cited (`1, 2, 9, 10`).

    The door set is anchored in the scope (see `_missing_e2e6_doors`), so the pin
    cannot be shrunk by rewording the document under test.
    """
    scope = _scope_section("### E2E-6")
    for door in SCOPE_E2E6_DOOR_SURFACES:
        assert door in scope, (
            f"`03-scope.md` E2E-6 no longer names {door!r} — the door authority "
            "moved; update SCOPE_E2E6_DOOR_SURFACES in the same edit")
    contract_doors = _contract_doors()
    assert 10 in contract_doors, (
        "surface 10 owns the Settings Setup guide — the contract scan found "
        f"{sorted(contract_doors)}")
    cited = _e2e_rows()["E2E-6"]
    missing = _missing_e2e6_doors(
        cited, scope_text=scope, contract_doors=contract_doors)
    assert not missing, (
        f"E2E-6 cites {sorted(cited)} but the re-entry doors are on "
        f"{sorted(missing)} too — a door with no E2E row has no scenario")


def test_the_e2e6_door_guard_rejects_a_reworded_contract_plus_a_reverted_row():
    """The first version of the E2E-6 pin was FAIL-OPEN, and this is the receipt.

    It derived `doors` only from the map's Contract column and asserted only
    `10 in doors`; the door that motivated the pin lived on surface 9, whose
    contract named it. So this state passed it:

        surface 9's contract reworded to drop "Setup guide"/"Continue-setup"
        E2E-6 reverted to `1, 2, 10`

    An anchored door set rejects the same state — the scope says both doors
    exist, and it is not the document under test.
    """
    scope = _scope_section("### E2E-6")
    assert _missing_e2e6_doors(
        {1, 2, 10}, scope_text=scope, contract_doors={10}) == {9}


def test_the_counted_dismissible_checklist_belongs_to_surface_10_not_9():
    """PRE-FIX: surface 9's contract claimed "a dismissible checklist rendered
    from `OnboardingState`.completed_steps". The shipped Overview next-action
    element is one next step + a CTA into the Settings Setup guide; the counted
    N-of-M checklist is the Settings-tab Setup guide card; neither is
    dismissible. If the dashboard ever GAINS a dismissal affordance, update the
    doc and this pin together — they are the same claim."""
    surfaces = _surfaces()
    nine, ten = surfaces[9], surfaces[10]
    assert "dismissible" not in nine["contract"].lower(), (
        "surface 9's next action is not dismissible in the shipped dashboard "
        "(the only `dismiss` hits are the recovery/claim/billing banners)")
    assert "dismissible" not in nine["failure_modes"].lower(), (
        "surface 9's failure modes must not assume a dismissal affordance")
    assert "checklist" in ten["contract"].lower(), (
        "surface 10 owns the counted checklist (the Setup-guide card) — its "
        "contract must say so, or the checklist has no owner and surface 9's "
        "`not a checklist` claim has nothing to point at")
    assert "same graph-held state" in nine["contract"].lower(), (
        "surface 9's next-action element must be stated as rendering the SAME "
        "graph-held state as the Settings Setup guide (`completed_steps` is a "
        "read projection, never a second store)")


def test_no_orphan_close_paren_after_a_code_span():
    """The reviewed head already read ``OnboardingState``'s completed_steps`` —
    the stray `)` the review reported (`rendered from
    `OnboardingState`).completed_steps`) was gone by `1758bbe59`. Kept as a
    GUARD (it would have caught it), with the detector's own non-blindness
    asserted on synthetic text so it cannot pass by seeing nothing."""
    assert _orphan_close_parens("rendered from `OnboardingState`).completed"), (
        "the detector misses the shape the review reported")
    assert not _orphan_close_parens("rendered from `OnboardingState`'s completed")
    assert not _orphan_close_parens("(`completed_steps` is a read projection)"), (
        "a `)` that CLOSES a real `(` is not an orphan")
    offenders: list[str] = []
    for number, surface in _surfaces().items():
        for column in ("contract", "failure_modes", "layer"):
            offenders += [
                f"surface {number}.{column}: …{frag}…"
                for frag in _orphan_close_parens(surface[column])
            ]
    assert not offenders, "orphan `)` after a code span:\n" + "\n".join(offenders)


def test_the_checklist_row_carries_the_surface_9_projection_obligation():
    """PRE-FIX: the `9-10 overview + settings` checklist row said only
    "3 elements zero toggles; sources only in Settings; single-owner merge
    discipline" — the projection obligation lived only in the surface table,
    which is not what a child issue is told to satisfy."""
    row = next(
        (ln for ln in _doc_lines()
         if ln.startswith("| 9-10 overview + settings")), None)
    assert row, "the `9-10 overview + settings` verification-checklist row is gone"
    lowered = row.lower()
    assert "read projection" in lowered, (
        "the child-issue checklist row must carry the `completed_steps` read-"
        "projection obligation the surface table states")
    assert "never a second store" in lowered
    assert "checklist" in lowered, (
        "the row must say the Overview next action is not a counted checklist "
        "of its own")


def test_the_ux_decision_row_the_map_cites_no_longer_claims_a_dismissible_list():
    """PRE-FIX: `05-ux-decisions.md` row 5 — "Affordance: setup card", the row the
    map's W5 rationale cites as its authority — read "Dashboard renders the SAME
    state as a **dismissible checklist card**; disappears when complete". It named
    no door, so it licensed exactly the claim the map had to correct: the shipped
    Overview next action is one next step + a CTA (`OverviewNextActionCard`), the
    counted checklist is the Settings-tab `SetupGuideCard`, and neither carries a
    dismissal. A map that points, for its corrected claim, at a decision row still
    stating the superseded one is the same "two answers for one capability" defect
    one document over.

    The LIVE half of the row is checked (everything before the recorded
    correction), so the superseded wording may stay quoted as history.
    """
    row = next(
        (ln for ln in UX.read_text(encoding="utf-8").splitlines()
         if ln.startswith("| 5 | Affordance: setup card")), None)
    assert row, "the `Affordance: setup card` decision row is gone"
    live = row.split("Corrected")[0].lower()
    assert "overview" in live, (
        "the row must say which door renders what — naming no door is what let "
        "the Overview next action be read as the dismissible checklist")
    assert "never a counted checklist" in live, (
        "the row must state that the Overview next action is not a counted "
        "checklist of its own")
    assert "dismissible checklist card" not in live, (
        "the shipped Overview next-action element carries no dismissal — the "
        "collapse/dismissible-hide belongs to the Settings Setup guide")
    conflicts = _dismissal_claim_conflicts(live)
    assert not conflicts, (
        "the row gives two answers for the dismissal claim it is cited for:\n- "
        + "\n- ".join(conflicts) + "\nrow: " + live)


def test_the_dismissal_guard_states_its_own_limit_rather_than_overclaiming():
    """The limits the module docstring names are real and pinned here.

    Each row below DOES contradict the corrected claim and is NOT caught — anaphora
    ("it carries no dismissal", where "it" is the Settings door) and a claim asserted
    in the other door's exact words cannot be separated by a pattern. Naming them as
    limits, in an executable tuple, is what stops the guard from being read as a truth
    oracle. If a future change makes one of these catchable, move it into
    `test_the_dismissal_door_guard_rejects_the_one_sentence_contradiction`'s must_fail
    table — do not just delete it from here.
    """
    for row in _STATED_LIMIT_ROWS:
        assert not _dismissal_claim_conflicts(row), (
            f"a stated-limit row is now caught — move it to the must_fail table "
            f"instead of leaving the docstring understating the guard: {row!r}")


def test_the_dismissal_door_guard_rejects_the_one_sentence_contradiction():
    """Non-vacuity, and the RECEIPT for every rule this guard has had.

    The reviewed head's live row said the Settings card "collapses to compact
    status / dismissible-hide with re-open path when complete" and then "neither
    card carries a dismissal" — both claims in one sentence, and an earlier
    literal-only assertion passed it because the contradiction lives in the two
    doors, not in one phrase.

    The table below is the accumulated receipt: each `must_fail` row is a wording
    that one of this guard's earlier rules certified CLEAN, and each `must_pass`
    row is wording one of them wrongly rejected. A rule is only worth keeping if it
    keeps both columns.
    """
    settings_claim = ("the Settings Setup guide is the counted checklist card, and "
                      "the decision recorded for that door is dismissible-hide with "
                      "a re-open path when complete")
    overview_claim = "the Overview next action carries no dismissal"
    row = f"{settings_claim}; {overview_claim}"

    must_pass = (
        row,
        # the live shape: the "never a counted checklist" negation belongs to the
        # Overview clause and must not deny either claim
        f"{settings_claim}; the Overview next action mirrors the state — never a "
        f"counted checklist of its own — and carries no dismissal",
        # trailing prose about OTHER things must not be read as retiring the claim,
        # and a non-dismissal "without"/"lacks" or a legitimately non-dismissible
        # BANNER must not be read as a denial of the Settings door
        f"{settings_claim}, and later steps are unaffected; {overview_claim}",
        # the two claims joined by a COMMA rather than `;` — one clause, both doors.
        # Door-scoped shapes are matched per comma segment, so neither door's wording
        # is attributed to the other (measured false rejection, both orders).
        f"the Settings card's recorded decision is dismissible-hide with a re-open "
        f"path, and the Overview next action carries no dismissal",
        f"{overview_claim}, and the Settings card's recorded decision is "
        f"dismissible-hide with a re-open path",
        f"{row}; the CTA opens Settings without a full reload",
        f"{row}; later steps proceed without blocking",
        f"{row}; the CTA lacks a tooltip today",
        f"{row}; the billing banner is non-dismissible",
        f"{settings_claim}, not a blocking modal; {overview_claim}",
        # the Settings door named by the component its own docs use
        f"the Settings-tab `SetupGuideCard` is the counted checklist card and its "
        f"recorded decision is dismissible-hide with a re-open path; {overview_claim}",
    )
    must_fail = (
        # the superseded wording
        f"{settings_claim}; neither card carries a dismissal",
        f"{settings_claim}; the Overview next action is a dismissible checklist",
        # the Overview door affirmed to have a dismissal
        f"{settings_claim}; the Overview next action is dismissible once complete",
        # the component-named Overview door, WITH both claims present — so this row
        # cannot pass via the missing-claim rule and actually exercises the shape
        f"{settings_claim}; `OverviewNextActionCard` shows a dismiss control; "
        f"{overview_claim}",
        f"{settings_claim}; the Overview card renders a dismiss control once "
        f"complete; {overview_claim}",
        f"{settings_claim}; the Settings card has no dismissal; {overview_claim}",
        # a denial in the comma clause AFTER the door name — the four rows that the
        # comma-segment version let through (measured regression), now receipts
        f"{settings_claim}, and it has no dismissal; {overview_claim}",
        f"{settings_claim}, and nothing there can be dismissed; {overview_claim}",
        f"{settings_claim}, which lacks any dismiss control; {overview_claim}",
        f"{overview_claim}, but it is dismissible once complete; {settings_claim}",
        f"{settings_claim}; the Overview next action is not only dismissible but "
        f"re-openable",
        # a row that never states the Overview's answer
        settings_claim,
        # the Settings record negated in many grammatical positions — each drops the
        # VERBATIM claim, which is why the verbatim form is what gets matched
        f"the Settings Setup guide does not have a dismissible-hide with a re-open "
        f"path; {overview_claim}",
        f"the Settings Setup guide is non-dismissible; {overview_claim}",
        f"the Settings Setup guide lacks any dismissal; {overview_claim}",
        f"the Settings Setup guide has nothing to dismiss; {overview_claim}",
        f"the Settings Setup guide no longer carries a dismissible-hide; "
        f"{overview_claim}",
        # the claim present AND retired in the same row
        f"{settings_claim} — a dismissible-hide that never shipped; {overview_claim}",
        f"{settings_claim}, since superseded; {overview_claim}",
        f"{settings_claim}; the dismissible-hide with a re-open path was removed; "
        f"{overview_claim}",
    )
    for r in must_pass:
        assert not _dismissal_claim_conflicts(r), (
            f"the guard rejects a correct row: {r!r}\n- "
            + "\n- ".join(_dismissal_claim_conflicts(r)))
    for r in must_fail:
        assert _dismissal_claim_conflicts(r), (
            f"the guard certifies a contradicting row CLEAN: {r!r}")


# ═══════════════════════════════════════════════════════════════════════
# 10-12: the superseded mirror claim, reconciled everywhere it survived
# ═══════════════════════════════════════════════════════════════════════

#: The pre-fix wording. It named no door, so it read as licensing the
#: "Overview renders the counted dismissible checklist" claim the map dropped.
_SUPERSEDED_MIRROR = "dismissible checklist card"
_ALIGN = EPIC / "00-align-and-scope.md"


def test_the_scope_e2e6_mirror_claim_is_reconciled_where_it_was_left_stale():
    """PRE-FIX: `03-scope.md` §4 E2E-6 — the AUTHORITY this module's E2E-6 door
    pin anchors to, "which the document under test cannot reword" — still read
    "mirrored as a dismissible checklist card" after the map and
    `05-ux-decisions.md` row 5 were corrected. The epic then carried two answers
    for one claim, in the document a reader is told to trust.

    Only the MIRROR claim is reconciled: the `When:` line's door names are what
    the anchor binds to and they are untouched (asserted by
    `test_e2e6_cites_every_surface_that_owns_a_reentry_door`)."""
    section = _scope_section("### E2E-6")
    live = section.split("Superseded wording")[0].lower()
    assert "counted checklist" in live, (
        "the scope's E2E-6 must name the counted checklist card explicitly — "
        "`dismissible checklist card` alone named no door: " + live)
    assert "one next step" in live and "cta" in live, (
        "the scope's E2E-6 must state the Overview mirror's actual shape (one "
        "next step + a CTA into the guide), not just that a card exists")
    for line in section.splitlines():
        if _SUPERSEDED_MIRROR in line:
            assert "superseded" in line.lower(), (
                "the pre-fix mirror claim may survive only where it is marked "
                "as superseded history: " + line)


def test_the_historical_align_copy_carries_the_supersession_note():
    """`00-align-and-scope.md` keeps the pre-fix wording as history (its E2E-6
    is the record of what was aligned). History is fine; UNMARKED history is
    the third answer for one claim."""
    lines = _ALIGN.read_text(encoding="utf-8").splitlines()
    hits = [i for i, ln in enumerate(lines) if _SUPERSEDED_MIRROR in ln]
    assert hits, (
        "the align record no longer carries the wording this pin exists for — if "
        "it was deleted outright, delete this guard in the same edit")
    for i in hits:
        window = " ".join(lines[i : i + 4]).lower()
        assert "superseded" in window, (
            "the align record keeps the pre-fix wording, so the supersession "
            "note must sit beside it: " + lines[i])


def test_the_scope_w5_capability_bullet_does_not_assert_the_dismissible_mirror():
    """The same claim appears a second time in `03-scope.md` §2's **W5** bullet
    — "dashboard mirror (**dismissible checklist card**)" — which is the
    capability definition the map's W5 rows are cut from, and
    `00-align-and-scope.md` carries the same bullet in its alignment record.

    Found by this guard, not by the review: reconciling only the E2E-6 line
    leaves the scope still stating the superseded mirror in the place a child
    issue reads its capability from."""
    for path in (SCOPE, _ALIGN):
        bullet = next(
            (ln for ln in path.read_text(encoding="utf-8").splitlines()
             if ln.startswith("- **W5 — Graph-held")), None)
        assert bullet, f"{path.name}: the W5 capability bullet is gone"
        assert _SUPERSEDED_MIRROR not in bullet, (
            f"{path.name}: the W5 bullet still states the superseded "
            "\"dashboard mirror (dismissible checklist card)\" — the counted "
            "checklist is the Settings door's and the Overview mirror is one "
            "next step + a CTA")
        assert "counted checklist card" in bullet and "Overview" in bullet, (
            f"{path.name}: the W5 bullet must name both doors' shapes")


def test_the_ux_classification_row_does_not_re_cite_the_superseded_claim():
    """PRE-FIX: the `Visual affordances` classification row sourced itself to
    "Epic body (**dismissible card**, ...)" — pointing the reader at the very
    line that was corrected."""
    row = next(
        (ln for ln in UX.read_text(encoding="utf-8").splitlines()
         if ln.startswith("| 5 | Visual affordances")), None)
    assert row, "the `Visual affordances` classification row is gone"
    lowered = row.lower()
    assert "dismissible card" not in lowered, (
        "the row still cites the epic body's superseded `dismissible card` "
        "wording; the affordance is the counted checklist on the Settings door "
        "and one next step + a CTA on the Overview one")
    assert "affordance: setup card" in lowered, (
        "the row must point at the corrected decision row it describes")

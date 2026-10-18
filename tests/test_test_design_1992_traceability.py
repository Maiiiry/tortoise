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
   stated both. Claims are now read per clause and attached to the door named in
   that clause; a door-less denial that contradicts an affirming door is a
   failure. Polarity is bound to a negation that GOVERNS "dismiss", never to any
   negation in the clause: the clause-wide scan the first version used read
   `never a counted checklist of its own, and it is dismissible once complete` as
   a DENIAL (so a regressed row affirming an Overview dismissal passed), and read
   the correct row's `implements no hide` as a denial of the Settings door's
   recorded dismissible-hide. Both counterexamples are in-suite rejection cases.
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


# ── dismissal claims: every one of them belongs to a DOOR ──────────────
#: The two doors `05-ux-decisions.md` row "Affordance: setup card" describes.
#: The Settings Setup guide owns the recorded dismissible-hide; the Overview
#: next action owns none. Matched as PATTERNS over the names the epic's own docs
#: use — the prose name ("the Settings Setup guide", "the Overview next
#: action") AND the component name (`SetupGuideCard`,
#: `OverviewNextActionCard`). Keying either door on one literal phrase makes a row
#: that names the door any other way invisible: with the phrase-only table a row
#: denying the Settings door's recorded dismissal via `SetupGuideCard` was read as
#: claiming nothing about it. Both shapes are pinned in-suite.
#:
#: The component-name alternative sits INSIDE the branch that requires the door's
#: own word (`settings` / `overview`), never standalone: a clause that mentions
#: `SetupGuideCard` while talking about the OVERVIEW ("the shipped `SetupGuideCard`
#: collapses to a status today") is a claim about the Overview door, and matching
#: the component name alone attributed the Overview's denial to the Settings door.
_DOOR_RES = {
    "settings": re.compile(
        r"\bsettings\b[^.;]{0,40}?(?:\bset\s*up\s+guide\b|\bguide\b|\bcard\b"
        r"|setup[\s_-]*guide[\s_-]*card)",
        re.I),
    "overview": re.compile(
        r"\boverview\b|\bcontinue[\s-]*setup\s+card\b",
        re.I),
}

#: A denial quantified over BOTH doors ("neither card carries a dismissal",
#: "no door ..."). This is the shape that swallowed the Settings door's
#: recorded dismissal while an adjacent clause asserted it.
_DOORLESS_DENIAL = re.compile(
    r"\b(?:neither|no)\s+(?:of\s+the\s+)?(?:both\s+)?(?:cards?|doors?)\b", re.I)

#: A negation that GOVERNS "dismiss" — the dismissal PHRASE, never a distance.
#:
#: Two earlier versions of this rule were wrong, measured on real clauses:
#:   (a) scanning the whole clause for any negation scored
#:       "never a counted checklist of its own — and the Overview door is
#:       dismissible once complete" as a DENIAL, so a row AFFIRMING an Overview
#:       dismissal passed the guard whose entire purpose is that claim;
#:   (b) bounding that scan to 24 chars did not fix it — it only required the
#:       unrelated negation to be NEARER: "the Overview card has no toggle and is
#:       dismissible once complete" still scored as a denial. The same bound also
#:       rejected CORRECT prose ("cannot be dismissed", "nothing ... is
#:       dismissible", "undismissable") as an affirmation.
#: So each alternative below matches a negation governing the dismissal token,
#: with at most two words between them ("no counted dismissal"), plus the
#: idiomatic forms. A negation attached to some OTHER noun ("no toggle", "no
#: hide", "not a checklist") is not a claim about dismissal and no longer denies.
_DISMISSAL_DENIAL = re.compile(
    r"\b(?:no|zero)\s+(?:\w+\s+){0,2}?dismiss"              # no dismissal
    r"|\bnot\s+(?:\w+\s+){0,2}?dismiss"                      # not dismissible
    r"|\bnever\s+(?:\w+\s+){0,2}?dismiss"                    # never dismissed
    r"|\bwithout\s+(?:\w+\s+){0,2}?dismiss"                  # without dismissal
    r"|\b(?:cannot|can't|won't|doesn't|don't|isn't|aren't)\b(?:\s+be)?\s+dismiss"
    r"|\b(?:is|are|was|were|can\s+be|will\s+be)\s+not\s+dismiss"   # is not dismissible
    r"|\bnothing\b[^.;]{0,24}?\b(?:is|are)\s+dismiss"         # nothing ... is dismissible
    r"|\bneither\b[^.;]{0,24}?\b(?:carries|has|offers|implements)\b[^.;]{0,12}?dismiss"
    r"|\bundismiss\w*",                                      # undismissable
    re.I,
)
_DISMISSAL = re.compile(r"dismiss", re.I)


def _dismissal_claims(text: str) -> dict[str, set[str]]:
    """Clause-level `door -> {"affirms", "denies"}` for every dismissal claim.

    `;`/`.` split each claim away from its neighbours so it stays attached to the
    door named in the SAME clause. Averaging the whole row into one boolean is
    what let "the Settings card collapses / dismissible-hides when complete" and
    "neither card carries a dismissal" sit in one sentence: two doors, two
    answers, and a reader tracing either one gets a different claim.
    """
    claims: dict[str, set[str]] = {"settings": set(), "overview": set()}
    for clause in re.split(r"[;.]", text):
        if not _DISMISSAL.search(clause):
            continue
        # Bound to the dismissal token, never to the clause: see _DISMISSAL_DENIAL.
        verdict = "denies" if _DISMISSAL_DENIAL.search(clause) else "affirms"
        doors = [d for d, rx in _DOOR_RES.items() if rx.search(clause)]
        for door in doors:
            claims[door].add(verdict)
        if verdict == "denies" and _DOORLESS_DENIAL.search(clause):
            claims.setdefault("*", set()).add(verdict)
    return claims


def _dismissal_claim_conflicts(live: str) -> list[str]:
    """The ways one row can give two answers for one dismissal claim.

    Returns the violations (an empty list = one answer per door).
    """
    claims = _dismissal_claims(live)
    out: list[str] = []
    if "affirms" in claims["overview"]:
        out.append("the Overview door carries no dismissal in the shipped "
                   "dashboard (`OverviewNextActionCard`), but the row affirms one")
    if "denies" not in claims["overview"]:
        out.append("the row must state that the Overview door carries NO "
                   "dismissal — that is the claim the map corrected")
    if "denies" in claims["settings"]:
        out.append('the decision recorded for the Settings door IS a '
                   'dismissible-hide with a re-open path (row "Layout: Setup '
                   'guide"), but the row denies it')
    if "denies" in claims.get("*", set()) and "affirms" in claims["settings"]:
        out.append("a door-LESS denial (\"neither card ...\" / \"no door ...\") "
                   "contradicts the Settings door's affirmed dismissible-hide")
    return out


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
    block = m.group(0).lower()
    assert "the settings setup guide is the counted checklist card" in block, (
        "the plan must state that the Settings Setup guide IS the counted "
        "checklist card (its role), not only that it renders the same state")
    assert "the overview next action is one next step" in block, (
        "the plan must state the Overview next action's role (ONE next step + a "
        "CTA), not only that it mirrors the state")
    assert not _dismissal_claim_conflicts(block), (
        "the plan's DE2E-6 gives two answers for one dismissal claim:\n- "
        + "\n- ".join(_dismissal_claim_conflicts(block)))


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


def test_the_dismissal_door_guard_rejects_the_one_sentence_contradiction():
    """Non-vacuity, and the RECEIPT for why the previous guard was too narrow.

    The reviewed head's live row said the Settings card "collapses to compact
    status / dismissible-hide with re-open path when complete" and then "neither
    card carries a dismissal" — both claims in one sentence. The literal-only
    assertion above (`"dismissible checklist card" not in live`) passed it,
    because the contradiction lives in the two doors, not in one phrase.
    """
    prefix_head = (
        "Dashboard renders the SAME state on both of its doors — the Settings "
        "Setup guide is the counted checklist card (collapses to compact status "
        "/ dismissible-hide with re-open path when complete, per row \"Layout: "
        "Setup guide\"); the Overview next action mirrors that state as ONE next "
        "step + a CTA into the guide — never a counted checklist of its own, "
        "and neither card carries a dismissal. ")
    assert _dismissal_claim_conflicts(prefix_head), (
        "the guard passes the exact self-contradicting sentence it exists for")
    assert any("door-LESS" in c for c in _dismissal_claim_conflicts(prefix_head))
    # a row that never says what the Overview door does is the same defect from
    # the other side: silence let the next action be read as the checklist.
    silent = ("the Settings Setup guide is the counted checklist card "
              "(dismissible-hide with re-open path when complete)")
    assert _dismissal_claim_conflicts(silent), (
        "a row that never states the Overview door's answer must not pass")
    # and the corrected shape must be clean, in both doors' presence
    good = ("the Settings Setup guide is the counted checklist card, and the "
            "decision recorded for THAT door is dismissible-hide with a "
            "re-open path when complete; the Overview next action mirrors that "
            "state as ONE next step + a CTA and carries no dismissal")
    assert not _dismissal_claim_conflicts(good), _dismissal_claim_conflicts(good)

    # The two counterexamples the round-4 review MEASURED on the pre-fix guard,
    # which read polarity from ANY negation anywhere in the clause. They are kept
    # as in-suite rejection cases so the rule cannot silently regress to it.
    # (a) FAIL-OPEN: the row's own "never a counted checklist" supplied the
    #     negation, so a row AFFIRMING a dismissible Overview passed the guard.
    regressed = (
        "the Settings Setup guide is the counted checklist card and the decision "
        "recorded for THAT door is dismissible-hide with a re-open path; the "
        "Overview next action mirrors that state as ONE next step + a CTA into the "
        "guide — never a counted checklist of its own — and the Overview door is "
        "dismissible once complete")
    assert _dismissal_claim_conflicts(regressed), (
        "the guard passes a row that AFFIRMS a dismissible Overview door: a "
        "negation elsewhere in the clause must not deny the door's own claim")
    # (b) FALSE POSITIVE: a correct row whose Settings clause mentions "no hide"
    #     was rejected as denying the Settings door's recorded dismissible-hide.
    correct_with_no_hide = (
        "the Settings Setup guide is the counted checklist card — its recorded "
        "decision is dismissible-hide with a re-open path, though the shipped card "
        "implements no hide; the Overview next action carries no dismissal")
    assert not _dismissal_claim_conflicts(correct_with_no_hide), (
        "the guard rejects a correct row because 'implements no hide' reads as a "
        "denial of the Settings door: "
        + str(_dismissal_claim_conflicts(correct_with_no_hide)))

    # Round-5 counterexamples. (b) above was only half the fail-open: bounding the
    # negation scan to 24 chars still DENIED these, because the unrelated negation
    # only had to be nearer than "dismiss".
    for nearby_negation in (
        "the Overview card has no toggle and is dismissible once complete",
        "the Overview next action is not a checklist and is dismissible",
        "the Overview card is not a counted checklist but it is dismissible",
    ):
        assert _dismissal_claim_conflicts(nearby_negation), (
            "a negation attached to some OTHER noun (a toggle, a checklist) must "
            f"not deny the door's dismissal claim: {nearby_negation!r}")

    # ... and the converse: idiomatic denials that name no "no"/"not" adjacent to
    # "dismiss" were read as AFFIRMATIONS by the clause/proximity scans.
    for idiomatic_denial in (
        "the Overview card cannot be dismissed",
        "nothing on the Overview is dismissible",
        "the Overview card is undismissable",
    ):
        assert not _dismissal_claim_conflicts(idiomatic_denial), (
            f"a denial phrased {idiomatic_denial!r} must read as a denial, not an "
            "affirmation: " + str(_dismissal_claim_conflicts(idiomatic_denial)))

    # A door named by its COMPONENT must be seen, and a denial of its recorded
    # dismissal must fail. With a phrase-only door table the Settings door was
    # invisible here and the row passed.
    by_component = ("the Settings-tab `SetupGuideCard` is not dismissible; "
                    "the Overview next action carries no dismissal")
    assert _dismissal_claim_conflicts(by_component), (
        "a row denying the Settings door's recorded dismissible-hide via the "
        "component name must fail")
    assert any("Settings door IS a dismissible-hide" in c
               for c in _dismissal_claim_conflicts(by_component)), (
        "the component-name door shape is not attributed to the Settings door: "
        + str(_dismissal_claim_conflicts(by_component)))
    # ... while the component name inside an OVERVIEW sentence stays the Overview's
    # claim. Matching `SetupGuideCard` standalone attributed the Overview's denial
    # to the Settings door and failed the correct live row.
    overview_mentions_component = (
        "the Settings Setup guide is the counted checklist card and its recorded "
        "decision is dismissible-hide with a re-open path; the Overview next "
        "action is ONE next step + a CTA and carries no dismissal (the shipped "
        "`SetupGuideCard` collapses to a status today and implements no hide)")
    assert not _dismissal_claim_conflicts(overview_mentions_component), (
        "a component name mentioned inside the Overview's clause must not be "
        "read as a claim about the Settings door: "
        + str(_dismissal_claim_conflicts(overview_mentions_component)))


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

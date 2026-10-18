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

Every surface is also exercised by at least one high-level E2E row (the E2E-side
reverse direction) — a surface no scenario touches has no scenario that would
catch its regression.

NON-VACUITY
-----------
Checks 4, 5, 6 and 8 fail on the pre-fix document at `1758bbe59` (each
docstring names the pre-fix text); checks 1-3 are the standing invariants the
earlier cycles established, check 9 is a parser-shape guard and check 7 is a
guard whose detector is proved non-blind — guard rails, not defect pins. The
pre-fix blobs are replayed by pointing this module's pure functions at them
(`git show <sha>:<path>`), which is how the non-vacuity claims were measured.
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


def test_every_surface_is_exercised_by_at_least_one_e2e_row():
    """The E2E-side reverse direction: a surface no high-level scenario walks has
    no scenario that would catch its regression.

    This replaces an assertion that was implied by the capability-direction check
    above (`{n for c in ...} <= set(_surfaces())` holds whenever the union equals
    the surface set) and could never fail independently while reading as a second
    direction."""
    exercised = set().union(*_e2e_rows().values())
    assert exercised == set(_surfaces()), (
        f"surfaces no E2E row exercises: {sorted(set(_surfaces()) - exercised)}")


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

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
   it produced two answers.
5. **An E2E row cites every surface that owns a door it exercises.** The
   pre-fix defect: `E2E-6 graph-held resumption` was `1, 2, 10` while the scope
   defines that re-entry as "the Settings Setup guide **or the Continue-setup
   card**" — the card being surface 9's next-action element, which the same
   document had just written into surface 9's contract.
6. **The counted/dismissible checklist belongs to surface 10, not surface 9.**
   The pre-fix defect: surface 9's contract claimed "a dismissible checklist
   rendered from `OnboardingState`.completed_steps" — but the shipped Overview
   next-action element is ONE calm next step plus a CTA into the Settings Setup
   guide (`website/apps/dashboard/src/main.jsx`, `OverviewNextActionCard`), the
   counted N-of-M checklist is `SetupGuideCard` on the Settings tab, and
   neither card carries a dismissal (the only `dismiss` hits are the recovery,
   claim and billing banners). Surfaces 9/10 shipped under #2000/#2001, both
   CLOSED, so a map that contradicts the shipped dashboard is silent drift.
7. **No orphan `)` after a code span** — the review reported
   `rendered from `OnboardingState`).completed_steps`; the reviewed head had
   already dropped the stray paren, so this is a standing GUARD with the
   detector's non-blindness asserted rather than a pre-fix defect pin.
8. **The checklist child issues consume carries the obligation the surface table
   states** — a projection obligation that lives only in the table is one a card
   implementation can never fail.

NON-VACUITY
-----------
Checks 4, 5, 6 and 8 fail on the pre-fix document at `1758bbe59` (each
docstring names the pre-fix text); checks 1-3 are the standing invariants the
earlier cycles established and check 7 is a guard whose detector is proved
non-blind — guard rails, not defect pins. Static: doc-text scans plus one file
read per doc. No network, DB or graph.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EPIC = ROOT / "docs" / "epics" / "2026-08-29-agent-driven-onboarding-1976"
DOC = EPIC / "04-test-design.md"
SCOPE = EPIC / "03-scope.md"

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
        if len(cells) > 7:
            # Two pre-existing pipe shapes inflate the column count: surface 1's
            # ESCAPED pipes stay inside one cell (handled by `_cells`), while
            # surface 4's one unescaped pipe — the file's baselined MD056 —
            # splits whatever cell holds `fork: 'self'|'build'` (its NAME). So
            # locate the Data Flow column by its closed vocabulary and rebuild
            # the row around it rather than assuming a fixed index.
            flow = next(
                (i for i, c in enumerate(cells)
                 if i >= 2 and c in {"Both", "Write", "Out"}),
                None,
            )
            assert flow is not None and flow + 3 < len(cells), (
                f"unparseable surface row: {line[:80]}")
            cells = [
                cells[0],                       # #
                "|".join(cells[1 : flow - 1]),  # Surface (re-joined)
                cells[flow - 1],                # Type
                cells[flow],                    # Data Flow
                cells[flow + 1],                # Test Layer
                "|".join(cells[flow + 2 : -1]),  # Contract (re-joined)
                cells[-1],                      # Key Failure Modes
            ]
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
    """Small integers that stand alone — not part of a filename (`06-plan.md`),
    a line reference (`:184`) or a hyphenated slug (`05-ux-decisions.md`)."""
    return {
        int(n)
        for n in re.findall(r"(?<![\w.:-])(\d{1,2})(?![\w.:-])", text)
    }


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
    # and the reverse: every capability's surfaces exist
    assert {n for c in _capabilities().values() for n in c[0]} <= set(_surfaces())


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
    capability. Post-fix the row is `9, 10, 17`."""
    capabilities = _capabilities()
    for prefix in ("W5 setup card", "W9 trigger/entry", "W7 fusion",
                   "W2 universal command"):
        cited = capabilities[prefix][0]
        named = _standalone_numbers(_section_text(prefix))
        assert named <= set(_surfaces()), (
            f"{prefix}: rationale names {sorted(named - set(_surfaces()))}, "
            "which are not surfaces")
        assert named <= cited, (
            f"{prefix}: the rationale names surface(s) {sorted(named - cited)} "
            f"but the row cites only {sorted(cited)} — a reader reconciling "
            "row and prose gets two answers")


def test_e2e6_cites_every_surface_that_owns_a_reentry_door():
    """PRE-FIX: `E2E-6 graph-held resumption | 1, 2, 10` omitted surface 9, the
    Overview element the document itself called the Continue-setup card — the
    scope's second door in "via the Settings Setup guide **or the Continue-setup
    card**" (`03-scope.md` E2E-6). Post-fix surface 9 is cited (`1, 2, 9, 10`).

    The door set is derived from the map's own Contract column, so the pin
    follows a door that MOVES surfaces instead of hard-coding where it lives.
    """
    surfaces = _surfaces()
    doors = {
        n for n, s in surfaces.items()
        if "Setup guide" in s["contract"] or "Continue-setup" in s["contract"]
    }
    assert doors, "no surface owns a Setup-guide door — the scan is broken"
    assert 10 in doors, (
        "surface 10 owns the Settings Setup guide — the door scan found "
        f"{sorted(doors)}")
    cited = _e2e_rows()["E2E-6"]
    assert doors <= cited, (
        f"E2E-6 cites {sorted(cited)} but the re-entry doors are on "
        f"{sorted(doors)} — {sorted(doors - cited)} missing")


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

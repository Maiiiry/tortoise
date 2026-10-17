"""#8845 — a stale registry row must not turn one label into two lane records.

The observed defect (measured 2026-10-17): ``fleet_state.build_state`` emitted TWO
lane records for the label ``2b Extractor`` — one BOUND row (live workspace, worktree,
branch, head) and one leftover row whose workspace id was absent from the live cmux
list and which bound nothing at all. ``collision_preflight`` reads a repeated label as
unreadable by construction (``lanes_by_label`` is last-wins, so a live record followed
by a stopped one could answer a false CLEAN), so the whole preflight went
INCOMPLETE/exit 2 — "do NOT dispatch" — for every issue attributed to that lane.

Each fact below fails under a NAMED mutation:

1. ``[stale workspace, live workspace]`` for ONE label  ->  exactly ONE lane record,
   and the claim gate does NOT report the label as duplicated.
   Mutation: delete the redundant-row filter in ``build_state`` -> two lane records,
   ``MORE THAN ONE lane record`` -> red.
2. TWO **BOUND** records sharing a label  ->  still ``HOLDER_UNMEASURED``.
   Mutation: delete the ``duplicated`` check in
   ``collision_preflight._holders_from_board`` -> the last-wins record answers -> red.
3. The dropped row is REPORTED (``orphans``, kind ``registry-stale``), never silently
   erased — and ``fleet_state lane <stale-ws-id>`` names the real reason instead of
   manufacturing "no lane matching".
4. A label whose ONLY row is a gone-workspace row is NOT dropped: #7738 publishes
   exactly that lane as RETIRED/RECOVERABLE, and erasing it would lose the lane.
   Mutation: drop every unbound row unconditionally -> red.
5. An UNREADABLE cmux list drops nothing (fail open, #7738's posture): a failed read
   says nothing about whether the workspace still exists.
   Mutation: ignore ``workspaces_measured`` -> red.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import fleet_state as fs

ISSUE = 8818
LABEL = "2b Extractor"
WS_STALE = "69A56B48-7F11-41E0-9926-0382648C4F5F"
WS_LIVE = "3D67CF8A-444C-47E8-B335-E72E42ABF25D"
WS_LIVE_2 = "7AF6EB2E-1E4E-48E2-8D2A-AC61306B46DB"
WT_1 = "/repo/.worktrees/8818-pin-prose"
WT_2 = "/repo/.worktrees/8818-sweep"
HEAD = "12e1a17300000000000000000000000000000000"

_CP = None


def collision_preflight():
    """`tools/collision_preflight.py` (hyphenated name) — loaded once, as its tests do."""
    global _CP
    if _CP is None:
        spec = importlib.util.spec_from_file_location(
            "collision_preflight_8845", ROOT / "tools" / "collision_preflight.py"
        )
        module = importlib.util.module_from_spec(spec)
        # Register BEFORE exec: dataclasses resolve annotations via
        # `sys.modules[cls.__module__]` while the module body runs.
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _CP = module
    return _CP


def _build(
    monkeypatch,
    *,
    registry,
    live,
    measured=True,
    worktrees=None,
    bindings=None,
    sessions=None,
    overrides=None,
    error="",
):
    """`build_state` over a synthesised registry + cmux list; every other source empty."""
    monkeypatch.setattr(fs, "load_registry", lambda: list(registry))
    monkeypatch.setattr(
        fs, "cmux_workspaces", lambda: fs.WorkspaceMap(live, measured=measured, error=error)
    )
    monkeypatch.setattr(fs, "pane_bindings", lambda: dict(bindings or {}))
    monkeypatch.setattr(fs, "live_sessions", lambda: dict(sessions or {}))
    monkeypatch.setattr(fs, "load_overrides", lambda: dict(overrides or {}))
    monkeypatch.setattr(fs, "session_index", lambda: ({}, {}))
    monkeypatch.setattr(fs, "worktrees", lambda _root: dict(worktrees or {}))
    issue = {"number": ISSUE, "title": "an issue", "state": "open",
             "assignees": [], "labels": [], "url": "https://example.invalid/8818"}
    monkeypatch.setattr(fs, "open_prs", lambda _repo, limit=400: fs.GitHubRows([]))
    monkeypatch.setattr(
        fs, "open_issues", lambda _repo, limit=800: fs.GitHubRows([issue])
    )
    return fs.build_state(
        repo="o/r", repo_root="/repo", orch_ws="", sample_s=0.0, do_pane=False
    )


def _duplicate_pair(monkeypatch, **kwargs):
    """The measured #8845 shape: one bound row + one stale row, ONE label."""
    return _build(
        monkeypatch,
        registry=[(LABEL, WS_STALE, ""), (LABEL, WS_LIVE, "")],
        live={WS_LIVE: {"cwd": WT_1}},
        worktrees={WT_1: {"branch": "refs/heads/fix/8818-pin-prose", "head": HEAD}},
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 1. the defect: two records -> one
# ---------------------------------------------------------------------------


def test_a_stale_unbound_row_is_not_a_lane_record(monkeypatch) -> None:
    st = _duplicate_pair(monkeypatch)

    lane_labels = [lane["identity"]["lane"] for lane in st["lanes"]]
    assert lane_labels == [LABEL], lane_labels
    assert st["summary"]["lanes"] == 1
    assert st["summary"]["stale_registry"] == 1

    (lane,) = st["lanes"]
    # The SURVIVING record is the one that binds evidence — not "whichever the
    # registry happened to list last".
    assert lane["identity"]["workspace"] == WS_LIVE
    assert lane["identity"]["worktree"] == WT_1
    assert lane["identity"]["branch"] == "fix/8818-pin-prose"
    assert lane["identity"]["head_sha"] == HEAD


def test_the_claim_gate_does_not_read_the_label_as_duplicated(monkeypatch) -> None:
    """The acceptance criterion: the gate answers instead of going INCOMPLETE.

    `_holders_from_board` is where a repeated label becomes `HOLDER_UNMEASURED`
    with "MORE THAN ONE lane record"; with one record it must classify the lane
    instead (here: liveness UNMEASURED, because the fixture has no session store —
    an honest answer, and NOT the duplication refusal).
    """
    cp = collision_preflight()
    st = _duplicate_pair(monkeypatch)

    assert st["index"]["issues"][str(ISSUE)]["lanes"] == [LABEL], st["index"]
    (holder,) = cp._holders_from_board(st, ISSUE)
    assert "MORE THAN ONE lane record" not in holder.reason, holder.reason
    assert holder.verdict == cp.HOLDER_UNMEASURED
    assert "records no session" in holder.reason, holder.reason


# ---------------------------------------------------------------------------
# 2. the fail-closed that must SURVIVE: two BOUND records, one label
# ---------------------------------------------------------------------------


def test_two_bound_records_sharing_a_label_stay_unmeasured(monkeypatch) -> None:
    cp = collision_preflight()
    st = _build(
        monkeypatch,
        registry=[(LABEL, WS_LIVE, ""), (LABEL, WS_LIVE_2, "")],
        live={WS_LIVE: {"cwd": WT_1}, WS_LIVE_2: {"cwd": WT_2}},
        worktrees={
            WT_1: {"branch": "refs/heads/fix/8818-pin-prose", "head": HEAD},
            WT_2: {"branch": "refs/heads/fix/8818-sweep", "head": HEAD},
        },
    )
    # No row was dropped: both bind a live workspace/worktree/branch.
    assert [lane["identity"]["workspace"] for lane in st["lanes"]] == [WS_LIVE, WS_LIVE_2]
    assert st["summary"]["stale_registry"] == 0

    holders = cp._holders_from_board(st, ISSUE)
    assert holders, "the label is attributed to the issue"
    for holder in holders:
        assert holder.verdict == cp.HOLDER_UNMEASURED
        assert "MORE THAN ONE lane record" in holder.reason, holder.reason


# ---------------------------------------------------------------------------
# 3. still visible: reported, never erased
# ---------------------------------------------------------------------------


def test_the_dropped_row_is_reported_as_an_orphan(monkeypatch) -> None:
    st = _duplicate_pair(monkeypatch)

    (stale,) = [o for o in st["orphans"] if o.get("kind") == "registry-stale"]
    assert stale["lane"] == LABEL
    assert stale["workspace"] == WS_STALE
    assert LABEL in stale["reason"]
    assert WS_STALE[:8] in stale["reason"], stale["reason"]
    assert "not a lane record" in stale["reason"]

    # ...and a lookup by the stale workspace id says WHAT is wrong, instead of
    # manufacturing "no lane matching" (the name DOES exist; it is not a lane).
    assert fs._stale_registry_match(st, WS_STALE)["lane"] == LABEL
    assert fs._stale_registry_match(st, WS_STALE.lower())["lane"] == LABEL
    assert fs._stale_registry_match(st, "someone else") is None
    assert fs._find_lane(st, WS_STALE) is None


# ---------------------------------------------------------------------------
# 4. the anti-over-drop guard: a label's ONLY row is kept (#7738)
# ---------------------------------------------------------------------------


def test_a_lone_ghost_row_is_still_reported_as_the_lane(monkeypatch) -> None:
    """#7738: a gone workspace says nothing about whether a pi process outlived it.

    Dropping this row would erase a lane the board publishes as RETIRED, so the
    filter must require that the SAME label has a record that binds evidence.
    """
    st = _build(
        monkeypatch,
        registry=[(LABEL, WS_STALE, "")],
        live={WS_LIVE: {"cwd": WT_1}},
        worktrees={WT_1: {"branch": "refs/heads/fix/8818-pin-prose", "head": HEAD}},
    )
    (lane,) = st["lanes"]
    assert lane["identity"]["lane"] == LABEL
    assert lane["identity"]["workspace"] == WS_STALE
    assert lane["terminal"]["state"] == "RETIRED"
    assert st["summary"]["stale_registry"] == 0
    assert not [o for o in st["orphans"] if o.get("kind") == "registry-stale"]


def test_a_surviving_session_keeps_the_ghost_as_a_lane(monkeypatch) -> None:
    """The same guard through the OVERRIDE rule: a re-bindable ghost is a lane."""
    sid = "c" * 36
    st = _build(
        monkeypatch,
        registry=[(LABEL, WS_STALE, ""), (LABEL, WS_LIVE, "")],
        live={WS_LIVE: {"cwd": WT_1}},
        worktrees={WT_1: {"branch": "refs/heads/fix/8818-pin-prose", "head": HEAD}},
        overrides={LABEL: sid},
        sessions={WS_STALE.upper(): [{"pid": 999999, "agent_lifecycle": "running",
                                     "session_id": sid}]},
    )
    # An override is a human's statement that the label is a lane, and it is the only
    # rule that can resolve a ghost's session — so the row is NOT dropped, and the
    # duplicate stays visible to the gate (fail closed, by design).
    assert [lane["identity"]["workspace"] for lane in st["lanes"]] == [WS_STALE, WS_LIVE]
    assert st["summary"]["stale_registry"] == 0


# ---------------------------------------------------------------------------
# 5. fail open: an unreadable cmux list drops NOTHING
# ---------------------------------------------------------------------------


def test_an_unreadable_workspace_list_drops_nothing(monkeypatch) -> None:
    st = _build(
        monkeypatch,
        registry=[(LABEL, WS_STALE, ""), (LABEL, WS_LIVE, "")],
        live={},
        measured=False,
        error="cmux list-workspaces returned an empty set",
    )
    assert st["reconciliation"]["workspaces_measured"] is False
    assert len(st["lanes"]) == 2, "an unread list must retire nothing (#7738)"
    assert st["summary"]["stale_registry"] == 0
    assert not [o for o in st["orphans"] if o.get("kind") == "registry-stale"]


# ---------------------------------------------------------------------------
# 6. the review's P1: the OVERRIDE arm must not resurrect the duplicate
# ---------------------------------------------------------------------------


def test_a_label_override_does_not_make_the_stale_row_a_lane_record(monkeypatch) -> None:
    """`overrides` is keyed by LABEL, so it cannot answer a per-ROW question.

    Consulted at row level it answers identically for every row sharing the label:
    once ANY override existed, the stale row would "bind evidence" too, nothing
    would be dropped, and the duplicate — with its exit-2 gate — would come back.
    Mutating `row_binds_evidence` to also consult `overrides.get(label)` reds this.
    """
    st = _build(
        monkeypatch,
        registry=[(LABEL, WS_STALE, ""), (LABEL, WS_LIVE, "")],
        live={WS_LIVE: {"cwd": WT_1}},
        worktrees={WT_1: {"branch": "refs/heads/fix/8818-pin-prose", "head": HEAD}},
        # a DANGLING override: it names the stale workspace, and still must not
        # promote that row to a lane record.
        overrides={LABEL: WS_STALE},
    )
    assert [lane["identity"]["lane"] for lane in st["lanes"]] == [LABEL]
    assert st["summary"]["lanes"] == 1
    assert st["lanes"][0]["identity"]["workspace"] == WS_LIVE
    assert st["summary"]["stale_registry"] == 1


def test_a_lone_row_with_an_override_is_still_kept(monkeypatch) -> None:
    """The override keeps its own meaning (#7738): a lone gone-workspace row stays."""
    st = _build(
        monkeypatch,
        registry=[(LABEL, WS_STALE, "")],
        live={},
        overrides={LABEL: WS_STALE},
    )
    assert [lane["identity"]["lane"] for lane in st["lanes"]] == [LABEL]
    assert st["summary"]["stale_registry"] == 0


# ---------------------------------------------------------------------------
# 7. the review's P3: a dropped row must not take `lane_label` with it
# ---------------------------------------------------------------------------


def test_a_dropped_rows_issue_label_survives_on_the_surviving_row(monkeypatch) -> None:
    """`issue_label` reaches the emitted record as `claim.lane_label`.

    Only the stale row carries it here, so dropping that row without inheritance
    silently blanks the lane's `lane:` label.
    """
    st = _build(
        monkeypatch,
        registry=[(LABEL, WS_STALE, "lane:2b-extractor"), (LABEL, WS_LIVE, "")],
        live={WS_LIVE: {"cwd": WT_1}},
        worktrees={WT_1: {"branch": "refs/heads/fix/8818-pin-prose", "head": HEAD}},
    )
    (lane,) = st["lanes"]
    assert lane["claim"]["lane_label"] == "lane:2b-extractor"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))

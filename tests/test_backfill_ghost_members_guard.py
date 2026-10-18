"""#5188 — the invite-ghost backfill gates on the graph it ACTUALLY writes.

``graph-scripts/backfill_invite_ghost_members.py`` sweeps the registry
namespace, which the SDK's ``registry`` namespace resolves to
``registry_control_plane`` regardless of the URI path. Gating on the URI-path
name meant a test-prefixed ``--uri`` auto-approved a DETACH+DELETE sweep of the
shared registry graph — the bypass #5188 tracks.

These pins are HERMETIC: the SDK is faked, so no server is contacted and no
sweep ever runs destructively. The regression shape is a test-prefixed URI
path whose RESOLVED name is not test-prefixed (must refuse without ``--yes``),
plus the converse (a genuinely test-prefixed resolved name still auto-approves).
"""
from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest

# graph-scripts/ is a hyphenated (namespace) dir — import the backfill script
# via path insert (AGENTS.md sibling-import convention; same as
# tests/test_issue_4010_sessions_unlimited.py).
_GRAPH_SCRIPTS = str(Path(__file__).resolve().parent.parent / "graph-scripts")
if _GRAPH_SCRIPTS not in sys.path:
    sys.path.insert(0, _GRAPH_SCRIPTS)

# `test_guard` is reached as a MODULE attribute, never imported by name: a bare
# `test_guard` import would be COLLECTED by pytest as a test, since the helper's
# name matches the test pattern (same note as the #4010 sibling test).
import backfill_invite_ghost_members as backfill  # noqa: E402


def _install_fake_sdk(monkeypatch, resolved_name: str, *,
                      name_only: str | None = None,
                      supabase: bool = False) -> dict:
    """Fake the registry SDK so the guard's INPUT and ORDERING are observable.

    ``name_only`` is what the NAME-ONLY derivation returns (defaults to
    ``resolved_name``); ``resolved_name`` is what OPENING the graph returns. A
    difference drives #5233's "guard approved X but the SDK resolved Y" refusal.

    ``_get_registry`` RECORDS that it was called: a refused run must never reach
    it, because that is the call that MATERIALIZES the graph (#5233) —
    ``_get_proj`` draws the main registry graph and ``_ensure_registry_indexes``
    the control plane, and FalkorDB auto-creates a missing graph on any query.
    """
    calls: dict = {"order": []}

    class _FakeSDK:
        def __init__(self, *args, **kwargs):
            calls["sdk_kwargs"] = kwargs
            calls["order"].append("ctor")

        def _registry_graph_name(self):
            calls["order"].append("name_only")
            return name_only if name_only is not None else resolved_name

        def _get_registry(self):
            calls["get_registry"] = True     # the MATERIALIZING call (#5233)
            calls["order"].append("get_registry")
            return SimpleNamespace(name=resolved_name)

        def sweep_invite_ghost_memberships(self, *, dry_run=False):
            calls["sweep_dry_run"] = dry_run
            calls["order"].append("sweep")
            return {"found": 0, "ghosts": 0, "deleted": 0}

        def close(self):
            calls["closed"] = True
            calls["order"].append("close")

    monkeypatch.setattr("tortoise.sdk.TortoiseSDK", _FakeSDK)
    if supabase:
        import tortoise.supabase_control as sc
        monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
    return calls


def _run_main(monkeypatch, argv: list[str]) -> tuple[str, SystemExit | None]:
    """Run the script's ``main()`` with a captured stdout.

    Returns ``(output, SystemExit-or-None)`` so a refusal's message is still
    assertable (``main()`` returns 0; the guard's refusal raises SystemExit).
    """
    monkeypatch.setattr(sys, "argv", ["backfill_invite_ghost_members", *argv])
    out = io.StringIO()
    exc: SystemExit | None = None
    try:
        with redirect_stdout(out):
            backfill.main()
    except SystemExit as e:
        exc = e
    return out.getvalue(), exc


def test_guard_refuses_a_non_test_resolved_name_without_yes():
    """The pure guard. `registry_control_plane` is never test-prefixed."""
    with pytest.raises(SystemExit):
        backfill.test_guard("registry_control_plane", yes=False)
    backfill.test_guard("registry_control_plane", yes=True)  # explicit consent
    backfill.test_guard("test_backfill_guard")  # test-scoped, no consent


def test_the_uri_path_name_would_have_auto_approved():
    """The bypass SHAPE, pinned: the URI-path name `test_bypass` passes the
    guard — which is exactly why the gate must never be fed it (#5188)."""
    backfill.test_guard("test_bypass")  # the URI path — would auto-approve
    with pytest.raises(SystemExit):
        backfill.test_guard("registry_control_plane")  # what the sweep writes


def test_test_prefixed_uri_does_not_auto_approve_the_shared_registry(monkeypatch):
    """#5188 regression: a `test_*` URI PATH must not authorise a
    `registry_control_plane` sweep. The guard sees the RESOLVED name and
    refuses; the dry-run never reaches the sweep."""
    calls = _install_fake_sdk(monkeypatch, "registry_control_plane")
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    out, exc = _run_main(
        monkeypatch,
        ["--dry-run", "--uri", "docker://:falkordb@localhost:6379/test_bypass"])
    assert exc is not None and exc.code == 1, out
    assert "Target graph is 'registry_control_plane'" in out
    # Non-destructive: the guard refused BEFORE the sweep, and it was fed the
    # SDK-resolved name, never the test-prefixed URI path.
    assert "sweep_dry_run" not in calls
    assert calls.get("closed") is True  # the finally still closes the SDK
    # #5233: and before the MATERIALIZING call — a refused run must create no
    # graph at all. `_get_registry()` draws `registry_tortoise` +
    # `registry_control_plane` (index DDL auto-creates a missing graph).
    assert calls.get("get_registry") is not True, calls["order"]
    assert calls["order"] == ["ctor", "name_only", "close"], calls["order"]


# ── #5233: the guard runs BEFORE any materializing call ─────────────────────


def test_guard_reaches_the_sweep_only_after_the_materializing_call(monkeypatch):
    """The converse of the refusal pin: when the guard PASSES, `_get_registry`
    is reached — proving the ordering assertion above is non-vacuous (it fails
    because the guard refused, not because the script never calls it)."""
    calls = _install_fake_sdk(monkeypatch, "test_ghost_backfill")
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    out, exc = _run_main(
        monkeypatch,
        ["--dry-run", "--uri", "docker://:falkordb@localhost:6379/whatever"])
    assert exc is None, out
    assert calls["order"] == ["ctor", "name_only", "get_registry", "sweep",
                              "close"], calls["order"]


def test_guard_approved_name_must_match_the_opened_graph(monkeypatch):
    """#5233: the guard approves a NAME; the SDK opens a graph. If they differ,
    refuse — never sweep a graph the operator did not consent to (#5188's
    contract). Driven by a `name_only` that would auto-approve while the opened
    graph is the shared registry."""
    calls = _install_fake_sdk(monkeypatch, "registry_control_plane",
                              name_only="test_ghost_backfill")
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.setattr(
        sys, "argv",
        ["backfill_invite_ghost_members", "--dry-run", "--uri",
         "docker://:falkordb@localhost:6379/whatever"])
    out = io.StringIO()
    with redirect_stdout(out):
        rc = backfill.main()
    assert rc == 1, out.getvalue()
    assert "the guard approved 'test_ghost_backfill'" in out.getvalue()
    assert "but the SDK resolved 'registry_control_plane'" in out.getvalue()
    assert "sweep_dry_run" not in calls


# ── #5233: the Supabase-lane refusal (mirrors the #4010 sweep's) ────────────


def test_supabase_mode_refuses_without_opening_anything(monkeypatch):
    """A Supabase-mode control plane has no registry graph — opening the
    namespace auto-creates the graph the #669 flip deleted. The refusal must run
    BEFORE the SDK is even constructed."""
    calls = _install_fake_sdk(monkeypatch, "registry_control_plane",
                              supabase=True)
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    out, exc = _run_main(
        monkeypatch,
        ["--dry-run", "--uri", "docker://:falkordb@localhost:6379/whatever"])
    assert exc is None, out
    assert "control plane is Supabase" in out
    assert calls["order"] == [], calls["order"]  # no SDK, no graph


def test_undeterminable_mode_fails_closed_on_a_broken_import(monkeypatch):
    """The import-failure arm (the module-state variant) — refusing matters
    because the real resolution would otherwise PROCEED to the registry."""
    monkeypatch.setitem(sys.modules, "tortoise.supabase_control", None)
    refusal = backfill._supabase_lane_refusal()
    assert refusal is not None and "could not be determined" in refusal


def test_registry_mode_does_not_refuse(monkeypatch):
    """The non-vacuous converse: a registry-mode control plane proceeds."""
    import tortoise.supabase_control as sc
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: False)
    assert backfill._supabase_lane_refusal() is None


def test_resolved_test_prefixed_name_still_auto_approves(monkeypatch):
    """The converse: a genuinely test-prefixed RESOLVED name needs no `--yes`,
    whatever the URI path says — so the guard keys on the resolved name."""
    calls = _install_fake_sdk(monkeypatch, "test_registry_control_plane")
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    out, exc = _run_main(
        monkeypatch,
        ["--dry-run", "--uri", "docker://:falkordb@localhost:6379/whatever"])
    assert exc is None, out
    assert "Test graph detected (test_registry_control_plane)" in out
    assert calls.get("sweep_dry_run") is True
    assert calls.get("closed") is True

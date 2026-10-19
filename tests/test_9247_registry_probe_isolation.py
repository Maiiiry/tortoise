"""#9247 — a stale registry-probe target must not cross a test boundary.

The registry probe (``tortoise.hosted_api._REGISTRY_PROBE_SDK``) is a
PROCESS-lifetime handle cached by the ``TORTOISE_DB_URI`` STRING (#4661). That
key is sound in production, where one process talks to one server for its whole
life. It is NOT sound inside a test session: the #1647 URI-aware redirect maps
the SAME uri string onto a PER-TEST server graph (``test_<stem>_<hash of
session + path + graph name>``), so two tests share a uri while addressing
DIFFERENT graphs. The cache is only honest per test because the suite drops it
at the test boundary.

It was NOT honest, because the drop was one-sided. ``_sweep_events``
(``tortoise/hosted_api.py``, submitted to the monitoring pool at boot — i.e.
once per ``TestClient`` lifespan) calls ``_registry_existing_graphs()`` →
``_registry_probe_sdk()`` on a BACKGROUND thread, so the cache can be
repopulated a few milliseconds after the previous test's reset, with that
previous test's target. The next test then inherits it, and every read through
``_registry_anchor()`` answers from the wrong graph:

  * ``_require_owner_admin`` → ``_membership_role`` → ``_registry_anchor()``
    finds no membership and returns 403 — the
    ``test_session_key_http.py::TestSessionKeyMintConcurrency::
    test_registry_mint_blocks_while_team_lock_held`` red ("registry mint
    proceeded while the team lock was held ... Requires owner or admin role in
    team");
  * ``list_my_orgs`` → ``_org_node`` → ``_registry_anchor()`` returns None for
    the team the test just created, the row is skipped, and the org is missing
    from ``GET /v1/organizations`` — the
    ``test_onboarding_endpoints.py::TestOnboardingTeam::
    test_create_team_then_session_key_mint_registry`` red.

Both reds are READS through ``_registry_anchor()``, which is why two different
files reddened together on one shard while each passed alone, and why the
failure moved with the schedule instead of staying on one nodeid.

This module pins the OUTCOME the two nodeids depend on: a test's anchor resolves
THIS test's target, never the one a late background build left behind. The
polluting handle is a REAL ``TortoiseSDK`` bound to a foreign target, installed
exactly where the background build installs it — into the process-global cache,
before the test takes its own target.
"""
from __future__ import annotations

import os

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import pytest  # noqa: F401

import tortoise.hosted_api as ha
from tests._http_fixtures import patched_tortoise_sdk
from tortoise.hosted_api import _make_sdk
from tortoise.sdk import TortoiseSDK


def _leave_a_late_background_build_behind(tmp_path) -> None:
    """Cache a registry probe bound to a FOREIGN target.

    This is ``_sweep_events`` → ``_registry_existing_graphs()`` →
    ``_registry_probe_sdk()`` running on the monitoring pool thread between two
    tests: it builds a handle, and the cache is keyed on the uri STRING, which
    does not change — so whatever it built is served to the next test.
    """
    foreign = TortoiseSDK(db_path=str(tmp_path / "foreign.db"),
                          namespace="registry")
    try:
        foreign._get_proj()
    except Exception:  # pragma: no cover — the handle is what matters, not it
        pass  # being connected, and a failed connect must not mask the point
    ha._REGISTRY_PROBE_SDK = foreign
    ha._REGISTRY_PROBE_URI = os.environ.get("TORTOISE_DB_URI")


def test_no_registry_probe_target_is_inherited(tmp_path):
    """#9247: the anchor must resolve THIS test's graph, never the last one's.

    RED before the fix: the foreign handle below is still cached, so
    ``_registry_anchor()`` serves it and every anchor read (``_membership_role``,
    ``_org_node``) answers from another test's graph — a 403 for a caller who IS
    an owner, and an org that is missing from the switcher right after it was
    created.
    """
    _leave_a_late_background_build_behind(tmp_path)
    assert ha._REGISTRY_PROBE_SDK is not None, "precondition: cache is poisoned"

    with patched_tortoise_sdk(str(tmp_path / "probe.db")):
        assert ha._REGISTRY_PROBE_SDK is None, (
            "#9247: the registry probe cached before this test took its own "
            "target was NOT dropped — the cache is keyed on the uri STRING, "
            "which the test redirect maps onto a per-test graph, so this test "
            "reads the PREVIOUS test's graph")

        # The fresh per-request SDK is the target a seed writes to.
        fresh = _make_sdk(namespace="registry")
        fresh_graph = fresh._get_proj().graph_name
        fresh._get_registry().query(
            "CREATE (m:Membership {user_id:'u-9247', org_id:'t-9247', "
            "role:'owner', status:'active'})")

        # `_registry_anchor()` is the seam _membership_role / _org_node read.
        anchor = ha._registry_anchor()
        assert anchor._get_proj().graph_name == fresh_graph, (
            "#9247: the resolved registry probe addresses a DIFFERENT server "
            "graph than this test's target — every read through the anchor "
            "answers from another test's state")
        role = anchor._get_registry().query(
            "MATCH (m:Membership {user_id:'u-9247', org_id:'t-9247'}) "
            "RETURN m.role").result_set
        assert role == [["owner"]], (
            f"#9247: the anchor did not read this test's writes: {role}")


def test_anchor_and_fresh_sdk_agree_without_any_pollution(tmp_path):
    """The uncontaminated shape, so a fix cannot pass the test above by
    breaking the probe outright."""
    with patched_tortoise_sdk(str(tmp_path / "probe.db")):
        fresh = _make_sdk(namespace="registry")
        fresh_graph = fresh._get_proj().graph_name
        anchor = ha._registry_anchor()
        assert anchor._get_proj().graph_name == fresh_graph
        # and the cache is doing its job — a second resolution reuses it
        assert ha._registry_anchor() is anchor
        assert ha._REGISTRY_PROBE_SDK is anchor

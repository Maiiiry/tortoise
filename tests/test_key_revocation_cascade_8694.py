"""#8694 — revoking a parent API key must revoke the keys delegated from it.

A ``deleg=0`` child key is minted FROM a live caller key and records that
lineage in ``created_by_key_id``. Before #8694 NOTHING on the resolution path
consulted the ancestor's ``revoked_at``, so an owner could revoke a compromised
``keys:manage`` parent and every credential it had handed out kept
authenticating indefinitely.

The fix has two halves and this file pins BOTH, on BOTH auth lanes:

  * **Resolution (load-bearing)** — a delegated key's authority is INHERITED,
    so a revoked ancestor takes the whole branch down at resolution. That is
    what makes every revoke path cascade, including the ones that grow no
    stamp of their own.
  * **Inventory** — the descendants' own ``revoked_at`` is stamped, so
    ``GET /v1/team/keys`` and the counts read truthfully, and one
    ``api_key_revoke`` audit event names the cascade count.

The ``*_load_bearing_*`` tests are the MUTATION legs the acceptance asks for:
each one UNDOES the inventory stamp (restores the child's ``revoked_at`` to
NULL, i.e. exactly the state a stamp-only fix would leave) and asserts the child
is STILL dead. Delete the parent-liveness check and those tests go red — the
stamp alone does not satisfy them.

Lineage is joined on ``created_by_key_id`` throughout — no name-based matching.
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid
from datetime import UTC, datetime, timedelta

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import tortoise.hosted_api as ha_mod
import tortoise.supabase_control as sc
from tests._http_fixtures import patched_tortoise_sdk
from tests._verdict import verdict_client
from tests.fake_control_plane import FakeControlPlane
from tortoise.auth import lookup_hash
from tortoise.hosted_api import app

_SUPABASE_URL = "https://revoke-cascade-8694.supabase.co"
#: Any authenticated, org-scoped, SCOPE-UNGATED read: the probe answers "does
#: this credential RESOLVE?" without a second authorization axis confusing the
#: signal (a 403 would be ambiguous between "denied" and "still authenticating").
_PROBE = "/v1/capabilities"

_PARENT_TOKEN = "tt_" + "p" * 32
_CHILD_TOKEN = "tk_" + "c" * 32
_SIBLING_TOKEN = "tk_" + "s" * 32
_OTHER_PARENT_TOKEN = "tt_" + "o" * 32
_DIRECT_TOKEN = "tt_" + "d" * 32

_FAR_FUTURE = (datetime.now(UTC) + timedelta(days=365)).isoformat()


# ── helpers ────────────────────────────────────────────────────────────────

def _key_row(fake, key_id: str) -> dict:
    return next(r for r in fake.tables["api_keys"] if r.get("id") == key_id)


def _seed_key(fake, org_id: str, *, key_id: str, token: str,
              scopes: list | None = None, delegation_depth: int | None = None,
              created_by_key_id: str | None = None,
              revoked_at: str | None = None) -> dict:
    """Seed one api_keys row with a REAL ``lookup_hash`` (so it resolves).

    Seeded rather than minted: the mint surfaces carry their own cap gates
    (``max_api_keys`` is 2 on a free org) that have nothing to do with the
    cascade, and a seeded row lets a test place a lineage a mint surface could
    not produce today (a grandchild) without pretending the mint allows it.
    """
    row = {
        "id": key_id, "org_id": org_id,
        "key_prefix": token[:10], "lookup_hash": lookup_hash(token),
        "created_via": "provisioned", "created_by": "api",
        "created_at": datetime.now(UTC).isoformat(),
        "expires_at": _FAR_FUTURE, "revoked_at": revoked_at,
        "enabled": True, "name": key_id, "scopes": scopes or [],
        "delegation_depth": delegation_depth, "graph_id": None,
        "created_by_key_id": created_by_key_id,
    }
    fake.tables.setdefault("api_keys", []).append(row)
    return row


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _probe(client, token: str) -> int:
    return client.get(_PROBE, headers=_auth(token)).status_code


def _revoke(client, key_id: str, as_token: str):
    return client.delete(f"/v1/team/keys/{key_id}", headers=_auth(as_token))


@pytest.fixture
def sb(monkeypatch):
    """Supabase-mode TestClient over a FakeControlPlane + a temp embedded DB.

    The org comes from the real ``/v1/agent/signup`` lane (so ``teams`` and the
    signing key exist for every other read); the keys under test are seeded.
    """
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", _SUPABASE_URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-revoke-cascade-8694")
    monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
    ha_mod._CLAIM_BUCKETS.clear()
    fake = FakeControlPlane()
    monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "revoke-cascade-sb.db")
        with patched_tortoise_sdk(db_path), verdict_client(app) as tc:
            yield tc, fake
    ha_mod._CLAIM_BUCKETS.clear()
    app.dependency_overrides.clear()


def _org(client, fake) -> dict:
    """Provision the org via the real signup lane and seed the key graph.

    Lineage under test (all in ONE org, joined by ``created_by_key_id``):

        parent(P) ── deleg=0 ──> child(C)          # the cascade pair
        other(O)  ── deleg=0 ──> sibling(S)        # a DIFFERENT parent's child
        direct(D)  (no lineage)                     # a directly-minted key
    """
    r = client.post("/v1/agent/signup", json={})
    assert r.status_code == 200, r.text
    org_id = r.json()["org_id"]
    _seed_key(fake, org_id, key_id="k-parent-8694", token=_PARENT_TOKEN,
              scopes=[], delegation_depth=None)
    _seed_key(fake, org_id, key_id="k-child-8694", token=_CHILD_TOKEN,
              scopes=["graphs:read"], delegation_depth=0,
              created_by_key_id="k-parent-8694")
    _seed_key(fake, org_id, key_id="k-other-parent-8694",
              token=_OTHER_PARENT_TOKEN, scopes=[], delegation_depth=None)
    _seed_key(fake, org_id, key_id="k-sibling-8694", token=_SIBLING_TOKEN,
              scopes=["graphs:read"], delegation_depth=0,
              created_by_key_id="k-other-parent-8694")
    _seed_key(fake, org_id, key_id="k-direct-8694", token=_DIRECT_TOKEN,
              scopes=[], delegation_depth=None)
    return {"org_id": org_id, "key": r.json()["key"]}


# ═══════════════════════════════════════════════════════════════════════════
# 1. Supabase lane — resolution + inventory + audit
# ═══════════════════════════════════════════════════════════════════════════


class TestSupabaseLane:

    def test_parent_revoke_kills_the_delegated_child_at_resolution(self, sb):
        """THE acceptance case: the child resolves, the parent is revoked, the
        child stops resolving — at RESOLUTION, not merely out of a listing."""
        tc, fake = sb
        org = _org(tc, fake)
        assert _probe(tc, _CHILD_TOKEN) == 200, "the child must be live first"

        r = _revoke(tc, "k-parent-8694", as_token=_PARENT_TOKEN)
        assert r.status_code == 200, r.text

        assert _probe(tc, _CHILD_TOKEN) == 401, (
            "a delegated child must fail closed once its parent is revoked"
        )
        # and the parent itself is dead, as before
        assert _probe(tc, _PARENT_TOKEN) == 401
        assert org["org_id"]

    def test_parent_revoke_stamps_the_child_and_the_inventory_says_revoked(
            self, sb):
        """Half two: the child's OWN row is stamped, so the listing is truthful
        (a stamp alone is not the fix, but a fix without it lies)."""
        tc, fake = sb
        _org(tc, fake)
        assert _key_row(fake, "k-child-8694")["revoked_at"] is None
        assert _revoke(tc, "k-parent-8694", as_token=_PARENT_TOKEN).status_code == 200
        assert _key_row(fake, "k-child-8694")["revoked_at"] is not None, (
            "the cascade must stamp the descendant row"
        )
        listed = tc.get("/v1/team/keys", headers=_auth(_DIRECT_TOKEN))
        assert listed.status_code == 200, listed.text
        by_id = {k["id"]: k for k in listed.json()["keys"]}
        assert by_id["k-child-8694"]["revoked_at"] is not None
        assert by_id["k-parent-8694"]["revoked_at"] is not None

    def test_the_resolve_gate_is_load_bearing_not_the_stamp(self, sb):
        """MUTATION leg. Undo the inventory stamp (child ``revoked_at`` back to
        NULL — the exact state a stamp-only fix leaves on any revoke path that
        forgets to cascade) and the child must STILL be dead.

        Remove ``key_lineage_live`` from ``resolve_api_key`` and this is the
        test that goes red.
        """
        tc, fake = sb
        _org(tc, fake)
        assert _revoke(tc, "k-parent-8694", as_token=_PARENT_TOKEN).status_code == 200
        _key_row(fake, "k-child-8694")["revoked_at"] = None
        assert _probe(tc, _CHILD_TOKEN) == 401, (
            "the stamp was undone and the child STILL resolved — the "
            "parent-liveness check at resolution is missing"
        )

    def test_a_sibling_and_a_directly_minted_key_are_untouched(self, sb):
        """Blast-radius check: another parent's child, and a key with a NULL
        lineage, are unaffected (acceptance: no behaviour change for NULL
        lineage)."""
        tc, fake = sb
        _org(tc, fake)
        assert _revoke(tc, "k-parent-8694", as_token=_PARENT_TOKEN).status_code == 200
        assert _probe(tc, _SIBLING_TOKEN) == 200, (
            "a child of a DIFFERENT parent must not be collateral"
        )
        assert _key_row(fake, "k-sibling-8694")["revoked_at"] is None
        assert _probe(tc, _DIRECT_TOKEN) == 200, (
            "a directly-minted key (created_by_key_id NULL) must be unaffected"
        )

    def test_revoking_a_child_does_not_touch_its_parent(self, sb):
        """The cascade runs DOWN the lineage only — a child's revocation must
        never widen into the credential that minted it."""
        tc, fake = sb
        _org(tc, fake)
        assert _revoke(tc, "k-child-8694", as_token=_PARENT_TOKEN).status_code == 200
        assert _key_row(fake, "k-parent-8694")["revoked_at"] is None
        assert _probe(tc, _PARENT_TOKEN) == 200
        assert _probe(tc, _CHILD_TOKEN) == 401

    def test_a_grandchild_dies_with_its_grandparent(self, sb):
        """Depth > 0: one level of delegation exists today, but the rule is
        written for descendants in general. A hand-seeded two-hop chain (the
        shape a future depth change would produce) must be walked, not read as
        \"the immediate parent is live, therefore fine\"."""
        tc, fake = sb
        org = _org(tc, fake)
        _seed_key(fake, org["org_id"], key_id="k-grandchild-8694",
                  token="tk_" + "g" * 32, scopes=["graphs:read"],
                  delegation_depth=0, created_by_key_id="k-child-8694")
        assert _probe(tc, "tk_" + "g" * 32) == 200
        assert _revoke(tc, "k-parent-8694", as_token=_PARENT_TOKEN).status_code == 200
        assert _probe(tc, "tk_" + "g" * 32) == 401, (
            "the walk must reach beyond the immediate parent"
        )
        assert _key_row(fake, "k-grandchild-8694")["revoked_at"] is not None

    def test_the_cascade_names_its_count_in_the_audit_trail(self, sb, monkeypatch):
        """One audit event naming the cascade count, so a parent revocation's
        blast radius is legible from the trail and not only from child rows."""
        tc, fake = sb
        org = _org(tc, fake)
        events: list[dict] = []

        async def _fake_audit(request, org_id, operation, **kwargs):
            events.append({"org_id": org_id, "operation": operation, **kwargs})

        monkeypatch.setattr(ha_mod, "_async_audit", _fake_audit)
        assert _revoke(tc, "k-parent-8694", as_token=_PARENT_TOKEN).status_code == 200
        revoke_ev = [e for e in events if e["operation"] == "api_key_revoke"]
        assert len(revoke_ev) == 1, events
        ev = revoke_ev[0]
        assert ev["org_id"] == org["org_id"]
        assert ev["resource_id"] == "k-parent-8694"
        assert ev["detail"]["cascaded"] == 1, ev["detail"]
        assert ev["detail"]["cascaded_key_ids"] == ["k-child-8694"], ev["detail"]

    def test_the_same_gate_is_the_mcp_seam(self, sb):
        """REST and MCP resolve through the SAME function in hosted mode
        (``tortoise/mcp_auth.py`` calls ``supabase_control.resolve_api_key``),
        so pinning it here pins the MCP lane too — one check, two doors."""
        tc, fake = sb
        _org(tc, fake)
        assert sc.resolve_api_key(fake, _CHILD_TOKEN) is not None
        assert _revoke(tc, "k-parent-8694", as_token=_PARENT_TOKEN).status_code == 200
        assert sc.resolve_api_key(fake, _CHILD_TOKEN) is None, (
            "MCP's resolver must reject the child of a revoked parent"
        )
        assert sc.resolve_api_key(fake, _DIRECT_TOKEN) is not None

    def test_an_unknown_ancestor_fails_closed(self, sb):
        """A lineage pointing at a row that does not exist attests NOTHING — a
        delegation we cannot verify is not a delegation we honour."""
        tc, fake = sb
        org = _org(tc, fake)
        _seed_key(fake, org["org_id"], key_id="k-orphan-8694",
                  token="tk_" + "x" * 32, scopes=["graphs:read"],
                  delegation_depth=0, created_by_key_id="k-does-not-exist")
        assert _probe(tc, "tk_" + "x" * 32) == 401


# ═══════════════════════════════════════════════════════════════════════════
# 2. Registry / selfhost lane — REST resolve, MCP resolve, cascade
# ═══════════════════════════════════════════════════════════════════════════

# #1588: hold registry SDKs alive — the SDK owns the embedded server, and a
# dropped handle closes it before the test is done.
_REG_SDKS: list = []


@pytest.fixture
def reg_client(monkeypatch):
    """Registry-mode (selfhost) TestClient. No Supabase creds → the registry
    lane of ``get_current_org`` is the door under test."""
    monkeypatch.delenv("TORTOISE_CONTROL_PLANE", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "revoke-cascade-reg.db")
        with patched_tortoise_sdk(db_path):
            try:
                with verdict_client(app) as tc:
                    yield tc
            finally:
                while _REG_SDKS:
                    try:  # noqa: SIM105
                        _REG_SDKS.pop().close()
                    except Exception:
                        pass


@pytest.fixture
def reg(reg_client):
    from tortoise.sdk import TortoiseSDK
    sdk = TortoiseSDK(namespace="registry")
    _REG_SDKS.append(sdk)
    return sdk


def _reg_chain(reg) -> dict:
    """A parent key + a delegated child + a directly-minted key, all minted
    through the real registry mint (real salted hashes, real prefixes)."""
    team = reg.org_create(f"revoke-cascade-{uuid.uuid4().hex[:8]}")
    parent = reg.apikey_create(team["id"], "api")
    child = reg.apikey_create(
        team["id"], "api", scopes=["graphs:read"], delegation_depth=0,
        created_by_key_id=parent["id"], prefix="tk_")
    direct = reg.apikey_create(team["id"], "api")
    return {"team": team, "parent": parent, "child": child, "direct": direct}


def _reg_row(reg, key_id: str) -> dict:
    rows = reg._get_registry().query(
        "MATCH (k:APIKey {id:$id}) RETURN k.revoked_at", params={"id": key_id},
    ).result_set
    return {"revoked_at": rows[0][0] if rows else "missing"}


class TestRegistryLane:

    def test_registry_parent_revoke_kills_the_child_on_rest_and_mcp(
            self, reg_client, reg):
        """The selfhost lane has TWO resolve doors — REST ``get_current_org``
        and ``TortoiseSDK.apikey_verify`` (MCP) — and both must ask."""
        tc = reg_client
        chain = _reg_chain(reg)
        child_key = chain["child"]["api_key"]
        assert _probe(tc, child_key) == 200, "the child must be live first"
        assert reg.apikey_verify(child_key) is not None

        r = _revoke(tc, chain["parent"]["id"], as_token=chain["parent"]["api_key"])
        assert r.status_code == 200, r.text

        assert _probe(tc, child_key) == 401, (
            "REST/selfhost must fail closed on a revoked ancestor"
        )
        assert reg.apikey_verify(child_key) is None, (
            "the MCP/selfhost door must fail closed on a revoked ancestor"
        )
        assert _reg_row(reg, chain["child"]["id"])["revoked_at"] is not None, (
            "the cascade must stamp the child node"
        )

    def test_registry_resolve_gate_is_load_bearing(self, reg_client, reg):
        """MUTATION leg on the registry lane: undo the node stamp and the child
        must STILL be rejected by both doors."""
        tc = reg_client
        chain = _reg_chain(reg)
        child_key = chain["child"]["api_key"]
        assert _revoke(tc, chain["parent"]["id"], as_token=chain["parent"]["api_key"]).status_code == 200
        reg._get_registry().query(
            "MATCH (k:APIKey {id:$id}) SET k.revoked_at = NULL",
            params={"id": chain["child"]["id"]},
        )
        assert _probe(tc, child_key) == 401, (
            "the stamp was undone and the child STILL resolved over REST — "
            "the registry lineage gate is missing"
        )
        assert reg.apikey_verify(child_key) is None, (
            "the stamp was undone and the child STILL resolved over MCP — "
            "the registry lineage gate is missing"
        )

    def test_registry_directly_minted_key_is_unaffected(self, reg_client, reg):
        tc = reg_client
        chain = _reg_chain(reg)
        assert _revoke(tc, chain["parent"]["id"], as_token=chain["parent"]["api_key"]).status_code == 200
        assert _probe(tc, chain["direct"]["api_key"]) == 200
        assert reg.apikey_verify(chain["direct"]["api_key"]) is not None


class TestRegistryMcpLane:
    """The MCP surface itself (not just its resolver): a delegated child that
    loses its parent must stop being able to ``initialize``."""

    def test_mcp_initialize_is_refused_after_the_parent_is_revoked(
            self, tmp_path, monkeypatch):
        from tests.test_mcp_server_auth_modes import (
            _initialize_payload,
            _mcp_post,
            _mounted_test_client,
        )
        from tortoise.mcp_server import create_http_app
        from tortoise.sdk import TortoiseSDK

        db_path = str(tmp_path / "revoke-cascade-mcp.db")
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
        reg = TortoiseSDK(db_path=db_path, namespace="registry")
        try:
            team = reg.org_create("revoke-cascade-mcp")
            parent = reg.apikey_create(team["id"], "api")
            # deleg=0 WITH a data scope: the C5 gate admits it over MCP, so a
            # 401 after the parent dies is the lineage gate, not dormancy.
            child = reg.apikey_create(
                team["id"], "api", scopes=["graphs:read"], delegation_depth=0,
                created_by_key_id=parent["id"], prefix="tk_")
            app_ = create_http_app(allowed_origins=[], _registry_sdk=reg)
            tc = _mounted_test_client(app_)
            with tc:
                ok = _mcp_post(tc, _initialize_payload(),
                               auth_header=f"Bearer {child['api_key']}")
                assert ok.status_code in (200, 202), ok.text

                assert reg.apikey_revoke(parent["id"])["revoked"] is True

                dead = _mcp_post(tc, _initialize_payload(),
                                 auth_header=f"Bearer {child['api_key']}")
                assert dead.status_code == 401, dead.text
        finally:
            try:  # noqa: SIM105
                reg.close()
            except Exception:
                pass

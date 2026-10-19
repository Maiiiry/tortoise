"""#8692 — phase 3 of the #2427 permission model: the permission VOCABULARY,
the ROLE BUNDLES over it, and the HOLDER field.

WHAT THIS FILE PINS. `tortoise/permissions.py` is the one home of the gate
vocabulary. Before it, a gate was a ROLE — every key/member endpoint picked
`_require_owner_admin` / `_require_owner` / `_require_keys_manage` — so "own key
vs any key" was inexpressible (#2427 brief §1). This file holds the four
properties the issue's acceptance list names:

1. **A flat, machine-readable vocabulary.** One dotted string per permission;
   `RoleBundle`s are flat frozensets of them. A nested shape would make the
   escalation CHECK vacuous (the constraint recorded in
   `supabase/migrations/20260901000001_graphs_and_key_scopes.sql`).
2. **The three roles are bundles, not a fourth role.**
   `owner` = all; `admin` = all except the two owner-only powers; `member` =
   `org.read` + `keys.*.own` + `keys.audit.read`.
3. **`org.export` / `org.delete` stay OWNER-ONLY** — encodable as permissions,
   never grantable below `role == 'owner'` (brief §4.2, deliberately strict).
   `org.import` joins them because it is the same whole-tenant class and the
   brief's matrix simply omitted it.
4. **Behavioural parity.** The decision function refuses every non-admin path
   except the holder-scoped `.own` one, and the 403 details are the pre-#8692
   strings verbatim — so `authority_for` widens NOTHING on day one. The
   end-to-end member/admin/owner matrices live in
   `tests/test_own_key_selfservice_2446.py`, `tests/test_rotate_key.py` and
   `tests/test_suspension_parity.py`; they must keep passing UNCHANGED, which
   is why this file tests the vocabulary and the anti-drift tripwires only.

Plus two drift pins:

* **The `owner_user_id` backfill agrees with the read predicate.** The column is
  delivered by an ADDITIVE migration (no mint RPC redefined), so a row minted
  after it arrives with the column unset and `holder_user_id` derives the holder
  from the self-service class. The two must yield the SAME answer for every row
  class — the migration's SQL and the predicate are asserted together here.
* **An endpoint cannot silently pick a role again.** An AST scan of
  `tortoise/hosted_api.py` requires every production call of the three legacy
  gate names to name a permission. A new endpoint that reaches for the role
  gate without one reddens here instead of shipping a role-shaped gate.
"""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")


import tortoise.hosted_api as ha
from tortoise.permissions import (
    OWN_PERMISSIONS,
    OWNER_ONLY_PERMISSIONS,
    ROLE_BUNDLES,
    ROLE_NAMES,
    SELF_SERVICE_CREATED_VIA,
    SURFACE_PERMISSIONS,
    Permission,
    authority_for,
    bundle_for,
    holder_user_id,
    permission_detail,
    role_allows,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_HOSTED_API = _REPO_ROOT / "tortoise" / "hosted_api.py"
_MIGRATION = (_REPO_ROOT / "supabase" / "migrations"
              / "20261019000001_api_keys_owner_user_id.sql")

#: #2427 brief §2.2 — the endpoint→permission matrix is this issue's ACCEPTANCE
#: TABLE, so its rows are restated here (surface key only) and the token table is
#: asserted to cover every one. If the brief grows a row, this list is where the
#: omission becomes visible.
_BRIEF_2_2_SURFACES = (
    "POST /v1/team/keys",
    "POST /v1/team/keys/{id}/rotate",
    "GET /v1/team/keys",
    "DELETE /v1/team/keys/{id}",
    "PATCH /v1/team/keys/{id}",
    "POST /v1/session/key?purpose=bootstrap",
    "POST /v1/session/key?purpose=recovery",
    "GET /v1/organizations/{id}/members",
    "POST /v1/invites",
    "GET /v1/organizations/{id}/export",
    "DELETE /v1/organizations/{id}",
    "PATCH /v1/team/dashboard-login",
    "data plane (every handler)",
)


# ── the holders we exercise: one row per class in the wild ─────────────────
def _row(*, created_via="bootstrap", created_by="u-holder", owner_user_id=None):
    row = {"created_via": created_via, "created_by": created_by}
    if owner_user_id is not None:
        row["owner_user_id"] = owner_user_id
    return row


# ===========================================================================
# 1) A flat, machine-readable vocabulary
# ===========================================================================
def test_the_vocabulary_is_flat_and_machine_readable():
    """Every permission is ONE flat string — no nesting, no spaces.

    The scope vocabulary's own header records why a nested shape is unsafe (a
    nested permission makes the escalation CHECK vacuous). A permission value is
    also the thing an audit record and a future per-member grant name, so it has
    to be a stable, greppable token rather than a TUPLE that a column, a JSON
    envelope and a set deliberately mis-handle.
    """
    assert issubclass(Permission, str), "a permission must BE its string value"
    values = [p.value for p in Permission]
    assert len(values) == len(set(values)), "duplicate permission value"
    for value in values:
        assert isinstance(value, str)
        assert re.fullmatch(r"[a-z]+(\.[a-z]+)*(:[a-z]+)?", value), value
        assert " " not in value and "," not in value


def test_every_permission_member_is_its_own_value():
    for member in Permission:
        assert isinstance(member.value, str)
        # str-subclass equality is what lets a call site pass either form and
        # what makes the DB row / JSON envelope / bundle check the same token.
        assert member == member.value
        assert member in {member.value}


def test_the_data_plane_scopes_keep_their_recorded_spelling():
    """The data plane's capability scopes are carried ON the key (`graphs:read`)
    and enforced by `_require_scope`; the issue says "plus the existing
    data-plane scopes unchanged", so the vocabulary must adopt the RECORDED
    spelling rather than renamespace them. A rename here would silently detach
    the enum from the strings `_require_scope` matches."""
    for name in ("graphs:read", "graphs:write", "graphs:create",
                 "graphs:delete", "team:manage", "keys:manage"):
        assert name in {p.value for p in Permission}, name
    # ...and `_require_scope`'s own matcher still sees the same token.
    assert Permission.GRAPHS_READ in {"graphs:read", "graphs:write"}
    assert Permission.KEYS_MANAGE in ["keys:manage"]


# ===========================================================================
# 2) Role bundles
# ===========================================================================
def test_owner_bundle_is_every_permission():
    assert bundle_for("owner") == frozenset(Permission)


def test_admin_bundle_is_owner_minus_the_owner_only_powers():
    admin = bundle_for("admin")
    assert admin == frozenset(Permission) - OWNER_ONLY_PERMISSIONS
    for owner_only in OWNER_ONLY_PERMISSIONS:
        assert owner_only not in admin, owner_only


def test_org_export_and_org_delete_stay_owner_only():
    """#2427 brief §4.2, deliberately strict: a permission that CAN be delegated
    to an admin is a different product promise than "only the account owner can
    export or delete". Widening this is a product change, not a refactor."""
    assert Permission.ORG_EXPORT in OWNER_ONLY_PERMISSIONS
    assert Permission.ORG_DELETE in OWNER_ONLY_PERMISSIONS
    assert not role_allows("admin", Permission.ORG_EXPORT)
    assert not role_allows("admin", Permission.ORG_DELETE)
    assert role_allows("owner", Permission.ORG_EXPORT)
    assert role_allows("owner", Permission.ORG_DELETE)


def test_member_bundle_is_the_own_key_slice_and_nothing_else():
    member = bundle_for("member")
    assert member == frozenset({
        Permission.ORG_READ,
        Permission.KEYS_MINT_OWN,
        Permission.KEYS_READ_OWN,
        Permission.KEYS_ROTATE_OWN,
        Permission.KEYS_REVOKE_OWN,
        Permission.KEYS_TOGGLE_OWN,
        Permission.KEYS_AUDIT_READ,
    })
    # The org-wide / all-row half is admin territory, never a member's.
    for denied in (Permission.KEYS_READ_ALL, Permission.KEYS_ROTATE_ANY,
                   Permission.KEYS_REVOKE_ANY, Permission.KEYS_TOGGLE_ANY,
                   Permission.KEYS_MINT_TEAM, Permission.KEYS_RECOVER_ANY,
                   Permission.MEMBERS_READ, Permission.MEMBERS_MANAGE,
                   Permission.TEAM_SETTINGS):
        assert denied not in member, denied


def test_member_durable_mint_stays_a_policy_a_reopen_not_a_bundle_entry():
    """POLICY A (#2297 owner decision 2026-09-05, #2535 HARD STOP) forbids a
    member DURABLE mint; #8692 lists it as out of scope and requires a reopen of
    #2297, not a code change. The member bundle grants `keys.mint.own` — the
    EPHEMERAL bootstrap door that is already member-open — and NOT the durable
    recovery verb, which is the thing POLICY A forbids."""
    assert Permission.KEYS_MINT_OWN in bundle_for("member")
    assert Permission.KEYS_RECOVER_ANY not in bundle_for("member")
    assert role_allows("admin", Permission.KEYS_RECOVER_ANY)
    assert role_allows("owner", Permission.KEYS_RECOVER_ANY)


def test_unknown_and_absent_roles_get_the_empty_bundle():
    """A membership row carrying a role this vocabulary does not know must fail
    CLOSED — never fall back to a default bundle."""
    assert bundle_for("superuser") == frozenset()
    assert bundle_for(None) == frozenset()
    assert bundle_for("") == frozenset()
    assert not role_allows("superuser", Permission.ORG_READ)


def test_the_three_roles_are_unchanged():
    assert ROLE_NAMES == ("owner", "admin", "member")
    assert set(ROLE_BUNDLES) == {"owner", "admin", "member"}


def test_every_own_permission_has_a_role_that_admits_it():
    for own in OWN_PERMISSIONS:
        assert role_allows("owner", own), own
        assert role_allows("admin", own), own
        assert role_allows("member", own), own


# ===========================================================================
# 3) The decision function — authority_for
# ===========================================================================
class TestAuthorityFor:
    HELD = _row(created_via="bootstrap", created_by="u-member")
    ORG_OWNED = _row(created_via="provisioned", created_by="u-member")
    SHARED = _row(created_via="recovery", created_by="u-owner")

    def test_the_role_path_admits_an_owner_and_an_admin(self):
        assert authority_for("owner", Permission.MEMBERS_MANAGE) == "owner"
        assert authority_for("admin", Permission.MEMBERS_MANAGE) == "admin"

    def test_the_role_path_refuses_an_admin_the_owner_only_powers(self):
        assert authority_for("admin", Permission.ORG_EXPORT) is None
        assert authority_for("admin", Permission.ORG_DELETE) is None
        assert authority_for("admin", Permission.ORG_IMPORT) is None

    def test_the_role_path_refuses_a_member_every_org_wide_verb(self):
        for perm in (Permission.MEMBERS_READ, Permission.MEMBERS_MANAGE,
                     Permission.TEAM_SETTINGS, Permission.KEYS_READ_ALL,
                     Permission.KEYS_ROTATE_ANY, Permission.KEYS_REVOKE_ANY,
                     Permission.KEYS_TOGGLE_ANY, Permission.KEYS_MINT_TEAM,
                     Permission.KEYS_RECOVER_ANY):
            assert authority_for("member", perm) is None, perm

    def test_the_holder_path_admits_the_holder_of_a_self_service_row(self):
        assert authority_for("member", Permission.KEYS_TOGGLE_OWN,
                             user_id="u-member", target=self.HELD) == "own"
        assert authority_for("member", Permission.KEYS_REVOKE_OWN,
                             user_id="u-member", target=self.HELD) == "own"
        assert authority_for("member", Permission.KEYS_ROTATE_OWN,
                             user_id="u-member", target=self.HELD) == "own"

    def test_the_holder_path_refuses_a_row_the_caller_only_MINted(self):
        """`created_by` is the MINTER, not the holder (brief §2.3 item 4) — a
        member-minted DURABLE row carries their id and is still ORG-OWNED. The
        false premise "own == created_by" is exactly the escalation this
        predicate exists to refuse."""
        assert authority_for("member", Permission.KEYS_TOGGLE_OWN,
                             user_id="u-member", target=self.ORG_OWNED) is None

    def test_the_holder_path_refuses_a_shared_row_a_member_merely_holds(self):
        """An org-owned/shared key a member holds a COPY of must never be
        rotatable or retirable by them (brief §4.2)."""
        assert authority_for("member", Permission.KEYS_TOGGLE_OWN,
                             user_id="u-member", target=self.SHARED) is None

    def test_the_holder_path_requires_an_active_membership(self):
        """A REMOVED member (role None) is refused even while holding the row:
        `remove_member` does not revoke api_keys and key auth does no membership
        check, so the holder bit alone would let them re-enable a credential
        they were left with (#2446 review P2-1)."""
        assert authority_for(None, Permission.KEYS_TOGGLE_OWN,
                             user_id="u-member", target=self.HELD) is None

    def test_the_holder_path_requires_an_EXPLICIT_target(self):
        """The holder path is the only non-admin admission, so forgetting the
        target must REFUSE — never treat "no row" as "the caller's own"."""
        assert authority_for("member", Permission.KEYS_TOGGLE_OWN,
                             user_id="u-member") is None
        assert authority_for("member", Permission.KEYS_TOGGLE_OWN,
                             user_id="u-member", target=None) is None
        assert authority_for("member", Permission.KEYS_TOGGLE_OWN,
                             user_id="u-member", target={}) is None

    def test_a_non_own_permission_has_no_holder_path(self):
        """`org.read` / `keys.audit.read` are DECLARED in the member bundle but
        have no holder-scoped admission in this phase — admitting them here
        would widen a surface nobody has gated yet."""
        assert authority_for("member", Permission.ORG_READ,
                             user_id="u-member", target=self.HELD) is None
        assert authority_for("member", Permission.KEYS_AUDIT_READ,
                             user_id="u-member", target=self.HELD) is None

    def test_the_legacy_coarse_gate_is_owner_or_admin_only(self):
        """`permission=None` is the pre-#8692 coarse gate, kept expressible for
        callers that have not been named over yet."""
        assert authority_for("owner", None) == "owner"
        assert authority_for("admin", None) == "admin"
        assert authority_for("member", None) is None
        assert authority_for(None, None) is None


def test_the_403_details_are_the_pre_8692_strings_verbatim():
    """A distinct message per case would turn the gate into an ownership oracle
    for rows a member cannot manage; two suites pin the exact wording."""
    assert permission_detail(Permission.ORG_EXPORT) == "Requires owner role in team"
    assert permission_detail(Permission.ORG_DELETE) == "Requires owner role in team"
    assert permission_detail(Permission.ORG_IMPORT) == "Requires owner role in team"
    assert permission_detail(Permission.MEMBERS_MANAGE) == (
        "Requires owner or admin role in team")
    assert permission_detail(Permission.KEYS_TOGGLE_ANY) == (
        "Requires owner or admin role in team")
    assert permission_detail(None) == "Requires owner or admin role in team"


# ===========================================================================
# 4) The holder field
# ===========================================================================
class TestHolder:
    def test_the_column_wins_when_it_is_set(self):
        """`owner_user_id` is the HOLDER — it makes the owner-mints-for-member
        case (#2446 phase 2's surviving dead-end) reachable, and it OVERRIDES
        the class derivation."""
        row = _row(created_via="provisioned", created_by="u-owner",
                   owner_user_id="u-member")
        assert holder_user_id(row) == "u-member"
        assert holder_user_id(_row(created_via="bootstrap",
                                   created_by="u-a",
                                   owner_user_id="u-b")) == "u-b"

    def test_no_column_derives_the_holder_from_the_self_service_class(self):
        """The additive migration does not redefine any mint RPC, so a row minted
        after it arrives with the column unset — the derivation must yield
        exactly what the backfill would have written."""
        assert holder_user_id(_row(created_via="bootstrap",
                                   created_by="u-member")) == "u-member"

    def test_no_column_and_no_self_service_class_is_org_owned(self):
        for created_via in ("provisioned", "recovery", None):
            assert holder_user_id(_row(created_via=created_via,
                                       created_by="u-member")) is None

    def test_a_non_user_attribution_is_never_a_holder(self):
        """`_mint_key` attributes a key-driven mint to the literal `'api'`."""
        assert holder_user_id(_row(created_via="bootstrap",
                                   created_by="api")) is None
        assert holder_user_id(_row(created_via="provisioned",
                                   created_by="api",
                                   owner_user_id="api")) is None

    def test_a_missing_or_empty_row_has_no_holder(self):
        assert holder_user_id(None) is None
        assert holder_user_id({}) is None

    def test_the_self_service_class_constant_matches_the_mint_lane(self):
        assert SELF_SERVICE_CREATED_VIA == "bootstrap"
        assert ha._SESSION_KEY_CREATED_VIA == SELF_SERVICE_CREATED_VIA


def test_hosted_api_delegates_the_holder_predicate():
    """The gate seam must stay a DELEGATION, not a second copy of the rule — a
    copy is how `created_by` gets read inline at the next call site (the failure
    MEMORY.md records for #2446)."""
    row = _row(created_via="provisioned", created_by="u-owner",
               owner_user_id="u-member")
    assert ha._session_key_holder(row) == holder_user_id(row) == "u-member"
    assert ha._session_key_holder(_row(created_via="bootstrap",
                                       created_by="u-member")) == "u-member"
    assert ha._session_key_holder(_row(created_via="provisioned",
                                       created_by="u-member")) is None


# ===========================================================================
# 5) The surface → permission table (the issue's acceptance table)
# ===========================================================================
def test_the_surface_table_covers_every_row_of_brief_2_2():
    missing = [s for s in _BRIEF_2_2_SURFACES if s not in SURFACE_PERMISSIONS]
    assert not missing, f"brief §2.2 rows with no named permission: {missing}"


def test_every_surface_names_permissions_that_exist():
    for surface, perms in SURFACE_PERMISSIONS.items():
        assert perms, f"{surface} names no permission"
        for perm in perms:
            assert isinstance(perm, Permission), (surface, perm)
            assert perm.value in {p.value for p in Permission}, (surface, perm)


def test_the_own_any_pairs_are_declared_where_the_holder_slice_exists():
    """The pair IS the point of the vocabulary: `.own` (the holder, member) and
    `.any` (every row, admin) must both be named for the verbs #2446 needs."""
    pairs = {
        "POST /v1/team/keys/{id}/rotate": (Permission.KEYS_ROTATE_ANY,
                                           Permission.KEYS_ROTATE_OWN),
        "DELETE /v1/team/keys/{id}": (Permission.KEYS_REVOKE_ANY,
                                      Permission.KEYS_REVOKE_OWN),
        "PATCH /v1/team/keys/{id}": (Permission.KEYS_TOGGLE_ANY,
                                     Permission.KEYS_TOGGLE_OWN),
    }
    for surface, (any_perm, own_perm) in pairs.items():
        assert SURFACE_PERMISSIONS[surface] == (any_perm, own_perm)
        assert own_perm in OWN_PERMISSIONS
        assert any_perm not in OWN_PERMISSIONS
        assert role_allows("admin", any_perm)
        assert not role_allows("member", any_perm)
        assert role_allows("member", own_perm)


def test_the_owner_only_surfaces_map_to_owner_only_permissions():
    for surface in ("GET /v1/organizations/{id}/export",
                    "DELETE /v1/organizations/{id}"):
        for perm in SURFACE_PERMISSIONS[surface]:
            assert perm in OWNER_ONLY_PERMISSIONS, (surface, perm)


# ===========================================================================
# 6) Drift pin — the endpoint cannot silently pick a role again
# ===========================================================================
_LEGACY_GATES = ("_require_owner_admin", "_require_owner",
                 "_require_owner_admin_if_session")


def _named_permission(call: ast.Call) -> bool:
    if any(kw.arg == "permission" for kw in call.keywords):
        return True
    for arg in call.args:
        if isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name) \
                and arg.value.id == "Permission":
            return True
    return False


def test_every_production_gate_call_site_names_a_permission():
    """An endpoint may not reach for a role again without saying WHICH
    permission it needs: a new surface that calls a legacy gate bare would pick
    a role by accident, which is precisely the defect #8692 removes. The AST
    scan is the enforcement — a source substring check would pass a call that
    merely mentions the name."""
    tree = ast.parse(_HOSTED_API.read_text())
    bare: list[str] = []
    seen = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else None
        if name not in _LEGACY_GATES:
            continue
        seen += 1
        if not _named_permission(node):
            bare.append(f"{name} at line {node.lineno}")
    assert seen >= 20, (
        f"only {seen} gate call sites found — the scan stopped matching, so this "
        "pin is vacuous (did hosted_api.py move?)")
    assert not bare, (
        "gate call site(s) pick a ROLE instead of naming a permission: "
        + ", ".join(bare))


def test_the_authorize_seam_is_the_single_decision_point():
    """`authorize` must delegate the DECISION to the vocabulary rather than
    re-state the bundle test inline — a second copy is how the owner-only pin
    gets widened at one call site and nowhere else."""
    tree = ast.parse(_HOSTED_API.read_text())
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.AsyncFunctionDef) and n.name == "authorize"),
              None)
    assert fn is not None, "hosted_api.authorize seam is gone"
    called = {n.func.id for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "authority_for" in called
    assert "permission_detail" in called


# ===========================================================================
# 7) Drift pin — the additive backfill agrees with the read predicate
# ===========================================================================
def test_the_migration_is_additive_and_reversible():
    assert _MIGRATION.exists(), f"{_MIGRATION.name} is missing"
    sql = _MIGRATION.read_text()
    # Executable statements only: the rollback recipe lives in a COMMENT, and a
    # comment that names a destructive verb is documentation, not a statement.
    statements = "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    assert re.search(r"ALTER TABLE public\.api_keys\s*\n\s*ADD COLUMN IF NOT EXISTS"
                     r"\s+owner_user_id text", statements), (
        "the column add is not additive")
    # Additive means: no table rewrite, no constraint change, and NO function
    # redefinition (a `CREATE OR REPLACE FUNCTION` would make the migration
    # non-additive and re-open the mint RPC for review on a schema-only change).
    for forbidden in ("CREATE OR REPLACE FUNCTION", "DROP COLUMN",
                      "ALTER COLUMN", "CREATE INDEX", "ADD CONSTRAINT"):
        assert forbidden not in statements, (
            f"additive migration contains {forbidden!r}")
    # The rollback is documented, because it is what makes the column safe to
    # drop: everything it writes is derivable from created_via + created_by.
    assert "DROP COLUMN IF EXISTS owner_user_id" in sql


def test_the_backfill_predicate_is_the_holder_derivation():
    """The migration's WHERE clause and `holder_user_id`'s derivation are the
    same rule in two languages; if either drifts, some class of row silently
    loses (or gains) a holder. Pin the SQL terms and prove the predicate agrees
    on each class."""
    sql = _MIGRATION.read_text()
    assert "SET owner_user_id = created_by" in sql
    assert "created_via = 'bootstrap'" in sql
    assert "created_by IS NOT NULL" in sql
    assert "created_by <> 'api'" in sql
    assert "owner_user_id IS NULL" in sql, "the backfill is not idempotent"

    # The derivation yields the backfilled value for the class the backfill
    # writes, and None for every class it leaves NULL.
    assert holder_user_id(_row(created_via="bootstrap",
                               created_by="u-1")) == "u-1"
    for created_via in ("provisioned", "recovery", None):
        assert holder_user_id(_row(created_via=created_via,
                                   created_by="u-1")) is None


def test_the_row_projections_read_the_holder_column():
    """The column is only worth having if the lanes RETURN it: a projection that
    omits `owner_user_id` makes every row read as derived, so an explicit holder
    would silently lose its power (the #2446 note on `api_key_by_id`)."""
    from tortoise import supabase_control

    src = Path(supabase_control.__file__).read_text()
    fn = src.split("def api_key_by_id", 1)[1].split("\ndef ", 1)[0]
    assert "owner_user_id" in fn, "api_key_by_id no longer projects the holder"

    ha_src = _HOSTED_API.read_text()
    rotatable = ha_src.split("def _rotatable_key_row", 1)[1].split("\ndef ", 1)[0]
    assert "k.owner_user_id" in rotatable, (
        "_rotatable_key_row (the registry lane's row read) no longer returns the "
        "holder — the member slice would silently 403 on that lane")

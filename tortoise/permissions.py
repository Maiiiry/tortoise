"""Permission vocabulary + role bundles — phase 3 of the #2427 model (#8692).

WHAT THIS MODULE IS. The gate vocabulary the key and member surfaces are
gated by. Before this module a gate was a ROLE: every key/member endpoint
picked one of ``_require_owner_admin`` / ``_require_owner`` /
``_require_keys_manage``, so "own key vs any key" could not be expressed at
all — a role decides WHO a caller is, never WHICH row they may touch
(#2427 brief §1, §4.1). Permissions are now the primitive and roles are
bundles over them (``ROLE_BUNDLES``), which is the brief's recommendation
and #2446's own framing ("roles = bundles").

DESIGN CONSTRAINTS THIS MODULE HOLDS.

* **Flat, machine-readable values.** A nested permission shape makes the
  escalation CHECK vacuous — the constraint recorded in the scope
  vocabulary's own header
  (``supabase/migrations/20260901000001_graphs_and_key_scopes.sql``). So a
  permission is one dotted string, and ``RoleBundle`` is a flat frozenset of
  them. ``test_permissions_8692.py`` pins the flatness.
* **The data-plane scopes keep their RECORDED spelling.** They are capability
  scopes carried on the key itself (``graphs:read`` — colon form, enforced by
  ``hosted_api._require_scope``), not role permissions. The issue's scope says
  "plus the existing data-plane scopes unchanged", so they are declared here
  with their existing spelling rather than renamespaced: the vocabulary must
  describe what exists, not a preferred shape.
* **``org.export`` / ``org.delete`` stay OWNER-ONLY.** They are in the enum —
  encoding them as permissions is fine — but they are NOT in the ``admin``
  bundle and MUST NOT be widened below ``role == 'owner'``. #2427 brief §4.2
  keeps this strict deliberately: "a permission that can be delegated to an
  admin is a different product promise than 'only the account owner can
  export or delete'". ``OWNER_ONLY_PERMISSIONS`` is the single declaration of
  that promise and the bundle construction is asserted against it.

  OVERRIDES: the "design permissions first, then let roles be *derived*
  bundles" default (the brief's own recommendation, §3 convergent pattern 2) —
  two permissions are pinned to the owner role instead of being grantable, so
  the ``admin`` bundle is NOT "everything minus nothing": the two irreversible
  whole-tenant powers stay a property of the role, not of a grantable bit.

* **``keys.mint.own`` for a member is the EPHEMERAL class only.** The member
  bundle grants the permission, and POLICY A (#2297 owner decision
  2026-09-05, #2535 HARD STOP, #2380) bounds what the mint endpoint will
  actually issue on it: the 24h cap-exempt ``bootstrap`` session credential.
  A member *durable* mint is the thing POLICY A forbids, and it is a reopen of
  #2297 — not a code change here. The bundle therefore does not widen
  anything: it names the door that already exists (``POST /v1/session/key``
  ``purpose=bootstrap`` is member-open today, brief §2.2).

AUTHORITY, NOT JUST MEMBERSHIP. ``authority_for`` is the ONE decision
function; the I/O stays where it already lives (``hosted_api.authorize`` for
the role-gated lanes, ``hosted_api._require_owner``'s own read for the strict
owner lanes, which must keep its single role+status query and the
removed-owner replay of #302). A caller asks "may this principal perform
<permission> on <target>?" and gets the admitted authority back.

    "owner" / "admin"  — the role path: the role's bundle contains it.
    "own"              — the HOLDER path: a non-admin role whose bundle
                         contains the permission AND who holds <target>.
    None               — refused; the caller raises its historical 403.

Behavioural parity is the contract of this phase: the wrappers in
``hosted_api`` keep their byte-identical 403 details, and ``authority_for``
refuses every non-admin path except the holder-scoped ``.own`` one, so no
surface widens on day one (brief §8, and the #2446 pins in
``tests/test_own_key_selfservice_2446.py``).

The holder predicate. ``holder_user_id`` is the HOLDER of a credential row —
the user who may self-manage it — and it is distinct from ``created_by``, the
MINTER (brief §2.3 item 4). An owner who mints a key and hands it to a member
leaves ``created_by = owner``; only an explicit holder makes it the member's.
That is the correction ``api_keys.owner_user_id`` (phase 3, this issue)
carries, and it is why a member who merely holds a COPY of an org-owned
shared key can never rotate or retire it (``owner_user_id IS NULL`` ⇒
org-owned ⇒ admin-managed).
"""
from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

__all__ = [
    "OWNER_ONLY_PERMISSIONS",
    "OWN_PERMISSIONS",
    "ROLE_BUNDLES",
    "ROLE_NAMES",
    "SELF_SERVICE_CREATED_VIA",
    "SURFACE_PERMISSIONS",
    "Permission",
    "authority_for",
    "bundle_for",
    "holder_user_id",
    "permission_detail",
    "role_allows",
]


class Permission(StrEnum):
    """The flat permission vocabulary (#2427 brief §4.1 + the existing scopes).

    ``StrEnum`` on purpose: ``Permission.KEYS_READ_ALL == "keys.read.all"`` and
    the two hash alike, so a call site may pass either the member or its value
    and the bundle check, the JSON envelope and a DB row all use the same
    string. (``str.__hash__`` wins the MRO, which is what makes a set/frozenset
    of members behave for a plain-string lookup.) The values are stable API —
    they are what an audit record and a future per-member grant (phase 4) name
    — so they are never renamed in place.
    """

    # ── org surface ────────────────────────────────────────────────────
    ORG_READ = "org.read"
    ORG_EXPORT = "org.export"
    ORG_IMPORT = "org.import"
    ORG_DELETE = "org.delete"
    TEAM_SETTINGS = "team.settings"

    # ── members / invites ──────────────────────────────────────────────
    MEMBERS_READ = "members.read"
    MEMBERS_MANAGE = "members.manage"

    # ── key lifecycle ──────────────────────────────────────────────────
    # `.own` is scoped to the row's HOLDER (`holder_user_id`), `.any` covers
    # every row in the org. The pair is the point: the issue that motivated
    # this vocabulary (#2446) needed "this member may touch this key".
    KEYS_MINT_TEAM = "keys.mint.team"
    KEYS_MINT_OWN = "keys.mint.own"
    KEYS_READ_OWN = "keys.read.own"
    KEYS_READ_ALL = "keys.read.all"
    KEYS_ROTATE_OWN = "keys.rotate.own"
    KEYS_ROTATE_ANY = "keys.rotate.any"
    KEYS_REVOKE_OWN = "keys.revoke.own"
    KEYS_REVOKE_ANY = "keys.revoke.any"
    KEYS_TOGGLE_OWN = "keys.toggle.own"
    KEYS_TOGGLE_ANY = "keys.toggle.any"
    KEYS_AUDIT_READ = "keys.audit.read"
    KEYS_RECOVER_ANY = "keys.recover.any"

    # ── data plane (existing capability scopes, RECORDED spelling) ─────
    GRAPHS_READ = "graphs:read"
    GRAPHS_WRITE = "graphs:write"
    GRAPHS_CREATE = "graphs:create"
    GRAPHS_DELETE = "graphs:delete"
    TEAM_MANAGE = "team:manage"
    KEYS_MANAGE = "keys:manage"


#: The ``.own`` half of a key verb — a permission whose admission depends on
#: the TARGET ROW's holder, not on the role alone (brief §4.2).
OWN_PERMISSIONS: frozenset[Permission] = frozenset({
    Permission.KEYS_MINT_OWN,
    Permission.KEYS_READ_OWN,
    Permission.KEYS_ROTATE_OWN,
    Permission.KEYS_REVOKE_OWN,
    Permission.KEYS_TOGGLE_OWN,
})

#: Permissions that stay bound to ``role == 'owner'``. The ``admin`` bundle is
#: every OTHER permission (see the OVERRIDES note in the module docstring):
#: both are irreversible or whole-tenant disclosures, and making either
#: grantable below owner is a product change, not a refactor (brief §4.2).
OWNER_ONLY_PERMISSIONS: frozenset[Permission] = frozenset({
    Permission.ORG_EXPORT,
    # `org.import` is in the same class and was the one whole-tenant surface
    # #2427 brief §2.2's matrix omitted (`import_org` shares the pre-#8692
    # `_require_owner` gate verbatim). Naming it keeps the vocabulary
    # exhaustive over the surfaces the strict-owner gate actually guards
    # instead of making import ride `org.export` — a named permission that
    # does not describe what it gates is worse than one more member.
    Permission.ORG_IMPORT,
    Permission.ORG_DELETE,
})

#: The roles, unchanged (brief §2.1): three hats, no new one. ``owner`` is
#: never invitable and never demotable; ``admin`` is owner-minus-export/delete;
#: ``member`` is the control-plane read plus the holder-scoped key verbs.
ROLE_NAMES: tuple[str, ...] = ("owner", "admin", "member")

#: ``member`` = ``org.read`` + the current and future ``keys.*.own`` verbs +
#: ``keys.audit.read`` (#8692 scope item 3). Deliberately WITHOUT
#: ``keys.read.all`` (the org-wide inventory is ``keys.read.all``; today's
#: ``GET /v1/team/keys`` passes a member through the CAPABILITY gate — the
#: brief §0 residual this vocabulary names but does not change), and without
#: ``members.*`` / ``team.settings`` / ``keys.*.any``.
_MEMBER: frozenset[Permission] = frozenset({
    Permission.ORG_READ,
    Permission.KEYS_MINT_OWN,
    Permission.KEYS_READ_OWN,
    Permission.KEYS_ROTATE_OWN,
    Permission.KEYS_REVOKE_OWN,
    Permission.KEYS_TOGGLE_OWN,
    Permission.KEYS_AUDIT_READ,
})

#: ``owner`` = all; ``admin`` = all except the two owner-only powers;
#: ``member`` = the set above. Derived, not restated, so a new enum member
#: lands in ``owner`` automatically and in ``admin`` unless it is owner-only.
ROLE_BUNDLES: Mapping[str, frozenset[Permission]] = {
    "owner": frozenset(Permission),
    "admin": frozenset(Permission) - OWNER_ONLY_PERMISSIONS,
    "member": _MEMBER,
}

#: The class a member's self-service credential is minted under
#: (``POST /v1/session/key`` ``purpose=bootstrap``). A ``recovery`` row is a
#: DURABLE, POLICY A-gated mint (#2380) and is never self-service.
SELF_SERVICE_CREATED_VIA = "bootstrap"

#: ``_mint_key`` attributes a key-driven mint to the literal ``"api"`` — never
#: a key id and never a user. A non-user attribution can never be a holder.
_NON_USER_ATTRIBUTION = "api"

#: The 403 detail per permission class. These strings are BYTE-IDENTICAL to
#: the pre-#8692 role gates on purpose: a distinct message per case would turn
#: the gate into an ownership oracle, and two suites pin the exact wording
#: (tests/test_own_key_selfservice_2446.py, tests/test_suspension_parity.py).
_DETAIL_OWNER = "Requires owner role in team"
_DETAIL_OWNER_ADMIN = "Requires owner or admin role in team"

#: surface → the permissions that admit it, per #2427 brief §2.2 (the
#: acceptance table: "every surface in brief §2.2 is gated by a named
#: permission, not a role"). This mapping is the MACHINE-READABLE half of that
#: acceptance — ``test_permissions_8692.py`` asserts it is exhaustive over the
#: enum's key/member/org members and that every named permission exists, so an
#: endpoint cannot silently pick a role again without reddening a test.
#:
#: A surface lists MORE than one permission where the holder-scoped half is
#: the narrow alternative to the org-wide one (rotate: ``.any`` else
#: ``.own``). Keys are the endpoint shapes, not the FastAPI function names, so
#: a handler rename does not churn this table.
SURFACE_PERMISSIONS: Mapping[str, tuple[Permission, ...]] = {
    # #2427 §2.2 rows 1-13 (the key lifecycle + members/org control plane).
    "GET /v1/team": (Permission.ORG_READ,),
    "POST /v1/team/keys": (Permission.KEYS_MINT_TEAM,),
    "POST /v1/team/keys/{id}/rotate": (Permission.KEYS_ROTATE_ANY,
                                       Permission.KEYS_ROTATE_OWN),
    "GET /v1/team/keys": (Permission.KEYS_READ_OWN, Permission.KEYS_READ_ALL),
    "DELETE /v1/team/keys/{id}": (Permission.KEYS_REVOKE_ANY,
                                  Permission.KEYS_REVOKE_OWN),
    "PATCH /v1/team/keys/{id}": (Permission.KEYS_TOGGLE_ANY,
                                 Permission.KEYS_TOGGLE_OWN),
    "POST /v1/session/key?purpose=bootstrap": (Permission.KEYS_MINT_OWN,),
    "POST /v1/session/key?purpose=recovery": (Permission.KEYS_RECOVER_ANY,),
    "GET /v1/organizations/{id}/members": (Permission.MEMBERS_READ,),
    "POST /v1/organizations/{id}/members": (Permission.MEMBERS_MANAGE,),
    "PATCH /v1/organizations/{id}/members/{user_id}": (Permission.MEMBERS_MANAGE,),
    "DELETE /v1/organizations/{id}/members/{user_id}": (Permission.MEMBERS_MANAGE,),
    "POST /v1/invites": (Permission.MEMBERS_MANAGE,),
    "GET /v1/invites": (Permission.MEMBERS_READ,),
    "DELETE /v1/invites/{id}": (Permission.MEMBERS_MANAGE,),
    "POST /v1/invites/{id}/resend": (Permission.MEMBERS_MANAGE,),
    "POST /v1/invites/{id}/expire": (Permission.MEMBERS_MANAGE,),
    "PATCH /v1/team/dashboard-login": (Permission.TEAM_SETTINGS,),
    "GET /v1/organizations/{id}/export": (Permission.ORG_EXPORT,),
    "POST /v1/organizations/{id}/import": (Permission.ORG_IMPORT,),
    "DELETE /v1/organizations/{id}": (Permission.ORG_DELETE,),
    # #2427 §2.2 last row — the data plane, whose gates are CAPABILITY scopes
    # on the key (`_require_scope`), not role bundles. Listed so the table is
    # exhaustive over the brief's matrix; no role bundle admits them through
    # `authority_for`, which is why they appear on no role's admission path.
    "data plane (every handler)": (Permission.GRAPHS_READ, Permission.GRAPHS_WRITE),
}


def bundle_for(role: str | None) -> frozenset[Permission]:
    """The permission bundle for a role; empty for an unknown/absent role.

    An unknown role gets the EMPTY bundle, never a default one: a membership
    row with a role this vocabulary does not know must fail closed.
    """
    return ROLE_BUNDLES.get(role or "", frozenset())


def role_allows(role: str | None, permission: Permission | str | None) -> bool:
    """True when ``role``'s bundle contains ``permission``.

    ``permission is None`` is the LEGACY COARSE GATE (owner-or-admin, no named
    permission yet) and is the only reason this helper is not a plain set
    membership test — it keeps ``_require_owner_admin``'s historical behaviour
    expressible for callers that have not been named over yet.
    """
    if permission is None:
        return role in ("owner", "admin")
    return permission in bundle_for(role)


def permission_detail(permission: Permission | str | None) -> str:
    """The 403 detail for a refused permission — one of the two historical
    strings, so no surface's refusal wording changes."""
    if permission in OWNER_ONLY_PERMISSIONS:
        return _DETAIL_OWNER
    return _DETAIL_OWNER_ADMIN


def holder_user_id(row: Mapping | dict | None) -> str | None:
    """The HOLDER of a credential row, or None when it is org-owned.

    THE ONE holder predicate — never read ``created_by`` inline. ``created_by``
    is the MINTER (brief §2.3 item 4): a member-minted DURABLE row carries the
    member's id while being org-owned, and a key-driven mint carries the
    literal ``"api"``. Neither is a holder.

    Resolution, in order:

    1. ``owner_user_id`` set (phase 3 — ``api_keys.owner_user_id``): that user
       is the holder. This is the field an owner uses to hand a minted key to a
       member (#2446's owner-mints-for-member case), and it OVERRIDES the class
       derivation below, so an org-owned durable row can become self-managed
       the day a surface can set it.
    2. ``owner_user_id`` NULL/absent — no explicit holder. The row is
       org-owned UNLESS it is a self-service row (``created_via='bootstrap'``),
       where the minter IS the holder by construction: that mint is the
       server handing the caller their own credential.

    Why the derivation survives the column. ``owner_user_id`` is delivered by
    an ADDITIVE migration (#8692 acceptance: "additive and reversible"), and
    the additive backfill stamps it on existing self-service rows only. A row
    minted AFTER the migration therefore arrives with the column unset until a
    writer sets it, and step 2 keeps that row's holder stable — it yields
    exactly what the backfill would have written (``created_by`` iff
    ``created_via='bootstrap'``), so the two steps agree on every row in the
    table and neither reads ``created_by`` as a holder outside the
    self-service class.
    """
    if not row:
        return None
    explicit = row.get("owner_user_id")
    if explicit:
        return None if explicit == _NON_USER_ATTRIBUTION else explicit
    if row.get("created_via") != SELF_SERVICE_CREATED_VIA:
        return None
    minter = row.get("created_by")
    if not minter or minter == _NON_USER_ATTRIBUTION:
        return None
    return minter


def authority_for(role: str | None,
                  permission: Permission | str | None, *,
                  user_id: str | None = None,
                  target: Mapping | dict | None = None) -> str | None:
    """THE decision function: the admitted authority, or None when refused.

    Two admission paths, and only two:

    * **role path** — an owner/admin whose bundle contains ``permission`` is
      admitted AS that role. This is where ``org.export`` / ``org.delete``
      stay owner-only: they are absent from the ``admin`` bundle, so an admin
      is refused exactly as it was before this vocabulary existed.
    * **holder path** — a NON-admin role whose bundle contains ``permission``
      is admitted as ``"own"`` only when ``permission`` is an ``.own`` verb and
      ``target``'s holder is ``user_id``. Nothing else admits a non-admin.

    The holder path is deliberately the only widening, and it is not a
    widening at all: it reproduces #2446's member slice exactly (the caller
    manages the credential the server handed them, and nothing else), and it
    requires an EXPLICIT target — a caller that forgets to pass one gets a
    refusal, never an admission.

    ``role=None`` (no active membership) is always refused: holding a row is
    not membership (a REMOVED member must not re-enable the credential they
    were left with — #2446 review P2-1).
    """
    if role is None:
        return None
    if permission is None:
        # Legacy coarse gate: owner-or-admin, no named permission yet.
        return role if role in ("owner", "admin") else None
    if role in ("owner", "admin"):
        return role if permission in bundle_for(role) else None
    if permission not in bundle_for(role):
        return None
    if permission not in OWN_PERMISSIONS:
        # A non-admin role's non-own permissions (``org.read``,
        # ``keys.audit.read``) are DECLARED in the bundle but have no
        # holder-scoped admission in this phase — the surfaces that would use
        # them are phase 4+, and admitting them here would widen a surface
        # nobody has gated yet.
        return None
    if user_id is None:
        return None
    holder = holder_user_id(target)
    if holder is not None and holder == user_id:
        return "own"
    return None

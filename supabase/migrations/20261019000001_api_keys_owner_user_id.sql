-- Migration: api_keys.owner_user_id — the HOLDER (phase 3 of the #2427
-- permission model, #8692)
--
-- WHAT THIS COLUMN IS. `created_by` is the MINTER of a key; this column is the
-- HOLDER — the user who may self-manage the credential. The distinction is the
-- correction that makes member self-service (#2446) reachable for the
-- owner-mints-for-member case: an owner who mints a key and hands it to a
-- member leaves `created_by = <owner>`, so `created_by` alone can never say
-- "this member may replace or retire this key". With the holder recorded, it
-- can. `#2427` brief §2.3 item 4 and §4.2.
--
-- SEMANTICS
--   owner_user_id IS NULL      → ORG-OWNED / shared (admin-managed). A member
--                                who merely holds a COPY of a shared key must
--                                never be able to rotate or retire it, so NULL
--                                is the safe default and stays so.
--   owner_user_id = <user id>  → that user HOLDS the credential and may
--                                self-manage it (subject to their role still
--                                admitting the `keys.*.own` permission AND
--                                their membership still being ACTIVE — a
--                                removed member's holder bit does not survive
--                                their membership, #2446 review P2-1).
--
-- The read-time predicate is `tortoise.permissions.holder_user_id` — the ONE
-- declaration. It prefers this column and derives the holder from the
-- self-service class (`created_via='bootstrap'`, minter = holder) only when the
-- column is unset. That derivation is not redundant with the backfill: this
-- migration is ADDITIVE (its acceptance criterion) and therefore does not
-- redefine any mint RPC, so a row minted after it arrives with the column
-- unset, and the derivation yields exactly what the backfill would have
-- written. Column and derivation agree on every row in the table —
-- `tests/test_permissions_8692.py` pins that agreement.
--
-- BACKFILL (brief §4.2, verbatim): "self-service bootstrap mints get
-- owner_user_id = created_by; every other existing row stays NULL."
-- A `provisioned` / `recovery` row stays NULL even when created_by is a user,
-- because those are org-owned mints by construction — the self-service mint is
-- the bootstrap class (the only class a member can mint; `recovery` is
-- POLICY A-gated, #2380).
--
-- ADDITIVE + REVERSIBLE. No table rewrite, no constraint change, no function
-- redefinition. The rollback is
--     ALTER TABLE public.api_keys DROP COLUMN IF EXISTS owner_user_id;
-- and it loses only data that is derivable from `created_via` + `created_by`
-- for every row this migration can write (see the agreement note above), which
-- is what makes the column safe to drop without an export.
--
-- ⛔ The column is NOT part of the MCP tool surface or the public
-- `TortoiseSDK` surface (AGENTS.md MCP/SDK Surface Approval) — it is an
-- internal control-plane column behind the existing gates.

-- ============================================================================
-- 1) The column
-- ============================================================================
-- No FK to an auth table: `created_by` has none either, and the same column
-- legitimately carries the non-user attribution `'api'` on the mint side. The
-- holder is always a real user id or NULL, which the writer (not the DB)
-- enforces — a CHECK here would be a second source of truth for a rule the
-- predicate already owns.
ALTER TABLE public.api_keys
    ADD COLUMN IF NOT EXISTS owner_user_id text;

COMMENT ON COLUMN public.api_keys.owner_user_id IS
    'The HOLDER of the key (the user who may self-manage it), distinct from '
    'created_by (the MINTER). NULL = org-owned/shared, admin-managed. '
    'Read through tortoise.permissions.holder_user_id — never inline. '
    'Phase 3 of the #2427 permission model (#8692).';

-- ============================================================================
-- 2) Backfill — self-service bootstrap mints only (idempotent)
-- ============================================================================
-- `WHERE owner_user_id IS NULL` makes a re-run a no-op, so this file is safe
-- to re-apply (every migration in this tree is, by the append-only +
-- idempotent convention). `created_by <> 'api'` is excluded because a
-- key-driven mint attributes to the literal 'api' and that can never be a
-- holder — `holder_user_id` refuses the same value, so the two agree.
UPDATE public.api_keys
   SET owner_user_id = created_by
 WHERE owner_user_id IS NULL
   AND created_via = 'bootstrap'
   AND created_by IS NOT NULL
   AND created_by <> 'api';

-- ============================================================================
-- 3) Grants — column allowlist parity with 0007
-- ============================================================================
-- 0007 grants `authenticated` an EXPLICIT column allowlist (and revokes the
-- table-level privilege), so a column added later is invisible to that role
-- unless it is named here. `owner_user_id` is non-secret identity metadata —
-- the same class as `created_by`, which 0007 already grants — so it joins the
-- allowlist; `anon`/`public` stay revoked at the table level.
GRANT SELECT (owner_user_id) ON public.api_keys TO authenticated;

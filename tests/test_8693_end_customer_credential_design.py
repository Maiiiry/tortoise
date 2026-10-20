"""#8693 — the end-customer credential design's measured claims, pinned to the code.

Canonical document:
``docs/scoping/2026-10-19-8693-end-customer-credential-design.md``.

That document is a *design deliverable*, so its measured claims ARE the artifact.
Three of them were wrong in the first revision (PR #9178 review) because nothing
tied the prose to the code it cites:

1. per-end-user Replace/Revoke was described as shipped, while ``rotate`` and
   ``DELETE /v1/team/keys/{key_id}`` are pinned to the caller's OWN org
   (``_ensure_key_in_pinned_org(..., required=True)``) — a target key in another
   team is a ``403 "Not your API key"``, and a minted (``deleg=0``) credential
   cannot rotate anything at all;
2. §1 claimed there is no key-auth graph-creation path, while
   ``POST /v1/organizations/{org_id}/graphs`` ships one — it needs an owner-class
   ``graphs:create`` key (``_OWNER_SCOPE_ALLOWLIST``, never ``_MINTABLE_SCOPES``)
   and rejects ``deleg=0`` keys;
3. §8 cited two ``api_key_mint`` lanes as evidence for ``api_key_create`` /
   ``api_key_rotate`` — a third action name, conflated.

The tests are of two kinds, both deliberate:

* **code pins** — the property the document asserts must stay true of
  ``tortoise/hosted_api.py`` (these fail if the pin, the scope class, or the
  audit action drifts, i.e. exactly when the document becomes stale); and
* **prose pins** — the corrected sentences must still be present, and the
  refuted ones absent (these fail on the PRE-FIX document, which is the
  non-vacuity proof for a docs-only fix).

Every assertion imports nothing that needs a database: the module under test is
parsed with ``ast`` and only ``_ensure_key_in_pinned_org`` is imported, so this
file belongs to the fast pool.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HOSTED_API = REPO / "tortoise" / "hosted_api.py"
DESIGN_DOC = (
    REPO / "docs" / "scoping" / "2026-10-19-8693-end-customer-credential-design.md"
)


# ── helpers ────────────────────────────────────────────────────────────────

def _module() -> ast.Module:
    return ast.parse(HOSTED_API.read_text(encoding="utf-8"))


def _function(name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(_module()):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"tortoise/hosted_api.py no longer defines {name}()")


def _calls(node: ast.AST, func_name: str) -> list[ast.Call]:
    """Every ``func_name(...)`` call inside ``node``."""
    out: list[ast.Call] = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        fn = sub.func
        called = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
        if called == func_name:
            out.append(sub)
    return out


def _kwarg_true(call: ast.Call, name: str) -> bool:
    for kw in call.keywords:
        if kw.arg == name and isinstance(kw.value, ast.Constant) and kw.value.value is True:
            return True
    return False


def _string_constants(node: ast.AST) -> list[str]:
    return [
        sub.value
        for sub in ast.walk(node)
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
    ]


def _membership_checks(node: ast.AST, needle: str) -> list[tuple[ast.Compare, ast.AST]]:
    """``(Compare, container)`` for membership tests of ``needle`` (``x in y``/``x not in y``).

    Structural on purpose: a membership TEST cannot be satisfied by the function's
    docstring or by the detail string of a raise, which is what made the old
    ``_string_constants`` assertions pass with the check deleted. Both operand
    orders are covered (``needle in container`` and ``needle not in container``).
    """
    out: list[tuple[ast.Compare, ast.AST]] = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Compare):
            continue
        for op, comparator in zip(sub.ops, sub.comparators):
            if not isinstance(op, (ast.In, ast.NotIn)):
                continue
            left = sub.left
            if isinstance(left, ast.Constant) and left.value == needle:
                out.append((sub, comparator))
            elif isinstance(comparator, ast.Constant) and comparator.value == needle:
                out.append((sub, left))
    return out


def _audit_actions(node: ast.AST) -> list[str]:
    """The exact action literals passed to ``_async_audit(request, id, "<action>", …)

    Exact, not a substring: ``api_key_created`` (an analytics event imported at
    module scope) contains ``api_key_create``, so a substring test would keep
    passing while §8's row became false.
    """
    actions: list[str] = []
    for call in _calls(node, "_async_audit"):
        if len(call.args) < 3:
            continue
        action = call.args[2]
        if isinstance(action, ast.Constant) and isinstance(action.value, str):
            actions.append(action.value)
    return actions


_CITED_POINT = re.compile(r":(\d{4,5})\b")
_CITED_SPAN = re.compile(r"hosted_api\.py:(\d{4,5})-(\d{4,5})")


def _source_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _route_line(src: list[str], route: str) -> int:
    """1-based line of the ``@app.<verb>("<route>")`` decorator."""
    for i, line in enumerate(src, 1):
        if line.startswith("@app.") and f'"{route}"' in line:
            return i
    raise AssertionError(f"tortoise/hosted_api.py no longer registers {route}")


def _doc() -> str:
    return DESIGN_DOC.read_text(encoding="utf-8")


# ── 1. the lifecycle verbs are pinned to the caller's own org ──────────────

def test_rotate_carries_the_required_org_pin():
    """``rotate`` must keep the pin §5.2 measures (a cross-team target is 403)."""
    rotate = _function("rotate_api_key")
    pins = _calls(rotate, "_ensure_key_in_pinned_org")
    assert pins, (
        "rotate_api_key lost its _ensure_key_in_pinned_org call — the design doc (§5.2, §8) "
        "states a builder-org credential cannot rotate a key in another team because of it. "
        "If cross-team rotate was deliberately shipped, update §5.2/§5.3/§7 row 4/§9.1/§10 first."
    )
    assert any(_kwarg_true(call, "required") for call in pins), (
        "rotate_api_key's org pin is no longer required=True — the helper then no-ops on a "
        "None/absent org, which is the fail-OPEN edge §5.2's 403 claim rests against"
    )


def test_rotate_rejects_minted_callers():
    """A ``deleg=0`` credential cannot rotate — it cannot even rotate itself."""
    rotate = _function("rotate_api_key")
    rejects = _calls(rotate, "_reject_minted_delegated_key")
    assert rejects, (
        "rotate_api_key must keep _reject_minted_delegated_key — §3.1/§5.2 state that the "
        "end-user credential cannot self-rotate (hosted_api.py:12004 in the doc)"
    )
    assert any(
        "rotate" in arg
        for call in rejects
        for arg in _string_constants(call)  # the action label, e.g. "rotate API keys"
    ), "the minted-key rejection must still name the rotate action it refuses"


def test_delete_is_pinned_on_both_lanes():
    """``DELETE`` pins in the supabase lane AND the registry lane (§8 evidence row)."""
    revoke = _function("revoke_api_key")
    pins = _calls(revoke, "_ensure_key_in_pinned_org")
    assert len(pins) == 2, (
        f"revoke_api_key has {len(pins)} _ensure_key_in_pinned_org call(s), expected 2 "
        "(supabase lane + registry lane). The design doc's §8 row says the pin covers BOTH "
        "lanes; a lane that loses it becomes the cross-team revoke §5.3 says does not exist."
    )
    assert all(_kwarg_true(call, "required") for call in pins), (
        "both DELETE lanes must keep required=True (the resolved org owns the key "
        "unconditionally)"
    )


def test_the_pin_actually_refuses_a_cross_team_target():
    """The measured fact behind §5.2: mismatched orgs → 403, never a silent pass."""
    from fastapi import HTTPException

    from tortoise.hosted_api import _ensure_key_in_pinned_org

    with pytest.raises(HTTPException) as excinfo:
        _ensure_key_in_pinned_org("org_builder", "org_end_user", required=True)
    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == "Not your API key"
    # and the no-pin edge really no-ops, so the pin's absence is what widens reach
    _ensure_key_in_pinned_org(None, "org_end_user", required=False)


# ── 2. key-auth graph creation exists, owner-class only ────────────────────

def test_key_auth_graph_creation_exists_and_is_owner_class():
    """§1's corrected second reason: the route ships, ``graphs:create`` gates it."""
    from tortoise.hosted_api import _MINTABLE_SCOPES, _OWNER_SCOPE_ALLOWLIST

    create = _function("create_org_graph")
    routes = [ast.unparse(d) for d in create.decorator_list]
    assert any("/v1/organizations/{org_id}/graphs" in r for r in routes), (
        "create_org_graph is no longer registered at POST /v1/organizations/{org_id}/graphs — "
        "§1/§8 would then be describing a route that does not exist"
    )
    scope_checks = _membership_checks(create, "graphs:create")
    assert scope_checks, (
        "create_org_graph lost its `\"graphs:create\" not in scopes` membership test — §1's "
        "corrected reason rests on the scope being checked (and on it being owner-class). "
        "A string constant elsewhere in the function (its docstring says graphs:create twice) "
        "cannot satisfy this assertion."
    )
    assert any("scopes" in ast.unparse(container) for _, container in scope_checks), (
        "the graphs:create test no longer consults the resolved `scopes` local"
    )
    assert any(
        isinstance(sub, ast.Compare)
        and len(sub.ops) == 1
        and isinstance(sub.ops[0], ast.Eq)
        and isinstance(sub.left, ast.Call)
        and isinstance(sub.left.func, ast.Attribute)
        and sub.left.func.attr == "get"
        and sub.left.args
        and isinstance(sub.left.args[0], ast.Constant)
        and sub.left.args[0].value == "delegation_depth"
        and isinstance(sub.comparators[0], ast.Constant)
        and sub.comparators[0].value == 0
        for sub in ast.walk(create)
    ), (
        "create_org_graph must keep its minted-key rejection — the `delegation_depth == 0` "
        "comparison, not merely a docstring or detail string naming it"
    )

    assert "graphs:create" in _OWNER_SCOPE_ALLOWLIST, (
        "graphs:create left the owner-class allowlist — §1's corrected reason says an "
        "OWNER-class key can create graphs programmatically today"
    )
    assert "graphs:create" not in _MINTABLE_SCOPES, (
        "graphs:create became MINTABLE — a per-end-user child could then provision graphs, "
        "which reverses the child policy §3.1 and §1's second reason both rely on"
    )


# ── 3. the three audit action names are not conflated ─────────────────────

def test_audit_action_names_are_distinct_and_as_documented():
    """``api_key_create``/``api_key_rotate`` are the lifecycle actions; ``api_key_mint``
    is the #2446 member self-service / recovery lane's THIRD name (§8 rows)."""
    assert "api_key_create" in _audit_actions(_function("create_api_key")), (
        "the mint handler must still audit as exactly api_key_create (§8) — asserted on the "
        "action literal handed to _async_audit, so the analytics event api_key_created "
        "cannot stand in for it"
    )
    assert "api_key_rotate" in _audit_actions(_function("rotate_api_key")), (
        "the rotate handler must still audit as exactly api_key_rotate (§8)"
    )
    assert "api_key_mint" in _audit_actions(_module()), (
        "api_key_mint (the member self-service / recovery lanes) is gone — §8's third-action "
        "row and #9177's premise (create+rotate audited, mint lanes a different action) need it"
    )


def test_the_doc_cites_the_api_key_mint_audit_lines_that_really_are_them():
    """§8's ``api_key_mint`` row must cite lines that ARE the mint audit calls.

    Non-vacuity: at the #9178 head the row carried ``:27258`` and ``:27475`` — two
    numbers a merge had invalidated (+96), where ``:27258`` is a comment and
    ``:27475`` is ``_require_owner_admin(...)``. The old assertion here was
    ``"api_key_mint" in HOSTED_API.read_text()``, which passes wherever the lanes
    live; this pin reads the doc's OWN numbers and asks the module what is on them,
    so the rot that shipped this row fails instead of blessing it.
    """
    src = _source_lines(HOSTED_API)
    cited = [
        int(number)
        for line in _doc().splitlines()
        if "api_key_mint" in line and "hosted_api.py" in line
        for number in _CITED_POINT.findall(line)
    ]
    assert cited, (
        "§8 must still cite the two api_key_mint lanes by line number — the row exists so "
        "the three audit action names are not conflated"
    )
    for number in cited:
        target = src[number - 1]
        assert "_async_audit" in target and "api_key_mint" in target, (
            f"§8 cites hosted_api.py:{number} as an api_key_mint audit call, but that line is "
            f"{target.strip()[:90]!r} — the citation has rotted (this module moves under "
            "merges; re-resolve the numbers and update §8 in the same commit)"
        )


def test_the_doc_oauth_span_really_contains_the_oauth_routes():
    """§0/§8 cite one ``hosted_api.py:A-B`` span as proof `#524` shipped.

    Non-vacuity: the pre-fix span ``:38504-38807`` stopped BEFORE ``/oauth/token``
    and ``/oauth/revoke`` — the two routes the row names — so the "evidence"
    pointed at DCR bookkeeping and the consent prelude. This pin requires the
    cited span to bracket the routes it is cited for.
    """
    src = _source_lines(HOSTED_API)
    routes = {
        route: _route_line(src, route)
        for route in (
            "/.well-known/oauth-protected-resource",
            "/oauth/token",
            "/oauth/revoke",
        )
    }
    spans = [
        (int(start), int(end))
        for line in _doc().splitlines()
        if "/oauth/" in line
        for start, end in _CITED_SPAN.findall(line)
    ]
    assert spans, "§0/§8 must still cite the OAuth route span that proves #524 shipped"
    for start, end in spans:
        for route, number in routes.items():
            assert start <= number <= end, (
                f"the design doc cites hosted_api.py:{start}-{end} as the OAuth evidence, but "
                f"{route} is registered at :{number} — outside the cited span (re-resolve the "
                "span and update every row that cites it)"
            )


def test_revoke_writes_no_audit_row_until_the_doc_is_updated():
    """Pins #9177's measured premise: no ``_async_audit`` in the DELETE handler.

    This is a doc pin, not a requirement: when somebody fixes #9177 the §8 row
    ("Key revoke writes NO audit row") and §7 row 7 become false, and this
    assertion is the reminder to update them in the same PR.
    """
    revoke = _function("revoke_api_key")
    assert not _calls(revoke, "_async_audit"), (
        "revoke_api_key now writes an audit row — #9177 appears fixed. Update the design doc's "
        "§8 row ('Key revoke writes NO audit row'), §7 row 7, and §10 precondition 4, then "
        "delete this assertion (it exists so the doc cannot go stale silently)."
    )


# ── 4. the corrected prose is present and the refuted prose is gone ───────

def test_design_doc_keeps_the_corrected_claims():
    """Non-vacuity: the fragments this PR's correction ADDED are absent pre-fix.

    Per-fragment, not blanket. ``create_org_graph`` predates the correction (§3.2
    already cited it), so its presence is not evidence; its load-bearing neighbour
    ``key-auth graph creation DOES exist`` is absent pre-fix and is. The rest
    (``_ensure_key_in_pinned_org``, ``not one shipped call``, ``cross-team
    lifecycle reach``, the ``session-authed only`` REMOVAL) discriminate too —
    measured against ``1e0f0f0b1``.
    """
    doc = _doc()

    # Finding 1 — Replace/Revoke reach, measured as absent.
    assert "_ensure_key_in_pinned_org" in doc, (
        "§5.2/§8 must cite the pin that makes cross-team rotate/revoke a 403 — the pre-fix "
        "document described the per-end-user Replace/Revoke story as shipped"
    )
    assert "not one shipped call" in doc, (
        "§5.2 must state that the Replace round trip is NOT one shipped call"
    )
    assert "cross-team lifecycle reach" in doc, (
        "§5.3 must name the missing cross-team lifecycle reach as a precondition"
    )

    # Finding 2 — key-auth graph creation exists, owner-class only.
    assert "key-auth graph creation DOES exist" in doc, (
        "§1 must state the corrected reason; the pre-fix text claimed '/v1/graphs is "
        "session-authed only … no key-auth path today either'"
    )
    assert "create_org_graph" in doc, (
        "§1/§8 must cite the key-driven provisioning route (create_org_graph)"
    )
    assert "session-authed only" not in doc, (
        "the refuted claim ('POST /v1/graphs is session-authed only') must not come back as a "
        "standalone fact — the key lane exists at POST /v1/organizations/{org_id}/graphs"
    )

    # Finding 3 — the third action name is labelled, not cited as create/rotate evidence.
    assert "api_key_mint" in doc, (
        "§8 must name api_key_mint as a THIRD action, not evidence for create/rotate"
    )
    for line in doc.splitlines():
        if "api_key_create" not in line and "api_key_rotate" not in line:
            continue
        assert "27258" not in line and "27475" not in line, (
            "a row about api_key_create/api_key_rotate cites a stale api_key_mint lane "
            f"({line.strip()[:80]}…) — re-resolve the line numbers (see the citation pins above)"
        )

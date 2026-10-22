"""#4122 — `risk` is the ONE classification axis; the hints and the permission are
DERIVED from it, so they cannot drift apart again.

The defect this pins: `tortoise_get_confidence`, `tortoise_compute_confidence` and
`tortoise_packs_list` advertised `readOnlyHint: True` while their handlers wrote to
the tenant graph, and nothing in the registry could notice — `annotations` and
`writes` were two INDEPENDENT declarations. A `graphs:read-only` MCP key could
therefore change a customer's stored knowledge, and MCP clients auto-approve on
`readOnlyHint: true`.

The owner ruling (2026-10-21, comment on #4122) replaced the A/B question with a
classification axis — `read | maintenance | write | destructive | admin` — where
`writes`, `readOnlyHint` and `idempotentHint` are DERIVED. Completion criteria 3
of that ruling is this file: *"A test that fails if any tool's `readOnlyHint` ever
disagrees with `risk == read`, and if `writes` ever disagrees with
`risk in {write, destructive, admin}`."*

Falsifiability: an assertion that survives its own bug is dead, so none of these
are value-sweeps alone — every criterion-3 assertion also builds an entry through
the CONSTRUCTOR with a deliberately wrong hand-set annotation and proves the
derivation corrected it (`test_read_only_hint_is_derived_from_risk`,
`test_writes_is_derived_from_risk`), or feeds a guard a `RiskProbe` violation and
proves it fires (`test_exemption_set_can_fail`). Delete the derivation in
`__post_init__` (or restore `writes` as a field) and this file reds.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import pathlib

import pytest
from mcp.types import ToolAnnotations

import tortoise.tool_registry as registry_module
from tortoise.tool_registry import (
    RETIRED_TOOL_REGISTRY,
    RISK_LEVELS,
    TOOL_REGISTRY,
    WRITE_RISKS,
    ToolDefinition,
    get_write_tool_names,
)


def _served() -> list[ToolDefinition]:
    """The served census (#3883): retired names still answer by name."""
    return [*TOOL_REGISTRY, *RETIRED_TOOL_REGISTRY]


def _entry(name: str) -> ToolDefinition:
    return next(t for t in _served() if t.name == name)


class _Stub:
    """Alias for the shared constructor-bypassing stand-in (#4122); see
    `tool_surface_capabilities.RiskProbe`."""

    def __new__(cls, *args, **kwargs):
        from tool_surface_capabilities import RiskProbe
        return RiskProbe(*args, **kwargs)


# ── The invariant (criterion 3) ───────────────────────────────────

def test_every_served_entry_declares_a_known_risk():
    """Criterion 1: `risk` is DECLARED on every registry entry, with a known value.

    A *defaulted* `risk` could not tell "declared `read`" from "forgot to
    declare" — the exact human error behind #4122 (nobody decided
    `packs_list` was read-only; the default did) — so the declaration half is
    pinned twice, at both places the omission could reappear:

    * `risk` is a REQUIRED field → a forgotten declaration is a `TypeError` at
      import (`test_a_forgotten_risk_is_a_type_error`);
    * every `ToolDefinition(...)` in the registry passes `risk=` explicitly
      (AST walk below), so re-adding a default cannot reopen the hole silently.

    The census assertion is the value half: an unknown value is a hard error in
    `__post_init__`.
    """
    served = _served()
    assert len(served) == 99, f"served census moved ({len(served)}) — re-derive, do not widen"
    for entry in served:
        assert entry.risk in RISK_LEVELS, (
            f"{entry.name}: risk={entry.risk!r} is not one of {RISK_LEVELS}")

    # The DECLARATION half, mechanically: no construction site may omit `risk=`.
    tree = ast.parse(pathlib.Path(registry_module.__file__).read_text())
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", getattr(node.func, "attr", None)) == "ToolDefinition"
    ]
    assert len(calls) == 99, (
        f"AST walk found {len(calls)} `ToolDefinition(...)` calls, expected 99 — the "
        f"pin below would be vacuous")
    missing = [node.lineno for node in calls
               if "risk" not in {kw.arg for kw in node.keywords}]
    assert not missing, (
        f"`ToolDefinition(...)` at line(s) {missing} omits `risk=` — an undeclared "
        f"entry would silently inherit the default (#4122)")


def test_a_forgotten_risk_is_a_type_error():
    """The omission path is CLOSED at the constructor (#4122 review, fail-open
    default): `risk` carries no default, so an entry that forgets to classify
    itself cannot be built at all — never silently derived as `read`
    (`writes=False` + `readOnlyHint=True`, the #4122 shape)."""
    field = next(f for f in dataclasses.fields(ToolDefinition) if f.name == "risk")
    assert field.default is dataclasses.MISSING and field.default_factory is dataclasses.MISSING, (
        "`risk` grew a default again — that default is the #4122 fail-open")
    with pytest.raises(TypeError, match="risk"):
        ToolDefinition(
            name="tortoise_probe_no_risk", id="surface.probe.no_risk",
            description="probe", annotations=ToolAnnotations(readOnlyHint=True),
            http_policy=True, sdk_method="create_point",
        )


def test_read_only_hint_is_derived_from_risk():
    """Criterion 3a: `readOnlyHint` is DERIVED from `risk == "read"`, in BOTH
    directions — and the served census agrees with the derivation.

    The registry sweep alone is a CENSUS PIN, not the criterion: every entry's
    hand-set annotation already agrees with its `risk` today, so the sweep alone
    would stay green if the derivation in `__post_init__` were deleted. What
    makes this the criterion is the constructor half below — an entry handed a
    DELIBERATELY WRONG `readOnlyHint` must come back corrected, for every value
    of `risk` and in both directions (a `read` entry handed `False` → `True`,
    a non-read entry handed `True` → `False`).
    """
    for risk in RISK_LEVELS:
        for hand_set in (True, False):
            entry = ToolDefinition(
                name=f"tortoise_probe_ro_{risk}_{hand_set}",
                id=f"surface.probe.ro_{risk}_{hand_set}".lower(),
                description="probe",
                annotations=ToolAnnotations(readOnlyHint=hand_set),
                http_policy=True, sdk_method="query", risk=risk,
            )
            assert entry.annotations.readOnlyHint is (risk == "read"), (
                f"risk={risk!r} handed readOnlyHint={hand_set}: the derivation did "
                f"NOT correct it — readOnlyHint is a second declaration again")

    for entry in _served():
        assert entry.annotations.readOnlyHint is (entry.risk == "read"), (
            f"{entry.name}: readOnlyHint={entry.annotations.readOnlyHint} but "
            f"risk={entry.risk!r} — the hint must be DERIVED, never hand-set")


def test_writes_is_derived_from_risk():
    """Criterion 3b: `writes` is a DERIVED property over `risk in WRITE_RISKS`,
    not a second declaration a lane can set.

    Asserting the served value only would compare the property against itself
    (the point `tests/test_tool_registry.py` makes about keying on the hint), so
    the root fix is pinned structurally: `writes` must not be a dataclass field,
    must be a `property`, and must flip on `risk` alone for every level. A lane
    that re-adds `writes: bool = False` — restoring the two independent
    declarations #4122 is about — reds here.
    """
    assert "writes" not in {f.name for f in dataclasses.fields(ToolDefinition)}, (
        "`writes` is a storable dataclass field again — that is the SECOND "
        "declaration #4122 removed; it can disagree with `risk`")
    assert isinstance(inspect.getattr_static(ToolDefinition, "writes"), property), (
        "`writes` is no longer the derived property over `risk in WRITE_RISKS`")

    for risk in RISK_LEVELS:
        entry = ToolDefinition(
            name=f"tortoise_probe_writes_{risk}", id=f"surface.probe.writes_{risk}",
            description="probe", annotations=ToolAnnotations(),
            http_policy=True, sdk_method="query", risk=risk,
        )
        assert entry.writes is (risk in WRITE_RISKS), (
            f"risk={risk!r} derived writes={entry.writes} — not DERIVED from risk")

    for entry in _served():
        assert entry.writes is (entry.risk in WRITE_RISKS), (
            f"{entry.name}: writes={entry.writes} but risk={entry.risk!r}")
    # ...and the derived set the scope gate and the read-velocity meter read is
    # exactly that derivation over the SERVED set (#3883).
    assert get_write_tool_names() == frozenset(
        t.name for t in _served() if t.risk in WRITE_RISKS)


#: Criterion 1's re-tag, pinned by NAME so a re-tag is a reviewable diff (the
#: same discipline as the #4474 write census). The mechanical default is
#: `write` — an entry is `destructive`/`admin` only where its OWN description
#: says so ("DESTRUCTIVE — cannot be undone"; "provisioning"; "schema-level
#: migration (operator-only)"), never by a lane's opinion of it.
DESTRUCTIVE_TOOLS: frozenset[str] = frozenset({
    "tortoise_delete", "tortoise_delete_entity", "tortoise_delete_point",
})
ADMIN_TOOLS: frozenset[str] = frozenset({
    "tortoise_backfill_v25", "tortoise_org_create",
})


def test_the_five_value_axis_is_not_dead():
    """Criterion 1's re-tag step, made falsifiable: the declared `destructive`
    and `admin` classes are USED, exactly where the entries say so, and every
    one of them still needs `graphs:write` (a re-tag must never be a quiet
    de-escalation of a destructive tool)."""
    by_risk: dict[str, set[str]] = {}
    for entry in _served():
        by_risk.setdefault(entry.risk, set()).add(entry.name)

    assert by_risk["destructive"] == DESTRUCTIVE_TOOLS, (
        f"destructive census moved: {sorted(by_risk['destructive'])}")
    assert by_risk["admin"] == ADMIN_TOOLS, (
        f"admin census moved: {sorted(by_risk['admin'])}")
    # Every value in the axis is in use, so no class is a dead label a future
    # consent bundle would silently have no inputs for.
    assert set(by_risk) == set(RISK_LEVELS), sorted(set(by_risk))

    for name in DESTRUCTIVE_TOOLS | ADMIN_TOOLS:
        entry = _entry(name)
        assert entry.writes is True, f"{name}: escalated risk lost graphs:write"
        assert entry.annotations.readOnlyHint is False, f"{name}: readOnlyHint over a destructive tool"


# ── The three tools the issue names (criterion 2) ─────────────────

@pytest.mark.parametrize("name", [
    "tortoise_get_confidence",
    "tortoise_compute_confidence",
    "tortoise_packs_list",
])
def test_the_three_read_through_tools_are_maintenance(name: str):
    """Criterion 2 + the ruling's two sides: these KEEP graphs:read (`writes`
    False, no integration loses a tool, read-velocity metering stays on) and
    STOP declaring `readOnlyHint: true` (the only thing actually wrong today —
    clients auto-approve on it)."""
    entry = _entry(name)
    assert entry.risk == "maintenance", f"{name}: risk={entry.risk!r}"
    assert entry.writes is False, (
        f"{name}: must stay read-permissioned — the ruling rejected option A "
        f"(`writes=True`) because it would take the tool from read-only keys AND "
        f"stop metering the recompute as a read")
    assert entry.annotations.readOnlyHint is False, (
        f"{name}: still advertises readOnlyHint:true — clients auto-approve on "
        f"that hint, which is the defect")
    assert entry.annotations.destructiveHint is False, (
        f"{name}: a confined, derived-value write is not destructive")


def test_a_read_only_key_can_still_call_the_three_tools():
    """The customer-facing promise of the ruling, measured at the SCOPE GATE —
    a `graphs:read` key is admitted (no integration loses a tool)."""
    from tortoise.mcp_auth import _current_legacy_full_access, _current_scopes
    from tortoise.mcp_server import _enforce_mcp_tool_scope

    scopes_tok = _current_scopes.set(["graphs:read"])
    legacy_tok = _current_legacy_full_access.set(False)
    try:
        for name in ("tortoise_get_confidence", "tortoise_compute_confidence",
                     "tortoise_packs_list"):
            _enforce_mcp_tool_scope(name)  # must NOT raise
    finally:
        _current_scopes.reset(scopes_tok)
        _current_legacy_full_access.reset(legacy_tok)


# ── The derivation cannot drift (the root fix, not the symptom) ───

def test_derivation_overrides_a_hand_set_annotation():
    """A hand-set `readOnlyHint` is corrected by the derivation — this is the
    root fix: the value a caller observes is computed from `risk`, so the
    #4122 defect (read hint over a writing handler) is unrepresentable."""
    lying = ToolDefinition(
        name="tortoise_probe_lying_read", id="surface.probe.lying_read",
        description="probe", annotations=ToolAnnotations(readOnlyHint=True),
        http_policy=True, sdk_method="query", risk="maintenance",
    )
    assert lying.annotations.readOnlyHint is False

    honest_write = ToolDefinition(
        name="tortoise_probe_honest_write", id="surface.probe.honest_write",
        description="probe", annotations=ToolAnnotations(readOnlyHint=True),
        http_policy=True, sdk_method="create_point", risk="write",
    )
    assert honest_write.annotations.readOnlyHint is False
    assert honest_write.writes is True


def test_maintenance_is_idempotent_but_a_plain_declaration_is_rejected():
    """`maintenance` forces `idempotentHint: true` (the ruling: a confined
    recompute is repeatable), and an unknown risk is a hard error rather than a
    silent default to "read"."""
    maint = ToolDefinition(
        name="tortoise_probe_maint", id="surface.probe.maint", description="probe",
        annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False),
        http_policy=True, sdk_method="query", risk="maintenance",
    )
    assert maint.annotations.idempotentHint is True
    assert maint.annotations.readOnlyHint is False

    with pytest.raises(ValueError, match="unknown risk"):
        ToolDefinition(
            name="tortoise_probe_bogus", id="surface.probe.bogus", description="probe",
            annotations=ToolAnnotations(), http_policy=True, sdk_method="query",
            risk="power-user",
        )


def test_idempotent_writers_keep_their_hint():
    """The one deviation from a literal reading of the ruling's diagram: deriving
    `idempotentHint` strictly from `risk == maintenance` would STRIP it from
    idempotent writers (`tortoise_create_point` has `dedup=True`), changing
    client retry semantics the ruling never discusses."""
    entry = _entry("tortoise_create_point")
    assert entry.risk == "write"
    assert entry.annotations.idempotentHint is True, (
        "idempotentHint was stripped from an idempotent writer — see the "
        "ToolDefinition docstring's idempotentHint note")


def test_retirement_transform_preserves_the_derivation():
    """`dataclasses.replace` re-runs `__post_init__`; a derived value must not be
    lost or re-derived wrongly when an entry is grouped/retired."""
    writer = next(t for t in TOOL_REGISTRY if t.risk == "write")
    renamed = dataclasses.replace(writer, name="tortoise_probe_renamed_4122")
    assert renamed.risk == "write" and renamed.writes is True
    assert renamed.annotations.readOnlyHint is False


# ── The guards that consume the derivation can still FAIL ─────────

def test_exemption_set_can_fail():
    """Falsifiability for `exemption_set_violations` (#4122 restatement): it must
    fire on both an inconsistent read hint and an inconsistent `writes`."""
    from tool_surface_capabilities import exemption_set_violations

    assert exemption_set_violations(_served()) == []

    lying_read = _Stub(
        "tortoise_rogue_read_only_writer", risk="read",
        annotations=ToolAnnotations(readOnlyHint=False), writes=False)
    assert exemption_set_violations([lying_read]), (
        "a `read` entry that does not declare readOnlyHint was not caught")

    unpermissioned = _Stub(
        "tortoise_rogue_write_without_permission", risk="write",
        annotations=ToolAnnotations(readOnlyHint=False), writes=False)
    assert exemption_set_violations([unpermissioned]), (
        "a `write` entry with `writes=False` was not caught")

    undeclared = _Stub(
        "tortoise_rogue_no_risk", risk=None,
        annotations=ToolAnnotations(readOnlyHint=True), writes=False)
    assert exemption_set_violations([undeclared]), (
        "an entry with no `risk` was not caught")


# ── Criterion 4: the OVERRIDES marker lives next to the field ─────

def test_overrides_marker_lives_beside_the_field():
    """House rule: a decision that goes against the industry default carries an
    `OVERRIDES:` line ON the artifact a lane reads — here, next to `risk`."""
    source = pathlib.Path(registry_module.__file__).read_text()
    doc = source.split("class ToolDefinition:", 1)[1].split("    name: str", 1)[0]
    assert "OVERRIDES:" in doc, "no OVERRIDES: marker beside the `risk` field"
    assert "AC-6" in doc, (
        "the OVERRIDES: marker must name the default it departs from (NIST "
        "SP 800-53 AC-6 read-low/write-high)")

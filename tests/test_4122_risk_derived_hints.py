"""#4122 — the FALSIFIABILITY half of the risk axis: `risk` is the ONE
classification a tool declares, and everything a caller observes about it is
DERIVED from it, so a hint, a permission and a scope cannot drift apart again.

The defect this pins: `tortoise_get_confidence`, `tortoise_compute_confidence` and
`tortoise_packs_list` advertised `readOnlyHint: True` while their handlers wrote to
the tenant graph — `annotations` and `writes` were two INDEPENDENT declarations, so
nothing in the registry could notice. A `graphs:read-only` MCP key could therefore
change a customer's stored knowledge, because MCP clients auto-approve on
`readOnlyHint: true`.

The owner ruling (2026-10-21, on #4122) replaced the A/B question with a
classification axis — `read | maintenance | write | destructive | admin` — where
`writes`, `readOnlyHint` and `idempotentHint` are DERIVED in
`ToolDefinition.__post_init__`. `tests/test_tool_registry.py::TestRiskAxis` holds
the structural test (`annotations`/`writes` cannot be passed at all); this file is
the part that has to be able to FAIL, so no assertion here is a value-sweep alone:

* `risk` must be DECLARED on every construction site (AST pin), and must carry no
  default — omission is a `TypeError`, never a silent `read`;
* the hint/permission table must be produced BY the derivation, so deleting
  `__post_init__` reds these tests instead of leaving them green;
* the guards that consume the derivation are fed a violation and must fire;
* the departures are marked where the next lane reads them (the code's
  `OVERRIDES:` line and the canonical document's).
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib
import types

import pytest
from mcp.types import ToolAnnotations

import tortoise.tool_registry as registry_module
from tortoise.tool_registry import (
    RETIRED_TOOL_REGISTRY,
    RISK_ADMIN,
    RISK_CLASSES,
    RISK_DESTRUCTIVE,
    RISK_MAINTENANCE,
    RISK_READ,
    RISK_WRITE,
    TOOL_REGISTRY,
    WRITE_RISKS,
    ToolDefinition,
    get_write_tool_names,
    risk_annotations,
    risk_writes,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _served() -> list[ToolDefinition]:
    """The served census (#3883): retired names still answer by name."""
    return [*TOOL_REGISTRY, *RETIRED_TOOL_REGISTRY]


def _entry(name: str) -> ToolDefinition:
    return next(t for t in _served() if t.name == name)


def _build(**kwargs) -> ToolDefinition:
    base = dict(name="tortoise_probe", id="surface.probe", description="probe",
                http_policy=True, sdk_method="query")
    base.update(kwargs)
    return ToolDefinition(**base)


# ── Criterion 1: the declaration, mechanically ────────────────────

def test_every_registry_entry_declares_risk_explicitly():
    """Criterion 1: `risk` is DECLARED on every served entry, with a known value —
    and no construction site may rely on a default.

    The value half is not enough on its own: an entry that FORGOT the axis would
    be indistinguishable from one that declared `read`, which is the human error
    #4122 came from (nobody decided `packs_list` was read-only). So the
    declaration half is pinned two ways — `risk` carries no default
    (`test_risk_has_no_default`) and every `ToolDefinition(...)` in the registry
    names it (AST walk here), which a re-added default could not evade.
    """
    served = _served()
    assert len(served) == 99, f"served census moved ({len(served)}) — re-derive, do not widen"
    unknown = [(e.name, e.risk) for e in served if e.risk not in RISK_CLASSES]
    assert not unknown, f"entry with an unknown risk class: {unknown}"

    tree = ast.parse(pathlib.Path(registry_module.__file__).read_text())
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", getattr(node.func, "attr", None)) == "ToolDefinition"
    ]
    assert len(calls) == 99, (
        f"AST walk found {len(calls)} `ToolDefinition(...)` calls, expected 99 — "
        f"the pin below would be vacuous")
    missing = [node.lineno for node in calls
               if "risk" not in {kw.arg for kw in node.keywords}]
    assert not missing, (
        f"`ToolDefinition(...)` at line(s) {missing} omits `risk=` — an undeclared "
        f"entry must not be able to inherit a default (#4122)")


def test_risk_has_no_default():
    """The omission path is CLOSED at the constructor: `risk` has no default, so an
    entry that forgets to classify itself cannot be built at all — it can never be
    silently derived as `read` (`writes=False` + `readOnlyHint=True`, the #4122
    shape).

    Field-level pin as well as the `TypeError` one, because an `Optional` +
    `None`-handling implementation would satisfy `TypeError` only by accident.
    """
    field = next(f for f in dataclasses.fields(ToolDefinition) if f.name == "risk")
    assert field.default is dataclasses.MISSING, "`risk` grew a default — the #4122 fail-open"
    assert field.default_factory is dataclasses.MISSING
    with pytest.raises(TypeError, match="risk"):
        ToolDefinition(name="tortoise_probe_no_risk", id="surface.probe.no_risk",
                       description="probe", http_policy=True, sdk_method="query")


# ── Criterion 3: the derivation, not the value it lands on ────────

def test_read_only_hint_is_derived_from_risk():
    """Criterion 3a: `readOnlyHint` is `risk == 'read'` — for EVERY class, in both
    directions, produced BY the derivation.

    If `__post_init__` stopped deriving (or `annotations` stopped being
    `init=False`), `entry.annotations` would be the inert default and this reds —
    it is not comparing the property with itself.
    """
    for risk in sorted(RISK_CLASSES):
        entry = _build(risk=risk)
        assert entry.annotations is not None, (
            f"risk={risk!r}: `annotations` was not derived — the #4122 root fix is gone")
        assert entry.annotations.readOnlyHint is (risk == RISK_READ), (
            f"risk={risk!r}: readOnlyHint={entry.annotations.readOnlyHint} — "
            f"readOnlyHint must be DERIVED as `risk == 'read'`")
        assert risk_annotations(risk).readOnlyHint is (risk == RISK_READ)

    for entry in _served():
        assert entry.annotations.readOnlyHint is (entry.risk == RISK_READ), (
            f"{entry.name}: readOnlyHint={entry.annotations.readOnlyHint} but "
            f"risk={entry.risk!r}")


def test_writes_is_derived_from_risk():
    """Criterion 3b: `writes` is `risk in {write, destructive, admin}` — derived,
    never a second declaration a lane can set.

    Structurally pinned (not a field a caller can pass) and behaviourally pinned
    per class: a `writes` that stopped being derived would leave every entry
    `False` — including the 48 writers — and this reds.
    """
    writes_field = next(f for f in dataclasses.fields(ToolDefinition) if f.name == "writes")
    assert writes_field.init is False, (
        "`writes` is hand-settable again — that is the SECOND declaration #4122 removed")
    annotations_field = next(
        f for f in dataclasses.fields(ToolDefinition) if f.name == "annotations")
    assert annotations_field.init is False, (
        "`annotations` is hand-settable again — the hint could disagree with `risk`")

    for risk in sorted(RISK_CLASSES):
        entry = _build(risk=risk)
        assert entry.writes is risk_writes(risk) is (risk in WRITE_RISKS), (
            f"risk={risk!r}: writes={entry.writes} — `writes` must be DERIVED from risk")

    for entry in _served():
        assert entry.writes is (entry.risk in WRITE_RISKS), (
            f"{entry.name}: writes={entry.writes} but risk={entry.risk!r}")
    assert get_write_tool_names() == frozenset(
        t.name for t in _served() if t.risk in WRITE_RISKS)


def test_the_hint_table_per_class_is_pinned():
    """The derivation's TABLE, per class — a logic change is a reviewable diff.

    `idempotentHint` is deliberately `maintenance or declared` (a literal
    `risk == maintenance` reading would strip it from idempotent writers such as
    `tortoise_create_point`, changing client retry semantics the #4122 ruling never
    discusses). `destructiveHint` is `destructive or declared`, so a write that also
    destroys keeps the hint that makes a client demand human confirmation.
    """
    table = {
        RISK_READ:        (True,  False, False),
        RISK_MAINTENANCE: (False, False, True),
        RISK_WRITE:       (False, False, False),
        RISK_DESTRUCTIVE: (False, True,  False),
        RISK_ADMIN:       (False, False, False),
    }
    assert set(table) == set(RISK_CLASSES), sorted(table)
    for risk, (ro, destructive, idem) in table.items():
        a = risk_annotations(risk)
        assert (a.readOnlyHint, a.destructiveHint, a.idempotentHint) == (ro, destructive, idem), (
            f"risk={risk!r}: derived hints moved: "
            f"{(a.readOnlyHint, a.destructiveHint, a.idempotentHint)}")
    # The `destructiveHint` rule is fail-closed for a non-writer: a read that
    # claims to be destructive is a contradiction, not a preference.
    with pytest.raises(ValueError, match="destructiveHint"):
        risk_annotations(RISK_READ, destructive_hint=True)
    # ...and the declared bits can only RAISE the claim, never lower it.
    assert risk_annotations(RISK_MAINTENANCE, idempotent_hint=False).idempotentHint is True
    assert risk_annotations(RISK_WRITE, idempotent_hint=True).idempotentHint is True


def test_the_risk_census_is_pinned_and_retagging_is_a_product_decision():
    """The served census, pinned so a reclassification is a reviewable diff.

    `destructive`/`admin` are declared in the axis and (deliberately) not yet used
    by an entry: #9573 recorded that re-tagging the 48 writers into
    write/destructive/admin is completion criterion 1's separate product pass, and
    must not silently strip the `destructiveHint` 39 entries rely on. A lane that
    re-tags here owes this pin an edit.
    """
    census: dict[str, int] = {}
    for entry in _served():
        census[entry.risk] = census.get(entry.risk, 0) + 1
    assert census == {RISK_READ: 48, RISK_WRITE: 48, RISK_MAINTENANCE: 3}, census
    assert census.get(RISK_DESTRUCTIVE, 0) == 0 and census.get(RISK_ADMIN, 0) == 0, (
        "the destructive/admin re-tag landed — update this pin and the #4122 "
        "criterion-1 record rather than widening it")


# ── Criterion 2: the three tools, both sides of the ruling ────────

@pytest.mark.parametrize("name", [
    "tortoise_get_confidence",
    "tortoise_compute_confidence",
    "tortoise_packs_list",
])
def test_the_read_through_tools_are_maintenance(name: str):
    """Criterion 2 + the ruling's two sides: these KEEP `graphs:read` (`writes`
    False, no integration loses a tool, read-velocity metering stays on) and STOP
    declaring `readOnlyHint: true` (the only thing actually wrong — clients
    auto-approve on it)."""
    entry = _entry(name)
    assert entry.risk == RISK_MAINTENANCE, f"{name}: risk={entry.risk!r}"
    assert entry.writes is False, (
        f"{name}: must stay read-permissioned — promoting it to a write would take "
        f"it from read-only keys AND drop the recompute out of read-velocity metering")
    assert entry.annotations.readOnlyHint is False, (
        f"{name}: still advertises readOnlyHint:true — clients auto-approve on it")
    assert entry.annotations.destructiveHint is False, (
        f"{name}: a confined, derived-value write is not destructive")
    assert entry.annotations.idempotentHint is True, (
        f"{name}: `maintenance` implies idempotentHint (repeatable recompute)")


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


def test_retirement_transform_preserves_the_derivation():
    """`dataclasses.replace` re-runs `__post_init__`; a derived value must not be
    lost or re-derived wrongly when an entry is grouped/retired (`_apply_groups`
    depends on this being idempotent)."""
    writer = next(t for t in TOOL_REGISTRY if t.risk == RISK_WRITE)
    renamed = dataclasses.replace(writer, name="tortoise_probe_renamed_4122")
    assert renamed.risk == RISK_WRITE and renamed.writes is True
    assert renamed.annotations.readOnlyHint is False


# ── The guard that consumes the derivation can still FAIL ─────────

def test_exemption_set_can_fail():
    """Falsifiability for `exemption_set_violations`: it must fire on a lying read
    hint, on a write with no permission, and on a pre-#4122 entry that declares no
    risk at all.

    The probes are duck-typed stand-ins (`SimpleNamespace`), because the derivation
    makes a disagreeing `ToolDefinition` unrepresentable — that is the point of the
    fix, so a guard that could only ever be fed real entries would be unfalsifiable.
    """
    from tool_surface_capabilities import exemption_set_violations

    assert exemption_set_violations(_served()) == []

    def probe(name: str, risk, annotations, writes: bool):
        return types.SimpleNamespace(
            name=name, risk=risk, annotations=annotations, writes=writes)

    lying_read = probe("tortoise_rogue_read_only_writer", RISK_READ,
                       ToolAnnotations(readOnlyHint=False), writes=False)
    assert exemption_set_violations([lying_read]), (
        "a `read` entry that does not declare readOnlyHint was not caught")

    unpermissioned = probe("tortoise_rogue_write_without_permission", RISK_WRITE,
                           ToolAnnotations(readOnlyHint=False), writes=False)
    assert exemption_set_violations([unpermissioned]), (
        "a `write` entry with `writes=False` was not caught")

    undeclared = probe("tortoise_rogue_no_risk", None,
                       ToolAnnotations(readOnlyHint=True), writes=False)
    # A pre-#4122 entry object (or a duck-typed probe) declares no `risk`; the guard
    # keeps the legacy 2-state check for it rather than skipping it, so a writer
    # annotation with `writes=False` still reds. (A read hint + writes=False is
    # coherent under that legacy rule — it IS the pre-#4122 correct pair.)
    assert exemption_set_violations([undeclared]) == [], (
        "a legacy read entry (no risk, read hint, no writes) is not a violation")
    legacy_4474 = probe("tortoise_rogue_legacy_4474", None,
                        ToolAnnotations(readOnlyHint=False), writes=False)
    assert exemption_set_violations([legacy_4474]), (
        "a risk-less entry with a writer annotation and no permission was not caught "
        "— the #4474 shape the legacy arm exists for")


# ── Criterion 4: the departures are marked where a lane reads them ─

def test_overrides_marker_lives_beside_the_axis():
    """House rule: a decision that goes against the industry default carries an
    `OVERRIDES:` line ON the artifact a lane reads — here, beside `risk`."""
    source = pathlib.Path(registry_module.__file__).read_text()
    head = source.split("class ToolDefinition:", 1)[0]
    assert "OVERRIDES:" in head, "no OVERRIDES: marker beside the risk axis"
    assert "AC-6" in head, (
        "the OVERRIDES: marker must name the default it departs from (NIST "
        "SP 800-53 AC-6 read-low/write-high)")


def test_canonical_doc_marks_the_maintenance_departure():
    """The canonical document states "reads and writes never share a tool"; the
    `maintenance` class is a third class ON the customer-grantable surface, so the
    principle must carry the marker — otherwise the next lane reads the three tools
    as #4122 surviving its own fix and "repairs" them."""
    doc = (ROOT / "docs" / "product" / "canonical-mcp-tools.md").read_text()
    principle = doc.split("## Principle: reads and writes never share a tool", 1)
    assert len(principle) == 2, "the principle section moved — re-anchor this pin"
    section = principle[1].split("**Operator-only tools are exempt.**", 1)[0]
    assert "OVERRIDES:" in section, (
        "the `maintenance` departure from the read-or-write principle is unmarked")
    assert "maintenance" in section and "tortoise_packs_list" in section, (
        "the OVERRIDES: line must name the class and the tools it covers")
    assert "#4122" in section, "the OVERRIDES: line must cite the ruling"

"""#5045 — the declared cost-dimension registry must be TRUE about the code.

The registry (``tortoise/cost_dimensions.py``) is the substrate's declaration;
this file is the proof that it is a claim about reality rather than a comment.
Four things are pinned:

1. **Completeness.** Every org-attributed prometheus child in
   ``tortoise/monitoring.py`` is declared, and no other tracked module builds a
   prometheus metric. A seventh counter added in a hurry reds this, which is the
   failure #5045 exists to end (six lanes, each adding its own counter).
2. **Truth.** Every declared writer/reader resolves and is callable, every
   declared metric carries EXACTLY its declared labels, every declared column and
   SQL function exists in a tracked migration, and every declared cap/overflow
   attribute resolves.
3. **No dead hook.** A declared writer must be reachable from a tracked NON-TEST
   file, or the declaration must say why it has no schedule (#4493: a metric that
   registered, exported and tested while never being incremented in production).
4. **Declared policy == measured policy.** Each dimension's declared label
   cardinality LIFETIME (``persistent`` / ``per_publish``) is pinned against its
   real behaviour, so the two conventions the #4493 lane found could not drift
   further without a red test — the *choice* between them stays an open decision
   on #5045, recorded rather than silently made.

The mutation tests are the discriminating half: each injects the drift it claims
to catch and asserts the audit REPORTS it. A gate nobody has seen fail is not
evidence.
"""
from __future__ import annotations

import dataclasses
import importlib.util
import inspect
import os
import subprocess
import sys
import textwrap
from contextlib import contextmanager
from pathlib import Path

import pytest
from prometheus_client import Counter

import tortoise.cost_allocation as ca
import tortoise.cost_dimensions as cd
import tortoise.monitoring as monitoring
from tortoise.monitoring import (
    COMPUTE_MAX_ORGS,
    COMPUTE_OVERFLOW,
    EGRESS_MAX_ORGS,
    EGRESS_OVERFLOW,
)

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

_ROOT = Path(__file__).resolve().parent.parent
AUDIT_VERB = _ROOT / "tools" / "cost_coverage_audit.py"

#: The audit tool is loaded from its PATH (``tools/`` is not an import package,
#: and adding it to ``sys.path`` would put its imports after executable code).
_SPEC = importlib.util.spec_from_file_location("cost_coverage_audit", AUDIT_VERB)
audit = importlib.util.module_from_spec(_SPEC)
sys.modules["cost_coverage_audit"] = audit
_SPEC.loader.exec_module(audit)


# ── helpers ──────────────────────────────────────────────────────────────


@contextmanager
def _declaring(*dimensions):
    """Swap the registry for a mutated one, restoring it afterwards.

    The registry is module state (a tuple plus its index), so a mutation test
    has to patch BOTH — exactly as the audit reads it.
    """
    original = cd.DIMENSIONS
    cd.DIMENSIONS = tuple(dimensions)
    cd._BY_NAME = {d.name: d for d in cd.DIMENSIONS}
    try:
        yield
    finally:
        cd.DIMENSIONS = original
        cd._BY_NAME = {d.name: d for d in original}


def _mutated(name: str, **changes) -> cd.Dimension:
    return dataclasses.replace(cd.by_name(name), **changes)


# ── 1. completeness ──────────────────────────────────────────────────────


def test_every_org_attributed_metric_is_declared():
    """The substrate's per-org metric surface is exactly the declared one."""
    assert audit.undeclared_metric_findings() == [], (
        "an org-attributed metric exists that no declared dimension names")


def test_no_prometheus_metric_is_built_outside_the_metric_module():
    """A second metric surface is how a substrate grows an unowned counter."""
    assert audit.foreign_metric_findings(_ROOT) == []


def test_a_seventh_counter_cannot_appear_undeclared(monkeypatch):
    """MUTATION: a new org-labelled counter reds the audit.

    Driven through the LIVE enumeration (no injected child list), so the failure
    is the real predicate — a counter added to ``monitoring`` and nothing else.
    """
    monkeypatch.setattr(
        monitoring, "EGRESS_BYTES_V2",
        Counter("tortoise_egress_bytes_v2", "a seventh counter", ["org"]),
        raising=False)
    findings = audit.undeclared_metric_findings()
    assert any("EGRESS_BYTES_V2" in f for f in findings), findings


def test_a_prometheus_metric_outside_monitoring_is_flagged(tmp_path):
    """MUTATION: a metric constructed in another module is drift."""
    module = tmp_path / "tortoise" / "elsewhere.py"
    module.parent.mkdir(parents=True)
    module.write_text(textwrap.dedent("""
        from prometheus_client import Counter
        NEW_DIMENSION = Counter("tortoise_new_dimension_total", "x", ["org"])
    """), encoding="utf-8")
    findings = audit.foreign_metric_findings(tmp_path)
    assert findings, "a metric built outside the declared module was not flagged"
    assert "elsewhere.py" in findings[0]


def test_a_plain_collections_counter_is_not_mistaken_for_a_metric(tmp_path):
    """The gate must not red on ``collections.Counter`` tally code.

    A false positive here would make the check unusable in this repo, which uses
    ``Counter(...)`` for ordinary tallies in several modules.
    """
    module = tmp_path / "tortoise" / "tally.py"
    module.parent.mkdir(parents=True)
    module.write_text(textwrap.dedent("""
        from collections import Counter
        def tally(tokens):
            return Counter(tokens)
    """), encoding="utf-8")
    assert audit.foreign_metric_findings(tmp_path) == []


# ── 2. truth of the declaration ──────────────────────────────────────────


def test_every_declared_binding_resolves():
    """Each declared writer/reader callable, each metric with its exact labels."""
    assert audit.check_bindings() == []


def test_every_declared_column_and_sql_function_exists():
    """The ledger half of each declaration is verified against the DDL."""
    assert audit.check_ledger(_ROOT) == []


def test_a_renamed_column_is_drift():
    """MUTATION: a declared column that no migration creates is reported."""
    with _declaring(_mutated("egress", columns=("no_such_column_xyz",)),
                    *[d for d in cd.DIMENSIONS if d.name != "egress"]):
        findings = audit.check_ledger(_ROOT)
    assert any("no_such_column_xyz" in f for f in findings), findings


def test_a_renamed_cap_constant_is_drift():
    """MUTATION: a cap attribute that no longer resolves is reported."""
    broken = _mutated(
        "egress",
        cardinality=cd.Cardinality(
            lifetime=cd.PERSISTENT,
            cap="tortoise.monitoring.EGRESS_MAX_ORGS_RENAMED",
            overflow="tortoise.monitoring.EGRESS_OVERFLOW"))
    with _declaring(broken, *[d for d in cd.DIMENSIONS if d.name != "egress"]):
        findings = audit.check_cardinality()
    assert any("EGRESS_MAX_ORGS_RENAMED" in f for f in findings), findings


def test_declared_metric_labels_match_the_live_metric():
    """MUTATION: a declared label set that differs from the live child is drift."""
    dim = cd.by_name("egress")
    wrong = dataclasses.replace(
        next(b for b in dim.metrics() if b.attr == "EGRESS_BYTES"),
        labels=("org",))
    others = tuple(b for b in dim.bindings if b.attr != "EGRESS_BYTES")
    rest = tuple(d for d in cd.DIMENSIONS if d.name != "egress")
    with _declaring(dataclasses.replace(dim, bindings=(wrong, *others)), *rest):
        findings = audit.check_bindings()
    assert any("carries labels" in f for f in findings), findings


# ── 3. no dead hook ──────────────────────────────────────────────────────


def test_every_dimension_has_a_production_call_site_or_says_why_not():
    assert audit.check_dead_hooks(_ROOT) == []


def test_the_dead_hook_check_is_not_vacuous():
    """The dimensions with no schedule DECLARE it — they are not silent.

    ``measure_postgres_storage``/``read_analytics_row_growth`` have no caller
    outside tests on purpose (the SQL function is the read path). If that were
    silent the check above would be passing for the wrong reason, so the declared
    reason is asserted here.
    """
    for name in ("postgres_storage", "analytics_row_growth"):
        assert cd.by_name(name).unscheduled_reason, (
            f"{name} has no production caller and no declared reason")


def test_a_writer_only_tests_reach_is_a_dead_hook():
    """MUTATION: a writer no production file mentions is reported."""
    ghost = _mutated(
        "egress",
        bindings=(cd.Binding("tortoise.monitoring", "record_egress_ghost",
                             cd.WRITER),),
        unscheduled_reason=None)
    with _declaring(ghost, *[d for d in cd.DIMENSIONS if d.name != "egress"]):
        findings = audit.check_dead_hooks(_ROOT)
    assert any("dead hook" in f for f in findings), findings


# ── 4. the coverage query ────────────────────────────────────────────────


def test_no_dimension_is_unmeasured_and_undeclared():
    """#5045's target: 0 unmeasured dimensions, each out-of-scope one named."""
    assert cd.states() == (cd.MEASURED, cd.ALLOCATION, cd.OUT_OF_SCOPE)
    for dim in cd.dimensions():
        assert dim.state in cd.states(), dim.name
        if dim.state != cd.MEASURED:
            assert dim.limits, f"{dim.name} is {dim.state} with no stated reason"
    matrix = cd.render_matrix()
    assert "unmeasured-and-undeclared: 0" in matrix
    for dim in cd.out_of_scope():
        assert dim.name in matrix, dim.name
        assert dim.limits[0][:40] in matrix, dim.name


def test_the_coverage_matrix_names_its_allocations():
    """An ALLOCATION must never be readable as a measurement."""
    allocations = cd.allocations()
    assert [d.name for d in allocations] == ["fixed_shared_saas"]
    assert allocations[0].state != cd.MEASURED


def test_query_by_name_answers_and_typos_are_not_answers():
    assert cd.by_name("egress").state == cd.MEASURED
    assert cd.by_name("mitigation_weight_delta").state == cd.OUT_OF_SCOPE
    with pytest.raises(KeyError) as exc:
        cd.by_name("egres")  # a typo must never read as "not measured"
    assert "egress" in str(exc.value)


def test_query_verb_exit_codes_distinguish_the_three_answers():
    """measured -> 0; declared out of scope -> 3; unknown -> 2."""
    def run(name: str) -> int:
        return audit.main(["query", name])

    assert run("egress") == 0
    assert run("fixed_shared_saas") == 0
    assert run("mitigation_weight_delta") == 3
    assert run("egres") == 2


def test_the_matrix_verb_reports_no_drift_on_this_tree(capsys):
    """The operator verb, end to end: exit code 0 and every check reported."""
    assert audit.main(["matrix"]) == 0
    out = capsys.readouterr().out
    assert "no drift" in out
    assert "unmeasured-and-undeclared: 0" in out


def test_the_cli_entrypoint_works_in_a_fresh_interpreter():
    """A real ``python tools/cost_coverage_audit.py <verb>`` invocation.

    ``query`` (not ``matrix``) so the subprocess pays an import and nothing else:
    the exit code and the argv plumbing are what this pins, and the corpus scan is
    already covered in-process above.
    """
    proc = subprocess.run(
        [sys.executable, str(AUDIT_VERB), "query", "egress"],
        cwd=_ROOT, capture_output=True, text=True,
        env={**os.environ, "TORTOISE_SECRET_PEPPER": "test-static-pepper"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "egress: measured" in proc.stdout


# ── 5. declared cardinality POLICY == measured policy ────────────────────
#
# The #4493 lane found two live conventions with different SEMANTICS and no
# convergence point. They are declared (module docstring) and pinned here, so the
# divergence is a recorded fact rather than an accident — the choice between them
# is the open #5045 decision.


def test_egress_org_admission_is_persistent_by_arrival_not_by_sorted_order():
    """``persistent``: an admitted label keeps its child; a later one folds.

    The discriminating observation is ORDER: with the cap full, an org that sorts
    BEFORE the admitted ones still folds, because admission is first-come and is
    never revisited. A ``per_publish`` policy (recomputed from the current set, in
    sorted order — the allocation's rule) would instead give the newcomer the
    child and demote the incumbent.
    """
    monitoring._reset_egress()
    original_cap = monitoring.EGRESS_MAX_ORGS
    monitoring.EGRESS_MAX_ORGS = 2
    try:
        monitoring.record_egress("m-org", "/v1/x", 10)
        monitoring.record_egress("z-org", "/v1/x", 20)
        monitoring.record_egress("a-org", "/v1/x", 40)  # sorts first, arrives last
        totals = monitoring.egress_bytes_by_org()
        assert totals["m-org"] == 10 and totals["z-org"] == 20
        assert totals[EGRESS_OVERFLOW] == 40
        assert "a-org" not in totals, "a first-come cap re-admitted a later org"
        # A second cycle keeps the admitted children where they are: no flap.
        monitoring.record_egress("m-org", "/v1/x", 5)
        monitoring.record_egress("a-org", "/v1/x", 1)
        totals = monitoring.egress_bytes_by_org()
        assert totals["m-org"] == 15
        assert totals[EGRESS_OVERFLOW] == 41
    finally:
        monitoring.EGRESS_MAX_ORGS = original_cap
        monitoring._reset_egress()


def test_compute_org_admission_is_persistent_like_egress():
    """The compute twin declares the same lifetime — pinned, not assumed."""
    monitoring._reset_compute()
    original_cap = monitoring.COMPUTE_MAX_ORGS
    monitoring.COMPUTE_MAX_ORGS = 1
    try:
        monitoring.record_compute("m-org", "/v1/x", 0.5)
        monitoring.record_compute("a-org", "/v1/x", 0.25)
        by_org = monitoring.compute_by_org()
        assert by_org["m-org"]["requests"] == 1.0
        assert by_org[COMPUTE_OVERFLOW]["requests"] == 1.0
        assert "a-org" not in by_org
        assert COMPUTE_OVERFLOW == EGRESS_OVERFLOW  # one sentinel spelling
        assert COMPUTE_MAX_ORGS == EGRESS_MAX_ORGS  # one cap value
    finally:
        monitoring.COMPUTE_MAX_ORGS = original_cap
        monitoring._reset_compute()


def test_allocation_org_admission_is_per_publish_and_self_healing():
    """``per_publish``: the admitted set follows the CURRENT orgs, so it prunes.

    Under the persistent policy the folded ``__other__`` child would keep its
    accumulated value forever. Here a re-publish with the org gone prunes the
    child, which is what makes the two conventions different KINDS.
    """
    ca._reset_for_tests()
    original_cap = ca.MAX_ORG_LABELS
    ca.MAX_ORG_LABELS = 2
    try:
        ca.publish(_snapshot_for({"a": 10, "b": 20, "c": 30}))
        first = monitoring.team_cost_cents()
        assert set(first) == {"a", "b", ca.ORG_OVERFLOW}
        assert first[ca.ORG_OVERFLOW] == 30
        # The fleet shrinks: the per-publish rule recomputes admission, so the
        # overflow child is pruned rather than retained (persistent would keep it).
        ca.publish(_snapshot_for({"a": 10, "b": 20}))
        second = monitoring.team_cost_cents()
        assert set(second) == {"a", "b"}, second
    finally:
        ca.MAX_ORG_LABELS = original_cap
        ca._reset_for_tests()


def _snapshot_for(shares: dict[str, int]) -> ca.AllocationSnapshot:
    """One-line MEASURED snapshot carrying *shares* (integer cents)."""
    return ca.AllocationSnapshot(
        generated_at="2026-10-18T00:00:00+00:00",
        window_start="2026-10-01T00:00:00+00:00",
        window_end="2026-11-01T00:00:00+00:00",
        unit="cents",
        state=ca.STATE_MEASURED,
        enumeration_available=True,
        unallocated_cents=0,
        lines=(
            ca.LineAllocation(
                line="shared-saas",
                basis="even",
                total_cents=sum(shares.values()),
                source="test",
                as_of="2026-10-18T00:00:00+00:00",
                is_estimate=False,
                state=ca.STATE_MEASURED,
                shares=tuple(ca.OrgShare(org_label=org, cents=cents)
                             for org, cents in shares.items()),
                residual_cents=0,
                detail="",
            ),
        ),
    )


# ── 6. one read path for the org-attributed dimensions ───────────────────


def test_both_org_readers_share_one_extraction():
    """#5045's convergence: a dimension differs in unit, not in how it is read."""
    egress_src = inspect.getsource(monitoring.egress_bytes_by_org)
    compute_src = inspect.getsource(monitoring._sum_compute_by_org)
    assert "_sum_by_label" in egress_src
    assert "_sum_by_label" in compute_src
    # ... and the shared helper is what actually sums, not a leftover second loop.
    monitoring._reset_egress()
    monitoring.record_egress("o", "/v1/a", 7)
    monitoring.record_egress("o", "/v1/b", 5)
    assert monitoring.egress_bytes_by_org()["o"] == 12
    assert monitoring._sum_by_label(
        monitoring.EGRESS_BYTES, "org", cast=int)["o"] == 12
    monitoring._reset_egress()

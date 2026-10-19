"""#9189 / §17.2 — the record↔cache reconciler has a PRODUCTION TRIGGER.

``tortoise/reconciliation.py`` compares and refuses; it had **no production
caller at all** measured at ``f96a18da1`` (tests and the #7950 evidence probes
only), so §17.2's *"divergence must be OBSERVED, not merely recoverable"* had a
mechanism and no observer. ``tortoise/record_reconcile.py`` is the observer and
``python -m tortoise record-reconcile`` is its caller.

Five things are pinned here, and each is a way the observer could be worthless:

1. **A clean projection reconciles CLEAN** — ``divergent == 0``,
   ``field_mismatches == 0``, ``missing_in_cache == 0``. Uses the shipped
   writer (``cache_projection.project_records``) and the shipped reader
   (``read_cache_view``), not a fixture shaped to agree.
2. **The observer is NOT VACUOUS** — a deliberate second writer, a REAL raw
   graph-handle write that appends nothing to any journal, raises
   ``extra_in_cache > 0``. The count is not a mock's arithmetic (#9189
   indicator 2); it is read back out of the cache.
3. **The guarded lanes REFUSE the same write, and the raw handle does not** —
   which is why the guard alone cannot be the answer and an observer is needed.
   ⚠️ Stated as it is: the §17.1 gate closes ``FalkorProjection.apply`` and
   ``FalkorProjection.query``, and the raw graph handle (``proj.g.query``)
   survives — the surviving second-writer seam this count exists to see. Both
   halves are asserted on ONE **journal-backed** projection, and the write the
   handle accepts is then COUNTED by the shipped read path, because a change
   that closed ``proj.g.query`` too would otherwise keep this file green while
   ``python -m tortoise record-reconcile`` reported exit 2 forever.
4. **An unreadable side is a REFUSAL, and nothing compared is NOT clean** — an
   unreadable cache, a node with no contract key, two nodes at one coordinate,
   and a pair of placeholder property maps all refuse; an empty pair verdicts
   ``unanswerable`` (exit 2), never ``clean``.
5. **A PARTIAL declaration's unwritten keys are RESIDENCY, not divergence**
   (§17.3) — counted as ``not_hot``, never as ``missing_in_cache`` — while a
   ``complete`` declaration's missing key still counts as divergence.
6. **The trigger exists in production** — an AST import-graph pin, so the
   observer cannot drift back to being reachable only from tests and probes —
   and its documented exit-code contract, including the usage case argparse
   answers with ``2``, is pinned rather than narrated.

⚠️ This file lives in BOTH the `api`/`core` surfaces and `carve_out:`: item 3
is embedded-only (§17.1's journal arm exists only on the embedded lane), and a
carve-out-less registration would leave it collected-and-SKIPPED on every full
selection — a permanently green, permanently unexecuted gate (#4047).

⚠️ The trigger is a SWEEP (the CLI verb), not a call inside the capture turn:
detection latency is the sweep interval, and divergence created AND repaired
between two sweeps is not observed. Migrating the trigger onto the capture
boundary is #9188's, whose writer does not exist yet.
"""
from __future__ import annotations

import ast
import contextlib
import functools
import os
import uuid
import warnings
from pathlib import Path
from typing import ClassVar

import pytest

from tortoise import cache_projection as cp
from tortoise import record_reconcile as rr
from tortoise.projection import DirectCacheWriteRefused
from tortoise.reconciliation import ReconciliationUnavailable
from tortoise.residency import (
    COVERAGE_COMPLETE,
    COVERAGE_PARTIAL,
    ResidencyDeclaration,
    read_declaration,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent

#: The trigger module this suite is about. Named once so the import-graph pin
#: and the positive control cannot drift apart.
_TRIGGER_MODULE = "tortoise.record_reconcile"


# ── fixtures: an isolated cache graph per test ─────────────────────────────


@pytest.fixture
def cache_graph(shared_proj):
    """A per-test cache graph, so the comparison sees ONLY this test's nodes.

    A shared graph would make ``extra_in_cache`` a function of whatever another
    test left behind, which is the one assertion in this file that must be
    exact. ``select_graph`` on the session client costs no second server, so
    this is isolation without a new embedded construction (and so without the
    carve-out / redirect registries a fresh server would need).
    """
    if shared_proj is None:
        pytest.skip("no graph engine available on this lane")
    graph = shared_proj.db.select_graph(f"test_9189_{uuid.uuid4().hex[:8]}")
    try:
        yield graph
    finally:
        with contextlib.suppress(Exception):
            graph.delete()


def _record_view() -> cp.RecordView:
    """Two Points, in the record's own vocabulary (real Point columns)."""
    return cp.RecordView(nodes=(
        cp.RecordNode(label="Point", key="p-9189-1",
                      props={"id": "p-9189-1", "content": "alpha",
                             "status": "live"}),
        cp.RecordNode(label="Point", key="p-9189-2",
                      props={"id": "p-9189-2", "content": "beta",
                             "status": "live"}),
    ))


def _project(view: cp.RecordView, graph, *, limit: int | None = None):
    """Project the record with the SHIPPED writer, and declare what was written."""
    nodes = tuple(view.nodes) if limit is None else tuple(
        sorted(view.nodes, key=lambda n: (n.label, n.key))[:limit])
    plan = cp.record_plan(nodes, view.relations)
    cp.project_records(graph, plan)
    declaration = cp.declaration_for(view.record_keys, plan.node_keys())
    cp.write_declaration(graph, declaration)
    return declaration


def _run(view: cp.RecordView, graph) -> rr.RecordReconcileResult:
    return rr.reconcile_record_and_cache(
        rr.record_key_map(view),
        rr.read_cache_view(graph),
        declaration=read_declaration(graph),
    )


# ── 1. a clean capture reconciles clean ────────────────────────────────────


def test_a_clean_projection_reconciles_clean(cache_graph):
    view = _record_view()
    _project(view, cache_graph)

    result = _run(view, cache_graph)

    assert result.report.checked == 2
    assert result.report.divergent == 0
    assert result.report.missing_in_cache == 0
    assert result.report.extra_in_cache == 0
    assert result.report.field_mismatches == 0
    assert result.report.form_normalized == 0
    assert result.not_hot == 0
    assert result.verdict() == rr.VERDICT_CLEAN
    assert rr.exit_code_for(result) == rr.EXIT_CLEAN


def test_a_verbatim_point_reconciles_clean_end_to_end(cache_graph):
    """#9184's disposition, END TO END, through the shipped writer and reader.

    A verbatim Point's marker is ``text`` in the record and a BOOLEAN in the
    cache, so before the stored-form rule this projection reported a permanent
    ``field_mismatches=1`` and indicator (1) of #9189 was unreachable. The rule
    absorbs it and says so: ``form_normalized=1``, ``field_mismatches=0``.
    """
    view = cp.RecordView(nodes=(
        cp.RecordNode(label="Point", key="p-9189-verbatim",
                      props={"id": "p-9189-verbatim", "content": "alpha",
                             "embedding": [0.1, 0.2],
                             "embedding_verbatim": "true"}),
    ))
    _project(view, cache_graph)

    # The two sides really do hold different FORMS — otherwise this proves
    # nothing (the marker the cache holds is a boolean, the record's is text).
    cache = rr.read_cache_view(cache_graph)
    assert cache["Point:p-9189-verbatim"]["embedding_verbatim"] is True
    assert rr.record_key_map(view)["Point:p-9189-verbatim"][
        "embedding_verbatim"] == "true"

    result = _run(view, cache_graph)

    assert result.report.field_mismatches == 0
    assert result.report.divergent == 0
    assert result.report.form_normalized == 1, result.report.render()
    assert result.verdict() == rr.VERDICT_CLEAN


def test_the_cache_reader_returns_the_real_property_maps(cache_graph):
    """Not placeholders: the maps are what the cache holds (#8609 finding 3)."""
    view = _record_view()
    _project(view, cache_graph)

    cache = rr.read_cache_view(cache_graph)

    assert cache["Point:p-9189-1"] == {
        "id": "p-9189-1", "content": "alpha", "status": "live"}
    assert all(cache.values()), "a placeholder map makes the field check vacuous"


# ── 2. the observer is not vacuous ────────────────────────────────────────


def test_a_second_writer_raises_extra_in_cache(cache_graph):
    """A REAL unjournalled write through the graph handle, read back as a count."""
    view = _record_view()
    _project(view, cache_graph)

    # The raw graph handle: the surviving second-writer seam. It takes no
    # journal, so this write bypasses the record entirely — no projection
    # statement ran, and the record view knows nothing about it.
    cache_graph.query("CREATE (:Point {id:'p-9189-direct'})")

    result = _run(view, cache_graph)

    assert result.report.extra_in_cache == 1, result.report.render()
    assert result.report.divergent == 1
    assert result.report.missing_in_cache == 0
    assert result.verdict() == rr.VERDICT_DIVERGENT
    assert rr.exit_code_for(result) == rr.EXIT_DIVERGENT


@pytest.mark.embedded_only
def test_the_guarded_lanes_refuse_the_same_write_while_the_handle_takes_it(tmp_path):
    """§17.1's gate closes the fold and raw-projection lanes, not the handle.

    Stated precisely, because the difference is the reason this observer
    exists: on a **journal-backed** projection ``FalkorProjection.apply`` and
    ``FalkorProjection.query`` refuse, while ``proj.g.query`` still writes the
    cache — so the cache can hold a node the record never had, and only a count
    can reveal it.

    Both halves run on ONE journal-backed projection, and the write the handle
    accepts is read back through the shipped reader the CLI uses
    (``read_cache_view(proj.g)``) and then COUNTED. Asserting them on two
    different objects would let a change that closed ``proj.g.query`` as well
    keep this file green while the verb reported exit 2 forever.
    """
    from tests._embedded import fresh_embedded_proj

    view = _record_view()
    with fresh_embedded_proj(tmp_path) as proj:
        proj._bind_journal(str(tmp_path / "events.jsonl"))
        try:
            with pytest.raises(DirectCacheWriteRefused):
                proj.apply({"type": "PointAdded", "id": "p-9189-fold"})
            with pytest.raises(DirectCacheWriteRefused):
                proj.query("CREATE (:Point {id:'p-9189-lane'})")

            # The READ lane the CLI drives is not what the gate closes: the
            # shipped reader walks the same guarded handle.
            _project(view, proj.g)
            assert rr.read_cache_view(proj.g)["Point:p-9189-1"]["content"] == "alpha"

            # ...and the raw handle STILL accepts an unjournalled write.
            proj.g.query("CREATE (:Point {id:'p-9189-direct'})")

            result = rr.reconcile_record_and_cache(
                rr.record_key_map(view),
                rr.read_cache_view(proj.g),
                declaration=read_declaration(proj.g),
            )
        finally:
            proj._bind_journal(None)

    assert result.report.extra_in_cache == 1, result.report.render()
    assert result.verdict() == rr.VERDICT_DIVERGENT


# ── 3. residency is not divergence (§17.3) ────────────────────────────────


def test_a_partial_declaration_counts_unwritten_keys_as_residency(cache_graph):
    """A bounded projection's unwritten keys are `not-hot`, never a lag."""
    view = _record_view()
    _project(view, cache_graph, limit=1)

    result = _run(view, cache_graph)

    assert result.declaration.coverage == COVERAGE_PARTIAL
    assert result.not_hot == 1
    assert result.report.missing_in_cache == 0, (
        "a residency miss was counted as a projection lag")
    assert result.report.checked == 1
    assert result.verdict() == rr.VERDICT_CLEAN


def test_a_complete_declaration_still_counts_a_missing_key_as_divergence(
        cache_graph):
    """The partition must not become a way to excuse a real gap."""
    view = _record_view()
    _project(view, cache_graph)
    cache_graph.query("MATCH (n:Point {id:'p-9189-2'}) DETACH DELETE n")

    result = _run(view, cache_graph)

    assert result.declaration.coverage == COVERAGE_COMPLETE
    assert result.not_hot == 0
    assert result.report.missing_in_cache == 1
    assert result.verdict() == rr.VERDICT_DIVERGENT


def test_an_undeclared_side_treats_a_missing_key_as_divergence(cache_graph):
    """`declaration=None` is the §2.0 default (complete), not "no opinion"."""
    view = _record_view()
    _project(view, cache_graph)

    result = rr.reconcile_record_and_cache(
        rr.record_key_map(view),
        {"Point:p-9189-1": {"id": "p-9189-1", "content": "alpha",
                            "status": "live"}},
    )

    assert result.not_hot == 0
    assert result.report.missing_in_cache == 1


# ── 4. refusals, and "nothing compared" is not "clean" ────────────────────


def test_an_unreadable_cache_side_refuses():
    class _Broken:
        def query(self, *a, **k):
            raise RuntimeError("falkordb is down")

    with pytest.raises(ReconciliationUnavailable) as excinfo:
        rr.read_cache_view(_Broken(), labels=("Point",))
    assert "UNKNOWN, not zero" in str(excinfo.value)


def test_a_cache_node_with_no_contract_key_refuses():
    """Skipping it would UNDERCOUNT `extra_in_cache` — the second-writer count."""
    class _Keyless:
        class _Result:
            result_set: ClassVar[list] = [[{"content": "no id here"}]]

        def query(self, *a, **k):
            return self._Result()

    with pytest.raises(ReconciliationUnavailable) as excinfo:
        rr.read_cache_view(_Keyless(), labels=("Point",))
    assert "contract key" in str(excinfo.value)


def test_a_label_outside_the_record_placement_is_refused(cache_graph):
    with pytest.raises(ValueError):
        rr.read_cache_view(cache_graph, labels=("Point", ") DETACH DELETE n //"))


def test_two_cache_nodes_at_one_coordinate_refuse(cache_graph):
    """Collapsing a duplicate would report `extra_in_cache=0` over a real node.

    The writer ``MERGE``s on the key and the graph has no uniqueness
    constraint, so a second writer's ``CREATE`` at an EXISTING id is reachable
    — and it is the one shape that leaves the two key SETS equal. Pre-fix the
    reader kept the last node and the run read clean; a new id (the test above)
    cannot discriminate this case.
    """
    view = _record_view()
    _project(view, cache_graph)
    cache_graph.query("CREATE (:Point {id:'p-9189-1', content:'impostor'})")

    with pytest.raises(ReconciliationUnavailable) as excinfo:
        rr.read_cache_view(cache_graph, labels=("Point",))

    message = str(excinfo.value)
    assert "Point:p-9189-1" in message
    assert "UNKNOWN, not zero" in message


def test_a_vacuous_comparison_refuses():
    """Placeholder maps make `field_mismatches` structurally zero (#8609)."""
    with pytest.raises(ReconciliationUnavailable) as excinfo:
        rr.reconcile_record_and_cache(
            {"Point:p1": {}, "Point:p2": {}}, {"Point:p1": {}, "Point:p2": {}})
    assert "EMPTY property maps" in str(excinfo.value)


def test_nothing_compared_is_unanswerable_never_clean():
    result = rr.reconcile_record_and_cache({}, {})

    assert result.checked == 0
    assert result.verdict() == rr.VERDICT_UNANSWERABLE
    assert rr.exit_code_for(result) == rr.EXIT_UNAVAILABLE


def test_the_result_carries_its_counts_and_verdict():
    result = rr.reconcile_record_and_cache(
        {"Point:p1": {"id": "p1"}}, {"Point:p1": {"id": "p1"}})
    payload = result.as_dict()

    assert payload["verdict"] == rr.VERDICT_CLEAN
    assert payload["checked"] == 1
    assert payload["not_hot"] == 0
    assert payload["declaration_coverage"] == COVERAGE_COMPLETE
    assert payload["divergent"] == 0


def test_a_partial_declaration_reports_its_coverage():
    declaration = ResidencyDeclaration(coverage=COVERAGE_PARTIAL,
                                       hot_keys=frozenset({"Point:p1"}))
    result = rr.reconcile_record_and_cache(
        {"Point:p1": {"id": "p1"}, "Point:p2": {"id": "p2"}},
        {"Point:p1": {"id": "p1"}},
        declaration=declaration,
    )

    assert result.not_hot == 1
    assert result.report.missing_in_cache == 0
    assert result.as_dict()["declaration_coverage"] == COVERAGE_PARTIAL


# ── 5. the trigger exists in production ───────────────────────────────────


#: Never descend into these while looking for importers: vendored, cached,
#: and OTHER WORKTREES' checkouts of the same repo (a lane's sibling worktree is
#: not this tree's production code).
_SKIP_DIRS = frozenset({
    ".git", ".venv", "venv", ".worktrees", "node_modules", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache", "build", "dist",
    # The pin below asserts every importer is OUTSIDE these; pruning them from
    # the walk is what keeps it fast, and the assertion keeps the intent
    # visible (a test/probe importer can never be what satisfies the gate).
    "tests", "docs",
})


def _production_importers(module: str) -> set[Path]:
    """Every non-test, non-docs file that imports ``module`` (AST, not grep).

    ``tests/`` and ``docs/`` are PRUNED rather than filtered: the question this
    answers is *"is a probe or a test what wires this?"* (#9188's indicator 3,
    the same question for the record write), so a hit can only come from
    production code.

    Four spellings count, because all four are real imports of the same module:
    ``import tortoise.record_reconcile`` · ``from tortoise.record_reconcile
    import x`` · ``from .record_reconcile import x`` (the intra-package form the
    CLI uses) · ``from tortoise import record_reconcile``.
    """
    leaf = module.rsplit(".", 1)[1]
    importers: set[Path] = set()
    for dirpath, dirnames, filenames in os.walk(_REPO_ROOT):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for filename in sorted(filenames):
            if not filename.endswith(".py"):
                continue
            path = Path(dirpath) / filename
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            # Cheap, SOUND pre-filter: every import spelling of a module
            # contains its leaf name as a literal, so a file without the string
            # cannot import it (a false POSITIVE only costs an `ast.parse`, and
            # a comment hit is discarded by the AST pass below). Without it,
            # parsing the two 10k+ line production files dominates the suite.
            if leaf not in text:
                continue
            try:
                with warnings.catch_warnings():
                    # A dependency's file with an invalid escape is not this
                    # pin's subject; parsing is all it needs to survive.
                    warnings.simplefilter("ignore", SyntaxWarning)
                    tree = ast.parse(text)
            except (SyntaxError, ValueError):
                continue
            if _imports_module(tree, module, leaf):
                importers.add(path)
    return importers


def _imports_module(tree: ast.AST, module: str, leaf: str) -> bool:
    """Does this module-level AST import `module` by any of the four spellings?"""
    parent = module.rsplit(".", 1)[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
                alias.name == module for alias in node.names):
            return True
        if isinstance(node, ast.ImportFrom):
            if node.module == module:
                return True
            if node.level and node.module == leaf:
                return True
            if node.module == parent and any(
                    alias.name == leaf for alias in node.names):
                return True
    return False


@functools.cache
def _cached_production_importers(module: str) -> frozenset[str]:
    """The walk is repo-wide; do it ONCE for every pin that needs it."""
    return frozenset(
        str(p.relative_to(_REPO_ROOT))
        for p in _production_importers(module))


def test_the_reconciler_has_a_non_test_non_docs_caller():
    importers = sorted(_cached_production_importers(_TRIGGER_MODULE))

    assert importers, (
        f"no production importer of {_TRIGGER_MODULE} — the observer is again "
        f"reachable only from tests and evidence probes")
    assert all(not r.startswith(("tests/", "docs/")) for r in importers)


def test_the_trigger_path_is_the_cli_verb():
    """The chosen trigger is the operator-invoked sweep, wired at the CLI."""
    assert "tortoise/__main__.py" in _cached_production_importers(_TRIGGER_MODULE)


def test_the_cli_verb_refuses_without_a_record_dsn(monkeypatch, capsys):
    """A scheduling signal that cannot read the record is NOT a clean run."""
    from tortoise import __main__ as cli

    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)
    rc = cli.main(["record-reconcile", "--db", "docker://:x@localhost:6379/g",
                   "--org", "o", "--graph", "g"])

    assert rc == rr.EXIT_UNAVAILABLE
    assert "UNKNOWN, not zero" in capsys.readouterr().err


def test_a_usage_error_exits_2_not_1(capsys):
    """The verb's documented exit contract, pinned where argparse decides it.

    ``--graph`` is required, so argparse's own ``error()`` answers ``2`` — the
    SAME code as ``EXIT_UNAVAILABLE`` — and ``main()`` re-raises that
    ``SystemExit``. The docstring used to promise ``1`` for usage, which no
    path returns; a scheduler keying on the documented code would read a
    malformed cron line as "the record is unreachable".
    """
    from tortoise import __main__ as cli

    with pytest.raises(SystemExit) as excinfo:
        cli.main(["record-reconcile", "--db", "docker://:x@localhost:6379/g",
                  "--org", "o"])

    assert excinfo.value.code == 2
    _cmd = cli._cmd_record_reconcile
    assert "``1`` is never returned" in (_cmd.__doc__ or "")

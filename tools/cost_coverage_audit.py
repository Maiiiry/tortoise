#!/usr/bin/env python3
"""#5045 — the cost-coverage audit: is dimension X measured? (a query, not a grep)

Two verbs against ONE declaration (``tortoise/cost_dimensions.py``):

``query <dimension>``
    Answer the substrate's own question. Prints the declared state, unit,
    attribution, read path and every declared limit. Exit 0 when the dimension
    is measured or an allocation, 3 when it is declared OUT OF SCOPE (a real
    answer, with its reason — not "nobody"), 2 when no such dimension is
    declared (so a caller can never read a typo as "not measured").

``matrix``
    Print the coverage matrix and RE-VERIFY every declaration against the live
    code and the tracked tree. This is the part that makes the registry an
    instrument rather than a comment:

    1. **bindings** — every declared writer/reader resolves and is callable;
       every declared ``metric`` resolves to a live prometheus child whose label
       set is EXACTLY the declared one (a metric that gained or lost a label is
       a series change, so it is drift, not detail).
    2. **ledger** — every declared column is created by a tracked migration and
       every declared SQL RPC is defined by one. A column renamed in SQL and not
       in the declaration fails here.
    3. **no dead hook** — a dimension's writer (or reader, when the read path is
       the primitive) must have a call site in a tracked NON-TEST file, or the
       declaration must carry ``unscheduled_reason``. #4493's defect was a metric
       that registered, exported and tested while never being incremented in
       production: "wired and tested" is not "measured".
    4. **no undeclared dimension** — every org-attributed prometheus child in
       ``tortoise/monitoring.py`` must be declared, and no OTHER tracked module
       may construct a prometheus metric without declaring it. A seventh counter
       added in a hurry is the failure mode #5045 exists to end.
    5. **cardinality** — each declared cap/overflow attribute resolves to a live
       int/str, so a renamed or re-valued cap cannot silently detach from its
       declaration.

Exit 0 clean, 1 on ANY drift (naming each finding), 2 on a usage error.

⛔ The audit never fabricates: an unresolvable declaration is reported as
drift, never skipped with a warning.

Usage:
    uv run python tools/cost_coverage_audit.py matrix [--root .] [--json]
    uv run python tools/cost_coverage_audit.py query egress
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from functools import cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tortoise import cost_dimensions as cd  # noqa: E402

#: Where the prometheus metrics of this repo are declared. The drift gate is
#: scoped to it because it IS the declared metric surface: ``monitoring.py``
#: owns the substrate's children, and a metric built anywhere else is a second,
#: undeclared surface.
METRIC_MODULE = "tortoise.monitoring"

#: Tracked path prefixes that are not production call sites. ``tests/`` is the
#: point of the check (a writer only tests reach is the #4493 dead hook); the
#: worktree path is a full second copy of the tree and must never be scanned;
#: ``docs/`` holds no code; ``battery/`` and ``graph-scripts/`` are an eval
#: harness and historical one-off scripts that are not the product's cost path
#: (a metric they build would not be a production measurement — if one ever does
#: become one, it belongs in ``tortoise/monitoring.py`` and this list is wrong,
#: which the reviewer of that change should catch here).
_NOT_PRODUCTION = ("tests/", ".worktrees/", "docs/", "battery/", "graph-scripts/")

_MIGRATIONS = "supabase/migrations"


def _repo_root(root: Path | None) -> Path:
    return Path(root).resolve() if root else _ROOT


def tracked_python(root: Path) -> list[str]:
    """The tracked ``.py`` corpus (``git ls-files``), or a walk as a fallback."""
    return list(_tracked_python(Path(root).resolve()))


@cache
def _tracked_python(root: Path) -> tuple[str, ...]:
    """Cached corpus: the audit is called once per check and the tree is static."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "*.py"], cwd=root, capture_output=True,
            text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return tuple(str(p.relative_to(root)) for p in root.rglob("*.py"))
    return tuple(line for line in out.splitlines() if line.endswith(".py"))


def migration_text(root: Path) -> str:
    """Every tracked migration's SQL, concatenated (the DDL is the authority)."""
    return _migration_text(Path(root).resolve())


@cache
def _migration_text(root: Path) -> str:
    migdir = root / _MIGRATIONS
    if not migdir.is_dir():
        return ""
    return "\n".join(p.read_text(encoding="utf-8", errors="replace")
                     for p in sorted(migdir.glob("*.sql")))


# ── 1. bindings ──────────────────────────────────────────────────────────


def check_bindings() -> list[str]:
    """Every declared binding resolves; metric labels match the declaration."""
    findings: list[str] = []
    for dim in cd.dimensions():
        for binding in dim.bindings:
            target = _safe_resolve(binding, findings)
            if target is None:
                continue
            if binding.kind == cd.METRIC:
                labels = tuple(getattr(target, "_labelnames", ()) or ())
                if labels != binding.labels:
                    findings.append(
                        f"{dim.name}: metric {binding.dotted} carries labels "
                        f"{labels!r}, declared {binding.labels!r}")
            elif not callable(target):
                findings.append(
                    f"{dim.name}: {binding.kind} {binding.dotted} is not callable")
    return findings


def _safe_resolve(binding: cd.Binding, findings: list[str]):
    try:
        return cd.resolve(binding)
    except Exception as exc:  # noqa: BLE001, RUF100 — an unresolvable declaration IS drift
        findings.append(f"declared binding does not resolve: {binding.dotted} ({exc!r})")
        return None


# ── 2. ledger columns + RPCs ─────────────────────────────────────────────


def check_ledger(root: Path) -> list[str]:
    """Every declared column and SQL RPC is CREATED by a tracked migration.

    A column is matched only in DDL position (an ``ADD COLUMN``, or a definition
    line inside ``CREATE TABLE``), never as a bare substring: the migrations in
    this repo quote column names in their own header comments, so a substring
    match would pass on prose that creates nothing — the false green this whole
    instrument exists to avoid.
    """
    sql = migration_text(root)
    if not sql:
        return [f"no migrations found under {_MIGRATIONS}/ — cannot verify the ledger"]
    findings: list[str] = []
    for dim in cd.dimensions():
        for column in dim.columns:
            if not _creates_column(sql, column):
                findings.append(
                    f"{dim.name}: declared column {column!r} is not created by any "
                    "migration (DDL position)")
        for rpc in dim.rpcs:
            if not re.search(rf"FUNCTION\s+public\.{re.escape(rpc)}\b", sql):
                findings.append(
                    f"{dim.name}: declared SQL function {rpc!r} is not defined by "
                    "any migration")
    return findings


#: A column DEFINITION, not a mention: ``ADD COLUMN [IF NOT EXISTS] <name>`` or a
#: definition line inside a ``CREATE TABLE`` body.
_COLUMN_TYPES = (
    "text", "bigint", "integer", "int", "smallint", "real", "numeric",
    "double precision", "boolean", "timestamptz", "timestamp", "date", "uuid",
    "jsonb", "json",
)


def _creates_column(sql: str, column: str) -> bool:
    pattern = (
        rf"ADD COLUMN (?:IF NOT EXISTS )?{re.escape(column)}\b"
        rf"|^\s*{re.escape(column)}\s+(?:{'|'.join(_COLUMN_TYPES)})\b")
    return bool(re.search(pattern, sql, re.IGNORECASE | re.MULTILINE))


# ── 3. no dead hook ──────────────────────────────────────────────────────


def referenced_names(path: Path) -> set[str]:
    """Every function NAME referenced in *path* (``f`` and ``m.f``, called or not).

    A reference, not only a call, because a legitimate production call site can
    hand the writer to a runner instead of calling it inline — measured:
    ``hosted_api`` reaches ``metering.record_capture_usage`` as an ARGUMENT to
    ``run_on_daemon_worker``, so a Call-only predicate reported the live capture
    dimension as a dead hook. Comments never appear in the AST and a docstring is
    a ``Constant``, so prose cannot satisfy this check.
    """
    return set(_file_facts(Path(path)).names)


@dataclass(frozen=True)
class _FileFacts:
    """One cached AST pass per file: both corpus gates read the same parse.

    The dead-hook scan and the foreign-metric scan both need every tracked
    module, so parsing twice doubled the audit's cost (measured: ~7 s per pass
    over 1,700 files). One pass, two facts.
    """

    names: frozenset[str]
    builds_prometheus_metric: bool


@cache
def _file_facts(path: Path) -> _FileFacts:
    """Cached per-file AST facts — the scan walks 1,700 files."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, OSError):
        return _FileFacts(frozenset(), False)
    names: set[str] = set()
    metric_names = {"Counter", "Gauge", "Histogram", "Summary"}
    builds = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        if builds or not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else "")
        if name in metric_names and _looks_like_prometheus(node):
            builds = True
    return _FileFacts(frozenset(names), builds)


def production_call_sites(root: Path) -> dict[str, list[str]]:
    """Map each declared writer/reader name to the non-test files that use it."""
    wanted = {
        binding.attr
        for dim in cd.dimensions()
        for binding in (dim.writers() or dim.readers())
    }
    sites: dict[str, list[str]] = {name: [] for name in wanted}
    for rel in tracked_python(root):
        if any(rel.startswith(prefix) for prefix in _NOT_PRODUCTION):
            continue
        path = root / rel
        if not path.is_file():
            continue
        for name in wanted & referenced_names(path):
            sites[name].append(rel)
    return sites


def check_dead_hooks(root: Path) -> list[str]:
    """A dimension must have a production call site, or say why it has none."""
    sites = production_call_sites(root)
    findings: list[str] = []
    for dim in cd.dimensions():
        if dim.state == cd.OUT_OF_SCOPE:
            continue
        bindings = dim.writers() or dim.readers()
        if not bindings:
            findings.append(f"{dim.name}: declares no writer and no reader")
            continue
        reached = [b.attr for b in bindings if sites.get(b.attr)]
        if not reached and not dim.unscheduled_reason:
            findings.append(
                f"{dim.name}: no production call site for "
                f"{', '.join(b.attr for b in bindings)} — a writer only tests "
                "reach is a dead hook (#4493); declare unscheduled_reason if "
                "that is deliberate")
    return findings


# ── 4. no undeclared dimension ───────────────────────────────────────────


def _prometheus_children(module) -> dict[str, object]:
    """Every prometheus Counter/Gauge/Histogram/Summary held by *module*."""
    try:
        from prometheus_client import Counter, Gauge, Histogram, Summary
    except ImportError:  # pragma: no cover — prometheus is a hard dependency
        return {}
    kinds = (Counter, Gauge, Histogram, Summary)
    return {name: obj for name, obj in vars(module).items()
            if isinstance(obj, kinds)}


def undeclared_metric_findings(children: dict[str, object] | None = None) -> list[str]:
    """Org-attributed prometheus children that no declared dimension names."""
    if children is None:
        children = _prometheus_children(cd.resolve(METRIC_MODULE))
    declared = cd.attributed_bindings()
    findings: list[str] = []
    for name, obj in children.items():
        labels = tuple(getattr(obj, "_labelnames", ()) or ())
        if not cd.ATTRIBUTION_KEYS.intersection(labels):
            continue
        if name not in declared:
            findings.append(
                f"undeclared per-org metric {METRIC_MODULE}.{name} (labels "
                f"{labels!r}) — declare it as a dimension, or declare it out of "
                "scope with its reason")
    return findings


def foreign_metric_findings(root: Path) -> list[str]:
    """A prometheus metric constructed outside the declared metric module.

    A second metric surface is how a substrate grows a seventh counter that no
    declaration knows about. The check is source-level (AST), so it also catches
    a metric built lazily or in a test-visible helper.
    """
    try:
        import prometheus_client  # noqa: F401 — hard dependency; probes availability
    except ImportError:  # pragma: no cover
        return []
    findings: list[str] = []
    for rel in tracked_python(root):
        if any(rel.startswith(p) for p in _NOT_PRODUCTION):
            continue
        if rel == METRIC_MODULE.replace(".", "/") + ".py":
            continue
        path = root / rel
        if not path.is_file():
            continue
        if _file_facts(path).builds_prometheus_metric:
            findings.append(
                f"{rel}: constructs a prometheus metric outside "
                f"{METRIC_MODULE} — the substrate's metric surface is declared "
                "in one module")
    return findings


def _looks_like_prometheus(node: ast.Call) -> bool:
    """Distinguish ``prometheus_client.Counter`` from ``collections.Counter``.

    ``collections.Counter`` is used all over this repo for plain tallies; the
    only structural difference is the metric NAME string as the first argument,
    so that is what the check keys on rather than an import-list match (which a
    ``from prometheus_client import Counter as _C`` would defeat).
    """
    if not node.args:
        return False
    first = node.args[0]
    if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
        return False
    # A prometheus metric name is ``tortoise_*_total``-shaped, never a collection.
    return bool(re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", first.value))


# ── 5. cardinality ───────────────────────────────────────────────────────


def check_cardinality() -> list[str]:
    """Each declared cap/overflow attribute resolves to a live int/str."""
    findings: list[str] = []
    for dim in cd.dimensions():
        card = dim.cardinality
        if card is None:
            continue
        if card.lifetime not in (cd.PERSISTENT, cd.PER_PUBLISH):
            findings.append(
                f"{dim.name}: undeclared cardinality lifetime {card.lifetime!r}")
        for label, dotted, kind in (("cap", card.cap, int),
                                    ("overflow", card.overflow, str)):
            if dotted is None:
                findings.append(f"{dim.name}: declares {label} lifetime but no {label}")
                continue
            try:
                value = cd.resolve(dotted)
            except Exception as exc:  # noqa: BLE001, RUF100 — drift, not a warning
                findings.append(f"{dim.name}: {label} {dotted} does not resolve ({exc!r})")
                continue
            if not isinstance(value, kind):
                findings.append(
                    f"{dim.name}: {label} {dotted} is {type(value).__name__}, "
                    f"declared {kind.__name__}")
    return findings


# ── the verbs ────────────────────────────────────────────────────────────


def audit(root: Path) -> dict[str, list[str]]:
    """Run every check. Keys are the check names; values are its findings."""
    return {
        "bindings": check_bindings(),
        "ledger": check_ledger(root),
        "dead_hooks": check_dead_hooks(root),
        "undeclared_metrics": (
            undeclared_metric_findings() + foreign_metric_findings(root)),
        "cardinality": check_cardinality(),
    }


def _cmd_matrix(args) -> int:
    root = _repo_root(args.root)
    findings = audit(root)
    rows = cd.coverage()
    if args.json:
        print(json.dumps({"coverage": rows, "findings": findings}, indent=2))
    else:
        print(cd.render_matrix())
        print()
        flat = [f for group in findings.values() for f in group]
        if flat:
            print(f"DRIFT — {len(flat)} finding(s):")
            for name, group in findings.items():
                for finding in group:
                    print(f"  [{name}] {finding}")
        else:
            print("verified: bindings, ledger columns/RPCs, production call "
                  "sites, declared metrics, cardinality — no drift")
    return 1 if any(findings.values()) else 0


def _cmd_query(args) -> int:
    try:
        dim = cd.by_name(args.dimension)
    except KeyError as exc:
        print(f"no such dimension: {exc}", file=sys.stderr)
        return 2
    writer = ", ".join(b.attr for b in dim.writers()) or "—"
    reader = ", ".join(b.attr for b in dim.readers()) or "—"
    print(f"{dim.name}: {dim.state}")
    print(f"  kind:        {dim.kind}")
    print(f"  unit:        {dim.unit}")
    print(f"  attribution: {dim.attribution}")
    print(f"  writer:      {writer}")
    print(f"  reader:      {reader}")
    if dim.cardinality is not None:
        print(f"  cardinality: {dim.cardinality.lifetime} "
              f"(cap {dim.cardinality.cap}, overflow {dim.cardinality.overflow})")
    if dim.columns:
        print(f"  columns:     {', '.join(dim.columns)}")
    if dim.rpcs:
        print(f"  sql:         {', '.join(dim.rpcs)}")
    for limit in dim.limits:
        print(f"  ⚠ limit:     {limit}")
    if dim.unscheduled_reason:
        print(f"  schedule:    none — {dim.unscheduled_reason}")
    # A declared out-of-scope dimension is an ANSWER (with its reason), not a
    # missing primitive: exit 3 so a caller can tell the two apart.
    return 3 if dim.state == cd.OUT_OF_SCOPE else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="verb", required=True)

    matrix = sub.add_parser("matrix", help="print + verify the coverage matrix")
    matrix.add_argument("--root", default=None)
    matrix.add_argument("--json", action="store_true")
    matrix.set_defaults(func=_cmd_matrix)

    query = sub.add_parser("query", help="is this dimension measured?")
    query.add_argument("dimension")
    query.set_defaults(func=_cmd_query)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

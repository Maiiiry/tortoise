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
       every declared ``metric`` resolves to a live prometheus child whose family
       name and label set are EXACTLY the declared ones (a metric that gained or
       lost a label, or was renamed, is a series change, so it is drift, not
       detail).
    2. **ledger** — every declared column is created by a tracked migration and
       every declared SQL RPC is defined by one — both in DDL position (a
       ``CREATE [OR REPLACE] FUNCTION``, an ``ALTER TABLE … ADD COLUMN``), never as
       a substring and never inside a SQL comment. A column renamed in SQL and not
       in the declaration fails here, and so does an RPC renamed by ``ALTER
       FUNCTION … RENAME TO``.
    3. **no dead hook** — a dimension's writer (or reader, when the read path is
       the primitive) must have a call site in a tracked NON-TEST, NON-TOOL file,
       or the declaration must carry ``unscheduled_reason``. #4493's defect was a
       metric that registered, exported and tested while never being incremented in
       production: "wired and tested" is not "measured", and a developer
       instrument that mentions the writer is not a production call site either.
    4. **no undeclared dimension** — every org-attributed prometheus child in
       ``tortoise/monitoring.py`` must be declared, and no OTHER tracked module
       may construct a prometheus metric without declaring it (detected from the
       callee's IMPORT provenance, so an aliased constructor and a ``name=``
       keyword construction are both visible). A seventh counter added in a hurry
       is the failure mode #5045 exists to end.
    5. **cardinality** — each declared cap/overflow attribute resolves to a live
       int/str, on BOTH axes (the per-org cap/overflow and the route-class
       ``path_caps``/``path_overflow`` that carries its own ``__unrouted__``
       sentinel).
    6. **states** — every row's ``state`` is in the declared vocabulary, so a typo
       cannot fall into none of measured / allocation / out-of-scope and report a
       clean matrix.
    7. **corpus** — every tracked module the scans read can actually be PARSED. An
       unparseable file contributes no names and no metric facts, so reporting it
       is the difference between a gate that ran and a gate that looked like it
       did.

Exit 0 clean, 1 on ANY drift (naming each finding), 2 on a usage error.

⛔ The audit never fabricates: an unresolvable declaration is reported as
drift, never skipped with a warning.

Usage:
    uv run python tools/cost_coverage_audit.py matrix [--root .] [--json]
    uv run python tools/cost_coverage_audit.py query egress
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter BEFORE the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape), and
# the failure would be unattributed. This tool's documented invocation is
# `uv run python tools/cost_coverage_audit.py …`; the ambient `python3` may be
# older, and a guard here turns an ImportError into a named refusal.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/cost_coverage_audit.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/cost_coverage_audit.py`"
    )

import argparse
import ast
import json
import re
import subprocess
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
#: ``docs/`` holds no code; ``tools/`` is a set of developer instruments
#: (#4493/#5045 review P2: a script that mentions a writer is not a production
#: call site — accepting one is how "registered, exported and tested while never
#: incremented in production" reads as measured), and ``battery/`` and
#: ``graph-scripts/`` are an eval harness and historical one-off scripts that are
#: not the product's cost path (a metric they build would not be a production
#: measurement — if one ever does become one, it belongs in
#: ``tortoise/monitoring.py`` and this list is wrong, which the reviewer of that
#: change should catch here).
_NOT_PRODUCTION = (
    "tests/", ".worktrees/", "docs/", "tools/", "battery/", "graph-scripts/"
)

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
    """Every declared binding resolves; metric family AND labels match.

    A series is identified by its emitted family name plus its label set, so both
    halves are compared against the LIVE child: ``_name`` (what a scrape and every
    dashboard/alert selects on) and ``_labelnames``. Comparing only the labels
    left a family rename — the same series change in the other axis — invisible.
    """
    findings: list[str] = []
    for dim in cd.dimensions():
        for binding in dim.bindings:
            target = _safe_resolve(binding, findings)
            if target is None:
                continue
            if binding.kind == cd.METRIC:
                family = getattr(target, "_name", None)
                if binding.family and family != binding.family:
                    findings.append(
                        f"{dim.name}: metric {binding.dotted} emits family "
                        f"{family!r}, declared {binding.family!r}")
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

    Both halves are matched only in DDL position — an ``ALTER TABLE … ADD COLUMN``
    or a definition line inside ``CREATE TABLE`` for a column, a
    ``CREATE [OR REPLACE] FUNCTION public.<name>(`` for an RPC — never as a bare
    substring, and never inside a SQL comment: the migrations in this repo quote
    column names and function names in their own header comments, and an
    ``ALTER FUNCTION … RENAME TO`` / ``GRANT`` / ``REVOKE`` / ``COMMENT ON`` line
    names a function WITHOUT defining it. A substring match would pass on prose
    that creates nothing, which is the false green this whole instrument exists to
    avoid.
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
            if not _defines_function(sql, rpc):
                findings.append(
                    f"{dim.name}: declared SQL function {rpc!r} is not defined by "
                    "any migration (no CREATE FUNCTION)")
    return findings


def _strip_sql_comments(sql: str) -> str:
    """*sql* with ``--`` line and ``/* */`` block comments removed.

    The predicates below match DDL, and the migrations carry long header comments
    that quote the very statements they describe (measured:
    ``20260925000002_oauth_redemption_state.sql:100`` says "each `ADD COLUMN
    code_id` takes ACCESS EXCLUSIVE"). Matching the raw text therefore accepts a
    comment as a definition; stripping first is what makes the "DDL position"
    claim true.

    ORDER MATTERS: line comments go FIRST. A ``--`` note that happens to contain
    an unmatched ``/*`` would otherwise make the DOTALL block-comment sweep run to
    the next ``*/`` far away and delete real DDL — measured while fixing this
    (``graph_storage_mb``'s ``ALTER TABLE … ADD COLUMN`` vanished).
    """
    without_line = re.sub(r"--[^\n]*", "", sql)
    return re.sub(r"/\*.*?\*/", "", without_line, flags=re.DOTALL)


#: A column DEFINITION, not a mention: ``ADD COLUMN [IF NOT EXISTS] <name>`` or a
#: definition line inside a ``CREATE TABLE`` body.
_COLUMN_TYPES = (
    "text", "bigint", "integer", "int", "smallint", "real", "numeric",
    "double precision", "boolean", "timestamptz", "timestamp", "date", "uuid",
    "jsonb", "json",
)


def _creates_column(sql: str, column: str) -> bool:
    """True when *column* is CREATED — never merely named in a comment.

    Two accepted positions, both statement-anchored: an ``ALTER TABLE … ADD
    COLUMN`` (the whole statement, so the ``ADD COLUMN`` must belong to an ALTER
    and not to prose) or a definition line inside a ``CREATE TABLE`` body.
    """
    ddl = _strip_sql_comments(sql)
    alter = (
        rf"ALTER\s+TABLE\b[^;]*?\bADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?"
        rf"{re.escape(column)}\b")
    if re.search(alter, ddl, re.IGNORECASE | re.DOTALL):
        return True
    definition = (
        rf"^\s*{re.escape(column)}\s+(?:{'|'.join(_COLUMN_TYPES)})\b")
    return bool(re.search(definition, ddl, re.IGNORECASE | re.MULTILINE))


def _defines_function(sql: str, rpc: str) -> bool:
    """True when *rpc* is DEFINED (`CREATE [OR REPLACE] FUNCTION public.<rpc>(`).

    A ``GRANT``/``REVOKE``/``COMMENT ON``/``ALTER FUNCTION`` line names a function
    without creating it, so an RPC renamed by ``ALTER FUNCTION … RENAME TO`` keeps
    matching the loose ``FUNCTION public.<name>`` predicate while the declaration
    goes stale. Anchoring on the CREATE makes the rename visible.
    """
    pattern = (
        rf"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+public\."
        rf"{re.escape(rpc)}\s*\(")
    return bool(re.search(pattern, _strip_sql_comments(sql),
                          re.IGNORECASE | re.MULTILINE))


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

    ``error`` is the third fact: a file that cannot be PARSED is reported, never
    read as "no names, no metric". Returning clean facts for an unparseable
    module let a file that references every declared writer satisfy the dead-hook
    check while contributing nothing to it — a silent fail-OPEN in the check whose
    stated job is "never skipped with a warning".
    """

    names: frozenset[str]
    builds_prometheus_metric: bool
    error: str | None = None


@cache
def _file_facts(path: Path) -> _FileFacts:
    """Cached per-file AST facts — the scan walks 1,700 files."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except (SyntaxError, OSError, ValueError) as exc:
        return _FileFacts(frozenset(), False, f"{type(exc).__name__}: {exc}")
    names: set[str] = set()
    constructs, modules = _prometheus_handles(tree)
    builds = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        if builds or not isinstance(node, ast.Call):
            continue
        if _looks_like_prometheus(node, constructs, modules):
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


def unparseable_corpus_findings(root: Path) -> list[str]:
    """Tracked modules the corpus gates could not inspect.

    A file that does not parse contributes no names and no metric facts, so it
    silently satisfies BOTH the dead-hook scan (it may reference every declared
    writer) and the foreign-metric scan (it may build a tenth counter). The audit's
    contract is that an unresolvable input is reported as drift, never skipped —
    this is that report.
    """
    findings: list[str] = []
    for rel in tracked_python(root):
        if any(rel.startswith(prefix) for prefix in _NOT_PRODUCTION):
            continue
        path = root / rel
        if not path.is_file():
            continue
        error = _file_facts(path).error
        if error is not None:
            findings.append(
                f"{rel}: cannot be parsed ({error}) — the dead-hook and "
                "foreign-metric scans cannot see it")
    return findings


# ── 4. no undeclared dimension ───────────────────────────────────────────


def _prometheus_children(module) -> dict[str, object]:
    """Every prometheus Counter/Gauge/Histogram/Summary held by *module*.

    Raises ``ImportError`` when prometheus_client is absent — deliberately: the
    caller turns that into a FINDING. Swallowing it returned ``{}``, which made
    the undeclared-metric gate pass silently on a tree where it could not run at
    all (the audit never fabricates, and never reports a vacuous clean either).
    """
    from prometheus_client import Counter, Gauge, Histogram, Summary

    kinds = (Counter, Gauge, Histogram, Summary)
    return {name: obj for name, obj in vars(module).items()
            if isinstance(obj, kinds)}


def undeclared_metric_findings(children: dict[str, object] | None = None) -> list[str]:
    """Org-attributed prometheus children that no declared dimension names."""
    if children is None:
        try:
            children = _prometheus_children(cd.resolve(METRIC_MODULE))
        except ImportError as exc:  # pragma: no cover — prometheus is a hard dependency
            return [f"prometheus_client is not importable ({exc}) — the "
                    "declared-metric gate cannot run; install the runtime deps "
                    "and re-run (a gate that cannot run is drift, not a pass)"]
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
    except ImportError as exc:  # pragma: no cover — prometheus is a hard dependency
        return [f"prometheus_client is not importable ({exc}) — the "
                "foreign-metric gate cannot run; install the runtime deps and "
                "re-run (a gate that cannot run is drift, not a pass)"]
    findings: list[str] = []
    for rel in tracked_python(root):
        if any(rel.startswith(p) for p in _NOT_PRODUCTION):
            continue
        if rel == METRIC_MODULE.replace(".", "/") + ".py":
            continue
        path = root / rel
        if not path.is_file():
            continue
        facts = _file_facts(path)
        if facts.error is not None:
            findings.append(
                f"{rel}: cannot be parsed ({facts.error}) — the foreign-metric "
                "gate cannot inspect it, so it is reported rather than skipped")
            continue
        if facts.builds_prometheus_metric:
            findings.append(
                f"{rel}: constructs a prometheus metric outside "
                f"{METRIC_MODULE} — the substrate's metric surface is declared "
                "in one module")
    return findings


#: The prometheus metric constructors this repo's surface is built from.
_PROMETHEUS_TYPES = frozenset({"Counter", "Gauge", "Histogram", "Summary"})


def _prometheus_handles(tree: ast.Module) -> tuple[frozenset[str], frozenset[str]]:
    """The names *tree* binds to prometheus constructors / the module itself.

    ``(constructors, modules)``: bare names bound by ``from prometheus_client
    import Counter [as _C]`` and aliases bound by ``import prometheus_client [as
    pc]``. Resolving the callee to its IMPORT is what makes the gate trustworthy in
    both directions: an aliased constructor is still caught, while
    ``collections.Counter(...)`` — ordinary tally code used all over this repo — is
    not, whatever its arguments look like.
    """
    constructors: set[str] = set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "prometheus_client":
            for alias in node.names:
                if alias.name in _PROMETHEUS_TYPES:
                    constructors.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "prometheus_client":
                    modules.add(alias.asname or alias.name)
    return frozenset(constructors), frozenset(modules)


def _looks_like_prometheus(node: ast.Call, constructors: frozenset[str] = frozenset(
                               {"Counter", "Gauge", "Histogram", "Summary"}),
                           modules: frozenset[str] = frozenset({"prometheus_client"}),
                           ) -> bool:
    """Does *node* construct a prometheus metric?

    Keyed on the CALLEE's provenance (bound from ``prometheus_client``, aliased or
    module-qualified), not on a name-shaped string argument. Both weaker designs
    failed in opposite directions, and each is pinned by a test: a name-shape-only
    check cannot see an aliased import (``_C("tortoise_x_total", …)``) and cannot
    see a ``name=`` keyword construction (``Counter(name="tortoise_x_total",
    documentation=…)``), while an any-identifier check reds on honest tallies.
    """
    func = node.func
    if isinstance(func, ast.Name) and func.id in constructors:
        return True
    return (
        isinstance(func, ast.Attribute)
        and func.attr in _PROMETHEUS_TYPES
        and isinstance(func.value, ast.Name)
        and func.value.id in modules
    )


# ── 5. cardinality ───────────────────────────────────────────────────────


def check_states() -> list[str]:
    """Every ``Dimension.state`` is in the declared vocabulary.

    A typo'd state falls into NONE of measured / allocations / out-of-scope, so the
    matrix's arithmetic silently stops adding up and the coverage query answers
    "not measured" for a row that claims to be. Declared vocabulary or drift.
    """
    return [
        f"{dim.name}: undeclared state {dim.state!r} (declared: "
        f"{', '.join(cd.states())})"
        for dim in cd.dimensions()
        if dim.state not in cd.states()
    ]


def check_cardinality() -> list[str]:
    """Each declared cap/overflow attribute resolves to a live int/str.

    Covers BOTH axes: the per-org cap/overflow and the route-class
    ``path_caps``/``path_overflow``. The path axis is a live label-cap convention
    in this substrate (its own caps, its own sentinel), so leaving it out of the
    declaration would be exactly the undeclared-convention drift this gate exists
    to end.
    """
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
            findings.extend(_resolve_typed(dim.name, label, dotted, kind))
        for dotted in card.path_caps:
            findings.extend(_resolve_typed(dim.name, "path cap", dotted, int))
        if card.path_caps and card.path_overflow is None:
            findings.append(f"{dim.name}: declares path caps but no path overflow")
        if card.path_overflow is not None:
            findings.extend(
                _resolve_typed(dim.name, "path overflow", card.path_overflow, str))
    return findings


def _resolve_typed(dim_name: str, label: str, dotted: str, kind: type) -> list[str]:
    """Resolve *dotted* and check its type — a single finding, never a raise."""
    try:
        value = cd.resolve(dotted)
    except Exception as exc:  # noqa: BLE001, RUF100 — drift, not a warning
        return [f"{dim_name}: {label} {dotted} does not resolve ({exc!r})"]
    if not isinstance(value, kind):
        return [f"{dim_name}: {label} {dotted} is {type(value).__name__}, "
                f"declared {kind.__name__}"]
    return []


# ── the verbs ────────────────────────────────────────────────────────────


def audit(root: Path) -> dict[str, list[str]]:
    """Run every check. Keys are the check names; values are its findings."""
    return {
        "bindings": check_bindings(),
        "ledger": check_ledger(root),
        "corpus": unparseable_corpus_findings(root),
        "dead_hooks": check_dead_hooks(root),
        "states": check_states(),
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
        # The summary's last figure is DERIVED: the registry's own unbacked rows
        # plus the undeclared-metric findings this audit owns (it needs the live
        # prometheus children, which the dependency-free registry does not import).
        print(cd.render_matrix(
            undeclared_metrics=len(findings["undeclared_metrics"])))
        print()
        flat = [f for group in findings.values() for f in group]
        if flat:
            print(f"DRIFT — {len(flat)} finding(s):")
            for name, group in findings.items():
                for finding in group:
                    print(f"  [{name}] {finding}")
        else:
            print("verified: bindings, ledger columns/RPCs, production call "
                  "sites, declared metrics, cardinality, states, corpus — "
                  "no drift")
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

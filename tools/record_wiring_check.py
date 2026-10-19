#!/usr/bin/env python3
"""Is the Postgres record WIRED? (#9188 / #9078, epic #7879)

WHY THIS EXISTS. `tortoise/record_projection.py` was complete, proven and
callable — and **nothing in production called its write half**. Measured on
#9078: its only importer was `supabase/tests/pglite/validate.mjs`, a test
harness. That is not a defect in any one function, so no unit test could see
it: the claim "the record is written in production" was true of the tests and
false of the product, and only a check that asks the *shape of the code base*
can say so. This is that check.

WHAT IT ASSERTS (exit 0 only when all three hold)

1. **A production caller exists.** Some module inside the shipped package
   (`tortoise/`) imports the record's write half and CALLS it. "Production" is
   not a path allowlist a probe can join: it is *the package that is
   installed*, so `tests/**`, `docs/evidence/**` and `supabase/tests/**` can
   never satisfy it — the issue's own words: *"a probe is the defect, not the
   fix."*
2. **Every one of the four ontology layers has a writer.** The layer tables are
   derived from `record_projection.RECORD_MUTATIONS` — the ONE declaration of
   journal→record — never from a hand-written list here. A second list is the
   second declaration that goes stale.
3. **The live caller does not invent a position.** The live path cannot write
   `journal_event.seq` (#9190, `record_writer.POSITION_ROW_DEFERRED_ON`), so a
   live call that omits `position=False` is a FAILURE: an invented position
   collides with a later replay's numbering in silence.

EXIT CODES — a signal that cannot be read is not a pass.

    0  wired: a production caller + a writer for every layer + no invented position
    1  NOT wired: no production caller, a layer with no writer, or an invented position
    2  CANNOT ANSWER: a module could not be parsed, or the fold could not be read

The tool is read-only and DB-free: it must run in every lane, including where
no Postgres exists.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = REPO_ROOT / "tortoise"

#: The four ontology layers and the record tables each is made of. Layer 4
#: (procedural) owns NO table by construction — `20261012000004_record_
#: procedural.sql` is views over layers 1-3 ("The procedural layer is a
#: VIEW/READ — never a fourth table") — so the writer its `object_current_status`
#: view needs is the `object` row. Stated here because a maintainer looking for
#: a procedural TABLE must not find one.
LAYERS: dict[str, tuple[str, ...]] = {
    "event stream": ("event", "source", "session"),
    "semantic": ("object", "subject"),
    "epistemic": ("point", "relation"),
    "procedural": ("object",),
}

#: The modules that ARE the record, never callers of it.
_RECORD_STEMS = frozenset({"record_projection", "record_writer"})

#: The names that mean "this file calls the record's write half" — and the
#: record module they must be reached THROUGH. A bare `replay`/`RecordWriter`
#: is not evidence: a module can hold a local helper of either name, or
#: construct a writer it never writes with. Only an attribute off the record
#: module counts, so the check cannot go green on a name collision.
_CALL_NAMES = frozenset({"write_live_record", "write_records", "write"})

#: The one LIVE door: the helper every journal-append site calls. Its existence
#: is what the position check anchors on.
_LIVE_DOOR = "write_live_record"


def _iter_package_modules() -> list[Path]:
    return sorted(p for p in PACKAGE.rglob("*.py") if p.is_file())


def _imported_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
            for alias in node.names:
                names.add(alias.name)
    return names


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _imports_the_record(names: set[str]) -> bool:
    """Does an import set reach `record_projection`/`record_writer`?"""
    for name in names:
        head = name.split(".")[-1]
        if head in _RECORD_STEMS:
            return True
    return False


def _record_calls(tree: ast.AST) -> set[str]:
    """Calls reached THROUGH the record module (never a bare name collision)."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Attribute) and func.attr in _CALL_NAMES
                and isinstance(func.value, ast.Name)
                and func.value.id in _RECORD_STEMS):
            found.add(func.attr)
    return found


def find_production_callers() -> tuple[list[dict[str, object]], list[str]]:
    """Modules in the package that import AND call the record's write half."""
    callers: list[dict[str, object]] = []
    problems: list[str] = []
    for path in _iter_package_modules():
        rel = path.relative_to(REPO_ROOT).as_posix()
        if path.stem in _RECORD_STEMS:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            problems.append(f"{rel}: could not be parsed ({exc})")
            continue
        if not _imports_the_record(_imported_names(tree)):
            continue
        called = sorted(_record_calls(tree))
        if called:
            callers.append({"module": rel, "calls": called})
    return callers, problems


def layer_writers() -> tuple[dict[str, list[str]], list[str]]:
    """`{table: [record types]}` derived from the fold's own registry."""
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from tortoise import record_projection
    except Exception as exc:                          # unimportable fold
        return {}, [f"could not import tortoise.record_projection ({exc})"]
    registry = getattr(record_projection, "RECORD_MUTATIONS", None)
    if not registry:
        return {}, ["record_projection.RECORD_MUTATIONS is empty or missing"]
    writers: dict[str, list[str]] = {}
    problems: list[str] = []
    for record_type, spec in registry.items():
        mutations = spec if isinstance(spec, (list, tuple)) else (spec,)
        for mutation in mutations:
            tables, problem = _tables_of(mutation)
            if problem:
                problems.append(f"{record_type}: {problem}")
            for table in tables:
                writers.setdefault(table, []).append(record_type)
    return writers, problems


#: Effects whose table is IMPLICIT in the statement emitter (they carry no
#: `node`/`table` attribute), named here so the check sees every layer. A new
#: effect class that is missing from this map makes the check CANNOT-ANSWER
#: (exit 2) rather than quietly under-reporting a layer — the fail-closed
#: direction, and the reason this is a map and not a default.
_IMPLICIT_TABLES: dict[str, tuple[str, ...]] = {
    "RelationUpsert": ("relation",),
    "RelationDelete": ("relation",),
    # The refusal STAYS in the event stream: it writes no layer row by design
    # (NoRecordEffect, #9187/#9200 row 4).
    "NoRecordEffect": (),
}


def _tables_of(mutation: object) -> tuple[set[str], str | None]:
    """`({table}, None)` for a mutation, or `(set(), reason)` if unclassifiable.

    Structural first (any nested attribute carrying a `table`), then the
    declared implicit map. Anything else is a hole in this check, reported
    rather than assumed empty.
    """
    found: set[str] = set()
    effect = getattr(mutation, "effect", mutation)
    table = getattr(effect, "table", None)
    if isinstance(table, str) and table:
        found.add(table)
    for value in getattr(effect, "__dict__", {}).values():
        nested = getattr(value, "table", None)
        if isinstance(nested, str) and nested:
            found.add(nested)
    name = type(effect).__name__
    # `OpDispatch` (EntityMutated / PointsMerged) resolves its table from the
    # record's `op` via `table_for_label` — read the declaration rather than
    # guessing a layer.
    table_for_label = getattr(effect, "table_for_label", None)
    if isinstance(table_for_label, dict):
        found.update(t for t in table_for_label.values() if isinstance(t, str) and t)
    if name in _IMPLICIT_TABLES:
        found.update(_IMPLICIT_TABLES[name])
        return found, None
    if found:
        return found, None
    return set(), f"effect class {name!r} writes no table this check can see"


def live_door_defers_the_position() -> list[str]:
    """The LIVE door must pass `position=False` as a LITERAL, or not exist.

    The live path has no journal ordinal (#9190), so a position row it wrote
    would be invented — and an invented `seq` collides with a later replay's
    numbering on the `(org_id, graph_id, seq)` primary key, in silence. Two
    ways to fail, both checked: the door is missing (the wiring moved and this
    tool must learn the new place), or its `write_records(...)` call binds
    `position` to anything but the literal `False`.
    """
    path = REPO_ROOT / "tortoise" / "record_writer.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        return [f"tortoise/record_writer.py could not be read ({exc})"]
    door = next(
        (node for node in ast.walk(tree)
         if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
         and node.name == _LIVE_DOOR),
        None,
    )
    if door is None:
        return [
            f"tortoise/record_writer.py defines no {_LIVE_DOOR}() — the live "
            "door moved, and this check no longer knows where the position "
            "rule lives"
        ]
    problems: list[str] = []
    calls = [
        node for node in ast.walk(door)
        if isinstance(node, ast.Call) and _call_name(node) in _CALL_NAMES
    ]
    if not calls:
        return [
            f"{_LIVE_DOOR}() calls no seam entry point — nothing is written "
            "through the record module"
        ]
    for call in calls:
        bound = {kw.arg: kw.value for kw in call.keywords if kw.arg}
        value = bound.get("position")
        if not (isinstance(value, ast.Constant) and value.value is False):
            problems.append(
                f"{_LIVE_DOOR}() calls {_call_name(call)}(…) with "
                f"position={ast.unparse(value) if value is not None else '<unset>'}"
                " — the live path has no journal ordinal (#9190), so it must "
                "pass the literal `position=False`"
            )
    return problems


def check() -> tuple[int, dict[str, object]]:
    """The whole gate, memoised — it reads the tree and imports the fold, and a
    test file asks it several times (the import dominates the cost)."""
    callers, parse_problems = find_production_callers()
    writers, fold_problems = layer_writers()

    gaps = [
        f"{layer} layer has NO writer: none of {list(tables)} is written by "
        "any record type in RECORD_MUTATIONS"
        for layer, tables in LAYERS.items()
        if not any(writers.get(table) for table in tables)
    ]
    position_problems = live_door_defers_the_position()

    report: dict[str, object] = {
        "production_callers": callers,
        "layer_writers": {t: sorted(set(types)) for t, types in writers.items()},
        "gaps": gaps,
        "parse_problems": parse_problems,
        "fold_problems": fold_problems,
        "position_problems": position_problems,
    }

    if parse_problems or fold_problems:
        report["verdict"] = "CANNOT ANSWER"
        return 2, report
    if not callers:
        report["verdict"] = "NOT WIRED"
        report["reason"] = (
            "no module inside tortoise/ imports AND calls the record's write "
            "half — the record has no production call site (#9078)"
        )
        return 1, report
    if gaps or position_problems:
        report["verdict"] = "NOT WIRED"
        return 1, report
    report["verdict"] = "WIRED"
    return 0, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    args = parser.parse_args(argv)
    code, report = check()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return code

    callers = report["production_callers"]
    writers = report["layer_writers"]
    print(f"record wiring — {report['verdict']}")
    for key in ("parse_problems", "fold_problems"):
        for problem in report[key]:
            print(f"  ✗ unreadable: {problem}")
    if callers:
        for caller in callers:
            print(f"  ✓ caller: {caller['module']} → {', '.join(caller['calls'])}")
    else:
        print("  ✗ caller: none — no module in tortoise/ calls the write half")
    for layer, tables in LAYERS.items():
        written = [table for table in tables if writers.get(table)]
        if written:
            detail = ", ".join(
                f"{table} ({len(writers[table])} record types)" for table in written
            )
            print(f"  ✓ {layer:12s}: {detail}")
        else:
            print(f"  ✗ {layer:12s}: no writer for any of {list(tables)}")
    for problem in report["position_problems"]:
        print(f"  ✗ position: {problem}")
    if report.get("reason"):
        print(f"  reason: {report['reason']}")
    return code


if __name__ == "__main__":
    sys.exit(main())

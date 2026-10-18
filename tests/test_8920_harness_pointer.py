"""#8920 review P3 — a renamed harness must keep every pointer to it resolvable.

THE DEFECT THIS PINS
--------------------
PR #8920 moved the `TestClient` background-work quiesce OUT of
`tests/test_export_delete.py` into `tests/_http_fixtures.py` and dropped its
leading underscore (`_quiesce_testclient_background_work` →
`quiesce_testclient_background_work`; `_quiet_boot_sweeps` →
`quiet_boot_sweeps`). The DEFINITIONS moved and were renamed. The REFERENCES
were not, so five of them named symbols that no longer existed anywhere.

The one that matters is production source. `tortoise/hosted_api.py` records
this helper as THE harness pattern for the #3472 duplicate-purge overlap — it
is the stated reason the helper is a canonical seam rather than a per-file
copy — and it still read
`tests/test_export_delete._quiesce_testclient_background_work`. A lane
following that pointer found nothing. This is the "relay the answer to the
artifact the next lane reads" discipline, in code: a record that names a
deleted symbol is not a record.

WHAT THIS PINS
--------------
1. Every pointer from a production root (`tortoise/`, `tools/`,
   `graph-scripts/`, `eval/`) into the test tree RESOLVES. Both idioms are
   covered, because both break on a rename and the node-id form is the COMMON
   one — `tests/<path>.py::node` (110 occurrences at the time of writing) as
   well as `tests.<module>.<symbol>`. Resolution reads the target FILE's AST;
   it never imports the module, because 289 test files live in directories with
   no `__init__.py` (`tests/e2e`, `tests/eval`, `tests/fixtures`, …) and
   importing one would execute its collection-time guards — a false RED on the
   most common correct pointer. A long node id is routinely WRAPPED mid-name in
   prose, so a definition matching the token as a PREFIX resolves it (a rename
   that changes the leading word still reddens); a data path
   (`tests/fixtures/x.jsonl`) is not a module and is skipped.
2. No SOURCE file still names a symbol a rename left behind — a stale name in a
   comment or docstring is exactly what made (1) un-followable, and prose is
   not reachable by any other guard. A line may opt out deliberately with
   `# stale-name-ok: <reason>`, so a documented rename can be narrated.
   Scope is source (`.py`); `docs/**` is NOT scanned — see KNOWN OUTSTANDING.
3. The seam is SHARED, not re-copied: the helper is DEFINED exactly once
   (`tests/_http_fixtures.py`, sync or async — an `async def` copy is the same
   defect) and each consumer CALLS it. Both via AST, not substring.

NON-VACUITY, measured: on the pre-fix head (`f0d5144`) check 1 reddens on the
`hosted_api.py` pointer (`tests.test_export_delete` has no attribute
`_quiesce_testclient_background_work`) and check 2 reddens on five stale
`_quiesce…`/`_quiet…` occurrences across `tortoise/hosted_api.py`,
`tests/test_export_delete.py` and `tests/_http_fixtures.py`.

Building this guard found THREE stale production pointers it also corrects here,
all comment-only: the `hosted_api.py` harness record itself, a
`tests/test_operator_alert.py::test_kind_constants_match_the_runbook` pointer in
`tortoise/incident_kinds.py` whose test was deleted by #4780, and two
illustrative examples marked `# pointer-ok:` (`tools/skip-guard.py`,
`tools/ci_timing.py`) which are output formats, not records.

KNOWN OUTSTANDING (deliberately not fixed here): five files under `docs/` still
name `_purge_deleted_teams`, a symbol #3543/#3622 renamed to
`_purge_deleted_orgs` — a DIFFERENT rename, tracked on its own issue rather than
smuggled into a #8920 guard.

Static: no network, no DB, no graph.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = ROOT / "tests"

#: Roots whose prose points into the test tree. `tortoise/` is the one the
#: #8920 defect landed in; the others are scanned because the same idiom lives
#: there (`graph-scripts/graph-diagnostics.py` already points at
#: `tests/epic903_fixtures.f5_diagnostics`) and a stale pointer is equally
#: un-followable wherever it sits.
PRODUCTION_ROOTS = tuple(
    root for root in (ROOT / name for name in ("tortoise", "tools", "graph-scripts", "eval"))
    if root.is_dir()
)

#: `tests.<module>.<...>.<symbol>` — the dotted idiom.
_DOTTED = re.compile(r"(?<![\w.\/])tests\.(?P<parts>[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+)")
#: `tests/<path>.py::<node>` (the common idiom), `tests/<path>.py` (a whole
#: file — skipped), and `tests/<path>.<symbol>` (slash form WITHOUT `.py`, which
#: is how production source spells the protected pointer: the host-api record
#: reads `tests/_http_fixtures.quiesce_testclient_background_work`).
_PATH = re.compile(
    # A suffix is REQUIRED. Leaving it optional let the lazy path quantifier
    # match a single character (`tests/_`) and report a pointer that is not one.
    r"(?<![\w\/])tests/(?P<path>[A-Za-z0-9_\/]+?)"
    r"(?:\.py::(?P<node>[A-Za-z0-9_.\[\]\-]+)"
    r"|\.(?P<legacy>[A-Za-z_][A-Za-z0-9_]*))"
)

#: A `tests/<path>` pointer with no symbol and no node — a whole-file reference.
_SKIP_EXTENSIONS = frozenset({"py", "json", "yml", "yaml", "md", "txt", "toml"})

#: A trailing token that is a file EXTENSION, not a symbol — i.e. the pointer
#: names a whole file, which does not break on a rename of a symbol inside it.
_NOT_A_SYMBOL = frozenset({"py", "json", "yml", "yaml", "md", "txt", "toml"})

#: Names a rename left behind. `#8920` moved and renamed the two helpers; the
#: callee spelling `_purge_deleted_teams` was killed earlier by #3543/#3622
#: (tenant → org) and is kept here because the moved docstring re-introduced it
#: despite belonging to the seam this PR owns.
STALE_NAMES = (
    "_quiesce_testclient_background_work",
    "_quiet_boot_sweeps",
    "_purge_deleted_teams",
)
STALE_OPT_OUT = "# stale-name-ok:"
#: A line may opt out of the POINTER check with `# pointer-ok: <reason>` — for a
#: pointer that is illustrative rather than a record (a sample pytest line, an
#: example of the failure format) and therefore never intended to resolve.
POINTER_OPT_OUT = "# pointer-ok:"

SHARED_MODULE = "_http_fixtures"
HELPER = "quiesce_testclient_background_work"
#: The pointer whose loss made this guard necessary: production source recording
#: the canonical seam. Checked SPECIFICALLY, not merely as "some pointer exists",
#: because `assert pointers` was satisfied by an unrelated pointer
#: (`tests.fake_control_plane.FakeControlPlane` in `tortoise/backup_sweep.py`) —
#: so deleting the protected sentence left the guard green.
PROTECTED_POINTER_FILE = "hosted_api.py"
PROTECTED_POINTER_SUBSTRING = f"tests/{SHARED_MODULE}.{HELPER}"

#: Every fixture module that opens `TestClient(app)` on a shared store and
#: therefore needs the quiesce. `test_supabase_control.py` /
#: `test_writer_inventory.py` drive the app without the boot sweep reaching
#: their fake, so they are not consumers of this seam.
CONSUMERS = ("test_export_delete.py", "test_pack_state.py")

#: The node-id idiom was 73 occurrences when written. A floor, not an identity:
#: the count is main's business, but if it collapses to a handful this guard has
#: stopped covering the idiom it was extended for.
_MIN_NODE_POINTERS = 10


def _defined_names(path: Path) -> set[str]:
    """Every name DEFINED in `path`, read from its AST (never imported).

    Covers functions, classes, class members (so `Class.method` resolves), and
    module/module-attribute assignment targets. Over-inclusive on purpose: this
    answers "did a rename orphan this pointer", and a name that exists in some
    scope is not orphaned by a symbol rename.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            if isinstance(node.target, ast.Name):
                names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
    return names


def _resolve_dotted(parts: list[str]) -> tuple[Path | None, str, str]:
    """`tests.a.b.symbol` → (file, symbol, error). Longest module path wins."""
    for cut in range(len(parts) - 1, 0, -1):
        candidate = TESTS_DIR / Path(*parts[:cut]).with_suffix(".py")
        if candidate.is_file():
            return candidate, ".".join(parts[cut:]), ""
    return None, "", f"no module file for tests.{'.'.join(parts)}"


def _pointer_hits(text: str) -> list[tuple[str, Path | None, str]]:
    """(token, target file or None, symbol-or-node) for one source file.

    Line-addressed so `# pointer-ok:` can exempt a single illustrative mention.
    """
    hits: list[tuple[str, Path | None, str]] = []
    for line in text.splitlines():
        if POINTER_OPT_OUT in line:
            continue
        for match in _DOTTED.finditer(line):
            parts = match.group("parts").split(".")
            if parts[-1] in _NOT_A_SYMBOL:
                continue
            path, symbol, err = _resolve_dotted(parts)
            hits.append((match.group(0), path, symbol if path is not None else err))
        for match in _PATH.finditer(line):
            base = match.group("path")
            is_node_id = match.group("node") is not None
            node = match.group("node") or match.group("legacy")
            if node is not None:
                node = node.rstrip(".")
            if node is None or node in _SKIP_EXTENSIONS:
                continue  # a whole-file pointer: no symbol to orphan
            # The file may be spelled with or without `.py`; resolve either.
            candidates = (TESTS_DIR / f"{base}.py", ROOT / f"{base}.py")
            target = next((c for c in candidates if c.is_file()), None)
            if target is None and not is_node_id:
                # `tests/fixtures/labeled_pairs.jsonl` and friends: a DATA path
                # whose extension parses as a "symbol". Not a module, so there is
                # no symbol to orphan — resolving it would be a false RED.
                continue
            hits.append((match.group(0), target, node))
    return hits


def _production_pointers() -> list[tuple[Path, str, Path | None, str]]:
    found: list[tuple[Path, str, Path | None, str]] = []
    for root in PRODUCTION_ROOTS:
        for path in sorted(root.rglob("*.py")):
            for token, target, symbol in _pointer_hits(path.read_text(encoding="utf-8")):
                found.append((path, token, target, symbol))
    return found


def test_production_pointers_into_the_test_tree_resolve():
    pointers = _production_pointers()

    # Non-vacuity, SPECIFIC: the pointer this guard exists for must be among
    # them, spelled as it is spelled in production source.
    assert any(
        token.strip("`") == PROTECTED_POINTER_SUBSTRING and path.name == PROTECTED_POINTER_FILE
        for path, token, _target, _symbol in pointers
    ), (
        f"{PROTECTED_POINTER_FILE} must still record `{PROTECTED_POINTER_SUBSTRING}` as the "
        "canonical harness pattern (#3472/#3505) — without it this guard protects nothing"
    )
    node_seen = sum(1 for _p, token, _t, _s in pointers if "::" in token)
    assert node_seen >= _MIN_NODE_POINTERS, (
        f"only {node_seen} `tests/<path>.py::node` pointer(s) seen (floor {_MIN_NODE_POINTERS}) — "
        "the node-id arm has stopped covering the common idiom"
    )

    cache: dict[Path, set[str]] = {}
    broken: list[str] = []
    for path, token, target, symbol in pointers:
        if target is None:
            broken.append(f"{path.relative_to(ROOT)}: {token} → {symbol}")
            continue
        if target not in cache:
            cache[target] = _defined_names(target)
        # `test_x[...]` parametrization suffix is not part of the name.
        wanted = symbol.split("[")[0]
        leaves = [part for part in wanted.split(".") if part]
        # PREFIX, not equality: a long node id is routinely WRAPPED mid-name in
        # prose (`::test_explicit_dir_keeps_an_` continues on the next line), and
        # an equality check calls every one of those a rename. A rename that
        # changes the leading word still reddens.
        if not leaves or any(
            not any(defined.startswith(leaf) for defined in cache[target])
            for leaf in leaves
        ):
            broken.append(
                f"{path.relative_to(ROOT)}: {token} → "
                f"{target.relative_to(ROOT)} defines no {wanted!r} (rename without a relay?)"
            )
    assert not broken, (
        "production source points at test symbols that do not resolve:\n  " + "\n  ".join(broken)
    )


def test_no_source_still_names_a_symbol_a_rename_left_behind():
    # This guard must SPELL the names it forbids, so it is the one file exempt
    # from its own scan; every other module — and the production tree — is
    # checked. A line may opt out with `# stale-name-ok: <reason>`.
    itself = Path(__file__).name
    hits: list[str] = []
    for root in (TESTS_DIR, *PRODUCTION_ROOTS):
        for path in sorted(root.rglob("*.py")):
            if path.name == itself:
                continue
            text = path.read_text(encoding="utf-8")
            if not any(name in text for name in STALE_NAMES):
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                if STALE_OPT_OUT in line:
                    continue
                for name in STALE_NAMES:
                    if name in line:
                        hits.append(f"{path.relative_to(ROOT)}:{lineno}: {name}")
    assert not hits, (
        "a comment or docstring still names a symbol a rename left behind, so it "
        "points a reader at nothing (add `# stale-name-ok: <reason>` when the "
        "mention is deliberate narration):\n  " + "\n  ".join(hits)
    )


def _definitions(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == HELPER
    ]


def _calls_helper(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == HELPER:
            return True
        if isinstance(func, ast.Attribute) and func.attr == HELPER:
            return True
    return False


def test_the_seam_is_defined_once_and_called_by_its_consumers():
    definers = [
        (path.name, len(found))
        for path in sorted(TESTS_DIR.rglob("*.py"))
        if (found := _definitions(path))
    ]
    assert definers == [(f"{SHARED_MODULE}.py", 1)], (
        f"{HELPER} must be DEFINED exactly once (tests/{SHARED_MODULE}.py) — sync or "
        f"async, since an async copy is the same defect; found "
        f"{[f'{name} x{count}' for name, count in definers]}. A per-consumer copy "
        "stops being updated when `_lifespan` grows a new background caller — the "
        "#3472/#3505 defect."
    )

    missing = [
        consumer
        for consumer in CONSUMERS
        if not _calls_helper(TESTS_DIR / consumer)
    ]
    assert not missing, (
        "these fixtures must CALL the shared quiesce (a comment mentioning it "
        "does not quiesce anything):\n  " + "\n  ".join(missing)
    )

#!/usr/bin/env python3
"""#3554 — the ``:Session`` writer inventory (re-runnable) + the D9 retrofit audit.

Two verbs, one instrument, because both answer the same question — *who writes
a* ``:Session`` *node, and what does the graph hold because of them*:

``inventory``
    Re-runs the RECORDED search predicate over the TRACKED tree — minus the
    path prefixes :data:`PREDICATE` declares ``excluded`` (``tests/``,
    ``docs/``) and the third-party/cache directory names it declares in
    ``skip_parts`` — and diffs the sites it finds against the declared
    inventory in ``tortoise/session_schema.py``. The corpus is ``git ls-files``
    (tracked, reviewable scope), not "whatever is on disk": a linked worktree
    under ``.worktrees/<lane>/`` is a full second copy of the source tree and
    would otherwise report every writer at a second, undeclared path. The
    scope is the predicate, not a private allow-list in this tool, so a
    ``:Session`` writer under ANY other tracked directory is a site the
    inventory must declare. The completeness claim is a predicate, never a
    number: run this on every rebase instead of trusting the table. Exit 1 on
    any undeclared site (a new writer) or any declared site the predicate no
    longer finds (a stale row).

    Two halves, per #3554's Target:

    * **graph writers** — Python string literals (AST nodes, so comments are
      excluded by construction) that mention ``:Session`` *and* carry a write
      verb against the Session alias (``MERGE (a:Session`` / ``SET a.``).
      A DOCSTRING is an AST ``Constant`` too, so it is excluded by identity:
      ``seed_capture_turn_store`` quotes the Session MERGE shape in its
      docstring without writing it, and the declaration module's ``note=``
      strings quote sites rather than being sites — neither is a writer;
    * **HTTP clients** — call-shaped POSTs to the DECLARED route
      (``PREDICATE['client_route']``, compiled at scan time) in TS / shell /
      Python entrypoints (prose mentions of the route are NOT sites: the
      matcher requires a call expression). A handler DECORATOR
      (``@app.post("/v1/sessions")``) is the route's DEFINITION, not a caller,
      and the route must be the full terminal segment — ``/v1/sessions/commit``
      and ``/v1/sessions/install-probe`` are different routes.

``retrofit`` (D9 — the audit #3554 owns)
    Quantifies, per graph, the three divergences the epic pushed down:

    * **(a) short turn count under** ``capture_ok = true`` — stored
      ``s.turn_count`` vs the Session's live episodic turn members;
    * **(b)** the pre- vs post-verification-read ``capture_ok`` ordering —
      measured as an UPPER BOUND, because the computation lane is not stored
      (the verdict and the reason are in ``session_schema.RETROFIT_VERDICTS``);
    * **(c)** ``entity_links_created`` under-count — the recorded counter vs
      the live ``aboutObject`` edge count, the drift the per-capture
      (``_capture_session_impl`` / ``capture_session``) and per-index-run
      (``_relink_sessions_after_index``) writers produce.

    A graph URI is never invented: pass ``--db`` (embedded) or ``--uri`` with
    ``TORTOISE_DB_URI`` set, exactly like ``tools/subject_binding_audit.py``.
    Without either, the verb REFUSES TO RUN — ``SystemExit``, non-zero, nothing
    printed on stdout — rather than printing a zero for a graph nobody read.

Usage:
    uv run python tools/session_retrofit_audit.py inventory [--root .]
    uv run python tools/session_retrofit_audit.py retrofit --db /tmp/memory.db
    uv run python tools/session_retrofit_audit.py retrofit --uri --namespace my-graph
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/session_retrofit_audit.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/session_retrofit_audit.py`"
    )

import argparse
import ast
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.session_schema import (
    PREDICATE,
    RETROFIT_VERDICTS,
    SESSION_CLIENTS,
    SESSION_WRITERS,
    audit_session_retrofit,
)

#: The declaration module itself. Every string in it is a declaration ABOUT a
#: site — a ``note=`` quoting a writer's Cypher, a ``marker=`` quoting a
#: client's call — never a site. The module is not a ``:Session`` writer and
#: cannot be its own client, so scanning it could only manufacture false
#: positives the inventory can never clear.
DECLARATION_MODULE = "tortoise/session_schema.py"
PY_EXTENSIONS = (".py",)
CLIENT_EXTENSIONS = (".py", ".ts", ".tsx", ".js", ".sh", ".bash")

#: A string literal carries the Session alias for the write-verb test.
_SESSION_ALIAS_RE = re.compile(r"\(\s*(\w+)\s*:\s*Session\s*[{)$\s]")
_SESSION_PROP_RE = re.compile(
    r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s*=(?!=)")

#: An HTTP POST to the sessions route — a CALL, never a prose mention. The
#: ROUTE is compiled from :data:`PREDICATE`'s ``client_route`` at scan time (see
#: :func:`_client_matchers`), so editing the declaration changes what is
#: matched and a private edit here cannot widen the scan. Each alternative is a
#: call shape that appears in the tree today; a new transport the matcher cannot
#: see is exactly what the inventory diff is for (add the shape AND the site
#: together, in one reviewed diff).


def _client_matchers() -> tuple[re.Pattern[str], ...]:
    """Compile the DECLARED client route into the known call shapes.

    The route comes from :data:`PREDICATE`'s ``client_route`` — the same
    binding contract as :func:`_write_matchers` for ``write_verbs`` — so the
    declaration, not a private literal in this tool, decides what a client site
    is. The route is anchored to its FULL terminal segment (``(?![-/\\w])``):
    ``/v1/sessions/commit`` and ``/v1/sessions/install-probe`` are DIFFERENT
    routes served by different handlers, so a caller of one is not a capture
    client — a bare substring test turns the commit endpoint's ``_post_commit``
    into an undeclared "client".
    """
    route = re.escape(PREDICATE["client_route"]) + r"(?![-/\w])"
    return (
        # TS/JS: post(cfg, "/v1/sessions", ...) / fetch("…/v1/sessions", …)
        re.compile(rf"""\bpost\s*\([^)]*["'`][^"'`]*{route}"""),
        re.compile(rf"""\bfetch\s*\([^)]*["'`][^"'`]*{route}"""),
        # Python: requests.post(...) / urlopen(Request("…/v1/sessions"))
        re.compile(rf"""\.post\s*\([^)]*["'`f][^"'`]*{route}"""),
        re.compile(rf"""Request\s*\([^)]*["'`f][^"'`]*{route}"""),
        # curl with an explicit POST
        re.compile(rf"""curl\b[^\n]*(-X\s*POST|--request\s+POST)[^\n]*{route}"""),
        re.compile(rf"""curl\b[^\n]*{route}[^\n]*(-X\s*POST|--request\s+POST)"""),
    )


@dataclass
class Site:
    """One predicate hit: where, who owns it, and the properties it writes."""
    kind: str                     # "graph-writer" | "http-client"
    path: str                     # repo-relative
    owner: str                    # enclosing function / module-level name
    line: int
    properties: tuple[str, ...] = field(default_factory=tuple)

    @property
    def key(self) -> str:
        return f"{self.path}::{self.owner}"

    @property
    def cid(self) -> str:
        return f"{self.path}:{self.line}"

    def __str__(self) -> str:
        props = ",".join(self.properties) or "-"
        return f"{self.kind} {self.cid} owner={self.owner} props=[{props}]"


def _owner_of(scopes: tuple[tuple[int, int, str, int], ...], lineno: int) -> str:
    """The narrowest declaring scope: the enclosing FUNCTION, else the
    module-level name an assignment binds the literal to, else ``<module>``.

    A function always beats an assignment, because a module-level constant
    carries statements that would otherwise be attributed to whatever local
    binding happens to span the line: ``_TURN_WRITE_CYPHER`` (the ONE turn
    store), ``BACKFILL_STATEMENTS`` (the R-18 migration). Naming those is what
    makes the rows reviewable rather than "somewhere in a 24k-line module".

    ``scopes`` is :attr:`_AstIndex.scopes` — built in the ONE walk, so this is
    a scan of the file's few hundred scopes, not another whole-tree walk.
    """
    func: tuple[int, str] | None = None
    assign: tuple[int, str] | None = None
    for start, end, name, kind in scopes:
        if not (start <= lineno <= end):
            continue
        span = end - start
        if kind == 0:               # function — beats any assignment
            if func is None or span < func[0]:
                func = (span, name)
        elif assign is None or span < assign[0]:
            assign = (span, name)
    if func is not None:
        return func[1]
    return assign[1] if assign is not None else "<module>"


class _AstIndex(NamedTuple):
    """Everything the two halves need, built in ONE ``ast.walk`` of a file.

    Three separate walks (docstrings, then every literal, then one more per
    candidate for its owning scope) made ``inventory`` take the better part of
    a minute on this tree — and the inventory test runs it, so that cost is a
    gate rather than an inconvenience. One pass builds all of it.

    * ``docstrings`` — the ``id()`` of every DOCSTRING ``Constant``. A
      docstring is an AST ``Constant``, so the "comments are excluded by
      construction" framing does not cover it; it DESCRIBES a write shape
      (``seed_capture_turn_store`` quotes the Session MERGE) without being one.
      Excluded by identity, so a sibling literal on the same statement stays;
    * ``scopes`` — ``(start, end, name, kind)`` for every function (kind 0) and
      every assignment (kind 1), consumed by :func:`_owner_of`;
    * ``literals`` — every ``str`` ``Constant``, in ``ast.walk`` order;
    * ``calls`` — every ``Call``, in ``ast.walk`` order;
    * ``decorators`` — the ``id()`` of every ``Call`` inside a DECORATOR.
      ``@app.post("/v1/sessions")`` is the route's DEFINITION — the server
      that serves it — not a caller of it.
    """
    docstrings: frozenset[int]
    scopes: tuple[tuple[int, int, str, int], ...]
    literals: tuple[ast.Constant, ...]
    calls: tuple[ast.Call, ...]
    decorators: frozenset[int]


def _ast_index(tree: ast.AST) -> _AstIndex:
    docstrings: set[int] = set()
    scopes: list[tuple[int, int, str, int]] = []
    literals: list[ast.Constant] = []
    calls: list[ast.Call] = []
    decorators: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef,
                             ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", ())
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scopes.append(
                    (node.lineno, node.end_lineno or node.lineno, node.name, 0))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            end = node.end_lineno or node.lineno
            targets = (node.targets if isinstance(node, ast.Assign)
                       else [node.target])
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if names:
                scopes.append((node.lineno, end, names[0], 1))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.append(node)
        if isinstance(node, ast.Call):
            calls.append(node)
        for dec in getattr(node, "decorator_list", ()) or ():
            decorators.update(id(sub) for sub in ast.walk(dec)
                              if isinstance(sub, ast.Call))
    return _AstIndex(frozenset(docstrings), tuple(scopes), tuple(literals),
                     tuple(calls), frozenset(decorators))


def _excluded(rel: str) -> bool:
    """True when a repo-relative path sits under a DECLARED excluded prefix.

    The prefixes come from :data:`PREDICATE`'s ``excluded`` (the declaration),
    never from a literal in this tool — so the scan's scope is part of the same
    reviewed predicate as its verbs and route.
    """
    return any(rel == prefix.rstrip("/") or rel.startswith(prefix)
               for prefix in PREDICATE["excluded"])


def _write_matchers(alias: str) -> tuple[re.Pattern[str], ...]:
    """Compile the DECLARED write verbs with ``<alias>`` bound.

    The verbs come from :data:`PREDICATE`'s ``write_verbs``: a widened
    predicate is a reviewed diff to ``tortoise/session_schema.py``, not a
    private edit here. Until this was read, the guarantee the declaration
    stated ("the tool cannot silently widen its own predicate") did not hold —
    the scanner hardcoded ``MERGE``/``SET`` while claiming to key on the dict.
    """
    return tuple(re.compile(verb.replace("<alias>", re.escape(alias)))
                 for verb in PREDICATE["write_verbs"])


def _skip_parts() -> frozenset[str]:
    """The DECLARED directory-name parts the walk never descends into.

    Read from :data:`PREDICATE`'s ``skip_parts`` (the declaration), never from
    a literal in this tool — so the scan's scope is part of the same reviewed
    predicate as its verbs and route. This is the ONLY thing that can narrow
    the walk besides the declared ``excluded`` prefixes.
    """
    return frozenset(PREDICATE["skip_parts"])


def _tracked_files(root: Path) -> frozenset[str] | None:
    """The TRACKED repo-relative paths under ``root``, or ``None`` if not a repo.

    The scan corpus is tracked, reviewable scope — not "whatever is on disk".
    A linked worktree under ``.worktrees/<lane>/`` is a full copy of the source
    tree, so every writer inside it appears at a second, undeclared path
    (``.worktrees/<lane>/tortoise/sdk.py::…``) that by construction is not in
    :data:`SESSION_WRITERS`; on the repo's own hub checkout (189 lanes) the
    walk reports INVENTORY DRIFT on a tree whose tracked content is clean.
    Intersecting the corpus with ``git ls-files`` — the pattern
    ``tests/test_entry_point_python_guard.py`` uses for a whole-tree scope —
    keeps the fail-closed property for every tracked directory while excluding
    parallel checkouts and any untracked scratch tree, neither of which can be
    a merged writer (#3554 review).

    ``None`` (``root`` is not a git work tree, e.g. a synthetic test root)
    makes :func:`_iter_source` fall back to the filesystem walk.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--"],
            capture_output=True, check=False)
    except OSError:                                    # git not on PATH
        return None
    if proc.returncode != 0:                           # not a work tree
        return None
    return frozenset(
        rel for rel in
        proc.stdout.decode("utf-8", "surrogateescape").split("\0") if rel)


def _is_declaration_module(path: Path) -> bool:
    """True for the declaration module, independent of the scan ``root``.

    The module's repo-relative path is fixed, but a scan may be rooted
    anywhere (``--root tortoise``), so compare the path's trailing parts rather
    than ``path.relative_to(root)``: a root-relative literal matched only when
    the root WAS the repo root, so a scan from any other root reported the
    declaration's own ``note=``/``marker=`` strings as sites (#3554 review).
    """
    parts = Path(DECLARATION_MODULE).parts
    return path.parts[-len(parts):] == parts


def _iter_source(root: Path, extensions: tuple[str, ...]):
    """Walk the TRACKED tree minus what ``PREDICATE`` declares out of scope.

    The scope is the predicate, not a private allow-list: an allow-list let a
    ``:Session`` writer under any unlisted tracked directory stay invisible, so
    ``inventory`` printed INVENTORY CLEAN with an undeclared writer present —
    the fail-open direction of a guard whose whole purpose is to be trusted
    (#3554 review). The corpus is intersected with ``git ls-files`` (see
    :func:`_tracked_files`) so parallel checkouts and untracked scratch trees —
    which can never be a merged writer — are not sites; only the declared
    ``excluded`` path prefixes and ``skip_parts`` directory names are then
    skipped.

    ``os.walk`` with PRUNED ``dirnames`` rather than ``Path.rglob``: rglob
    descends into ``.venv``/``.git``/``node_modules`` and filters afterwards,
    which costs ~25s on this tree; pruning never opens them. A root that is not
    a git work tree (a synthetic test root) falls back to that walk with the
    same declared pruning.
    """
    tracked = _tracked_files(root)
    if tracked is not None:
        for rel in sorted(tracked):
            if Path(rel).suffix not in extensions or _excluded(rel):
                continue
            path = root / rel
            if _is_declaration_module(path):
                continue
            if not path.is_file() or path.is_symlink():
                continue
            yield path
        return
    skip_parts = _skip_parts()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in skip_parts)
        top = Path(dirpath)
        rel_dir = top.relative_to(root).as_posix()
        if rel_dir != "." and _excluded(rel_dir + "/"):
            dirnames[:] = []          # an excluded subtree is not walked
            continue
        for name in sorted(filenames):
            if Path(name).suffix not in extensions:
                continue
            path = top / name
            if path.is_symlink():
                continue
            if _is_declaration_module(path):
                continue
            yield path


def scan_graph_writers(root: Path) -> list[Site]:
    """Half 1 — every Python literal that WRITES a ``:Session`` node."""
    sites: list[Site] = []
    for path in _iter_source(root, PY_EXTENSIONS):
        try:
            raw = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        # The site is a LITERAL containing ``:Session``, so a file whose text
        # never mentions the label cannot hold one. Without this prefilter the
        # scan ``ast.parse``s every ``.py`` under the roots (hundreds of files)
        # on every run — the dominant cost of a scan the inventory test runs.
        if PREDICATE["label"] not in raw:
            continue
        try:
            tree = ast.parse(raw)
        except SyntaxError:
            continue
        index = _ast_index(tree)
        for node in index.literals:
            if id(node) in index.docstrings:
                continue
            text = node.value
            if ":Session" not in text:
                continue
            match = _SESSION_ALIAS_RE.search(text)
            if not match:
                continue
            alias = match.group(1)
            # The write test reads the DECLARED verbs (PREDICATE), so widening
            # the predicate is a reviewed diff to the declaration rather than a
            # hardcoded regex here.
            writes = any(rx.search(text) for rx in _write_matchers(alias))
            # ``SET a += $props`` (the rebuild_all snapshot restore) carries no
            # property NAME at the site — it is the one writer whose property
            # set is the whole schema, and it is declared as such.
            if not writes:
                continue
            props = tuple(sorted({
                prop for owner_alias, prop in _SESSION_PROP_RE.findall(text)
                if owner_alias == alias}))
            sites.append(Site(
                kind="graph-writer", path=str(path.relative_to(root)),
                owner=_owner_of(index.scopes, node.lineno), line=node.lineno,
                properties=props))
    return sorted(sites, key=lambda s: (s.path, s.owner, s.line))


def scan_http_clients(root: Path) -> list[Site]:
    """Half 2 — every FILE carrying a POST-to-``/v1/sessions`` CALL.

    File-level on purpose: a line number is not a stable identity (a comment
    above the call moves it), and the declaration's job is to make a new
    TRANSPORT a reviewed diff. The per-file :class:`SessionClient.marker`
    pins the call site inside the file, so a row cannot survive the call
    being deleted — the marker assertion is in the test, and the file-level
    diff here catches a file nobody declared.
    """
    sites: list[Site] = []
    matchers = _client_matchers()
    for path in _iter_source(root, CLIENT_EXTENSIONS):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if PREDICATE["client_route"] not in text:
            continue
        line = 0
        comment = ""
        if path.suffix == ".py":
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            index = _ast_index(tree)
            # ``ast.get_source_segment`` re-splits the WHOLE file on every
            # call, so using it in this loop is O(calls × file length) — on
            # ``tortoise/sdk.py`` alone that is minutes, for a scan the
            # inventory test runs. Slice the pre-split lines instead: each
            # call costs only its own span.
            lines = text.splitlines(keepends=True)
            for node in index.calls:
                if id(node) in index.decorators:
                    continue
                if node.end_lineno is None:
                    continue
                src = "".join(lines[node.lineno - 1:node.end_lineno])
                if any(rx.search(src) for rx in matchers):
                    line = node.lineno
                    comment = _owner_of(index.scopes, node.lineno)
                    break
        else:
            for idx, raw in enumerate(text.splitlines(), start=1):
                if any(rx.search(raw) for rx in matchers):
                    line = idx
                    comment = raw.strip()[:60]
                    break
        if not line:
            continue
        sites.append(Site(
            kind="http-client", path=str(path.relative_to(root)),
            owner=comment, line=line))
    return sorted(sites, key=lambda s: s.path)


def inventory_report(root: Path, *, writers: list[Site] | None = None,
                    clients: list[Site] | None = None) -> dict:
    """The predicate's verdict: declared vs found, both directions.

    ``writers``/``clients`` let a caller that already scanned (the test
    module's module-scoped fixture) reuse the sites instead of paying for a
    second full-tree walk. Omitted, each half is scanned here as before.
    """
    writers = scan_graph_writers(root) if writers is None else writers
    clients = scan_http_clients(root) if clients is None else clients
    writers_expected = {w.key for w in SESSION_WRITERS}
    declared_clients_expected = {c.path for c in SESSION_CLIENTS
                                 if c.transport == "http"}
    found_writers = {s.key for s in writers}
    found_clients = {c.path for c in clients}
    return {
        "graph_writers_found": sorted(found_writers),
        "graph_writers_declared": sorted(writers_expected),
        "graph_writers_undeclared": sorted(found_writers - writers_expected),
        "graph_writers_stale": sorted(writers_expected - found_writers),
        "http_clients_found": sorted(found_clients),
        "http_clients_declared": sorted(declared_clients_expected),
        "http_clients_undeclared": sorted(found_clients - declared_clients_expected),
        "http_clients_stale": sorted(declared_clients_expected - found_clients),
        "ok": not (found_writers - writers_expected
                   or writers_expected - found_writers
                   or found_clients - declared_clients_expected
                   or declared_clients_expected - found_clients),
    }


def _cmd_inventory(args) -> int:
    report = inventory_report(Path(args.root).resolve())
    if args.json:
        # Machine-readable means the payload is the ONLY thing on stdout. The
        # verdict is `report["ok"]` (and the process exit code); appending the
        # human verdict line here is what made `--json` unparseable.
        print(json.dumps(report, indent=2))
    else:
        for row in SESSION_WRITERS:
            print(f"  {row.key}\n      kind={row.kind} "
                  f"props={sorted(row.properties) or '(whole schema)'}")
        for row in SESSION_CLIENTS:
            print(f"  {row.path}\n      transport={row.transport} "
                  f"lane={row.lane}")
        for bucket in ("graph_writers_undeclared", "graph_writers_stale",
                       "http_clients_undeclared", "http_clients_stale"):
            for key in report[bucket]:
                print(f"  ⛔ {bucket}: {key}")
        print(f"  graph writers: {len(report['graph_writers_found'])} found / "
              f"{len(report['graph_writers_declared'])} declared")
        print(f"  http clients:  {len(report['http_clients_found'])} found / "
              f"{len(report['http_clients_declared'])} declared")
        print("INVENTORY CLEAN" if report["ok"] else "INVENTORY DRIFT")
    return 0 if report["ok"] else 1


def _cmd_retrofit(args) -> int:
    from tortoise.sdk import TortoiseSDK

    if args.uri:
        if not os.environ.get("TORTOISE_DB_URI"):
            raise SystemExit(
                "--uri requires TORTOISE_DB_URI to be set in the environment")
        sdk = TortoiseSDK(namespace=args.namespace or None)
    elif args.db:
        sdk = TortoiseSDK(args.db)
    else:
        raise SystemExit(
            "refusing to invent a graph: pass --db <path> or --uri "
            "(with TORTOISE_DB_URI set). A zero printed for a graph nobody "
            "read is a lie with a number on it.")
    try:
        report = audit_session_retrofit(sdk._get_proj().g,
                                        graph=args.namespace or args.db)
    finally:
        sdk.close()
    report["verdicts"] = RETROFIT_VERDICTS
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"graph: {report['graph']}")
        print(f"  sessions total          : {report['sessions']}")
        print(f"  (a) capture_ok=true, short turn count : "
              f"{report['short_turn_count']['capture_ok_true']} "
              f"(of {report['short_turn_count']['candidates_measured']} measured)")
        print(f"  (b) capture_ok=true (ordering-ambiguous upper bound): "
              f"{report['ordering_ambiguous']['sessions']} — "
              f"retro_quantifiable="
              f"{report['ordering_ambiguous']['retro_quantifiable']}")
        print(f"  (c) entity_links_created under-count : "
              f"{report['entity_links_under_count']['sessions']} "
              f"(under-counted edges: "
              f"{report['entity_links_under_count']['edges']})")
        for name, verdict in RETROFIT_VERDICTS.items():
            print(f"  verdict[{name}] = {verdict['decision']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="verb", required=True)

    inv = sub.add_parser("inventory", help="re-run the writer predicate")
    inv.add_argument("--root", default=".")
    inv.add_argument("--json", action="store_true")
    inv.set_defaults(func=_cmd_inventory)

    ret = sub.add_parser("retrofit", help="D9 per-graph retrofit audit")
    ret.add_argument("--db", help="embedded SDK graph path")
    ret.add_argument("--uri", action="store_true",
                     help="connect via TORTOISE_DB_URI")
    ret.add_argument("--namespace", default=None)
    ret.add_argument("--json", action="store_true")
    ret.set_defaults(func=_cmd_retrofit)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

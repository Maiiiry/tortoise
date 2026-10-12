#!/usr/bin/env python3
"""#3554 — the ``:Session`` writer inventory (re-runnable) + the D9 retrofit audit.

Two verbs, one instrument, because both answer the same question — *who writes
a* ``:Session`` *node, and what does the graph hold because of them*:

``inventory``
    Re-runs the RECORDED search predicate over the tree and diffs the sites it
    finds against the declared inventory in ``tortoise/session_schema.py``.
    The completeness claim is a predicate, never a number: run this on every
    rebase instead of trusting the table. Exit 1 on any undeclared site (a new
    writer) or any declared site the predicate no longer finds (a stale row).

    Two halves, per #3554's Target:

    * **graph writers** — Python string literals (AST nodes, so comments are
      excluded by construction) that mention ``:Session`` *and* carry a write
      verb against the Session alias (``MERGE (a:Session`` / ``SET a.``);
    * **HTTP clients** — call-shaped POSTs to ``/v1/sessions`` in TS / shell /
      Python entrypoints (prose mentions of the route are NOT sites: the
      matcher requires a call expression).

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
    Without either, the tool reports the classes as UNMEASURED rather than
    printing a zero that reads like a clean graph.

Usage:
    uv run python tools/session_retrofit_audit.py inventory [--root .]
    uv run python tools/session_retrofit_audit.py retrofit --db /tmp/memory.db
    uv run python tools/session_retrofit_audit.py retrofit --uri --namespace my-graph
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.session_schema import (  # noqa: E402
    PREDICATE,
    RETROFIT_VERDICTS,
    SESSION_CLIENTS,
    SESSION_WRITERS,
    audit_session_retrofit,
)

#: The predicate's declared scan scope. A directory here is walked for the
#: file extensions its half needs; ``tests/``/``docs/`` are excluded (the
#: predicate is a source scan, and a test fixture or a doc table is not a
#: writer) and ``.venv``/``node_modules`` are third-party.
SCAN_ROOTS: tuple[str, ...] = (
    "tortoise", "tools", "graph-scripts", "battery", "benchmarks", "spike",
    "capability", "integrations", "services", "validation", ".github/scripts",
)
SCAN_SKIP_PARTS: frozenset[str] = frozenset(
    {".venv", "node_modules", "tests", "__pycache__", "bakeoff"})
PY_EXTENSIONS = (".py",)
CLIENT_EXTENSIONS = (".py", ".ts", ".tsx", ".js", ".sh", ".bash")

#: A string literal carries the Session alias for the write-verb test.
_SESSION_ALIAS_RE = re.compile(r"\(\s*(\w+)\s*:\s*Session\s*[{)$\s]")
_SESSION_PROP_RE = re.compile(
    r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s*=(?!=)")

#: An HTTP POST to the sessions route — a CALL, never a prose mention. Each
#: alternative is a shape that appears in the tree today; a new transport the
#: matcher cannot see is exactly what the inventory diff is for (add the shape
#: and the site together, in one reviewed diff).
_CLIENT_CALL_RES = (
    # TS/JS: post(cfg, "/v1/sessions", ...) / fetch("…/v1/sessions", {…POST…})
    re.compile(r"""\bpost\s*\([^)]*["'`][^"'`]*/v1/sessions["'`]"""),
    re.compile(r"""\bfetch\s*\([^)]*["'`][^"'`]*/v1/sessions["'`]"""),
    # Python: requests.post(...) / urlopen(Request("…/v1/sessions"))
    re.compile(r"""\.post\s*\([^)]*["'`f][^"'`]*/v1/sessions"""),
    re.compile(r"""Request\s*\([^)]*["'`f][^"'`]*/v1/sessions"""),
    # curl with an explicit POST
    re.compile(r"""curl\b[^\n]*(-X\s*POST|--request\s+POST)[^\n]*/v1/sessions"""),
    re.compile(r"""curl\b[^\n]*/v1/sessions[^\n]*(-X\s*POST|--request\s+POST)"""),
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


def _owner_of(tree: ast.AST, lineno: int) -> str:
    """The narrowest declaring scope: the enclosing FUNCTION, else the
    module-level name an assignment binds the literal to, else ``<module>``.

    A function always beats an assignment, because a module-level constant
    carries statements that would otherwise be attributed to whatever local
    binding happens to span the line: ``_TURN_WRITE_CYPHER`` (the ONE turn
    store), ``BACKFILL_STATEMENTS`` (the R-18 migration). Naming those is what
    makes the rows reviewable rather than "somewhere in a 24k-line module".
    """
    func: tuple[int, str] | None = None
    assign: tuple[int, str] | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = node.end_lineno or node.lineno
            if node.lineno <= lineno <= end:
                span = end - node.lineno
                if func is None or span < func[0]:
                    func = (span, node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            end = node.end_lineno or node.lineno
            if node.lineno <= lineno <= end:
                targets = (node.targets if isinstance(node, ast.Assign)
                           else [node.target])
                names = [t.id for t in targets if isinstance(t, ast.Name)]
                if names:
                    span = end - node.lineno
                    if assign is None or span < assign[0]:
                        assign = (span, names[0])
    if func is not None:
        return func[1]
    return assign[1] if assign is not None else "<module>"


def _iter_source(root: Path, extensions: tuple[str, ...]):
    for rel_root in SCAN_ROOTS:
        base = root / rel_root
        if not base.exists() or base.is_symlink():
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix not in extensions or not path.is_file():
                continue
            if any(part in SCAN_SKIP_PARTS for part in path.parts):
                continue
            if path.is_symlink():
                continue
            yield path


def scan_graph_writers(root: Path) -> list[Site]:
    """Half 1 — every Python literal that WRITES a ``:Session`` node."""
    sites: list[Site] = []
    for path in _iter_source(root, PY_EXTENSIONS):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            text = node.value
            if ":Session" not in text:
                continue
            match = _SESSION_ALIAS_RE.search(text)
            if not match:
                continue
            alias = match.group(1)
            writes = bool(re.search(
                rf"MERGE\s*\(\s*{re.escape(alias)}\s*:\s*Session", text))
            writes = writes or bool(re.search(
                rf"\bSET\s+{re.escape(alias)}\.", text))
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
                owner=_owner_of(tree, node.lineno), line=node.lineno,
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
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                src = ast.get_source_segment(text, node) or ""
                if any(rx.search(src) for rx in _CLIENT_CALL_RES):
                    line = node.lineno
                    comment = _owner_of(tree, node.lineno)
                    break
        else:
            for idx, raw in enumerate(text.splitlines(), start=1):
                if any(rx.search(raw) for rx in _CLIENT_CALL_RES):
                    line = idx
                    comment = raw.strip()[:60]
                    break
        if not line:
            continue
        sites.append(Site(
            kind="http-client", path=str(path.relative_to(root)),
            owner=comment, line=line))
    return sorted(sites, key=lambda s: s.path)


def inventory_report(root: Path) -> dict:
    """The predicate's verdict: declared vs found, both directions."""
    writers = scan_graph_writers(root)
    clients = scan_http_clients(root)
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
              f"{report['short_turn_count']['sessions']} "
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

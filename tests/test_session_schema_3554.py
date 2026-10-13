"""#3554 — the ``:Session`` write-schema declaration and its inventory predicate.

``tortoise/session_schema.py`` names this file as the enforcement of its own
claim ("``tools/session_retrofit_audit.py inventory`` re-runs the recorded
search predicate over the tree and diffs what it finds against
``SESSION_WRITERS`` / ``SESSION_CLIENTS`` … and
``tests/test_session_schema_3554.py`` fails on it"). Two halves, both guarded
here:

* the **inventory** half — the predicate must be CLEAN on the tree it ships
  with. The three false-positive mechanisms that made its clean verdict
  UNREACHABLE are pinned individually, not only through the aggregate: the
  declaration module quoting its own sites, a docstring quoting a write shape,
  and an HTTP-side handler DECORATOR / sibling route counted as a client;
* the **retrofit** half — ``audit_session_retrofit``'s report shape and the CLI
  that prints it, including the metric key the CLI reads (a key that did not
  exist once, and crashed the verb before it printed anything).

The full-tree scan is genuinely expensive (hundreds of files), so it runs ONCE
in a module-scoped fixture and every aggregate assertion shares it. No graph
and no live SDK: ``tortoise/session_schema.py`` is deliberately dependency-free,
so the retrofit CLI is exercised against a fake graph handle injected through a
fake ``tortoise.sdk`` module rather than a real one.
"""
from __future__ import annotations

import ast
import json
import re
import sys
import types
from pathlib import Path

import pytest

from tools import session_retrofit_audit as audit
from tortoise import session_schema

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def repo_sites() -> tuple[list, list]:
    """ONE full scan of the real tree; the scan is the cost, not the assert."""
    return audit.scan_graph_writers(REPO_ROOT), audit.scan_http_clients(REPO_ROOT)


@pytest.fixture(scope="module")
def repo_inventory(repo_sites) -> dict:
    """The report, built from the ONE scan above rather than a second walk."""
    writers, clients = repo_sites
    return audit.inventory_report(REPO_ROOT, writers=writers, clients=clients)


# ── the inventory half ─────────────────────────────────────────────────────


def test_inventory_is_clean_on_this_tree(repo_inventory: dict) -> None:
    """The predicate and the declaration must agree, in BOTH directions."""
    assert repo_inventory["ok"], (
        "the :Session writer/client predicate drifted from the declaration:\n"
        f"  undeclared writers: {repo_inventory['graph_writers_undeclared']}\n"
        f"  stale writers:      {repo_inventory['graph_writers_stale']}\n"
        f"  undeclared clients: {repo_inventory['http_clients_undeclared']}\n"
        f"  stale clients:      {repo_inventory['http_clients_stale']}\n"
        "Declare the new site (or drop the stale row) in "
        "tortoise/session_schema.py in the same reviewed diff.")


def test_declaration_module_is_not_its_own_site(repo_inventory: dict) -> None:
    """``tortoise/session_schema.py`` DECLARES sites; it never is one.

    Its ``note=`` strings quote a writer's Cypher and its ``marker=`` strings
    quote a client's call. Scanning the file reports the declaration as an
    undeclared writer/client the inventory can never clear.
    """
    assert (REPO_ROOT / audit.DECLARATION_MODULE).exists()
    assert ("tortoise/session_schema.py::SESSION_WRITERS"
            not in repo_inventory["graph_writers_found"])
    assert "tortoise/session_schema.py" not in repo_inventory["http_clients_found"]


def test_ask_fixture_declares_the_writer_not_its_docstring(
        repo_inventory: dict) -> None:
    """The ask fixture's declared row is the function that WRITES."""
    found = set(repo_inventory["graph_writers_found"])
    assert "tools/ask_spotcheck.py::merge_capture_session" in found
    assert "tools/ask_spotcheck.py::seed_capture_turn_store" not in found


def test_declared_http_client_markers_are_present() -> None:
    """A declared row cannot survive its call site being deleted silently."""
    for client in session_schema.SESSION_CLIENTS:
        text = (REPO_ROOT / client.path).read_text(encoding="utf-8")
        assert client.marker in text, (client.path, client.marker)


def test_declared_clients_name_every_shipped_hook_recorder() -> None:
    """Every shipped hook recorder is a declared client.

    ``SESSION_CLIENTS``' non-http rows exist so the inventory answers "who can
    create a session?", not only "who runs the Cypher?" — but
    ``inventory_report`` diffs only the ``transport == "http"`` rows, so a
    missing ``cli`` row is invisible to every other guard. Pre-fix two of the
    four shipped recorders were absent (Claude and Codex; only pi and cursor
    were declared).
    """
    declared = {c.path for c in session_schema.SESSION_CLIENTS}
    for path in ("tortoise/pi-hooks/tortoise-capture.ts",
                 "tortoise/claude-hooks/session-end.sh",
                 "tortoise/codex-hooks/session-end.sh",
                 "tortoise/cursor-hooks/session-end.sh"):
        assert path in declared, path


def test_scanner_scope_is_the_declared_predicate(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The walk covers the WHOLE tree minus the DECLARED exclusions.

    Pre-fix the scope was a private 11-directory allow-list inside the tool
    while every statement of the predicate described it as "the tree minus
    tests/ and docs/": a writer under any unlisted tracked directory
    (``website/``, ``apps/``, ``operations/``) was invisible, so ``inventory``
    printed INVENTORY CLEAN with an undeclared writer present — the fail-open
    direction of a guard whose whole purpose is to be trusted. This pins both
    halves of the declared scope: the default walk reaches a directory outside
    the old allow-list, and each declared field drives the walk. Pre-fix the
    first assertion fails (the allow-list does not contain ``website/``).
    """
    pkg = tmp_path / "website" / "src"
    pkg.mkdir(parents=True)
    (pkg / "writer.py").write_text(
        'QUERY = "MERGE (s:Session {id:$sid}) SET s.turn_count = 1"\n',
        encoding="utf-8")
    assert {s.key for s in audit.scan_graph_writers(tmp_path)} == {
        "website/src/writer.py::QUERY"}
    # ``excluded`` decides the path prefixes...
    monkeypatch.setitem(audit.PREDICATE, "excluded", ("website/",))
    assert audit.scan_graph_writers(tmp_path) == []
    monkeypatch.setitem(audit.PREDICATE, "excluded", ())
    # ...and ``skip_parts`` decides the directory names.
    monkeypatch.setitem(audit.PREDICATE, "skip_parts", ("src",))
    assert audit.scan_graph_writers(tmp_path) == []


#: The declared writers whose property names the scanner CANNOT read from the
#: source literal: each builds its ``SET`` clause with an f-string (a shared
#: helper or a ``for prop in (...)`` loop), so no ``s.<prop> =`` name sits in
#: the literal. Their property sets are hand-declared. ``rebuild_all`` is
#: excluded separately — it is the whole-schema writer (``SET s += $props``)
#: and is declared as such.
_FSTRING_WRITERS: frozenset[str] = frozenset({
    "tools/ask_spotcheck.py::merge_capture_session",
    "tortoise/projection/entities.py::_fold_session_recorded",
    "tortoise/sdk.py::_write_session_and_turns",
})


def test_scanner_properties_match_the_declaration(repo_sites) -> None:
    """Every writer whose literal carries property names declares exactly them.

    ``inventory_report`` diffs writer KEYS only, so a declared property set that
    disagrees with the properties its site writes is invisible to the inventory
    — the drift the §4.8 owner column and this declaration exist to answer.
    The exemption for the f-string writers is itself pinned: a new writer cannot
    join the unreadable set unnoticed.
    """
    writers, _ = repo_sites
    scanned: dict[str, set[str]] = {}
    for site in writers:
        scanned.setdefault(site.key, set()).update(site.properties)
    mismatched: dict[str, dict[str, list[str]]] = {}
    for writer in session_schema.SESSION_WRITERS:
        if writer.writes_whole_schema or not scanned.get(writer.key):
            continue
        if scanned[writer.key] != set(writer.properties):
            mismatched[writer.key] = {
                "declared_only": sorted(set(writer.properties)
                                        - scanned[writer.key]),
                "site_only": sorted(scanned[writer.key]
                                    - set(writer.properties)),
            }
    assert not mismatched, mismatched
    unreadable = {
        w.key for w in session_schema.SESSION_WRITERS
        if not w.writes_whole_schema and not scanned.get(w.key)}
    assert unreadable == _FSTRING_WRITERS, (
        "only the declared f-string writers may have no literal property "
        f"names; got {sorted(unreadable)}")


def test_ast_index_excludes_only_the_docstring_constant() -> None:
    """A docstring is an AST ``Constant``; it is excluded BY IDENTITY."""
    tree = ast.parse(
        'def seed(sdk):\n'
        '    """MERGE (s:Session {id:$sid}) SET s.turn_count = 1."""\n'
        '    QUERY = "MERGE (s:Session {id:$sid}) SET s.turn_count = 1"\n'
        '    return QUERY\n')
    index = audit._ast_index(tree)
    assert len(index.literals) == 2
    doc_literals = [n for n in index.literals if id(n) in index.docstrings]
    kept = [n for n in index.literals if id(n) not in index.docstrings]
    assert len(doc_literals) == 1
    assert doc_literals[0].value.startswith("MERGE")
    assert len(kept) == 1                  # the sibling literal survives


def test_declaration_module_path_is_skipped(tmp_path: Path) -> None:
    """``session_schema.py`` is excluded from BOTH halves by path.

    Its ``note=`` is a writer-shaped literal and its ``SessionClient(...)``
    call carries a client-shaped ``marker=`` — the two self-matches that made
    the real tree's inventory permanently red.
    """
    pkg = tmp_path / "tortoise"
    pkg.mkdir()
    (pkg / "session_schema.py").write_text(
        'NOTE = "MATCH (s:Session) WHERE s.is_episodic IS NULL '
        'SET s.is_episodic = true"\n'
        'ROWS = SessionClient(marker=\'post(cfg, "/v1/sessions", payload\')\n',
        encoding="utf-8")
    assert audit.scan_graph_writers(tmp_path) == []
    assert audit.scan_http_clients(tmp_path) == []


def test_docstring_mention_is_not_a_writer_site(tmp_path: Path) -> None:
    """A docstring that QUOTES a write shape is not a writer."""
    pkg = tmp_path / "tortoise"
    pkg.mkdir()
    (pkg / "seeder.py").write_text(
        'def seed(sdk):\n'
        '    """MERGE (s:Session {id:$sid}) SET s.turn_count = 1."""\n'
        '    raise NotImplementedError\n',
        encoding="utf-8")
    assert audit.scan_graph_writers(tmp_path) == []
    # …and the exclusion is not over-broad: the same literal in real code IS a
    # site, so the test above cannot pass by weakening the predicate entirely.
    (pkg / "real_writer.py").write_text(
        'QUERY = "MERGE (s:Session {id:$sid}) SET s.turn_count = 1"\n',
        encoding="utf-8")
    assert {s.key for s in audit.scan_graph_writers(tmp_path)} == {
        "tortoise/real_writer.py::QUERY"}


def test_route_definition_and_sibling_routes_are_not_client_sites(
        tmp_path: Path) -> None:
    """Only a CALL to the capture route is a client; its definition is not.

    ``@app.post("/v1/sessions")`` is the server that serves the route, and
    ``/v1/sessions/commit`` is a DIFFERENT route. Counting either as a capture
    client is the false positive that made the inventory permanently red.
    """
    pkg = tmp_path / "tortoise"
    pkg.mkdir()
    (pkg / "capture_client.py").write_text(
        'import requests\n'
        'def post_capture(base):\n'
        '    return requests.post(f"{base}/v1/sessions", json={})\n',
        encoding="utf-8")
    (pkg / "commit_client.py").write_text(
        'import requests\n'
        'def post_commit(base):\n'
        '    return requests.post(f"{base}/v1/sessions/commit", json={})\n',
        encoding="utf-8")
    (pkg / "route_server.py").write_text(
        'app = None\n'
        '@app.post("/v1/sessions")\n'
        'def handler():\n'
        '    return None\n',
        encoding="utf-8")
    assert {s.path for s in audit.scan_http_clients(tmp_path)} == {
        "tortoise/capture_client.py"}


def test_inventory_cli_reports_clean_and_exits_zero(
        repo_inventory: dict, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """The CLI's verdict/exit mapping, without a second full scan."""
    monkeypatch.setattr(audit, "inventory_report", lambda root: repo_inventory)
    rc = audit.main(["inventory"])
    assert rc == 0
    assert capsys.readouterr().out.rstrip().endswith("INVENTORY CLEAN")


def test_inventory_json_stdout_is_only_json(tmp_path: Path, capsys) -> None:
    """``--json`` is the machine-readable form: the payload and nothing else.

    An empty root matches no declaration, so the verdict is DRIFT — but the
    point is the parse: pre-fix the appended verdict line broke ``json.loads``.
    """
    rc = audit.main(["inventory", "--root", str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["ok"] is False


# ── the retrofit half ──────────────────────────────────────────────────────


class _FakeResult:
    def __init__(self, rows: list) -> None:
        self.result_set = rows


class _FakeGraph:
    """The five reads ``audit_session_retrofit`` issues, answered from fixtures.

    Two short sessions under ``capture_ok``, only ONE of them ``capture_ok``
    true — so ``capture_ok_true`` (1) and ``all_short`` (2) differ and a test
    can tell which key the CLI printed.

    The (c) branch is DELIBERATELY universe-sensitive: it reports an
    under-count only when the query reaches the turn points through
    ``CONTAINS``, because the recorded counter sums the Session-origin AND the
    turn-Point-origin edges (``link_session_entities``). A Session-origin-only
    query would understate the gap — and can print a clean zero for a real one.
    """

    def query(self, cypher: str, params: dict | None = None) -> _FakeResult:
        if "stored_turns < s.turn_count" in cypher:
            return _FakeResult([
                ("s-short-ok", 5, 2, True, "llm", "claude", "hosted"),
                ("s-short-fail", 5, 2, False, "llm", "claude", "sdk"),
            ])
        if "live_edges > s.entity_links_created" in cypher:
            if "CONTAINS" not in cypher:
                return _FakeResult([])
            return _FakeResult([("s-links", 1, 3, 4)])
        if "s.turn_count IS NOT NULL RETURN count(s)" in cypher:
            return _FakeResult([[2]])
        if "s.capture_ok = true RETURN count(s)" in cypher:
            return _FakeResult([[1]])
        if "MATCH (s:Session) RETURN count(s)" in cypher:
            return _FakeResult([[2]])
        raise AssertionError(f"unexpected query: {cypher}")


class _FakeSDK:
    def __init__(self, *args, **kwargs) -> None:
        self.graph = _FakeGraph()
        self.closed = False

    def _get_proj(self) -> types.SimpleNamespace:
        return types.SimpleNamespace(g=self.graph)

    def close(self) -> None:
        self.closed = True


@pytest.fixture()
def fake_sdk_module(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """A fake ``tortoise.sdk`` — the schema module stays dependency-free."""
    module = types.ModuleType("tortoise.sdk")
    module.TortoiseSDK = _FakeSDK          # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tortoise.sdk", module)
    return module


def test_retrofit_verb_prints_the_capture_ok_true_short_count(
        fake_sdk_module: types.ModuleType, capsys) -> None:
    """The ``retrofit`` verb must print a metric, not raise ``KeyError``."""
    rc = audit.main(["retrofit", "--db", "fake-graph.db"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "capture_ok=true, short turn count : 1" in out
    assert "of 2 measured" in out


def test_retrofit_json_is_valid_json(
        fake_sdk_module: types.ModuleType, capsys) -> None:
    rc = audit.main(["retrofit", "--db", "fake-graph.db", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["short_turn_count"]["capture_ok_true"] == 1
    assert payload["short_turn_count"]["all_short"] == 2
    assert payload["entity_links_under_count"]["sessions"] == 1
    assert payload["verdicts"] == session_schema.RETROFIT_VERDICTS


def test_retrofit_without_a_graph_refuses_instead_of_printing_a_zero(
        fake_sdk_module: types.ModuleType, capsys) -> None:
    """No graph → ``SystemExit`` and NOTHING on stdout.

    The module docstring and the plan amendment used to state that without a
    graph "the tool reports the classes as UNMEASURED", a path that does not
    exist: the verb refuses to invent a graph. This pins the actual behaviour
    (the sentence's intent) so the docs and the code cannot drift apart again —
    and so a later refactor cannot quietly start printing a zero for a graph
    nobody read.
    """
    with pytest.raises(SystemExit) as exc:
        audit.main(["retrofit"])
    assert "--db" in str(exc.value)
    assert capsys.readouterr().out == ""
    # The claim and the code agree: neither home still describes an UNMEASURED
    # *report*. Pre-fix both did, and no code path could produce one.
    assert "UNMEASURED" not in (audit.__doc__ or "")
    assert "UNMEASURED" not in PLAN.read_text(encoding="utf-8")


class _RecordingGraph(_FakeGraph):
    def __init__(self) -> None:
        self.queries: list[str] = []

    def query(self, cypher: str, params: dict | None = None) -> _FakeResult:
        self.queries.append(cypher)
        return super().query(cypher, params)

    def undercount_query(self) -> str:
        return next(q for q in self.queries
                    if "live_edges > s.entity_links_created" in q)


def test_undercount_counts_the_same_edge_universe_as_the_counter() -> None:
    """(c) must count Session-origin AND turn-Point-origin ``aboutObject`` edges.

    The recorded counter is ``link_session_entities``' ``created``, which sums
    BOTH ``_link(proj, "Session", …)`` and ``_link(proj, "Point", tid, …)``.
    A Session-origin-only count understates the gap and can print a clean ZERO
    for a real one: ``0 > 3`` is false, so a session holding 3 turn-point edges
    against a counter of 1 reads clean. Pre-fix this test fails — the fake
    graph answers the (c) query with an under-count only when it reaches the
    turn points through ``CONTAINS``.
    """
    report = session_schema.audit_session_retrofit(_RecordingGraph())
    assert report["entity_links_under_count"]["sessions"] == 1
    assert report["entity_links_under_count"]["edges"] == 2


def test_undercount_query_aggregates_the_two_legs_separately() -> None:
    """The two ``OPTIONAL MATCH``es must not cross-product their counts.

    Counting both legs in ONE row scope multiplies them
    (``session_edges x turn_edges``) and over-reports the gap — the
    mirror-image defect of the under-count. Each leg is aggregated in its own
    ``WITH`` stage before the sum.

    The assertions are structural, not substring presence: a single-scope
    cross-product query still CONTAINS ``count(r1) AS session_edges`` and
    ``count(r2) AS turn_edges``, so the pre-fix substrings-only test passed on
    the very shape it names. Here the session leg's aggregation must close
    before the turn leg's ``OPTIONAL MATCH`` opens.
    """
    graph = _RecordingGraph()
    session_schema.audit_session_retrofit(graph)
    q = graph.undercount_query()
    assert "CONTAINS" in q
    assert "pointKind = 'event'" in q and "is_episodic = true" in q
    assert "count(r1) AS session_edges" in q
    assert "count(r2) AS turn_edges" in q
    assert "count(r1) + count(r2)" not in q
    # The stage boundary: ``count(r1) AS session_edges`` must be emitted by a
    # WITH that precedes the turn leg's ``OPTIONAL MATCH (s)-[:CONTAINS]``.
    # In the single-scope cross-product the count lands AFTER that match, so
    # this fails on the shape the test exists to catch.
    leg1_aggregated = q.index("count(r1) AS session_edges")
    leg2_opens = q.index("OPTIONAL MATCH (s)-[:CONTAINS]")
    assert leg1_aggregated < leg2_opens, (
        "the session-edge count must be aggregated in its own WITH before the "
        "turn leg opens; counting both legs in one scope cross-products them "
        "and over-reports the gap")
    # ...and the aggregation is introduced by a WITH clause, not appended to
    # the first match's row scope.
    assert "WITH s, count(r1) AS session_edges" in q


# ── the recorded decisions must have a real home ───────────────────────────

ONTOLOGY = REPO_ROOT / "docs/ONTOLOGY.md"
PLAN = REPO_ROOT / "docs/plans/2026-08-25-1714-memory-capture-onboarding.md"


def _session_section() -> str:
    """The §4.8 body, with the §5 heading as its exclusive end."""
    ontology = ONTOLOGY.read_text(encoding="utf-8")
    start = ontology.index("### §4.8")
    return ontology[start:ontology.index("## §5.", start)]


def test_retrofit_verdicts_cite_documentation_homes_that_exist() -> None:
    """A ``recorded-honest-gap`` decision is only recorded if its home exists.

    The module's framing is that these three divergences are *recorded*
    decisions; pre-fix every ``where_recorded`` pointed at
    ``docs/ONTOLOGY.md §4.8`` and a plan ``#3554 amendment``, neither of which
    existed.
    """
    ontology = ONTOLOGY.read_text(encoding="utf-8")
    plan = PLAN.read_text(encoding="utf-8")
    assert "### §4.8" in ontology
    assert "#3554 amendment" in plan
    for name, verdict in session_schema.RETROFIT_VERDICTS.items():
        where = verdict["where_recorded"]
        assert "docs/ONTOLOGY.md §4.8" in where, name
        assert "§ '#3554 amendment'" in where, name


def test_section_4_8_changelog_entry_sits_under_its_own_version_block() -> None:
    """The §4.8 changelog bullet belongs to its own version/date/issue header.

    Pre-fix the bullet was the FIRST item of the ``v3.23 (2026-10-10 —
    issues #7813 + #7853)`` block, making that header's date and issue
    enumeration false and sending a reader looking up "when was §4.8 added"
    to the wrong version. The document is v3.24 (2026-10-12), so the entry
    sits under the v3.24 header.
    """
    ontology = ONTOLOGY.read_text(encoding="utf-8")
    bullet = ontology.index("§4.8 (added by #3554")
    header = ontology.rindex("> **Changelog v3.", 0, bullet)
    header_line = ontology[header:ontology.index("\n", header)]
    assert "v3.24" in header_line, header_line


def test_ontology_section_4_8_owner_lists_match_the_declaration() -> None:
    """The doc mirror must name every property AND its exact owner set.

    The section claims the owners mirror ``SESSION_PROPERTY_OWNERS``; the
    pre-fix test asserted only that each property NAME appeared, so a row could
    lose an owner (or the declaration could gain one) with the table still
    green. Pre-fix this test fails on the ``created_at`` and ``capture_lane``
    rows: ``_fold_session_recorded`` writes both (``s.created_at=coalesce(…)``,
    ``s.capture_lane=coalesce(…)``) and the declaration omitted them.
    """
    section = _session_section()
    row_re = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*(.*?)\s*\|\s*$", re.M)
    rows: dict[str, set[str]] = {}
    for prop, owners in row_re.findall(section):
        if prop in session_schema.SESSION_PROPERTIES:
            rows[prop] = {o.strip().strip("`")
                          for o in owners.split(",") if o.strip()}
    assert set(rows) == set(session_schema.SESSION_PROPERTIES), (
        "§4.8 must name every declared property, and no other")
    mismatched: dict[str, dict[str, list[str]]] = {}
    for prop, owners in session_schema.SESSION_PROPERTY_OWNERS.items():
        expected = {key.split("::")[1] for key in owners}
        if rows[prop] != expected:
            mismatched[prop] = {
                "doc_only": sorted(rows[prop] - expected),
                "declaration_only": sorted(expected - rows[prop]),
            }
    assert not mismatched, mismatched


def test_fold_session_recorded_declares_every_property_it_writes() -> None:
    """The fold's declared row must name every property its own Cypher writes.

    The fold builds ``sets`` with an f-string, so ``tools/session_retrofit_audit.py
    `` reads no property names at its site (it is one of the declared f-string
    writers). This reads the function's source instead. Pre-fix the row omitted
    ``created_at`` and ``capture_lane``, which the fold writes on every
    journaled rebuild (both coalesce-preserved, like ``actor_user_id``).
    """
    src = (REPO_ROOT / "tortoise/projection/entities.py").read_text(
        encoding="utf-8")
    fn = next(node for node in ast.walk(ast.parse(src))
              if isinstance(node, ast.FunctionDef)
              and node.name == "_fold_session_recorded")
    set_re = re.compile(r"\bs\.([A-Za-z_]\w*)\s*=(?!=)")
    written: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            written.update(set_re.findall(node.value))
        elif isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple):
            # ``for prop in ("turn_count", …): sets.append(f"s.{prop}=$v_{prop}")``
            # — the property names live in the loop's tuple, not the literal.
            for elt in node.iter.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    written.add(elt.value)
    declared = next(
        w.properties for w in session_schema.SESSION_WRITERS
        if w.key == "tortoise/projection/entities.py::_fold_session_recorded")
    assert written == set(declared), (
        "declaration disagrees with the fold: "
        f"declared-only={sorted(set(declared) - written)} "
        f"code-only={sorted(written - set(declared))}")


def test_scanner_write_verbs_come_from_the_declared_predicate(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The writer test reads ``PREDICATE['write_verbs']``, not a literal.

    Pre-fix the scanner hardcoded ``MERGE``/``SET`` while the declaration
    claimed the tool "cannot silently widen its own predicate": editing the
    declared verbs changed nothing. Here a ``MERGE`` file and an ``UPSERT``
    file are scanned with an UPSERT-only predicate — the declared binding
    admits only the UPSERT site, and the hardcoded pair would also admit the
    MERGE one (both carry ``SET s.``).
    """
    pkg = tmp_path / "tortoise"
    pkg.mkdir()
    (pkg / "merge_writer.py").write_text(
        'QUERY = "MERGE (s:Session {id:$sid}) SET s.turn_count = 1"\n',
        encoding="utf-8")
    (pkg / "upsert_writer.py").write_text(
        'QUERY = "UPSERT (s:Session {id:$sid}) SET s.turn_count = 1"\n',
        encoding="utf-8")
    monkeypatch.setitem(audit.PREDICATE, "write_verbs",
                        (r"UPSERT\s*\(\s*<alias>\s*:\s*Session",))
    assert {s.key for s in audit.scan_graph_writers(tmp_path)} == {
        "tortoise/upsert_writer.py::QUERY"}


def test_scanner_client_matchers_come_from_the_declared_route(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The client matcher reads ``PREDICATE['client_route']``, not a literal.

    Pre-fix the call matchers were compiled at import from a hardcoded
    ``/v1/sessions``: editing the declared route changed only the text
    prefilter, so the declared clients read as STALE while the matchers kept
    scanning the old route. Here the declaration moves to ``/v9/sessions`` and
    only the v9 caller may be found.
    """
    pkg = tmp_path / "tortoise"
    pkg.mkdir()
    (pkg / "v9_client.py").write_text(
        'import requests\n'
        'def post_v9(base):\n'
        '    return requests.post(f"{base}/v9/sessions", json={})\n',
        encoding="utf-8")
    (pkg / "v1_client.py").write_text(
        'import requests\n'
        'def post_v1(base):\n'
        '    return requests.post(f"{base}/v1/sessions", json={})\n',
        encoding="utf-8")
    monkeypatch.setitem(audit.PREDICATE, "client_route", "/v9/sessions")
    assert {s.path for s in audit.scan_http_clients(tmp_path)} == {
        "tortoise/v9_client.py"}


def test_scanner_skips_the_declared_excluded_prefixes(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The walk reads ``PREDICATE['excluded']``, not a private constant."""
    pkg = tmp_path / "tortoise" / "vendored"
    pkg.mkdir(parents=True)
    (pkg / "writer.py").write_text(
        'QUERY = "MERGE (s:Session {id:$sid}) SET s.turn_count = 1"\n',
        encoding="utf-8")
    assert {s.path for s in audit.scan_graph_writers(tmp_path)} == {
        "tortoise/vendored/writer.py"}
    monkeypatch.setitem(audit.PREDICATE, "excluded", ("tortoise/vendored/",))
    assert audit.scan_graph_writers(tmp_path) == []


# ── the demo session id has ONE derivation ─────────────────────────────────


def test_demo_session_id_is_the_one_derivation() -> None:
    assert session_schema.DEMO_SESSION_ID_PREFIX == "session_demo_"
    assert session_schema.demo_session_id("abcdef123456") == \
        "session_demo_abcdef12"


def test_seed_demo_graph_derives_its_session_id_from_the_declaration() -> None:
    """#3554: the seeder calls ``demo_session_id``, not a local f-string.

    Pre-fix the function was dead code (never called anywhere) and the seeder
    re-spelled ``f"session_demo_{org_id[:8]}"`` — the "one derivation" the
    module claimed did not exist.
    """
    src = (REPO_ROOT / "tortoise/hosted_api.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(node for node in ast.walk(tree)
              if isinstance(node, ast.FunctionDef)
              and node.name == "_seed_demo_graph")
    body = ast.get_source_segment(src, fn) or ""
    assert "demo_session_id(org_id)" in body
    assert "session_demo_" not in body

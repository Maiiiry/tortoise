"""#9079 — §17.1's statement door, one hop BELOW the write handle.

WHY THIS FILE EXISTS. `#9124` put §17.1's authority check on the projection's
write handle (``_GuardedGraph``, i.e. ``proj.g``) and `tortoise/projection``'s
own SCOPE block listed the two escapes it left open, one hop below that object:

   2. ``proj.g._g.query("CREATE …")``        — the handle's own inner handle;
   3. ``proj.db.select_graph(<g>).query(…)`` — the client, naming ANY graph.

Both bypass `_GuardedGraph` BY CONSTRUCTION, so no verb-level guard on that
object can see them. MEASURED on ``origin/main`` at ``02699a4fd`` with
``_HANDLE_GUARD_ENFORCES = True`` on a journal-backed projection:

    WROTE    escape 2: proj.g._g.query CREATE
    WROTE    escape 3: proj.db.select_graph().query CREATE
    WROTE    escape 3: ...execute_command GRAPH.QUERY
    WROTE    escape 3: ...profile CREATE
    landed: ['e2', 'e3', 'e4', 'e6']

Four writes reached an ARMED cache with no journal record and no refusal. That
is exactly the second writer §17.1 forbids — the thing #9079's objective names —
reached through paths the issue's own "Research Needed" item 1 requires the fix
to enumerate: *"the SDK registry handles built through ``guarded_client``. The
fix is only correct if the enumeration is complete"*.

WHAT CLOSES THEM. The signal moves one hop DOWN, onto the handles
``tortoise.cypher_guard.guarded_client`` already wraps: every guarded Cypher
entry point consults the projection's authority hook, so the decision no longer
depends on WHICH handle a writer reached. `cypher_guard` carries the call (it
cannot import the projection — the dependency runs the other way) and the
projection installs the decision, using the SAME classifier
(``_statement_writes``) and the SAME two modes as the write handle, so the doors
cannot drift (the #3595 shared-decision precedent).

TWO INDEPENDENT NESTINGS, and each is pinned below because each was a measured
defect before it was closed:
  * the write handle's own dispatch must not re-take the decision at the client
    handle below it (``proj.g.query`` counted 2);
  * one CLIENT call reaches the guarded seams up to four times (the vendor's
    ``query`` delegates to ``_query``, which dispatches through the guarded
    command channels), so the count of one write was 4 on the channel this
    change adds.
Both are census defects — the census is the evidence the declaration programme
sizes the remainder with, and its contract says the count is exact.

MODE POLARITY IS UNCHANGED. The landed state is still MEASURE (#9124 part 1):
an undeclared write at a client handle is COUNTED and logged, not raised. Every
refusal test below takes the ``enforced`` fixture, which arms the door for that
test only — arming it globally still refuses the product's own apply-first
doors, and that is #9206, not this lane.

STILL OPEN, stated rather than implied — the enumeration this file is named after
is only honest if it says where it stops:
  * a client handle's NON-Cypher destruction (``proj.db.select_graph(<own>).delete()``
    → ``GRAPH.DELETE``, or a ``copy`` over the cache) carries no statement for the
    classifier to read, so it is not refused here — the same class the write
    handle's own ``proj.g._g.delete()`` spelling already has (#9209 closes it at
    that handle, not at the client);
  * the RAW SOCKET channels below a guarded connection —
    ``connection.pipeline()``, ``connection.client()`` and
    ``connection_pool.get_connection()`` — are unbound by ``_guard_connection``
    (its docstring states the exclusion) and were MEASURED writing an armed
    journal-backed cache through ``GRAPH.QUERY``. Pre-existing, not a regression,
    and the cheapest one to close is ``pipeline()``;
  * a raw vendor client the package did not build carries no hook at all;
  * a channel that cannot name its store (a duck-typed handle with no ``.client``)
    is matched by graph NAME only — the cycle-1 defect, still the behaviour on
    that one channel (stated on ``_JOURNAL_BACKED_KEYS``).

WHAT THIS FILE PROVES ON THE PRE-FIX TREE (stated exactly, #9079 review P3). It
collects **32** cases under pytest (at the review head 25: 15 test functions,
with one parametrisation adding 5 and one adding 7; the review fix adds the
split-spelling, bytes-graph-name and `close()` pins). On ``origin/main`` it does
not report "8 failed / 4 passed": its module-level imports
(`_client_store_key`, `_journal_backed_graphs`, `_journal_backed_keys`,
`_dispatch_decided`) do not exist there, so the run is a single collection
**ImportError** — 0 passed, 0 failed, and the pins cannot execute at all. That is
a stronger statement than a failure count, and it is the one a reader should
quote.
"""

from __future__ import annotations

import pytest

from tortoise.cypher_guard import _client_store_key
from tortoise.projection import (
    _HANDLE_MUTATING_VERBS,
    DirectCacheWriteRefused,
    _journal_backed_graphs,
    _journal_backed_keys,
    _live_write_scope,
    _reset_undeclared_handle_writes,
    undeclared_handle_writes,
)

#: The client-handle verbs that carry a Cypher statement. The SAME shared tuple
#: `_GuardedGraph` and the declaration census read, minus `execute_command`
#: (whose Cypher sits at ``args[2]`` inside a Redis command, so it gets its own
#: cases below rather than the uniform positional call).
MUTATING_QUERY_VERBS = tuple(
    verb for verb in _HANDLE_MUTATING_VERBS if verb != "execute_command")


@pytest.fixture
def enforced(monkeypatch):
    """Arm §17.1's doors for one test (part 2's state, exercised deliberately)."""
    import tortoise.projection as projection

    monkeypatch.setattr(projection, "_HANDLE_GUARD_ENFORCES", True)
    return projection


@pytest.fixture
def measure_only(monkeypatch):
    """Pin the LANDED mode: detect and count, never raise."""
    import tortoise.projection as projection

    monkeypatch.setattr(projection, "_HANDLE_GUARD_ENFORCES", False)
    _reset_undeclared_handle_writes()
    return projection


@pytest.fixture
def armed_sdk(tmp_path):
    """A real SDK with a configured JSONL journal, so §17.1 is ARMED at its graph."""
    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK(str(tmp_path / "client-door.db"),
                      event_log_path=str(tmp_path / "journal.jsonl"))
    try:
        yield sdk
    finally:
        sdk.close()


def _count(proj, label: str, prop: str, value: str) -> int:
    return int(proj.g.query(
        f"MATCH (n:{label} {{{prop}:$v}}) RETURN count(n)",
        params={"v": value},
    ).result_set[0][0])


def _client(proj):
    """A CLIENT handle on the projection's OWN graph — escape (3)'s entry point."""
    return proj.db.select_graph(proj.graph_name)


def _hits(census, marker: str) -> dict[str, int]:
    return {k: v for k, v in census.items() if marker in k}


# ── the load-bearing pins: both escapes wrote the cache on origin/main ─────

def test_escape_two_the_inner_handle_is_refused_when_armed(armed_sdk, enforced):
    """`proj.g._g.query("CREATE …")` — one private hop past the guarded handle.

    RED before this lane: measured writing the cache with the guard ARMED and
    the marker landing (``landed: ['e2', …]`` in the module docstring).
    """
    proj = armed_sdk._get_proj()
    with pytest.raises(DirectCacheWriteRefused):
        proj.g._g.query("CREATE (n:Esc2 {id:'e2'})")
    assert _count(proj, "Esc2", "id", "e2") == 0, (
        "a refused write must land NOTHING — the client handle is one hop below "
        "the guarded handle but writes the same cache")


@pytest.mark.parametrize("verb", MUTATING_QUERY_VERBS)
def test_escape_three_the_client_handle_is_refused_when_armed(
        armed_sdk, enforced, verb):
    """`proj.db.select_graph(<own graph>)` — the SDK's registry-handle shape.

    ⚠️ `ro_query` is the verb whose ENGINE policy refuses a write on the pinned
    server, which is exactly why the guard cannot lean on that: the policy is
    the vendor's and is unmeasured on the 6.x production engine. The refusal
    asserted here is OURS (`DirectCacheWriteRefused`), raised before dispatch.
    """
    proj = armed_sdk._get_proj()
    statement = f"CREATE (n:Esc3 {{id:'{verb}'}})"
    with pytest.raises(DirectCacheWriteRefused):
        getattr(_client(proj), verb)(statement)
    assert _count(proj, "Esc3", "id", verb) == 0


def test_escape_three_the_command_channel_is_refused_when_armed(armed_sdk, enforced):
    """The same door through `execute_command` — a statement at args[2]."""
    proj = armed_sdk._get_proj()
    with pytest.raises(DirectCacheWriteRefused):
        _client(proj).execute_command(
            "GRAPH.QUERY", proj.graph_name, "CREATE (n:Esc4 {id:'e4'})")
    assert _count(proj, "Esc4", "id", "e4") == 0


# ── the door reads the command the CLIENT will actually SEND ──────────────
#
# ⛔ THE FIRST ARGUMENT IS SPLIT BY THE SERIALIZER (#9079 review, P1). Both
# pinned ``redis`` packers do ``args[0].encode().split()`` / ``args[0].split()``
# before sending, so the arguments of a command can ride inside its command WORD:
#
#     execute_command("GRAPH.QUERY", <graph>, "CREATE (n:Split {id:'s'})")
#     execute_command("GRAPH.QUERY <graph>", "CREATE (n:Split {id:'s'})")
#     execute_command("GRAPH.QUERY <graph> CREATE (n:Split {id:'s'})")
#
# A reader gated on ``len(args) >= 3`` and an exact verb at ``args[0]`` saw only
# the first: MEASURED pre-fix, the 2-arg spelling wrote an ARMED journal-backed
# cache with no refusal and no census entry (and the empty-head spelling, one
# case below, did the same) — the same spelling-shaped class #9209 closed at the
# classifier, through the door this file is named after.
#
# ⚠️ The fully-inline 1-arg spelling is NOT the same write on the wire, and it is
# kept as a case anyway: the split words become separate SERVER arguments and
# ``GRAPH.QUERY`` reads its Cypher from the THIRD one, so the engine receives the
# single word ``CREATE`` and answers a grammar error (measured, v4.20.6). The
# door refuses it before dispatch — the fail-closed direction, since a payload
# that cannot be shown to be a read is not forwarded — and the census names the
# text the caller asked to write.
_SPLIT_WRITE = "CREATE (n:Split {id:'s'})"


@pytest.mark.parametrize("build", [
    pytest.param(lambda p: ("GRAPH.QUERY", p.graph_name, _SPLIT_WRITE),
                 id="three-args"),
    pytest.param(lambda p: (f"GRAPH.QUERY {p.graph_name}", _SPLIT_WRITE),
                 id="head-carries-the-graph"),
    pytest.param(lambda p: (f"GRAPH.QUERY {p.graph_name} {_SPLIT_WRITE}",),
                 id="head-carries-the-statement-too"),
])
def test_every_spelling_the_client_serializer_produces_is_refused(
        armed_sdk, enforced, build):
    """RED for the last two spellings before the splitter fix."""
    proj = armed_sdk._get_proj()
    with pytest.raises(DirectCacheWriteRefused):
        proj.g.execute_command(*build(proj))
    assert _count(proj, "Split", "id", "s") == 0, (
        "a refused write must land nothing, whatever spelling carried it")


def test_a_split_spelling_is_refused_at_the_client_handle(armed_sdk, enforced):
    """Escape (3) takes the same splitter, one hop below the write handle."""
    proj = armed_sdk._get_proj()
    with pytest.raises(DirectCacheWriteRefused):
        _client(proj).execute_command(
            f"GRAPH.QUERY {proj.graph_name}", "CREATE (n:Split2 {id:'s2'})")
    assert _count(proj, "Split2", "id", "s2") == 0


# ── the landed mode: COUNTED exactly once per write, not refused ──────────

@pytest.mark.parametrize("shape", [
    ("proj.g.query", lambda proj: proj.g.query("CREATE (n:Once {id:'once'})")),
    ("proj.g._query", lambda proj: proj.g._query("CREATE (n:Once {id:'once'})")),
    ("proj.g.execute_command",
     lambda proj: proj.g.execute_command(
         "GRAPH.QUERY", proj.graph_name, "CREATE (n:Once {id:'once'})")),
    ("client.query", lambda proj: _client(proj).query("CREATE (n:Once {id:'once'})")),
    ("client._query", lambda proj: _client(proj)._query("CREATE (n:Once {id:'once'})")),
    ("client.execute_command",
     lambda proj: _client(proj).execute_command(
         "GRAPH.QUERY", proj.graph_name, "CREATE (n:Once {id:'once'})")),
    ("connection.execute_command",
     lambda proj: proj.db.connection.execute_command(
         "GRAPH.QUERY", proj.graph_name, "CREATE (n:Once {id:'once'})")),
    # ⛔ THE GRAPH HALF OF THE SUPPRESSION KEY IS NORMALISED TOO (#9079 review,
    # P2). The door asks about ``_as_text(args[1])``; a key built from the RAW
    # ``args[1]`` held ``(store, None, stmt)`` for a bytes graph name, which no
    # consultation ever asks about — so nothing was suppressed and one write was
    # measured 3 times.
    ("proj.g.execute_command (bytes graph name)",
     lambda proj: proj.g.execute_command(
         "GRAPH.QUERY", proj.graph_name.encode(),
         b"CREATE (n:Once {id:'once'})")),
    # ... and the same for the spellings whose graph rides inside ``args[0]``:
    # the key must name the graph the door judges, not ``args[1]`` blindly.
    ("proj.g.execute_command (head carries the graph)",
     lambda proj: proj.g.execute_command(
         f"GRAPH.QUERY {proj.graph_name}", "CREATE (n:Once {id:'once'})")),
    # ... and for the spelling whose command starts at ``args[1]`` because the
    # serializer DROPPED an empty command word: the key named ``args[1]`` (the
    # VERB) rather than the graph, so again nothing was suppressed.
    ("proj.g.execute_command (empty command word)",
     lambda proj: proj.g.execute_command(
         "", "GRAPH.QUERY", proj.graph_name,
         "CREATE (n:Once {id:'once'})")),
])
def test_one_write_is_counted_exactly_once_whatever_the_channel(
        armed_sdk, measure_only, shape):
    """The census is the evidence the declaration programme consumes.

    `_record_undeclared_handle_write` says the count is EXACT ("a reader can
    tell one call from a thousand"), and that is only true if one logical write
    is decided once. Two independent nestings used to break it, both MEASURED:
    the write handle's dispatch re-took the decision at the client handle below
    it (`proj.g.execute_command` = 2), and one client call reaches the guarded
    seams up to four times (`client.query` = 4, `client._query` = 3,
    `connection.execute_command` = 2).
    """
    label, issue = shape
    proj = armed_sdk._get_proj()
    issue(proj)
    census = undeclared_handle_writes()
    hits = _hits(census, "Once")
    assert hits, f"{label}: the write must be measured at all; census={census}"
    assert sum(hits.values()) == 1, (
        f"{label}: one write counted {sum(hits.values())} times: {hits}")
    # MEASURE MODE does not refuse — the write landed, which is the whole point
    # of part 1's ordering (declare first, arm last).
    assert _count(proj, "Once", "id", "once") == 1


# ── the suppression key is the statement the DISPATCH carries ─────────────
#
# ⛔ The channel above is not the whole question. Its key is ``args[0]``, which
# is the Cypher for the five query verbs — but NOT for the two verbs that build
# or rewrite their statement after the door has decided. One write was counted
# TWICE on each (measured on the embedded lane, one write per case):
#
#   * ``execute_command`` — the #7162 ``WITH *`` barrier rewrites ``args[2]``
#     between the decision and the dispatch, so the guarded channel below asks
#     about text no key in the suppression set covered;
#   * ``call_procedure`` — ``args[0]`` is the procedure NAME; the vendor builds
#     ``CALL …`` and routes it through the guarded ``_query``.
#
# Both are census defects only (no refusal is affected: an armed door raises
# before dispatch), and both matter because the census is the evidence the
# declaration programme sizes the remainder with.

#: A mutating statement in the #6976 shape, so the #7162 normaliser inserts a
#: `WITH *` barrier — the write still lands (`_count` sees `Once`), the text the
#: wire carries is NOT the text the door decided.
_BARRIER_WRITE = (
    "MATCH (n:Seed) WHERE n.id = 's' MATCH (n) CREATE (x:Once {id:'once'})")


def test_a_barrier_rewritten_command_is_counted_exactly_once(
        armed_sdk, measure_only, monkeypatch):
    """One `GRAPH.QUERY` whose text the barrier rewrites is ONE census entry.

    RED before the fix: the pre-barrier text was suppressed and the barred text
    was not, so the census held TWO entries (one per spelling) for one write.
    """
    import tortoise.cypher_guard as cypher_guard

    # An UNREADABLE engine version is the normaliser's own "barrier needed"
    # verdict (a wrong probe must never be a false CLEAN), so forcing it makes
    # the barrier deterministic instead of engine-dependent.
    monkeypatch.setattr(
        cypher_guard, "_engine_needs_predicate_barrier", lambda client: True)
    proj = armed_sdk._get_proj()
    proj.g.query("CREATE (n:Seed {id:'s'})")  # the row the WHERE filters on
    _reset_undeclared_handle_writes()
    from tortoise.cypher_guard import _predicate_drop_barrier

    barred = _predicate_drop_barrier(_BARRIER_WRITE)
    assert barred != _BARRIER_WRITE, (
        "the fixture no longer produces the #6976 shape, so this test would "
        "pass without exercising the barrier")
    proj.g.execute_command("GRAPH.QUERY", proj.graph_name, _BARRIER_WRITE)
    hits = _hits(undeclared_handle_writes(), "Once")
    assert sum(hits.values()) == 1, (
        f"one write counted {sum(hits.values())} times (one per spelling of its "
        f"text): {hits}")
    assert _count(proj, "Once", "id", "once") == 1


def test_a_mutating_procedure_is_counted_exactly_once(armed_sdk, measure_only):
    """`call_procedure` builds `CALL …`; that text is what must be suppressed.

    RED before the fix: the key was derived from ``args[0]`` — the procedure
    NAME — so nothing the vendor's ``_query`` asked about was suppressed and one
    procedure write was counted twice under one key.
    """
    proj = armed_sdk._get_proj()
    proj.g.call_procedure(
        "db.idx.fulltext.createNodeIndex", False, args=["Once", "id"])
    hits = _hits(undeclared_handle_writes(), "createNodeIndex")
    assert hits, f"the procedure write must be measured at all: {hits}"
    assert sum(hits.values()) == 1, (
        f"one procedure write counted {sum(hits.values())} times: {hits}")
    catalogue = {str(cell) for row in proj.g.ro_query(
        "CALL db.indexes()").result_set for cell in row}
    assert any("Once" in entry for entry in catalogue), (
        f"the procedure must have LANDED in measure mode; catalogue={catalogue}")


def test_a_non_text_payload_does_not_break_the_suppression_key():
    """The key derivation is TOTAL — a raw command head can be `bytes`.

    RED before the fix, and it was a BUILD failure, not a census one: redis-py
    accepts the bytes spelling of `execute_command`, and normalising that head
    as text raised `TypeError: sequence item 0: expected str instance, bytes
    found` on a legitimate dispatch (measured through
    `_GuardedGraph.execute_command(b"COPY", "x")` on an unarmed embedded
    projection — three #9209 tests, and the `test-d14-hosted-api` job).
    """
    from tortoise.projection import _dispatch_decided

    class _Inner:
        name = "probe"
        client = None

        def __init__(self):
            self.calls = []

        def execute_command(self, *args, **kwargs):
            self.calls.append(args)
            return "OK"

    inner = _Inner()
    assert _dispatch_decided(
        inner, "execute_command", b"COPY", "probe") == "OK"
    assert inner.calls == [(b"COPY", "probe")], (
        "the payload must reach the handle unchanged — the key derivation is "
        "an observation, never a rewrite")


# ── the door is a §17.1 door, not a blanket refusal ───────────────────────

def test_a_declared_lane_authorizes_the_client_handle(armed_sdk, enforced):
    """A live door that appended its record first declares, then writes."""
    proj = armed_sdk._get_proj()
    with _live_write_scope("test-declared-lane"):
        _client(proj).query("CREATE (n:Esc6 {id:'e6'})")
    assert _count(proj, "Esc6", "id", "e6") == 1


def test_a_read_through_the_client_handle_still_passes(armed_sdk, enforced):
    """A read is not a write: the shared classifier decides, not the verb name."""
    proj = armed_sdk._get_proj()
    result = _client(proj).query("MATCH (n:Point) RETURN count(n)")
    assert result.result_set, "a read through the client handle must still work"


def test_a_graph_that_is_nobodys_cache_is_outside_section_17_1(armed_sdk, enforced):
    """§17.1 has nothing to enforce on a graph that is no journal's projection."""
    proj = armed_sdk._get_proj()
    other = proj.db.select_graph(f"not_a_journal_projection_{id(proj)}")
    other.query("CREATE (n:NotOurs {id:'n1'})")
    assert int(other.query("MATCH (n:NotOurs) RETURN count(n)").result_set[0][0]) == 1


# ── the registry itself ──────────────────────────────────────────────────

def test_the_registry_names_the_journal_backed_graph_and_is_released_on_close(tmp_path):
    """A stale entry would judge a LATER projection reusing the name."""
    from tortoise.sdk import TortoiseSDK

    journal = tmp_path / "journal.jsonl"
    sdk = TortoiseSDK(str(tmp_path / "registry.db"), event_log_path=str(journal))
    proj = sdk._get_proj()
    name = proj.graph_name
    assert name in _journal_backed_graphs(), (
        "binding a journal must register the graph for the client-level door")
    assert any(graph == name and store for store, graph in _journal_backed_keys()), (
        "the registration must carry the STORE, not only the name; "
        f"keys={_journal_backed_keys()}")
    sdk.close()
    assert name not in _journal_backed_graphs(), (
        "closing the projection must release its registration")


def test_close_releases_the_registry_but_not_the_journal_authority(tmp_path):
    """`close()` disarms the REGISTRY, never the projection's own doors.

    RED before the fix: ``close()`` also wrote ``self._journal_backed = False``,
    which disarmed every §17.1 door on that projection (the write handle,
    `apply`, the public `query`) while the graph stayed WRITABLE — a server
    ``db.close()`` is a pool disconnect and redis-py reconnects on the next
    command — so a post-close write landed with no census entry and, once armed,
    no refusal. The flag says "this graph is a derived cache of a journal that
    exists on disk"; closing a connection does not make that untrue.
    """
    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK(str(tmp_path / "closed.db"),
                      event_log_path=str(tmp_path / "journal.jsonl"))
    proj = sdk._get_proj()
    assert proj._journal_backed, "the fixture must arm the guard"
    sdk.close()
    assert proj.graph_name not in _journal_backed_graphs(), (
        "the (store, graph) registration is the release close() owes")
    assert proj._journal_backed, (
        "close() disarmed the projection's own §17.1 doors: a post-close write "
        "would reach the journal-backed graph with no census entry and no "
        "refusal, because the journal still exists and the client reconnects")


def test_a_same_named_graph_in_another_store_is_not_judged_as_this_journal(enforced):
    """Every embedded SDK names its graph `tortoise`; the stores differ.

    Keyed on the NAME alone, a journal-bound store A made a write to an
    unrelated store B be judged by A's journal — with the door armed, REFUSED, a
    write §17.1 has nothing to say about. The decision is driven directly here
    because the docker lane gives each embedded SDK a UNIQUE graph name, which
    would make the case unreachable through two real SDKs.
    """
    from tortoise.projection import (
        _journal_write_authority,
        _register_journal_backed_graph,
        _unregister_journal_backed_graph,
    )

    name = "tortoise"
    _register_journal_backed_graph("host:127.0.0.1:6379", name)
    try:
        statement = "CREATE (n:OtherStore {id:'b1'})"
        # the journal-bound store: REFUSED (armed, no declared lane)
        with pytest.raises(DirectCacheWriteRefused):
            _journal_write_authority("host:127.0.0.1:6379", name, statement)
        # the same NAME in a different store: not this journal's cache
        _journal_write_authority("host:127.0.0.1:16391", name, statement)
    finally:
        _unregister_journal_backed_graph("host:127.0.0.1:6379", name)


def test_a_dropped_projection_releases_its_registration():
    """A projection dropped WITHOUT close() must not outlive its own guard.

    The SDK keeps a strong reference to ITS projection, so this builds a bare
    instance — the shape a thrown-away, never-closed projection actually has.
    """
    import gc
    import weakref as weakref_mod

    from tortoise.projection import FalkorProjection

    proj = object.__new__(FalkorProjection)
    proj.graph_name = "gc_probe_graph"
    proj.db = None
    proj._bind_journal("/tmp/gc-probe-journal.jsonl")
    assert "gc_probe_graph" in _journal_backed_graphs()
    ref = weakref_mod.ref(proj)
    del proj
    gc.collect()
    assert ref() is None, "still referenced, so this proves nothing"
    assert "gc_probe_graph" not in _journal_backed_graphs(), (
        "a projection dropped without close() left its registration behind")


def test_a_write_handle_over_a_journal_less_projection_cannot_write_a_journals_cache(
        tmp_path, enforced):
    """REVIEW CYCLE 2's bypass — a write handle that fronts SOMEONE ELSE's graph.

    `_GuardedGraph` is built over an arbitrary graph:
    `hosted_api` does `_GuardedGraph(proj.db.select_graph(<other>), <proj>)`, and
    such a projection often has NO journal. Measured bypass before the fix: the
    wrapper's own door let the write through (its projection has no journal) and
    the suppression told the client door one hop lower to stay out of it, so the
    write landed on a JOURNAL-BACKED cache with no refusal and no census entry —
    the second writer, through a handle the guard itself published approval for.
    """
    from tortoise.projection import _GuardedGraph
    from tortoise.sdk import TortoiseSDK

    victim = TortoiseSDK(str(tmp_path / "victim.db"),
                         event_log_path=str(tmp_path / "journal.jsonl"))
    outsider = TortoiseSDK(str(tmp_path / "outsider.db"))
    try:
        proj_victim = victim._get_proj()
        proj_outsider = outsider._get_proj()
        assert proj_victim._journal_backed and not proj_outsider._journal_backed
        # ⛔ THIS SHAPE NEEDS BOTH PROJECTIONS ON ONE STORE, and the EMBEDDED lane
        # gives every SDK its own (a private socket/file), so there the outsider's
        # handle names ITS OWN graph `tortoise` — a graph no journal derives, which
        # §17.1 correctly leaves alone (`test_a_graph_that_is_nobodys_cache_…`). The
        # bypass this test is about is the same NAME in the SAME store, so the
        # premise is unestablished rather than the door open: state it, and run it
        # on the docker lane (`tools/test_lane.py uri`), which is where CI's `core`
        # surface runs this file.
        if _client_store_key(proj_victim.db) != _client_store_key(proj_outsider.db):
            pytest.skip(
                "needs both projections on ONE store (embedded gives each SDK its "
                f"own): {_client_store_key(proj_victim.db)} != "
                f"{_client_store_key(proj_outsider.db)}")
        # the shape that bypassed: a journal-LESS projection's write handle over
        # the journal-backed graph.
        handle = _GuardedGraph(
            proj_outsider.db.select_graph(proj_victim.graph_name), proj_outsider)
        with pytest.raises(DirectCacheWriteRefused):
            handle.query("CREATE (n:Hijack {id:'h1'})")
        assert int(proj_victim.db.select_graph(proj_victim.graph_name).query(
            "MATCH (n:Hijack) RETURN count(n)").result_set[0][0]) == 0
        # and the client door one hop lower refuses it on its own, so removing
        # either half of the fix leaves the other covering the bypass
        with pytest.raises(DirectCacheWriteRefused):
            proj_outsider.db.select_graph(proj_victim.graph_name).query(
                "CREATE (n:Hijack {id:'h2'})")
        # ⛔ THE SAME BYPASS ONE VERB OVER (#9079 review, P1 fail-open —
        # MEASURED at the head: NOT REFUSED, the CREATE landed, census empty).
        # `Graph.execute_command` is the CLIENT's raw method, so the graph it
        # writes is the one its PAYLOAD names — while this door judged
        # `self._g.name`, i.e. a graph nobody journals. The suppression key reads
        # the payload's graph, so it then told the client door below to stand
        # down and an ARMED projection accepted the write through a handle it had
        # approved as out of scope.
        other = _GuardedGraph(
            proj_outsider.db.select_graph(f"not_the_victim_{id(proj_victim)}"),
            proj_outsider)
        with pytest.raises(DirectCacheWriteRefused):
            other.execute_command(
                "GRAPH.QUERY", proj_victim.graph_name,
                "CREATE (n:HijackCmd {id:'h3'})")
        assert int(proj_victim.db.select_graph(proj_victim.graph_name).query(
            "MATCH (n:HijackCmd) RETURN count(n)").result_set[0][0]) == 0
    finally:
        victim.close()
        outsider.close()


def test_the_hook_is_installed_by_the_projection():
    """`cypher_guard` carries the call; the projection owns the decision."""
    import tortoise.cypher_guard as cypher_guard
    import tortoise.projection as projection

    assert cypher_guard._JOURNAL_WRITE_AUTHORITY is projection._journal_write_authority

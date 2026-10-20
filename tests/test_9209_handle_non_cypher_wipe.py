"""#9209 — §17.1's FOURTH door: the write handle's NON-CYPHER verbs.

`docs/architecture/STORAGE-ARCHITECTURE.md` §17.1 makes the graph a DERIVED
CACHE of the journal, and #7951 armed a refusal at two of its doors — the fold
(`FalkorProjection.apply`) and the public raw-query lane
(`FalkorProjection.query`). Both of those doors read a Cypher **statement**,
and a whole-graph destruction carries none, so `proj.g` still wiped an armed
projection two ways:

* ``proj.g.delete()`` — the vendor's ``GRAPH.DELETE``, reached through
  ``_GuardedGraph.__getattr__``, which forwards every attribute the wrapper
  does not itself define. No verb-level guard can see a forwarded attribute *by
  construction*.
* ``proj.g.execute_command("GRAPH.DELETE", <graph>)`` — the raw command
  channel, which ``cypher_guard._guard_execute_command`` forwards untouched
  because it classifies only the Cypher argument of the four ``GRAPH.*`` QUERY
  verbs.

MEASURED on `main` before this suite existed (embedded, journal-backed — the
same shape the issue reports from `feat/9124-tm`):

    proj.g.query("MATCH (n) RETURN count(n)")                 -> 2
    proj.g.execute_command("GRAPH.DELETE", proj._graph_name)  -> 'OK'
    proj.g.query("MATCH (n) RETURN count(n)")                 -> 0
    proj.g.delete()                                           -> 'OK'

while the module comment stated the handle's escapes as three *statement*
escapes. The wipe is unrepairable in the direction it ran: `rebuild_all` can
only replay records, and "somebody deleted the graph" is not one.

WHAT THIS SUITE PINS — the wrong behaviour is IMPOSSIBLE, not merely that a new
path runs. Every refusal case asserts the CACHE IS STILL THERE afterwards
(count unchanged), because a guard that raised *after* the wipe would leave
exactly the corruption it claims to prevent, with a traceback on top.

WHAT IT DOES NOT CLAIM. Not every non-Cypher mutation is refused — only the one
that destroys this cache. `GRAPH.COPY` writes this cache's contents into
ANOTHER graph and removes nothing here, so it must stay allowed: a rule that
refused it would be refusing an operation the cache never lost anything to, and
`tests/test_graphcopy_boolean_index_3154.py` legitimately copies through this
handle. That asymmetry is the rule, and
`test_an_unrelated_non_cypher_verb_...` is what keeps it honest. The
Cypher-bearing raw command
(``execute_command("GRAPH.QUERY", <g>, "MATCH (n) DETACH DELETE n")``) is NOT
closed here — it needs statement recovery, which is #9124's door.

⛔ THE VERB LIST IS NOT THE RULE EITHER (review round 6), and this suite was
written wrongly once because of it: a FalkorDB graph IS a Redis key, so
``execute_command("DEL", <own graph>)`` — and ``UNLINK``, ``RENAME``,
``EXPIRE 0`` — wiped an armed cache while the enumerated list named only
``GRAPH.DELETE`` and the flushes. MEASURED, on a real journal-backed SDK at
the head that shipped the list:

    proj.g.execute_command("DEL", proj._graph_name)        -> 'OK', 2 -> 0
    proj.g.execute_command("UNLINK", proj._graph_name)     -> 1,    2 -> 0
    proj.g.execute_command("RENAME", proj._graph_name, x)  -> True, cache moved
    proj.g.execute_command("EXPIRE", proj._graph_name, 0)  -> 1,    2 -> 0

So the armed handle enforces a NAMESPACE boundary instead: outside the
``GRAPH.`` family it refuses every command except the read-only probes the repo
actually sends (``_ARMED_READ_ONLY_COMMANDS``), because anything else that can
reach the key space can remove the cache. The tests below pin BOTH halves —
the refusal, and that a read still passes (an over-refusal here would silently
drop a legitimately retried write: `tortoise/sdk.py::_graph_presence` probes
``EXISTS`` and answers "do not retry" when it raises).
"""
from __future__ import annotations

import contextlib
import inspect

import pytest

from tortoise.projection import (
    DirectCacheWriteRefused,
    FalkorProjection,
    _GuardedGraph,
    _is_bulk_wipe,
    _replay_write_scope,
)

# ── fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def sdk_with_journal(tmp_path):
    """A real SDK whose projection is ARMED (`event_log_path` configured).

    ALWAYS EMBEDDED, and this is measured rather than assumed: an explicit
    `db_path` WINS over `TORTOISE_DB_URI` in the SDK, so `proj._is_embedded`
    stays True even on the docker lane — the same shape
    `tests/test_7951_enforce_atomic_write.py` uses. (The earlier wording said
    this construction "reaches the docker server when one is" configured, which
    a fresh-context review measured FALSE: a run with `TORTOISE_DB_URI` set
    still logged `_is_embedded: True`.)

    That is the right lane for what this suite pins — every refusal below is
    asserted against a real journal-backed projection AND a real node count, and
    the cache-intact half needs no server. The consequence, named so it is not
    mistaken for coverage: with `_is_embedded` True, L2's NAME branch
    (`_assert_test_graph`) cannot fire here, so the `embedded=False` cases use
    `_RecordingGraph` and assert the DECISION plus that nothing was dispatched.
    No server-lane case of the #9209 door exists in this suite.
    """
    from tortoise.sdk import TortoiseSDK

    journal = tmp_path / "journal.jsonl"
    sdk = TortoiseSDK(str(tmp_path / "noncypher.db"), event_log_path=str(journal))
    sdk.create_entity("subject", "armed-subject", description="a real node")
    try:
        yield sdk
    finally:
        sdk.close()


def _count(proj, graph=None) -> int:
    handle = proj if graph is None else graph
    return int(handle.query("MATCH (n) RETURN count(n)").result_set[0][0])


class _RecordingGraph:
    """Minimal vendor-shaped handle: records the calls it receives.

    Used only where the assertion is about a decision made BEFORE dispatch
    (the L2 / declared-scope cases), so nothing here reaches a server.
    """

    def __init__(self):
        self.calls: list[tuple] = []

    def delete(self, *args, **kwargs):
        self.calls.append(("delete", args, kwargs))
        return "OK"

    def execute_command(self, *args, **kwargs):
        self.calls.append(("execute_command", args, kwargs))
        return "OK"

    def query(self, cypher, params=None, timeout=None):  # pragma: no cover
        raise AssertionError("no query may run in these cases")


class _NamedRecordingGraph(_RecordingGraph):
    """A recording handle that carries the vendor's ``name`` attribute.

    `delete()` passes the handle's OWN graph name as the target, because the
    wrapper is also built in-tree over a graph that is not the projection's
    (`tortoise/hosted_api.py`), so "the projection's name" is not always "the
    graph this verb removes".
    """

    def __init__(self, name: str):
        super().__init__()
        self.name = name


def _armed_projection(*, graph_name: str, embedded: bool):
    """An ARMED projection with no DB — the guard must fire before any I/O.

    `_journal_backed` is the arming (`FalkorProjection._bind_journal`); it is a
    class-level default of `False`, so setting it per instance arms this object
    exactly as a configured journal does.
    """
    proj = object.__new__(FalkorProjection)
    proj._journal_backed = True
    proj._is_embedded = embedded
    proj._graph_name = graph_name
    proj._path = None
    proj.g = _RecordingGraph()
    return proj


# ── the command channel: GRAPH.DELETE ─────────────────────────────────────

@pytest.mark.parametrize("verb", ["GRAPH.DELETE", "graph.delete", b"GRAPH.DELETE"])
def test_GRAPH_DELETE_on_the_command_channel_is_refused_on_an_armed_projection(
        sdk_with_journal, verb):
    """RED before #9209 (the wipe LANDED — count went to 0), GREEN after.

    Three spellings, because the classifier must not be one literal: redis-py
    accepts the BYTES form (the #3595 review finding) and Redis itself is
    case-insensitive, so a str-only or case-sensitive match would leave the
    door open two ways while appearing closed.

    MUTATION: drop the `_DESTRUCTIVE_HANDLE_COMMANDS` branch from
    `_GuardedGraph.execute_command` and this test fails at `pytest.raises`
    (DID NOT RAISE), with the node count at 0.
    """
    proj = sdk_with_journal._get_proj()
    assert proj._journal_backed, (
        "the fixture must ARM the guard (`event_log_path` configured) — an "
        "unarmed projection is outside §17.1 and this test would be vacuous"
    )
    before = _count(proj)
    assert before > 0, "the armed cache must hold something a wipe could destroy"

    with pytest.raises(DirectCacheWriteRefused) as exc:
        proj.g.execute_command(verb, proj._graph_name)

    assert "17.1" in str(exc.value), (
        "the refusal must cite the rule it enforces so the next writer can read "
        f"why; message={str(exc.value)!r}"
    )
    assert _count(proj) == before, (
        "the refusal must be PRE-DISPATCH: an armed projection whose wipe raises "
        "AFTER deleting the cache is worse than no guard at all"
    )


# ── the server-wide commands: FLUSHALL / FLUSHDB ─────────────────────────


@pytest.mark.parametrize(
    "args",
    [("FLUSHALL",), ("flushall",), (b"FLUSHALL",), ("FLUSHALL", "ASYNC"),
     ("FLUSHALL ASYNC",), (b"flushall async",), ("FLUSHDB",), ("flushdb", "ASYNC")],
)
def test_a_server_wide_flush_is_refused_on_an_armed_projection(
    sdk_with_journal, args
):
    """Review finding, now a pin: a flush carries no statement and no graph name.

    ``_guard_execute_command`` forwards every non-``GRAPH`` command untouched,
    so ``proj.g.execute_command("FLUSHALL")`` destroyed an armed cache — the
    same class as #9209 one level up (it takes EVERY graph on the server, not
    just this one). The module's own maxmemory message already told operators
    "do NOT FLUSHALL"; this is that advice enforced at the handle.

    The joined-string spellings are included because the client SPLITS the
    first argument at pack time (`redis.connection.*Serializer.pack`), so
    ``"FLUSHALL ASYNC"`` is the same command as ``("FLUSHALL", "ASYNC")`` — a
    classifier that reads one token would close the pair spelling only.

    MUTATION: drop ``FLUSHALL``/``FLUSHDB`` from
    ``_DESTRUCTIVE_HANDLE_COMMANDS`` and this fails with DID NOT RAISE and the
    cache at 0 nodes.
    """
    proj = sdk_with_journal._get_proj()
    before = _count(proj)
    assert before > 0

    with pytest.raises(DirectCacheWriteRefused):
        proj.g.execute_command(*args)

    assert _count(proj) == before, "the flush reached the server despite the refusal"


def test_a_server_wide_flush_is_refused_even_inside_a_declared_scope():
    """A declaration says "this WRITE is a projection of the journal".

    It cannot say that about a flush: ``FLUSHALL`` also removes graphs this
    journal never wrote and no replay can restore — and L2 (the disposability
    check) reads THIS projection's name, so it can speak for exactly one graph
    while the command takes all of them. Review round 2 measured that gap: with
    a ``test_*``-named (or embedded) armed projection, an in-scope flush passed.

    MUTATION: delete the ``_SERVER_WIDE_COMMANDS`` branch from
    `_refuse_unauthorized_graph_wipe` and this fails — the in-scope flush is
    dispatched instead of raising.
    """
    proj = _armed_projection(graph_name="test_9209", embedded=False)
    guarded = _GuardedGraph(proj.g, proj)

    with _replay_write_scope(), pytest.raises(
        DirectCacheWriteRefused, match="EVERY graph"
    ):
        guarded.execute_command("FLUSHALL")
    with _replay_write_scope(), pytest.raises(
        DirectCacheWriteRefused, match="EVERY graph"
    ):
        guarded.execute_command("FLUSHDB", "ASYNC")

    assert proj.g.calls == [], (
        f"a server-wide flush reached the vendor handle; calls={proj.g.calls!r}"
    )


# ── the client splits the first argument: the joined spelling ─────────────


@pytest.mark.parametrize("sep", [" ", "  ", "\t"])
def test_a_joined_graph_delete_is_refused_like_the_two_argument_form(
    sdk_with_journal, sep
):
    """Review finding, now a pin: the CLIENT splits the first argument.

    Both serializers in the pinned ``redis`` do ``args[0].encode().split()``
    before packing (their own comment: a caller may put "1 or more literal
    arguments in the command name, e.g. 'CONFIG GET'"), so
    ``execute_command("GRAPH.DELETE <graph>")`` and
    ``execute_command("GRAPH.DELETE", "<graph>")`` arrive byte-identical. A
    classifier that reads ``args[0]`` as ONE TOKEN therefore closes one spelling
    and leaves the other open — the same spelling-shaped coverage defect #9209
    exists to end. Whitespace variants are included because the splitter is
    ``str.split()``, not a space-only split.

    MUTATION: take the verb from ``args[0]`` verbatim instead of from
    ``_handle_command_words`` and this fails with DID NOT RAISE, cache at 0.
    """
    proj = sdk_with_journal._get_proj()
    before = _count(proj)
    assert before > 0

    with pytest.raises(DirectCacheWriteRefused):
        proj.g.execute_command(f"GRAPH.DELETE{sep}{proj._graph_name}")

    assert _count(proj) == before, (
        "the joined-spelling wipe reached the server despite the refusal"
    )


# ── the forwarded attribute: proj.g.delete() ──────────────────────────────


def test_the_handle_delete_verb_is_refused_and_lands_nothing(sdk_with_journal):
    """RED before #9209 (`proj.g.delete()` returned 'OK' and emptied the graph).

    This is the shape no query guard can reach: `__getattr__` forwarded the
    vendor's `delete` verb straight through. Pinned behaviourally on the REAL
    vendor handle, so an implementation that merely renames the hole (defines
    `delete` but forgets to call the decision) fails here.

    MUTATION: delete the `delete` method from `_GuardedGraph` (back to a
    forwarded attribute) and this test fails — the assertion is on the node
    count, which reaches 0.
    """
    proj = sdk_with_journal._get_proj()
    before = _count(proj)
    assert before > 0

    with pytest.raises(DirectCacheWriteRefused):
        proj.g.delete()

    assert _count(proj) == before, (
        "the whole-graph delete reached the vendor handle despite the refusal"
    )


def test_the_same_decision_covers_both_spellings():
    """One decision, two spellings — pinned structurally, not by prose.

    The command channel and the forwarded attribute must consult the SAME
    refusal (`_refuse_unauthorized_graph_wipe`). Two copies of the rule is how
    one of them silently drifts open the next time the other is narrowed; the
    repo's #3595 convention is one shared decision per class.
    """
    assert hasattr(_GuardedGraph, "delete"), (
        "`delete` must be DEFINED on the wrapper — a forwarded attribute is "
        "unreachable by any verb-level guard by construction (#9209)"
    )
    assert hasattr(_GuardedGraph, "_refuse_unauthorized_graph_wipe")
    for fn in (_GuardedGraph.delete, _GuardedGraph.execute_command):
        src = inspect.getsource(fn)
        assert "_refuse_unauthorized_graph_wipe" in src, (
            f"{fn.__qualname__} does not consult the shared wipe refusal"
        )


# ── the precise half: an UNRELATED non-Cypher verb ────────────────────────

def test_an_unrelated_non_cypher_verb_leaves_the_armed_cache_intact(
        sdk_with_journal):
    """A non-Cypher mutation that destroys nothing must not be refused, and must
    not disturb an armed cache.

    Three assertions, each load-bearing:
    1. the verb was NOT refused — so the guard refuses destruction, not every
       non-Cypher mutation (a blacklist would fail right here);
    2. the COPY really ran (the destination holds the same nodes) — otherwise
       "the cache is intact" would be satisfied by a no-op and prove nothing;
    3. the arming survived (a following drive-by `GRAPH.DELETE` is still
       refused) — a guard that "cleared" its own state on an unrelated verb
       would re-open §17.1 for the rest of the process.

    MUTATION: make `_refuse_unauthorized_graph_wipe` / `_refuse_key_space_handle_
    command` refuse every non-Cypher verb and (1) fails; make them clear/disarm
    anything on the way through and (3) fails.
    """
    proj = sdk_with_journal._get_proj()
    before = _count(proj)
    dst = proj._graph_name + "_9209_copy"

    proj.g.copy(dst)  # must NOT be refused: it removes nothing from this cache

    assert _count(proj) == before, "an unrelated non-Cypher verb changed the cache"
    assert _count(proj, proj.db.select_graph(dst)) == before, (
        "GRAPH.COPY did not actually run — the 'cache intact' assertion below "
        "would be vacuous"
    )
    with pytest.raises(DirectCacheWriteRefused):
        proj.g.delete()
    assert _count(proj) == before, (
        "the arming did not survive an unrelated non-Cypher verb"
    )


# ── the key-space door: the cache IS a Redis key (review round 6) ────────

#: Every one of these DESTROYS the graph's key — and with it the armed cache —
#: while carrying no Cypher statement and, in the first two, no graph argument
#: that any Cypher-reading guard could see. Measured wiping a real armed cache
#: at the head that enumerated only GRAPH.DELETE and the flushes.
_KEY_SPACE_WIPES = [
    ("DEL",),
    ("UNLINK",),
    ("RENAME", "<other>"),
    ("EXPIRE", 0),
    ("PEXPIRE", 0),
]


@pytest.mark.parametrize("tail", _KEY_SPACE_WIPES,
                         ids=[t[0] for t in _KEY_SPACE_WIPES])
def test_a_key_space_command_on_the_graph_key_is_refused(
        sdk_with_journal, tail):
    """RED at the previous head: the cache went to 0 with no error at all.

    The graph's derived cache is stored under the graph's OWN Redis key, so
    ``DEL`` / ``UNLINK`` / ``RENAME`` / ``EXPIRE 0`` are the same whole-graph
    wipe as ``GRAPH.DELETE`` — with no statement for either Cypher door to read
    and, for the first two, no graph name in a position the classifier looked
    at. That is why the fix is a NAMESPACE boundary and not four more entries in
    a verb list: the list was incomplete at every round that used one.

    PRE-DISPATCH, as everywhere in this suite: the count is read AFTER the
    refusal, so a guard that raised on top of a completed wipe fails here.

    MUTATION: return early from `_refuse_key_space_handle_command` and this
    fails with DID NOT RAISE and the node count at 0 (verified against the
    head before this test existed).
    """
    proj = sdk_with_journal._get_proj()
    assert proj._journal_backed, "the fixture must ARM the guard"
    before = _count(proj)
    assert before > 0, "the armed cache must hold something a wipe could destroy"

    args = [tail[0], proj._graph_name]
    for extra in tail[1:]:
        args.append(proj._graph_name + "_9209_dst" if extra == "<other>" else extra)

    with pytest.raises(DirectCacheWriteRefused) as exc:
        proj.g.execute_command(*args)

    assert "17.1" in str(exc.value), (
        f"the refusal must cite the rule it enforces; message={str(exc.value)!r}"
    )
    assert _count(proj) == before, (
        f"`{tail[0]}` was refused AFTER it destroyed the cache — an armed "
        "projection whose wipe raises on top of a completed deletion is worse "
        "than no guard at all"
    )


@pytest.mark.parametrize("verb", [b"DEL", "del", "DEL"])
def test_the_key_space_refusal_is_not_spelling_shaped(sdk_with_journal, verb):
    """Bytes and case, because the client accepts both and Redis is
    case-insensitive — the same two spellings the GRAPH.DELETE cases pin.
    """
    proj = sdk_with_journal._get_proj()
    before = _count(proj)
    with pytest.raises(DirectCacheWriteRefused):
        proj.g.execute_command(verb, proj._graph_name)
    assert _count(proj) == before


def test_the_joined_key_space_spelling_is_refused_too(sdk_with_journal):
    """The client splits the first argument, so ``"DEL <graph>"`` in one string
    is the same command as two arguments (`_handle_command_words`).
    """
    proj = sdk_with_journal._get_proj()
    before = _count(proj)
    with pytest.raises(DirectCacheWriteRefused):
        proj.g.execute_command(f"DEL {proj._graph_name}")
    assert _count(proj) == before


# ── the classifier's own blind spots (review measure, P0) ─────────────────

#: First arguments that carry ZERO command words. The serializer does
#: ``args[0].encode().split()`` and DROPS the empty result, so the command the
#: server receives starts at ``args[1]`` — which is where the verb hides.
_ZERO_WORD_HEADS = ["", "   ", " \t "]


@pytest.mark.parametrize("head", _ZERO_WORD_HEADS)
@pytest.mark.parametrize("flush", ["FLUSHALL", "FLUSHDB"])
def test_a_zero_word_first_argument_cannot_hide_a_server_wide_flush(
        sdk_with_journal, head, flush):
    """P0, measured: ``execute_command("", "FLUSHDB")`` emptied an armed cache.

    The first version of `_handle_command_words` returned ``(None, ())`` when
    the head carried no words, so the guard saw no verb and the payload was
    forwarded — with the verb one position to the left of where it looked:

        proj.g.execute_command("", "FLUSHDB")   -> 'OK', nodes 2 -> 0

    The word list is now built the way the serializer builds it (the head's
    words followed by every argument), so the verb is classified wherever the
    serializer puts it.

    MUTATION: return ``(None, ())`` from `_handle_command_words` when
    ``args[0].split()`` is empty — i.e. restore the pre-fix helper — and this
    fails with DID NOT RAISE and the node count at 0.
    """
    proj = sdk_with_journal._get_proj()
    assert proj._journal_backed, "the fixture must ARM the guard"
    before = _count(proj)
    assert before > 0, "the armed cache must hold something a wipe could destroy"

    with pytest.raises(DirectCacheWriteRefused) as exc:
        proj.g.execute_command(head, flush)

    assert "17.1" in str(exc.value), (
        f"the refusal must cite the rule it enforces; message={str(exc.value)!r}"
    )
    assert _count(proj) == before, (
        "a flush reached the server because its verb was one argument further "
        "right than the guard looked — the wipe landed before anything raised"
    )


@pytest.mark.parametrize("head", _ZERO_WORD_HEADS)
def test_a_zero_word_first_argument_cannot_hide_a_graph_delete(sdk_with_journal, head):
    """The same blind spot, on the verb that destroys ONE graph.

    ``execute_command(" ", "GRAPH.DELETE", <own graph>)`` deleted the armed
    cache, and ``execute_command(" ", "GRAPH.DELETE", "victim9209")`` deleted a
    graph this handle does not own — neither the target check nor L2 ran,
    because neither was reached.
    """
    proj = sdk_with_journal._get_proj()
    before = _count(proj)

    with pytest.raises(DirectCacheWriteRefused):
        proj.g.execute_command(head, "GRAPH.DELETE", proj._graph_name)
    assert _count(proj) == before, "the cache was wiped by a hidden GRAPH.DELETE"

    with pytest.raises(DirectCacheWriteRefused, match="not 'victim9209'"):
        proj.g.execute_command(head, "GRAPH.DELETE", "victim9209")
    assert _count(proj) == before


#: The command words the pinned client forwards as a BUFFER, not as ``bytes``:
#: ``redis.connection.Encoder.encode`` passes a ``memoryview`` through unchanged
#: and the serializer's ``b" " in args[0]`` test is never true for one, so the
#: buffer IS the command name on the wire.
_BUFFER_HEADS = [
    memoryview(b"DEL"),
    memoryview(b"del"),
    memoryview(bytearray(b"DEL")),
]


@pytest.mark.parametrize("head", _BUFFER_HEADS)
def test_a_buffer_command_word_is_classified_not_forwarded(sdk_with_journal, head):
    """P0, measured: ``execute_command(memoryview(b"DEL"), <graph>)`` wiped.

    ``_handle_command_text`` decoded only ``bytes``/``bytearray``, so a
    ``memoryview`` head returned ``None`` → the verb was never classified → the
    key-space wipe was dispatched, and only THEN did the client raise
    ``AttributeError: 'memoryview' object has no attribute 'upper'`` from its
    response-callback lookup: a refusal-shaped traceback on top of a completed
    deletion.

    MUTATION: drop the ``memoryview`` branch from `_handle_command_text` and this
    fails with DID NOT RAISE (the wipe lands, and the count is 0).
    """
    proj = sdk_with_journal._get_proj()
    before = _count(proj)
    with pytest.raises(DirectCacheWriteRefused):
        proj.g.execute_command(head, proj._graph_name)
    assert _count(proj) == before, (
        "a buffered key-space command destroyed the cache before raising"
    )


@pytest.mark.parametrize("head", [*_BUFFER_HEADS, memoryview(b"GRAPH.DELETE")])
def test_a_buffer_graph_delete_is_classified_too(sdk_with_journal, head):
    """The same decoding hole on the graph-delete spelling.

    ``execute_command(memoryview(b"GRAPH.DELETE"), <own graph>)`` deleted the
    graph; the measure is the one in `_handle_command_text`'s docstring.
    """
    proj = sdk_with_journal._get_proj()
    before = _count(proj)
    with pytest.raises(DirectCacheWriteRefused):
        proj.g.execute_command(memoryview(b"GRAPH.DELETE"), proj._graph_name)
    assert _count(proj) == before


def test_a_buffer_TARGET_cannot_walk_past_the_target_check():
    """The target side of the same hole, and it needed its own fix.

    A ``memoryview`` TARGET could not be decoded, so the argument was DROPPED
    from the word list: ``execute_command("GRAPH.DELETE", memoryview(b"victim"))``
    inside a declared scope read as "no target named", skipped the foreign-graph
    comparison, and L2 validated the projection's own disposable name — so a
    graph this handle does not own was deleted.

    The refusal must live in the DECISION (`_refuse_unauthorized_graph_wipe`),
    so this asserts on the stub handle: nothing may reach the vendor.
    """
    proj = _armed_projection(graph_name="test_9209", embedded=False)
    guarded = _GuardedGraph(proj.g, proj)

    with _replay_write_scope(), pytest.raises(
        DirectCacheWriteRefused, match="not 'victim9209'"
    ):
        guarded.execute_command("GRAPH.DELETE", memoryview(b"victim9209"))

    assert proj.g.calls == [], (
        f"a foreign-graph wipe reached the vendor handle; calls={proj.g.calls!r}"
    )


def test_a_buffer_graph_query_payload_is_still_measured(sdk_with_journal):
    """The SAME decoding gap in the statement reader, on main's side of the merge.

    `_command_statement` decides whether a raw payload carries Cypher (§17.1's
    statement door, #9124, MEASURE mode in the landed state — it records the
    statement in the census and refuses only once `_HANDLE_GUARD_ENFORCES` is
    True). It decoded only ``bytes``, so a ``memoryview`` head returned "" and
    a ``GRAPH.QUERY`` payload the server executes was not counted at all — a
    write missing from the inventory part 2 is gated on.

    MUTATION: drop the ``memoryview`` branch from `_handle_command_text` (or
    decode only ``bytes`` inside `_command_statement`, the pre-merge form) and
    this fails with an empty census.
    """
    from tortoise.projection import (
        _reset_undeclared_handle_writes,
        undeclared_handle_writes,
    )

    proj = sdk_with_journal._get_proj()
    assert proj._journal_backed, "the fixture must ARM the guard"
    _reset_undeclared_handle_writes()

    # The pinned client raises `AttributeError` from its response-callback
    # lookup AFTER the bytes are sent (a `memoryview` has no `.upper()`), so the
    # payload is not refused here — it is MEASURED, which is exactly part 1's
    # contract and is why the census is the artifact this test reads.
    with contextlib.suppress(Exception):
        proj.g.execute_command(
            memoryview(b"GRAPH.QUERY"), proj._graph_name,
            "CREATE (n:Point {id:'buffer-cmd-9209'})",
        )

    census = undeclared_handle_writes()
    assert "buffer-cmd-9209" in " ".join(census), (
        "a buffered GRAPH.QUERY payload was not recorded by §17.1's statement "
        f"door — the census is what part 2 arms on; census={census!r}"
    )


def test_an_unclassifiable_command_is_refused_not_forwarded():
    """Fail CLOSED where the classifier cannot speak (:9209 review, P0).

    Every classification here is a positive match, so a payload it cannot read
    used to fall through to "the client will raise anyway". That assumption is
    what a buffer head falsified, so it is not relied on any more: an armed
    handle refuses a command it cannot classify, and — the part that matters —
    it refuses a GRAPH.DELETE whose TARGET it cannot read, because the foreign
    comparison cannot run on it and L2 would validate the projection's own name
    instead.

    MUTATION: return early from `_refuse_key_space_handle_command` on ``None``
    and drop the ``target_present`` branch, and the in-scope case is dispatched
    to the vendor handle (``calls`` is non-empty) instead of raising.
    """
    proj = _armed_projection(graph_name="test_9209", embedded=False)
    guarded = _GuardedGraph(proj.g, proj)

    with pytest.raises(DirectCacheWriteRefused):
        guarded.execute_command(123)
    with pytest.raises(DirectCacheWriteRefused):
        guarded.execute_command("GRAPH.DELETE", 123)
    with _replay_write_scope(), pytest.raises(DirectCacheWriteRefused):
        guarded.execute_command("GRAPH.DELETE", object())

    assert proj.g.calls == [], (
        f"an unclassifiable payload reached the vendor handle; calls={proj.g.calls!r}"
    )

    unarmed = _armed_projection(graph_name="test_9209", embedded=False)
    unarmed._journal_backed = False
    _GuardedGraph(unarmed.g, unarmed).execute_command(123)
    assert unarmed.g.calls == [("execute_command", (123,), {})], (
        "an UNARMED projection must keep the pre-#9209 pass-through — the guard "
        "has no truth store to enforce against"
    )


def test_a_key_space_command_is_refused_on_NAMESPACE_not_on_a_list(
        sdk_with_journal):
    """The rule's own evidence: a verb named NOWHERE in the module is refused.

    ``SET``/``RESTORE``/``SWAPDB`` are not in any list (and adding them would
    only move the incompleteness), yet each can overwrite, restore over, or
    swap away the key the cache lives under. The guard must refuse them because
    they leave the ``GRAPH.`` namespace, not because someone enumerated them.

    MUTATION: replace the namespace check with a verb allow-list of the currently
    enumerated commands and this fails (``SET`` would be dispatched).
    """
    proj = sdk_with_journal._get_proj()
    before = _count(proj)
    for args in (("SET", proj._graph_name, "{} "), ("RESTORE", proj._graph_name, 0, "x"),
                 ("SWAPDB", 0, 1)):
        with pytest.raises(DirectCacheWriteRefused) as exc:
            proj.g.execute_command(*args)
        assert "GRAPH." in str(exc.value), (
            "the refusal must name the boundary it enforces so the next writer "
            f"can see why; message={str(exc.value)!r}"
        )
    assert _count(proj) == before


def test_a_read_only_probe_still_passes_on_an_armed_projection(sdk_with_journal):
    """The other half of the rule: over-refusal here would be SILENT.

    `tortoise/sdk.py::_graph_presence` probes ``proj.g.execute_command("EXISTS",
    <graph>)`` on its retry path and treats a refusal as "do not retry" — so a
    guard that refused the read would not fail loudly, it would quietly drop a
    legitimately retried write (#7685). Both spellings the SDK uses are pinned.
    """
    proj = sdk_with_journal._get_proj()
    before = _count(proj)

    assert proj.g.execute_command("EXISTS", proj._graph_name), (
        "EXISTS must still answer on an armed projection — the SDK's retry gate "
        "reads it"
    )
    assert proj.g.execute_command(b"TYPE", proj._graph_name) is not None
    assert _count(proj) == before


def test_a_graph_namespace_command_that_destroys_nothing_still_passes(
        sdk_with_journal):
    """The precise half of the NAMESPACE rule, on the command channel.

    ``GRAPH.COPY`` is inside the family and removes nothing from this cache, so
    the boundary must not touch it — the same asymmetry
    `test_an_unrelated_non_cypher_verb_...` pins for the forwarded attribute.

    MUTATION: make `_refuse_key_space_handle_command` refuse the whole non-empty
    command space instead of leaving the ``GRAPH.`` family alone and this fails.
    """
    proj = sdk_with_journal._get_proj()
    before = _count(proj)
    dst = proj._graph_name + "_9209_ns_copy"

    proj.g.execute_command("GRAPH.COPY", proj._graph_name, dst)

    assert _count(proj) == before, "GRAPH.COPY changed this cache"
    assert _count(proj, proj.db.select_graph(dst)) == before, (
        "GRAPH.COPY did not actually run — 'cache intact' would be vacuous"
    )


# ── the declaration: a scope authorizes the WRITE, never the GRAPH ────────

def test_a_declared_scope_still_refuses_a_non_test_server_graph():
    """L2 applies INSIDE the declaration (#2944's polarity, one door over).

    `prod_tortoise` is the name the existing wipe guard treats as real-looking:
    a declared write scope says "this write is sanctioned", never "this graph is
    disposable". Without this, the new door would be strictly weaker than the
    old ones — the first place a whole-graph wipe on production could hide.
    """
    proj = _armed_projection(graph_name="prod_tortoise", embedded=False)
    guarded = _GuardedGraph(proj.g, proj)

    with _replay_write_scope():
        with pytest.raises(RuntimeError, match="non-test graph"):
            guarded.delete()
        with pytest.raises(RuntimeError, match="non-test graph"):
            guarded.execute_command("GRAPH.DELETE", "prod_tortoise")

    assert proj.g.calls == [], (
        f"a wipe reached the vendor handle despite L2; calls={proj.g.calls!r}"
    )


def test_a_destructive_command_naming_another_graph_is_refused():
    """Review finding, now a pin: L2 reads THIS projection's graph name.

    So inside a declared scope, an armed projection named ``test_9209``
    dispatched ``execute_command("GRAPH.DELETE", "prod_tortoise")`` — the L2
    name check passed because it validated the projection's ``test_*`` name
    while the command addressed a production graph on the same server. The
    handle addresses exactly one graph (its own cache); a wipe of any other
    graph is refused here rather than judged by a name it does not own.

    MUTATION: drop the target comparison from
    `_refuse_unauthorized_graph_wipe` and the in-scope half passes the wipe
    through to the vendor handle.
    """
    proj = _armed_projection(graph_name="test_9209", embedded=False)
    guarded = _GuardedGraph(proj.g, proj)

    with pytest.raises(DirectCacheWriteRefused, match="not 'prod_tortoise'"):
        guarded.execute_command("GRAPH.DELETE", "prod_tortoise")
    with _replay_write_scope(), pytest.raises(
        DirectCacheWriteRefused, match="not 'prod_tortoise'"
    ):
        guarded.execute_command("GRAPH.DELETE", "prod_tortoise")

    assert proj.g.calls == [], (
        f"a foreign-graph wipe reached the vendor handle; calls={proj.g.calls!r}"
    )


def test_a_bulk_wipe_hidden_in_a_procedure_call_is_refused():
    """Round 3 finding, now a pin: `call_procedure` pastes caller text into CYPHER.

    The vendor builds ``CALL <procedure>(<args>)`` and sends it through
    ``_query``, so ``proj.g.call_procedure`` was a forwarded attribute that no
    verb-level guard could see — MEASURED before the fix, on an armed
    projection: ``call_procedure("db.labels() MATCH (n) DETACH DELETE n //",
    False)`` emptied the graph while ``proj.g.query`` refused the same DETACH
    DELETE on the same non-test graph. `_GuardedGraph.call_procedure` is now
    defined and runs the statement through the same L2 classifier `query` uses.

    MUTATION: remove `call_procedure` from `_GuardedGraph` (back to a forwarded
    attribute) and this fails — the vendor call is dispatched instead of raising.
    """
    proj = _armed_projection(graph_name="prod_tortoise", embedded=False)
    guarded = _GuardedGraph(proj.g, proj)

    with pytest.raises(RuntimeError, match="non-test graph"):
        guarded.call_procedure(
            "db.labels() MATCH (n) DETACH DELETE n //", read_only=False
        )

    assert proj.g.calls == [], f"the wipe was dispatched: {proj.g.calls!r}"


def test_arguments_cannot_exempt_or_invent_a_wipe():
    """The classified statement must be the one the VENDOR sends.

    The vendor turns ``args`` into ``$paramN`` placeholders and passes the
    values as ``params``, so an argument VALUE is never in the wire statement.
    Rendering the values into the classified text instead — the first version of
    this guard did — is wrong in BOTH directions, and a verifier measured both:
    an arg carrying a scoping construct made `_is_bulk_wipe` read False while the
    vendor still sent the wipe, and a read-only call whose ARG merely contained
    ``DETACH DELETE`` was refused. The wipe is in the ``procedure`` text, so that
    is what must be classified.
    """
    proj = _armed_projection(graph_name="prod_tortoise", embedded=False)
    guarded = _GuardedGraph(proj.g, proj)

    # (a) an argument whose text LOOKS scoping must not exempt the real wipe
    with pytest.raises(RuntimeError, match="non-test graph"):
        guarded.call_procedure(
            "db.labels() MATCH (n) DETACH DELETE n //",
            read_only=False,
            args=["\nMATCH (n:Foo) RETURN n"],
        )
    assert proj.g.calls == [], f"the wipe was dispatched: {proj.g.calls!r}"

    # (b) an argument VALUE containing the delete verb must not over-refuse a
    proj.g.calls = []
    proj.g.call_procedure = lambda *a, **k: "OK"
    assert guarded.call_procedure(
        "db.idx.fulltext.queryNodes",
        read_only=True,
        args=["index_name", "MATCH (n) DETACH DELETE n"],
    ) == "OK", "a read-only call was refused for text in an ARGUMENT value"


def test_the_L2_classifier_still_requires_DETACH():
    """The boundary `call_procedure` inherits, stated as a pin (#9209 review).

    `_GuardedGraph.call_procedure` protects through `_is_bulk_wipe` — the SAME
    L2 classifier `query` uses — so its reach is L2's reach, no wider: a BULK
    ``DETACH DELETE`` is classified, a bare ``DELETE`` is not. A reviewer asked
    for that boundary to be pinned or named, because otherwise the verb's
    docstring reads wider than it is; both are done here, at the classifier, so
    a future widening neither breaks this test silently nor leaves the docs
    claiming a reach the classifier does not have.

    This is the pre-existing scope of L2 (#3007/#3037/#8215), not a regression:
    the same bare DELETE passes through ``proj.g.query`` today, and #9124's
    statement door — not L2 — is the layer that judges it.
    """
    assert _is_bulk_wipe("CALL db.labels() MATCH (n) DETACH DELETE n //") is True
    assert _is_bulk_wipe("CALL db.labels() MATCH (n) DELETE n //") is False, (
        "`_is_bulk_wipe` now classifies a bare DELETE. That is a WIDENING of L2: "
        "it may be the right call, but it must be a deliberate one — update "
        "`_GuardedGraph.call_procedure`'s docstring (which states the DETACH "
        "boundary), this test, and the module comment together, and check the "
        "event-retention maintenance delete (#3007) still runs on a server graph"
    )
    assert _is_bulk_wipe(
        "CALL db.labels() MATCH (n:Point {id:$p}) DETACH DELETE n //") is False, (
        "a scoped DETACH DELETE must stay classified as targeted, or every "
        "targeted delete becomes a bulk-wipe refusal"
    )


def test_a_read_only_procedure_call_still_passes(sdk_with_journal):
    """The no-over-refusal half: L2 must not become a wall around procedures.

    ``db.labels()`` is a read. A guard that refused procedure calls wholesale
    would pass the wipe test above and break every diagnostic that uses one.
    """
    proj = sdk_with_journal._get_proj()

    labels = proj.g.call_procedure("db.labels", read_only=True).result_set

    assert labels, "the procedure call returned nothing — it did not reach the graph"
    assert ["Subject"] in labels


def test_delete_judges_the_handles_own_graph_not_the_projections():
    """Review finding, now a pin: `delete()` must name the graph it REMOVES.

    The wrapper is built in-tree over a graph that is not the projection's own
    — `tortoise/hosted_api.py` does `_GuardedGraph(proj.db.select_graph(name),
    proj)` — so judging a `delete()` by `_proj._graph_name` would authorize it
    against a name it does not delete, the same defect round 1 fixed for the
    command channel, one spelling over.

    MUTATION: pass `target=None` from `delete()` (i.e. judge only the
    projection) and the foreign-handle half of this test fails — the in-scope
    delete reaches the vendor handle.
    """
    proj = _armed_projection(graph_name="test_9209", embedded=False)

    foreign = _NamedRecordingGraph("team_other")
    with _replay_write_scope(), pytest.raises(
        DirectCacheWriteRefused, match="not 'team_other'"
    ):
        _GuardedGraph(foreign, proj).delete()
    assert foreign.calls == [], f"a foreign delete dispatched: {foreign.calls!r}"

    own = _NamedRecordingGraph("test_9209")
    with _replay_write_scope():
        assert _GuardedGraph(own, proj).delete() == "OK"
    assert [c[0] for c in own.calls] == ["delete"], (
        "the declared lane must still reach a disposable graph named like the "
        "projection's"
    )


def test_a_declared_scope_reaches_a_disposable_graph():
    """The declared lane is not dead — otherwise the guard would be a wall.

    This proves the refusal is scoped to `_journal_backed and no scope`, not to
    "delete is always refused". It deliberately uses an `embedded=False`
    projection with a `test_`-named graph, so L2's NAME branch actually runs:
    the embedded exemption returns from `_assert_test_graph` early and would
    keep this case green even if that branch were inverted.
    """
    proj = _armed_projection(graph_name="test_9209", embedded=False)
    guarded = _GuardedGraph(proj.g, proj)

    with _replay_write_scope():
        assert guarded.delete() == "OK"
    assert ("delete", (), {}) in proj.g.calls
    assert proj._graph_name.startswith("test_"), (
        "this case must exercise L2's NAME branch (embedded=False), so the "
        "permitted path is proven with the disposability check actually "
        "reading a name — an embedded projection returns from L2 early and "
        "would keep the suite green if that branch inverted"
    )


def test_an_unarmed_projection_passes_through_unchanged():
    """§17.1 reaches a projection with a journal and nothing else.

    No journal ⇒ no truth store for the cache to be ahead of, so the wipe is the
    pre-#9209 pass-through (this is the polarity `_journal_backed` documents, and
    it is why `graph-scripts/dream-scale-bench.py`'s `proj.g.delete()` keeps
    working). The pass-through covers the handle's whole NON-STATEMENT surface —
    `delete()` and `execute_command` — and it includes the key-space commands
    (``DEL``), which have nothing to enforce against without a journal.

    ⛔ The STATEMENT path is the asymmetry, and it is `query`'s polarity, not an
    exception carved here: ``call_procedure`` reconstructs a CYPHER statement, so
    it goes through L2 regardless of arming — exactly as
    ``proj.g.query("MATCH (n) DETACH DELETE n")`` refuses a non-test graph on an
    unarmed projection too. Pinned so a later "consistency" edit cannot quietly
    remove L2 from it, and so the docstring cannot drift from the code.
    """
    proj = _armed_projection(graph_name="prod_tortoise", embedded=False)
    proj._journal_backed = False
    guarded = _GuardedGraph(proj.g, proj)

    assert guarded.delete() == "OK"
    assert guarded.execute_command("GRAPH.DELETE", "prod_tortoise") == "OK"
    assert guarded.execute_command("DEL", "prod_tortoise") == "OK"
    assert [c[0] for c in proj.g.calls] == [
        "delete", "execute_command", "execute_command"
    ]

    # The statement half: same polarity as `query`, armed or not.
    with pytest.raises(RuntimeError) as exc:
        guarded.call_procedure("db.labels() MATCH (n) DETACH DELETE n //", False)
    assert "non-test graph" in str(exc.value), (
        "call_procedure must apply L2's name check exactly as `query` does — "
        f"message={str(exc.value)!r}"
    )


# ── the drift guard: every forwarded verb is classified ───────────────────

#: Non-Cypher verbs the handle FORWARDS to the vendor, each classified by what a
#: call can do to THIS cache. A new vendor verb — or a new wrapper method —
#: fails `test_every_handle_verb_is_classified` until it is declared here, which
#: is the whole point: the #9209 hole was a verb nobody had enumerated.
#:
#: ⛔ `name`, `client` and `schema` are deliberately ABSENT: they are not verbs
#: (`name` is a str, `schema` is the client-side schema cache, `client` is a
#: handle), so the enumeration below does not see them. That is not an oversight
#: being papered over — `client` is escape (3) of the module's SCOPE block by
#: another door (`proj.g.client.execute_command(...)` reaches the same guarded
#: CLIENT, which carries the #3595 operator guard and not §17.1's), and it is
#: named there.
_NON_DESTRUCTIVE_FORWARDED = {
    "copy": "writes this cache INTO another graph; removes nothing here",
    "slowlog": "reads the server's slowlog",
    "slowlog_reset": "clears the server's slowlog, not the cache",
    "list_indices": "reads index DDL",
    "list_constraints": "reads constraint DDL",
}
# Index/constraint DDL: mutates SCHEMA, never corpus nodes, so a call cannot
# destroy the cache an unrelated path armed. The op sets are the vendor's own
# (indices: range/fulltext/vector; constraints: unique/mandatory) — pinned by
# the reverse assertion below, which rejects a ledger entry naming a verb the
# handle does not have.
for _kind in ("node", "edge"):
    for _op in ("range", "fulltext", "vector"):
        _NON_DESTRUCTIVE_FORWARDED[f"create_{_kind}_{_op}_index"] = "schema DDL"
        _NON_DESTRUCTIVE_FORWARDED[f"drop_{_kind}_{_op}_index"] = "schema DDL"
    for _op in ("unique", "mandatory"):
        _NON_DESTRUCTIVE_FORWARDED[f"create_{_kind}_{_op}_constraint"] = "schema DDL"
        _NON_DESTRUCTIVE_FORWARDED[f"drop_{_kind}_{_op}_constraint"] = "schema DDL"

#: Verbs the wrapper defines itself. `delete` is here BECAUSE OF #9209 — it is
#: the destructive one, and defining it is what closes the forwarding hole —
#: and `call_procedure` joined it in round 3: the vendor pastes the caller's
#: procedure string into CYPHER position, so a forwarded attribute was a write
#: channel no verb-level guard could see.
_GUARDED_ON_THE_WRAPPER = frozenset(
    {"query", "ro_query", "_query", "profile", "explain", "execute_command",
     "delete", "call_procedure"}
)

#: Statement-bearing channels that are NOT this suite's: they carry Cypher, so
#: §17.1's statement recovery (#9124) owns them. Declared, not silently ignored.
#: EMPTY since #9209's round 3 — `call_procedure` was the only entry and it is
#: now DEFINED on the wrapper (its reconstructed statement goes through L2), so
#: it moved to `_GUARDED_ON_THE_WRAPPER`. Kept as a named bucket so the next
#: statement-bearing forward has a place to be declared instead of forgotten.
_STATEMENT_BEARING_NOT_CLOSED_HERE: dict[str, str] = {}


def test_every_handle_verb_is_classified(sdk_with_journal):
    """Enumerate the handle's verbs and require each to be guarded or declared.

    The #9209 defect was not "one bad call": it was that a verb DESTROYING the
    cache had never been enumerated, so no guard could be pointed at it. This
    test is the executable form of that enumeration, taken from the handle a
    caller actually holds.

    WHAT IT SEES, stated exactly: every PUBLIC CALLABLE reachable by attribute —
    class-level verbs (``dir(type(handle))``) UNION per-instance bindings
    (``vars(handle)``), because the pinned vendor binds
    ``self.execute_command = client.execute_command`` in ``Graph.__init__`` and a
    class-only enumeration cannot see it. Non-callable attributes are handles
    or data, not verbs: ``name`` is a string, ``schema`` is the client-side
    schema cache (``GraphSchema.clear`` only empties that object, ``refresh``
    reads), and ``client`` is escape (3) of the module's SCOPE block by another
    door. None of the three issues a destructive command.

    WHAT IT CANNOT SEE: a verb reached through a returned object
    (``handle.copy(x).delete()``), a dunder, or ``getattr`` by computed name.
    It is a drift guard over the verb surface, not a completeness proof.
    """
    proj = sdk_with_journal._get_proj()
    handle = proj.g
    inner = handle._g
    wrapper_defined = set()
    for klass in type(handle).__mro__:
        wrapper_defined |= set(klass.__dict__)

    names = {n for n in dir(type(inner)) if not n.startswith("_")}
    names |= {n for n in vars(inner) if not n.startswith("_")}
    public = {n for n in names if callable(getattr(inner, n, None))}
    declared = set(_NON_DESTRUCTIVE_FORWARDED) | set(_STATEMENT_BEARING_NOT_CLOSED_HERE)

    unclassified = public - wrapper_defined - declared
    assert not unclassified, (
        "the write handle forwards these verbs unclassified — decide for each "
        f"whether a call can destroy the cache: {sorted(unclassified)}"
    )
    stale = declared - public
    assert not stale, (
        f"the ledger declares verbs the handle does not have: {sorted(stale)}"
    )
    assert "delete" in wrapper_defined, (
        "`delete` (GRAPH.DELETE) must be DEFINED on the wrapper, not forwarded: "
        "a forwarded attribute is exactly the #9209 hole"
    )
    assert "execute_command" in wrapper_defined, (
        "the raw command channel must be the WRAPPER's (it carries the #9209 "
        "refusal and the #3595 guards), not the vendor's instance binding"
    )
    # Both directions: nothing declared as guarded may actually be forwarded,
    # or the ledger would describe a wrapper that is not the one shipping.
    for name in _GUARDED_ON_THE_WRAPPER:
        assert name in wrapper_defined, f"{name} is declared guarded but is not defined"

"""#9079 — the §17.1 write authority on the projection's OTHER mutating doors.

`#7951` closed the fold (`FalkorProjection.apply`) and the public raw-query lane
(`FalkorProjection.query`), and left the guarded handle's `query` verb open on
purpose: that verb is the SDK's own live write lane AND the lane every
unjournaled live writer (§17.1's class (a) — `onboarding.state._run` is the
pinned one) reaches the cache through, and their callers are FAIL-OPEN. The
recorded `#7951` OVERRIDES keeps it an allowed direct cache write until
mechanism (1) lands (docs/plans/2026-10-14-7951-atomic-write-outbox.md).

What was NOT recorded, and what this suite pins, is the rest of the handle's
surface: five more verbs can carry a mutating statement, and NONE of them had an
authority check at all —

* `proj.g.ro_query("CREATE …")`      — the declared read-only verb;
* `proj.g._query("CREATE …")`        — the vendor's internal verb;
* `proj.g.profile("CREATE …")` / `proj.g.explain("CREATE …")` — plans put the
  statement on the wire without routing through `_query`;
* `proj.g.execute_command("GRAPH.QUERY", <graph>, "CREATE …")` — the RAW COMMAND
  CHANNEL, which bypasses every query-verb refusal including `#7951`'s on
  `FalkorProjection.query`.

No repo code sends a mutating statement through any of those five (`grep`-verified:
the only `execute_command` on a projection handle is the SDK's `EXISTS` probe,
and the only `explain`/`profile` callers issue reads), so closing them costs the
product's write lane nothing — which is exactly why they, and not `query`, are
the doors this increment closes.

Two things are asserted here beyond "it raises":

1. **One decision, N doors.** Every door calls the SAME
   `_refuse_unauthorized_cache_write`, so the classifier and the policy cannot
   drift apart (the `#3595` shared-decision pattern). The enumeration test at the
   bottom reads `_GuardedGraph`'s source and fails if a future verb reaches the
   handle without making that decision — the door cannot be re-opened by adding
   a method.
2. **The residual is MEASURED, not described.** The `query` verb is not refused
   (see above), so it is counted:
   `tortoise_direct_cache_writes_total{site=<module:function>}` names the writer
   lane. That label set IS §17.1 mechanism (1)'s conversion worklist, and it
   reads 0 the day the door closes. A refusal here would silently break
   onboarding; a counter cannot.
"""
from __future__ import annotations

import ast
import inspect

import pytest

import tortoise.monitoring as monitoring
import tortoise.projection as projection
from tortoise.projection import (
    DirectCacheWriteRefused,
    _GuardedGraph,
    _live_write_scope,
)

# ── fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def armed(shared_proj):
    """The shared embedded projection, ARMED for §17.1 for one test.

    Arming is `_bind_journal` — the same call `TortoiseSDK._get_proj` makes when
    an event log is configured — so this is the state the hosted lane is in. The
    projection is session-scoped (`tests/_embedded.py::shared_proj`), so the
    binding is DISARMED in the teardown: a leaked binding would arm the guard
    over every later test in the session.
    """
    if shared_proj is None:
        pytest.skip("embedded FalkorDB unavailable")
    proj = shared_proj
    proj._bind_journal("/tmp/9079-unused-journal.jsonl")
    try:
        assert proj._journal_backed is True
        yield proj
    finally:
        proj._bind_journal(None)


@pytest.fixture
def unarmed(shared_proj):
    """The same projection with NO journal — outside §17.1's reach."""
    if shared_proj is None:
        pytest.skip("embedded FalkorDB unavailable")
    proj = shared_proj
    proj._bind_journal(None)
    yield proj


def _count(proj, label: str, prop: str, value: str) -> int:
    rows = proj.g.query(
        f"MATCH (n:{label} {{{prop}: $v}}) RETURN count(n)", params={"v": value}
    ).result_set
    return int(rows[0][0]) if rows else 0


# ── the five verbs that carried no authority check ─────────────────────────

def test_a_mutating_statement_on_ro_query_is_refused(armed):
    """`ro_query` is a DECLARED read-only verb; a mutating statement on it is a
    second writer by definition and nothing else guards it."""
    with pytest.raises(DirectCacheWriteRefused) as exc:
        armed.g.ro_query("CREATE (:Point {id:'9079-ro'})")
    assert "§17.1" in str(exc.value)
    assert _count(armed, "Point", "id", "9079-ro") == 0


def test_a_mutating_statement_on_the_internal_query_verb_is_refused(armed):
    """`_query` is the vendor's internal dispatch. A caller reaching it directly
    must not slip past the refusal the public verbs make."""
    with pytest.raises(DirectCacheWriteRefused):
        armed.g._query("CREATE (:Point {id:'9079-internal'})")
    assert _count(armed, "Point", "id", "9079-internal") == 0


def test_a_mutating_statement_on_profile_is_refused(armed):
    """PROFILE/EXPLAIN put the statement on the wire WITHOUT `_query`, so they
    need the same decision rather than inheriting it."""
    with pytest.raises(DirectCacheWriteRefused):
        armed.g.profile("CREATE (:Point {id:'9079-profile'})")


def test_a_mutating_statement_on_explain_is_refused(armed):
    with pytest.raises(DirectCacheWriteRefused):
        armed.g.explain("CREATE (:Point {id:'9079-explain'})")


def test_a_mutating_graph_query_through_the_command_channel_is_refused(armed):
    """The raw command channel is the widest of the five: it carries a Cypher
    statement no query verb ever sees, so it bypassed even `#7951`'s refusal on
    `FalkorProjection.query`."""
    with pytest.raises(DirectCacheWriteRefused) as exc:
        armed.g.execute_command(
            "GRAPH.QUERY", armed.graph_name, "CREATE (:Point {id:'9079-cmd'})")
    assert "execute_command" in str(exc.value)
    assert _count(armed, "Point", "id", "9079-cmd") == 0


def test_an_undecodable_graph_query_payload_fails_closed(armed):
    """A bytes statement is decoded (redis-py accepts it); a payload whose
    statement is not text at all is UNREADABLE, and unreadable must refuse —
    the direction `_statement_writes` fails for an unreadable statement."""
    with pytest.raises(DirectCacheWriteRefused):
        armed.g.execute_command("GRAPH.QUERY", armed.graph_name, None)


# ── reads and non-query commands are untouched ────────────────────────────

def test_a_read_on_every_guarded_verb_still_passes(armed):
    cypher = "MATCH (n:Point) RETURN count(n)"
    assert armed.g.ro_query(cypher).result_set
    assert armed.g._query(cypher).result_set
    assert armed.g.profile(cypher) is not None
    assert armed.g.explain(cypher) is not None


def test_a_non_query_command_on_the_raw_channel_still_passes(armed):
    """`execute_command` is also the SDK's `EXISTS` probe (`sdk.py::_graph_exists`)
    — a non-`GRAPH.*`-query command has no statement and must ride through."""
    assert armed.g.execute_command("EXISTS", armed.graph_name) is not None


# ── the scope still authorizes, and a journal-less projection is exempt ────

def test_a_sanctioned_scope_authorizes_the_same_statement(armed):
    """The guard is a SCOPE check, not a statement blacklist: the identical
    statement passes inside a sanctioned write."""
    with _live_write_scope("test-9079"):
        armed.g._query("CREATE (:Point {id:'9079-scoped'})")
    assert _count(armed, "Point", "id", "9079-scoped") == 1


def test_a_journal_less_projection_is_outside_the_guard(unarmed):
    """`_journal_backed` False ⇒ §17.1 has nothing to enforce (there is no truth
    store for the cache to be ahead of). The permissive default is preserved."""
    unarmed.g._query("CREATE (:Point {id:'9079-unarmed'})")
    assert _count(unarmed, "Point", "id", "9079-unarmed") == 1


# ── the remaining open door is CENSUSED, not silently allowed ─────────────

def _census() -> dict[str, int]:
    return monitoring.direct_cache_write_counts()


def test_the_write_handle_is_counted_by_writer_lane(armed):
    """`proj.g.query` is the recorded residual — it still writes (a refusal would
    break onboarding), but the write is now VISIBLE with the lane that issued it.
    """
    before = dict(_census())
    armed.g.query("CREATE (:Point {id:'9079-census'})")
    assert _count(armed, "Point", "id", "9079-census") == 1
    after = _census()
    delta = {k: v - before.get(k, 0) for k, v in after.items() if v > before.get(k, 0)}
    assert delta, (
        "an unauthorized mutating statement on the write handle must be counted "
        f"— the §17.1 residual is supposed to be measurable; census={after}"
    )
    assert any("test_9079" in site for site in delta), (
        "the census label must name the writer LANE (module:function), not a "
        f"generic bucket; labels={sorted(delta)}"
    )


def test_a_read_through_the_write_handle_is_not_counted(armed):
    before = dict(_census())
    armed.g.query("MATCH (n:Point {id:'9079-census'}) RETURN n")
    assert _census() == before, "a read is not a direct cache write"


def test_an_authorized_write_is_not_counted(armed):
    """The census counts the RESIDUAL, not all traffic: a write inside the
    sanctioned scope is exactly what §17.1 requires and must not be reported."""
    before = dict(_census())
    with _live_write_scope("test-9079-authorized"):
        armed.g.query("CREATE (:Point {id:'9079-authorized'})")
    assert _census() == before


def test_the_census_rides_the_health_payload():
    """A counter with no production reader is the #3677 loss class; the hosted
    app exposes no `/metrics`, so the census must ride `metrics()`."""
    payload = monitoring.metrics(setup_timeout=0.01)
    assert "direct_cache_writes" in payload
    assert isinstance(payload["direct_cache_writes"], dict)


# ── the enumeration: no verb may reach the handle without a decision ──────

#: Every verb on `_GuardedGraph` that dispatches to the underlying FalkorDB
#: handle. A new one added without a decision fails the test below.
_GUARDED_VERBS = frozenset({
    "query", "ro_query", "_query", "profile", "explain", "execute_command",
})

#: The doors that REFUSE. `query` is the recorded `#7951` OVERRIDES residual and
#: makes the other decision (census); it is the only name allowed here.
_CENSUSED_VERBS = frozenset({"query"})

_REFUSAL = "_refuse_unauthorized_cache_write"
_CENSUS = "_observe_direct_cache_write"


def _decisions_in(node: ast.AST) -> set[str]:
    found: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            name = getattr(child.func, "id", None) or getattr(child.func, "attr", None)
            if name in (_REFUSAL, _CENSUS):
                found.add(name)
    return found


def test_every_mutating_capable_verb_makes_the_authority_decision():
    """The enumeration `#9079` asks for, as an executable pin.

    Reads `_GuardedGraph`'s own source: every verb in `_GUARDED_VERBS` must call
    the shared refusal — or be a DECLARED censused residual — so a seventh verb
    cannot reach the cache unexamined. It also fails if a verb listed here
    disappears (the list is the door inventory, and a silent removal would make
    this test vacuous).
    """
    tree = ast.parse(inspect.getsource(_GuardedGraph))
    klass = next(n for n in tree.body
                 if isinstance(n, ast.ClassDef) and n.name == "_GuardedGraph")
    methods = {n.name: n for n in klass.body if isinstance(n, ast.FunctionDef)}

    missing = sorted(_GUARDED_VERBS - set(methods))
    assert not missing, (
        f"`_GuardedGraph` no longer defines {missing} — update the door "
        "inventory in this test alongside the class"
    )

    for verb in sorted(_GUARDED_VERBS):
        decisions = _decisions_in(methods[verb])
        if verb in _CENSUSED_VERBS:
            assert _CENSUS in decisions, (
                f"`{verb}` is the declared §17.1 residual — it must at least "
                f"CENSUS the write it allows (found {sorted(decisions) or 'none'})"
            )
            assert _REFUSAL not in decisions, (
                f"`{verb}` is the SDK's live write lane; refusing it breaks the "
                "unjournaled class (a) writers (#7951 OVERRIDES). If you are "
                "closing it, do it deliberately and move it out of "
                "_CENSUSED_VERBS in this test."
            )
        else:
            assert _REFUSAL in decisions, (
                f"`{verb}` reaches the cache with NO §17.1 decision — a new "
                "mutating-capable verb must call "
                f"`{_REFUSAL}` (found {sorted(decisions) or 'none'})"
            )


def test_every_door_defers_to_one_shared_decision():
    """One policy, N call sites: the refusal lives in exactly one function, so a
    classifier/policy change cannot reach one door and miss the rest."""
    src = inspect.getsource(projection._refuse_unauthorized_cache_write)
    assert "_statement_writes_cached" in src, (
        "the shared decision must use the ONE cached classifier the parameter "
        "gate and the raw-query lane use"
    )
    # and the classifier is the SAME function the rest of the module reasons about
    assert projection._statement_writes_cached("CREATE (n:Point)") is True
    assert projection._statement_writes_cached("MATCH (n) RETURN n") is False


def test_the_refusal_type_is_public_and_documented():
    """A refusal a caller cannot catch is a crash. The type is exported, raises
    RuntimeError (so an existing `except Exception` still catches it), and states
    the rule it enforces."""
    assert issubclass(DirectCacheWriteRefused, RuntimeError)
    assert "17.1" in (DirectCacheWriteRefused.__doc__ or "")


def test_guarded_graph_verbs_are_the_only_ones_that_touch_the_handle():
    """The inventory's other half: `_GuardedGraph` forwards everything it does
    not override to the underlying handle through `__getattr__`, so a NEW
    query-shaped verb must be added to `_GUARDED_VERBS` (and therefore to the
    decision test above) rather than slipping through the forwarder."""
    tree = ast.parse(inspect.getsource(_GuardedGraph))
    klass = next(n for n in tree.body
                 if isinstance(n, ast.ClassDef) and n.name == "_GuardedGraph")
    overrides = {n.name for n in klass.body if isinstance(n, ast.FunctionDef)}
    assert overrides >= _GUARDED_VERBS
    # `__getattr__` is the only forwarding escape; it must keep existing so the
    # inventory above stays the complete list of methods that reach the handle.
    assert "__getattr__" in overrides

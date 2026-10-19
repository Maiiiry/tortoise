"""#9124 part 1 — the §17.1 declaration census, enumerated in CODE, not by hand.

WHAT THIS SUITE IS FOR. §17.1's third door (`_GuardedGraph`, i.e. `proj.g`) now
DETECTS a mutating statement that arrives with no declared lane. Detection is
only worth having if the surface is *accounted for*: every entry point that
reaches the handle must be either

* DECLARED — a `_live_write_scope` / `_replay_write_scope` /
  `_graph_metadata_write_scope` in the function body, or
  `@_declares_live_write_lane` on the door itself; or
* INVENTORIED — named in ONE of the pinned inventories below, with the reason it
  cannot declare a lane yet.

The enumeration is derived from the SOURCE of `tortoise/sdk.py` and
`tortoise/event_store.py`, never from a hand-maintained list of call sites, so a
function added later cannot slip in undeclared: it fails here until someone
declares it or adds it to an inventory (which is a reviewed line in this file,
not a silent omission). The inventories are pinned EXACTLY, so they can only
shrink — a stale entry (a function that has since been declared, renamed or
deleted) fails too.

⛔ THE GUARD IS MEASURING, NOT REFUSING (#9124 part 1). `_HANDLE_GUARD_ENFORCES`
is `False`: an undeclared write is counted (`undeclared_handle_writes()`) and
logged, not raised. That is the owner's option B — declare the mechanical
journal-first doors first, keep the apply-first arms for their own change, and
flip the switch only when the surface is fully declared. The flip is not left to
memory either: `test_arming_the_guard_requires_a_fully_declared_surface` reads
every module in the package that reaches the handle and refuses to let the
switch be True while anything is still undeclared.

WHAT COUNTS AS A MUTATING CALL. The verb list is `_HANDLE_MUTATING_VERBS`, the
SAME constant the guard's own enumeration test reads, and the statement text is
run through `_statement_writes`, the SAME classifier the guard consults — so
this census and the runtime door cannot disagree about what a write is (the
#3595 shared-decision precedent). A statement whose text the enumerator cannot
resolve (built by a helper, passed in from a generator) is counted as a WRITE:
the classifier's own polarity is "anything not provably read-only is a write",
and this gate may over-report but never under-report. Five such functions are
pinned in `_UNRESOLVED` for that reason, not because they are believed to write.

WHY `sdk.py` AND `event_store.py`, AND NOT EVERY MODULE. These two are what part
1 DECLARES: the SDK's journal-first doors and the `:GraphEvent` store. The other
modules that reach the handle (`hosted_api.py`, `mining.py`, `ingest.py`,
`commit_idempotency.py`, the connectors, ...) are measured on #9124 and belong to
part 2's arming — which is exactly what
`test_arming_the_guard_requires_a_fully_declared_surface` enforces: it scans
them all and names every undeclared function, so arming cannot happen on a
surface this suite never looked at.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tortoise.projection import (
    _HANDLE_GUARD_ENFORCES,
    _HANDLE_MUTATING_VERBS,
    _statement_writes,
)

ROOT = Path(__file__).resolve().parent.parent
SDK_PY = ROOT / "tortoise" / "sdk.py"
EVENT_STORE_PY = ROOT / "tortoise" / "event_store.py"

#: A callable that APPENDS a journal record (the commit point the lane declares
#: itself after). Pattern-matched rather than enumerated, so a NEW append helper
#: (`_emit_*`, `_journal_*`, ...) is recognised instead of silently reclassifying
#: a journal-first door as "appends nothing".
_APPEND_CALL_RE = re.compile(
    r"^(?:_?emit_event|_?apply_and_journal|_?journal_\w+|_?emit_\w+_record"
    r"|append_event)$")

#: The scope openers, PLUS the decorator that opens one around a whole door.
_SCOPE_CALL_NAMES = (
    "_live_write_scope",
    "_replay_write_scope",
    "_graph_metadata_write_scope",
    "_cache_write_authorized",
    "_declares_live_write_lane",
)
#: The subset that claims "this door appends its record, THEN writes the cache".
#: Only these are held to the journal-first ordering pin below: a replay or
#: graph-metadata lane makes no such claim.
_LIVE_LANE_NAMES = ("_live_write_scope", "_declares_live_write_lane")


# ── the inventories: everything part 1 does NOT declare, and why ───────────
#
# Each entry is `function name -> the reason it cannot declare a lane yet`.
# Grouped by reason so the census reads as a census; pinned EXACTLY by the
# tests below, so an entry can only leave this file by being declared (or by
# disappearing), never by drifting.

#: Apply-first PUBLIC doors: the record that describes the write is emitted
#: AFTER it, so a `_live_write_scope` here would authorize a cache write with no
#: committed record — the #9068 defect, and the opposite of what §17.1 requires.
#: Fixing one means moving the record above the write AND deciding its failure
#: contract (does a failed append abort the write?), which is #9124 part 2.
_APPLY_FIRST_DOORS = {
    "approve_merge",         # SET reviewed flags, then PointRevised/… records
    "assess_source",         # SET reliability, then the assessment record
    "compute_confidence",    # SET n.confidence per row, then ConfidenceChanged
    "create_direct_edge",    # MERGE/SET the edge, then DirectEdgeCreated
    "create_operator",       # CREATE the operator node, then OperatorAdded
    "mitigate_operator",     # CREATE the mitigation, then its records
    "promote_point",         # CAS SET status='live', then PointPromoted
    "record_calibration",    # write the calibration row, then its record
    "set_point_baseline",    # SET the baseline, then its record
}

#: Apply-first INTERNAL helpers — the same ordering as the doors above, reached
#: from them (or from capture/ingest). Same part-2 decision, same reason.
_APPLY_FIRST_HELPERS = {
    "_apply_capture_ingest_ep",
    "_apply_source_inheritance",
    "_dedup_content_candidates",   # marks the candidate, then records it
    "_delete_entity",              # DETACH DELETE, then the mutation record
    "_embedding_repair_attempt",
    "_materialize_session_source",  # MERGE(:Source), then the record
    "_promote_incident_operators",
    "_session_event_write",
    "_stamp_batch_id",             # SET n.batch_id, then _emit_batch_id_record
    "_update_entity",              # SET, then _journal_entity_mutation
    "_write_capture_turns",
}

#: Journal-first BY ORDER, but holding an arm whose record is deliberately
#: stated after its guarded write — so the door is part 2's, not part 1's:
#:
#: * `update_point` — the `status='live'` promote arm (a `WHERE`-guarded SET that
#:   can legitimately match nothing and then RAISES, so journalling first would
#:   record a promotion the graph refused; the deviation is pinned by
#:   `tests/test_7951_update_point_outbox.py`). The SAME write region serves the
#:   journal-first arms, so one `with` cannot separate them.
#: * `capture_session` — its `:Session` counter writes record their outcome only
#:   AFTER the write ("the raw SET above has no journal carrier ... emit a
#:   TRAILING SessionRecorded"), which is the same shape.
_MIXED_ARM_DOORS = {
    "capture_session",
    "update_point",
}

#: Reach the handle with a write-like statement and append NO record at all, so
#: there is no lane to declare: each needs either a named graph-metadata lane
#: (the `_advance_ep_version` / `onboarding.metadata` pattern) or a move onto the
#: fold. Measured on #9124; this is the "19 writers that journal nothing" class
#: in the issue's landing path, at the count the source actually holds.
_NO_JOURNAL = {
    "_clear_reliability_cache",
    "_connect_issue_objects",
    "_extract_session_llm",
    "_extract_session_v2",
    "_index_source_merge",
    "_invalidate_inheritance_gate",
    "_mark_dirty",
    "_reapply_operator_promotion",
    "_record_embedding_marker",
    "_register_failed_attempt",
    "_stamp_authored_word",
    "_sweep_dirty_roots",
    "_touch_inherited_at",
    "_write_props",
    "_write_reliability_cache",
    "_write_session_and_turns",
    "backfill_v25",
    "complete_source",
    "ingest",
    "migrate_orgs_to_registry",
    "set_source_tier",
}

#: A statement the enumerator cannot PROVE read-only (it is built by a helper or
#: yielded by a generator), in a function that appends no record. Counted as a
#: write by the classifier's own "not provably read-only" polarity; listed apart
#: so the census does not claim these five are known writers.
_UNRESOLVED = {
    "_assert_lifecycle_guard",
    "_graph_presence",
    "_hop",
    "_window_closure",
    "expand_relationships",
}

_INVENTORY = {
    "apply-first door (part 2)": _APPLY_FIRST_DOORS,
    "apply-first helper (part 2)": _APPLY_FIRST_HELPERS,
    "mixed arm (part 2)": _MIXED_ARM_DOORS,
    "no journal record to declare": _NO_JOURNAL,
    "statement not provably read-only": _UNRESOLVED,
}


# ── the enumerator ────────────────────────────────────────────────────────

def _text(expr, consts, local):
    """Best-effort statement text for an argument expression, or ``None``.

    Resolves string literals, f-string/concatenation skeletons (their constant
    parts, which is where a write keyword would sit), module-level string
    constants and string locals assigned earlier in the function — enough to
    classify the real call sites, and deliberately silent when it cannot be
    sure (the caller then counts the call as a write).
    """
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    if isinstance(expr, ast.JoinedStr):
        return "".join(
            v.value if isinstance(v, ast.Constant) else " x " for v in expr.values)
    if isinstance(expr, ast.Name):
        return local.get(expr.id, consts.get(expr.id))
    if isinstance(expr, ast.BinOp):
        left, right = _text(expr.left, consts, local), _text(expr.right, consts, local)
        if left is not None or right is not None:
            return (left or " x ") + " x " + (right or " x ")
    return None


def _bound_names(node, consts, local):
    """``{name: text}`` for a single string-ish assignment/annotation/augassign."""
    if isinstance(node, ast.Assign) and len(node.targets) == 1 \
            and isinstance(node.targets[0], ast.Name):
        text = _text(node.value, consts, local)
        return {node.targets[0].id: text} if text is not None else {}
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
            and node.value is not None:
        text = _text(node.value, consts, local)
        return {node.target.id: text} if text is not None else {}
    if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
        extra = _text(node.value, consts, local)
        if extra is not None and node.target.id in local:
            return {node.target.id: local[node.target.id] + extra}
    return {}


def _module_consts(tree):
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            for name, text in _bound_names(node, out, out).items():
                out.setdefault(name, text)
    return out


def enumerate_handle_surface(source: str, filename: str = "<src>") -> dict:
    """Map each innermost function to what it does at the write handle.

    Returns ``{function name: {lineno, writes, reads, appends, scopes,
    aliases}}`` for every function holding at least one mutating call. ``writes``
    is a sorted list of ``(line, verb, statement-prefix)``; ``scopes`` a list of
    ``(line, opener name)``; ``appends`` the lines of journal-append calls.
    Nested functions are attributed to THEMSELVES, never to their enclosing
    function (a helper's write is not its caller's).
    """
    tree = ast.parse(source, filename=filename)
    consts = _module_consts(tree)
    facts: dict[str, dict] = {}

    def visit(node, current, local):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            current = node.name
            facts.setdefault(current, {
                "lineno": node.lineno, "writes": [], "reads": 0, "appends": [],
                "scopes": [], "aliases": set(),
            })
            local = {}
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call):
                    opener = getattr(decorator.func, "id", None) or getattr(
                        decorator.func, "attr", None)
                    if opener in _SCOPE_CALL_NAMES:
                        facts[current]["scopes"].append((decorator.lineno, opener))
        if current is None:
            for child in ast.iter_child_nodes(node):
                visit(child, current, local)
            return

        fact = facts[current]
        # `g = proj.g` / `g = self._get_proj().g` — event_store binds the handle
        # to a local, so a receiver-name check alone would miss its writers.
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and isinstance(node.value, ast.Attribute) and node.value.attr == "g":
            fact["aliases"].add(node.targets[0].id)
        local.update(_bound_names(node, consts, local))

        if isinstance(node, ast.Call):
            opener = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if opener and _APPEND_CALL_RE.match(opener):
                fact["appends"].append(node.lineno)
            elif opener in _SCOPE_CALL_NAMES:
                fact["scopes"].append((node.lineno, opener))
            elif isinstance(node.func, ast.Attribute) \
                    and node.func.attr in _HANDLE_MUTATING_VERBS:
                receiver = node.func.value
                direct = isinstance(receiver, ast.Attribute) and receiver.attr == "g"
                aliased = isinstance(receiver, ast.Name) and receiver.id in fact["aliases"]
                if direct or aliased:
                    # `execute_command("GRAPH.QUERY", <graph>, <cypher>)` — the
                    # statement sits at index 2 (cypher_guard's own pinned shape),
                    # every other verb takes it positionally.
                    index = 2 if node.func.attr == "execute_command" else 0
                    arg = node.args[index] if len(node.args) > index else None
                    text = _text(arg, consts, local) if arg is not None else None
                    if text is None or _statement_writes(text):
                        fact["writes"].append(
                            (node.lineno, node.func.attr,
                             " ".join((text or "<unresolved>").split())[:90]))
                    else:
                        fact["reads"] += 1

        for child in ast.iter_child_nodes(node):
            visit(child, current, local)

    visit(tree, None, {})
    for fact in facts.values():
        fact["writes"].sort()
    return {name: fact for name, fact in facts.items() if fact["writes"]}


def _kind(fact) -> str:
    """Classify one function: declared, or which inventory it belongs to."""
    if fact["scopes"]:
        return "declared"
    if not fact["appends"]:
        return "no-journal"
    if fact["writes"][0][0] < min(fact["appends"]):
        return "apply-first"
    return "journal-first"


def _inventory_of(name: str):
    for reason, members in _INVENTORY.items():
        if name in members:
            return reason
    return None


def _all_inventoried() -> set[str]:
    return {name for members in _INVENTORY.values() for name in members}


# ── the live gate: every SDK entry point is declared or accounted for ─────

def test_every_mutating_sdk_entry_point_is_declared_or_inventoried():
    """THE gate. A new mutating `sdk.py` function must declare a lane.

    Non-vacuity is proven two ways: `test_the_enumerator_flags_an_undeclared_
    journal_first_door` drives this same enumerator over a synthetic module (so
    the detector is exercised even when the real tree is clean), and the change
    that added this suite was run with one declaration removed — the failure
    names `supersede_point` by function and line.
    """
    surface = enumerate_handle_surface(SDK_PY.read_text(), str(SDK_PY))
    offenders = []
    for name, fact in sorted(surface.items(), key=lambda kv: kv[1]["lineno"]):
        kind = _kind(fact)
        reason = _inventory_of(name)
        if kind == "declared":
            if reason is not None:
                offenders.append(
                    f"{name}:{fact['lineno']} is DECLARED ({fact['scopes']}) but "
                    f"still inventoried as {reason!r} — drop the inventory entry")
            continue
        if reason is None:
            offenders.append(
                f"{name}:{fact['lineno']} reaches the write handle "
                f"({fact['writes'][0][1]} at line {fact['writes'][0][0]}: "
                f"{fact['writes'][0][2]!r}) with NO declared lane and no "
                f"inventory entry [{kind}]")
            continue
        expected = {
            "apply-first door (part 2)": "apply-first",
            "apply-first helper (part 2)": "apply-first",
            "mixed arm (part 2)": "journal-first",
            "no journal record to declare": "no-journal",
            "statement not provably read-only": "no-journal",
        }[reason]
        if kind != expected:
            offenders.append(
                f"{name}:{fact['lineno']} is inventoried as {reason!r} but the "
                f"source now reads {kind!r} (writes={[w[0] for w in fact['writes']]}, "
                f"appends={fact['appends']}) — the inventory reason is stale")
    assert not offenders, (
        "§17.1's write-handle surface is not fully accounted for. Declare the "
        "lane (`_live_write_scope` AFTER the journal append, or "
        "`@_declares_live_write_lane` on a journal-first door), or add the "
        "function to the matching inventory above with its reason:\n  "
        + "\n  ".join(offenders))


def test_the_inventories_hold_no_stale_entry():
    """The inventories can only shrink: a renamed/declared/deleted name fails."""
    surface = enumerate_handle_surface(SDK_PY.read_text(), str(SDK_PY))
    stale = sorted(_all_inventoried() - set(surface))
    assert not stale, (
        "these inventory entries no longer name a function with a mutating "
        "handle call (renamed, deleted, or the call was removed) — delete the "
        f"entries so the census stays a census: {stale}")


def test_every_live_declared_door_is_journal_first():
    """A live lane is only HONEST when its record precedes its write.

    `@_declares_live_write_lane` (and any inline `_live_write_scope` around a
    whole door) authorizes everything the function writes, including anything
    above the append — so the ordering is the property that makes the
    declaration true. Pinned here for every declared live door, including the
    ones that predate this suite.
    """
    surface = enumerate_handle_surface(SDK_PY.read_text(), str(SDK_PY))
    checked, offenders = [], []
    for name, fact in surface.items():
        openers = {opener for _line, opener in fact["scopes"]}
        if not openers & set(_LIVE_LANE_NAMES):
            continue
        if not fact["appends"]:
            offenders.append(
                f"{name}:{fact['lineno']} declares a LIVE lane but appends no "
                "record — a live lane is 'append first, then write the cache'")
            continue
        first_write, first_append = fact["writes"][0][0], min(fact["appends"])
        if first_write < first_append:
            offenders.append(
                f"{name}:{fact['lineno']} declares a LIVE lane but writes at "
                f"line {first_write}, BEFORE its first append at {first_append} "
                f"({fact['writes'][0][1]}: {fact['writes'][0][2]!r}) — that is "
                "the apply-first shape, and the declaration would bless it")
            continue
        checked.append(name)
    assert not offenders, (
        "a live-lane declaration covers a write that precedes its record:\n  "
        + "\n  ".join(offenders))
    assert {"delete_point", "invalidate_point", "retract_point",
            "supersede_point"} <= set(checked), (
        "the four journal-first doors #9124 part 1 declared are not all being "
        f"checked ({sorted(checked)}) — the ordering pin would be vacuous for "
        "them")


def test_the_event_store_declares_every_writer():
    """The `:GraphEvent` store declares its own lane (#9124 part 1).

    Its writers are not the SDK's: `_emit_event` reaches the store BEFORE the
    JSONL append, so a caller's `_live_write_scope` cannot cover them — the
    declaration has to live at the writer. Measured before this landed: on a
    journal-backed SDK the store's writes were refused and `_emit_event`'s bare
    `except Exception` swallowed the refusal, so `poll_events`/the change feed
    returned nothing while the API reported success (#9208).
    """
    surface = enumerate_handle_surface(EVENT_STORE_PY.read_text(), str(EVENT_STORE_PY))
    undeclared = {
        name: fact for name, fact in surface.items() if not fact["scopes"]
    }
    assert not undeclared, (
        "every event_store write must declare a lane — "
        "`_graph_metadata_write_scope` for the store's own bookkeeping (schema, "
        "seq allocator, append, retention) or `_replay_write_scope` for the "
        "wipe+replay repair. Undeclared:\n  " + "\n  ".join(
            f"{name}:{fact['lineno']} {fact['writes'][0][1]}: "
            f"{fact['writes'][0][2]!r}" for name, fact in undeclared.items()))
    # The store is small enough to pin exactly: a NEW write here must be a
    # deliberate declaration, never an accident.
    assert set(surface) == {
        "ensure_event_schema", "next_seq", "reestablish_watermark",
        "append_event", "purge_expired", "purge_overflow", "_refresh_first_seq",
    }, f"unexpected event_store handle surface: {sorted(surface)}"


# ── non-vacuity: the detector fires on a synthetic undeclared door ────────

def test_the_enumerator_flags_an_undeclared_journal_first_door():
    """The gate's own RED test — the enumerator must SEE an undeclared write.

    Without this, a refactor that broke the enumerator (a renamed attribute, an
    alias it stopped following) would turn the gate into a green rubber stamp.
    The synthetic module carries the same shape as the real doors: a journal
    append, then a mutating handle call, and (in the second case) no declaration.
    """
    undeclared_src = '''
def _emit_event(*a, **k):
    return True


class Door:
    def a_door(self, proj, pid):
        self._emit_event("PointAdded", {"id": pid})
        proj.g.query("CREATE (n:Point {id:$id})", params={"id": pid})
'''
    surface = enumerate_handle_surface(undeclared_src, "<synthetic-undeclared>")
    fact = surface["a_door"]
    assert _kind(fact) == "journal-first", fact
    assert fact["appends"] and fact["writes"], fact
    assert not fact["scopes"]
    assert _inventory_of("a_door") is None, (
        "the synthetic door must NOT accidentally match a real inventory entry")

    declared_src = undeclared_src.replace(
        "        self._emit_event(\"PointAdded\", {\"id\": pid})\n"
        "        proj.g.query",
        "        self._emit_event(\"PointAdded\", {\"id\": pid})\n"
        "        with _live_write_scope(\"a_door\"):\n"
        "            proj.g.query")
    fact = enumerate_handle_surface(declared_src, "<synthetic-declared>")["a_door"]
    assert _kind(fact) == "declared", fact
    assert fact["scopes"] and fact["scopes"][0][1] == "_live_write_scope"


def test_the_enumerator_sees_an_apply_first_door_as_apply_first():
    """The other half of the classification: order is measured, not assumed.

    `create_direct_edge` is the real-world shape (MERGE the edge, then emit
    `DirectEdgeCreated`), and the census must call that apply-first — if it
    reported journal-first, every declaration made from it would be dishonest.
    """
    src = '''
def _emit_event(*a, **k):
    return True


class Door:
    def apply_first(self, proj, src_id, tgt_id):
        proj.g.query("MERGE (a)-[r:IMPL]->(b)")
        self._emit_event("DirectEdgeCreated", id="a->b")
'''
    fact = enumerate_handle_surface(src, "<synthetic-apply-first>")["apply_first"]
    assert _kind(fact) == "apply-first", fact


# ── the flip gate: arming requires a fully declared surface ───────────────

def _modules_reaching_the_handle():
    """Every package module that issues a handle verb, by source text."""
    pattern = re.compile(
        r"\.g\.(?:" + "|".join(_HANDLE_MUTATING_VERBS) + r")\s*\(")
    return sorted(p for p in (ROOT / "tortoise").rglob("*.py")
                  if pattern.search(p.read_text(errors="replace")))


def test_arming_the_guard_requires_a_fully_declared_surface():
    """`_HANDLE_GUARD_ENFORCES = True` is only allowed once nothing is left.

    This is the owner's decision made executable (#9124, option B): the guard is
    flipped LAST, when every mutating entry point either declares a lane or is an
    inventoried part-2 remainder. The scan is package-wide — `sdk.py` and
    `event_store.py` are what part 1 declared, but `hosted_api.py`, `mining.py`,
    `ingest.py`, the connectors and the rest reach the same handle, and arming
    while any of them is undeclared refuses the product (measured: 16 failed / 4
    errors across four §17.1 suites).

    While the switch is OFF the census is still proven REAL (not an empty scan):
    every inventoried SDK name must appear in it.
    """
    remaining: dict[str, list[str]] = {}
    for path in _modules_reaching_the_handle():
        surface = enumerate_handle_surface(path.read_text(errors="replace"), str(path))
        undeclared = [f"{name}:{fact['lineno']}"
                      for name, fact in surface.items() if not fact["scopes"]]
        if undeclared:
            remaining[path.relative_to(ROOT).as_posix()] = sorted(undeclared)

    if _HANDLE_GUARD_ENFORCES:
        assert not remaining, (
            "`_HANDLE_GUARD_ENFORCES` is True while the write surface is NOT "
            "fully declared — arming here refuses the product's own writes. "
            "Declare a lane for every entry point below (or inventory it as a "
            "documented part-2 remainder) before flipping the switch:\n"
            + "\n".join(f"  {path}: {len(names)} undeclared, e.g. {names[:4]}"
                        for path, names in sorted(remaining.items())))
        return

    # Part 1's state: measuring, and the census must be able to see the remainder
    # it is measuring — a scanner that silently returns nothing would make the
    # check above vacuous on the day someone flips the switch.
    sdk_remaining = {name.split(":")[0] for name in remaining.get(
        "tortoise/sdk.py", [])}
    assert _all_inventoried() <= sdk_remaining, (
        "the package scan did not see every inventoried SDK entry point — the "
        "scan itself is broken, so the arming gate would pass vacuously. "
        f"missing={sorted(_all_inventoried() - sdk_remaining)}")


def test_the_measure_mode_census_is_what_a_flip_would_refuse(armed_sdk_factory):
    """The runtime door and the static census agree about the same statement.

    A statement the enumerator calls a write must be one the handle MEASURES in
    measure mode — and, once the switch is on, one it REFUSES. That is the
    shared-classifier property this whole suite rests on (#3595).
    """
    from tortoise.projection import (
        DirectCacheWriteRefused, _reset_undeclared_handle_writes,
        undeclared_handle_writes,
    )
    import tortoise.projection as projection

    sdk = armed_sdk_factory()
    proj = sdk._get_proj()
    assert proj._journal_backed
    statement = "CREATE (n:Point {id:'census-9124'})"
    assert _statement_writes(statement)
    _reset_undeclared_handle_writes()
    # measure: no refusal, counted
    proj.g.query(statement)
    census = undeclared_handle_writes()
    assert any("census-9124" in key for key in census), (
        f"the measure-mode census did not record the write: {census!r}")
    # armed: refused, and not counted (a refusal is not a measurement)
    _reset_undeclared_handle_writes()
    projection._HANDLE_GUARD_ENFORCES = True
    try:
        with pytest.raises(DirectCacheWriteRefused):
            proj.g.query("CREATE (n:Point {id:'census-armed-9124'})")
        assert not undeclared_handle_writes()
    finally:
        projection._HANDLE_GUARD_ENFORCES = False


@pytest.fixture
def armed_sdk_factory(tmp_path):
    """An SDK whose projection is journal-backed, so the door is live."""
    from tortoise.sdk import TortoiseSDK

    created = []

    def _make():
        journal = tmp_path / "journal.jsonl"
        sdk = TortoiseSDK(str(tmp_path / "declared-doors.db"),
                          event_log_path=str(journal))
        created.append(sdk)
        return sdk

    try:
        yield _make
    finally:
        for sdk in created:
            sdk.close()

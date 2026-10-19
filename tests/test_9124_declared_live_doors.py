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
the classifier's own polarity is "anything not provably read-only is a write".
That OVER-report polarity is what `_text` implements — every unresolved part of
an f-string or a `+` skeleton returns `None` rather than a placeholder, because
a placeholder lets `f"MATCH (n:Point {{id:$id}}) {action}"` read as a non-write
and drop the whole function out of the census. Five such functions are pinned in
`_UNRESOLVED` for that reason, not because they are believed to write.

WHICH EXPRESSION IS THE HANDLE. `proj.g`, `self._get_proj().g` (any attribute
chain whose LAST attribute is `g`), `getattr(x, "g")`, and any name bound from
one of those by `h = proj.g`, `h: Any = proj.g`, `h, _ = proj.g, 1` or
`raw = getattr(graph, "g", None)` — the shapes the tree actually uses
(`event_store.py`, `onboarding/state.py`, `onboarding/seed.py`,
`fallback_snapshot.py`, `weights.py`, `why.py`).
⛔ KNOWN LIMIT, stated rather than implied: a handle RECEIVED AS A PARAMETER is
not resolved (there is no binding to follow), and neither is one passed through
`functools.partial`. Measured on this tree when part 1 landed: sdk.py has 7
verb calls on a parameter receiver (3 read-only `MATCH … RETURN` probes —
`_refuse_inverted_point_window`, `_refuse_inconsistent_validity_precision`,
`_source_reachable_by` — and 4 calls to the SDK's own `query` method), and none
of them is a write, so nothing is hidden TODAY; a future write through a
parameter-received handle would be invisible here, which is why the shape is
named in this docstring instead of being claimed away.

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
#: `_onboarding_write_scope` is the onboarding funnel's own opener (`with
#: _onboarding_write_scope(cypher):` around the dispatch in
#: `tortoise/onboarding/state.py`) — a lane declared through a module-local
#: helper is still declared, and leaving it out reported that module as
#: undeclared while it was the opposite.
_SCOPE_CALL_NAMES = (
    "_live_write_scope",
    "_replay_write_scope",
    "_graph_metadata_write_scope",
    "_onboarding_write_scope",
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

#: A statement the enumerator cannot PROVE read-only, in a function that appends
#: no record. Two shapes produce them: a statement built by a helper or yielded
#: by a generator, and one whose f-string interpolates a COMPUTED fragment (a
#: call, or a name bound from a parameter or a loop) that cannot be resolved. The
#: classifier's polarity — "anything not provably read-only is a write" — counts
#: both as writes, and they are listed here (rather than being silently called
#: reads) because that IS the census being honest about what it can prove. Every
#: entry below was checked by hand: the resolvable part of each statement is a
#: `MATCH … RETURN` read with no write keyword in it.
_UNRESOLVED = {
    "_assert_lifecycle_guard",
    "_graph_presence",
    "_hop",
    "_window_closure",
    "expand_relationships",
    # f-string interpolates a computed fragment (`{_label}`, `{relationship_type}`,
    # `{_capture_passthrough_read_fields()}`, …) — the #9124 review's P1 shape,
    # which a placeholder substitution used to flatten into a false READ.
    "_count_ep_factors",
    "_cross_lens_pool",
    "_entity_name_holders",
    "_epistemic_label",
    "_find_operator",
    "_find_point_by_content",
    "_find_points_by_key",
    "_find_terminal_dedup_hit",
    "_read_capture_passthrough_props",
    "_reachable_claim_count",
    "_review_pool",
    "_review_prune",
    "_reverse_bfs_neighbors",
    "_semantic_dedup",
    "_state_related_objects",
    "_state_related_points",
    "_unsweepable_dirty_roots",
    "list_batch",
    "tortoise_fts_query",
    "traverse",
}

_INVENTORY = {
    "apply-first door (part 2)": _APPLY_FIRST_DOORS,
    "apply-first helper (part 2)": _APPLY_FIRST_HELPERS,
    "mixed arm (part 2)": _MIXED_ARM_DOORS,
    "no journal record to declare": _NO_JOURNAL,
    "statement not provably read-only": _UNRESOLVED,
}


# ── the enumerator ────────────────────────────────────────────────────────

def _runs_in_a_branch(node, field: str) -> bool:
    """Is `node.<field>` a BRANCH BODY (as opposed to a condition/iterator)?"""
    if isinstance(node, (ast.If, ast.While, ast.For, ast.AsyncFor)):
        return field in ("body", "orelse")
    if isinstance(node, (ast.Try, ast.TryStar)):
        return field in ("body", "handlers", "orelse", "finalbody")
    if isinstance(node, ast.ExceptHandler):
        return field == "body"
    if isinstance(node, ast.Match):
        return field == "cases"
    if isinstance(node, ast.match_case):
        return field == "body"
    return False


def _text(expr, consts, local):
    """Best-effort statement text for an argument expression, or ``None``.

    Resolves string literals, f-strings (RECURSING into each substituted
    expression, so a write clause carried by a name or a module constant is
    resolved instead of flattened away), string concatenation, module-level
    string constants and string locals assigned earlier in the function.

    ``None`` means "cannot resolve", and it is the SAFE answer: the caller
    counts an unresolvable statement as a WRITE. Substituting a placeholder for
    an unresolved part instead would make
    ``f"MATCH (n:Point {{id:$id}}) {action}"`` read as a non-write and drop the
    function out of the census entirely — the under-report direction.
    """
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    if isinstance(expr, ast.JoinedStr):
        parts = []
        for part in expr.values:
            if isinstance(part, ast.FormattedValue):
                resolved = _text(part.value, consts, local)
            elif isinstance(part, ast.Constant) and isinstance(part.value, str):
                resolved = part.value
            else:
                resolved = None
            if resolved is None:
                return None
            parts.append(resolved)
        return "".join(parts)
    if isinstance(expr, ast.Name):
        return local.get(expr.id, consts.get(expr.id))
    if isinstance(expr, ast.BinOp):
        left, right = _text(expr.left, consts, local), _text(expr.right, consts, local)
        if left is None or right is None:
            return None
        return left + right
    return None


def _is_handle_ref(expr, aliases) -> bool:
    """Does this expression name the write handle?

    `proj.g`, `self._get_proj().g` (any attribute chain ending in `g`),
    `getattr(p, "g")`, or a NAME already bound to one of those.
    """
    if isinstance(expr, ast.Attribute):
        return expr.attr == "g"
    if isinstance(expr, ast.Name):
        return expr.id in aliases
    if isinstance(expr, ast.Call):
        return (getattr(expr.func, "id", None) == "getattr"
                and len(expr.args) >= 2
                and isinstance(expr.args[1], ast.Constant)
                and expr.args[1].value == "g")
    return False


def _add_alias_target(target, aliases) -> None:
    """Bind `target` (a name, or a tuple/list of names) to the handle."""
    if isinstance(target, ast.Name):
        aliases.add(target.id)
    elif isinstance(target, ast.Starred):
        _add_alias_target(target.value, aliases)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            _add_alias_target(elt, aliases)


def _bind_handle_aliases(node, aliases) -> None:
    """`h = proj.g`, `h = getattr(p, "g")`, `h, _ = proj.g, 1`, `h: X = proj.g`.

    A receiver-name check alone misses every module that binds the handle to a
    local first — which is exactly how `event_store.py` and the onboarding
    funnel reach it, and how three read-side modules would have stayed outside
    the package-wide scan.
    """
    if isinstance(node, ast.Assign):
        targets, value = node.targets, node.value
    elif isinstance(node, ast.AnnAssign):
        targets, value = [node.target], node.value
    else:
        return
    if value is None:
        return
    if _is_handle_ref(value, aliases):
        for target in targets:
            _add_alias_target(target, aliases)
        return
    if isinstance(value, (ast.Tuple, ast.List)) and len(targets) == 1:
        pairs = zip(value.elts, _flatten_target(targets[0]))
        for part, target in pairs:
            if _is_handle_ref(part, aliases):
                _add_alias_target(target, aliases)


def _flatten_target(target):
    if isinstance(target, (ast.Tuple, ast.List)):
        return list(target.elts)
    return [target]


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

    def visit(node, current, local, cond=0):
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
                visit(child, current, local, cond)
            return

        fact = facts[current]
        # `g = proj.g` / `raw = getattr(graph, "g", None)` — event_store, the
        # onboarding funnel and the three read-side modules all bind the handle
        # to a LOCAL, so a receiver-name check alone would miss their writers.
        _bind_handle_aliases(node, fact["aliases"])
        local.update(_bound_names(node, consts, local))

        if isinstance(node, ast.Call):
            opener = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if opener and _APPEND_CALL_RE.match(opener):
                fact["appends"].append((node.lineno, cond))
            elif opener in _SCOPE_CALL_NAMES:
                fact["scopes"].append((node.lineno, opener))
            elif isinstance(node.func, ast.Attribute) \
                    and node.func.attr in _HANDLE_MUTATING_VERBS \
                    and _is_handle_ref(node.func.value, fact["aliases"]):
                # `execute_command("GRAPH.QUERY", <graph>, <cypher>)` — the
                # statement sits at index 2 (cypher_guard's own pinned shape),
                # every other verb takes it positionally.
                index = 2 if node.func.attr == "execute_command" else 0
                arg = node.args[index] if len(node.args) > index else None
                text = _text(arg, consts, local) if arg is not None else None
                if text is None or _statement_writes(text):
                    fact["writes"].append(
                        (node.lineno, node.func.attr,
                         " ".join((text or "<unresolved>").split())[:90], cond))
                else:
                    fact["reads"] += 1

        for field, child in ast.iter_fields(node):
            # CONTROL-FLOW DEPTH counts a statement that runs only in a BRANCH,
            # not one that merely appears in a branch's CONDITION: the append in
            # `if self._journal_event_raw_write(...) is False: return False`
            # guards its own write (`_event_raw_write_door`), so counting the
            # `if` test as conditional would misread a journal-first door as
            # apply-first.
            nested = cond + (1 if _runs_in_a_branch(node, field) else 0)
            if isinstance(child, list):
                for item in child:
                    if isinstance(item, ast.AST):
                        visit(item, current, local, nested)
            elif isinstance(child, ast.AST):
                visit(child, current, local, nested)

    visit(tree, None, {})
    for fact in facts.values():
        fact["writes"].sort()
    return {name: fact for name, fact in facts.items() if fact["writes"]}


def _journal_first_violation(fact) -> str | None:
    """Why this function's record does not provably precede its write, or None.

    Two checks, both necessary and neither sufficient: SOURCE ORDER (the first
    append is not after the first write) and CONTROL-FLOW DEPTH (the append is
    not buried deeper than the write — an `_emit_event` inside an `if`/`try`
    while the write runs unconditionally is the "declaration blesses a cache
    write with no committed record" shape even when its line comes first).
    """
    if not fact["appends"]:
        return ("appends no record at all — a live lane is 'append first, then "
                "write the cache'")
    first_write = fact["writes"][0]
    first_append = min(fact["appends"])
    if first_write[0] < first_append[0]:
        return (f"writes at line {first_write[0]}, BEFORE its first append at "
                f"{first_append[0]} ({first_write[1]}: {first_write[2]!r}) — that "
                "is the apply-first shape, and the declaration would bless it")
    if first_append[1] > first_write[3]:
        return (f"appends its record at line {first_append[0]} inside a "
                f"conditional/try block while writing at line {first_write[0]} "
                f"OUTSIDE it ({first_write[1]}) — the record is not guaranteed to "
                "precede the write")
    return None


def _kind(fact) -> str:
    """Classify one function: declared, or which inventory it belongs to."""
    if fact["scopes"]:
        return "declared"
    if not fact["appends"]:
        return "no-journal"
    return "apply-first" if _journal_first_violation(fact) else "journal-first"


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
    """A live lane is only HONEST when its record provably precedes its write.

    `@_declares_live_write_lane` (and any inline `_live_write_scope` around a
    whole door) authorizes everything the function writes, including anything
    above the append — so journal-first order is NECESSARY for the declaration
    to be true. It is not sufficient, and this pin states the gap it leaves
    instead of implying there is none: it reads SOURCE ORDER plus CONTROL-FLOW
    DEPTH, so an append that is unconditional while the write sits inside a
    branch passes, and an append buried in an `if`/`try` while the write runs
    unconditionally FAILS. An append skipped by an earlier `return`, or one
    guarded by a DIFFERENT condition than the write, is invisible to an AST —
    that residue is what a reviewer reads, not what this test proves.
    """
    surface = enumerate_handle_surface(SDK_PY.read_text(), str(SDK_PY))
    checked, offenders = [], []
    for name, fact in surface.items():
        openers = {opener for _line, opener in fact["scopes"]}
        if not openers & set(_LIVE_LANE_NAMES):
            continue
        violation = _journal_first_violation(fact)
        if violation:
            offenders.append(
                f"{name}:{fact['lineno']} declares a LIVE lane but {violation}")
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


def test_the_enumerator_resolves_every_shape_that_reaches_the_handle():
    """The receivers and statements a placeholder-based scan would have missed.

    Each shape here was a REAL gap in an earlier iteration of this enumerator
    (#9124 review round 1): an f-string whose interpolated NAME carries the
    write clause read as a non-write and the function vanished from the census;
    so did a handle bound to a local (`h = proj.g`), bound by `getattr`, bound
    through a tuple target, and reached through an attribute chain. The gate is
    the deliverable, so every one of them is pinned by a synthetic module.
    """
    src = '''
DETACH = "DETACH DELETE n"


def _emit_event(*a, **k):
    return True


class Door:
    def interpolated_write(self, proj, pid, action):
        proj.g.query(f"MATCH (n:Point {{id:$id}}) {action}")

    def module_const_write(self, proj):
        proj.g.query(f"MATCH (n:Point) {DETACH}")

    def getattr_alias_write(self, graph):
        raw = getattr(graph, "g", None)
        raw.query("CREATE (n:Point)")

    def tuple_alias_write(self, proj):
        h, _ignored = proj.g, 1
        h.query("CREATE (n:Point)")

    def chained_write(self, self_proj):
        self_proj.g.query("CREATE (n:Point)")
'''
    surface = enumerate_handle_surface(src, "<synthetic-shapes>")
    missing = sorted({
        "interpolated_write", "module_const_write", "getattr_alias_write",
        "tuple_alias_write", "chained_write",
    } - set(surface))
    assert not missing, (
        "the enumerator lost these shapes — each one is a mutating statement "
        f"through the write handle, so the census would under-report: {missing}")
    for name in ("interpolated_write", "module_const_write",
                 "getattr_alias_write", "tuple_alias_write", "chained_write"):
        assert _kind(surface[name]) == "no-journal", (name, surface[name])


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

#: Entry points whose lane is opened by their ONLY CALLER, so a declaration in
#: the body would be redundant and misleading — `rebuild_all` wraps
#: `_rebuild_all_replay` in `_replay_write_scope`, and `_ensure_indexes` wraps
#: `_ensure_indexes_declared` in `_graph_metadata_write_scope`. They are listed
#: here rather than left in the flip gate's remainder, where part 2 would read
#: "declare a lane below" and add exactly that redundant scope.
_CALLER_DECLARED = {
    ("tortoise/projection/__init__.py", "_rebuild_all_replay"),
    ("tortoise/projection/__init__.py", "_ensure_indexes_declared"),
}


def _modules_reaching_the_handle():
    r"""Every package module that issues a mutating handle verb, resolved from the AST.

    A source-text probe (`\.g\.(verb)\(`) is NOT this population: it misses every
    module that reaches the handle through a local binding — `raw =
    getattr(graph, "g"); raw.query(...)` (`tortoise/onboarding/state.py`,
    `seed.py`), `g = proj.g` (`weights.py`, `why.py`, `fallback_snapshot.py`).
    Measured when this was regex-based: the scan saw 24 modules and 154
    undeclared entry points, and `onboarding/state.py` — the #9121 write funnel,
    which DOES declare its lane — was not among them. Selection goes through the
    SAME enumerator the census uses, so population and census cannot disagree.
    """
    out = []
    for path in sorted((ROOT / "tortoise").rglob("*.py")):
        source = path.read_text(errors="replace")
        try:
            parsed = ast.parse(source)
        except SyntaxError:
            continue
        if parsed is None:  # pragma: no cover — keep the linters honest
            continue
        if enumerate_handle_surface(source, str(path)):
            out.append(path)
    return out


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
        rel = path.relative_to(ROOT).as_posix()
        undeclared = [f"{name}:{fact['lineno']}"
                      for name, fact in surface.items()
                      if not fact["scopes"] and (rel, name) not in _CALLER_DECLARED]
        if undeclared:
            remaining[rel] = sorted(undeclared)

    if _HANDLE_GUARD_ENFORCES:
        assert not remaining, (
            "`_HANDLE_GUARD_ENFORCES` is True while the write surface is NOT "
            "fully declared — arming here refuses the product's own writes. "
            "Declare a lane for every entry point below (or inventory it as a "
            "documented part-2 remainder; a helper whose only caller declares "
            "belongs in `_CALLER_DECLARED`, not in a new scope) before "
            "flipping the switch:\n"
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

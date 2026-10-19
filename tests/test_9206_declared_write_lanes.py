"""#9206 — the SDK's own write doors DECLARE the §17.1 live lane they write in.

WHY THIS SUITE EXISTS. §17.1 (`docs/architecture/STORAGE-ARCHITECTURE.md`) says
every write goes to the truth store first and the cache is projected from it —
"A derived cache that is also written to directly is not derived". #7951 landed
that as a RUNTIME, FAIL-CLOSED gate with two doors: the fold
(`FalkorProjection.apply`) and the public raw-query lane
(`FalkorProjection.query`). §17.1's THIRD door — the projection's write HANDLE
(`proj.g`), which is the path every SDK writer actually takes — is implemented
on #9124 and **landed last**, because arming it before the writers declare
themselves refuses the PRODUCT's own writes rather than a violation of them.

This suite is the declaration half of that sequencing. It does three things:

1. PINS the lane declaration of each door this issue converted, and pins that
   the declaration is HONEST — the door's first mutating handle statement is
   textually AFTER its first journal append. That is the ordering check §17.1
   actually rests on (the append is the COMMIT POINT — the JOURNAL ORDERING
   CONTRACT at the top of `tortoise/sdk.py`), and it is what stops a later edit
   that adds a write above the append from silently WIDENING the lane. A
   `_live_write_scope` opened over an apply-first door is the #9068 defect: a
   declaration that blesses an unjournalled cache write of recoverable state.
2. MEASURES the floor. The issue's own words: "A static scan finds 176 literal
   write statements under a `proj.g` verb in a function holding no scope — so
   this list is a floor, not a census. Enumerate before declaring." So every
   function in `tortoise/sdk.py` and `tortoise/hosted_api.py` that issues a
   mutating statement at the write handle is enumerated from the SOURCE, and
   must be either DECLARED or named in `_INVENTORY` with the reason it cannot
   declare yet. The inventory is pinned EXACTLY, so it can only SHRINK: a stale
   entry (a door that has since been declared, renamed or deleted) fails too.
3. Records the per-site DISPOSITION the issue asks for ("State which, per site,
   in this issue before writing code") as DATA, so the decision cannot drift
   away from the code it describes.

WHAT THIS SUITE DOES *NOT* CLAIM. It is a lexical census over two modules, not
a soundness proof. It resolves the handle shapes those two modules use
(`proj.g`, any attribute chain ending in `g`, `getattr(x, "g")`, and names bound
from one of those) and it OVER-reports on purpose: a statement whose text it
cannot resolve is counted as a WRITE, because the classifier's own polarity
(`projection._statement_writes`) is "anything not provably read-only is a
write". Shapes it does NOT resolve are named rather than implied — a handle
stashed on an object attribute (`self._h = proj.g`) and used later, a handle
reaching a module-level function that is not one of the two scanned files, and
`eval`/`globals()`. `hosted_api.py` is scanned because the issue names three of
its `:Session` upsert sites; its non-handle writers (the HTTP surface, the
commit-record store) are not.

⛔ ONE RULE, ONE READER — the sibling suite. `tests/test_9124_declared_live_doors.py`
(on `feat/9124-tm`, which lands AFTER this issue) is the same census for the
same rule, extended to `tortoise/event_store.py` and to the arming gate. This
file exists because that one does not exist on `main` and the DECLARATIONS have
to land first. When #9124 merges, the two inventories are reconciled into that
file — it is the arming gate's reader, so it is the one that must survive.
Duplicating the classifier is avoided: `_statement_writes` is imported from
`tortoise.projection`, which is the single home #3595 established for "what is
a write".
"""

from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path

import pytest

from tortoise.projection import (
    _CACHE_WRITE_AUTHORIZED,
    _declares_live_write_lane,
    _statement_writes,
)

ROOT = Path(__file__).resolve().parent.parent
SDK_PY = ROOT / "tortoise" / "sdk.py"
HOSTED_PY = ROOT / "tortoise" / "hosted_api.py"

#: The verbs that can put a MUTATING statement on the wire at the write handle.
#: The same list `_GuardedGraph` guards (all six, `query` included) — and NOT
#: just `query`, because a guard on one verb is one verb-name away from being no
#: guard at all (`ro_query`/`_query` dispatch the same statement, `profile` and
#: `explain` reach the engine, `execute_command` carries `GRAPH.QUERY`).
HANDLE_MUTATING_VERBS = frozenset(
    {"query", "ro_query", "_query", "profile", "explain", "execute_command"})

#: A callable that APPENDS a journal record — the commit point a live lane
#: declares itself after. Pattern-matched, not enumerated, so a NEW append
#: helper (`_emit_*`, `_journal_*`) is recognised instead of reclassifying a
#: journal-first door as "appends nothing".
_APPEND_CALL_RE = re.compile(
    r"^(?:_?emit_event|_?apply_and_journal|_?journal_\w+|_?emit_\w+_record"
    r"|append_event)$")

#: Names the pattern above would call an append but which append NO record.
#: `_journal_instant` reads an instant off a record; `_journal_safe_params`
#: sanitises a params map. Counting either as the commit point would let a door
#: whose record never landed pass the ordering pin.
_APPEND_NOT_A_RECORD = frozenset({"_journal_instant", "_journal_safe_params"})

#: The declaration openers. A door holding one of these, wrapping its write
#: region, has declared the lane it writes in.
_SCOPE_CALL_NAMES = frozenset({
    "_live_write_scope",
    "_replay_write_scope",
    "_graph_metadata_write_scope",
    "_onboarding_write_scope",
    "_cache_write_authorized",
    "_declares_live_write_lane",
})

#: The subset that claims "this door appends its record, THEN writes". Only
#: these are held to the journal-first ordering pin: a replay or graph-metadata
#: lane makes no such claim.
_LIVE_LANE_NAMES = frozenset({"_live_write_scope", "_declares_live_write_lane"})


# ── the per-site disposition (what the issue asks to be stated) ─────────────
#
# The issue's table, site by site, with the decision this lane took and WHY.
# `DECLARED` — the record is appended first, so the lane is honest and the door
#              now declares it (this lane's change).
# `JOURNAL_IT` — the record was stated AFTER the write; the record is a
#              function of the caller's INTENT only, so it was moved above the
#              write and the door declares the lane (this lane's change).
# `DEFERRED` — the record cannot precede the write without a decision outside
#              this issue's scope; the site is in `_INVENTORY` with the reason.
#              A DEFERRED entry is a MEASURED fact (asserted below), not a hope.

DECLARED = "declared"
JOURNAL_IT = "journal-it"
DEFERRED = "deferred"

#: `door -> (disposition, the door's function object name, why)`.
DISPOSITIONS: dict[str, tuple[str, str, str]] = {
    # ── this lane's declarations ───────────────────────────────────────────
    "update_point": (
        DEFERRED, "update_point",
        "MIXED ARM. The revision arms already append `PointRevised` first "
        "(#7951 increment 2) and would declare cleanly; the `status='live'` "
        "PROMOTE arm cannot. Its `SET` carries a `WHERE` that can legitimately "
        "match nothing (an illegal transition, or a node a concurrent replay "
        "has not re-landed), the door then RAISES, and journalling first would "
        "record a promotion the graph REFUSED — a replay would then apply it. "
        "That deviation is pinned by "
        "tests/test_7951_update_point_outbox.py::"
        "test_the_promote_arm_stays_apply_first_a_recorded_deviation, and the "
        "SAME write region serves both arms, so one scope cannot separate "
        "them. Declaring the whole door would bless the promote arm (#9068)."),
    "update_entity": (
        DEFERRED, "_update_entity",
        "APPLY-FIRST. The `SET` runs first and `_journal_entity_mutation` "
        "records it afterwards, so a scope here would bless an unjournalled "
        "cache write. Moving the record above the write changes the failure "
        "contract of the entity mutate path (what does a caller see when the "
        "record lands and the `SET` does not?) — its own change."),
    "supersede_point": (
        DECLARED, "supersede_point",
        "JOURNAL-FIRST. `PointSuperseded` (and, per transferred edge, "
        "`DirectEdgeRepoint`) is appended before the CAS/transfer writes; every "
        "write in the door is downstream of a committed record. The edge "
        "DELETEs in the self-edge arm emit no separate descriptor precisely "
        "because pass-2b must not recreate them post-rebuild — that suppression "
        "IS the record's projection."),
    "create_operator": (
        DEFERRED, "create_operator",
        "APPLY-FIRST, and NOT reorderable as-is: the record's `point` payload is "
        "`dict(self.get_point(pid))` — a POST-WRITE read-back. The payload does "
        "not exist until the cache has been written, which is the same shape "
        "that made `create_point` the recorded apply-first deviation until "
        "#7951's outbox increment gave it an intent-built snapshot. Building one "
        "for an operator (`OperatorAdded` carries the node snapshot AND the "
        "operator envelope) is its own change."),
    "create_direct_edge": (
        JOURNAL_IT, "create_direct_edge",
        "WAS APPLY-FIRST, NOW JOURNAL-FIRST (#9206). The `DirectEdgeCreated` "
        "payload is a function of the caller's intent only — src/tgt/edge_type/"
        "direction/confidence/weight/label/batch_id, none of them a post-write "
        "read — so the record was moved ABOVE the MERGE and the door gates its "
        "cache write on the append's per-call commit signal, exactly as "
        "`create_point` and `update_point` do. The replay is MERGE-keyed, so a "
        "record without its write is idempotently repaired by the next replay."),
    "source_upsert": (
        DEFERRED, "_materialize_session_source",
        "APPLY-FIRST. The `:Source` MERGE runs first and the capture record "
        "(`:Event` + `SessionRecorded`) afterwards. It is also a HELPER inside "
        "`capture_session`, which is itself a mixed-arm door (its `:Session` "
        "counter writes record their outcome only AFTER the write). Declaring a "
        "helper whose caller's lane is undeclared would move the hole, not "
        "close it."),
    "session_upsert": (
        DEFERRED, "_execute_commit_writes",
        "NO JOURNAL RECORD OF ITS OWN IN THIS MODULE. The hosted commit path's "
        "truth record is the `:CommitRecord` merged by `CommitRecordStore` "
        "before these writers run — whether that record IS the journal for this "
        "region (and therefore which lane name is honest) is a hosted-commit "
        "decision, not an SDK one. The counters are also relative "
        "(`coalesce(x,0) + $n`), so a replay must not re-apply them."),
    "point_delete": (
        DEFERRED, "delete_point",
        "DELIBERATELY NOT DECLARED HERE. The append already precedes the "
        "`DETACH DELETE`, so the ordering pin would pass — but #9207 pins the "
        "opposite requirement: the delete commits a destruction record before a "
        "cache write it cannot GUARANTEE, so a failure between the two leaves "
        "the journal saying *destroyed* while the node is still present, which "
        "no replay repairs in the direction the caller asked for. Its own "
        "issue says in terms: *do not fix this by declaring the delete's cache "
        "write sanctioned and leaving the ordering as-is*. #9207 owns the door; "
        "declaring it here would pin the shape that issue exists to replace."),
}

#: The inventory: every other handle-reaching function in the two scanned
#: modules, with the reason it holds no lane. Grouped by reason so the census
#: reads as a census, and pinned EXACTLY by the tests below.
#:
#: `journal-first but UNDECLARED`: the record already precedes the write, so the
#: ordering is §17.1-compliant, but the door holds no lane marker — either
#: because a SECOND arm of the same write region is apply-first (a mixed arm,
#: where one scope cannot separate the arms) or because another issue owns the
#: door's ordering decision. Named individually so the reason is per door.
_JOURNAL_FIRST_UNDECLARED = {
    "capture_session",        # mixed arm: its `:Session` counter writes record
                              # their outcome only AFTER the write
    "delete_point",           # #9207 owns its ordering; not declared here
    "update_point",           # mixed arm: the `status='live'` promote arm
}

#: `apply-first`: the record that describes the write is emitted AFTER it, so a
#: `_live_write_scope` would authorize a cache write with no committed record.
_APPLY_FIRST = {
    "_apply_source_inheritance",
    "_dedup_content_candidates",
    "_delete_entity",
    "_embedding_repair_attempt",
    "_materialize_session_source",
    "_promote_incident_operators",
    "_session_event_write",
    "_stamp_batch_id",
    "_update_entity",
    "approve_merge",
    "assess_source",
    "compute_confidence",
    "create_operator",
    "mitigate_operator",
    "promote_point",
    "record_calibration",
    "set_point_baseline",
    "_apply_capture_ingest_ep",
    "_write_capture_turns",
    "_capture_session_impl",
    "_sweep_orphaned_writes",
    "_delete_session_sync",
}

#: `no journal record to declare`: reaches the handle with a PROVABLE write and
#: appends no record at all, so there is no lane to declare. Each needs either a
#: named graph-metadata lane (the `_advance_ep_version` / `onboarding.metadata`
#: pattern) or a move onto the fold.
_NO_JOURNAL = {
    "_clear_reliability_cache",
    "_connect_issue_objects",
    "_extract_session_llm",
    "_extract_session_v2",
    "_graph_presence",
    "_index_source_merge",
    "_invalidate_inheritance_gate",
    "_mark_dirty",
    "_reapply_operator_promotion",
    "_record_embedding_marker",
    "_register_failed_attempt",
    "_sweep_dirty_roots",
    "_touch_inherited_at",
    "_write_reliability_cache",
    "backfill_v25",
    "complete_source",
    "migrate_orgs_to_registry",
    "set_source_tier",
    "_capture_abandoned_marker",
    "_execute_commit_writes",
    "_relink_sessions_after_index",
    "_seed_demo_graph",
    "_store_commit_telemetry",
    "_commit_region",
}

#: `nested in a declared door`: the function writes and appends nothing of its
#: own, but it is reached from a lane that DOES — so its writes are covered by
#: the caller's declaration. That is the stated residual of a whole-door lane
#: (see `_declares_live_write_lane`), and naming the member here is what keeps
#: it from being an assumption.
_NESTED_OF_A_DECLARED_DOOR = {
    "_epistemic_label",
}

#: `statement not provably read-only`: every write the enumerator found in the
#: function is UNRESOLVED (built by a helper, or an f-string interpolating a
#: computed fragment) and the function appends no record. The classifier's
#: polarity — "anything not provably read-only is a write" — counts them as
#: writes, and they are listed here rather than being silently called reads,
#: because that IS the census being honest about what it can prove. Each was
#: read by hand: the resolvable part is a `MATCH … RETURN` read with no write
#: keyword in it.
_UNRESOLVED = {
    "_assert_lifecycle_guard",
    "_count_ep_factors",
    "_cross_lens_pool",
    "_entity_name_holders",
    "_find_operator",
    "_find_point_by_content",
    "_find_points_by_key",
    "_find_terminal_dedup_hit",
    "_reachable_claim_count",
    "_read_capture_passthrough_props",
    "_reverse_bfs_neighbors",
    "_review_pool",
    "_review_prune",
    "_semantic_dedup",
    "_stamp_authored_word",
    "_state_related_objects",
    "_state_related_points",
    "_unsweepable_dirty_roots",
    "_window_closure",
    "_hop",
    "backfill_about_entities",
    "expand_relationships",
    "ingest",
    "list_batch",
    "paginated_query",
    "query",
    "search_sessions",
    "suggest_entry_points",
    "tortoise_fts_query",
    "traverse",
    "_write_session_and_turns",
    "_load_commit_graph_state",
    "_read_graph",
    "list_points",
    "list_sessions",
}

#: `group -> the claim the enumerator must confirm for every member`. The claim
#: is re-derived from the SOURCE by the tests below, so a group can never
#: outlive the fact that justified it.
_INVENTORY: dict[str, set[str]] = {
    "journal-first but undeclared": _JOURNAL_FIRST_UNDECLARED,
    "apply-first (part 2)": _APPLY_FIRST,
    "no journal record to declare": _NO_JOURNAL,
    "nested in a declared door": _NESTED_OF_A_DECLARED_DOOR,
    "statement not provably read-only": _UNRESOLVED,
}

# ── the enumerator ────────────────────────────────────────────────────────

def _text(expr, consts, local):
    """Best-effort statement text for an argument expression, or ``None``.

    ``None`` means "cannot resolve", and it is the SAFE answer: the caller
    counts an unresolvable statement as a WRITE. Substituting a placeholder for
    an unresolved part would make ``f"MATCH (n:Point {{id:$id}}) {action}"``
    read as a non-write and drop the function out of the census — the
    under-report direction, and the #9124 review's P1 shape.
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
        # A name BOUND in this function wins over a module constant of the same
        # name: classifying from a literal that is not the runtime value is the
        # under-report direction.
        if expr.id in local:
            return local[expr.id]
        return consts.get(expr.id)
    if isinstance(expr, ast.BinOp):
        left = _text(expr.left, consts, local)
        right = _text(expr.right, consts, local)
        return None if left is None or right is None else left + right
    return None


def _is_handle_ref(expr, aliases) -> bool:
    """`proj.g`, any attribute chain ending in `g`, `getattr(x, "g")`, an alias."""
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


def _bind_aliases(node, aliases) -> None:
    """Bind every shape in this tree that can put the write handle in a local."""
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
        value = node.value
        targets = (node.targets if isinstance(node, ast.Assign)
                   else [node.target])
    elif isinstance(node, ast.withitem):
        value, targets = node.context_expr, [node.optional_vars]
    elif isinstance(node, (ast.For, ast.AsyncFor)):
        value, targets = node.iter, [node.target]
    else:
        value, targets = None, []
    if value is not None and _is_handle_ref(value, aliases):
        for target in targets:
            if isinstance(target, ast.Name):
                aliases.add(target.id)


def _call_name(call) -> str:
    """The name a call (or a decorator expression) is issued under."""
    if not isinstance(call, ast.Call):
        return ""
    fn = call.func
    if isinstance(fn, ast.Name):
        return fn.id
    if isinstance(fn, ast.Attribute):
        return fn.attr
    return ""


#: The Cypher clauses that MUTATE. Used only for the ordering pin's answer to
#: "an unresolvable statement — is the part I can read already a write?", which
#: is a different question from the census's "could this be a write at all?"
#: (that one is `_statement_writes`'s, and it answers yes).
_MUTATING_CLAUSE_RE = re.compile(
    r"\b(?:CREATE|MERGE|SET|DELETE|DETACH|REMOVE|FOREACH)\b", re.IGNORECASE)


def _carries_write_keyword(expr) -> bool:
    """Is an UNRESOLVED statement's readable content already a write clause?

    Two different questions live here, and conflating them is how a census goes
    quietly wrong in the UNDER-report direction:

    * a statement whose text the enumerator can PARTLY read (an f-string, a
      concatenation) is judged on those parts — `f"MATCH (n:{_label}) WHERE
      n.id = $id RETURN n.id"` carries no write clause and is a READ probe,
      which is why `create_direct_edge`'s per-label probe does not count as a
      write above its record;
    * a statement passed as a NAME (`proj.g.query(_q, …)` where `_q` came from
      a ``epistemic_label_queries(...)`` generator), or in any other shape with
      no readable string at all, is NOT cleared: there is nothing to judge, so
      it is counted as a write. Measured: `compute_confidence` writes
      `UNWIND … SET n.confidence = p.c` through such a name and emits its
      `ConfidenceChanged` record AFTERWARDS — judging the name as a read would
      have reclassified an apply-first door as journal-first.
    """
    if isinstance(expr, (ast.Constant, ast.JoinedStr, ast.BinOp)):
        for node in ast.walk(expr):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and _MUTATING_CLAUSE_RE.search(node.value):
                return True
        return False
    # No readable string structure: cannot clear it, so it stays a write.
    return True


def _module_consts(tree) -> dict[str, str]:
    consts: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    consts[target.id] = node.value.value
    return consts


#: The AST "fields" that are BRANCH bodies — a node reached through one of
#: these runs only when that branch is taken. A `with` body is deliberately
#: absent: it runs whenever the enclosing code does, so a write inside a `with`
#: is unconditional in the sense the ordering pin means. So are an `if`'s TEST
#: and its condition — evaluating the test is not a choice, which is why an
#: append inside `if self._journal_...(...) is False:` is NOT "conditional"
#: (measured: `_event_raw_write_door` appends in an `if` test and writes under a
#: `with`, and a naive per-construct depth called that a violation).
_BRANCH_BODY_FIELDS: dict[type, tuple[str, ...]] = {
    ast.If: ("body", "orelse"),
    ast.While: ("body", "orelse"),
    ast.For: ("body", "orelse"),
    ast.AsyncFor: ("body", "orelse"),
    ast.Try: ("body", "handlers", "orelse", "finalbody"),
    ast.ExceptHandler: ("body",),
    ast.Match: ("cases",),
    ast.match_case: ("body",),
}
if hasattr(ast, "TryStar"):  # pragma: no cover - 3.11+
    _BRANCH_BODY_FIELDS[ast.TryStar] = ("body", "handlers", "orelse", "finalbody")


def _is_branch_body(parent, field: str) -> bool:
    return field in _BRANCH_BODY_FIELDS.get(type(parent), ())


def _walk_function(node, depth: int = 0):
    """Walk a function's OWN body IN SOURCE ORDER as ``(node, branch_depth)``.

    ORDER MATTERS: the handle-alias tracker (`h = proj.g` … `h.query(...)`) is
    only correct if a binding is seen before the call that uses it, so the walk
    follows `ast.iter_fields`, which yields children in source order.

    ``branch_depth`` counts the BRANCH BODIES a node runs inside (see
    `_BRANCH_BODY_FIELDS`). It is what lets the ordering pin tell "the record is
    appended, then the write" from "the record is appended INSIDE a branch
    while the write runs unconditionally": the second is the #9068 shape even
    though its line number comes first.

    Nested `def`s and classes are NOT descended into — their writes belong to
    them, and an alias they capture from this scope is not resolved (a stated
    limit of the census, not a claim of completeness).
    """
    for field, value in ast.iter_fields(node):
        children = value if isinstance(value, list) else [value]
        for child in children:
            if not isinstance(child, ast.AST):
                continue
            yield child, depth
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef)):
                continue
            inner = depth + (1 if _is_branch_body(node, field) else 0)
            yield from _walk_function(child, inner)


def _function_facts(node, consts) -> dict:
    """The census facts for ONE function: its writes, appends, scopes, aliases."""
    aliases: set[str] = set()
    local: dict[str, str] = {}
    writes: list[tuple[int, str, bool, int]] = []
    appends: list[tuple[int, int]] = []
    scopes: set[str] = set()
    declared_by_decorator = any(
        _call_name(d) in _LIVE_LANE_NAMES for d in node.decorator_list)
    if declared_by_decorator:
        scopes.update(_call_name(d) for d in node.decorator_list
                      if _call_name(d) in _LIVE_LANE_NAMES)

    for cur, depth in _walk_function(node):
        _bind_aliases(cur, aliases)
        if isinstance(cur, ast.Assign) and isinstance(cur.value, ast.Constant) \
                and isinstance(cur.value.value, str):
            for target in cur.targets:
                if isinstance(target, ast.Name):
                    local[target.id] = cur.value.value
        if not isinstance(cur, ast.Call):
            continue
        name = _call_name(cur)
        if name in _SCOPE_CALL_NAMES:
            scopes.add(name)
        if _APPEND_CALL_RE.match(name) and name not in _APPEND_NOT_A_RECORD:
            appends.append((cur.lineno, depth))
        # The machine is `proj.g.query(...)` — a verb call on a handle.
        if not isinstance(cur.func, ast.Attribute) or name not in HANDLE_MUTATING_VERBS:
            continue
        receiver = cur.func.value
        if not isinstance(receiver, ast.Attribute) and not isinstance(receiver, ast.Name):
            continue
        if not _is_handle_ref(receiver, aliases):
            continue
        if name == "execute_command":
            # The statement travels inside a Redis command payload; recovering
            # it is the guard's job (`_command_statement`), not this census's.
            # Counted as a write so the function stays visible.
            writes.append((cur.lineno, "<raw command>", True, depth))
            continue
        if not cur.args:
            writes.append((cur.lineno, "<no statement>", True, depth))
            continue
        statement = _text(cur.args[0], consts, local)
        if statement is None:
            # UNRESOLVED: a WRITE for the census (the over-report direction),
            # but only a PROVABLE write when the parts the enumerator CAN read
            # already carry a write keyword. The distinction exists because the
            # two questions are different: "is this function a handle writer?"
            # must over-report, while "does a write precede the append?" needs
            # an actual write clause, and an id-interpolated `MATCH … RETURN`
            # probe (`f"MATCH (n:{_label}) WHERE … RETURN n.id"`) is neither a
            # write nor invisible. The residual is stated: a write clause that
            # lives ENTIRELY inside an interpolated name is not seen here — it
            # is `_UNRESOLVED`'s documented hazard, and such a function stays
            # in the census through its unresolved write.
            writes.append((cur.lineno, "<unresolved>",
                           _carries_write_keyword(cur.args[0]), depth))
        elif _statement_writes(statement):
            writes.append((cur.lineno, " ".join(statement.split())[:120], True,
                           depth))

    return {
        "name": node.name,
        "lineno": node.lineno,
        "writes": writes,
        "appends": appends,
        "scopes": scopes,
        "declared": bool(scopes),
        # A LANE that claims "append first, then write" — as opposed to the
        # replay or graph-metadata lanes, which make no such claim and are
        # therefore NOT held to the ordering pin below.
        "live_lane": bool(scopes & _LIVE_LANE_NAMES),
        "declared_by_decorator": declared_by_decorator,
        "all_unresolved": bool(writes) and all(
            statement == "<unresolved>" for _, statement, _, _ in writes),
        "first_provable_write": min(
            (line for line, _, provable, _ in writes if provable), default=None),
    }


def enumerate_handle_surface(source: str, filename: str = "<src>") -> dict[str, dict]:
    """Every function in ``source`` that reaches the write handle, with its facts."""
    tree = ast.parse(source, filename=filename)
    consts = _module_consts(tree)
    out: dict[str, dict] = {}

    def visit(body, prefix="", in_function=False):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                facts = _function_facts(node, consts)
                facts["nested_in_function"] = in_function
                if facts["writes"]:
                    out[prefix + node.name] = facts
                visit(node.body, prefix + node.name + ".", True)
            elif isinstance(node, ast.ClassDef):
                visit(node.body, prefix + node.name + ".", in_function)
            elif isinstance(node, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                visit(node.body, prefix, in_function)

    visit(tree.body)
    return out


def _classify(facts: dict) -> str:
    """`declared` | `journal-first` | `apply-first` | `no-journal`.

    `journal-first` uses the SAME depth-aware coverage rule the ordering pin
    uses (`_ordering_violation`), so the census and the pin cannot disagree
    about a door — the #3595 shared-decision precedent, applied here.
    """
    if facts["declared"]:
        return "declared"
    if not facts["appends"]:
        return "no-journal"
    return "apply-first" if _ordering_violation({
        "name": facts["name"], "appends": facts["appends"],
        "writes": facts["writes"], "live_lane": True}) else "journal-first"


def _ordering_violation(facts: dict) -> str | None:
    """Why this DECLARED live-lane door is not journal-first, or ``None``.

    One check, run PER PROVABLE WRITE and existentially over the appends: for
    every mutating statement there must be some append with (a) a smaller line
    number and (b) a control-flow depth no greater than that write's. Line order
    alone is not enough — an `_emit_event` inside an `if`/`try` while the write
    runs unconditionally is the "declaration blesses a cache write with no
    committed record" shape even when its line comes first. Neither is the
    depth: a record appended AFTER the write is not a commit point. And neither
    is sufficient in principle: a record skipped by an earlier `return` is
    invisible to an AST — which is why the declarations are placed where the
    append is unconditional, not merely where it is early.
    """
    if not facts.get("live_lane", True):
        return None  # a replay / graph-metadata lane claims no ordering
    if not facts["appends"]:
        return (f"{facts['name']} declares a live lane but appends NO record — a "
                f"lane is 'append first, then write the cache'")
    writes = [w for w in facts["writes"] if w[2]]
    if not writes:
        return None
    uncovered = [write for write in writes
                 if not any(append[0] < write[0] and append[1] <= write[3]
                            for append in facts["appends"])]
    if not uncovered:
        return None
    shallowest = min(uncovered, key=lambda write: write[3])
    earliest = min(facts["appends"])
    if earliest[0] > shallowest[0]:
        return (f"{facts['name']} writes at line {shallowest[0]} "
                f"({shallowest[1]!r}) BEFORE its first append at {earliest[0]} — "
                f"that is the apply-first shape, and the declaration would "
                f"bless it (#9068)")
    return (f"{facts['name']} has no append covering its write at line "
            f"{shallowest[0]} (depth {shallowest[3]}); its records are at "
            f"{[(line, depth) for line, depth in facts['appends']]}, so a branch "
            f"can reach that write with no record appended")


def _surfaces():
    return {
        "tortoise/sdk.py": enumerate_handle_surface(
            SDK_PY.read_text(), "tortoise/sdk.py"),
        "tortoise/hosted_api.py": enumerate_handle_surface(
            HOSTED_PY.read_text(), "tortoise/hosted_api.py"),
    }


# ── 1. the declarations are honest ───────────────────────────────────────

def test_the_declared_doors_are_exactly_the_ones_this_issue_converted():
    """The declaration set is pinned, so a door cannot gain a lane unreviewed."""
    declared = set()
    for surface in _surfaces().values():
        for key, facts in surface.items():
            if facts["declared"]:
                declared.add(facts["name"])
    expected = {
        # #7951's existing declarations in these two modules. (`_create_entity`,
        # `_doc_write` and `_apply_and_journal` declare their lane too, but
        # their writes reach the FOLD, not the raw handle, so this census does
        # not see them — `tests/test_7951_write_doors.py` is their reader.)
        "create_point", "_advance_ep_version", "_event_raw_write_door",
        # #9206 — this lane.
        "invalidate_point", "retract_point", "supersede_point",
        "create_direct_edge",
    }
    assert expected <= declared, (
        f"not declared: {sorted(expected - declared)} — the door's lane marker "
        f"is missing, and §17.1's third door (#9124) will refuse it when armed")
    assert declared - expected == set(), (
        f"undeclared in this suite's expectation: {sorted(declared - expected)} "
        f"— add it to the expected set (a reviewed line) if the declaration is "
        f"honest, or remove the marker if it is not")


#: The doors THIS issue declared. Their lane reason must name the door, so a
#: refusal and a test can both key on it. (#7951's older markers use a lane name
#: rather than the function name — `_apply_and_journal` is "chokepoint" — and
#: that is a reviewed precedent, not something this suite renames.)
_9206_DOORS = ("invalidate_point", "retract_point", "supersede_point",
               "create_direct_edge")


def test_every_declared_door_opens_its_lane_with_its_own_door_name():
    """A marker that does not NAME its door is not a declaration a reader can follow."""
    surfaces = _surfaces()
    for rel, surface in surfaces.items():
        source = (ROOT / rel).read_text()
        tree = ast.parse(source, filename=rel)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in _9206_DOORS:
                continue
            markers = [d for d in node.decorator_list
                       if isinstance(d, ast.Call)
                       and _call_name(d) in _LIVE_LANE_NAMES]
            inline = [c for c in ast.walk(node)
                      if isinstance(c, ast.Call)
                      and _call_name(c) in _LIVE_LANE_NAMES
                      and c.args
                      and isinstance(c.args[0], ast.Constant)]
            reasons = [
                d.args[0].value for d in markers
                if d.args and isinstance(d.args[0], ast.Constant)]
            reasons += [c.args[0].value for c in inline]
            assert reasons, (
                f"{rel}::{node.name} declares a lane with no readable reason — "
                f"the marker's first argument is the NAME a refusal and a test "
                f"both key on")
            assert node.name in reasons, (
                f"{rel}::{node.name} declares lane {reasons!r}, which does not "
                f"name the door: a marker that says the wrong thing is worse "
                f"than none, because the census believes it")



def test_every_declared_door_is_journal_first():
    """⛔ THE RULE: the append is the commit point, so it must PRECEDE the write.

    This is the pin that makes the declarations above honest rather than
    asserted. A `_live_write_scope` opened over an apply-first door authorizes a
    cache write with no committed record — the #9068 defect — and a later edit
    that moves a write above the append reddens HERE instead of silently
    widening the lane.
    """
    violations = []
    for rel, surface in _surfaces().items():
        for key, facts in surface.items():
            if not facts["declared"]:
                continue
            violation = _ordering_violation(facts)
            if violation:
                violations.append(f"{rel}::{violation}")
    assert not violations, "\n".join(violations)


def test_create_direct_edge_states_its_record_before_the_merge():
    """The #9206 reorder, pinned where it is observable in the source.

    `create_direct_edge` was apply-first: the `DirectEdgeCreated` descriptor was
    emitted AFTER the MERGE. The payload is a function of the caller's intent
    only, so it moved above — and the door now gates its cache write on the
    append's per-call commit signal, the `create_point`/`update_point` shape.
    """
    surface = enumerate_handle_surface(SDK_PY.read_text(), "tortoise/sdk.py")
    facts = surface["TortoiseSDK.create_direct_edge"]
    assert facts["live_lane"], "create_direct_edge declares no live lane"
    assert _ordering_violation(facts) is None
    assert min(line for line, _depth in facts["appends"]) < facts["first_provable_write"]

    source = SDK_PY.read_text()
    tree = ast.parse(source)
    node = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef)
                and n.name == "create_direct_edge")
    gate = [n for n in ast.walk(node)
            if isinstance(n, ast.If)
            and isinstance(n.test, ast.Compare)
            and isinstance(n.test.left, ast.Name)
            and n.test.left.id == "_record_committed"]
    assert gate, (
        "create_direct_edge must gate its cache write on the append's commit "
        "signal (`if _record_committed is False: raise WriteNotLandedError`) — "
        "without it the record-first order is decorative, and a failed append "
        "still leaves the cache in the lead")


# ── 2. the floor is a floor ──────────────────────────────────────────────

def test_every_handle_writer_is_declared_or_inventoried():
    """⛔ THE CENSUS: no function in the two scanned modules is unaccounted for.

    The issue's own words: the hand-listed sites are "a floor, not a census".
    This is the census — derived from the SOURCE, never from a hand-maintained
    list of call sites — so a function added later cannot slip in undeclared:
    it fails here until someone declares it or adds it to an inventory, which
    is a reviewed line in this file rather than a silent omission.
    """
    inventoried = set().union(*_INVENTORY.values())
    offenders = []
    for rel, surface in _surfaces().items():
        for key, facts in surface.items():
            if facts["declared"]:
                continue
            if facts["name"] not in inventoried:
                offenders.append(
                    f"{rel}::{key} (write at line {min(l for l, _ in facts['writes'])}, "
                    f"appends={facts['appends'] or 'none'})")
    assert not offenders, (
        "undeclared handle writers with no inventory entry:\n  "
        + "\n  ".join(sorted(offenders)))


def test_the_inventories_hold_no_stale_entry():
    """An inventory entry can only leave this file by being declared or deleted.

    A stale entry is a lie in the census: it names a function that no longer
    writes (or no longer exists) and pads the floor with a row nobody re-reads.
    """
    seen = {facts["name"]
            for surface in _surfaces().values()
            for facts in surface.values() if not facts["declared"]}
    stale = sorted(set().union(*_INVENTORY.values()) - seen)
    assert not stale, (
        f"inventory entries that no longer name an undeclared handle writer: "
        f"{stale} — declare the door, delete the entry, or correct its reason")


def test_every_deferred_site_in_the_issue_table_has_an_inventory_entry():
    """The disposition table and the inventory must not disagree.

    The issue names eight sites. Each is DECLARED, JOURNAL_IT, or DEFERRED, and
    a DEFERRED site has to appear in the inventory — otherwise the disposition
    claims "measured, deferred" about a function the census does not see, which
    is exactly the "state it, don't imply it" failure this file exists to stop.
    """
    inventoried = set().union(*_INVENTORY.values())
    surfaces = _surfaces()
    seen = {facts["name"] for surface in surfaces.values() for facts in surface.values()}
    problems = []
    for site, (disposition, fn_name, reason) in DISPOSITIONS.items():
        assert disposition in (DECLARED, JOURNAL_IT, DEFERRED), disposition
        assert len(reason) > 80, f"{site}: the disposition needs a real reason"
        if fn_name not in seen:
            problems.append(f"{site} -> {fn_name}: the census does not see this "
                            f"function at all (renamed or no longer a handle writer)")
            continue
        facts = next(f for surface in surfaces.values()
                     for f in surface.values() if f["name"] == fn_name)
        if disposition == DEFERRED:
            if fn_name not in inventoried:
                problems.append(f"{site} -> {fn_name}: DEFERRED but absent from "
                                f"the inventory")
            if facts["declared"]:
                problems.append(f"{site} -> {fn_name}: DEFERRED but the door "
                                f"declares a lane")
        else:
            if not facts["declared"]:
                problems.append(f"{site} -> {fn_name}: claims {disposition} but "
                                f"holds no lane marker")
    assert not problems, "\n".join(problems)


def _facts_by_name() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for surface in _surfaces().values():
        for facts in surface.values():
            out.setdefault(facts["name"], facts)
    return out


#: `group -> the classification its members must have when the SOURCE is read`.
#: The claim is re-derived, never trusted, so a group can only hold a member
#: while the fact that put it there is still true.
_GROUP_CLAIM = {
    "journal-first but undeclared": "journal-first",
    "apply-first (part 2)": "apply-first",
    "no journal record to declare": "no-journal",
}


def test_every_inventory_group_claim_is_measured_not_asserted():
    """⛔ A GROUP'S REASON IS A CLAIM ABOUT THE CODE, so it is re-checked.

    "apply-first" is only honest while the record really is stated after the
    write, and "no journal record" while the function really appends nothing.
    If someone reorders a door, this reddens and the entry is PROMOTED to
    DECLARED (or moved to the group that now describes it) instead of carrying
    a stale excuse forever. The two shape-agnostic groups are checked too: a
    "not provably read-only" member must have nothing but unresolved statements,
    and a "nested in a declared door" member must actually be nested.
    """
    facts_by_name = _facts_by_name()
    wrong = []
    for group, members in _INVENTORY.items():
        for fn_name in members:
            facts = facts_by_name.get(fn_name)
            if facts is None:
                continue  # covered by the staleness test
            if group in _GROUP_CLAIM:
                claim = _GROUP_CLAIM[group]
                kind = _classify(facts)
                if kind != claim:
                    wrong.append(f"{fn_name} is inventoried as {claim!r} but "
                                 f"classifies as {kind!r}")
            elif group == "statement not provably read-only":
                if not facts["all_unresolved"]:
                    wrong.append(f"{fn_name} is inventoried as unresolved but has "
                                 f"a statement the enumerator DID resolve")
            elif group == "nested in a declared door":
                if not facts.get("nested_in_function"):
                    wrong.append(f"{fn_name} is inventoried as nested but is not "
                                 f"defined inside another function")
    assert not wrong, "\n".join(wrong)


def test_the_no_journal_group_holds_no_function_that_only_has_unresolved_writes():
    """The two shape-agnostic groups must not overlap on the same function.

    A function whose only statements the enumerator could not resolve belongs in
    the `unresolved` group — filing it under `no journal record` would state a
    fact about a write nobody proved is one.
    """
    facts_by_name = _facts_by_name()
    misfiled = sorted(
        name for name in _NO_JOURNAL
        if name in facts_by_name and facts_by_name[name]["all_unresolved"])
    assert not misfiled, (
        f"inventoried as a proven no-journal writer but every statement is "
        f"unresolved: {misfiled}")


def test_no_two_census_entries_share_a_bare_function_name():
    """A bare name is how these inventories are keyed — so it must be unique.

    `_write_props` (a nested helper of `update_point`) and a module-level
    `_write_props` would collapse into one entry, and the inventory would then
    account for one writer while two exist.
    """
    counts: dict[str, int] = {}
    for surface in _surfaces().values():
        for key, facts in surface.items():
            counts[facts["name"]] = counts.get(facts["name"], 0) + 1
    dupes = {name: n for name, n in counts.items() if n > 1}
    assert not dupes, (
        f"the same bare function name is defined more than once in the scanned "
        f"modules: {dupes} — key the census by qualified name, or give the "
        f"nested helper its own reason")


# ── 3. non-vacuity: the pins fire ────────────────────────────────────────

_APPLY_FIRST_DOOR = textwrap.dedent('''
    class Door:
        def write_edge(self, src, tgt):
            proj = self._get_proj()
            proj.g.query("MATCH (a), (b) MERGE (a)-[:IMPL]->(b)")
            self._emit_event("DirectEdgeCreated", id=f"{src}->{tgt}")
    ''')

_JOURNAL_FIRST_DOOR = textwrap.dedent('''
    class Door:
        @_declares_live_write_lane("write_edge")
        def write_edge(self, src, tgt):
            proj = self._get_proj()
            self._emit_event("DirectEdgeCreated", id=f"{src}->{tgt}")
            proj.g.query("MATCH (a), (b) MERGE (a)-[:IMPL]->(b)")
    ''')

_WIDENED_DOOR = textwrap.dedent('''
    class Door:
        @_declares_live_write_lane("write_edge")
        def write_edge(self, src, tgt):
            proj = self._get_proj()
            proj.g.query("MATCH (a) SET a.touched = true")
            self._emit_event("DirectEdgeCreated", id=f"{src}->{tgt}")
    ''')


def test_the_enumerator_sees_an_apply_first_door_as_apply_first():
    surface = enumerate_handle_surface(_APPLY_FIRST_DOOR, "<synthetic>")
    facts = surface["Door.write_edge"]
    assert _classify(facts) == "apply-first"
    assert "MERGE" in facts["writes"][0][1]


def test_the_enumerator_sees_a_declared_journal_first_door_as_clean():
    surface = enumerate_handle_surface(_JOURNAL_FIRST_DOOR, "<synthetic>")
    facts = surface["Door.write_edge"]
    assert facts["declared"] and facts["live_lane"]
    assert _ordering_violation(facts) is None


def test_the_ordering_pin_catches_a_write_above_the_append():
    """The pin this suite rests on is not vacuous: a WIDENED lane reddens.

    This is the regression the declaration invites — a later edit adding a
    write above the append — and the synthetic door is the proof the check can
    fail rather than the proof it passes.
    """
    surface = enumerate_handle_surface(_WIDENED_DOOR, "<synthetic>")
    facts = surface["Door.write_edge"]
    assert facts["declared"] and facts["live_lane"]
    violation = _ordering_violation(facts)
    assert violation and "BEFORE its first append" in violation


def test_the_enumerator_counts_an_unresolvable_statement_as_a_write():
    """The over-report polarity, pinned — it is what keeps the census safe."""
    source = textwrap.dedent('''
        class Door:
            def helper(self, action):
                proj = self._get_proj()
                proj.g.query(f"MATCH (n:Point {{id:$id}}) {action}")
        ''')
    facts = enumerate_handle_surface(source, "<synthetic>")["Door.helper"]
    assert facts["writes"] and facts["writes"][0][1] == "<unresolved>"


def test_the_enumerator_follows_a_handle_bound_to_a_local():
    """`h = proj.g` then `h.query(...)` is the shape `event_store.py` uses."""
    source = textwrap.dedent('''
        def write_it():
            h = proj.g
            h.query("CREATE (n:Point {id:$id})")
        ''')
    facts = enumerate_handle_surface(source, "<synthetic>")["write_it"]
    assert facts["writes"]


# ── 4. the lane itself behaves ───────────────────────────────────────────

def test_the_decorator_declares_the_door_name_and_releases_it():
    """The marker is a per-call ContextVar scope, not a flag that can leak."""
    seen: list[str | None] = []

    @_declares_live_write_lane("synthetic-door")
    def door():
        seen.append(_CACHE_WRITE_AUTHORIZED.get())
        return "ok"

    assert _CACHE_WRITE_AUTHORIZED.get() is None
    assert door() == "ok"
    assert seen == ["synthetic-door"]
    assert _CACHE_WRITE_AUTHORIZED.get() is None, (
        "the lane leaked past the door — a raising or returning door must not "
        "authorize the caller's next write")


def test_a_raising_door_does_not_leak_its_lane():
    @_declares_live_write_lane("synthetic-door")
    def door():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        door()
    assert _CACHE_WRITE_AUTHORIZED.get() is None


def test_the_nested_no_journal_writes_a_declared_lane_covers_are_named():
    """⛔ THE STATED RESIDUAL, stated in code rather than implied.

    A whole-door lane also authorizes everything the door CALLS. Each of the
    doors this issue declared calls `_mark_dirty`, which writes through the
    handle and appends no record. That is covered by the door's lane — a
    residual of the per-function census, which cannot see a call edge — and it
    is named here so a NEW no-journal write reached from a declared door is
    reviewed instead of inheriting the lane silently.
    """
    covered_nested = {"_mark_dirty"}
    surfaces = _surfaces()
    assert covered_nested <= set().union(*_INVENTORY.values()), (
        "every nested no-journal writer a declared lane covers must have an "
        "inventory entry saying so")

    for fn_name in ("invalidate_point", "retract_point", "supersede_point",
                    "create_direct_edge"):
        node = next(
            n for n in ast.walk(ast.parse(SDK_PY.read_text()))
            if isinstance(n, ast.FunctionDef) and n.name == fn_name)
        called = {_call_name(c) for c in ast.walk(node) if isinstance(c, ast.Call)}
        assert "_mark_dirty" in called, (
            f"{fn_name} no longer calls _mark_dirty — if the call moved, the "
            f"lane may now cover a DIFFERENT nested no-journal write; re-derive "
            f"this pin instead of deleting it")


# ── 5. the hosted `:Session` sites the issue names ───────────────────────

_SESSION_UPSERT = "MERGE (s:Session {id:$sid})"


def test_the_hosted_session_upsert_sites_are_enumerated_and_accounted_for():
    """The issue names three `:Session` upsert sites; they must stay accounted.

    They are DEFERRED (see `DISPOSITIONS['session_upsert']`), but "deferred" is
    only honest while the census can still SEE each one. This pins the set of
    functions that issue a `MERGE (s:Session …)` so a fourth site cannot appear
    quietly, and so a site cannot move to a module this suite never scans
    without reddening here.
    """
    found: set[str] = set()
    for rel in ("tortoise/sdk.py", "tortoise/hosted_api.py"):
        source = (ROOT / rel).read_text()
        tree = ast.parse(source)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                        and _SESSION_UPSERT in " ".join(node.value.split()):
                    found.add(fn.name)

    # The three the issue names, plus the demo seed it also lists and the outer
    # wrapper `commit_session` that closes over `_commit_region`.
    expected = {"_execute_commit_writes", "_commit_region",
                "_write_session_and_turns", "_seed_demo_graph", "commit_session"}
    assert expected <= found, (
        f"the census lost a `:Session` upsert site: {sorted(expected - found)} "
        f"— a site that moved out of the scanned modules is a site no lane can "
        f"account for")
    assert found - expected == set(), (
        f"a NEW `:Session` upsert site appeared: {sorted(found - expected)} — "
        f"disposition it in DISPOSITIONS (declared, journal-it, or deferred with "
        f"a reason) before it lands")

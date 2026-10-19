"""#9233 §17.1 — the webhook delivery store **declares** its write lane, so
closing the guarded write handle (#9124) does not silently disable webhooks.

`tortoise/webhook_delivery.py` is a graph-native durable store: `:ContextSubscription`
rows are registrations and `:DeliveryReceipt` rows record outbound HTTP attempts.
It reaches the projection's write handle (`proj.g.query`), and **no** journal record
describes what it writes — the same class as the `:EpMeta` epoch (`#9123`) and the
onboarding metadata (`#9121`). `#9124` closes that handle, so without a declaration
the hosted webhook surface (`hosted_api.create_subscription` →
`_webhook_delivery.create_subscription(sdk._get_proj(), …)`) is refused the moment
the guard is armed.

⛔ WHAT THIS SUITE PINS, AND WHY IT IS NOT VACUOUS. `#9124`'s guard is not on
`main` yet (it lands last, after its declaration children — this issue among
them). So `_instrument` reproduces the third door's *predicate* verbatim —
journal-backed + no declaration in flight + a mutating statement ⇒
`DirectCacheWriteRefused` — and installs it on `_GuardedGraph.query`. That is the
predicate `#9124` pins, so the two cannot disagree about what a declaration is,
and this issue's contract is executable now instead of blocked on a sibling.
`test_the_instrument_refuses_an_undeclared_write` is the control: without it every
"declared" assertion below would also pass on a guard that refuses nothing.

⛔ THE DECLARATION MUST NOT HIDE A REFUSAL. `_run` opens the lane, but two
handlers on the delivery path exist for ORDINARY failures (the index DDL's
"already exists", the dispatcher's cursor/receipt writes) and a broad `except
Exception` there would count a refused statement as a completed write: measured
before the fix, a refused cursor write returned `{'delivered': 1, …}` with
`cursor == 0`, re-POSTing the same event on every pass forever (and
`max_attempts` can never trip, because the success path never calls
`_mark_failed`). Both are pinned below.

⛔ THE DECLARATION IS NOT A DURABILITY UPGRADE, and this suite says so rather
than implying it: `test_a_rebuild_does_not_restore_the_store` runs a REAL
`rebuild_all` and pins that subscriptions are gone afterwards. Recovery for this
class is re-registration, exactly as before — the declaration says *where the
write lives*, not that the write is now replayable.

MUTATION for the declaration tests: drop the `_cache_write_authorized` wrapper in
`webhook_delivery._run`. `test_every_webhook_write_declares_its_lane` fails with
the list of undeclared statements, and `test_the_armed_guard_does_not_refuse_the_product`
fails inside the write itself.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from tortoise.projection import (
    _CACHE_WRITE_AUTHORIZED,
    DirectCacheWriteRefused,
    _GuardedGraph,
    _statement_writes,
)

MODULE = Path(__file__).resolve().parent.parent / "tortoise" / "webhook_delivery.py"

#: The classes this lane declares. A label outside this set means the lane is
#: wider than the declaration claims — the class check, not the (deliberately
#: conservative) statement classifier, is what keeps it narrow.
_DECLARED_LABELS = frozenset({"ContextSubscription", "DeliveryReceipt"})

#: Every function of the module that issues a statement through the funnel
#: (`_run`), and how this suite covers it. `_EXERCISED_*` are called by the two
#: walks below; `_STATED_OTHERS` are reached from an exercised function, each
#: with the reason it needs no direct call.
_EXERCISED_WRITERS = frozenset({
    "ensure_subscription_schema", "create_subscription", "delete_subscription",
})
_EXERCISED_READERS = frozenset({
    "get_subscription", "list_subscriptions", "count_subscriptions",
    "read_receipts",
})
_STATED_OTHERS = {
    "_write_receipt": "reached from _mark_delivered/_mark_failed, both exercised",
    "_mark_delivered": "reached from dispatch_pending, exercised on a 200",
    "_mark_failed": "reached from dispatch_pending, exercised on a 500",
    "_due_subscriptions": "reached from dispatch_pending before every delivery",
}

#: Functions that query a RAW graph handle (`graph`, not `proj.g`) — the sweep's
#: injected handle, which may be `db.select_graph(<name>)` and therefore carries
#: no §17.1 door at all. All three are READS; the pin is that they stay reads.
_RAW_HANDLE_READERS = frozenset({"event_head_seq", "due_subscription_count",
                                 "_events_head"})

#: Every function that REFERENCES the projection's handle (`proj.g` / `proj.db`)
#: in any role — receiver of a call, or an argument handed to a helper (which is
#: how `event_head_seq(proj.g)` reaches it). Pinned so a new handle-touching site
#: is a decision here rather than an invisible second writer.
_HANDLE_TOUCHING_FUNCTIONS = frozenset({
    "_run",                      # the funnel itself (2 call sites)
    "_due_subscriptions",        # head read + the due query
    "dispatch_pending",          # reads the head before dispatching
})


@pytest.fixture
def journal_path(tmp_path) -> Path:
    return tmp_path / "journal.jsonl"


@pytest.fixture
def sdk_with_journal(tmp_path, monkeypatch, journal_path):
    """A real SDK with a configured journal, so §17.1's guard is ARMED.

    The same shape `tests/test_9079_onboarding_declared_lane.py` uses: an
    explicit `db_path` keeps the fixture embedded when no URI is configured, and
    `event_log_path` is what arms the guard.
    """
    from tortoise.sdk import TortoiseSDK

    monkeypatch.setenv("TORTOISE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("TORTOISE_WEBHOOK_DISPATCH", raising=False)
    sdk = TortoiseSDK(str(tmp_path / "webhook9233.db"),
                      event_log_path=str(journal_path))
    try:
        yield sdk
    finally:
        sdk.close()


class _ScriptedTransport:
    """The transport seam `dispatch_pending` takes: one scripted reply per POST."""

    def __init__(self, script=()):
        self.script = list(script)
        self.calls: list[tuple[str, bytes, dict]] = []

    def post(self, url, body, headers):
        self.calls.append((url, body, headers))
        item = self.script.pop(0) if self.script else 200
        if item == "err":
            return None, "connection refused"
        return item, None


def _instrument(monkeypatch, *, refuse: bool | str):
    """Install #9124's predicate on `_GuardedGraph.query`; record every statement
    the webhook writers issue as ``(statement, reason, writes)``.

    `refuse` selects which mutating statements the guard REFUSES:
    ``False`` (refuse nothing — the recording mode), ``True`` (refuse every
    UNDECLARED write — #9124 armed), or a substring (refuse that statement
    whether or not a declaration is in flight — used to aim a refusal at ONE
    handler and prove the caller does not swallow it; the swallow is what makes a
    missing declaration silent, so the refusal must be simulable independent of
    the declaration being present).

    `reason` is `_CACHE_WRITE_AUTHORIZED` AT THE WRITE — read inside the handle
    dispatch, which is where #9124's guard will read it — so "the write ran
    inside a declaration" is observed, not inferred from source.
    """
    original = _GuardedGraph.query
    seen: list[tuple[str, str | None, bool]] = []

    def instrumented(self, cypher, params=None, timeout=None):
        writes = _statement_writes(cypher)
        reason = _CACHE_WRITE_AUTHORIZED.get()
        text = " ".join((cypher or "").split())
        seen.append((text, reason, writes))
        aimed = isinstance(refuse, str) and refuse.lower() in text.lower()
        undeclared = (refuse is True and reason is None)
        if ((aimed or undeclared) and writes
                and getattr(self._proj, "_journal_backed", False)):
            raise DirectCacheWriteRefused(
                "§17.1 third door (#9079/#9124): a mutating statement reached "
                "`proj.g` with no declaration in flight — statement="
                + text[:160]
            )
        return original(self, cypher, params=params, timeout=timeout)

    monkeypatch.setattr(_GuardedGraph, "query", instrumented)
    return seen


def _seed_stream(proj, n: int = 2) -> list[int]:
    """Seed ``n`` durable ``:GraphEvent`` rows — the stream dispatch reads.

    Written BEFORE the instrument is installed, through `event_store` (whose
    declaration is `EventAPI._emit`'s, not this lane's). It is the stream this
    module READS; writing it is not under test here.
    """
    from tortoise import event_store

    seqs = []
    for i in range(n):
        seq = event_store.next_seq(proj)
        event_store.append_event(
            proj, seq, "PointAdded",
            {"id": f"p-{seq}", "seq": seq, "payload": {"i": i}},
            event_id=f"evt-{seq}-{i}")
        seqs.append(seq)
    return seqs


def _walk_the_write_set(proj) -> str:
    """Every MUTATING writer of this lane, in a plausible endpoint order.

    Returns the subscription id used, so a caller can re-read it.
    """
    from tortoise import webhook_delivery as wd

    sub = wd.create_subscription(
        proj, url="https://hooks.example.com/cb", types=["PointAdded"],
        description="9233")
    # a successful delivery: _mark_delivered + _write_receipt(CREATE) + the prune
    wd.dispatch_pending(proj, transport=_ScriptedTransport([200]), batch=1)
    # a failed delivery: _mark_failed + _write_receipt(retrying)
    wd.dispatch_pending(proj, transport=_ScriptedTransport([500]), batch=1)
    # the delete door: two DETACH DELETE statements
    assert wd.delete_subscription(proj, sub["sub_id"]) is True
    return sub["sub_id"]


def _walk_the_read_set(proj) -> None:
    """Every READER of the lane — the other half of the AST census."""
    from tortoise import webhook_delivery as wd

    sub = wd.create_subscription(proj, url="https://hooks.example.com/cb2",
                                 types=[], description="9233 reads")
    wd.get_subscription(proj, sub["sub_id"])
    wd.list_subscriptions(proj)
    wd.count_subscriptions(proj, include_disabled=True)
    wd.read_receipts(proj, sub["sub_id"])
    wd.due_subscription_count(proj.g)
    wd.event_head_seq(proj.g)
    wd.delete_subscription(proj, sub["sub_id"])


# ── the control: the instrument is a real guard ───────────────────────────

def test_the_instrument_refuses_an_undeclared_write(sdk_with_journal,
                                                    monkeypatch):
    """Without this, every "declared" assertion below would also pass on a guard
    that refuses nothing (the #7951 round-2 fail-open lesson).

    The statement is issued straight at the handle, outside any funnel, which is
    exactly what #9124 refuses.
    """
    proj = sdk_with_journal._get_proj()
    assert proj._journal_backed is True, (
        "the fixture did not arm §17.1 — an unarmed projection is outside the "
        "rule and every assertion in this file would be vacuous"
    )
    _instrument(monkeypatch, refuse=True)
    with pytest.raises(DirectCacheWriteRefused):
        proj.g.query("CREATE (x:Undeclared9233 {id: 'x'})")


# ── the declaration: every write of this lane runs inside it ──────────────

def test_every_webhook_write_declares_its_lane(sdk_with_journal, monkeypatch):
    """Was RED before this change: `_CACHE_WRITE_AUTHORIZED` was `None` at every
    one of these writes, so #9124's handle guard refuses the product's own
    webhook surface. MUTATION: drop the declaration in `_run` and this fails
    with a list of undeclared statements.
    """
    from tortoise import webhook_delivery as wd

    proj = sdk_with_journal._get_proj()
    assert proj._journal_backed is True
    _seed_stream(proj, 2)

    seen = _instrument(monkeypatch, refuse=False)
    _walk_the_write_set(proj)

    writes = [(c, r) for c, r, w in seen if w]
    assert len(writes) >= 6, (
        f"the writers issued {len(writes)} mutating statement(s); the walk no "
        "longer covers the delivery store's write set, so this pin went stale"
    )
    undeclared = [c for c, r in writes if r is None]
    assert not undeclared, (
        "these webhook writes reached the cache handle OUTSIDE any §17.1 "
        "declaration; #9124's guard will refuse them and the hosted door "
        f"reports a refusal as an ordinary failure: {undeclared}"
    )
    assert {r for _c, r in writes} == {wd.WEBHOOK_WRITE_LANE}, (
        "the lane must declare ONE name, so the guard and the refusal message "
        f"point at it; saw {sorted({r for _c, r in writes})}"
    )


def test_the_lane_name_is_pinned():
    """The declaration REASON is what the pin reads back at the write handle.

    It is not rendered into a refusal (`_cache_write_authorized`: a refusal fires
    precisely when the var is None, so there is no reason in flight to print) —
    which is why this is a name a TEST cites, not a message an operator sees.
    Read against a LITERAL, not against `wd.WEBHOOK_WRITE_LANE`: an expectation
    read from the code under test cannot fail when that code is renamed (#9233
    review P2).
    """
    from tortoise import webhook_delivery as wd

    assert wd.WEBHOOK_WRITE_LANE == "webhook.delivery-store", (
        "the lane name changed; update §17.1's carve-out paragraph and the "
        "tests/test_9079_* ledger with it, deliberately"
    )
    source = MODULE.read_text(encoding="utf-8")
    assert 'WEBHOOK_WRITE_LANE = "webhook.delivery-store"' in source, (
        "the lane constant is gone from the module source — the declaration "
        "no longer exists where the writer reads it"
    )
    assert "#9233" in source, (
        "the declaration must cite the issue that made the decision, so the "
        "choice is reviewable where the writer reads it"
    )


def test_the_armed_guard_does_not_refuse_the_product(sdk_with_journal,
                                                     monkeypatch):
    """The same walk with #9124's predicate REFUSING: it must complete.

    This is the issue's own indicator — "with the guard armed, the store's
    writes still land" — and it fails *inside* the write, not on a set
    comparison, so it cannot be satisfied by widening an assertion.
    """
    from tortoise import webhook_delivery as wd

    proj = sdk_with_journal._get_proj()
    _seed_stream(proj, 2)
    _instrument(monkeypatch, refuse=True)
    sub_id = _walk_the_write_set(proj)

    assert wd.get_subscription(proj, sub_id) is None, (
        "the walk's own delete did not land under the armed guard"
    )
    assert wd.list_subscriptions(proj) == [], (
        "a subscription survived the walk — the delete is the statement most "
        "likely to be refused after the write it depends on"
    )


def test_a_read_runs_unscoped(sdk_with_journal, monkeypatch):
    """The lane is opened for WRITES, not blanket-opened for the module.

    If `_run` declared every statement, the class check below would be the only
    thing keeping the lane narrow — and a read would be "declared", which is a
    claim this lane does not make.
    """
    proj = sdk_with_journal._get_proj()
    seen = _instrument(monkeypatch, refuse=False)
    _walk_the_read_set(proj)

    reads = [(c, r) for c, r, w in seen if not w]
    assert reads, "the read walk issued no statement the classifier calls read-only"
    assert {r for _c, r in reads} == {None}, (
        "a read ran with a declaration in flight — `_run` must scope writes only"
    )


# ── the refusal must not be swallowed (P1) ────────────────────────────────

def test_a_refused_cursor_write_is_not_counted_as_delivered(
        sdk_with_journal, monkeypatch):
    """A refused cursor write must be LOUD, not a delivery.

    Measured before the fix: `dispatch_pending` caught the refusal in its
    `except Exception`, returned `{'delivered': 1, …}` with the cursor unmoved,
    and the same event was re-POSTed on every pass forever — `max_attempts` can
    never trip because the success path never calls `_mark_failed`. A §17.1
    refusal is not a subscriber failure, so it propagates.
    """
    from tortoise import webhook_delivery as wd

    proj = sdk_with_journal._get_proj()
    _seed_stream(proj, 2)
    sub = wd.create_subscription(proj, url="https://hooks.example.com/cb",
                                 types=["PointAdded"], description="9233 refuse")

    transport = _ScriptedTransport([200])
    _instrument(monkeypatch, refuse="SET s.cursor=")
    with pytest.raises(DirectCacheWriteRefused):
        wd.dispatch_pending(proj, transport=transport)

    assert transport.calls, "the POST did not happen, so the refusal under test was not reached"
    assert wd.get_subscription(proj, sub["sub_id"])["cursor"] == 0, (
        "the fixture refused the wrong statement — the cursor moved, so this "
        "test proves nothing about a refused cursor write"
    )


def test_a_cursor_failure_is_reported_as_failed_not_delivered(
        sdk_with_journal, monkeypatch):
    """The same counter, for an ORDINARY cursor failure (a graph hiccup, not a
    refusal). At-least-once says re-deliver, so the pass must not report the
    event as delivered: the summary is what a caller logs, and `delivered` with
    the cursor unmoved is the infinite re-POST of #9233 review P1 with a
    non-refusal cause."""
    from tortoise import webhook_delivery as wd

    proj = sdk_with_journal._get_proj()
    _seed_stream(proj, 2)
    wd.create_subscription(proj, url="https://hooks.example.com/cb",
                           types=["PointAdded"], description="9233 hiccup")

    def _boom(*_a, **_kw):
        raise RuntimeError("graph hiccup on the cursor write")

    monkeypatch.setattr(wd, "_mark_delivered", _boom)
    summary = wd.dispatch_pending(proj, transport=_ScriptedTransport([200]))

    assert summary["delivered"] == 0, (
        "a pass whose cursor write failed reported a delivery — the caller's "
        f"log says the event landed when it will be re-POSTed: {summary}"
    )
    assert summary["failed"] >= 1, f"the failure is not counted at all: {summary}"


def test_a_refused_failure_write_is_not_swallowed(sdk_with_journal,
                                                  monkeypatch):
    """The FAILURE branch's refusal must be loud too.

    `_mark_failed` writes `SET s.attempts=…` and the next backoff. Swallowed, the
    attempt is never recorded: the subscription is immediately due again and
    `max_attempts` can never trip, so a dead endpoint is retried forever with no
    disable. The `refuse=True` walks cannot cover this (the declaration IS in
    flight there), so it is aimed here (#9233 review round 2).
    """
    from tortoise import webhook_delivery as wd

    proj = sdk_with_journal._get_proj()
    _seed_stream(proj, 2)
    sub = wd.create_subscription(proj, url="https://hooks.example.com/cb",
                                 types=["PointAdded"], description="9233 attempts")

    _instrument(monkeypatch, refuse="SET s.attempts=")
    with pytest.raises(DirectCacheWriteRefused):
        wd.dispatch_pending(proj, transport=_ScriptedTransport([500]))

    assert wd.get_subscription(proj, sub["sub_id"])["attempts"] == 0, (
        "the fixture refused the wrong statement — the attempt landed, so this "
        "test proves nothing about a refused failure write"
    )


def test_a_receipt_failure_does_not_un_deliver_the_event(
        sdk_with_journal, monkeypatch):
    """The cursor is the COMMIT POINT; the receipt is its audit.

    `_mark_delivered` writes the cursor first, so a receipt failure means the
    delivery already committed — reporting it as failed and breaking the batch
    skipped the remaining events and stalled the tenant (#9233 review round 2).
    """
    from tortoise import webhook_delivery as wd

    proj = sdk_with_journal._get_proj()
    _seed_stream(proj, 3)
    wd.create_subscription(proj, url="https://hooks.example.com/cb",
                           types=["PointAdded"], description="9233 receipt")

    def _no_receipt(*_a, **_kw):
        raise RuntimeError("receipt write hiccup")

    monkeypatch.setattr(wd, "_write_receipt", _no_receipt)
    summary = wd.dispatch_pending(proj, transport=_ScriptedTransport([200]))

    assert summary["failed"] == 0 and summary["delivered"] == 3, (
        "a receipt failure was reported as a failed delivery, so the batch "
        f"stalled on an already-committed event: {summary}"
    )


def test_a_refused_index_ddl_does_not_cache_the_schema_flag(
        sdk_with_journal, monkeypatch):
    """`ensure_subscription_schema` swallows "the index already exists" on
    purpose; a REFUSAL is not that, and caching `_SCHEMA_ATTR` over one leaves
    the graph permanently unindexed behind a DEBUG line (#9233 review P1)."""
    from tortoise import webhook_delivery as wd

    proj = sdk_with_journal._get_proj()
    _instrument(monkeypatch, refuse="CREATE INDEX")
    with pytest.raises(DirectCacheWriteRefused):
        wd.create_subscription(proj, url="https://hooks.example.com/cb",
                               types=[], description="9233 ddl")

    assert getattr(proj, wd._SCHEMA_ATTR, False) is False, (
        "the schema flag was cached after a REFUSED DDL — the index will never "
        "be retried and nothing but a DEBUG line records it"
    )


# ── the class check: the lane is narrow ───────────────────────────────────

def _labels_written(statement: str) -> set[str]:
    """Every LABEL-POSITION token in a statement — node labels and relationship
    types, in the `(v:Label {…})`, `-[:REL]->` and backticked-variable spellings.

    Anchored on the label position (after `(` or `[`) so a property VALUE is not
    mistaken for a label; a backticked variable is accepted because an engine
    accepts it, and missing one is the fail-OPEN direction for a class check.
    """
    ident = r"(?:[A-Za-z_]\w*|`[^`]+`)"
    return {m.strip("`") for m in re.findall(
        rf"[(\[]\s*(?:{ident})?\s*:\s*[`\"']?({ident})", statement)}


def test_the_label_reader_reads_labels_and_not_property_values():
    """A class check is only as good as its reader, in both directions: a missed
    label is fail-OPEN; a property value read as a label is a false alarm that
    gets the check weakened."""
    cypher = (
        "MATCH (s:ContextSubscription {sub_id: $sid}) "
        "CREATE (r:DeliveryReceipt {status: 'delivered'}) "
        "MERGE (s)-[:DELIVERED_AS]->(r)"
    )
    assert _labels_written(cypher) == {
        "ContextSubscription", "DeliveryReceipt", "DELIVERED_AS"
    }, _labels_written(cypher)
    assert _labels_written("MATCH (`r`:DeliveryReceipt) RETURN 1") == {
        "DeliveryReceipt"}
    assert _labels_written("MATCH (Receipt {id: $i}) RETURN 1") == set()


def test_every_declared_statement_writes_only_declared_labels(
        sdk_with_journal, monkeypatch):
    """The class check, not the classifier, is what keeps this lane narrow: the
    classifier is deliberately conservative, so a statement it cannot read takes
    the declaration. Every label the DECLARED statements actually write must be
    one the declaration named."""
    proj = sdk_with_journal._get_proj()
    _seed_stream(proj, 2)
    seen = _instrument(monkeypatch, refuse=False)
    _walk_the_write_set(proj)

    declared = [c for c, r, w in seen if w and r is not None]
    assert declared, "no declared statement was recorded — the walk is stale"

    # A LABEL-LESS write (`MATCH (n) DETACH DELETE n`, `CREATE (n {…})`) has an
    # empty label set, so the subset check below would pass it — the class check
    # is fail-open exactly where a bulk wipe lives (#9233 review round 2).
    label_less = [c for c in declared if not _labels_written(c)]
    assert not label_less, (
        "a declared statement writes NO named label (a bulk wipe or an "
        f"unlabelled create): {label_less}"
    )

    offenders = {c: _labels_written(c) for c in declared
                 if _labels_written(c) - _DECLARED_LABELS}
    assert not offenders, (
        "the webhook lane declares labels it does not own; either narrow the "
        f"declaration or name the class in it: {offenders}")


# ── the census: nothing writes outside the funnel ─────────────────────────

def _module_tree() -> ast.Module:
    return ast.parse(MODULE.read_text())


def _enclosing_functions(tree: ast.Module) -> dict[int, str]:
    """Map every node id to the name of the INNERMOST function enclosing it.

    A plain `NodeVisitor` cannot answer this (it would have to record each child
    as it descends); the recursion carries the current name instead.
    """
    owner: dict[int, str] = {}

    def walk(node: ast.AST, current: str) -> None:
        name = current
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            name = node.name
        owner[id(node)] = name
        for child in ast.iter_child_nodes(node):
            walk(child, name)

    walk(tree, "<module>")
    return owner


def _root_name(node: ast.AST) -> str | None:
    """The ROOT identifier of an attribute/call chain (`proj.g.query(…)` -> proj)."""
    while True:
        if isinstance(node, ast.Call):
            node = node.func
            continue
        if isinstance(node, ast.Attribute):
            node = node.value
            continue
        if isinstance(node, ast.Name):
            return node.id
        return None


def test_every_handle_call_is_the_pinned_verb_set():
    """The census. `#9233` asks for a SINGLE funnel "so the census can pin it",
    and this is that pin — over EVERY verb, not just `query`.

    A write reaches `proj.g` through `query`, `_query`, `ro_query`,
    `execute_command("GRAPH.QUERY", …)`, `create_*`/`drop_*` — so a census that
    matched only `.query` would go blind exactly when the module was rewritten to
    use a different verb (#9233 review P2). The verb set is pinned instead: today
    this module only ever calls `query`, at two call sites, both inside `_run`.
    """
    tree = _module_tree()
    owner = _enclosing_functions(tree)

    verbs: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if _root_name(node.func) not in {"proj", "graph"}:
            continue
        chain = []
        cur: ast.AST = node.func
        while isinstance(cur, ast.Attribute):
            chain.append(cur.attr)
            cur = cur.value
        # only handle-rooted chains (`…g.verb(` / `…db.verb(`); `proj.rebuild_all()`
        # is the projection's own API, not the handle
        if not ({"g", "db"} & set(chain)):
            continue
        verbs.setdefault(node.func.attr, set()).add(owner.get(id(node), "<module>"))

    assert verbs == {"query": {"_run"}}, (
        "the handle is no longer reached only through `_run` with `query`: "
        f"{ {v: sorted(f) for v, f in verbs.items()} }. Every statement this "
        "module issues must be classified and (when mutating) declared — route "
        "it through `_run` or pin the new site with its reason"
    )


def test_every_handle_reference_is_pinned():
    """`proj.g` / `proj.db` can also travel as an ARGUMENT (`event_head_seq(proj.g)`),
    where the writing happens inside the callee and no receiver scan can see it.
    Pin every enclosing function that touches the handle at all."""
    tree = _module_tree()
    owner = _enclosing_functions(tree)

    sites = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and node.attr in {"g", "db"}
                and isinstance(node.value, ast.Name) and node.value.id == "proj"):
            sites.add(owner.get(id(node), "<module>"))

    assert sites == set(_HANDLE_TOUCHING_FUNCTIONS), (
        "the set of functions that touch the projection's handle changed: "
        f"{sorted(sites)} — `_HANDLE_TOUCHING_FUNCTIONS` is the pin; a new one "
        "must be routed through `_run` (or stated, if it only READS)"
    )


def _render_fstring(node: ast.expr) -> str | None:
    """Evaluate a Cypher f-string against the MODULE's own namespace.

    Without this, a site whose statement is built from `_DUE_PREDICATE` /
    `_BEHIND_PREDICATE` is skipped by the scan — and that is the site the sweep
    runs per tenant graph per interval, i.e. exactly the one a vacuous census
    must not lose (#9233 review P2).
    """
    from tortoise import webhook_delivery as wd

    try:
        return eval(compile(ast.Expression(body=node), "<census>", "eval"),
                    vars(wd))
    except Exception:  # a site we cannot render is reported, not skipped
        return None


def test_raw_handle_queries_are_the_pinned_read_only_set():
    """`graph.query` (the SWEEP's injected handle, which may be
    `db.select_graph(<name>)` and sits below the door entirely) is allowed — but
    every such site must stay a READ, and the set is pinned so a new one is a
    decision rather than a silent bypass.

    Every site's statement is classified, INCLUDING the f-string one: a site that
    cannot be rendered fails here rather than being skipped."""
    tree = _module_tree()
    owner = _enclosing_functions(tree)

    sites: set[str] = set()
    unrendered: list[str] = []
    statements: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "query":
            continue
        if _root_name(node.func) != "graph":
            continue
        fn = owner.get(id(node), "<module>")
        sites.add(fn)
        literal = node.args[0] if node.args else None
        rendered = (literal.value if isinstance(literal, ast.Constant)
                    and isinstance(literal.value, str)
                    else _render_fstring(literal) if literal is not None else None)
        if rendered is None:
            unrendered.append(fn)
        else:
            statements.append(rendered)

    assert not unrendered, (
        "a raw-handle site's statement could not be rendered, so its read-only "
        f"claim is unverified — {unrendered}"
    )
    assert sites == set(_RAW_HANDLE_READERS), (
        f"raw-handle query sites changed: {sorted(sites)} — `_RAW_HANDLE_READERS` "
        "is the pin; a new one needs a reason and, if it MUTATES, it does not "
        "belong on a raw handle at all"
    )
    mutating = [s for s in statements if _statement_writes(s)]
    assert not mutating, (
        "a raw-handle query MUTATES; the raw handle carries no §17.1 door, so "
        f"this write is undeclarable by construction: {mutating}"
    )


def test_every_funnel_caller_is_covered_or_stated():
    """AST vs the two walks: a NEW `_run`-calling function fails here instead of
    being silently uncovered — which is what makes the declaration's "the class
    check is the counterweight" claim true rather than aspirational."""
    tree = _module_tree()
    owner = _enclosing_functions(tree)

    callers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "_run":
            callers.add(owner.get(id(node), "<module>"))

    covered = (_EXERCISED_WRITERS | _EXERCISED_READERS | set(_STATED_OTHERS))
    assert callers == covered, (
        "the funnel's caller set drifted from what this suite covers/excuses: "
        f"unseen={sorted(callers - covered)} stale={sorted(covered - callers)}"
    )


# ── what the declaration does NOT claim ───────────────────────────────────

def test_a_rebuild_does_not_restore_the_store(sdk_with_journal, journal_path,
                                              monkeypatch):
    """This is a DECLARATION, not a durability upgrade.

    A `:ContextSubscription` is graph-native operational state with no journal
    record describing it, so a `rebuild` from the record alone cannot restore it.
    Recovery for this class is re-registration. A REAL `rebuild_all` is run here:
    wiping the graph would pass whether or not anything was journalled, which is
    the property this test claims to pin (#9233 review P2).
    """
    from tortoise import webhook_delivery as wd

    proj = sdk_with_journal._get_proj()
    sub = wd.create_subscription(proj, url="https://hooks.example.com/keep",
                                 types=[], description="9233 rebuild")
    assert wd.get_subscription(proj, sub["sub_id"]) is not None

    _instrument(monkeypatch, refuse=False)
    proj.rebuild_all(str(journal_path.parent), confirm_destructive=True)

    assert wd.get_subscription(proj, sub["sub_id"]) is None, (
        "the store was restored by a rebuild, so this pin no longer describes "
        "recovery for this class — re-read the declaration before deleting it"
    )

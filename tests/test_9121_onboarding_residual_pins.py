"""#9121 — the seam the §17.1 declaration does **NOT** cover, pinned in code.

`tests/test_9079_onboarding_declared_lane.py` owns the declaration itself: it
arms the `#9124` predicate on the guarded handle and proves the onboarding lane
keeps working with the guard closed. This file owns the *residual*, and exists
because that suite cannot see it: the five `eager_init_query()` call sites
CREATE `:OnboardingState` on `proj.db.select_graph(<graph>)` — §17.1's escape
(3) — so they meet neither `state._run` nor `_onboarding_write_scope`, and they
keep working after `#9079` precisely *because* they sit below the handle.

⛔ WHY THIS FILE IS NOT A SECOND COPY OF test_9079. A previous draft of this
work asserted the residual with a runtime probe that built its OWN handle,
monkeypatched it and issued the statement ITSELF — so it recorded "no
declaration in flight" because the *test* did not open one, not because
production does not. That is a tautology: it passes on the pre-fix tree and it
would keep passing after the residual were fixed. Everything here reads the
PRODUCTION source with `ast` and with the same reader the note is written for,
so each assertion has a way to fail:

* a SIXTH `eager_init_query()` site appearing anywhere in the SDK / hosted API
  fails `test_the_note_and_the_ast_inventory_agree` (the note would be stale);
* a site being DELETED fails `test_the_note_and_the_ast_inventory_agree` too —
  it compares the measured inventory against the recorded one, so a site that
  disappears is a mismatch. NOT the linkage pin below: that one iterates only
  the sites the finder RETURNS, so one fewer site is one fewer iteration and no
  failure at all (#9150 review P2 — a map that attributes a mutation to the
  wrong pin is how a maintainer concludes an uncovered mutation is covered);
* a site being MIGRATED off `select_graph` fails
  `test_every_eager_init_site_issues_its_statement_on_the_escape_three_handle`,
  which ASSERTS THE LINKAGE — the statement must be issued by a `query(...)`
  call on the handle the same function bound from `select_graph(...)`. The two
  wrong closures red it: `proj.g.query(...)` (the §17.1 violation this file
  exists to see) and `state._run(...)` (the honest fix, which would leave the
  note describing a gap that no longer exists);
* the day one of those sites is DECLARED — the honest fix, `#9157` —
  `test_no_escape3_function_opens_a_declaration_anywhere_yet` turns RED *when
  the declaration is a `with` in the same function*, so the note, the guard's
  opener inventory and this file have to move together instead of drifting back
  to a universal ("every onboarding write is declared") that the escape
  disproves. The
  `_run` route opens its scope in another file and is caught by the linkage
  pin below;
* the fact that the detector is *capable* of firing is not argued, it is
  executed: `test_the_declaration_detector_can_fail` runs it over a synthetic
  declaration **for every recorded opener** and requires a hit, and
  `test_the_issuance_detector_reds_on_the_guarded_handle_and_on_run` does the
  same for the linkage pin over the two closure shapes.

The claim these pins protect, stated in the terms of the code: every statement
this package issues **through `state._run`** runs inside the declaration; the
`eager_init_query()` callers do not, and are unjournalled AND undeclared.

⛔ AND IT IS NOT THE ONLY INVENTORY OF THIS SEAM — the closer neighbour is
`tests/test_write_ahead_mint.py::_MINT_SITES`, which already walks
`tortoise/sdk.py` / `tortoise/hosted_api.py` / `tortoise/__main__.py` for
`:TeamMeta` mint sites and recognises `eager_init_query` bindings. The two share
**the site set** (a site that appears or disappears moves both), and each owns
what the other cannot see:

* unique to THIS file: the per-function **count** (`register_user` issues two —
a third arm moves nothing in `_MINT_SITES`), the note↔AST agreement, and the
  `#9157` no-declaration coupling;
* unique to `_MINT_SITES`: the write-ahead journaling ORDER, and the
  `tortoise/__main__.py::_cmd_key_create` site, which mints `:TeamMeta` through a
  different shape and therefore is not an `eager_init_query()` site at all.

Rather than keeping two free-handed copies that can drift apart silently,
`test_the_escape3_sites_are_the_mint_sites_that_use_eager_init` asserts the
RELATIONSHIP against `_MINT_SITES` itself.
"""
from __future__ import annotations

import ast
import re
import textwrap
from functools import cache
from pathlib import Path

from tortoise.onboarding import state as _os
from tortoise.projection import _statement_writes

_REPO = Path(__file__).resolve().parents[1]

#: The `:TeamMeta` mint sites that are NOT `eager_init_query()` sites — they mint
#: through another shape. Named, so the difference between this file's inventory
#: and `test_write_ahead_mint.py::_MINT_SITES` is an assertion rather than a
#: remainder nobody reads.
_NON_EAGER_MINT_SITES = {("tortoise/__main__.py", "_cmd_key_create")}


def _mint_sites_from_its_owner() -> set[tuple[str, str]]:
    """`_MINT_SITES`, read from the suite that owns it.

    Imported by path rather than copied: a second hand-written copy is the drift
    seam this file exists to close (a site set is not a thing to spell twice).
    """
    import importlib.util

    path = Path(__file__).resolve().parent / "test_write_ahead_mint.py"
    spec = importlib.util.spec_from_file_location(
        "_write_ahead_mint_owner", path
    )
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return set(module._MINT_SITES)

#: The primitives that OPEN a §17.1 declaration. A `with` over any of these is
#: what "declared" means; a site that acquires one has left the residual.
#: Every scope opener `tortoise/projection/__init__.py` defines is listed ON
#: PURPOSE — an earlier revision named only the three onboarding-ish ones, so
#: closing the residual through `_graph_metadata_write_scope(...)` (the
#: graph-metadata lane, and the shape `onboarding/state.py`'s SEAM block names)
#: left every pin green. `test_the_declaration_detector_can_fail` runs the
#: detector over EACH member, so a new opener cannot silently reopen the hole
#: without also failing that instrument check's recorded list below.
#:
#: ⚠️ Scope of what this detects: a `with` in the SAME function. The likeliest
#: shape of the honest fix (#9157) is routing the statement through
#: `onboarding/state.py::_run`, which opens its scope in ANOTHER file — that
#: route is caught by
#: `test_every_eager_init_site_issues_its_statement_on_the_escape_three_handle`,
#: which reds when the statement stops being issued on the `select_graph`
#: handle (including when it is handed to `_run`).

#: (file, enclosing function) -> how many `eager_init_query()` statements that
#: function issues. The COUNT is part of the pin: `register_user` issues two
#: (the self-service arm and the Supabase-mode arm), and a slice of that pair
#: disappearing without the note being re-read is exactly the drift here.
_ESCAPE3_CALL_SITES = {
    ("tortoise/sdk.py", "org_create"): 1,
    ("tortoise/hosted_api.py", "provision_tenant"): 1,
    ("tortoise/hosted_api.py", "register_user"): 2,
    ("tortoise/hosted_api.py", "_eager_provision_org_graph"): 1,
}

#: Spelled numerals for the count above, so the four records' PROSE count is a
#: pinned claim instead of one a hand edit can change while every assertion stays
#: green (#9150 review cycle 4: "the five" was unpinned in all four records).
_COUNT_WORDS = {0: "zero", 1: "one", 2: "two", 3: "three", 4: "four",
                5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine"}

#: A `module::function` site name, as the four records spell it.
_SITE_NAME_IN_NOTE = re.compile(r"(tortoise/[\w/]+\.py)::(\w+)")

#: The same, spelled by BASENAME — how the plan doc and the ledger table do it
#: (`sdk.py::org_create`, `hosted_api.py::register_user`).
_SITE_BASENAME_IN_RECORD = re.compile(r"`(\w+\.py)::(\w+)`")


# ── record slices: pin the RECORD, not the module that carries it ──────────
def _record(*, path: str, start: str, end: str) -> str:
    """The flattened prose of ONE record's region, `start` up to the next `end`.

    Every prose pin in this file must read the region its claim is about. A
    whole-module scan passes while the region says NOTHING, whenever the same
    words appear elsewhere in a 30 000-line module — measured four times in
    review (#9150): `eager_init_query`/`select_graph` occur ABOVE the escape-(3)
    note (the module docstring, `def eager_init_query`, the #4641 width bullet),
    `#9157` occurs in `_live_write_scope`'s docstring BELOW the inventory bullet,
    the site names could be re-added anywhere in `state.py`, and `#9144` could be
    cited anywhere in the projection module.

    The boundary is ASSERTED, not assumed: a moved or deleted marker raises here
    instead of silently widening the scan back to the whole file (which is how
    every one of those four false PASSes happened).

    Leading comment markers are STRIPPED before flattening: these records are
    `#`-commented Python, and a plain `split()` leaves a bare `#` between the
    words of a wrapped sentence (measured: "the five # `eager_init_query()` call
    sites"), so a claim was assertable only within one physical line.
    """
    source = (_REPO / path).read_text(encoding="utf-8")
    lo = source.find(start)
    hi = source.find(end, lo + 1) if lo != -1 else -1
    assert lo != -1 and hi != -1, (
        f"`{path}` no longer carries the region this pin reads "
        f"({start!r} … {end!r}) — the record moved or was deleted, so every "
        "claim about it must be re-derived. A missing boundary must never "
        "widen the scan to the whole file"
    )
    lines = (ln.lstrip().removeprefix("#").strip()
             for ln in source[lo:hi].splitlines())
    return " ".join(" ".join(lines).split())


def _assert_site_count(region: str, template: str, what: str,
                       n: int | None = None) -> None:
    """The record's NUMERAL must equal the AST inventory's count.

    `template` is the record's own sentence with `{n}` standing in for the
    numeral — the same convention the exact-phrase pins already use, so a
    re-wording reds this instead of silently un-pinning the count.

    `n` overrides the TOTAL for a record's PER-SITE numeral (#9150 review cycle
    16, P2): `register_user` issues TWO of the five, and both records say so in
    prose while only the total was pinned — editing either "two" to "three" left
    a record contradicting the AST with every pin green.
    """
    if n is None:
        n = sum(_ESCAPE3_CALL_SITES.values())
    word = _COUNT_WORDS.get(n)
    assert word is not None, (
        f"the escape-(3) inventory now holds {n} sites — extend `_COUNT_WORDS` "
        "and update the four records' numerals in the same change"
    )
    for candidate in (word, word.capitalize(), word.upper()):
        if template.format(n=candidate) in region:
            return
    raise AssertionError(
        f"{what} no longer says {template.format(n=word)!r} — the escape-(3) "
        f"site count in prose and the AST inventory ({n}) disagree. A count in "
        "prose is a claim like any other: update the numeral in the same change "
        "as the inventory (#9157)"
    )


@cache
def _parse(rel: str) -> tuple[ast.Module, dict[ast.AST, ast.AST]]:
    tree = ast.parse((_REPO / rel).read_text(encoding="utf-8"))
    parents = {
        child: node
        for node in ast.walk(tree)
        for child in ast.iter_child_nodes(node)
    }
    return tree, parents


def _enclosing_function(node: ast.AST, parents: dict[ast.AST, ast.AST]):
    while node is not None and not isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        node = parents.get(node)
    return node


def _bound_names(target: ast.AST) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        out: set[str] = set()
        for element in target.elts:
            out |= _bound_names(element)
        return out
    return set()


def _named(nodes, names: tuple[str, ...]) -> bool:
    """Does any node in `nodes` NAME one of `names`?

    BOTH spellings, on purpose: a `with` clause may say `_live_write_scope(...)`
    or `_proj._live_write_scope(...)`, and `_DECLARATION_PRIMITIVES` carries no
    origin module. Pinned by `test_the_declaration_detector_can_fail`'s attribute
    case (#9150 review cycle 13, P2): while that arm was unexercised it could be
    deleted with the suite green, after which a declaration written as
    `with _proj._live_write_scope(...)` inside `sdk.py::org_create` passed every
    pin — a silent false PASS on the coupling the note promises.
    """
    for root in nodes:
        for node in ast.walk(root):
            if isinstance(node, ast.Name) and node.id in names:
                return True
            if isinstance(node, ast.Attribute) and node.attr in names:
                return True
    return False


def _bindings(enclosing: ast.AST) -> list[tuple[ast.AST, ast.AST | None]]:
    """Every `(target, value)` binding inside `enclosing`, in every form a
    function BODY can spell.

    Covered: `Assign`, `AnnAssign`, `AugAssign`, `For`/`AsyncFor`,
    `With`/`AsyncWith`, `NamedExpr` (walrus), comprehension targets,
    `except … as`, and the `match` capture patterns (`MatchAs`, `MatchStar`,
    `MatchMapping`'s `__` rest). Out of scope on purpose: scope-ENTRY bindings
    (`def` parameters, `import … as`), which bind a name before any handle exists
    in this body rather than REBINDING one, and `del`, which unbinds.

    ⛔ `Assign`/`AnnAssign` only was the first revision (#9150 review cycle 9,
    P2). A handle-like name can equally be bound by a `for` target, a
    `with … as`, a walrus, an `AugAssign`, a comprehension target or an
    `except … as` — and on those shapes the detector stayed GREEN, which is the
    false PASS the handle rule exists to prevent. `match` captures were missed in
    the same way one cycle later (cycle 10): `case graph:` rebound the name while
    `rebound` stayed empty. `value` is `None` where the syntax has no single value
    expression (`AugAssign`, `except … as`, match captures); such a binding is
    treated as a REBINDING, i.e. it fails closed.
    """
    out: list[tuple[ast.AST, ast.AST | None]] = []
    for sub in ast.walk(enclosing):
        if isinstance(sub, ast.Assign):
            out.extend((target, sub.value) for target in sub.targets)
        elif isinstance(sub, ast.AnnAssign):
            out.append((sub.target, sub.value))
        elif isinstance(sub, ast.AugAssign):
            out.append((sub.target, None))
        elif isinstance(sub, (ast.For, ast.AsyncFor)):
            out.append((sub.target, sub.iter))
        elif isinstance(sub, (ast.With, ast.AsyncWith)):
            for item in sub.items:
                if item.optional_vars is not None:
                    out.append((item.optional_vars, item.context_expr))
        elif isinstance(sub, ast.NamedExpr):
            out.append((sub.target, sub.value))
        elif isinstance(sub, ast.comprehension):
            out.append((sub.target, sub.iter))
        elif isinstance(sub, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar,
                             ast.MatchMapping)):
            # `except … as name`, `case name`, `case _ as name`, `case [*name]`,
            # `case {**name}` — a capture, never an expression.
            bound = sub.name if hasattr(sub, "name") else sub.rest
            if bound:
                out.append((ast.Name(id=bound, ctx=ast.Store()), None))
    return out


def _is_select_graph(value: ast.AST | None) -> bool:
    return (isinstance(value, ast.Call)
            and getattr(value.func, "attr", None) == "select_graph")


def _escape_handle_names(enclosing: ast.AST) -> set[str]:
    """Names bound from a `select_graph(...)` call inside `enclosing`."""
    handles: set[str] = set()
    for target, value in _bindings(enclosing):
        if _is_select_graph(value):
            handles |= _bound_names(target)
    return handles


def _rebound_handles(enclosing: ast.AST, handles: set[str]) -> set[str]:
    """Escape-handle names ALSO bound to something that is not `select_graph`.

    The handle set is per FUNCTION; a binding is per BINDING SITE (#9150 review
    cycle 6, P2). `tortoise/hosted_api.py::register_user` issues TWO sites, one
    per arm of `if is_supabase_enabled(): … else:`, and BOTH arms bind the same
    name `org_graph` — so closing one arm on `proj.g` is exactly the §17.1
    violation this file exists to see, and a per-function handle set certified
    it green (measured on that two-arm shape: `offenders: []`). Fail closed: a
    name bound both ways at this head is reported as an offender, because the
    detector cannot tell which binding the sink used.

    "Both ways" covers every binding form a function BODY can spell —
    `Assign`/`AnnAssign`/`AugAssign`, `for`, `with … as`, walrus, comprehension
    targets, `except … as`, and `match` captures (#9150 review cycles 9 and 10,
    P2). Measured at cycle 9 on four equivalent two-binding shapes, a `for`
    target, a walrus, a `with … as` and a comprehension target all left the
    detector GREEN where the plain `Assign` red it; cycle 10 then measured the
    same for `case graph:`. Scope-ENTRY bindings (`def` parameters, `import … as`)
    are out of scope — they bind a name before a handle exists in this body rather
    than rebinding one.
    """
    rebound: set[str] = set()
    for target, value in _bindings(enclosing):
        if _is_select_graph(value):
            continue
        rebound |= _bound_names(target)
    return rebound & handles


def _statement_sinks(
    enclosing: ast.AST, issued: set[str]
) -> list[tuple[int, str, str | None, tuple[str, ...]]]:
    """Every call the statement text FLOWS INTO, as `enclosing` reads it.

    `(lineno, callee, receiver, statement names)` — `callee` is the source form
    of the call's `func` (`handle.query`, `proj.g.query`, `_os._run`), and
    `receiver` is the plain name it is called on, or `None` when the receiver
    is an expression (that is itself the signal: `proj.g.query(...)` leaves the
    escape handle). This is what makes the claim "issued on the
    `select_graph` handle" checkable: an earlier revision only asserted that
    SOME `select_graph(...)` existed in the function and that the statement was
    bound to SOME name, so it stayed green under both mutations it claimed to
    catch (`proj.g.query(...)`, and `_run(proj, _init_q, ...)`).
    """
    sinks: list[tuple[int, str, str | None, tuple[str, ...]]] = []
    for call in ast.walk(enclosing):
        if not isinstance(call, ast.Call):
            continue
        used: set[str] = set()
        for arg in call.args:
            used |= {n.id for n in ast.walk(arg) if isinstance(n, ast.Name)}
        for kw in call.keywords:
            if kw.value is not None:
                used |= {n.id for n in ast.walk(kw.value) if isinstance(n, ast.Name)}
        passed = used & issued
        if not passed:
            continue
        receiver: str | None = None
        if isinstance(call.func, ast.Attribute):
            callee = ast.unparse(call.func)
            if isinstance(call.func.value, ast.Name):
                receiver = call.func.value.id
        elif isinstance(call.func, ast.Name):
            callee = call.func.id
        else:
            callee = ast.unparse(call.func)
        sinks.append((call.lineno, callee, receiver, tuple(sorted(passed))))
    return sinks


def _not_on_the_escape_handle(
    enclosing: ast.AST, issued: set[str], handles: set[str],
    rebound: set[str] | None = None,
) -> list[str]:
    """Sinks where the statement leaves the `select_graph` handle, rendered.

    `rebound` is the fail-closed half of the handle rule (#9150 review cycle 6,
    P2): a name the same function ALSO binds from something other than
    `select_graph(...)` cannot certify a sink, because the detector cannot tell
    which binding that sink used.
    """
    rebound = rebound or set()
    offenders: list[str] = []
    for lineno, callee, receiver, passed in _statement_sinks(enclosing, issued):
        if (
            callee.rsplit(".", 1)[-1] == "query"
            and receiver in handles
            and receiver not in rebound
        ):
            continue
        why = (f" — the name is ALSO bound off the `select_graph` handle "
               f"elsewhere in this function: {sorted(rebound)}"
               if receiver in rebound else "")
        offenders.append(
            f"line {lineno}: {callee}({', '.join(passed)}) — "
            f"escape-(3) handle(s): {sorted(handles)}{why}"
        )
    return offenders


#: DERIVED, not hand-listed (#9150 review P2): the recorded set above was a
#: literal, so a NEW opener added to `tortoise/projection/__init__.py` — the
#: shape both #9123 and #9195 used — was invisible to the instrument check,
#: which iterated the literal and therefore could not notice it. This reads the
#: production source and returns every declaring function — DIRECTLY, or by
#: delegating to an opener (`return _live_write_scope(reason)`, the shape
#: #9150 review cycle 8 found invisible) — so a new lane joins the population by
#: existing.
def _declaration_primitives() -> set[tuple[str, str]]:
    """Every §17.1 declaration site the tree defines, as `(module, function)` —
    ALL of `tortoise/`.

    Two populations, because they answer two questions. This one is the DETECTOR
    population: any function that can put a declaration in flight, wherever it
    lives — an OPENER (returns the scope) OR a DOOR (opens and closes it in a
    `with`, then returns the query result: `webhook_delivery.py::_run`,
    `event_store.py::reestablish_watermark`). It is deliberately wider than the
    opener inventory, because `_declaring_functions()` matches
    `with <name>(...)` and a statement issued through a door is exactly as
    journal-eligible as through an opener.

    That distinction is not cosmetic (#9150 review cycle 9, P1): while both
    populations were derived alike, this docstring's `_run` was called "an
    opener" and the opener inventory — where `projection/__init__.py`'s bullet
    list is described as exhaustive — therefore claimed two DOORS, while the
    delegation fixpoint (which propagates by NAME) would enrol a one-line
    forwarder onto a door as an opener.

    Measured before it was widened (#9150 review, P2): a site wrapped in
    `tortoise/projection/edges.py::_structural_edge_write_scope` left 13/13
    green, because the population was projection-only — so "fails the day one of
    those sites enters a declaration" was false for a real, in-use opener.
    `_projection_openers()` below is the INVENTORY population: only
    `projection/__init__.py`, because that is the bullet list a reader is told
    is exhaustive.

    ⛔ IDENTITY, not a bare name (#9150 review cycle 5, P1). A name-keyed set
    cannot see a new site that REUSES an existing name, and a reused name is
    the normal case here — `_run` is already in this population
    (`tortoise/webhook_delivery.py::_run`). Measured: adding a declaring `_run`
    to `tortoise/reader.py` (an `sdk`-only module) left this population
    byte-identical and the pin green, while three of the six arm registrations in
    `config/ci-surfaces.yml` justify themselves by that pin firing.
    """
    return _openers_under(_REPO / "tortoise", opener_only=False)


def _openers_under(root: Path, *, opener_only: bool = True) -> set[tuple[str, str]]:
    """`(module, function)` for every function under `root` that declares.

    A FILE path is allowed too (the inventory population is one module:
    `projection/__init__.py`, the bullet list a reader is told is exhaustive —
    `projection/edges.py`'s `_structural_edge_write_scope` is a real opener that
    is NOT in that list).

    `opener_only=False` asks the DOOR question instead — see `_openers_in()`.
    """
    paths = [root] if root.is_file() else sorted(root.rglob("*.py"))
    return _openers_in(
        {
            str(path.relative_to(_REPO)): _parse(str(path.relative_to(_REPO)))[0]
            for path in paths
        },
        opener_only=opener_only,
    )


def _declares_directly(node: ast.AST) -> bool:
    """Does this function call `_cache_write_authorized()` in its own body?"""
    return any(
        isinstance(inner, ast.Call)
        and (getattr(inner.func, "id", None) == "_cache_write_authorized"
             or getattr(inner.func, "attr", None) == "_cache_write_authorized")
        for inner in ast.walk(node)
    )


def _returns_declaration_scope(node: ast.AST) -> bool:
    """Is this function an OPENER — does it RETURN the declaration scope?

    ⛔ An opener and a DOOR are different objects, and conflating them claimed a
    lane that does not exist (#9150 review cycle 9, P1). `_declares_directly()`
    asks "does the body call the primitive AT ALL", which is the right question
    for the detector population (a door enters a declaration too) and the wrong
    one for the opener INVENTORY: `tortoise/webhook_delivery.py::_run` and
    `tortoise/event_store.py::reestablish_watermark` open the scope in a `with`
    and CLOSE it before returning the query result — they return a value, not a
    context manager, so they cannot be opened by anything, and a reader told the
    bullet list is "every opener this module defines" was told about two doors.
    Worse, the delegation fixpoint propagates by NAME: with a door in the seed, a
    one-line forwarder onto it was enrolled as an opener in both populations,
    and in `projection/__init__.py` that reds the inventory pin with "a new
    opener can authorize a cache write" — which would be untrue.

    The test is the RETURN, because that is what makes a scope openable:
    `_onboarding_write_scope()` qualifies on its `if` branch (the other returns
    `contextlib.nullcontext()`, a no-op scope for a read), while a door's
    returns are the query result and never the primitive.
    """
    return any(
        isinstance(inner, ast.Return)
        and isinstance(inner.value, ast.Call)
        and (getattr(inner.value.func, "id", None) == "_cache_write_authorized"
             or getattr(inner.value.func, "attr", None)
             == "_cache_write_authorized")
        for inner in ast.walk(node)
    )


def _delegates_to(node: ast.AST, openers: set[str]) -> bool:
    """Is this function's whole body `return <a known opener>(...)`?"""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    body = list(node.body)
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    if len(body) != 1 or not isinstance(body[0], ast.Return):
        return False
    value = body[0].value
    return (isinstance(value, ast.Call)
            and (getattr(value.func, "id", None) in openers
                 or getattr(value.func, "attr", None) in openers))


def _openers_in(
    trees: dict[str, ast.Module], *, opener_only: bool = True
) -> set[tuple[str, str]]:
    """`(module, function)` for every function in `trees` that declares.

    Two populations, one derivation, and they are NOT the same set
    (#9150 review cycle 9, P1):

    * `opener_only=True` — the OPENER inventory: a function that RETURNS the
      scope (`_returns_declaration_scope()`), so something can be `with`-ed over
      it. This is the population `tortoise/projection/__init__.py`'s bullet list
      and `_projection_openers()` are about.
    * `opener_only=False` — the DETECTOR population: any function whose body
      calls the primitive, INCLUDING a DOOR (a per-call `with` that closes the
      scope before it returns). `tortoise/webhook_delivery.py::_run` and
      `tortoise/event_store.py::reestablish_watermark` live here and only here.
      `_declaring_functions()` matches `with <name>(...)`, so the `with`-matcher
      needs the door names, and a statement issued through one is exactly as
      journal-eligible as through an opener.

    ⛔ Both are TRANSITIVE over delegation (#9150 review cycle 8, P1).
    `return _live_write_scope(reason)` is the ordinary in-module shape — it is
    the same "two spellings, one primitive" that `onboarding/state.py` has to
    spell out — and a derivation keyed on a DIRECT `_cache_write_authorized` call
    only let such an opener join NEITHER population, so a site could enter a
    declaration with all 15 pins green (measured: a delegating
    `_future_lane_write_scope` around `sdk.py::org_create`'s statement left the
    suite 15/15 passed).

    The fixpoint's seed is this population, which is what keeps a forwarder onto
    a DOOR out of the opener inventory: in `opener_only` mode `_run` is not a
    name it knows. The fixpoint adds 0 members on this tree in both modes — the
    delegation gap is closed without changing either recorded population.
    """
    found: set[tuple[str, str]] = set()
    for rel, tree in trees.items():
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if _returns_declaration_scope(node) if opener_only \
                    else _declares_directly(node):
                found.add((rel, node.name))
    names = {name for _rel, name in found}
    changed = True
    while changed:
        changed = False
        for rel, tree in trees.items():
            for node in ast.walk(tree):
                if (not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        or (rel, node.name) in found):
                    continue
                if _delegates_to(node, names):
                    found.add((rel, node.name))
                    names.add(node.name)
                    changed = True
    return found


def _projection_openers(tree: ast.Module | None = None) -> set[str]:
    """The opener NAMES `tortoise/projection/__init__.py` defines (the inventory).

    NAMES here, unlike the detector population above, because the bullet list a
    reader is shown is a list of names.

    ⛔ `tree` exists for the INSTRUMENT CHECK (#9150 review cycle 15, P2). The
    derivation was itself unpinned: replacing this body with a literal of today's
    four names left the suite green, after which
    `test_the_opener_inventory_is_pinned_so_a_new_lane_moves_this_file` — whose
    docstring promises "derived, then compared" — could no longer fire on a new
    opener in the inventory module. With the module injectable,
    `test_the_opener_inventory_is_derived_not_hand_listed` feeds a synthetic tree
    carrying an added opener and requires it to appear.

    ⛔ The name-keying justification is NOT "this module has no duplicate function
    names" — it has ten, nested and same-scope (`_replay_seq` at two lines,
    `query`, `wrapper`, `__init__` …; measured, #9150 review cycle 11, P2). It is
    that every OPENER is a module-level `def` and no opener NAME is defined twice
    at module level, which
    `test_no_opener_name_is_defined_twice_in_the_inventory_module` pins —
    otherwise a second function sharing an opener's name would be invisible here
    (a set of names has no second slot), the same defect cycle 5 fixed for the
    wide population by keying it on identity.
    """
    rel = "tortoise/projection/__init__.py"
    module = tree if tree is not None else _parse(rel)[0]
    return {name for _rel, name in _openers_in({rel: module})}


#: The primitives that can authorize a cache write, from two places: every opener
#: the tree derives, plus the two names `onboarding/state.py` uses (its opener
#: declares through the raw ContextVar helper rather than a projection opener).
#: NAMES — this is a matcher (`_named`), and the `with`-clause it matches carries
#: no origin module. The IDENTITY-keyed form is `_declaration_primitives()`.
_DECLARATION_PRIMITIVES = (
    {name for _rel, name in _declaration_primitives()}
    | {"_cache_write_authorized", "_onboarding_write_scope"}
)


def _eager_init_calls(tree: ast.Module):
    """(node, call, targets) for every `eager_init_query` CALL in a module.

    Shared by the inventory and the linkage pin (#9150 review, P2): they used to
    hardcode `ast.Assign` + `func.attr` independently, so a bare-name / `Expr` /
    `AnnAssign` / `Return` site was COUNTED by one and never INSPECTED by the
    other — measured: such a site issued on the guarded handle (`proj.g.query`)
    left every pin green while the file reported it as below the handle.

    BOTH call spellings — `_os.eager_init_query(...)` and a bare
    `eager_init_query(...)` — are matched, and both are pinned by
    `test_the_eager_init_finder_sees_both_call_spellings` (#9150 review cycle 13,
    P2: the bare-name arm could be deleted with the suite green, after which a
    sixth site imported by name became invisible to the inventory, the linkage
    pin and the CI-subject sweep, and the note went stale green).
    """
    out = []
    # PARENT-AGNOSTIC (#9150 review, P2): walk every CALL, not "the call inside an
    # Assign/Expr/...". Measured: a sixth site in an ARGUMENT position
    # (`graph.query(*eager_init_query(...))`) was invisible, because the filter
    # looked at the parent node's type first — so the docstring's promise ("for
    # every `eager_init_query` CALL") was false and the "a SIXTH site fails the
    # inventory pin" claim did not hold for that shape.
    for call in ast.walk(tree):
        if not (
            isinstance(call, ast.Call)
            and (getattr(call.func, "attr", None) == "eager_init_query"
                 or getattr(call.func, "id", None) == "eager_init_query")
        ):
            continue
        # the BINDING node, when there is one, so the linkage pin can read the
        # target it was assigned to; the call itself otherwise.
        node = _binding_parent(tree, call) or call
        targets = getattr(node, "targets", None) or (
            [node.target] if isinstance(node, ast.AnnAssign) else [])
        out.append((node, call, targets))
    return out


def _binding_parent(tree: ast.Module, call: ast.AST) -> ast.AST | None:
    """The Assign/AnnAssign whose value IS `call` (None when it is an expression,
    an argument, or a return)."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is call:
            return node
    return None


def _scanned_modules() -> list[str]:
    """Every module the escape-(3) inventory sweep reads: ALL of `tortoise/`.

    ⛔ A NAMED POPULATION, because its width is the whole guarantee (#9150
    review cycle 11, P2): the sentence this file pins is unqualified ("five call
    sites"), so a sixth caller in any other module must be visible. While the
    loop header was inline, narrowing it to the two modules that hold today's
    sites — a plausible cleanup — left 17/17 green and let a planted sixth site
    in `tortoise/selfhost.py` go unseen, so the guarantee held only as long as
    nobody edited a `for` line. `test_the_inventory_scan_sweeps_every_module`
    pins it against an independently computed `rglob`.
    """
    return sorted(str(p.relative_to(_REPO)) for p in (_REPO / "tortoise").rglob("*.py"))


def test_the_inventory_scan_sweeps_every_module():
    """The sweep's WIDTH is the guarantee — pin it, don't leave it in a `for`.

    ⛔ Measured before this pin existed (#9150 review cycle 11, P2): narrowing
    `_scanned_modules()` to the two modules that hold today's sites —
    `("tortoise/sdk.py", "tortoise/hosted_api.py")`, a plausible cleanup — left
    17/17 green, and with that narrowing a sixth site planted in
    `tortoise/selfhost.py` (a door the plan doc itself names) stayed invisible
    while the shipped file on the same tree red. The `rglob` is recomputed HERE,
    independently, so the narrowing reds.
    """
    scanned = _scanned_modules()
    on_disk = sorted(
        str(path.relative_to(_REPO))
        for path in (_REPO / "tortoise").rglob("*.py")
    )
    assert scanned == on_disk, (
        "the escape-(3) inventory no longer sweeps every module under `tortoise/` "
        f"— unscanned: {sorted(set(on_disk) - set(scanned))}. A sixth "
        "`eager_init_query()` site in one of those modules would leave the note "
        "in `onboarding/state.py` stale with every pin green (#9157)"
    )
    # …and the width must actually be WIDER than the modules that hold today's
    # sites, or the sweep is a formality: `selfhost.py` is the door the plan doc
    # names, so it stands for the class of files a hardcoded pair would drop.
    site_modules = {rel for rel, _func in _ESCAPE3_CALL_SITES}
    assert "tortoise/selfhost.py" in scanned, (
        "`tortoise/selfhost.py` — named as a door by the plan doc — is not "
        f"scanned for eager-init sites: {scanned[:3]}…"
    )
    assert len(scanned) > len(site_modules) * 10, (
        f"the sweep covers {len(scanned)} modules for {len(site_modules)} "
        "site modules — if that gap closes, this pin is no longer the width "
        "check it claims to be"
    )


def _eager_init_sites() -> dict[tuple[str, str], int]:
    """(file, enclosing function) -> count, read off the production source."""
    sites: dict[tuple[str, str], int] = {}
    # EVERY module, not the two that hold today's sites (#9150 review P2): the
    # sentence this pins is unqualified ("five call sites"), so a sixth caller in
    # any other module must be visible. The defining module builds the STRING and
    # issues nothing, so it is skipped by the enclosing-function shape, not by a
    # file allow-list.
    for rel in _scanned_modules():
        tree, parents = _parse(rel)
        for node, _call, _targets in _eager_init_calls(tree):
            enclosing = _enclosing_function(node, parents)
            assert enclosing is not None, (
                f"{rel}:{node.lineno} builds an eager-init statement outside a "
                "function — this inventory would silently skip it"
            )
            key = (rel, enclosing.name)
            sites[key] = sites.get(key, 0) + 1
    return sites


def _declaring_functions(tree: ast.Module,
                         parents: dict[ast.AST, ast.AST]) -> set[str]:
    """Enclosing functions of a `with` that opens a §17.1 declaration.

    ⚠️ Deliberately FUNCTION-granular, not statement-granular: a `with` anywhere
    inside an escape-(3) function is worth a human look, because that function
    IS the site. The cost is a FALSE RED when an unrelated `with` is added
    nearby — accepted, and the failure message names both causes so a lane is
    never told it closed #9157 when it did not. Statement-level resolution
    (walk the `With` body for the issued statement name, as `_statement_sinks`
    does) is the fix if that turns out to hurt more than it helps.
    """
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            continue
        if not _named([item.context_expr for item in node.items],
                      _DECLARATION_PRIMITIVES):
            continue
        enclosing = _enclosing_function(node, parents)
        if enclosing is not None:
            out.add(enclosing.name)
    return out


# ── a premise, not a measurement ──────────────────────────────────────────
def test_the_eager_init_statement_is_a_mutating_one():
    """The residual matters only if the escape issues a WRITE.

    No graph, no fixture: this is the shared classifier the guard itself uses
    (`FalkorProjection.query` → `_statement_writes`), so the claim "escape (3)
    carries an undeclared mutating statement" is the guard's own reading, not
    this file's opinion.

    What REDS here is the classifier ceasing to read this statement as a write
    AT ALL — and that needs ALL THREE arms the fragment matches to go
    (`CREATE`, `MERGE`, `SET`); any ONE of them deleted leaves this GREEN
    (measured per arm: dropping `MERGE`, or dropping `CREATE`+`SET` while
    keeping `MERGE`, both still classify the statement as a write). What this
    pin buys is the PREMISE the residual rests on, not a check of the
    classifier's individual arms — those belong to its own suite. An earlier
    revision of this docstring claimed the `MERGE` arm alone, and the revision
    after that claimed `CREATE`/`SET`; both were the same
    wrong-red-trigger-attribution defect the module docstring's failure map had
    (#9150 review cycles 2 and 4).

    The `:OnboardingState` label is the other half, and had no pin: all FOUR
    records say these five sites CREATE an `:OnboardingState`
    (`onboarding/state.py`, `projection/__init__.py`, the increment-4 plan doc
    and §17.1's door ledger — see
    `test_the_plan_doc_and_the_storage_ledger_carry_the_residual`), so a fragment
    that stopped naming that node would leave every record describing a write to
    something no longer written. (This docstring said "three" for one commit
    after the population grew to four; a count in prose is a claim like any
    other — #9150 review cycle 3.)
    """
    cypher, _params = _os.eager_init_query(
        "CREATE (:TeamMeta {name:$name})",
        {"name": "escape3"},
        org_id="org9121-residual",
        fork=_os.FORK_SELF,
    )
    assert ":OnboardingState" in cypher, (
        "`eager_init_query()` no longer creates an `:OnboardingState` — the "
        "node all four records say the five escape-(3) sites CREATE. Re-derive "
        f"the residual before letting those records stand:\n{cypher}"
    )
    assert _statement_writes(cypher) is True, (
        "`eager_init_query()` no longer builds a statement the §17.1 classifier "
        "calls mutating — the residual this file pins would no longer exist as "
        f"described:\n{cypher}"
    )


# ── the note in the declaration says what it does NOT cover ───────────────
def test_the_declaration_note_names_the_seam_it_does_not_cover():
    """`state.py`'s declaration must state the escape, where a reader looks.

    The universal that five call sites disprove ("every onboarding write is
    declared") is the thing this pins against. The NOTE has to (a) state the
    exemption ("It does NOT cover the eager-init statement … on the org-graph
    lane") — the sentence that stops the universal — (b) name the mechanism
    (`eager_init_query` on `select_graph`), (c) name the residual issue
    (`#9157`) and the closing warning, so the gap is filed rather than implied
    closed.

    ⛔ SCOPED TO THE NOTE, not to the module (#9150 review cycle 3, P1): the
    whole-file scan this replaces passed while every mechanism word had been
    DELETED from the note, because `eager_init_query` also occurs at the module
    docstring, on `def eager_init_query` itself and in the #4641 width bullet,
    and `select_graph` at the module docstring and in the same bullet — all
    ABOVE the note. A pin that cannot fail on the edit it guards is the
    #1349/#2938 false-PASS class, one level up from where this file closed it.
    """
    note = _record(
        path="tortoise/onboarding/state.py",
        start="It does NOT cover the eager-init statement",
        # the SEAM block is the next thing a reader is told about this lane
        end="SEAM: `#9123`",
    )
    for phrase in ("eager_init_query", "select_graph", "#9157",
                   "do not close escape (3) believing"):
        assert phrase in note, (
            f"the escape-(3) NOTE in `tortoise/onboarding/state.py` no longer "
            f"contains {phrase!r} — the note has been removed or reworded, "
            "which restores the over-claim that five `eager_init_query()` call "
            "sites disprove. The assertion is scoped to the note: these words "
            "occur elsewhere in this module, and a whole-file scan passed "
            "while the note said none of it (#9157)"
        )
    _assert_site_count(note, "{n} call sites build that statement",
                       "the escape-(3) NOTE in `tortoise/onboarding/state.py`")
    # ⛔ And the PER-SITE numeral (#9150 review cycle 16, P2): the note says
    # `register_user` issues TWO of the five, and that "two" was pinned by
    # nothing — editing it to "three" left the note contradicting the AST with
    # every pin green, which is the failure mode this file says prose numerals
    # are pinned against.
    _assert_site_count(
        note, "({n} sites: the self-service arm and the Supabase-mode arm)",
        "the escape-(3) NOTE's `register_user` sub-count",
        n=_ESCAPE3_CALL_SITES[("tortoise/hosted_api.py", "register_user")])


def test_the_note_and_the_ast_inventory_agree():
    """The note's site list must BE the code's site list — both directions.

    Derived from `ast`, not from a copy of the answer: a sixth
    `eager_init_query()` site appearing in `sdk.py` / `hosted_api.py` makes the
    note stale (RED here forces the same change to re-read the residual), and a
    site's name leaving the note is caught by the per-site presence check.

    ⛔ The per-site names are checked against the NOTE, not the module (#9150
    review cycle 4, P1): the whole-module scan this replaces passed while every
    name had been deleted from the note and re-added as a comment anywhere else
    in `state.py` — the sibling of the cycle-3 P1, one function down.
    """
    measured = _eager_init_sites()
    assert measured == _ESCAPE3_CALL_SITES, (
        "the escape-(3) inventory moved: measured "
        f"{sorted(measured.items())} vs recorded "
        f"{sorted(_ESCAPE3_CALL_SITES.items())}. A new site has to be reasoned "
        "about (is it declared? is it journalled?), and the note in "
        "`onboarding/state.py` + the guard's opener inventory in "
        "`projection/__init__.py` have to be updated in the same change (#9157)"
    )
    note = _record(
        path="tortoise/onboarding/state.py",
        start="It does NOT cover the eager-init statement",
        end="SEAM: `#9123`",
    )
    for rel, func in measured:
        assert f"{rel}::{func}" in note, (
            f"the declaration's note no longer names the escape-(3) site "
            f"{rel}::{func} — a lane doing the §17.1 door inventory would read "
            "the onboarding class as fully declared. The name must be in the "
            "NOTE, not merely somewhere in `state.py`"
        )

    # …and the OTHER direction (#9150 review cycle 14, P1): every `module::function`
    # the note names must be a real site. Without it, a fabricated or stale name in
    # the record passes with every pin green while the defect this docstring's
    # "both directions" claimed is measurable — the note-goes-stale class in the
    # one direction that was not measured.
    named = set(_SITE_NAME_IN_NOTE.findall(note))
    unbacked = named - set(measured)
    assert not unbacked, (
        f"the note names escape-(3) sites the AST does not see: {sorted(unbacked)}. "
        "A name that is not a site reads as coverage the residual does not have — "
        "either it was renamed/deleted in the code (update the note) or it was "
        "never a site (remove it) (#9157)"
    )


# ── what the sites DO, and the coupling to the honest fix (#9157) ─────────
def test_every_eager_init_site_issues_its_statement_on_the_escape_three_handle():
    """Each site issues the statement ON a `select_graph(...)`-bound handle.

    This is the assertion that gives the note its meaning: `eager_init_query`
    only PRODUCES text, so the seam is "issued below the guarded handle" only
    while every caller does that — and only while the statement is issued on
    the handle it resolves in the SAME function. The linkage is asserted, not
    its neighbourhood: an earlier revision checked only that the enclosing
    function mentioned `select_graph(...)` somewhere, which stayed green when
    the statement moved to `proj.g.query(...)` (the §17.1 violation this file
    exists to see) or to `state._run(...)` (the honest #9157 fix) while an
    unrelated handle binding remained. Both of those now RED here, so the
    residual cannot be closed in code while the docstrings still describe it.
    """
    # Driven by the SAME finder and population as the inventory (#9150 review,
    # P2): a site this pin cannot see is a site it silently certifies. The
    # population is `_scanned_modules()` — the SAME pinned helper the inventory
    # uses, not a second inline `rglob` (#9150 review cycle 12, P2): while this
    # loop spelled its own sweep, narrowing it to the two modules that hold
    # today's sites left every pin green, so the module docstring's "a site
    # MIGRATED off `select_graph` fails this pin" was true only for those two.
    for rel in _scanned_modules():
        tree, parents = _parse(rel)
        for node, _call, targets in _eager_init_calls(tree):
            enclosing = _enclosing_function(node, parents)
            assert enclosing is not None
            handles = _escape_handle_names(enclosing)
            assert handles, (
                f"{rel}:{node.lineno} ({enclosing.name}) builds an eager-init "
                "statement but nothing in the function binds a `select_graph(...)` "
                "handle by name — the escape-(3) description in "
                "`onboarding/state.py` and in the guard's inventory is now wrong "
                "(#9157)"
            )
            issued = _bound_names(targets[0]) if len(targets) == 1 else set()
            assert issued, (
                f"{rel}:{node.lineno} binds the statement to something this walk "
                "cannot read — the inventory would silently skip it"
            )
            sinks = _statement_sinks(enclosing, issued)
            assert sinks, (
                f"{rel}:{node.lineno} ({enclosing.name}) binds the eager-init "
                f"statement to {sorted(issued)} but nothing in the function "
                "consumes it — re-derive the residual rather than assuming it"
            )
            offenders = _not_on_the_escape_handle(
                enclosing, issued, handles,
                _rebound_handles(enclosing, handles))
            assert not offenders, (
                f"{rel}:{node.lineno} ({enclosing.name}) no longer issues the "
                f"eager-init statement on the escape-(3) handle: {offenders}. "
                "The residual is moving (#9157): update, in the same change, the "
                "note in `tortoise/onboarding/state.py`, the opener inventory in "
                "`tortoise/projection/__init__.py`, and this pin"
            )


def test_the_issuance_detector_reds_on_the_guarded_handle_and_on_run():
    """The instrument check for the pin above — it must RED on every closure.

    Written because the pin it guards is worthless if it cannot fire: an earlier
    revision passed on both of the wrong closures (proved in the review), because
    it never linked the statement to the handle. Every snippet below is the
    STATEMENT used correctly, changed only in where it is issued — including the
    sibling-arm shape that rebinds the SAME handle name off `select_graph`
    (#9150 review cycle 6, P2).
    """
    cases = {
        # The escape-(3) shape, as production issues it today: NOT an offender.
        "on_the_escape_handle": (
            """
            def site(proj, _init_q, _init_p):
                handle = proj.db.select_graph("g")
                handle.query(_init_q, params=_init_p)
            """,
            [],
        ),
        # `hosted_api.register_user`'s shape: TWO arms, the SAME handle name.
        # Closing one arm on `proj.g` is the §17.1 violation, and a per-FUNCTION
        # handle set certified it green (#9150 review cycle 6, P2 — measured).
        # BOTH arms come back as offenders, on purpose: the detector cannot tell
        # which binding a sink used, so it fails CLOSED on the pair — one arm
        # issuing on `select_graph` does not certify the other. (No site in this
        # tree has that shape today, so the rule costs no false red.)
        "sibling_arm_rebinding_the_same_name": (
            """
            def site(proj, supabase, _init_q, _init_p):
                if supabase:
                    org_graph = proj.db.select_graph("g")
                    org_graph.query(_init_q, params=_init_p)
                else:
                    org_graph = proj._get_proj().g
                    org_graph.query(_init_q, params=_init_p)
            """,
            [".query(_init_p, _init_q)"] * 2,
        ),
        # Closed the WRONG way: the guarded handle, i.e. the §17.1 violation.
        "on_the_guarded_handle": (
            """
            def site(proj, _init_q, _init_p):
                handle = proj.db.select_graph("g")
                proj.g.query(_init_q, params=_init_p)
            """,
            ["proj.g.query(_init_p, _init_q)"],
        ),
        # Closed the honest way (#9157) — but then the note is stale.
        "through_the_run_funnel": (
            """
            def site(proj, _init_q, _init_p):
                handle = proj.db.select_graph("g")
                _os._run(handle, _init_q, _init_p)
            """,
            ["_os._run(_init_p, _init_q)"],
        ),
    }
    for name, (source, expected) in cases.items():
        tree = ast.parse(textwrap.dedent(source))
        func = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "site"
        )
        issued = {"_init_q", "_init_p"}
        handles = _escape_handle_names(func)
        offenders = _not_on_the_escape_handle(
            func, issued, handles, _rebound_handles(func, handles))
        assert len(offenders) == len(expected), (
            f"the issuance detector is {'blind' if expected else 'crying wolf'} on "
            f"the {name!r} shape: expected {expected}, got {offenders}"
        )
        for fragment in expected:
            assert any(fragment in offender for offender in offenders), (
                f"the {name!r} shape did not produce an offender naming "
                f"{fragment!r}: {offenders}"
            )


def test_the_rebinding_rule_covers_every_binding_form():
    """The instrument check for `_bindings()` — one case per form it handles.

    ⛔ Without this, the widening was pinned by nothing (#9150 review cycle 10,
    P2): deleting every non-`Assign` arm left 16 passed, so the docstring's own
    "these shapes left the detector GREEN" could not be re-measured after the
    fix. Measured on a clean copy of the cycle-9 head: removing the `AugAssign`,
    `For`, `With`, `NamedExpr`, comprehension and `except … as` arms changed no
    test outcome.

    Each snippet is the SAME site — handle bound from `select_graph`, statement
    issued on it — with the handle name REBOUND by one form. The rule fails
    closed: the detector cannot tell which binding the sink used, so every one of
    these must come back as one offender, and the control (no rebinding) as none.
    """
    cases = {
        "AugAssign": """
            graph += val.g
            graph.query(_init_q, params=_init_p)
        """,
        "for": """
            for graph in [val.g]:
                graph.query(_init_q, params=_init_p)
        """,
        "with_as": """
            with contextlib.nullcontext(val.g) as graph:
                graph.query(_init_q, params=_init_p)
        """,
        # The ASYNC forms (#9150 review cycle 15, P2): `ast.AsyncFor` and
        # `ast.AsyncWith` are separate node types, and while only the sync cases
        # existed each async arm could be deleted with the suite green — leaving
        # the detector blind on `async for graph in …` / `async with … as graph:`,
        # which matters at this head because 3 of the 5 escape-(3) sites live in
        # `async def` bodies.
        "async_for": """
            async for graph in _aiter(val.g):
                graph.query(_init_q, params=_init_p)
        """,
        "async_with_as": """
            async with _acm(val.g) as graph:
                graph.query(_init_q, params=_init_p)
        """,
        "walrus": """
            if (graph := val.g):
                graph.query(_init_q, params=_init_p)
        """,
        "comprehension": """
            [graph.query(_init_q, params=_init_p) for graph in [val.g]]
        """,
        "except_as": """
            try:
                raise RuntimeError
            except RuntimeError as graph:
                graph.query(_init_q, params=_init_p)
        """,
        "match_capture": """
            match val:
                case graph:
                    graph.query(_init_q, params=_init_p)
        """,
        # The two other capture forms the arm handles (#9150 review cycle 12,
        # P2: without these, dropping `MatchStar`/`MatchMapping` from `_bindings`
        # left every pin green).
        "match_star": """
            match val:
                case [*graph]:
                    graph.query(_init_q, params=_init_p)
        """,
        "match_mapping": """
            match val:
                case {**graph}:
                    graph.query(_init_q, params=_init_p)
        """,
        # The handle's own declaration form, rebound: an annotated assignment.
        "annassign": """
            graph: object = val.g
            graph.query(_init_q, params=_init_p)
        """,
    }
    for form, tail in cases.items():
        # `async for` / `async with` only parse inside a coroutine, and their node
        # types differ, so the case name decides the `def` keyword.
        prefix = "async def" if form.startswith("async_") else "def"
        source = (
            f"{prefix} site(proj, val, _init_q, _init_p):\n"
            "    graph = proj.db.select_graph('g')\n"
            + textwrap.indent(textwrap.dedent(tail), "    ")
        )
        tree = ast.parse(source)
        func = next(
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "site"
        )
        handles = _escape_handle_names(func)
        offenders = _not_on_the_escape_handle(
            func, {"_init_q", "_init_p"}, handles,
            _rebound_handles(func, handles))
        assert len(offenders) == 1, (
            f"the rebinding rule is blind to the {form!r} binding form: the sink "
            f"on a rebound escape handle was certified clean ({offenders}). A "
            "binding form `_bindings()` does not know is a false PASS on exactly "
            "the §17.1 closure this file exists to see (#9150)"
        )

    # The control: the SAME site with no second binding stays clean, so the cases
    # above are measuring the rebinding and not merely "is there a sink".
    clean = ast.parse(
        "def site(proj, _init_q, _init_p):\n"
        "    graph = proj.db.select_graph('g')\n"
        "    graph.query(_init_q, params=_init_p)\n"
    )
    func = next(node for node in ast.walk(clean)
                if isinstance(node, ast.FunctionDef))
    handles = _escape_handle_names(func)
    assert _rebound_handles(func, handles) == set(), (
        "the rebinding rule now cries wolf on a site that binds the handle once: "
        f"handles {sorted(handles)}"
    )


def test_the_escape3_sites_are_the_mint_sites_that_use_eager_init():
    """The two inventories of this seam must be two VIEWS, not two copies.

    `test_write_ahead_mint.py::_MINT_SITES` is the existing owner of "every
    product function that mints a `:TeamMeta` graph". This file owns a slice of
    it — the sites that mint through `eager_init_query()`. Asserting the
    relationship (rather than eyeballing two hand-written sets) means a site
    added to one inventory only cannot pass unnoticed here, and the ONE
    legitimate difference (`_cmd_key_create`, which mints through another shape)
    is named instead of being an unexplained remainder.
    """
    mint_sites = _mint_sites_from_its_owner()
    assert set(_ESCAPE3_CALL_SITES) <= mint_sites, (
        "this file lists an escape-(3) site that `tests/test_write_ahead_mint.py` "
        f"does not know mints a graph: {sorted(set(_ESCAPE3_CALL_SITES) - mint_sites)} "
        "— one inventory moved without the other"
    )
    assert mint_sites - set(_ESCAPE3_CALL_SITES) == _NON_EAGER_MINT_SITES, (
        "the mint sites this file does not cover changed: "
        f"{sorted(mint_sites - set(_ESCAPE3_CALL_SITES))} vs the recorded "
        f"{sorted(_NON_EAGER_MINT_SITES)}. A mint site that migrated ONTO "
        "`eager_init_query` (or a new one that mints through it) belongs in "
        "_ESCAPE3_CALL_SITES and in the note in `onboarding/state.py` (#9157); "
        "one that migrated OFF it leaves the residual"
    )


def test_no_escape3_function_opens_a_declaration_anywhere_yet():
    """THE COUPLING: today no escape-(3) function opens a §17.1 declaration.

    WHAT THIS MEASURES IS COARSER THAN ITS NAME USED TO SUGGEST, on purpose: it
    reds on a declaration-scope `with` ANYWHERE inside an escape-(3) function,
    not only around one of the five `eager_init_query()` sites. Those functions
    ARE the sites, so a scope appearing in one is worth a human look either way
    — but the two possible causes are different, and the failure message names
    both rather than assuming the interesting one.

    ⛔ IT SCANS THE INVENTORY, NOT A HARDCODED PAIR (#9150 review cycle 10, P1).
    While this walked `("tortoise/sdk.py", "tortoise/hosted_api.py")`, a sixth
    site landing in any THIRD module — `tortoise/selfhost.py` is named as a door
    by the plan doc itself — could be wrapped in a declaration scope and
    recorded properly in `_ESCAPE3_CALL_SITES` while every pin stayed green,
    which falsifies the `onboarding/state.py` sentence "fails the day one of
    those sites enters a declaration" for every site outside those two files.
    The population is now DERIVED from the inventory this pin is coupled to, so
    a sixth site joins it by being recorded (measured on that mutant: 16 passed
    before, 1 failed after).

    It still turns RED the day `#9157` lands: putting a `with` over any scope
    opener (`_DECLARATION_PRIMITIVES`) into one of those functions around one of
    the five sites is precisely when the note in `onboarding/state.py`, the
    opener inventory in `projection/__init__.py` and this file must change
    together.

    ⚠️ What this does NOT see: the honest fix may instead route the statement
    through `onboarding/state.py::_run`, which opens the scope in ANOTHER file.
    No `with` appears here in that shape, so this pin stays green — and
    `test_every_eager_init_site_issues_its_statement_on_the_escape_three_handle`
    is the pin that reds for it (the statement stops being issued on the
    `select_graph` handle). Both are needed; neither alone is the coupling.
    """
    declared: dict[str, set[str]] = {}
    for rel in sorted({rel for rel, _func in _ESCAPE3_CALL_SITES}):
        tree, parents = _parse(rel)
        found = _declaring_functions(tree, parents)
        if found:
            declared[rel] = found
    escaped = {
        rel: sorted(names & {func for r, func in _ESCAPE3_CALL_SITES if r == rel})
        for rel, names in declared.items()
    }
    escaped = {rel: names for rel, names in escaped.items() if names}
    assert not escaped, (
        "a §17.1 declaration scope now appears INSIDE an escape-(3) function: "
        f"{escaped}. TWO possible causes, check which before acting. "
        "(a) #9157 is being closed — then update, in the same change: the note "
        "in `tortoise/onboarding/state.py`, the opener inventory in "
        "`tortoise/projection/__init__.py`, and this pin, otherwise the "
        "docstrings keep describing a gap that no longer exists. "
        "(b) an UNRELATED `with` was added in that function — this pin is "
        "function-granular on purpose and cannot tell the two apart; move the "
        "scope out of the escape-(3) function, or tighten this pin to "
        "statement level."
    )


def test_the_opener_inventory_is_pinned_so_a_new_lane_moves_this_file():
    """A NEW opener in `projection/__init__.py` must red this suite.

    The instrument check below iterates the DERIVED set, so on its own it accepts
    any number of openers — measured: adding `_future_lane_write_scope()` to
    `projection/__init__.py` left 12/12 green (#9150 review, P2). Derived, then
    compared: `_declaration_primitives()` reads the production source, so the
    expected set below is the only hand-written part.

    ⛔ WHAT THIS PIN DOES *NOT* COVER, and the earlier revision of this docstring
    claimed it did (#9150 review cycle 2, P2): its population is ONE module
    (`_projection_openers()` → `_openers_under(projection/__init__.py)`), so a new
    opener defined ANYWHERE ELSE does not move `expected` and this stays green.
    That is not hypothetical — both existing precedents live outside this file:
    `#9195`'s `_structural_edge_write_scope` is `tortoise/projection/edges.py:817`
    and `#9079`'s `_onboarding_write_scope` is `tortoise/onboarding/state.py:481`,
    and `onboarding/state.py` is the likeliest home for the `#9157` fix. Those
    are caught by `test_the_detector_population_is_pinned_by_content` (whose
    population is derived over all of `tortoise/`) and by
    `test_no_escape3_function_opens_a_declaration_anywhere_yet` (a site that starts declaring
    itself) — not here. This pin's job is the BULLET LIST a reader is told is
    exhaustive, so it moves when the list gains an entry defined in the module
    that list lives in.
    """
    expected = {
        "_live_write_scope",
        "_replay_write_scope",
        "_graph_metadata_write_scope",
        # #9210 added the PRE-WIPE RESTORE lane to the same bullet list (the
        # restore path writes records that did not come from the journal, so it
        # is neither a live door nor a replay); #9218 landed it and moved this
        # inventory, which is the pin doing its job.
        "_prewipe_restore_write_scope",
    }
    derived = _projection_openers()
    assert derived == expected, (
        "the §17.1 opener inventory in `tortoise/projection/__init__.py` changed: "
        f"derived {sorted(derived)} vs recorded {sorted(expected)}. A new opener "
        "can authorize a cache write, so re-read the bullet list in that file and "
        "the escape-(3) note in `tortoise/onboarding/state.py` (and this list) "
        "before letting the claim stand (#9157)"
    )


def _module_level_defs(tree: ast.Module) -> dict[str, list[int]]:
    """`name -> [lineno, …]` for the module-level `def`s of `tree`.

    Module level only, deliberately: a duplicated name NESTED inside a class or a
    function cannot shadow an opener, because an opener is read off `tree.body`.
    """
    found: dict[str, list[int]] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.setdefault(node.name, []).append(node.lineno)
    return found


def _duplicated_module_level_names(
    tree: ast.Module, names: set[str]
) -> dict[str, list[int]]:
    """Which of `names` are defined MORE THAN ONCE at module level.

    The detector behind
    `test_no_opener_name_is_defined_twice_in_the_inventory_module`, hoisted so it
    has an instrument check of its own (#9150 review cycle 13, P2: with the
    computation inline, replacing it with `duplicated = {}` left the suite green,
    so the only detector in the file with no proof-of-firing was the one
    justifying name-keyed inventory lookups).
    """
    return {
        name: lines
        for name, lines in _module_level_defs(tree).items()
        if name in names and len(lines) > 1
    }


def test_the_opener_inventory_is_derived_not_hand_listed():
    """The inventory's derivation must SEE a new opener — proof of firing.

    ⛔ `_projection_openers()`'s body was unpinned (#9150 review cycle 15, P2):
    replacing it with a literal of today's four names left 21/21 green, after
    which `test_the_opener_inventory_is_pinned_so_a_new_lane_moves_this_file` —
    "derived, then compared" — could no longer fire on the shape #9123 and #9195
    both used. Measured: with the literal in place AND a real
    `_future_lane_write_scope` opener added above `_live_write_scope`, only the
    DETECTOR pin red; the inventory pin passed.

    Synthetic module, injected through `_projection_openers(tree=…)`, because the
    real one is read from disk.
    """
    module = ast.parse(textwrap.dedent(
        """
        def _live_write_scope(reason: str = "live"):
            return _cache_write_authorized(reason)

        def _future_lane_write_scope(reason: str = "future"):
            return _cache_write_authorized(reason)

        def _not_an_opener(proj, cypher):
            with _live_write_scope(cypher):
                return proj.db.select_graph("g").query(cypher)
        """
    ))
    derived = _projection_openers(module)
    assert derived == {"_live_write_scope", "_future_lane_write_scope"}, (
        "the opener inventory derivation does not pick up a NEW opener in the "
        "inventory module — if this body is a literal of today's four names, the "
        "inventory pin can never fire on the next lane that declares there, "
        f"which is the whole point of pinning it: {sorted(derived)}"
    )
    assert _projection_openers(None) == _projection_openers(_parse(
        "tortoise/projection/__init__.py")[0]), (
        "the injected-module path and the on-disk `_parse` path disagree, so "
        "this instrument check is not measuring the production derivation"
    )


def test_no_opener_name_is_defined_twice_in_the_inventory_module():
    """Why NAMES suffice for the inventory: no opener name is defined twice.

    ⛔ `_projection_openers()` used to justify keying on names with "this is ONE
    module, where a duplicated function name is not a thing" — which the module
    contradicts (#9150 review cycle 11, P2): it defines ten duplicated names,
    including same-scope pairs (`_replay_seq`, `_replay_mint_seq`, `query` at two
    lines, `wrapper`, `__init__`). The real reason names suffice is narrower and
    checkable: every OPENER is a module-level `def` and no opener
    NAME appears twice at module level. Without this pin a second function
    sharing an opener's name would be invisible to the inventory — a set of names
    has no second slot — which is the cycle-5 defect one scope narrower.

    Instrument check first (#9150 review cycle 13, P2): the detector must RED on
    a synthetic module that defines one opener name twice, or this pin could pass
    with its body emptied.
    """
    synthetic = ast.parse(textwrap.dedent(
        """
        def _live_write_scope(reason):
            return _cache_write_authorized(reason)

        def _live_write_scope(reason):
            return _cache_write_authorized(reason)

        def _unique_scope(reason):
            return _cache_write_authorized(reason)
        """
    ))
    assert _duplicated_module_level_names(
        synthetic, {"_live_write_scope", "_unique_scope"}
    ) == {"_live_write_scope": [2, 5]}, (
        "the duplicate-opener detector does not red on a module that defines one "
        "opener name twice — with it blind, name-keyed inventory lookups would "
        "silently miss the second definition"
    )

    rel = "tortoise/projection/__init__.py"
    tree, _parents = _parse(rel)
    module_level = _module_level_defs(tree)

    openers = _projection_openers()
    assert openers <= set(module_level), (
        "an opener in this inventory is not a module-level `def` — names are "
        f"then not enough to identify it: {sorted(openers - set(module_level))}"
    )
    duplicated = _duplicated_module_level_names(tree, openers)
    assert not duplicated, (
        f"an OPENER name is defined twice at module level: {duplicated}. The "
        "second definition is invisible to `_projection_openers()` (a set of "
        "names), so a lane declared there would leave the bullet list in this "
        "module and the note in `onboarding/state.py` claiming an inventory it "
        "no longer has (#9157). Key the inventory on identity before allowing "
        "this — the detector population already does."
    )
    # …and the module really does contain duplicated names IN SOME SCOPE, so this
    # pin measures a property of the OPENERS rather than of the file: if that ever
    # stops being true, the docstring above is stale rather than the rule. (All
    # ten measured duplicates are nested — `_replay_seq` twice inside one class —
    # which is exactly why "module level" has to be the qualifier.)
    every_scope: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            every_scope[node.name] = every_scope.get(node.name, 0) + 1
    assert any(count > 1 for count in every_scope.values()), (
        "this module no longer has ANY duplicated function name, so the "
        "docstring's distinction (duplicates exist, but not of openers) is stale"
    )


def test_the_detector_population_is_pinned_by_content():
    """The DETECTOR population needs a content anchor of its own.

    The instrument check iterates `_DECLARATION_PRIMITIVES`, which is DERIVED, so
    it shrinks with the derivation — circular (#9150 review, P2). Measured:
    narrowing `_declaration_primitives()` back to
    `projection/__init__.py` (a plausible cleanup — "only projection defines
    openers") and wrapping a site in
    `tortoise/projection/edges.py::_structural_edge_write_scope` left 13/13
    green, restoring exactly the hole round 3 closed. The members defined OUTSIDE
    `projection/__init__.py` are the point of the wide population, so they are
    named here.
    """
    expected = {
        ("tortoise/projection/__init__.py", "_graph_metadata_write_scope"),
        ("tortoise/projection/__init__.py", "_live_write_scope"),
        ("tortoise/onboarding/state.py", "_onboarding_write_scope"),
        ("tortoise/projection/__init__.py", "_replay_write_scope"),
        ("tortoise/projection/__init__.py", "_prewipe_restore_write_scope"),
        # those defined OUTSIDE `projection/__init__.py`:
        # `tortoise/webhook_delivery.py::_run` is a DOOR, not an opener — it opens
        # the scope in a `with` and closes it before returning the query result,
        # so nothing can be `with`-ed over it and it is deliberately ABSENT from
        # `_projection_openers()`. It is here because this population asks the
        # DETECTOR question (can a declaration be in flight?) and
        # `_declaring_functions()` matches `with <name>(...)` — a statement
        # issued through this door is as journal-eligible as through an opener.
        # Its name is REUSED all over the tree (including `onboarding/state.py`'s
        # own `_run` funnel, which delegates through `_onboarding_write_scope`,
        # one hop away), so it is recorded by IDENTITY, not by name: while this
        # set was name-keyed, a second declaring `_run` in any module added no key
        # and left this pin green (#9150 review cycle 5, P1 — measured with a
        # declaring `_run` appended to `tortoise/reader.py`, an `sdk`-only
        # module).
        ("tortoise/webhook_delivery.py", "_run"),
        ("tortoise/projection/edges.py", "_structural_edge_write_scope"),
        # #9210/#9218 added the watermark re-establish path, which declares its
        # own cache write from `tortoise/event_store.py` — outside projection
        # entirely, and therefore exactly the class this wide population exists
        # to see. A DOOR like `_run`, for the same reason.
        ("tortoise/event_store.py", "reestablish_watermark"),
    }
    derived = _declaration_primitives()
    assert derived == expected, (
        "the detector population changed: derived "
        f"{sorted(derived)} vs recorded {sorted(expected)}. This population is "
        "what makes `test_no_escape3_function_opens_a_declaration_anywhere_yet` "
        "able to see a "
        "site declared through ANY opener — narrowing it re-opens the hole "
        "(#9157)"
    )


def test_the_declaration_detector_can_fail():
    """The instrument check: `_declaring_functions` must SEE every declaration.

    Without this, `test_no_escape3_function_opens_a_declaration_anywhere_yet` would also pass
    if the detector were broken (a renamed helper, a `with` form it stopped
    matching) — an instrument that cannot fail is not evidence. It is run over
    EACH recorded opener: an earlier revision exercised only
    `_onboarding_write_scope`, so closing the residual through
    `_graph_metadata_write_scope(...)` left every pin green.
    """
    for primitive in _DECLARATION_PRIMITIVES:
        snippet = textwrap.dedent(
            f"""
            def org_create(proj, cypher):
                with {primitive}(cypher):
                    return proj.db.select_graph("g").query(cypher)
            """
        )
        tree = ast.parse(snippet)
        parents = {
            child: node
            for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)
        }
        assert _declaring_functions(tree, parents) == {"org_create"}, (
            f"the declaration detector no longer recognises a `with` over "
            f"{primitive}() — a site closed through that opener would leave "
            "every residual pin in this file green while the note went stale"
        )

    # ⛔ The ATTRIBUTE spelling (#9150 review cycle 13, P2). `_named()` matches
    # `ast.Name` and `ast.Attribute`, and `_DECLARATION_PRIMITIVES` carries no
    # origin module — so `with _proj._live_write_scope(...)` must count too.
    # While that arm was unexercised it could be deleted with the suite green,
    # after which planting exactly that `with` in `sdk.py::org_create` passed
    # every pin: a silent false PASS on the coupling the note promises.
    for primitive in _DECLARATION_PRIMITIVES:
        tree = ast.parse(textwrap.dedent(
            f"""
            def org_create(proj, cypher):
                with _proj.{primitive}(cypher):
                    return proj.db.select_graph("g").query(cypher)
            """
        ))
        parents = {
            child: node
            for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)
        }
        assert _declaring_functions(tree, parents) == {"org_create"}, (
            "the declaration detector no longer recognises an ATTRIBUTE-spelled "
            f"`with` (`_proj.{primitive}(...)`) — a lane that qualifies the "
            "opener through its module would enter a declaration with every "
            "residual pin green"
        )

    # ⛔ And its PRECISION (#9150 review cycle 14, P2): the `_named` filter is the
    # only thing separating "a `with` that opens a §17.1 declaration" from "any
    # `with`". Deleting it left the suite green, after which the coupling pin reds
    # on ANY `with` inside an escape-(3) function while its failure message still
    # promises it can tell "#9157 is being closed" from "an UNRELATED `with` was
    # added" — a false red with a misleading diagnosis.
    for unrelated in ('with open("/dev/null") as _fh:',
                      'with _not_a_scope(cypher):',
                      'with _proj._not_a_scope(cypher):'):
        tree = ast.parse(textwrap.dedent(
            f"""
            def org_create(proj, cypher):
                {unrelated}
                    return proj.db.select_graph("g").query(cypher)
            """
        ))
        parents = {
            child: node
            for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)
        }
        assert _declaring_functions(tree, parents) == set(), (
            f"the declaration detector reports `{unrelated}` as a §17.1 "
            "declaration — its `_named` filter is the only thing that makes it "
            "more precise than 'any `with`', and without it the coupling pin "
            "cries wolf with a message that blames the residual (#9157)"
        )

    # ⛔ And the ASYNC spelling (#9150 review cycle 15, P2): `ast.AsyncWith` is a
    # separate node type, and while only a sync `with` was fed here the async arm
    # could be deleted green — after which `async with <opener>(...)` in an
    # escape-(3) function was invisible to the coupling pin. That is the shape the
    # likely #9157 fix takes: 3 of the 5 sites are `async def` bodies.
    for primitive in _DECLARATION_PRIMITIVES:
        tree = ast.parse(textwrap.dedent(
            f"""
            async def org_create(proj, cypher):
                async with {primitive}(cypher):
                    return proj.db.select_graph("g").query(cypher)
            """
        ))
        parents = {
            child: node
            for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)
        }
        assert _declaring_functions(tree, parents) == {"org_create"}, (
            "the declaration detector no longer recognises an ASYNC `with` "
            f"(`async with {primitive}(...)`) — a residual closed by an async "
            "scope would leave every pin green while the note still said the "
            "sites were undeclared (#9157)"
        )


def test_the_eager_init_finder_sees_both_call_spellings():
    """The instrument check for `_eager_init_calls()` — both spellings, counted.

    ⛔ The bare-name arm was unpinned (#9150 review cycle 13, P2): deleting
    `func.id == "eager_init_query"` left 19/19 green, and with it a sixth site
    spelled `from … import eager_init_query; _q, _p = eager_init_query(...)`
    became invisible to the inventory, the linkage pin AND the CI-subject sweep —
    the inventory would report four functions while five sites existed, which is
    the "note goes stale green" failure this file exists to prevent.

    ⛔ The PARENT-AGNOSTIC property too (#9150 review cycle 14, P2): the docstring
    of `_eager_init_calls()` says an ARGUMENT-position site
    (`graph.query(*eager_init_query(...))`) and a bare `Expr` site are found
    because the finder walks calls rather than parents — and re-introducing the
    `Assign`-parent filter that bug was, left every pin green while the promise
    went stale. Both non-assignment shapes are exercised below.
    """
    tree = ast.parse(textwrap.dedent(
        """
        from tortoise.onboarding.state import eager_init_query

        def via_module(proj, org):
            handle = proj.db.select_graph("g")
            q, p = _os.eager_init_query("CREATE (:TeamMeta)", {"n": org})
            handle.query(q, params=p)

        def via_bare_name(proj, org):
            handle = proj.db.select_graph("g")
            q, p = eager_init_query("CREATE (:TeamMeta)", {"n": org})
            handle.query(q, params=p)

        def argument_position(proj, org):
            handle = proj.db.select_graph("g")
            handle.query(*_os.eager_init_query("CREATE (:TeamMeta)", {"n": org}))

        def bare_expression(proj, org):
            handle = proj.db.select_graph("g")
            _os.eager_init_query("CREATE (:TeamMeta)", {"n": org})

        def inside_a_return(proj, org):
            handle = proj.db.select_graph("g")
            return _os.eager_init_query("CREATE (:TeamMeta)", {"n": org})

        def annotated(proj, org):
            handle = proj.db.select_graph("g")
            q: tuple = _os.eager_init_query("CREATE (:TeamMeta)", {"n": org})
            handle.query(q[0], params=q[1])
        """
    ))
    found = {
        enclosing.name
        for node, _call, _targets in _eager_init_calls(tree)
        for enclosing in [_enclosing_function(
            node, {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)})]
        if enclosing is not None
    }
    assert found == {"via_module", "via_bare_name", "argument_position",
                     "bare_expression", "inside_a_return", "annotated"}, (
        "`_eager_init_calls()` missed a call spelling or SHAPE — it must match "
        "BOTH `_os.eager_init_query(...)` and a bare `eager_init_query(...)`, "
        "in an assignment, an annotation, an argument, a bare expression and a "
        f"return, or a sixth site can exist with the note claiming five: {sorted(found)}"
    )

    # ⛔ …and the TARGETS each site carries (#9150 review cycle 16, P2): the
    # `AnnAssign` arm of `_binding_parent()` (and of the target extraction) was
    # deletable with the suite green, although `_eager_init_calls()`' own
    # docstring enrols "an `AnnAssign` site" and `_binding_parent()`'s names the
    # shape. A site with no targets is one the linkage pin cannot inspect.
    annotated = next(
        (node, targets)
        for node, _call, targets in _eager_init_calls(tree)
        if isinstance(node, ast.AnnAssign)
    )
    assert annotated[1], (
        "an ANNOTATED-assignment site came back with NO binding target — the "
        "linkage pin cannot inspect a site whose target list is empty"
    )
    assert _bound_names(annotated[1][0]) == {"q"}, (
        "an ANNOTATED-assignment site came back with no (or the wrong) binding "
        f"target — the linkage pin cannot inspect it: {annotated[1]}"
    )


def test_the_record_slice_and_the_count_pin_can_fail():
    """The two prose-pin instruments must RED when their premise is gone.

    ⛔ Both were load-bearing and unpinned (#9150 review cycle 14, P2): with
    `_record`'s boundary assertion neutralised, a DELETED end marker widened the
    region to the rest of the module and all eight region pins went vacuous while
    the suite stayed green; with `_assert_site_count`'s `raise` reached only by a
    stale record, nothing in the suite ever presented a contradictory numeral, so
    emptying it changed no outcome. Each detector gets a synthetic premise it must
    refuse.
    """
    # 1. a boundary marker that does not exist must raise, never widen the scan.
    try:
        _record(path="tortoise/projection/__init__.py",
                start="#   * `onboarding.metadata`",
                end="#   * marker-that-does-not-exist")
    except AssertionError as exc:
        assert "no longer carries the region" in str(exc), exc
    else:
        raise AssertionError(
            "`_record()` accepted a MISSING end marker — the scan then widens to "
            "the rest of the module and every prose pin reading that region "
            "becomes vacuous while staying green"
        )

    # 2. a contradictory numeral must raise.
    template = "the {n} `eager_init_query()` call sites"
    try:
        _assert_site_count("the four `eager_init_query()` call sites", template,
                           "synthetic record")
    except AssertionError as exc:
        assert "no longer says" in str(exc), exc
    else:
        raise AssertionError(
            "`_assert_site_count()` accepted a record that states the WRONG "
            "numeral — the one guard on the word \"five\" in all four records "
            "would then be decorative"
        )

    # 3. …and the same template at the RIGHT numeral must pass, so the check is
    #    measuring the numeral and not merely "is there any number".
    word = _COUNT_WORDS[sum(_ESCAPE3_CALL_SITES.values())]
    _assert_site_count(f"the {word} `eager_init_query()` call sites", template,
                       "synthetic record")


def test_a_delegating_opener_joins_both_populations():
    """`return <opener>(reason)` must count as declaring — it authorizes as much.

    The instrument check for the fixpoint in `_openers_in()` (#9150 review cycle
    8, P1). A wrapper that delegates to a real opener is the ordinary in-module
    shape — `onboarding/state.py` spells it out as "two spellings, one
    primitive" — and while the derivation keyed on a DIRECT
    `_cache_write_authorized` call only, such an opener joined NEITHER
    population: measured, a delegating `_future_lane_write_scope` wrapping
    `sdk.py::org_create`'s statement left this suite 15/15 passed, while
    `test_the_opener_inventory_is_pinned_so_a_new_lane_moves_this_file` and
    `_declaration_primitives()` both promise a new lane cannot do that.

    Synthetic trees, because these two populations are read from the repo on
    disk — the point here is the DERIVATION, not where it is pointed.
    """
    trees = {
        "synthetic/direct.py": ast.parse(textwrap.dedent(
            """
            def _base_write_scope(reason: str = "base"):
                return _cache_write_authorized(reason)
            """
        )),
        "synthetic/delegating.py": ast.parse(textwrap.dedent(
            """
            def _future_lane_write_scope(reason: str = "future"):
                '''A one-line wrapper, as a new lane is usually added.'''
                return _base_write_scope(reason)

            def _not_an_opener(reason: str = "x"):
                with _base_write_scope(reason):
                    return None
            """
        )),
        "synthetic/door.py": ast.parse(textwrap.dedent(
            """
            def _run(proj, cypher):
                with _cache_write_authorized("door"):
                    return proj.g.query(cypher)

            def _forward_to_door(proj, cypher):
                return _run(proj, cypher)
            """
        )),
        # The ATTRIBUTE spelling, on both sides of the derivation (#9150 review
        # cycle 12, P2: each `or getattr(..., "attr", None)` branch could be
        # deleted with every pin green).
        "synthetic/attribute.py": ast.parse(textwrap.dedent(
            """
            def _qualified_scope(reason: str = "qualified"):
                return projection._cache_write_authorized(reason)

            def _qualified_forwarder(reason: str = "fwd"):
                return guard._qualified_scope(reason)
            """
        )),
        # …and MULTI-HOP delegation, which is what "TRANSITIVE" in the fixpoint
        # docstring claims: `_top` reaches the primitive through two levels.
        "synthetic/multihop.py": ast.parse(textwrap.dedent(
            """
            def _mid_scope(reason: str = "mid"):
                return _base_write_scope(reason)

            def _top_scope(reason: str = "top"):
                return _mid_scope(reason)
            """
        )),
        # The DETECTOR half of the attribute spelling (#9150 review cycle 12,
        # P2): a door that spells the primitive as an attribute is seen only by
        # `_declares_directly()`, so without this the `attr` branch of THAT
        # helper could be deleted with both populations still green.
        "synthetic/attribute_door.py": ast.parse(textwrap.dedent(
            """
            def _qualified_door(proj, cypher):
                with projection._cache_write_authorized("qualified"):
                    return proj.g.query(cypher)
            """
        )),
    }
    derived = _openers_in(trees)
    assert ("synthetic/direct.py", "_base_write_scope") in derived, derived
    assert ("synthetic/delegating.py", "_future_lane_write_scope") in derived, (
        "the opener derivation still requires a DIRECT `_cache_write_authorized` "
        f"call, so a delegating opener is invisible to both populations: {derived}"
    )
    assert ("synthetic/attribute.py", "_qualified_scope") in derived, (
        "an opener that spells the primitive as an ATTRIBUTE "
        "(`projection._cache_write_authorized(...)`) is invisible to both "
        f"populations: {derived}"
    )
    assert ("synthetic/attribute.py", "_qualified_forwarder") in derived, (
        "a forwarder that spells the opener as an ATTRIBUTE "
        f"(`guard._qualified_scope(...)`) is invisible to both populations: {derived}"
    )
    assert ("synthetic/multihop.py", "_mid_scope") in derived, derived
    assert ("synthetic/multihop.py", "_top_scope") in derived, (
        "the delegation fixpoint is not TRANSITIVE: `_top_scope` reaches "
        f"`_cache_write_authorized` through `_mid_scope`, and is missing: {derived}"
    )
    assert ("synthetic/delegating.py", "_not_an_opener") not in derived, (
        "a DOOR (a function that merely calls an opener inside a `with`) was "
        "collected as an OPENER — that is `_declaring_functions()`' question, "
        f"and conflating them makes the inventory claim a lane exists: {derived}"
    )

    # The DOOR shape, which review cycle 9 found in the population
    # (`webhook_delivery.py::_run`, `event_store.py::reestablish_watermark`).
    # An opener RETURNS the scope; a door opens it in a `with` and returns the
    # query result, so nothing can be `with`-ed over it.
    assert ("synthetic/door.py", "_run") not in derived, (
        "a DOOR was collected as an OPENER: it returns the query result, not a "
        "context manager, so `projection/__init__.py`'s bullet list — which a "
        f"reader is told is exhaustive — would claim a lane that is not one: {derived}"
    )
    assert ("synthetic/door.py", "_forward_to_door") not in derived, (
        "a one-line forwarder onto a DOOR was enrolled as an OPENER: the "
        "fixpoint propagates by NAME, so a door in the seed certifies its "
        f"forwarders as openable — they are not: {derived}"
    )

    # …and the detector population must still SEE a door and its forwarder,
    # because a statement issued through one enters a declaration exactly as one
    # issued through an opener does. This is the half that keeps the split from
    # becoming a hole. (`_not_an_opener` above is NOT here and needs not be: it
    # `with`s an OPENER, whose name is already in this population, so
    # `_declaring_functions()` matches that `with` as it stands.)
    detector = _openers_in(trees, opener_only=False)
    assert ("synthetic/attribute_door.py", "_qualified_door") in detector, (
        "a door that spells the primitive as an ATTRIBUTE is invisible to the "
        f"DETECTOR population, so a `with` over it can never be matched: {detector}"
    )
    assert ("synthetic/attribute_door.py", "_qualified_door") not in derived, (
        "a DOOR was collected as an OPENER because its primitive call is spelled "
        f"as an attribute: {derived}"
    )
    for key in (("synthetic/door.py", "_run"),
                ("synthetic/door.py", "_forward_to_door")):
        assert key in detector, (
            f"{key} is missing from the DETECTOR population — it opens a "
            "declaration, so `_declaring_functions()` must be able to match a "
            f"`with` over it: {sorted(detector)}"
        )


# ── the guard's own opener inventory ─────────────────────────────────────
def test_the_guards_inventory_names_this_lane_and_its_residual():
    """The `onboarding.metadata` BULLET in the inventory must agree with itself.

    The opener inventory is what ENFORCES the rule and what a §17.1 door
    inventory reads as exhaustive. `#9121` adds an opener that appends nothing,
    so the bullet has to say so *in its own terms* — otherwise the next reader
    trusts "only journal-first doors open this" and reintroduces exactly the P0
    this work blocks, or reads the onboarding scope as a §17.1 violation — and it
    has to name the residual (`#9157`): the escape-(3) creation writes are NOT
    this lane.

    ⛔ SCOPED TO THE BULLET, not to the file (#9150 review cycle 3, P2): the
    whole-file phrase scan this replaces passed while the bullet's residual
    PARAGRAPH had been deleted, because `#9157` also occurs in
    `_live_write_scope`'s docstring 300 lines below (measured).
    `_onboarding_write_scope` is NOT this pin's business — it never appears in
    the inventory at all; that docstring is owned by
    `test_the_live_scope_docstring_names_the_opener_the_lane_actually_calls`,
    so asserting it here only ever tested a different text than this docstring
    claims.
    """
    bullet = _record(
        path="tortoise/projection/__init__.py",
        start="#   * `onboarding.metadata`",
        end="#   * `structural-edge.derived`",
    )
    for phrase in (
        # the opener is NOT a `_live_write_scope` opener: the bullet says the
        # LANE appends nothing, and why that is not a contradiction.
        "THIS LANE APPENDS NOTHING",
        "not every opener",
        # the class the lane covers, named where the auditor looks
        "`:OnboardingState`",
        "#9157",
    ):
        assert phrase in bullet, (
            f"the `onboarding.metadata` bullet of the §17.1 opener inventory in "
            f"`tortoise/projection/__init__.py` no longer contains {phrase!r} — "
            "the block is the file that ENFORCES the rule, so a reader who "
            "trusts it over `onboarding/state.py` would conclude the onboarding "
            "lane is journal-first (it is not) or fully delegated (it is not: "
            "#9157)"
        )
    _assert_site_count(bullet, "the {n} `eager_init_query()` call sites",
                       "the `onboarding.metadata` bullet in "
                       "`tortoise/projection/__init__.py`")


def test_the_live_scope_docstring_names_the_opener_the_lane_actually_calls():
    """The docstring must name the call `state.py` makes — and not this alias.

    `_live_write_scope`'s own docstring said the onboarding lane opens *it*, and
    one grep disproves that: `tortoise/onboarding/state.py` never imports this
    alias — `_onboarding_write_scope` returns
    `_cache_write_authorized(ONBOARDING_WRITE_LANE)`, the same ContextVar under a
    different spelling. The sentence lives in the file that ENFORCES §17.1, so a
    reader who trusts it over `state.py` learns the wrong mechanism, and the
    phrase-presence pin above cannot see it (it asserts that words exist, never
    that they are true).

    Derived, not eyeballed: the mechanism names are read out of `state.py`'s own
    opener, so re-wording the docstring around a mechanism the lane does not use
    cannot satisfy this.

    ⛔ Both halves are checked where the claim lives (#9150 review cycle 4, P1).
    The alias half used to be a raw text scan of the WHOLE of `state.py`, which
    proved nothing about the docstring (the false sentence could be re-added
    verbatim and this stayed green) and false-RED on a TRUE prose mention of the
    alias. It is now (a) a DOCSTRING assertion — the sentence the defect lived
    in — and (b) an AST assertion that the module never calls or imports the
    alias, which is what "never through this alias" actually means and which a
    comment cannot violate.
    """
    tree, _parents = _parse("tortoise/onboarding/state.py")
    opener = next(
        (node for node in ast.walk(tree)
         if isinstance(node, ast.FunctionDef)
         and node.name == "_onboarding_write_scope"),
        None,
    )
    assert opener is not None, (
        "`tortoise/onboarding/state.py` no longer defines "
        "`_onboarding_write_scope` — the lane's §17.1 declaration moved, so "
        "every claim about how it declares must be re-derived"
    )
    names: set[str] = set()
    for node in ast.walk(opener):
        if isinstance(node, ast.Call):
            names.add(getattr(node.func, "id", None)
                      or getattr(node.func, "attr", None) or "")
        elif isinstance(node, ast.Name):
            names.add(node.id)
    mechanism = {
        name for name in names
        if name == "ONBOARDING_WRITE_LANE"
        or name.startswith("_cache_write_authorized")
        or name.startswith("_onboarding_write_scope")
    }
    assert mechanism == {"_cache_write_authorized", "ONBOARDING_WRITE_LANE"}, (
        "the onboarding lane's declaration mechanism moved: the opener now "
        f"names {sorted(mechanism)} — re-derive the claim in "
        "`tortoise/projection/__init__.py` rather than assuming it (#9121)"
    )
    projection_tree, _pp = _parse("tortoise/projection/__init__.py")
    live = next(
        (node for node in ast.walk(projection_tree)
         if isinstance(node, ast.FunctionDef) and node.name == "_live_write_scope"),
        None,
    )
    assert live is not None, (
        "`tortoise/projection/__init__.py` no longer defines "
        "`_live_write_scope` — the §17.1 opener inventory moved"
    )
    doc = ast.get_docstring(live) or ""
    missed = sorted(name for name in mechanism if name not in doc)
    assert not missed, (
        "`_live_write_scope`'s docstring describes the onboarding lane without "
        f"naming the mechanism it actually uses ({missed} absent). It claimed "
        "the lane opens THIS alias, which `tortoise/onboarding/state.py` never "
        "imports; a reader trusts the docstring over the code, and this is the "
        "file that ENFORCES §17.1 (#9121)"
    )
    # The defect this pin exists for was a SENTENCE, so it is asserted on the
    # docstring: the alias must not be described as how this lane declares.
    assert "_live_write_scope" not in doc, (
        "`_live_write_scope`'s docstring names ITSELF for the onboarding lane "
        "again. `tortoise/onboarding/state.py` never imports this alias — its "
        "opener calls `_cache_write_authorized(ONBOARDING_WRITE_LANE)` "
        "directly (two spellings, one primitive) (#9121)"
    )
    # ... and the CODE half: `state.py` must not reach the alias by call, getattr
    # or import. A comment naming it is not a mechanism and must not red this.
    # The dynamic `getattr(module, "_live_write_scope")` shape is included: it is
    # an `ast.Call` whose alias name is a STRING CONSTANT, so the Name/Attribute
    # arms below cannot see it, and this message says a getattr does red the pin
    # (#9150 review cycle 5, P2 — measured: it did not).
    alias_uses = {
        node.id for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id == "_live_write_scope"
    } | {
        node.attr for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "_live_write_scope"
    } | {
        a.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        for a in node.names if a.name == "_live_write_scope"
    } | {
        arg.value for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (getattr(node.func, "id", None) == "getattr"
             or getattr(node.func, "attr", None) == "getattr")
        for arg in node.args
        if isinstance(arg, ast.Constant) and arg.value == "_live_write_scope"
    }
    assert not alias_uses, (
        "`tortoise/onboarding/state.py` now CALLS or imports the "
        "`_live_write_scope` alias — re-read the opener inventory's onboarding "
        "bullet in `tortoise/projection/__init__.py` and this pin. (A COMMENT "
        "naming the alias is fine and does not trip this; only a call, a "
        "getattr or an import does.) (#9121)"
    )


def test_a_diff_to_the_note_itself_selects_this_file():
    """The pin must RUN on the change it guards — a named surface REPLACES `core`.

    `test_the_declaration_note_names_the_seam_it_does_not_cover` and
    `test_the_note_and_the_ast_inventory_agree` guard the note in
    `tortoise/onboarding/state.py`, and `tools/ci_selection.py` resolves
    `tortoise/onboarding/` to the `onboarding` surface — a NAMED
    `SOURCE_PATTERNS` match, which REPLACES the `core` fallback. Before the
    `onboarding` arm existed, an `onboarding`-only diff — the exact drift these
    two pins exist to catch — selected every suite EXCEPT this one, so the note
    could go stale green (the #1349/#2938 silent-drop class). The population is
    every file this suite actually READS, plus the whole module set it PARSES:

    * EVERY `tortoise/**/*.py` (242 files). `_declaration_primitives()` walks
      `rglob("*.py")`, so ANY of them can take a new §17.1 opener — and the six
      arms in `config/ci-surfaces.yml` exist to make each one's diff run this
      file (with `api` + `core` + `onboarding` only, the 12 modules resolving
      solely to `ep`/`sdk`/`eval` — `dream.py` first — could take a new opener on
      a PR that never ran this file). DERIVED rather than hand-listed (#9150
      review cycle 6, P2): the literal tuple of four representative modules that
      this replaces pinned none of the other 238, and deleting the
      `api`/`sdk`/`ep`/`eval` rows red nothing at all — so the six-arm
      justification in that manifest comment was itself unpinned, in exactly the
      removal direction this pin exists for.
    * `tests/test_write_ahead_mint.py` — the sibling inventory this file asserts
      against (`_mint_sites_from_its_owner()`).
    * `config/ci-surfaces.yml` — this suite reads it through `load_manifest()`, and
      it is the one file whose edit silently disables the pin: a registration
      change resolves to `core` and nothing else, so the change class that breaks
      this file is the one that must run it.
    * the two `docs/` records that
      `test_the_plan_doc_and_the_storage_ledger_carry_the_residual` reads, whose
      ONLY wiring is the two `SOURCE_PATTERNS["onboarding"]` entries added for
      this file. ⛔ Deleting one does NOT go unnoticed (#9150 review cycle 11,
      P2 — this parenthetical claimed "nothing reds", which is false): a
      `tools/ci_selection.py` diff selects the FULL matrix, so this pin runs and
      reds on the removal, because both docs are in `subjects` below.
      `test_every_source_pattern_is_selectable` alone cannot see a removal —
      it checks entry → runs, never the other direction — which is exactly why
      the two docs are in `subjects`.
    """
    from tools.ci_selection import load_manifest, select

    manifest = load_manifest()
    this_file = Path(__file__).name
    missing: dict[str, list[str]] = {}
    subjects = [
        # every module the suite PARSES — `_scanned_modules()`, the SAME pinned
        # helper the inventory sweep uses, so this population cannot be narrowed
        # without reding `test_the_inventory_scan_sweeps_every_module` (#9150
        # review cycle 12, P2: an inline copy of the `rglob` here was
        # revertible to the literal tuple this docstring says it replaced,
        # silently un-pinning the `ep` arm's justification).
        *_scanned_modules(),
        # read by the sibling-population pin below (a suite that pins the same
        # mint path), so a diff there must run this file too (#9150 review P2).
        "tests/test_write_ahead_mint.py",
        "config/ci-surfaces.yml",
        # read by `test_the_plan_doc_and_the_storage_ledger_carry_the_residual`
        # — the two records were WITHOUT the residual and had no pin at all, so
        # a docs-only edit to either is the change that silently un-pins them.
        # Their only wiring is the two `SOURCE_PATTERNS["onboarding"]` entries
        # in `tools/ci_selection.py`; this row is what notices one being
        # dropped (#9150 review P1).
        "docs/plans/2026-10-14-7951-atomic-write-outbox.md",
        "docs/architecture/STORAGE-ARCHITECTURE.md",
    ]
    for subject in subjects:
        sel = select([subject], "pull_request", manifest)
        selected = (sel["full"] or sel["test_files"] == "ALL"
                    or this_file in set(sel["test_files"]))
        if not selected:
            missing[subject] = list(sel["surfaces"])
    assert not missing, (
        "a diff to a file this suite reads does not SELECT this file, so the "
        f"pins above cannot fail there: {missing}. Register this file on the "
        "surfaces those files resolve to in `config/ci-surfaces.yml` — a named "
        "match REPLACES the `core` fallback (#9121 review P1)"
    )


def test_the_storage_ledger_declares_every_lane_the_tree_has():
    """The §17.1 ledger's NUMERAL and its ROWS must agree — the drift that landed.

    ⛔ Measured at the head this pin was written for (#9150 review cycle 15, P2):
    `docs/architecture/STORAGE-ARCHITECTURE.md` said the carve-out "has FOUR
    named lanes" and "is FOUR declared classes", and its table had four rows —
    while `tortoise/projection/edges.py::_structural_edge_write_scope` declares a
    FIFTH (`structural-edge.derived`, `#9195`, landed 2026-10-19) that appeared
    nowhere in the record. The paragraph itself says "a new lane belongs here in
    the PR that declares it", and this file quotes that preamble as the reason an
    auditor trusts the table — so a reader auditing §17.1 for second writers was
    told the inventory was complete one lane short.

    Content-anchored on purpose: a recorded row set in the test means a lane
    added to the tree reds here (with the doc updated in the same change), which
    is the pin the numeral never had.
    """
    rel = "docs/architecture/STORAGE-ARCHITECTURE.md"
    lines = (_REPO / rel).read_text(encoding="utf-8").splitlines()
    lo = next(i for i, ln in enumerate(lines)
              if ln.startswith("**⚠️ A PERMANENT CARVE-OUT IS DECLARED"))
    hi = next(i for i, ln in enumerate(lines)
              if ln.startswith("What all ") and "share, and what none" in ln)
    preamble = " ".join(lines[lo:hi])
    rows = [
        ln for ln in lines[lo:hi]
        if ln.startswith("| ") and not set(ln) <= set("| -")
    ]
    # header + separator are not lanes
    rows = rows[1:]

    recorded = {
        "graph-metadata": "tests/test_9079_ep_epoch_declared.py",
        "onboarding metadata": "tests/test_9079_onboarding_declared_lane.py",
        "webhook delivery store": "tests/test_9233_webhook_declared_lane.py",
        "pre-wipe restore": "tests/test_9210_rebuild_declares_restore.py",
        # #9195's lane — the row this pin exists because of.
        "structural-edge.derived": "tests/test_9079_edge_lane_declared.py",
    }
    derived = {
        row.split("|")[1].strip(): row for row in rows
    }
    assert set(derived) == set(recorded), (
        "the §17.1 ledger's lane rows changed: "
        f"missing {sorted(set(recorded) - set(derived))}, "
        f"unrecorded {sorted(set(derived) - set(recorded))}. A lane is DECLARED in "
        "the tree before it is listed here (or the reverse) — either way this "
        "table is what a reader auditing §17.1 for second writers trusts, so it "
        "must list them all (#9157, #9195)"
    )

    n = len(derived)
    word = _COUNT_WORDS.get(n)
    assert word is not None, f"the ledger now has {n} lanes — extend `_COUNT_WORDS`"
    # ⛔ EVERY ordinal phrasing, not just the headline two (#9150 review cycle 16,
    # P1): after the numeral went FIVE, the same paragraph still said "The other
    # THREE lanes declare through the same primitive" — the under-count the row
    # pin cannot see, because it is prose about the OTHER lanes.
    phrases = [f"has {word.upper()} named lanes",
               f"it is {word.upper()} declared classes"]
    if n >= 2:
        phrases.append(f"the other {_COUNT_WORDS[n - 1]} lanes")
    for phrase in phrases:
        assert phrase.lower() in preamble.lower(), (
            f"the ledger preamble no longer says {phrase!r} while the table has "
            f"{n} rows — a numeral that disagrees with its own table is how this "
            "said FOUR for a five-lane carve-out, and then FIVE while still "
            "telling the reader there were three OTHER lanes (#9150)"
        )

    for lane, pin in recorded.items():
        assert (_REPO / pin).is_file(), (
            f"the ledger's {lane!r} row points at a pin that does not exist: {pin}"
        )
        assert pin in derived[lane], (
            f"the ledger's {lane!r} row no longer names its pin {pin} — a row "
            "whose pin moved is a row nothing holds to the code"
        )


def test_the_plan_doc_and_the_storage_ledger_carry_the_residual():
    """The TWO remaining records must name the residual — they had no pin.

    `tortoise/onboarding/state.py` and `tortoise/projection/__init__.py` carry
    the escape-(3) note and are pinned above. Two more records a lane reads
    before concluding the onboarding class is covered were silently WITHOUT it
    (#9150 review):

    * `docs/plans/2026-10-14-7951-atomic-write-outbox.md` — the increment-4
      tracker, whose `onboarding` entry said the lane is DECLARED and stopped
      there, i.e. exactly the reading the other records exist to prevent;
    * `docs/architecture/STORAGE-ARCHITECTURE.md` — §17.1's ledger table, whose
      own preamble (`:1516`) says it "is the ledger a reader auditing §17.1 for
      second writers reads, so a new lane belongs here", and whose
      `onboarding metadata` row claimed `:OnboardingState` outright.

    Both now state `#9157`, and this pin is why they cannot drift back.

    ⛔ SCOPED TO EACH RECORD'S OWN ENTRY (#9150 review cycle 6, P2). The
    whole-document scan this replaces was only ACCIDENTALLY equivalent: each
    phrase happened to occur exactly once in each file, so the `onboarding
    metadata` row (or the increment-4 entry) could be reverted to "class
    covered" with the disclosure moved to a footnote elsewhere, leaving every
    assertion green — the same wider-than-the-claim defect the two Python
    records were cured of, left unfixed for the two docs.
    """
    for rel, what, template, start, end in (
        ("docs/plans/2026-10-14-7951-atomic-write-outbox.md",
         "the increment-4 tracker",
         "the {n} `eager_init_query()` call sites",
         # ⛔ NUMERAL-FREE boundary (#9150 review cycle 10, P2): with
         # `**STILL OPEN: the five` the marker EMBEDDED the numeral the count
         # assertion checks, so (a) the count pin was self-satisfied by the
         # marker — it could not see a contradictory count anywhere in the
         # entry — and (b) when the inventory legitimately grew, correcting the
         # header (the honest edit) red with "the record moved or was deleted".
         # This prefix occurs once in the plan doc.
         "**STILL OPEN: the ",
         "**LANDED SO FAR: the `delete_point` DOOR"),
        ("docs/architecture/STORAGE-ARCHITECTURE.md",
         "§17.1's door ledger",
         "the {n} `eager_init_query()` sites",
         "| onboarding metadata |",
         "| webhook delivery store |"),
    ):
        flat = _record(path=rel, start=start, end=end)
        for phrase in ("#9157", "eager_init_query", "select_graph"):
            assert phrase in flat, (
                f"{what} (`{rel}`) no longer contains {phrase!r} IN ITS OWN "
                "ENTRY. An auditor reading it concludes there is no second "
                "`:OnboardingState` writer outside the declared lane, which the "
                "five escape-(3) sites disprove (#9157)"
            )
        _assert_site_count(flat, template, f"{what} (`{rel}`)")

    # ⛔ BOTH DIRECTIONS of the per-site NAME list, for the record that SPELLS the
    # names (#9150 review cycle 15, P2). `tortoise/onboarding/state.py`'s note is
    # pinned both ways; the plan doc was pinned neither way, so a fabricated or
    # stale function name read as coverage with every assertion green. The ledger
    # row instead names the CLASS and the count, so it is covered by the count pin
    # and by `test_the_storage_ledger_declares_every_lane_the_tree_has`.
    plan = _record(
        path="docs/plans/2026-10-14-7951-atomic-write-outbox.md",
        start="**STILL OPEN: the ",
        end="**LANDED SO FAR: the `delete_point` DOOR",
    )
    named = {(Path(m_rel).name, m_func)
             for m_rel, m_func in _SITE_NAME_IN_NOTE.findall(plan)}
    named |= set(_SITE_BASENAME_IN_RECORD.findall(plan))
    expected_names = {(Path(rel).name, func) for rel, func in _ESCAPE3_CALL_SITES}
    assert named == expected_names, (
        "the increment-4 tracker (`docs/plans/2026-10-14-7951-atomic-write-outbox.md`) "
        f"disagrees with the AST inventory about WHICH sites the residual holds: "
        f"names not in the code {sorted(named - expected_names)}, sites it does not "
        f"name {sorted(expected_names - named)}. A name that is not a site reads as "
        "coverage the residual does not have, and a site the record omits reads as "
        "closed — the same class the `state.py` note is pinned both ways for (#9157)"
    )
    # …and the plan doc's PER-SITE numeral (`register_user` "(two statements)"),
    # the same unpinned sub-count as the note's "TWO sites" (#9150 cycle 16, P2).
    _assert_site_count(
        plan, "({n} statements)",
        "the increment-4 tracker's `register_user` sub-count",
        n=_ESCAPE3_CALL_SITES[("tortoise/hosted_api.py", "register_user")])


def test_the_inventory_cites_the_issue_that_owns_the_journalling_increment():
    """A pointer a reader follows must land on the tracked work, not a stub.

    The inventory names the larger increment (journal the onboarding class) as
    filed separately. That citation is load-bearing — it is how a reader weighing
    the declaration against journalling finds the work — and it was wrong (#9144,
    an auto-filed `NEVER_BACKED_UP` incident stub with `{}` detail, files no
    scope). The increment is **#9129**. No pin covered this, so nothing reddened;
    this is that pin.

    ⛔ SCOPED TO THE BULLET (#9150 review cycle 4, P2). The `#9144` half used to
    be a ban on that string across the whole 30 000-line module, so a LEGITIMATE
    future citation of `#9144` anywhere in `projection/__init__.py` red the pin
    and was reported as "the onboarding-journalling increment is `#9129`" — the
    same wider-than-the-claim defect the sibling pins were cured of.
    """
    bullet = _record(
        path="tortoise/projection/__init__.py",
        start="#   * `onboarding.metadata`",
        end="#   * `structural-edge.derived`",
    )
    assert "(`#9129`), not assumed by this lane" in bullet, (
        "the `onboarding.metadata` bullet in `tortoise/projection/__init__.py` "
        "no longer cites `#9129` for the onboarding-journalling increment — "
        "either the citation was dropped or it points somewhere else (it once "
        "pointed at `#9144`, an auto-filed NEVER_BACKED_UP incident stub that "
        "files no scope)"
    )
    assert "#9144" not in bullet, (
        "the `onboarding.metadata` bullet in `tortoise/projection/__init__.py` "
        "cites `#9144`, which is a NEVER_BACKED_UP incident stub — the "
        "onboarding-journalling increment is `#9129` (#9121 residual)"
    )

"""#9583: the served OpenAPI description is a string a CUSTOMER reads.

`@app.get("/v1/packs")`'s docstring said *"#318: read-only pack introspection"*.
FastAPI serves a route docstring as the OpenAPI operation ``description``, so that
phrase shipped in ``/openapi.json`` to every generated client — while the route calls
``pack_state.get_tenant_packs``, whose self-heal MERGEs the starter ``:PackInstall``
set into a graph that holds none. An operation that can write advertised itself as a
pure read.

This is the REST half of the same defect #9575 reports on the MCP half: #9575's lint
reads ``TOOL_REGISTRY``, and ``hosted_api.py`` is outside the surface-manifest gate
entirely, so nothing covered the served schema at all. This test widens that lint from
the tool registry to **served OpenAPI operation descriptions — every method**, because
a POST/PUT/DELETE described as a pure read is the same customer-facing falsehood (the
reach derivation makes no assumption about the verb).

The predicate is a DERIVATION, not a ledger of offenders: an operation whose handler
reaches a declared read-through writer may not claim read-only. The declared set is
``tests/tool_surface_capabilities.READ_THROUGH_WRITE_METHODS`` / ``NON_SDK_READ_BINDINGS``
— the same authority #4122 uses — so there is no second ledger to fall out of date.

What "reaches" means here, exactly (the bound is stated because a gate that promises
more than it scans is how ``tests/test_7951_write_doors.py`` lost eleven review rounds):

  the handler's own CODE — its body plus its decorators, since a decorator argument IS
  part of the handler — with the docstring excluded (the claim lives there, and prose
  must not manufacture an offender). Read via ``ast``: a ``Name``, an ``Attribute``, a
  ``from … import <writer> [as alias]``, a ``getattr(x, "<writer>")``, or a call to a
  module-level helper OF THE HANDLER'S MODULE whose own body names a declared writer
  (ONE hop, taken only when the handler's own code reaches no declared writer).

Blind shapes — deliberately NOT resolved, and not covered: two-or-more helper hops, a
writer reached through a bound method object, ``importlib``/string dispatch beyond the
``getattr`` literal form, and ``functools.partial``. A new operation built out of those
shapes still ships a read-only claim silently; the non-vacuity test below covers
``/v1/packs`` specifically, not the class. Closing that would mean following call graphs
in general, which is a different (and much larger) gate than this issue asked for.

Not gated here: operations that claim read-only and reach nothing declared — they are
measured, not adjudicated, because deciding each is a pure read is a larger question
than #9583 asked. Measured across all methods on the tree this gate was added against,
the set is ``/v1/internal/abuse/state``, ``/v1/internal/activation/cohort``,
``/v1/internal/oauth/clients/count``, ``/v1/internal/retention/cohort``,
``/v1/graphs/trash/{graph_id}/points`` and ``POST /v1/context`` (listed as paths rather
than as a count: the count is the part that goes stale). The POST's claim IS backed
rather than adjudicated away: the pipeline it drives performs zero graph writes
(``tortoise/sdk.py`` ``volunteer_context``: MATCH-only reads, assembled in
``tortoise/why.py``), which is why it is named here instead of being flagged.
"""
from __future__ import annotations

import ast
import inspect
import re
import sys
import textwrap
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT / "tests") not in sys.path:  # the shared capability ledger
    sys.path.insert(0, str(_REPO_ROOT / "tests"))

# The read-only CLAIM.  Every spelling, because a description copied out of the code is
# snake_case as often as it is hyphenated (`read_only`, `READ_ONLY`), the camelCase form
# (`readOnly`) is the same claim, and the kebab/space forms are what the two surfaces
# actually shipped ("Read-only;", "read-only pack introspection").
_CLAIM = re.compile(r"(?<![A-Za-z])read[\s\-_]?only", re.IGNORECASE)

# A negation INSIDE the same clause ("so not read-only", "not a read-only surface",
# "isn't read-only", "cannot be read-only", "no longer read-only") is the OPPOSITE
# claim — an endpoint documenting that it is *not* a pure read must not be pushed into
# rewording prose the regex happens to tolerate.  The window stops at a clause
# boundary (period, semicolon, colon, COMMA): "It is not a write, READ-ONLY" is a real
# claim in its own clause, and "not an install surface. Read-only when configured"
# keeps both clauses with the second half asserting one.
_NEGATION = re.compile(
    r"\b(?:not|never|no\s+longer|cannot|can'?t|won'?t|doesn'?t|don'?t|didn'?t"
    r"|isn'?t|aren'?t|non[-\s]?)\b[^.;:,]{0,24}$",
    re.IGNORECASE,
)

_CLAUSE_LOOKBACK = 40


def _claim_in(description: str) -> str | None:
    """The offending phrase in a description, or None when it makes no claim."""
    text = description or ""
    for match in _CLAIM.finditer(text):
        window = text[max(0, match.start() - _CLAUSE_LOOKBACK):match.start()]
        if _NEGATION.search(window):
            continue
        return match.group(0)
    return None


def _declared_read_through_writers() -> set[str]:
    """The declared reads that WRITE — one authority, shared with #4122/#9575."""
    from tool_surface_capabilities import (
        NON_SDK_READ_BINDINGS,
        READ_THROUGH_WRITE_METHODS,
    )
    return set(READ_THROUGH_WRITE_METHODS) | set(NON_SDK_READ_BINDINGS)


def _top_function(source: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """The single top-level function in ``source``, docstring dropped.

    Dropping the docstring is the point: the CLAIM lives in the docstring, the REACH
    lives in the body, and a prose mention of a writer must not manufacture an offender.
    The body is mutated in place on the parsed tree, so decorators stay in scope — a
    decorator argument that names a writer IS a reach, and the module docstring says so.
    """
    node = ast.parse(textwrap.dedent(source)).body[0]
    if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        raise AssertionError(
            "endpoint is not a plain function — the reach cannot be read: "
            f"{type(node).__name__}")
    body = node.body
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        node.body = body[1:]
    return node


def _names_in(fn: ast.AST, declared: set[str]) -> set[str]:
    """Declared writer names this function body names (one hop is not taken here)."""
    found: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)  # `pack_state.get_tenant_packs` form
        elif isinstance(node, ast.ImportFrom):
            # `from tortoise.pack_state import get_tenant_packs [as gtp]` — the writer
            # is imported whether or not the call site uses the alias, so the IMPORT
            # itself is the reach and an aliased import cannot slip through.
            for alias in node.names:
                found.add(alias.name)
                if alias.asname:
                    found.add(alias.asname)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id == "getattr" and len(node.args) > 1
              and isinstance(node.args[1], ast.Constant)
              and isinstance(node.args[1].value, str)):
            found.add(node.args[1].value)  # getattr(x, "get_tenant_packs")
    return found & declared


def _names_reached(source: str, declared: set[str], module=None) -> set[str]:
    """Declared writers the handler reaches: its own code, plus ONE helper hop."""
    fn = _top_function(source)
    reached = _names_in(fn, declared)
    if reached or module is None:
        return reached  # the hop is taken only when the handler reaches nothing itself
    called = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    for helper_name in sorted(called):
        if helper_name == fn.name:
            continue
        helper = vars(module).get(helper_name)
        if not inspect.isfunction(helper):
            continue
        try:
            helper_src = inspect.getsource(helper)
        except (OSError, TypeError):  # pragma: no cover - built from a def above
            continue
        reached |= _names_in(_top_function(helper_src), declared)
    return reached


def _violations(ops) -> list[str]:
    """Every (method, path) where an operation reaching a declared writer claims it."""
    out = []
    for method, path, description, reached in ops:
        phrase = _claim_in(description)
        if phrase and reached:
            out.append(f"{method} {path}: claims {phrase!r} while its handler reaches "
                       f"{sorted(reached)} — a declared read-through writer")
    return sorted(out)


def _served_operations(app=None, module=None):
    """(method, path, SERVED description, declared writers reached) for every op.

    ``app=`` exists so a probe can drive a synthetic FastAPI app end-to-end: the point
    of this whole file is that the string a CUSTOMER reads is the SERVED one, which a
    route may override independently of its docstring (``@app.get(..., description=…)``),
    so the probe must read the same surface the gate does.
    """
    if app is None:
        from tortoise import hosted_api  # the app is the subject under test
        app = hosted_api.app
    declared = _declared_read_through_writers()
    # A route registered after a first schema build is missing from the cached
    # `app.openapi_schema`, which would read as an absence and fire the assertion below
    # on a route that IS served. The gate reads a freshly generated schema, always.
    app.openapi_schema = None
    schema = app.openapi()
    ops = []
    for route in app.routes:
        methods = sorted(getattr(route, "methods", None) or set())
        if not methods:
            continue  # Mount / static / websocket — not an operation
        if getattr(route, "include_in_schema", True) is False:
            continue  # /openapi.json, /docs, /redoc — not part of the contract
        # An `include_in_schema` route absent from the schema would otherwise read as
        # "served with no description" and be checked for a claim it cannot make — a
        # silent coverage hole, so it is a gate failure instead. (Two routes registered
        # on one (path, method) pair share the single served description; the reach is
        # computed per route, so the offending pairing still surfaces.)
        assert route.path in schema["paths"], (
            f"{route.path} is served but absent from app.openapi()['paths'] — the "
            "description half of this gate would stop covering it silently")
        source = inspect.getsource(route.endpoint)
        helper_module = module if module is not None else inspect.getmodule(route.endpoint)
        for method in methods:
            # Method-level, not just path-level: a route whose `methods` carries a verb
            # FastAPI omits from the schema would otherwise read as "served with no
            # description" and be checked for a claim it cannot make.
            assert method.lower() in schema["paths"][route.path], (
                f"{method} {route.path} is registered but absent from "
                "app.openapi() — the description half of this gate would stop "
                "covering it silently")
            served = schema["paths"][route.path][method.lower()] or {}
            ops.append((
                method,
                route.path,
                served.get("description") or "",
                _names_reached(source, declared, helper_module),
            ))
    return ops


# --- the predicate is not vacuous: probes written to be caught, and not ----------

_SYNTHETIC_OFFENDER = '''
@app.get("/packs")
async def list_packs():
    """#318: read-only pack introspection — the tenant's ACTIVE packs."""
    from tortoise.pack_state import get_tenant_packs
    return get_tenant_packs(None)
'''

_SYNTHETIC_ALIASED_IMPORT = '''
async def list_packs():
    """Read-only list of the tenant's packs."""
    from tortoise.pack_state import get_tenant_packs as gtp
    return gtp(None)
'''

_SYNTHETIC_GETATTR = '''
async def list_packs():
    """Read-only pack listing."""
    import tortoise.pack_state as ps
    return getattr(ps, "get_tenant_packs")(None)
'''

_SYNTHETIC_NEGATED = '''
async def list_packs():
    """The tenant's packs — so not read-only in the strict sense."""
    from tortoise.pack_state import get_tenant_packs
    return get_tenant_packs(None)
'''

_SYNTHETIC_PROSE_ONLY = '''
async def list_packs():
    """Read-only pack listing; the self-heal calls get_tenant_packs once per graph."""
    return []
'''

_SYNTHETIC_PURE_READ = '''
async def health():
    """Read-only liveness probe."""
    return {"ok": True}
'''

_SYNTHETIC_ONE_HOP = '''
async def list_packs(sdk):
    """Read-only pack listing."""
    return _fetch_packs_for_probe(sdk)
'''


def _fetch_packs_for_probe(sdk=None):
    """Module-level helper the one-hop probe reaches through (see ``_SYNTHETIC_ONE_HOP``)."""
    from tortoise.pack_state import get_tenant_packs
    return get_tenant_packs(sdk)


def _helper_that_names_no_writer(sdk=None):
    """The negative twin: a one-hop helper that must NOT make the probe fire."""
    return []


def _ops_from_sources(*sources: str, method: str = "GET"):
    declared = _declared_read_through_writers()
    ops = []
    for source in sources:
        # Read the docstring from a SEPARATE parse: `_top_function` drops it (prose is
        # not a call), and the probe's whole point is the claim that lives there.
        raw = ast.parse(textwrap.dedent(source)).body[0]
        fn = _top_function(source)
        ops.append((method, fn.name, ast.get_docstring(raw) or "",
                    _names_reached(source, declared)))
    return ops


class TestServedOpenApiReadOnlyClaim:
    def test_an_operation_reaching_a_declared_writer_never_claims_read_only(self):
        """The gate itself. Red on `main` before #9583: `/v1/packs` claimed it."""
        offenders = _violations(_served_operations())
        assert offenders == [], (
            "a served OpenAPI description claims read-only while the route can "
            "write — state the write trigger instead of asserting a guarantee:\n  "
            + "\n  ".join(offenders))

    def test_the_gate_actually_examines_pack_introspection(self):
        """Non-vacuity, on BOTH halves, keyed on the writer this gate exists for.

        A gate that stops covering its own subject reports green, which is the failure
        this test exists to prevent — so: the route must still be reached, the SELF-HEAL
        writer specifically must still be the reason (any declared writer would satisfy
        a bare non-emptiness check), and it must still be examined through a served
        description (an empty description string is a claim-free pass).
        """
        ops = _served_operations()
        by_op = {(method, path): (desc, names) for method, path, desc, names in ops}
        assert ("GET", "/v1/packs") in by_op, "the packs introspection route moved or vanished"
        description, reached = by_op[("GET", "/v1/packs")]
        assert "get_tenant_packs" in reached, (
            "the /v1/packs handler no longer derives `get_tenant_packs`, so the "
            f"read-only-claim gate has stopped covering its subject (reached: "
            f"{sorted(reached)}; declared: {sorted(_declared_read_through_writers())})")
        assert description, (
            "/v1/packs is served with an empty OpenAPI description, so the "
            "description half of this gate examines nothing")

    def test_the_served_packs_description_states_the_write(self):
        """Acceptance, positively: the description says the call may write.

        Asserting only the ABSENCE of the claim would pass a description that says
        nothing at all, leaving the customer-facing text merely deleted rather than
        corrected. And asserting the writer's NAME would pass a revision that named
        `ensure_tenant_packs` while getting the trigger, the env switch and the empty
        case wrong.  So the pins are the PREDICATES a reader is promised — a write to
        `:PackInstall`, an all-or-nothing ("no record") trigger, a seed, and the switch
        that disarms it — expressed to accept a re-wording: pinning exact phrases would
        block a PR that keeps the semantics and improves the prose, which is the wrong
        trade for a gate whose subject is wording.
        """
        description = {
            (method, path): desc for method, path, desc, _n in _served_operations()
        }[("GET", "/v1/packs")]
        assert _claim_in(description) is None, (
            f"the served /v1/packs description still makes the claim: {description!r}")
        assert "PackInstall" in description, (
            f"the served /v1/packs description must name what is written: {description!r}")
        assert re.search(r"\b(?:no|none|zero|not\s+any)\b[^.;]{0,80}PackInstall"
                         r"|PackInstall[^.;]{0,80}\b(?:none|zero|nothing)\b",
                         description, re.IGNORECASE), (
            "the description must state the ALL-OR-NOTHING trigger — the self-heal "
            f"fires only when the graph holds no :PackInstall record: {description!r}")
        assert re.search(r"\b(?:re-?)?seed(?:ed|s|ing)?\b", description, re.IGNORECASE) or re.search(
                r"\bstarter\b[^.;]{0,40}(?:PackInstall|install)",
                description, re.IGNORECASE), (
            "the description must say a graph with no installs is not empty — the "
            f"starter set is seeded: {description!r}")
        assert re.search(r"PACK_STATE_DISABLE_SELF_HEAL[^.]{0,60}(?:``1``|=1)", description), (
            "the description must say what DISARMS the write (only the literal 1), or an "
            "operator who sets the switch to any other value still gets a write: "
            f"{description!r}")

    def test_the_d4_record_states_the_ensure_half(self):
        """Acceptance: D4 is a LOCKED decision list, so the correction is a RECORD.

        Amending D4 in place would read as history being tidied; D4a states what D4's
        word promised and what the mechanism actually does, right below D4.  The region
        runs from D4a to the NEXT decision marker, and markers are anchored to LINE
        STARTS on the raw docstring: D4a's own prose cross-references "the D5 backfill",
        so a substring search for `D5` truncates the region inside D4a — it did, and the
        sentence revision 3 added fell outside the window.  Whitespace is collapsed
        AFTER slicing, per ``tests/test_6131_operator_mutation_vocabulary.py``: a
        docstring is wrapped to the line length, so a needle spanning a wrap would fail
        while the prose was correct, and re-wrapping is a legitimate edit (losing the
        record is not).
        """
        from tortoise import pack_state

        raw = pack_state.__doc__ or ""
        flat = " ".join(raw.split())
        assert "D4 Read-only introspection surface" in flat, (
            "D4 must stay VERBATIM — the correction is recorded as D4a, not as an edit")
        marker = re.search(r"(?m)^- D4a \(#9583", raw)
        assert marker, "D4a (the #9583 record) is missing from pack_state's docstring"
        following = re.search(r"(?m)^- D5\b", raw[marker.end():])
        end = marker.end() + following.start() if following else len(raw)
        record = " ".join(raw[marker.start():end].split())
        assert "ensure_tenant_packs" in record, (
            "D4a must record the ensure half: the read can MERGE the starter "
            ":PackInstall set, which is the write D4's word hid")
        assert "get_tenant_packs" in record, (
            "D4a must say WHERE the trigger lives — the read, not the activation helper")
        assert "top up a partial set" in record or "tops up a partial set" in record, (
            "D4a must record the half that distinguishes the helper from the read: a "
            "DIRECT ensure_tenant_packs call re-MERGEs every starter namespace, so it "
            "DOES top up a partial set")

    def test_probe_an_offender_is_caught(self):
        caught = _violations(_ops_from_sources(_SYNTHETIC_OFFENDER))
        assert len(caught) == 1 and "read-only" in caught[0], caught

    def test_probe_an_aliased_import_is_caught(self):
        caught = _violations(_ops_from_sources(_SYNTHETIC_ALIASED_IMPORT))
        assert len(caught) == 1 and "get_tenant_packs" in caught[0], caught

    def test_probe_a_one_hop_helper_is_caught(self):
        """The refactor most likely to be applied to `list_packs` itself.

        The handler source is fed ALONE, exactly as `inspect.getsource(route.endpoint)`
        feeds it, and the helper is a real module-level def — an inline two-def source
        would be parsed as its FIRST def, so the probe would pass without ever taking
        the hop.
        """
        declared = _declared_read_through_writers()
        reached = _names_reached(
            _SYNTHETIC_ONE_HOP, declared, module=sys.modules[__name__])
        assert reached == {"get_tenant_packs"}, reached

    def test_probe_a_one_hop_helper_that_names_nothing_does_not_fire(self):
        declared = _declared_read_through_writers()
        source = '''
async def list_packs(sdk):
    """Read-only pack listing."""
    return _helper_that_names_no_writer(sdk)
'''
        assert _names_reached(source, declared, module=sys.modules[__name__]) == set()

    def test_probe_a_getattr_literal_is_caught(self):
        caught = _violations(_ops_from_sources(_SYNTHETIC_GETATTR))
        assert len(caught) == 1, caught

    def test_probe_every_spelling_of_the_claim_is_caught(self):
        for spelling in ("read-only", "Read-only;", "read only", "READ ONLY",
                         "read_only", "READ_ONLY", "readOnly"):
            ops = [("GET", f"/x/{spelling}", f"#318: {spelling} introspection",
                    {"get_tenant_packs"})]
            assert len(_violations(ops)) == 1, spelling

    def test_probe_negations_are_not_claims(self):
        for negated in (ast.get_docstring(_top_function(_SYNTHETIC_NEGATED)),
                        "not a read-only surface",
                        "isn't read-only",
                        "cannot be read-only",
                        "can't be considered read-only",
                        "no longer read-only"):
            assert _claim_in(negated) is None, negated
        # the negated shape still carries a real reach, so the pair is not vacuous
        assert _violations(_ops_from_sources(_SYNTHETIC_NEGATED)) == []

    def test_probe_a_negated_clause_does_not_excuse_the_next_one(self):
        for text in ("Not an install surface. Read-only when the pack set is configured.",
                     "It is not a write, READ-ONLY."):
            assert _claim_in(text) is not None, text

    def test_probe_prose_mentioning_the_writer_is_not_a_reach(self):
        assert _violations(_ops_from_sources(_SYNTHETIC_PROSE_ONLY)) == []

    def test_probe_a_pure_read_may_claim_read_only(self):
        assert _violations(_ops_from_sources(_SYNTHETIC_PURE_READ)) == []

    def test_probe_the_gate_reads_the_SERVED_string_not_the_docstring(self):
        """The file's whole premise: `description=` overrides the docstring.

        Without this probe the suite could pass while the gate read docstrings — the
        exact surface (#9575) already covers — and the served contract went unchecked.
        """
        from fastapi import FastAPI

        app = FastAPI()

        @app.get("/packs", description="Read-only pack introspection.")
        async def list_packs():
            from tortoise.pack_state import get_tenant_packs
            return get_tenant_packs(None)

        caught = _violations(_served_operations(app=app, module=None))
        assert len(caught) == 1 and "read-only" in caught[0].lower(), caught

    def test_probe_a_non_get_operation_is_examined_too(self):
        """The gate is not GET-only: the falsehood is about the verb the customer sees."""
        from fastapi import FastAPI

        app = FastAPI()

        @app.post("/packs", description="Read-only pack introspection.")
        async def create_packs():
            from tortoise.pack_state import get_tenant_packs
            return get_tenant_packs(None)

        caught = _violations(_served_operations(app=app, module=None))
        assert len(caught) == 1 and caught[0].startswith("POST /packs"), caught

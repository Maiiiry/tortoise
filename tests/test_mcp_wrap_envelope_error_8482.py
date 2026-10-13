"""#8482 — a wrap-envelope tool must not ship a payload its own schema forbids.

WHY THIS FILE EXISTS
--------------------
`tortoise_search` and `tortoise_expand_relationships` failed on the serving
build with::

    MCP error -32602: Structured content does not match the tool's output
    schema: data/result must be array

The issue could not identify the discriminator: `tortoise_check_structure`,
`tortoise_packs_list`, `tortoise_list_batches` and `tortoise_list_namespaces`
all return arrays and all WORKED. It is **not** "returns a list". It is whether
the underlying SDK call **RAISED**: `_safe` turns an exception into a dict, and
FastMCP's wrap envelope (`x-fastmcp-wrap-result`, built from a `-> list[dict]`
annotation) then carries `{"result": {"error": ...}}` where the advertised
schema requires an array. A conforming client validates `structuredContent`
against `outputSchema` and refuses the WHOLE call, so the SDK's real message is
destroyed and replaced by a schema complaint about a payload nobody can read.

These tests pin the properties the fix must have, in the order the issue asked
for them:

* the second factor is the RAISE, not the annotation (two tools, one
  annotation, different outcomes);
* the failure is delivered as an ERROR result a client accepts — no
  `structuredContent` at all — with the real message intact;
* a healthy wrap tool's envelope is byte-identical (the schema is NOT relaxed);
* an object-annotated tool's `_safe` error dict is UNCHANGED (the established
  contract, which works);
* an `anyOf` envelope (`tortoise_query`, `tortoise_delete_entity`) is untouched
  — a dict `result` is VALID there;
* the second trigger of the same class — the wait-bound refusal, whose payload
  has no `result` key at all — is repaired too;
* the raw tool FUNCTION still returns the `_safe` error dict: the repair lives
  at the MCP wire seam, not in the SDK-facing contract.
"""
from __future__ import annotations

import contextlib
from unittest import mock

import pytest
from fastmcp.tools import FunctionTool

from tortoise import mcp_server as ms

# ── Harness ───────────────────────────────────────────────────────────────

@pytest.fixture
def quiet(monkeypatch):
    """No analytics sink: these tests must not reach the network."""
    monkeypatch.setattr(ms, "_emit_mcp_tool_call_telemetry", lambda *a, **k: None)


@pytest.fixture
def stdio(monkeypatch):
    """A transport mode, so `_safe`'s fail-closed gate does not pre-empt the
    SDK call these tests are about (unset mode → the auth refusal)."""
    token = ms._transport_mode.set("http")
    yield
    ms._transport_mode.reset(token)


def _probe(name: str, fn):
    ms.mcp.add_tool(FunctionTool.from_function(fn, name=name, description=name))


def _drop(name: str) -> None:
    # Cleanup must never mask the assertion — hence the blanket suppression.
    with contextlib.suppress(Exception):
        ms.mcp.local_provider.remove_tool(name)


def _wire_payload(result) -> dict:
    """The exact JSON-RPC fields a client validates, as the TS SDK sees them.

    Mirrors `client.callToolStream`: when `structuredContent` is present it is
    validated against the advertised `outputSchema` REGARDLESS of `isError`;
    when it is absent, `isError` must be true or the client refuses the result
    as "has an output schema but did not return structured content".
    """
    return {"structuredContent": result.structured_content,
            "isError": result.is_error,
            "text": "".join(getattr(b, "text", "") for b in (result.content or ()))}


def _schema_validates_against(payload, output_schema) -> bool:
    """The rule the client applies — expressed without the 'array' shortcut.

    The envelope's `result` must satisfy whatever the schema declares for it,
    including the `anyOf` forms two registry tools use.
    """
    structured = payload["structuredContent"]
    if structured is None:
        return payload["isError"]          # absent payload is allowed on an error
    prop = (output_schema.get("properties") or {}).get("result") or {}
    value = structured.get("result")
    if isinstance(prop.get("type"), str):
        checks = {"array": list, "object": dict, "string": str, "boolean": bool}
        expected = checks.get(prop["type"])
        return expected is not None and isinstance(value, expected)
    return True                            # anyOf / undeclared: not our call


async def _registered_schema(name: str):
    return (await ms.mcp.get_tool(name)).output_schema


# ── The second factor: the RAISE, not the annotation ──────────────────────

@pytest.mark.asyncio
async def test_two_tools_one_annotation_the_raise_is_the_discriminator(quiet):
    """The exact confusion the issue could not resolve.

    `_healthy` and `_failing` carry the SAME `-> list[dict]` annotation and the
    same advertised schema. Only the one whose body RAISES (i.e. whose `_safe`
    result is a dict) produced -32602 — so the discriminator is the failure,
    never the shape of the declared return.
    """
    def _healthy() -> list[dict]:
        return [{"id": "p1"}]

    def _failing() -> list[dict]:
        return ms._SafeError({"error": "Type mismatch: expected Null or "
                                       "VectorF32 but was List"})

    _probe("_8482_healthy", _healthy)
    _probe("_8482_failing", _failing)
    try:
        ok = await ms.mcp.call_tool("_8482_healthy", {})
        bad = await ms.mcp.call_tool("_8482_failing", {})

        assert ok.structured_content == {"result": [{"id": "p1"}]}
        assert ok.is_error is False

        assert bad.is_error is True, "the client must be told this failed"
        assert bad.structured_content is None, (
            "the invalid envelope must not reach the wire: a client validates "
            "structuredContent even when isError is true")
        assert "VectorF32" in _wire_payload(bad)["text"], (
            "the SDK's real message must survive — that is the whole point")

        bad_schema = await _registered_schema("_8482_failing")
        assert _schema_validates_against(_wire_payload(bad), bad_schema), (
            "the repaired result must be one the client ACCEPTS")
    finally:
        _drop("_8482_healthy")
        _drop("_8482_failing")


@pytest.mark.asyncio
async def test_the_envelope_is_untouched_when_the_tool_succeeds(quiet):
    """No schema relaxation: a healthy wrap tool's payload is byte-identical."""
    def _two() -> list[dict]:
        return [{"id": "a"}, {"id": "b"}]

    _probe("_8482_two", _two)
    try:
        result = await ms.mcp.call_tool("_8482_two", {})
    finally:
        _drop("_8482_two")
    assert result.structured_content == {"result": [{"id": "a"}, {"id": "b"}]}
    assert result.is_error is False


@pytest.mark.asyncio
async def test_an_object_tool_keeps_its_structured_error(quiet):
    """The established `_safe` contract for dict-returning tools WORKS (the
    advertised schema is an object), so it must not be swept up by the fix."""
    def _mapping() -> dict:
        return ms._SafeError({"error": "boom"})

    _probe("_8482_mapping", _mapping)
    try:
        result = await ms.mcp.call_tool("_8482_mapping", {})
    finally:
        _drop("_8482_mapping")
    assert result.structured_content == {"error": "boom"}
    assert result.is_error is False


@pytest.mark.asyncio
async def test_an_anyof_envelope_is_left_alone(quiet):
    """`tortoise_query` / `tortoise_delete_entity` advertise `anyOf`, where a
    dict `result` is VALID — repairing them would break a working surface."""
    def _union() -> list[dict] | dict:
        return {"error": "page must be >= 1 (1-based), got 0"}

    _probe("_8482_union", _union)
    try:
        result = await ms.mcp.call_tool("_8482_union", {})
    finally:
        _drop("_8482_union")
    # The envelope is kept EXACTLY as FastMCP built it — a dict `result` is one
    # of the types this schema admits.
    assert result.structured_content == {
        "result": {"error": "page must be >= 1 (1-based), got 0"}}
    assert result.is_error is False


# ── The second trigger: the wait-bound refusal ────────────────────────────

@pytest.mark.asyncio
async def test_the_wait_bound_refusal_is_repaired_for_a_wrap_tool(quiet,
                                                                 monkeypatch):
    """Same class, second producer: the refusal payload is `{"error": {...}}`
    with no `result` key, which a wrap schema rejects just as hard. It must
    still carry its wait-bound marker so the timeout classification is not
    lost."""
    from tortoise.mcp_auth import _TRANSPORT_WAIT_BOUND_MESSAGE, ERR_TIMEOUT

    refusal = ms.ToolResult(
        content=_TRANSPORT_WAIT_BOUND_MESSAGE,
        structured_content={"error": {"code": ERR_TIMEOUT,
                                      "message": _TRANSPORT_WAIT_BOUND_MESSAGE,
                                      "data": {"retry_after": 5}}},
        meta={ms._WAIT_BOUND_META_KEY: True},
        is_error=True,
    )

    async def _refuse(*a, **k):
        return refusal

    monkeypatch.setattr(ms, "_await_under_mcp_wait_bound", _refuse)
    result = await ms.mcp.call_tool("tortoise_search", {"query": "q"})
    assert result.is_error is True
    assert result.structured_content is None
    assert _TRANSPORT_WAIT_BOUND_MESSAGE in _wire_payload(result)["text"]
    assert result.meta == {ms._WAIT_BOUND_META_KEY: True}, (
        "dropping the envelope must not drop the wait-bound marker — it is "
        "what classifies the call as a timeout rather than a failure")
    # `fastmcp.wrap_result` described a payload that is no longer there.
    assert "fastmcp" not in (result.meta or {})


# ── The real tool, and the contract the repair must NOT change ────────────

@pytest.mark.asyncio
async def test_the_real_search_tool_is_repaired_when_its_sdk_raises(quiet,
                                                                    stdio):
    """End-to-end on the tool the issue named. The SDK raise is what the issue's
    reproduction hit (`Query timed out` / the #8316 `List` type mismatch); the
    seam must translate it into an error the client can READ."""
    class _RaisingSDK:
        def tortoise_fts_query(self, *a, **kw):
            raise RuntimeError(
                "Type mismatch: expected Null or VectorF32 but was List")

    with mock.patch.object(ms, "_get_org_sdk", _RaisingSDK):
        result = await ms.mcp.call_tool("tortoise_search",
                                        {"query": "positioning", "limit": 10})
    assert result.is_error is True
    assert result.structured_content is None
    assert "VectorF32" in _wire_payload(result)["text"]
    assert _schema_validates_against(
        _wire_payload(result),
        await _registered_schema("tortoise_search"))


def test_the_raw_tool_function_still_returns_the_safe_error_dict(quiet, stdio):
    """The repair is a WIRE-seam repair. `_safe`'s function-level contract —
    an error dict, not an exception — is unchanged (`test_mcp_server.py` relies
    on it), so the MCP fix cannot silently become an SDK-facing one."""
    class _RaisingSDK:
        def tortoise_fts_query(self, *a, **kw):
            raise RuntimeError("boom")

    with mock.patch.object(ms, "_get_org_sdk", _RaisingSDK):
        raw = ms.tortoise_search("positioning")
    assert isinstance(raw, ms._SafeError)
    assert raw["error"] == "boom"


# ── The surface guard: this holds for EVERY array-envelope tool ───────────

@pytest.mark.asyncio
async def test_every_array_envelope_tool_is_recognised():
    """The class fix, not a two-tool patch (#8482 named `tortoise_search` and
    `tortoise_expand_relationships`, but 12 registry tools carry the envelope).

    `_wrap_envelope_result_type` is the seam's READ of the advertised schema;
    if it stops agreeing with the schema, the repair silently stops firing and
    the -32602 comes back unnoticed. This is the assertion that would have
    caught the defect for every affected tool at once.
    """
    from tortoise.tool_registry import TOOL_REGISTRY

    checked = 0
    for entry in TOOL_REGISTRY:
        schema = await _registered_schema(entry.name)
        if not isinstance(schema, dict) or not schema.get("x-fastmcp-wrap-result"):
            continue
        prop = (schema.get("properties") or {}).get("result") or {}
        expected = prop.get("type") if isinstance(prop.get("type"), str) else ""
        resolved = await ms._wrap_envelope_result_type(entry.name)
        assert resolved == (expected or None), (
            f"{entry.name}: seam resolved {resolved!r} but the advertised "
            f"schema declares {expected!r}")
        checked += 1
    assert checked >= 10, (
        f"only {checked} wrap-envelope tools found — the registry or FastMCP's "
        "envelope convention changed, so this guard is no longer measuring what "
        "it was written for")


@pytest.mark.asyncio
async def test_every_wrap_envelope_tool_cannot_satisfy_a_failed_payload():
    """The CLASS guard: every wrap-envelope tool, not the two the issue named.

    #8482 asked for a repo-wide sweep of declared-vs-returned shape. This walks
    the REAL registry and asserts the seam RECOGNISES both failure shapes for
    every tool FastMCP gave an envelope — and that a `result` a UNION envelope
    legitimately admits is left alone. Had this existed, it would have caught
    the defect for all 13 affected tools at once instead of two.
    """
    from tortoise.tool_registry import TOOL_REGISTRY

    #: A `result` each declared type must ACCEPT — the success shape.
    _ACCEPTED = {"array": [{"id": "p1"}], "object": {"id": "p1"},
                 "string": "s", "boolean": True, "number": 1.0,
                 "integer": 1}
    #: (1) `_safe`'s error dict lands INSIDE the envelope.
    _SAFE_FAILURE = {"result": {"error": "boom"}}
    #: (2) the wait-bound refusal carries no `result` at all — and `result` is
    #: REQUIRED, so this one is unreadable even to a union envelope.
    _WAIT_BOUND = {"error": {"code": -32001, "message": "x", "data": {}}}

    typed = unions = 0
    for entry in TOOL_REGISTRY:
        schema = await _registered_schema(entry.name)
        if not isinstance(schema, dict) or not schema.get("x-fastmcp-wrap-result"):
            continue
        assert ms._violates_wrap_envelope(schema, _WAIT_BOUND), (
            f"{entry.name}: a payload with no `result` is invalid against an "
            "envelope that requires one")
        kind = await ms._wrap_envelope_result_type(entry.name)
        if kind is None:
            unions += 1
            assert not ms._violates_wrap_envelope(schema, _SAFE_FAILURE), (
                f"{entry.name}: a UNION envelope admits a dict `result` — "
                "repairing it would break a working surface")
            continue
        typed += 1
        assert ms._violates_wrap_envelope(schema, _SAFE_FAILURE), entry.name
        assert not ms._violates_wrap_envelope(
            schema, {"result": _ACCEPTED[kind]}), (
            f"{entry.name}: a successful `{kind}` payload must pass untouched")
        assert not ms._violates_wrap_envelope(schema, None), (
            f"{entry.name}: FastMCP's own no-payload form is out of scope")
    # Shrinkage is the dangerous direction — the sweep must keep measuring the
    # surface it was written for. Growth (a new envelope tool) is fine and is
    # covered by the loop itself.
    assert typed >= 11 and unions >= 2, (
        f"sweep found {typed} typed + {unions} union envelopes; measured at "
        "11 + 2 when this guard was written")


@pytest.mark.asyncio
async def test_the_named_read_path_siblings(quiet, stdio):
    """The siblings the issue named — and the one it asked about that is fine.

    `tortoise_search` and `tortoise_expand_relationships` carry an ARRAY
    envelope and are repaired. `tortoise_recall` is NOT in that class: it is
    annotated `-> dict`, so it has no wrap envelope and `_safe`'s flat dict is
    exactly what its advertised OBJECT schema admits. It must be left as it is
    — which is the proof this repair is driven by the DECLARED SCHEMA and not
    by "the call failed".

    Both repaired tools returning a client-ACCEPTABLE error is the point: the
    pre-fix payload was refused by the client as a schema violation, so the
    caller could not read the failure at all.
    """
    class _RaisingSDK:
        def tortoise_fts_query(self, *a, **kw):
            raise RuntimeError("boom-search")

        def expand_relationships(self, *a, **kw):
            raise RuntimeError("boom-expand")

        def recall_state(self, *a, **kw):
            raise RuntimeError("boom-recall")

    calls = {"tortoise_search": {"query": "q"},
             "tortoise_expand_relationships": {"point_id": "p1"},
             "tortoise_recall": {"query": "q"}}
    with mock.patch.object(ms, "_get_org_sdk", _RaisingSDK):
        for name, args in calls.items():
            schema = await _registered_schema(name)
            result = await ms.mcp.call_tool(name, args)
            if schema.get("x-fastmcp-wrap-result"):
                assert result.is_error is True, name
                assert result.structured_content is None, name
                assert "boom" in _wire_payload(result)["text"], name
                assert _schema_validates_against(
                    _wire_payload(result), schema), name
            else:
                # Recall: no envelope, so `_safe`'s dict IS the advertised
                # shape — untouched, and NOT an error result.
                assert result.is_error is False, name
                assert result.structured_content == {
                    "mode": "state", "error": "boom-recall"}, name


@pytest.mark.asyncio
async def test_real_mcp_round_trip_delivers_the_sdk_message(quiet, stdio):
    """The issue's reproduction, end to end through a REAL MCP session.

    The -32602 in the report is raised by the CLIENT validating
    `structuredContent` against the advertised `outputSchema`, so reading
    `mcp.call_tool`'s ToolResult cannot establish it. This test speaks real
    JSON-RPC to the server (in-memory transport) and asserts on what a client
    actually receives: no `structuredContent`, `isError` set, and the SDK's own
    message intact — where before the fix the whole call was replaced by a
    schema complaint about a payload nobody could read.
    """
    from fastmcp import Client
    from fastmcp.exceptions import ToolError

    class _RaisingSDK:
        def tortoise_fts_query(self, *a, **kw):
            raise RuntimeError(
                "Type mismatch: expected Null or VectorF32 but was List")

    with mock.patch.object(ms, "_get_org_sdk", _RaisingSDK):
        async with Client(ms.mcp) as client:
            advertised = next(t for t in await client.list_tools()
                              if t.name == "tortoise_search")
            result = await client.call_tool(
                "tortoise_search", {"query": "positioning", "limit": 10},
                raise_on_error=False)
            with pytest.raises(ToolError) as raised:
                await client.call_tool(
                    "tortoise_search", {"query": "positioning", "limit": 10})

    assert result.is_error is True
    assert result.structured_content is None
    assert "VectorF32" in _wire_payload(result)["text"]
    # The message reaches the caller's error channel instead of being replaced
    # by the schema complaint the issue reported.
    assert "VectorF32" in str(raised.value)
    # ...and the advertised schema is UNCHANGED: this is not a relaxation.
    assert advertised.outputSchema["properties"]["result"]["type"] == "array"


@pytest.mark.asyncio
async def test_the_auth_gate_is_classified_through_a_wrap_envelope(monkeypatch):
    """The stdio auth gate, read through the envelope it gets wrapped in.

    The gate's refusal is a dict, so for a `-> list[dict]` tool FastMCP wraps
    it and a TOP-LEVEL `payload.get("error")` probe misses it — the call was
    recorded as `ok` while it had failed. That is the same "a failure that
    reads as a success" class as #8482, and the reason the defect could go
    unnoticed (#8135). The repair also makes the refusal legible, where it used
    to arrive as a -32602 about the envelope.
    """
    events: list[dict] = []
    monkeypatch.setattr(
        ms, "_emit_mcp_tool_call_telemetry",
        lambda org, name, status, latency, kind, count=None:
        events.append({"status": status, "error_kind": kind}))
    # Deterministic gate: `is_dev_mode()` reads TORTOISE_API_KEY at call time.
    monkeypatch.setattr(ms, "_is_dev_mode", lambda: False)
    token = ms._transport_mode.set("stdio")
    try:
        result = await ms.mcp.call_tool("tortoise_search", {"query": "q"})
    finally:
        ms._transport_mode.reset(token)

    assert events and events[0]["status"] == "auth_error"
    assert events[0]["error_kind"] == "stdio_auth_gate"
    assert result.is_error is True
    assert result.structured_content is None
    assert "Authentication required" in _wire_payload(result)["text"]

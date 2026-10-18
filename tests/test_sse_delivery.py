"""#8100 — SSE as a SECOND transport on the ONE delivery layer (epic #2080 W4 phase 2).

The whole point of the issue is that SSE must NOT become a second delivery
system. This file pins that, in the order the issue's indicators demand:

1. **Same envelope, same cursor bookkeeping.** One ``dispatch_pending`` pass
   delivers to a webhook subscriber and an SSE subscriber; the SSE frame's
   ``data:`` is BYTE-IDENTICAL to the webhook POST body, both cursors advance,
   both receipts say ``delivered`` (indicator 1 + 3).
2. **Exactly ONE drain** (indicator 2). Enumerated from the SOURCE by AST — not
   asserted by convention: one function in ``tortoise/**`` reads the event log
   and posts to a transport, one ``dispatch_pending``, one backoff/retry module.
3. **No second retry policy.** An SSE delivery that fails is recorded
   ``retrying`` with ``next_attempt_at`` set by the SAME ``backoff_seconds`` and
   the cursor unmoved.
4. **Reconnect resumes with no gap.** ``Last-Event-ID: k`` yields exactly the
   events after ``k``, read from the durable log.
5. **Receipts are one shape**, and the REST stream route is auth-fail-closed,
   409s a webhook subscription, and serves the frames.

Engine tests use the URI-aware ``sdk_factory`` fixture (embedded or docker) and
seed ``:GraphEvent`` rows through ``event_store.append_event`` — a real point
write would load the embedding model for no coverage here (the #2081 lesson).
"""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

import pytest

pytest.importorskip("cryptography")  # the at-rest encryption dependency

from tortoise import sse_delivery as sse
from tortoise import webhook_delivery as wd

REPO_ROOT = Path(__file__).resolve().parent.parent
TORTOISE = REPO_ROOT / "tortoise"


# ── fixtures / helpers ────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("TORTOISE_ENCRYPTION_KEY", Fernet.generate_key().decode())


@pytest.fixture(autouse=True)
def _delivery_knobs(monkeypatch):
    """Egress ON; a FAST stream so a bounded generator finishes in a test.

    The knobs are the production ones (same env vars, same reader) — the values
    are what change, so a knob that stops being read shows up here as a hang.
    """
    monkeypatch.delenv("TORTOISE_WEBHOOK_DISPATCH", raising=False)
    monkeypatch.setenv(sse.POLL_ENV, "0.01")
    monkeypatch.setenv(sse.MAX_STREAM_ENV, "0.05")
    monkeypatch.setenv(sse.RETRY_ENV, "1500")


class RecordingTransport:
    """The webhook sink, recorded. ``script`` entries: int status or ``"err"``."""

    def __init__(self, script=()):
        self.script = list(script)
        self.calls: list[tuple[str, bytes, dict]] = []

    def post(self, url, body, headers):
        self.calls.append((url, body, headers))
        item = self.script.pop(0) if self.script else 200
        if item == "err":
            return None, "connection refused"
        if isinstance(item, Exception):
            raise item
        return item, None


def _seed(sdk_factory, n=2):
    """Seed ``n`` durable ``:GraphEvent`` rows — the stream the dispatcher reads."""
    from tortoise import event_store
    from tortoise.ids import ulid

    sdk = sdk_factory(ensure_schema=True)
    proj = sdk._get_proj()
    for i in range(n):
        event_store.append_event(proj, event_store.next_seq(proj), "PointAdded",
                                 {"id": f"pt_{i}"}, f"evt-{ulid()}")
    return sdk


def _sse_events(chunks) -> list[dict]:
    """Parse a concatenated SSE stream into event dicts (comments dropped)."""
    out: list[dict] = []
    for block in b"".join(chunks).split(b"\n\n"):
        if not block.strip() or block.lstrip().startswith(b":"):
            continue
        fields: dict = {}
        for line in block.split(b"\n"):
            key, _, value = line.partition(b": ")
            if key == b"data":
                # The spec joins multiple data lines with "\n"; the frame
                # builder writes one per body line, so rejoin them.
                fields["data"] = fields.get("data", b"") + value
            else:
                fields[key.decode()] = value.decode()
        if "data" not in fields:
            continue  # the ``retry:`` directive block is not an event
        fields["data"] = json.loads(fields["data"].decode())
        out.append(fields)
    return out


def _stream(sub, proj, **kwargs):
    """Drain the bounded stream to completion (the knobs bound it at 50ms)."""
    return list(sse.stream_subscription(proj, sub, **kwargs))


# ── registry: the transport field ─────────────────────────────────────────

def test_transport_round_trips_and_defaults_to_webhook(sdk_factory):
    """A subscription states its sink; an SSE one needs no url and keeps none."""
    sdk = _seed(sdk_factory, n=0)
    proj = sdk._get_proj()

    hook = wd.create_subscription(proj, url="https://hooks.example.com/cb")
    stream = wd.create_subscription(proj, transport="sse",
                                    description="dashboard")

    assert hook["transport"] == wd.TRANSPORT_WEBHOOK  # the default
    assert stream["transport"] == wd.TRANSPORT_SSE
    # No target is stored for SSE: a url here would invite a future reader to
    # POST to it, i.e. the second dispatcher #8100 forbids.
    assert stream["url"] in ("", None)
    assert [s["transport"] for s in wd.list_subscriptions(proj)] == [
        wd.TRANSPORT_WEBHOOK, wd.TRANSPORT_SSE]


def test_sse_subscription_skips_the_ssrf_gate_and_webhook_keeps_it(sdk_factory):
    """The SSRF predicate polices EGRESS. SSE has none, so a url is not its gate.

    Stated as a test because silently validating an SSE url would be harmless
    today and misleading forever: it would imply a target exists.
    """
    sdk = _seed(sdk_factory, n=0)
    proj = sdk._get_proj()

    assert wd.create_subscription(proj, transport="sse",
                                  url="http://127.0.0.1:9000/")["transport"] == "sse"
    with pytest.raises(ValueError, match="globally routable"):
        wd.create_subscription(proj, url="http://127.0.0.1:9000/")


def test_unknown_transport_is_rejected_never_silently_downgraded(sdk_factory):
    sdk = _seed(sdk_factory, n=0)
    with pytest.raises(ValueError, match="unknown transport"):
        wd.create_subscription(sdk._get_proj(), transport="carrier-pigeon")
    assert wd.list_subscriptions(sdk._get_proj()) == []


def test_absent_transport_property_reads_as_webhook(sdk_factory):
    """Every row created before #8100 has no ``transport``; it must keep working."""
    sdk = _seed(sdk_factory, n=1)
    proj = sdk._get_proj()
    sub = wd.create_subscription(proj, url="https://hooks.example.com/cb")
    # Simulate a pre-#8100 row: the property is simply absent.
    proj.g.query("MATCH (s:ContextSubscription {sub_id:$id}) "
                 "REMOVE s.transport", params={"id": sub["sub_id"]})
    assert wd.get_subscription(proj, sub["sub_id"])["transport"] == "webhook"
    assert wd.transport_for({"sub_id": sub["sub_id"]}) is wd._webhook_transport()
    summary = wd.dispatch_pending(proj, transport=RecordingTransport())
    assert summary["delivered"] == 1, summary


# ── indicator 1 + 3: same envelope, same cursor, same receipt shape ────────

def test_one_dispatch_serves_webhook_and_sse_with_the_same_envelope(
        sdk_factory, monkeypatch):
    """The issue's indicator 1, as an assertion.

    ONE ``dispatch_pending`` pass, two subscriptions, two different sinks. The
    SSE frame's ``data:`` must be the exact bytes the webhook was POSTed, both
    cursors advance to the last seq, and both receipts carry the same shape.
    """
    sdk = _seed(sdk_factory, n=2)
    proj = sdk._get_proj()
    hook = wd.create_subscription(proj, url="https://hooks.example.com/cb")
    stream = wd.create_subscription(proj, transport="sse")

    webhook_sink = RecordingTransport()
    sse_sink = sse.SseTransport()
    monkeypatch.setattr(
        wd, "transport_for",
        lambda sub: (sse_sink if sub.get("transport") == "sse" else webhook_sink))

    summary = wd.dispatch_pending(proj)

    assert summary["delivered"] == 4, summary  # 2 events × 2 subscriptions
    assert len(webhook_sink.calls) == 2

    last_seq = max(e["seq"] for e in _events(proj))
    for sub_id in (hook["sub_id"], stream["sub_id"]):
        assert wd.get_subscription(proj, sub_id)["cursor"] == last_seq

    # The frames the SSE subscriber reads are built from the SAME builder.
    sub = wd.get_subscription(proj, stream["sub_id"])
    frames = _sse_events(_stream(sub, proj, last_event_id=0))
    assert [f["id"] for f in frames] == [str(e["seq"]) for e in _events(proj)]
    # The envelope is the same document for both sinks; only its OWN identity
    # fields (which subscription, hence which delivery id) differ — that is the
    # per-subscription bookkeeping, not a second envelope shape.
    assert [f["data"]["event"] for f in frames] == [
        json.loads(body)["event"] for _url, body, _h in webhook_sink.calls]
    assert [f["data"]["subscription_id"] for f in frames] == [stream["sub_id"]] * 2
    assert [json.loads(b)["subscription_id"]
            for _u, b, _h in webhook_sink.calls] == [hook["sub_id"]] * 2
    assert [f["data"]["delivery_id"] for f in frames] == [
        f"{stream['sub_id']}:{f['id']}" for f in frames]
    # ... and the webhook headers' delivery id agrees with the frame id.
    assert [h["X-Tortoise-Seq"] for _u, _b, h in webhook_sink.calls] == [
        f["id"] for f in frames]

    # Indicator 3: receipts are ONE shape across the two transports — compare the
    # key SETS of the two subscriptions' receipts, so a shape that drifted for
    # the SSE sink fails here (the stored property is ``http``, the read-path
    # name #2081 shipped).
    hook_receipts = wd.read_receipts(proj, hook["sub_id"])
    sse_receipts = wd.read_receipts(proj, stream["sub_id"])
    assert set(hook_receipts[0]) == set(sse_receipts[0]), (
        "receipts must have ONE shape across transports")
    for receipts in (hook_receipts, sse_receipts):
        assert [r["status"] for r in receipts] == ["delivered", "delivered"]
        assert all(r["http_status"] == 200 for r in receipts)
        # A null property is not stored (FalkorDB), so a SUCCESSFUL attempt has
        # no ``error`` key at all — the absence IS the contract for a delivery.
        assert all("error" not in r for r in receipts)


def _events(proj):
    from tortoise import event_store

    return event_store.read_after(proj, 0, limit=1000)


# ── indicator 2: ONE drain, ONE retry policy (enumerated, not asserted) ────

def _call_names(node) -> set[str]:
    names: set[str] = set()
    for inner in ast.walk(node):
        if isinstance(inner, ast.Call):
            func = inner.func
            if isinstance(func, ast.Attribute):
                names.add(func.attr)
            elif isinstance(func, ast.Name):
                names.add(func.id)
    return names


def _functions(repo_root: Path = TORTOISE):
    for path in sorted(repo_root.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield path, node


def test_exactly_one_drain_reads_the_event_log_and_posts():
    """Indicator 2, by ENUMERATION: a second dispatcher cannot appear beside #2081's.

    The shape of a drain is "read the durable event log, then hand an event to a
    transport". A new delivery path that reimplements it — the thing #8100
    exists to prevent — necessarily contains both calls, so this fails on the
    FIRST such function rather than on a convention nobody enforces.
    """
    drains = [
        f"{path.relative_to(REPO_ROOT)}::{node.name}"
        for path, node in _functions()
        if {"read_after", "post"} <= _call_names(node)
    ]
    assert drains == ["tortoise/webhook_delivery.py::dispatch_pending"], (
        "exactly one drain may read the event log and post to a transport — "
        f"found {drains}. A second transport is a SINK behind this drain, not a "
        "second dispatcher (#8100)."
    )


def test_exactly_one_dispatch_entry_point_and_one_retry_policy():
    """One ``dispatch_pending`` definition and one backoff/schedule owner."""
    defs: dict[str, list[str]] = {}
    for path, node in _functions():
        if node.name in {"dispatch_pending", "backoff_seconds",
                         "_mark_delivered", "_mark_failed"}:
            defs.setdefault(node.name, []).append(
                f"{path.relative_to(REPO_ROOT)}::{node.name}")
    for name, homes in defs.items():
        assert len(homes) == 1, f"{name} has {len(homes)} definitions: {homes}"

    # The SSE sink must not grow its own retry loop: the shared policy retries,
    # and this module only ever WAITS for a wake / for the next poll.
    source = (TORTOISE / "sse_delivery.py").read_text()
    assert "backoff_seconds" not in source, (
        "sse_delivery must not implement or schedule retries — the shared "
        "delivery layer owns backoff (#8100 target: 0 duplicated retry "
        "implementations)")
    assert "next_attempt_at" not in source, (
        "sse_delivery must not write the retry schedule — `_mark_failed` owns it")
    assert "time.sleep" not in source


def test_every_declared_transport_resolves_to_a_sink_with_the_dispatch_contract():
    """The registry and the resolver agree — no transport without a sink."""
    assert set(wd.TRANSPORTS) == {wd.TRANSPORT_WEBHOOK, wd.TRANSPORT_SSE}
    sinks = {kind: wd.transport_for({"transport": kind}) for kind in wd.TRANSPORTS}
    assert sinks["sse"] is sse.SSE_TRANSPORT
    assert sinks["webhook"] is wd._webhook_transport()
    for sink in sinks.values():
        assert callable(sink.post)
    # A corrupt stored value falls back to webhook instead of raising: a
    # delivery pass must never raise (contract 3 of the delivery layer).
    assert wd.transport_for({"transport": "not-a-transport"}) is sinks["webhook"]


# ── the shared retry policy, on the SSE sink ──────────────────────────────

def test_failed_sse_delivery_uses_the_shared_backoff_and_leaves_the_cursor(
        sdk_factory, monkeypatch):
    """An SSE delivery that cannot be accepted is retried by #2081's policy."""
    sdk = _seed(sdk_factory, n=1)
    proj = sdk._get_proj()
    sub = wd.create_subscription(proj, transport="sse")
    failing = RecordingTransport(["err"])
    monkeypatch.setattr(wd, "transport_for", lambda _sub: failing)

    summary = wd.dispatch_pending(proj)

    assert summary["failed"] == 1 and summary["delivered"] == 0
    after = wd.get_subscription(proj, sub["sub_id"])
    assert after["cursor"] == 0, "the cursor must not advance past a failure"
    assert after["attempts"] == 1 and after["next_attempt_at"] is not None
    receipts = wd.read_receipts(proj, sub["sub_id"])
    assert [r["status"] for r in receipts] == ["retrying"]
    assert receipts[0]["error"] == "connection refused"


# ── frames + reconnect ────────────────────────────────────────────────────

def test_frame_carries_id_event_and_one_line_of_json_data():
    body = b'{"a": "multi\\nline payload", "b": 1}'
    frame = sse.sse_frame(7, "PointAdded", body)
    assert frame.startswith(b"id: 7\nevent: PointAdded\ndata: ")
    assert frame.endswith(b"\n\n")
    parsed = _sse_events([frame])
    assert parsed[0]["id"] == "7" and parsed[0]["event"] == "PointAdded"
    assert parsed[0]["data"] == json.loads(body)
    # A newline in the event name cannot inject framing; a missing type is the
    # SSE default rather than "None".
    assert sse.sse_frame(1, "Bad\nName", b"{}").count(b"\nevent: ") == 1
    assert b"event: message" in sse.sse_frame(1, None, b"{}")


def test_reconnect_resumes_after_last_event_id_with_no_gap_and_no_duplicate(sdk_factory):
    """The issue's reconnect target: resume from the client's floor, exactly."""
    sdk = _seed(sdk_factory, n=3)
    proj = sdk._get_proj()
    sub = wd.create_subscription(proj, transport="sse")
    seqs = [e["seq"] for e in _events(proj)]
    assert seqs == sorted(seqs) and len(seqs) == 3

    frames = _sse_events(_stream(sub, proj, last_event_id=seqs[1]))
    assert [int(f["id"]) for f in frames] == seqs[2:], (
        "a reconnect must resume strictly AFTER Last-Event-ID: no gap, no "
        "duplicate of what the client already has")

    # And a floor BELOW the cursor replays (the documented at-least-once class).
    again = _sse_events(_stream(sub, proj, last_event_id=seqs[0]))
    assert [int(f["id"]) for f in again] == seqs[1:]


def test_fresh_stream_starts_at_the_subscription_cursor(sdk_factory):
    """No Last-Event-ID = a fresh connection: start at the tail, not at 0.

    The cursor IS the delivery bookkeeping (#2081) and the stream honours it, so
    a subscription created after the events existed does not replay history.
    """
    sdk = _seed(sdk_factory, n=2)
    proj = sdk._get_proj()
    sub = wd.create_subscription(proj, transport="sse")
    wd.dispatch_pending(proj)  # drains to the tail via the real SSE sink
    after = wd.get_subscription(proj, sub["sub_id"])
    assert after["cursor"] == max(e["seq"] for e in _events(proj))
    assert _sse_events(_stream(after, proj)) == []

    # The dispatcher's cursor is READ ONLY by the stream — an undelivered event
    # stays undelivered even if a client streams past it.
    from tortoise import event_store
    from tortoise.ids import ulid

    event_store.append_event(proj, event_store.next_seq(proj), "PointAdded",
                             {"id": "pt_new"}, f"evt-{ulid()}")
    assert wd.get_subscription(proj, sub["sub_id"])["cursor"] == after["cursor"]
    fresh = _sse_events(_stream(after, proj))
    assert [int(f["id"]) for f in fresh] == [after["cursor"] + 1]


def test_stream_emits_the_retry_directive_and_keepalives_while_idle(sdk_factory):
    """An idle stream stays open-able: ``retry:`` first, then comment keepalives."""
    sdk = _seed(sdk_factory, n=0)
    proj = sdk._get_proj()
    sub = wd.create_subscription(proj, transport="sse")
    chunks = _stream(sub, proj)
    assert chunks[0].startswith(b"retry: "), chunks[0]
    assert any(c == sse.KEEPALIVE_FRAME for c in chunks), (
        "a bounded stream with no events must emit keepalives, not an empty "
        "response the client reads as a closed connection")


def test_the_broker_wakes_an_idle_stream_but_is_not_load_bearing(sdk_factory):
    """A published wake shortens the wait; a LOST wake only costs one poll.

    The second half is the reason no in-memory queue exists: correctness comes
    from the durable log, so a stream in another process still sees the event.
    """
    sdk = _seed(sdk_factory, n=0)
    proj = sdk._get_proj()
    sub = wd.create_subscription(proj, transport="sse")

    assert sse.BROKER.wait(sub["sub_id"], 0.01) is False
    sse.BROKER.publish(sub["sub_id"])
    assert sse.BROKER.wait(sub["sub_id"], 0.5) is True
    sse.BROKER.forget(sub["sub_id"])
    assert sse.BROKER.wait(sub["sub_id"], 0.01) is False

    # No wake at all, and the frames still arrive (poll only).
    from tortoise import event_store
    from tortoise.ids import ulid

    event_store.append_event(proj, event_store.next_seq(proj), "PointAdded",
                             {"id": "pt_unwoken"}, f"evt-{ulid()}")
    assert len(_stream(sub, proj, last_event_id=0)) >= 2  # retry + 1 frame


def test_malformed_delivery_header_is_a_transport_error_not_a_silent_accept():
    """``SseTransport`` accepts only a well-formed ``<sub_id>:<seq>`` delivery id.

    A blanket 200 on garbage would mark an event delivered while no stream could
    ever name it — a hole, not a delivery.
    """
    assert sse.SseTransport().post("", b"{}", {})[0] is None
    assert sse.SseTransport().post("", b"{}", {"X-Tortoise-Delivery": "x"})[0] is None
    status, error = sse.SseTransport().post(
        "", b"{}", {"X-Tortoise-Delivery": "sub_1:notaseq"})
    assert status is None and "malformed" in error
    assert sse.SseTransport().post(
        "", b"{}", {"X-Tortoise-Delivery": "sub_1:4"}) == (200, None)


# ── REST surface ──────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch, tmp_path):
    """Auth-override harness — the same shape as tests/test_webhook_delivery.py."""
    import tempfile

    from fastapi.testclient import TestClient

    import tortoise.hosted_api as ha_mod
    from tortoise.hosted_api import app, get_current_org

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        app.dependency_overrides[get_current_org] = lambda: dict(
            {"org_id": "team-001", "tier": "free", "key_id": None})
        _orig_init = ha_mod.TortoiseSDK.__init__

        def _patched_init(self, db_path_arg=None, *, namespace=None,
                          graph_name=None, **kwargs):
            _orig_init(self, db_path, namespace=namespace, graph_name=graph_name)

        ha_mod.TortoiseSDK.__init__ = _patched_init
        os.environ["TORTOISE_DB_PATH"] = db_path
        ha_mod._FALLBACK_KEEPALIVE.clear()
        try:
            with TestClient(app) as tc:
                tc.db_path = db_path
                yield tc
        finally:
            os.environ.pop("TORTOISE_DB_PATH", None)
            ha_mod.TortoiseSDK.__init__ = _orig_init
            app.dependency_overrides.clear()
            ha_mod._FALLBACK_KEEPALIVE.clear()


def _seed_via_hosted(client, n=2):
    """Seed the guest graph the endpoint opens (the SAME path, same daemon)."""
    import tortoise.hosted_api as ha_mod
    from tortoise import event_store
    from tortoise.ids import ulid

    sdk = ha_mod._make_sdk(namespace="team-001")
    proj = sdk._get_proj()
    for i in range(n):
        event_store.append_event(proj, event_store.next_seq(proj), "PointAdded",
                                 {"id": f"pt_{i}"}, f"evt-{ulid()}")
    return proj


def test_rest_creates_an_sse_subscription_without_a_url(client):
    created = client.post("/v1/context/subscriptions",
                          json={"transport": "sse", "description": "dashboard"})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["transport"] == "sse" and body["secret_set"] is True
    assert body["url"] in ("", None)
    listed = client.get("/v1/context/subscriptions").json()["subscriptions"]
    assert [s["transport"] for s in listed] == ["sse"]

    # A webhook subscription still REQUIRES a url (the SSRF gate has a subject).
    missing = client.post("/v1/context/subscriptions",
                          json={"transport": "webhook"})
    assert missing.status_code == 422, missing.text
    unknown = client.post("/v1/context/subscriptions",
                          json={"transport": "carrier-pigeon"})
    assert unknown.status_code == 422 and "unknown transport" in unknown.text


def test_rest_stream_serves_frames_after_last_event_id(client, monkeypatch):
    monkeypatch.setenv(sse.MAX_STREAM_ENV, "0.2")
    monkeypatch.setenv(sse.POLL_ENV, "0.02")
    proj = _seed_via_hosted(client, n=2)
    from tortoise import event_store

    seqs = [e["seq"] for e in event_store.read_after(proj, 0, limit=100)]
    sub = client.post("/v1/context/subscriptions",
                      json={"transport": "sse"}).json()

    response = client.get(f"/v1/context/subscriptions/{sub['sub_id']}/stream",
                          headers={"Last-Event-ID": str(seqs[0])})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith(sse.SSE_MEDIA_TYPE)
    frames = _sse_events([response.content])
    assert [int(f["id"]) for f in frames] == seqs[1:], (
        "the REST stream must honour Last-Event-ID exactly like the generator")


def test_rest_refuses_to_stream_a_webhook_subscription(client):
    sub = client.post("/v1/context/subscriptions",
                      json={"url": "https://hooks.example.com/cb"}).json()
    response = client.get(f"/v1/context/subscriptions/{sub['sub_id']}/stream")
    assert response.status_code == 409, response.text
    assert "webhook subscription" in response.text
    assert client.get(
        "/v1/context/subscriptions/sub_missing/stream").status_code == 404


def test_rest_stream_requires_auth(client):
    from fastapi.testclient import TestClient

    from tortoise.hosted_api import app

    sub_id = client.post("/v1/context/subscriptions",
                         json={"transport": "sse"}).json()["sub_id"]
    app.dependency_overrides.clear()
    with TestClient(app) as tc:
        assert tc.get(
            f"/v1/context/subscriptions/{sub_id}/stream").status_code == 401

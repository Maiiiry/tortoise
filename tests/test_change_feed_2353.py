"""The #2353 semantic change feed — per-claim deltas over the events family.

WHAT THIS PINS
--------------
1. **Off by default.** With `TORTOISE_CHANGE_FEED` unset, `poll_events`' response
   is byte-identical (no `changes` key) and `update_point`'s content edit writes
   NO `:GraphEvent` — the store's claim-transition contract
   (`tests/test_event_store.py::test_content_edit_emits_nothing`) is intact.
2. **PointRevised reaches the store when the feed is on** — the source half of
   indicator (1), drawn as an opt-in so the default stream is untouched.
3. **`what_changed` is semantic** — one call returns per-claim deltas with
   before→after for content, validity and status, aggregated from the SAME page
   `poll_events` already returns, rather than a client-side replay of raw events.

NOTE ON THE E2E FIXTURE. The issue's E2E names "supersede + retract + evidence-add
on ONE point". That exact triple is UNREACHABLE: retract and supersede are both
terminal (`_ALLOWED_TRANSITIONS`; the shared lifecycle guard refuses the second),
so one point can carry at most one terminal transition. The fixture therefore
drives the reachable sequence on one point (create → revise → evidence-add →
supersede) and asserts the `retracted` delta on its own point, so every semantic
kind the E2E names is covered by a real write.

Runnable (embedded carve-out lane):
  TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest tests/test_change_feed_2353.py -q
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _graph_events(proj):
    rows = proj.g.query(
        "MATCH (e:GraphEvent) RETURN properties(e) ORDER BY e.seq").result_set
    return [r[0] for r in rows]


def _group(changes, claim_id):
    for g in changes:
        if g.get("id") == claim_id:
            return g
    return None


def _kinds(changes, claim_id):
    g = _group(changes, claim_id)
    return [c["change"] for c in (g["changes"] if g else [])]


# ── 1. the off switch is byte-identical ────────────────────────────────────

def test_off_by_default_adds_no_changes_key(monkeypatch, sdk_factory, tmp_path):
    monkeypatch.delenv("TORTOISE_CHANGE_FEED", raising=False)
    sdk = sdk_factory(tmp_path)
    try:
        p = sdk.create_point("statement", "off")
        sdk.update_point(p["id"], content="off-v2")
        resp = sdk.poll_events()
        assert set(resp) == {"events", "next_cursor"}, resp
        assert "changes" not in resp
    finally:
        sdk.close()


def test_off_by_default_keeps_revisions_out_of_the_store(monkeypatch, sdk_factory,
                                                         tmp_path):
    """The recorded decision, re-pinned: a content edit emits NOTHING to the
    :GraphEvent store unless the change feed is switched on."""
    monkeypatch.delenv("TORTOISE_CHANGE_FEED", raising=False)
    sdk = sdk_factory(tmp_path)
    try:
        p = sdk.create_point("statement", "before")
        sdk.update_point(p["id"], content="after")
        assert [e["type"] for e in _graph_events(sdk._get_proj())] == ["PointAdded"]
    finally:
        sdk.close()


def test_falsey_spellings_are_off(monkeypatch, sdk_factory, tmp_path):
    monkeypatch.setenv("TORTOISE_CHANGE_FEED", "0")
    sdk = sdk_factory(tmp_path)
    try:
        sdk.create_point("statement", "x")
        assert "changes" not in sdk.poll_events()
    finally:
        sdk.close()


# ── 2. PointRevised reaches the store when the feed is on ──────────────────

def test_revision_reaches_the_store_when_enabled(monkeypatch, sdk_factory, tmp_path):
    monkeypatch.setenv("TORTOISE_CHANGE_FEED", "1")
    sdk = sdk_factory(tmp_path)
    try:
        p = sdk.create_point("statement", "v1")
        sdk.update_point(p["id"], content="v2")
        sdk.update_point(p["id"], validTo="2030-01-01T00:00:00+00:00")
        rows = [e for e in _graph_events(sdk._get_proj())
                if e["type"] == "PointRevised"]
        assert len(rows) == 2, rows
        payloads = [json.loads(r["payload"]) for r in rows]
        assert all(pl.get("id") == p["id"] for pl in payloads), payloads
        # the revision payload rides the store, so a poll sees the new content
        assert any(pl.get("new_content") == "v2" for pl in payloads), payloads
    finally:
        sdk.close()


# ── 3. the feed is semantic ────────────────────────────────────────────────

def test_what_changed_returns_per_claim_deltas(monkeypatch, sdk_factory, tmp_path):
    monkeypatch.setenv("TORTOISE_CHANGE_FEED", "1")
    sdk = sdk_factory(tmp_path)
    try:
        p = sdk.create_point("statement", "v1")
        sdk.update_point(p["id"], content="v2")
        sdk.update_point(p["id"], content="v3")
        ev = sdk.create_point("statement", "supporting")
        sdk.create_operator("IMPL", p["id"], [ev["id"]])
        succ = sdk.create_point("statement", "successor")
        sdk.supersede_point(p["id"], succ["id"])

        resp = sdk.poll_events()
        assert "changes" in resp
        deltas = _group(resp["changes"], p["id"])["changes"]
        assert [c["change"] for c in deltas] == [
            "created", "revised", "revised", "evidence-added", "superseded",
        ]

        # before→after for CONTENT: from the SECOND revision on, the window
        # holds the previously observed content. The creation record carries
        # only `content_hash` (the store's PointAdded payload), so the FIRST
        # in-window revision reports no `before` rather than an invented one.
        assert "before" not in deltas[1]
        assert deltas[2]["before"] == {"content": "v2"}
        assert deltas[2]["after"] == {"content": "v3"}

        # STATUS: nothing in the window observed the pre-transition status
        # (the PointAdded payload carries no status), so the delta reports the
        # write path's PRECONDITION under its own key — never a fabricated
        # `before.status`. The transition itself is the `change` name.
        superseded = deltas[4]
        assert superseded["after"]["status"] == "superseded"
        assert superseded["after"]["supersededBy"] == succ["id"]
        assert superseded["precondition"] == {"terminal": False}
        assert "before" not in superseded

        # before→after for VALIDITY: the supersede's recorded window close.
        assert superseded["after"]["validTo"]
    finally:
        sdk.close()


def test_retraction_reports_its_own_kind(monkeypatch, sdk_factory, tmp_path):
    monkeypatch.setenv("TORTOISE_CHANGE_FEED", "1")
    sdk = sdk_factory(tmp_path)
    try:
        p = sdk.create_point("statement", "doomed")
        sdk.retract_point(p["id"])
        g = _group(sdk.poll_events()["changes"], p["id"])
        assert [c["change"] for c in g["changes"]] == ["created", "retracted"]
        assert g["changes"][1]["after"] == {"status": "retracted"}
    finally:
        sdk.close()


def test_evidence_add_is_news_for_both_endpoints(monkeypatch, sdk_factory, tmp_path):
    monkeypatch.setenv("TORTOISE_CHANGE_FEED", "1")
    sdk = sdk_factory(tmp_path)
    try:
        src = sdk.create_point("statement", "claim")
        tgt = sdk.create_point("statement", "evidence")
        op = sdk.create_operator("IMPL", src["id"], [tgt["id"]])
        changes = sdk.poll_events()["changes"]
        for cid in (src["id"], tgt["id"], op["id"]):
            assert "evidence-added" in _kinds(changes, cid), cid
    finally:
        sdk.close()


def test_handler_annotations_report_before_after(monkeypatch, sdk_factory, tmp_path):
    """`OperatorAnnotated` names the EP dims — the delta compares observed values."""
    monkeypatch.setenv("TORTOISE_CHANGE_FEED", "1")
    sdk = sdk_factory(tmp_path)
    try:
        a = sdk.create_point("statement", "a")
        b = sdk.create_point("statement", "b")
        op = sdk.create_operator("IMPL", a["id"], [b["id"]])
        sdk.annotate_operator(op["id"], 0.5, 0.5, 0.5, 0.5)
        sdk.annotate_operator(op["id"], 0.9, 0.5, 0.5, 0.5)
        ann = [c for c in _group(sdk.poll_events()["changes"], op["id"])["changes"]
               if c["change"] == "annotated"]
        assert len(ann) == 2
        assert ann[1]["before"]["bias"] == 0.5
        assert ann[1]["after"]["bias"] == 0.9
        assert "precondition" not in ann[1]
    finally:
        sdk.close()


# ── 4. the cursor scopes the feed ─────────────────────────────────────────

def test_cursor_scopes_the_feed(monkeypatch, sdk_factory, tmp_path):
    monkeypatch.setenv("TORTOISE_CHANGE_FEED", "1")
    sdk = sdk_factory(tmp_path)
    try:
        p = sdk.create_point("statement", "v1")
        cursor = sdk.poll_events()["next_cursor"]     # consumed the create
        sdk.update_point(p["id"], content="v2")
        resp = sdk.poll_events(after=cursor)
        assert [c["change"] for c in _group(resp["changes"], p["id"])["changes"]] == [
            "revised",
        ]
        # the create is behind the cursor and is NOT re-reported
        assert all(c["change"] != "created" for c in resp["changes"][0]["changes"])
    finally:
        sdk.close()


# ── 5. a stream is allowed to grow types this build does not know ───────────

def test_unknown_type_and_shapeless_payload_are_skipped(monkeypatch, sdk_factory,
                                                        tmp_path):
    monkeypatch.setenv("TORTOISE_CHANGE_FEED", "1")
    sdk = sdk_factory(tmp_path)
    try:
        from tortoise import event_store
        proj = sdk._get_proj()
        sdk.create_point("statement", "known")
        event_store.ensure_event_schema(proj)
        event_store.append_event(proj, event_store.next_seq(proj),
                                 "SomeFutureEvent", {"id": "future"}, "ev-future")
        # a shaped row whose payload decoded to a non-mapping must not raise
        proj.g.query(
            "CREATE (e:GraphEvent {seq: 9900, ts: 'x', type: 'PointRevised', "
            "payload: '[1,2,3]', event_id: 'ev-list'})")
        changes = sdk.poll_events()["changes"]
        assert all(g["id"] != "future" for g in changes)
        assert changes, "the feed must still describe the rows it does understand"
    finally:
        sdk.close()


def test_pure_fold_needs_no_graph(monkeypatch):
    """`aggregate_changes` is a pure function over rows — assertable with no DB,
    which is what keeps the field cheap enough to sit on a read path."""
    from tortoise.change_feed import aggregate_changes
    events = [
        {"seq": 2, "ts": "t2", "event_id": "e2", "type": "PointSuperseded",
         "payload": {"id": "p1", "new_id": "p2", "valid_to": "2030-01-01"}},
        {"seq": 1, "ts": "t1", "event_id": "e1", "type": "PointAdded",
         "payload": {"id": "p1", "kind": "statement"}},
    ]
    out = aggregate_changes(events)
    # `p2` is the SUCCESSOR (named in `after.supersededBy`), not a subject: the
    # supersede is news about `p1`, and labelling `p2` "superseded" would state
    # the opposite of what happened.
    assert [g["id"] for g in out] == ["p1"]
    # seq order, not input order
    assert [c["seq"] for c in out[0]["changes"]] == [1, 2]
    assert out[0]["changes"][1]["precondition"] == {"terminal": False}
    assert "before" not in out[0]["changes"][1]
    assert out[0]["changes"][1]["after"]["supersededBy"] == "p2"


def test_revision_status_before_after_is_observed_only(monkeypatch):
    """Status before→after is reported when the window OBSERVED the prior
    status (a promote is a PointRevised carrying `status`), and omitted — never
    fabricated — for the first sighting."""
    from tortoise.change_feed import aggregate_changes
    events = [
        {"seq": 1, "ts": "t1", "event_id": "e1", "type": "PointRevised",
         "payload": {"id": "p", "new_content": "a", "status": "draft"}},
        {"seq": 2, "ts": "t2", "event_id": "e2", "type": "PointRevised",
         "payload": {"id": "p", "status": "live"}},
    ]
    deltas = aggregate_changes(events)[0]["changes"]
    assert "before" not in deltas[0]
    assert deltas[1]["before"] == {"status": "draft"}
    assert deltas[1]["after"] == {"status": "live"}
    assert "precondition" not in deltas[1]


def test_supersede_is_news_about_the_superseded_claim(monkeypatch, sdk_factory,
                                                      tmp_path):
    """The successor is named, never mislabelled: it must NOT appear as a
    `superseded` group of its own."""
    monkeypatch.setenv("TORTOISE_CHANGE_FEED", "1")
    sdk = sdk_factory(tmp_path)
    try:
        old = sdk.create_point("statement", "old")
        new = sdk.create_point("statement", "new")
        sdk.supersede_point(old["id"], new["id"])
        changes = sdk.poll_events()["changes"]
        assert _kinds(changes, old["id"])[-1] == "superseded"
        assert _kinds(changes, new["id"]) == ["created"]
    finally:
        sdk.close()


def test_existing_mcp_tool_relays_the_field_without_a_surface_change(
        monkeypatch, tmp_path):
    """The field is reachable through the EXISTING `tortoise_events_poll` tool:
    the tool's declared parameters are unchanged (MCP tool count +0), and the
    off switch keeps its reply byte-identical.

    That the served handler fingerprint is untouched is pinned separately by
    `tools/surface-guard.py` / `tools/surface_manifest.py check` (which compare
    the frozen baseline against a fresh derivation from the code) — the point
    HERE is that the capability is reachable at all, on the approved read.
    """
    import inspect

    from tortoise.mcp_server import _transport_mode, tortoise_events_poll
    from tortoise.sdk import TortoiseSDK

    assert list(inspect.signature(tortoise_events_poll).parameters) == [
        "after", "types", "limit",
    ]
    sdk = TortoiseSDK(os.path.join(str(tmp_path), "evt-mcp.db"))
    monkeypatch.setattr("tortoise.mcp_server._get_org_sdk", lambda: sdk)
    token = _transport_mode.set("stdio")
    try:
        p = sdk.create_point("statement", "mcp relay")
        monkeypatch.delenv("TORTOISE_CHANGE_FEED", raising=False)
        assert "changes" not in tortoise_events_poll()
        monkeypatch.setenv("TORTOISE_CHANGE_FEED", "on")
        out = tortoise_events_poll()
        assert _kinds(out["changes"], p["id"]) == ["created"]
    finally:
        _transport_mode.reset(token)
        sdk.close()


def test_enabled_reads_the_declared_truthy_contract(monkeypatch):
    from tortoise.change_feed import CHANGE_FEED_ENV, enabled
    for raw, want in (("1", True), ("TRUE", True), ("on", True), ("yes", True),
                      ("0", False), ("off", False), ("", False), ("garbage", False)):
        monkeypatch.setenv(CHANGE_FEED_ENV, raw)
        assert enabled() is want, raw
    monkeypatch.delenv(CHANGE_FEED_ENV, raising=False)
    assert enabled() is False

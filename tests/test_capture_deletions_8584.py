"""#8584 — the capture path must SURFACE the extractor's result-level
DELETE-soft records and must NOT APPLY them.

Background (re-measured on this tree, not copied from the issue body):
``extractor_v2.execute_embed`` resolves an explicit conversation retraction
("forget my gym schedule") into ``result["deletions"]`` — a RESULT-level
record (E7 D5; D8 keeps it out of the Layer-1 payload). The only consumer in
the repo is the EVAL lane (``tools/longmem_eval/ingest_v2._apply_deletions`` →
``sdk.retract_point``); ``sdk._extract_session_v2`` — the shared self-hosted +
hosted capture seam — read ``payload``/``errors``/``warnings``/``noops`` and
never ``deletions``, so a retraction reached the extractor and then vanished
with no trace on any product surface.

The fix here is deliberately HALF of the issue's two separable halves, and the
other half is NOT an oversight:

* **Applied** — out of scope until approval. E7 gate 1 (payload channel +
  ``compute_client_commit_id`` canonical + hosted reconcile/quota semantics,
  which is also why ``deletions`` never enters the Layer-1 payload) and gate 3
  (NOOP/DELETE applied to a SHARED production graph outside the eval harness —
  needs gate 1 plus an operator-action audit) are ⛔ owner/architecture-approval
  items: ``docs/plans/2026-08-20-1539-e7-consolidation.md`` §5, "not approved
  by default". A capture path that retracted points on its own would silently
  reverse a recorded decision.
* **Surfaced** — in scope, and what these tests pin. The drop stops being
  silent: the records ride ``meta["deletions"]`` and the receipt carries a
  warning naming the withdrawn points and saying they were not applied.

So this file is the guard on the recorded decision, not just on the message:
``test_capture_does_not_apply_deletion_8584`` fails the day someone wires
``retract_point`` into the capture seam without the approval the plan requires
(it would flip the prior point to ``status='retracted'``), and the negative
control fails the day the warning turns into blanket noise on every capture.
"""

from __future__ import annotations

import pytest

from tortoise.sdk import TortoiseSDK

PRIOR_CONTENT = "my gym schedule is 6pm on weekdays"
SESSION_ID = "sess-8584"
NOW = "2026-10-14T12:00:00Z"


@pytest.fixture()
def sdk(tmp_path, force_sparse_tfidf):
    # Conftest-free apart from the embedder pin: the seam needs a real
    # projection and nothing else. ``force_sparse_tfidf`` keeps the file
    # deterministic and cheap — the assertion is about a Point's ``status``, not
    # its vector, and without it the first ``create_point`` pays the cold
    # embedder load (measured 31-142 s on this box, and HF-cache dependent).
    # Under a ``TORTOISE_DB_URI`` the SDK redirects to the server lane
    # (Epic #1647 D-1=A), so this file runs on both the docker and the embedded
    # carve-out lanes; URI-less it is a per-test redislite file.
    return TortoiseSDK(db_path=str(tmp_path / "t.db"))


def _stub_extractor(monkeypatch, out: dict) -> None:
    """Replace ``extractor_v2.extract_session_v2`` with a canned result.

    The subject is the SDK seam's treatment of ``out["deletions"]`` — the
    extractor's own derivation of that record is covered by
    ``tests/test_consolidation_4way.py`` / ``tests/test_extractor_v2.py``.
    """
    import tortoise.extractor_v2 as ev2

    def _fake(model, conversation, **kw):
        return out

    monkeypatch.setattr(ev2, "extract_session_v2", _fake)
    # The inner gate only checks that a routing-usable provider or the offline
    # seam is present; the stub above never calls a model.
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")


def _payload() -> dict:
    return {"schema_version": "1", "session_id": SESSION_ID,
            "client_commit_id": "", "summary": "", "story_arc": "",
            "entities": [], "points": [], "events": [], "operators": [],
            "supersessions": []}


def _status(sdk: TortoiseSDK, pid: str) -> str | None:
    rows = sdk._get_proj().g.query(
        "MATCH (p:Point {id:$id}) RETURN p.status",
        params={"id": pid}).result_set
    assert rows, f"the prior point {pid} must still exist"
    return rows[0][0]


def test_capture_does_not_apply_deletion_8584(sdk, monkeypatch):
    """A resolved retraction is NOT applied on the capture path (E7 gate 3).

    The prior point stays live — no ``retract_point`` call reaches the shared
    graph from capture — and the record is surfaced on ``meta`` + the receipt
    warnings instead of disappearing.
    """
    pid = sdk.create_point("statement", PRIOR_CONTENT)["id"]
    before = _status(sdk, pid)
    assert before in (None, "live", "draft")

    _stub_extractor(monkeypatch, {
        "payload": _payload(),
        "noops": [],
        "deletions": [{"point_id": pid,
                       "evidence": f"explicit retraction in session {SESSION_ID}"}],
        "warnings": [], "errors": [], "stats": {},
    })

    _extracted, meta = sdk._extract_session_v2(
        [{"role": "user", "content": "forget my gym schedule"}],
        SESSION_ID, NOW)

    # (1) NOT applied — the point is not tombstoned.
    assert _status(sdk, pid) == before

    # (2) NOT silently dropped — the structured record rides meta.
    assert [d["point_id"] for d in meta["deletions"]] == [pid]

    # (3) NOT silently dropped — the receipt names it and the reason class.
    surfaced = [w for w in meta["warnings"] if "NOT applied" in w]
    assert len(surfaced) == 1, meta["warnings"]
    assert pid in surfaced[0]
    assert "1 retraction(s)" in surfaced[0]
    assert "8584" in surfaced[0]  # the gate's own handle, for the next reader


def test_capture_surfaces_every_deletion_id_beyond_the_first_five_8584(
        sdk, monkeypatch):
    """The warning truncates names, never the COUNT — a long retraction list
    must still report how many were withheld."""
    ids = [sdk.create_point("statement", f"placeholder fact {i}")["id"]
           for i in range(7)]
    _stub_extractor(monkeypatch, {
        "payload": _payload(), "noops": [],
        "deletions": [{"point_id": p, "evidence": "withdrawn"} for p in ids],
        "warnings": [], "errors": [], "stats": {},
    })

    _extracted, meta = sdk._extract_session_v2(
        [{"role": "user", "content": "forget all of that"}], SESSION_ID, NOW)

    message = next(w for w in meta["warnings"] if "NOT applied" in w)
    assert "7 retraction(s)" in message
    assert "(+2 more)" in message
    assert len(meta["deletions"]) == 7


def test_capture_warning_is_absent_without_deletions_8584(sdk, monkeypatch):
    """Negative control: no retraction, no warning, and ``meta["deletions"]``
    is an empty list (present-but-empty, so a caller can tell "no retraction"
    from "not read at all")."""
    _stub_extractor(monkeypatch, {
        "payload": _payload(), "noops": [], "deletions": [],
        "warnings": [], "errors": [], "stats": {},
    })

    _extracted, meta = sdk._extract_session_v2(
        [{"role": "user", "content": "nothing to withdraw"}], SESSION_ID, NOW)

    assert meta["deletions"] == []
    assert not [w for w in meta["warnings"] if "NOT applied" in w]

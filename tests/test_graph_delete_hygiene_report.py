"""#8746: the graph-delete refusal record is written AND surfaced.

`tests/_live_utils.py::drop_test_graph` deletes a per-run FalkorDB graph at
teardown through `tortoise.graph_delete_guard.safe_graph_delete`, behind a
fail-closed name gate. It is log-and-continue BY POLICY (a teardown must not red
a suite), and the session sweep remains the backstop.

That policy has a capture problem the repo has already measured: a PASSING
test's `logger.warning` is DISCARDED by pytest capture (#7735 / #1103 — verified
0 occurrences under this CI's own flags). Relying on the log alone would make a
systematic refusal invisible, and the per-run graph leak #8746 removes would
return unobserved — the very defect #8746's own test docstring names ("the leak
stayed invisible because the only evidence consulted was the suite's own
bookkeeping").

So the tolerated refusals/failures are ACCUMULATED and handed to
`tests/_tmpdir_hygiene.py::write_tolerated_cleanup_report` as an EXTRA SECTION of
the `tempdir-hygiene-end.json` artifact. That file — not a new one — because the
suite-session jobs ALREADY dump it (pinned by
`tests/test_tmpdir_hygiene.py::test_suite_jobs_surface_the_tolerated_leak`): a
second artifact nothing reads is not an observability channel, and adding a dump
step is a workflow change (#8746 review r3 P2).

This file pins all three halves: the accumulation, the artifact section (write
and merge), and the WIRING that connects them at session end.

HERMETIC — no DB, no lane, no fixture.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

from tests import _live_utils
from tests._tmpdir_hygiene import write_tolerated_cleanup_report

REPO = Path(__file__).resolve().parent.parent
_SECTION = "drop_test_graph_events"
_ARTIFACT = "tempdir-hygiene-end.json"


class _FakeProj:
    """A projection stand-in: the name is all the gate reads."""

    def __init__(self, name) -> None:
        self.graph_name = name
        self.db = None


def test_a_tolerated_refusal_reaches_the_shared_artifact(tmp_path):
    """The refusal is WRITTEN, not only logged (the whole point of the channel)."""
    snapshot = _live_utils.graph_delete_events()
    try:
        probe = "team_review_probe_8746"
        _live_utils.drop_test_graph(_FakeProj(probe))
        recorded = [e for e in _live_utils.graph_delete_events()
                    if e["name"] == probe]
        assert recorded and recorded[-1]["event"] == "refused", recorded
        path = write_tolerated_cleanup_report(
            log_dir=str(tmp_path), extra={_SECTION: _live_utils.graph_delete_events()})
        assert path is not None, "no artifact written for a tolerated refusal"
        assert path.endswith(_ARTIFACT), path
        with open(path) as fh:
            written = json.load(fh)
        entries = written[_SECTION]
        assert any(e["name"] == probe and e["event"] == "refused"
                   for e in entries), entries
    finally:
        # The probe must not fork into the real session artifact (the #7735
        # `_discard_tolerated_leaks` precedent).
        _live_utils._restore_graph_delete_events(snapshot)


def test_a_clean_session_writes_no_graph_delete_section(tmp_path):
    """No tolerated event → no section, so a run cannot report a fabricated leak.

    The artifact may still be written for an unrelated tolerated tempdir leak;
    what must NOT happen is an invented graph-delete entry.
    """
    snapshot = _live_utils.graph_delete_events()
    try:
        _live_utils._restore_graph_delete_events([])
        assert write_tolerated_cleanup_report(
            log_dir=str(tmp_path), extra={_SECTION: []}) is None
        assert not (tmp_path / _ARTIFACT).exists()
    finally:
        _live_utils._restore_graph_delete_events(snapshot)


def test_the_section_merges_and_cannot_truncate_a_prior_record(tmp_path):
    """A peer/second session must not blank entries it does not own (#1103 shape).

    The section is merged BY NAME into whatever is already in the artifact. A
    plain truncate would lose a peer's record wholesale — and then a real leak
    reads as "never happened".
    """
    prior = tmp_path / _ARTIFACT
    prior.write_text(json.dumps({_SECTION: [
        {"name": "test_peer_owned", "event": "failed", "reason": "peer"},
    ]}))
    snapshot = _live_utils.graph_delete_events()
    try:
        _live_utils._restore_graph_delete_events([])
        _live_utils._record_delete_event("test_this_session", "refused", "probe")
        write_tolerated_cleanup_report(
            log_dir=str(tmp_path),
            extra={_SECTION: _live_utils.graph_delete_events()})
        written = json.loads(prior.read_text())
        names = {e["name"] for e in written[_SECTION]}
        assert names == {"test_peer_owned", "test_this_session"}, names
        # The tempdir key is still present and untouched by the new section.
        assert written["tolerated_cleanup_leaks"] == [], written
    finally:
        _live_utils._restore_graph_delete_events(snapshot)


def test_the_session_finalizer_feeds_the_section_into_the_artifact():
    """The WIRING is pinned, because an unwired writer is the r3 P2 defect.

    `tests/conftest.py::_redislite_hygiene` must hand `graph_delete_events()` to
    `write_tolerated_cleanup_report(extra=…)` — and it must be THAT finalizer:
    `_server_graph_hygiene` is URI-gated, while `live_proj` skips on a TCP probe
    and so also runs on the URI-less lane, where a refusal would otherwise be
    recorded in memory only.

    Asserted on the call's own arguments (an AST walk), not on a substring: a
    mention in a comment must not satisfy it.
    """
    tree = ast.parse((REPO / "tests" / "conftest.py").read_text())
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", getattr(node.func, "attr", ""))
        == "write_tolerated_cleanup_report"
        and any(kw.arg == "extra" for kw in node.keywords)
    ]
    assert len(calls) == 1, (
        "expected exactly one `write_tolerated_cleanup_report(extra=…)` call in "
        f"tests/conftest.py, found {len(calls)} — an unwired section is silent")
    extra = next(kw for kw in calls[0].keywords if kw.arg == "extra")
    assert isinstance(extra.value, ast.Dict), ast.dump(extra.value)[:200]
    keys = [k.value for k in extra.value.keys if isinstance(k, ast.Constant)]
    assert _SECTION in keys, (
        f"the session finalizer no longer feeds {_SECTION} into the artifact; "
        f"keys={keys} — the tolerated graph-delete record would be "
        "memory-only (#8746 review r3 P2)")

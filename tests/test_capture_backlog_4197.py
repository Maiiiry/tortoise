"""#4197 — the SAVED BACKLOG: offer the choice, price it, honour it.

The owner decision (recorded verbatim on #4197, 2026-09-19) is that a user whose
sessions were captured store-only (no provider key, #3892; or extraction turned
off, #4258) is OFFERED the choice to extract the saved backlog — with that
option RECOMMENDED and its cost visible before the spend — or to keep only
future conversations, with the choice re-offerable.

What made the choice impossible before this change is the property these tests
pin FIRST: a store-only 2xx was the only capture outcome that left no local
trace at all. A successful extraction writes a receipt; a failure writes a
breadcrumb; a store-only deferral writes neither (deliberately — a receipt
would make every later re-import skip the POST on a stale receipt, and the
session could then never gain its memory points). So the machine could not
enumerate its own backlog, could not price it, could not remember the user's
choice, and could not report which of the three states it was in.

MUTATIONS THAT RED THESE:
  * drop `record_not_processed` from the store-only branch of
    `sessions import` → the ledger stays empty and the report/offer vanish;
  * drop `mark_processed` from the receipt-write success path → a processed
    session stays offered forever (the report never flips);
  * let `record_not_processed` overwrite a `processed` record → a processed
    session is re-offered and re-processed;
  * make `set_policy` store an unknown choice, or make `read_policy` default to
    `declined` for an absent policy → the report claims a choice the user never
    made;
  * drop the provider gate from `--process` → a keyless run "processes" every
    entry, extracts nothing, and claims progress (the run's verdict is read
    from the ledger, which is what keeps that honest).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from tortoise import __main__ as main
from tortoise import capture_backlog as backlog


@pytest.fixture
def places(monkeypatch, tmp_path):
    """A HOME-scoped local state: receipts + ledger derived from ONE base.

    `TORTOISE_IMPORT_RECEIPT_DIR` is the shared override — the ledger derives
    from its PARENT through `hook_install.local_state_dir`, the same derivation
    the receipt writer and the breadcrumb use. That is the property that keeps
    the three local records in one tree (#4373's class), so the fixture uses
    the real derivation rather than pointing the ledger somewhere else.
    """
    home = tmp_path / "home"
    receipts = tmp_path / "receipts"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TORTOISE_IMPORT_RECEIPT_DIR", str(receipts))
    monkeypatch.setenv("TORTOISE_TEST_MODE", "1")
    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    (home / ".tortoise").mkdir(parents=True, exist_ok=True)
    (home / ".tortoise" / "credentials.json").write_text(json.dumps(
        {"api_key": "tt_test", "api_url": "https://legit.example.com"}),
        encoding="utf-8")
    for k in ("OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
              "GEMINI_API_KEY", "TORTOISE_SESSION_LLM_MOCK"):
        monkeypatch.delenv(k, raising=False)
    return home, receipts


def _transcript(tmp_path: Path, name: str = "session.jsonl", turns: int = 4):
    """A parses-as-pi transcript with `turns` real turns."""
    lines = [
        json.dumps({"type": "message",
                    "message": {"role": "user" if i % 2 == 0 else "assistant",
                                "content": f"saved turn {i} with content"}})
        for i in range(turns)
    ]
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class _FakeResponse:
    def __init__(self, body: dict):
        self._body = json.dumps(body).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _store_only_body(session_id: str, turns: int = 4) -> dict:
    return {
        "session_id": session_id, "turns": turns, "extracted": 0, "points": [],
        "extraction_mode": "no-provider",
        "warnings": ["no LLM provider key configured (set e.g. "
                     "OPENROUTER_API_KEY or DEEPSEEK_API_KEY) — the session's "
                     "turns were STORED and remain searchable, but LLM "
                     "extraction into memory points was skipped"],
        "errors": [], "ok": False,
    }


def _extracted_body(session_id: str, turns: int = 4) -> dict:
    body = _store_only_body(session_id, turns)
    body.update({"extraction_mode": "llm:mock", "extracted": 3, "ok": True,
                 "warnings": []})
    return body


def _posting(body_for):
    """A `urllib.request.urlopen` fake that records posted payloads."""
    posted: list[dict] = []

    def _urlopen(req, timeout=None):
        posted.append(json.loads(req.data.decode("utf-8")))
        return _FakeResponse(body_for(posted[-1]))

    return _urlopen, posted


# ── The ledger: three states, and the property none of them may violate ─────


def test_store_only_import_records_the_backlog_and_writes_no_receipt(places,
                                                                     tmp_path):
    """The core defect: the deferral used to leave NO local trace."""
    _, receipts = places
    transcript = _transcript(tmp_path)
    with mock.patch("urllib.request.urlopen",
                    _posting(lambda payload: _store_only_body("imp_store1"))[0]):
        rc = main._cmd_sessions_import(main.argparse.Namespace(
            file=str(transcript), harness="pi", session_id="imp_store1"))

    assert rc == 0, "a store-only 2xx is a deferral, not a failure"
    assert not receipts.exists() or not list(receipts.glob("*.json")), \
        "a deferred store-only capture must write NO receipt (a receipt would " \
        "make every later re-import skip the POST, #4188)"

    records = backlog.sessions()
    assert [r["session_id"] for r in records] == ["imp_store1"]
    record = records[0]
    assert record["status"] == backlog.STATUS_NOT_PROCESSED
    assert record["file"] == str(transcript)
    assert record["harness"] == "pi"
    assert record["turns"] == 4
    assert record["estimate"] > 0, "the offer must be priceable from the record"
    assert "no LLM provider key" in record["reason"]


def test_summary_reports_all_three_states_with_cost(places):
    """not-processed / processed / declined — and the cost only counts what a
    run would still spend."""
    backlog.record_not_processed(session_id="s_pending", turns=3, estimate=9)
    backlog.record_not_processed(session_id="s_done", turns=5, estimate=15)
    backlog.mark_processed("s_done", processed_at="2026-10-07T00:00:00Z")

    state = backlog.summary()
    counts = state["counts"]
    assert counts[backlog.STATUS_NOT_PROCESSED] == 1
    assert counts[backlog.STATUS_PROCESSED] == 1
    assert state["declined"] is False, "an UNANSWERED choice is not a decline"
    assert state["cost_units"] == 9, \
        "an already-processed entry's spend already happened"
    assert state["turns"] == 3

    backlog.set_policy(backlog.CHOICE_FUTURE_ONLY, decided_at="2026-10-07T00:00:00Z")
    state = backlog.summary()
    assert state["declined"] is True
    assert state["counts"][backlog.STATUS_NOT_PROCESSED] == 1, \
        "declining is a POLICY: the saved sessions stay listed, not erased"


def test_policy_refuses_an_unknown_choice(places):
    """A typo'd policy would read as asked-and-answered."""
    assert backlog.set_policy("maybe") is False
    assert backlog.read_policy() == {}
    assert backlog.summary()["declined"] is False


def test_record_never_downgrades_a_processed_session(places):
    """A later store-only re-capture must not re-list an extracted session."""
    backlog.record_not_processed(session_id="s_x", turns=2, estimate=6)
    assert backlog.mark_processed("s_x", processed_at="t") is True
    assert backlog.record_not_processed(session_id="s_x", turns=2, estimate=6) \
        is False
    assert backlog.summary()["counts"][backlog.STATUS_PROCESSED] == 1


def test_mark_processed_is_a_noop_for_a_session_never_deferred(places):
    """Inventing a `processed` record would report a backlog that never was."""
    assert backlog.mark_processed("never_seen") is False
    assert backlog.sessions() == []


def test_session_id_cannot_escape_the_ledger_directory(places):
    """The id arrives from a CLI argument and a server response."""
    assert backlog.record_not_processed(session_id="../../escape",
                                        turns=1, estimate=1) is True
    written = list(backlog.backlog_dir().glob("*.json"))
    assert len(written) == 1
    assert written[0].parent == backlog.backlog_dir()
    assert backlog.summary()["counts"][backlog.STATUS_NOT_PROCESSED] == 1


def test_corrupt_records_read_as_nothing_recorded(places):
    """A malformed/unreadable record must not become a traceback in a report."""
    target = backlog.backlog_dir() / "s_broken.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{not json", encoding="utf-8")
    assert backlog.sessions() == []
    assert backlog.summary()["counts"][backlog.STATUS_NOT_PROCESSED] == 0


def test_no_recommendation_over_an_empty_backlog(places):
    """A recommendation over nothing would turn every capture into an ad."""
    assert backlog.recommendation(backlog.summary()) is None
    assert main._print_backlog_offer() == 0


# ── The CLI: report, decline, process ──────────────────────────────────────


def test_backlog_report_shows_the_cost_before_the_choice(places, capsys):
    home, _ = places
    backlog.record_not_processed(session_id="s_1", harness="pi", turns=6,
                                 estimate=18, reason="no LLM provider key",
                                 file=str(home / "a.jsonl"))
    rc = main.main(["sessions", "backlog"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "1 not-processed" in out
    assert "~18 estimated extraction unit(s)" in out
    assert "6 saved turn(s)" in out
    assert "--process" in out and "Recommended" in out, \
        "the backlog option must be the visibly recommended one"


def test_backlog_json_emits_the_machine_readable_state(places, capsys):
    """`--json` is the machine-readable half of the same state the report
    renders — so the three states are consumable without parsing prose."""
    home, _ = places
    backlog.record_not_processed(session_id="s_1", harness="pi", turns=4,
                                 estimate=12, reason="no LLM provider key",
                                 file=str(home / "a.jsonl"))
    rc = main.main(["sessions", "backlog", "--json"])
    out = capsys.readouterr().out

    assert rc == 0
    # The report comes first, then the JSON document.
    document = out[out.index("\n{") + 1:]
    state = json.loads(document)
    assert state["counts"][backlog.STATUS_NOT_PROCESSED] == 1
    assert state["cost_units"] == 12
    assert state["declined"] is False
    assert state["statuses"] == [backlog.STATUS_NOT_PROCESSED,
                                 backlog.STATUS_PROCESSED]


def test_backlog_process_without_a_provider_refuses_and_spends_nothing(
        places, capsys):
    home, _ = places
    transcript = _transcript(home, "a.jsonl")
    backlog.record_not_processed(session_id="s_1", harness="pi", turns=4,
                                 estimate=12, reason="no LLM provider key",
                                 file=str(transcript))
    with mock.patch("urllib.request.urlopen") as urlopen:
        rc = main.main(["sessions", "backlog", "--process"])
    out = capsys.readouterr().out

    assert rc == 1
    assert not urlopen.called, "no key ⇒ no POST, no spend, no false progress"
    assert "no LLM provider key" in out
    assert backlog.summary()["counts"][backlog.STATUS_NOT_PROCESSED] == 1


def test_backlog_process_re_runs_the_capture_and_marks_the_entry_processed(
        places, capsys, monkeypatch):
    """The backlog run drives the EXISTING re-capture path (the #2335 gate) —
    the same POST the keyed `sessions import` makes, so the #4194 turn write
    (and its embedding) lands for the stored turns too."""
    home, receipts = places
    transcript = _transcript(home, "a.jsonl")
    backlog.record_not_processed(session_id="s_1", harness="pi", turns=4,
                                 estimate=12, reason="no LLM provider key",
                                 file=str(transcript))
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")

    urlopen, posted = _posting(lambda payload: _extracted_body("s_1"))
    with mock.patch("urllib.request.urlopen", urlopen):
        rc = main.main(["sessions", "backlog", "--process"])
    out = capsys.readouterr().out

    assert rc == 0, out
    assert [p["session_id"] for p in posted] == ["s_1"]
    assert "re-capturing s_1" in out, "progress is visible (#4197 AC6)"
    assert "extracted ✓" in out
    assert "1 processed, 0 still not-processed" in out, "completion is visible"
    assert (receipts / "s_1.json").exists(), \
        "the re-capture took the normal receipt path"
    state = backlog.summary()
    assert state["counts"][backlog.STATUS_PROCESSED] == 1
    assert state["counts"][backlog.STATUS_NOT_PROCESSED] == 0
    assert state["cost_units"] == 0


def test_a_multi_entry_run_prints_one_cost_report_and_one_completion(
        places, capsys, monkeypatch):
    """The cost is shown ONCE before the spend, then progress, then a
    completion — never N copies of the report burying the verdict."""
    home, _ = places
    first = _transcript(home, "a.jsonl")
    second = _transcript(home, "b.jsonl")
    for sid, path in (("s_1", first), ("s_2", second)):
        backlog.record_not_processed(session_id=sid, harness="pi", turns=4,
                                     estimate=12, reason="no LLM provider key",
                                     file=str(path))
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    urlopen, _ = _posting(lambda payload: _extracted_body("mock"))
    with mock.patch("urllib.request.urlopen", urlopen):
        rc = main.main(["sessions", "backlog", "--process"])
    out = capsys.readouterr().out

    assert rc == 0, out
    assert out.count("Saved backlog:") == 1, out
    assert out.count("Saved backlog run complete") == 1, out
    assert "[1/2]" in out and "[2/2]" in out
    assert "2 processed, 0 still not-processed" in out
    assert backlog.summary()["counts"][backlog.STATUS_PROCESSED] == 2


def test_backlog_process_keeps_a_failed_entry_and_reports_it(places, capsys,
                                                             monkeypatch):
    """A verdict read from the LEDGER, never from the import's exit code: a
    still-store-only re-import exits 0 while extracting nothing."""
    home, _ = places
    transcript = _transcript(home, "a.jsonl")
    backlog.record_not_processed(session_id="s_1", harness="pi", turns=4,
                                 estimate=12, reason="no LLM provider key",
                                 file=str(transcript))
    # A key is "available" but the server still stores-only (extraction
    # disabled org / provider rejected the work): the run must NOT claim it.
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    with mock.patch("urllib.request.urlopen",
                    _posting(lambda payload: _store_only_body("s_1"))[0]):
        rc = main.main(["sessions", "backlog", "--process"])
    out = capsys.readouterr().out

    assert rc == 1
    assert "still not extracted" in out
    assert "0 processed, 1 still not-processed" in out
    assert backlog.summary()["counts"][backlog.STATUS_NOT_PROCESSED] == 1


def test_backlog_process_reports_a_missing_transcript(places, capsys,
                                                      monkeypatch):
    home, _ = places
    backlog.record_not_processed(session_id="s_gone", harness="pi", turns=4,
                                 estimate=12, reason="no LLM provider key",
                                 file=str(home / "vanished.jsonl"))
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    rc = main.main(["sessions", "backlog", "--process"])
    out = capsys.readouterr().out

    assert rc == 1
    assert "still not extracted" in out
    assert backlog.summary()["counts"][backlog.STATUS_NOT_PROCESSED] == 1


def test_future_only_records_the_choice_and_leaves_the_backlog_alone(
        places, capsys):
    home, _ = places
    backlog.record_not_processed(session_id="s_1", harness="pi", turns=4,
                                 estimate=12, reason="no LLM provider key",
                                 file=str(home / "a.jsonl"))
    with mock.patch("urllib.request.urlopen") as urlopen:
        rc = main.main(["sessions", "backlog", "--future-only"])
    out = capsys.readouterr().out

    assert rc == 0
    assert not urlopen.called, "declining must not extract anything"
    assert "Only future conversations" in out or "only future conversations" in out
    state = backlog.summary()
    assert state["declined"] is True
    assert state["counts"][backlog.STATUS_NOT_PROCESSED] == 1

    # The declined state is distinguishable from "processed" and the choice is
    # re-offerable: the report names the way back.
    following = main.main(["sessions", "backlog"])
    out = capsys.readouterr().out
    assert following == 0
    assert "declined" in out
    assert "--process" in out


def test_future_only_is_overridden_by_a_later_explicit_process(places,
                                                               monkeypatch,
                                                               capsys):
    home, _ = places
    transcript = _transcript(home, "a.jsonl")
    backlog.record_not_processed(session_id="s_1", harness="pi", turns=4,
                                 estimate=12, reason="no LLM provider key",
                                 file=str(transcript))
    assert backlog.set_policy(backlog.CHOICE_FUTURE_ONLY, decided_at="t") is True
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    with mock.patch("urllib.request.urlopen",
                    _posting(lambda payload: _extracted_body("s_1"))[0]):
        rc = main.main(["sessions", "backlog", "--process"])
    capsys.readouterr()

    assert rc == 0
    assert backlog.read_policy()["choice"] == backlog.CHOICE_BACKLOG
    assert backlog.summary()["declined"] is False


def test_a_keyed_import_retires_the_entry_and_offers_what_remains(
        places, capsys, monkeypatch):
    """The offer appears at the point the key just proved it works (#4197 AC1),
    and only while a backlog remains."""
    home, _ = places
    first = _transcript(home, "a.jsonl")
    second = _transcript(home, "b.jsonl")
    backlog.record_not_processed(session_id="s_1", harness="pi", turns=4,
                                 estimate=12, reason="no LLM provider key",
                                 file=str(first))
    backlog.record_not_processed(session_id="s_2", harness="pi", turns=4,
                                 estimate=12, reason="no LLM provider key",
                                 file=str(second))
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    with mock.patch("urllib.request.urlopen",
                    _posting(lambda payload: _extracted_body("s_1"))[0]):
        rc = main._cmd_sessions_import(main.argparse.Namespace(
            file=str(first), harness="pi", session_id="s_1"))
    out = capsys.readouterr().out

    assert rc == 0
    state = backlog.summary()
    assert state["counts"][backlog.STATUS_PROCESSED] == 1
    assert state["counts"][backlog.STATUS_NOT_PROCESSED] == 1
    assert "Saved backlog: 1 not-processed" in out
    assert "--process" in out

    # With the backlog exhausted the offer goes silent — an ordinary import
    # must not become an advertisement.
    with mock.patch("urllib.request.urlopen",
                    _posting(lambda payload: _extracted_body("s_2"))[0]):
        rc = main._cmd_sessions_import(main.argparse.Namespace(
            file=str(second), harness="pi", session_id="s_2"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "Saved backlog" not in out
    assert backlog.summary()["counts"][backlog.STATUS_NOT_PROCESSED] == 0


def test_doctor_reports_the_backlog_and_the_recommended_action(places, capsys,
                                                               tmp_path):
    """`tortoise doctor` is where the provider key's state is reported, so it is
    where the owner's choice belongs."""
    home, _ = places
    backlog.record_not_processed(session_id="s_1", harness="pi", turns=10,
                                 estimate=30, reason="no LLM provider key",
                                 file=str(home / "a.jsonl"))
    main.main(["doctor", "--path", str(tmp_path / "doctor.db")])
    out = capsys.readouterr().out

    line = next(ln for ln in out.splitlines() if "Saved backlog" in ln)
    assert "1 saved session(s)" in line
    assert "~30 estimated extraction unit(s)" in line
    assert "--process" in line and "Recommended" in line


def test_doctor_is_silent_about_a_backlog_that_does_not_exist(places, capsys,
                                                              tmp_path):
    main.main(["doctor", "--path", str(tmp_path / "doctor.db")])
    out = capsys.readouterr().out
    assert "Saved backlog" not in out


def test_the_stored_backlog_survives_an_unwritable_ledger(places, capsys):
    """Every ledger write is best-effort: losing the offer must never cost the
    capture (the hook path's fail-open contract)."""
    with mock.patch.object(backlog, "_write_json", return_value=False):
        assert backlog.record_not_processed(session_id="s_1", turns=1,
                                            estimate=3) is False
    assert backlog.summary()["counts"][backlog.STATUS_NOT_PROCESSED] == 0

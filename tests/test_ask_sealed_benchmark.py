"""#9097 — the sealed ask run, the gates, and "fixed in advance" as a CHECK.

The HEAVY half of the sealed-benchmark suite: three real runs over embedded
stores (6 cases x 2 arms each) plus a real-git pre-registration history check.
The hermetic corpus/pre-registration/receipt contract lives in
`tests/test_ask_sealed_corpus.py` (the file a corpus-only diff selects).

What this file pins — the parts a stubbed-out contract test cannot:

  1. the sealed run produces both arms per case and CANNOT read its own answer
     key (every gold loader raises for the duration of the run);
  2. THE BAR CAN SAY NO, through the real seeding + rendering + judging path:
     a knowledge-update win with a verbatim loss is a `regression`, and dropping
     the planted supersession turns the D8 gate red;
  3. "the pre-registration precedes the run" is a git CHECK — ancestry AND
     byte-equality at the run commit, with a same-commit run and a retuned bar
     both rejected.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.eval.ask import corpus, generate_corpus  # noqa: E402, RUF100
from tools.longmem_eval import ask_sealed  # noqa: E402, RUF100
from tools.longmem_eval.judge import MockJudge  # noqa: E402, RUF100
from tortoise.ask_lane import _reset_ask_reader_cache_for_tests
from tortoise.sdk import TortoiseSDK


def _answers() -> tuple[tuple[str, str], ...]:
    """question → answer map, from the AUTHORED corpus source.

    Deliberately NOT from the sealed gold: a test double that reads the key
    would demonstrate the key is reachable, which is the opposite of the
    guarantee under test.
    """
    pairs = []
    for pair in generate_corpus.PAIRS:
        pairs.append((pair["ku_question"], pair["new"]["text"]))
        pairs.append((pair["verbatim_question"], pair["old"]["text"]))
    return tuple(pairs)


class _AnswerReader:
    """Answers from the question alone — the reader's quality is not under test
    here, the INSTRUMENT is."""

    last_completion_tokens = 8

    def complete(self, *, system: str, user: str) -> str:
        del system
        for fragment, answer in _answers():
            if fragment in user:
                return answer
        return ""

    def close(self) -> None:
        pass


class _VerbatimRegressionReader(_AnswerReader):
    """Commits the verbatim answer on the FIRST call for a verbatim case and the
    CURRENT state on the second — i.e. raw arm correct, distilled arm regressed.

    The reader double cannot see which arm it is serving, and the regression must
    not hinge on whether retrieval happened to surface a windowed claim (that
    would make the test a coincidence). `get_sealed_run` runs the two arms per
    case in `ARMS` order (`raw` → `distilled`), which this file pins separately,
    so call order IS the arm identity here.
    """

    def __init__(self) -> None:
        self._calls: dict[str, int] = {}

    def complete(self, *, system: str, user: str) -> str:
        del system
        for pair in generate_corpus.PAIRS:
            if pair["verbatim_question"] in user:
                n = self._calls[pair["case_id"]] = self._calls.get(
                    pair["case_id"], 0) + 1
                return (pair["old"]["text"] if n == 1
                        else pair["new"]["text"])
            if pair["ku_question"] in user:
                return pair["new"]["text"]
        return ""


def _install_reader(monkeypatch, reader=None) -> None:
    import tortoise.ask_lane as ask_lane_mod

    fake = reader or _AnswerReader()
    monkeypatch.setattr(ask_lane_mod, "_default_ask_reader_factory",
                        lambda: fake)


def _sdk_factory(tmp_path: Path):
    counter = iter(range(10_000))

    def make():
        return TortoiseSDK(str(tmp_path / f"store{next(counter)}.db"))

    return make


@pytest.fixture(autouse=True)
def _clean_ask_state(monkeypatch):
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    monkeypatch.delenv("TORTOISE_ASK_MODEL", raising=False)
    _reset_ask_reader_cache_for_tests()
    yield
    _reset_ask_reader_cache_for_tests()


@pytest.fixture(scope="module")
def prereg() -> dict:
    return ask_sealed.build_preregistration()


# ── 1. the sealed run, and the sealing ───────────────────────────────────

def test_the_sealed_run_has_both_arms_per_case_and_cannot_read_the_gold(
        monkeypatch, tmp_path):
    """ONE real run proves the structure AND the sealing.

    Every gold loader raises for the duration of the run, so a runner that read
    its own answer key could not complete; grading works again once the key is
    reachable, which is what makes the split an ORDER (read path first, key
    second) rather than a prohibition on grading.
    """
    def _explode(*_a, **_kw):
        raise AssertionError(
            "the sealed run read the gold — the answer key must be scorer-side "
            "only")

    _install_reader(monkeypatch)
    monkeypatch.setattr(corpus, "load_gold", _explode)
    monkeypatch.setattr(corpus, "load_gold_all", _explode)
    run = ask_sealed.get_sealed_run(sdk_factory=_sdk_factory(tmp_path),
                                    stub_reader=True)
    assert run["graded"] is False
    assert len(run["cases"]) == len(corpus.case_ids())
    assert run["corpus_hash"] == corpus.compute_fixtures_hash()
    assert ask_sealed.ARMS == ("raw", ask_sealed.GATE_ARM)
    for record in run["cases"]:
        assert set(record["arms"]) == set(ask_sealed.ARMS), record
        for arm in ask_sealed.ARMS:
            assert set(record["arms"][arm]) >= {
                "ok", "hypothesis", "old_seen", "rendered_live", "flagged"}

    monkeypatch.undo()
    _install_reader(monkeypatch)
    scored = ask_sealed.score_with_gold(run, judge=MockJudge())
    assert scored["graded"] is True
    assert all(isinstance(reading["committed"], bool)
               for record in scored["cases"]
               for reading in record["arms"].values())


# ── 2. the bar can say NO, through the real pipeline ─────────────────────

def test_a_ku_win_with_a_verbatim_loss_is_a_regression(monkeypatch, tmp_path,
                                                       prereg):
    """THE exit bar, end to end: the knowledge-update number is perfect and the
    run still FAILS, because the distilled corpus cost verbatim recall."""
    _install_reader(monkeypatch, _VerbatimRegressionReader())
    raw = ask_sealed.get_sealed_run(sdk_factory=_sdk_factory(tmp_path),
                                    stub_reader=True)
    scored = ask_sealed.score_with_gold(raw, judge=MockJudge())
    evaluation = ask_sealed.evaluate(scored, prereg=prereg)

    metrics = evaluation["metrics"]
    assert metrics["current_state_distilled"]["rate"] == 1.0, metrics
    verbatim = evaluation["gates"]["verbatim_no_regression"]
    assert verbatim["regressions"], verbatim
    assert evaluation["gates"]["d8_superseded_live"]["ok"] is True
    assert evaluation["verdict"] == "regression", evaluation
    assert evaluation["failure_origin"] == "gate_regression"


def test_dropping_the_planted_supersession_turns_the_d8_gate_red(
        monkeypatch, tmp_path, prereg):
    """The D8 gate is not a guaranteed green: with the supersession lost, the
    retired claim reaches the reader as a live claim (or, failing closed, is
    never observed) — either way the promise is violated."""
    _install_reader(monkeypatch)
    raw = ask_sealed.get_sealed_run(
        sdk_factory=_sdk_factory(tmp_path), stub_reader=True,
        mutate=lambda sdk: sdk._get_proj().g.query(
            "MATCH (a:Point)-[r:CORRECTS]->(b:Point) DELETE r "
            "WITH b SET b.status = 'live', b.validTo = null, "
            "b.expiredAt = null"))
    scored = ask_sealed.score_with_gold(raw, judge=MockJudge())
    evaluation = ask_sealed.evaluate(scored, prereg=prereg)

    d8 = evaluation["gates"]["d8_superseded_live"]
    assert d8["ok"] is False, d8
    assert d8["rendered_live"] > 0 or d8["seen"] < d8["n"], d8
    assert evaluation["verdict"] == "regression"
    assert evaluation["failure_origin"] == "gate_regression"


# ── 3. "fixed in advance" is a git check, not a claim ────────────────────

def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout


def test_preregistration_must_precede_the_run(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "lane@example.invalid")
    _git(repo, "config", "user.name", "lane")
    prereg_rel = "preregistration.json"
    (repo / prereg_rel).write_text('{"rule": "fixed"}\n', encoding="utf-8")
    _git(repo, "add", prereg_rel)
    _git(repo, "commit", "-qm", "preregister")
    prereg_commit = _git(repo, "rev-parse", "HEAD").strip()

    (repo / "run.json").write_text("{}\n", encoding="utf-8")
    _git(repo, "add", "run.json")
    _git(repo, "commit", "-qm", "run")
    run_commit = _git(repo, "rev-parse", "HEAD").strip()

    ok = ask_sealed.verify_preregistration_history(
        prereg_rel, prereg_commit, run_commit, repo=repo)
    assert ok["ok"], ok
    assert ok["ancestry_ok"] and ok["unchanged_ok"]

    # A run in the SAME commit fixed nothing in advance.
    same = ask_sealed.verify_preregistration_history(
        prereg_rel, prereg_commit, prereg_commit, repo=repo)
    assert same["ok"] is False
    assert any("SAME commit" in issue for issue in same["issues"])

    # Editing the rule in the run commit defeats the discipline even though the
    # pre-registration is still an ancestor.
    (repo / prereg_rel).write_text('{"rule": "chosen after the numbers"}\n',
                                   encoding="utf-8")
    _git(repo, "add", prereg_rel)
    _git(repo, "commit", "-qm", "run + retuned bar")
    retuned = _git(repo, "rev-parse", "HEAD").strip()
    bad = ask_sealed.verify_preregistration_history(
        prereg_rel, prereg_commit, retuned, repo=repo)
    assert bad["ok"] is False
    assert bad["ancestry_ok"] is True and bad["unchanged_ok"] is False
    assert any("CHANGED" in issue for issue in bad["issues"])

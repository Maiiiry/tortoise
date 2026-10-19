"""#9095 — the ask spot-check must be able to OBSERVE a supersession.

Measured defect (pre-fix): ``tools/ask_spotcheck.py`` seeded **raw turns
only**. ``git grep -n "supersed\\|distilled\\|planted" -- tools/ask_spotcheck.py``
returned NO match, so the epic's D7 measurement ("does the reader commit the
current state?") and the D8 product gate ("0% of superseded points appear as
live answers" — ``docs/product-success-eval.md:132``) had **no instrument at
all**. The defect is the MISSING INSTRUMENT, not a wrong answer from the
reader, and a missing instrument reads green.

What these tests pin, in order:

1. the authored corpus is really authored (planted old→new pairs, dated, and
   each distilled claim DISTINCT from the raw turn it was distilled from — the
   property that makes the D8 check unambiguous, see 4);
2. ``seed_distilled_corpus`` writes the CAPTURE EXTRACTION shape — distilled
   statement Points through ``create_point`` (+ the ``CONTAINS`` provenance
   the shipping read resolves identity from) and the supersession through the
   product's own ``supersede_point``;
3. the seeding is ADDITIVE: the raw turn store is byte-identical with and
   without it, so the 21-question composition keeps its number;
4. ``superseded_live_in_evidence`` counts a superseded claim RENDERED AS ITS
   OWN BLOCK BODY and unflagged — and is immune to the ``[SUPERSEDES: …]``
   snippet trap on the SUCCESSOR's block, which contains the old content in
   its PREFIX (a naive ``old in evidence`` check is a guaranteed green);
5. end-to-end both ARMS from one run: the raw arm has no supersession to
   render, the distilled arm renders the old claim WITH ``[SUPERSEDED BY:``;
6. the machine-readable ``DISTILLED_JSON`` line exists with the two required
   blocks, and the gate FAILS CLOSED when a superseded claim renders live or
   when the case was never observed AND could not be retrieved (a case that
   was retrieved but not served was EXCLUDED — D8's own second compliant
   option, reported as ``excluded: true`` and passing);
7. no retry-on-the-asserted-value helper stands in for that measurement: the
   retrievability probe records, per case and arm, whether the claim was in
   the lane's own pool, so a partial pool reads as a measured retrieval GAP
   (a failure) instead of a vacuous pass — and both the exclusion pass and the
   gap failure are pinned by mutation.

Assertions are on the RESOLVED graph and on the reader evidence the product
assembles — never a grep of source text.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.ask_spotcheck import (  # noqa: E402, RUF100
    DISTILLED_FIXTURE,
    _distilled_hard_failures,
    _load_composition,
    _seed_memory,
    load_distilled_cases,
    main,
    seed_distilled_corpus,
    superseded_claim_retrievable,
    superseded_live_in_evidence,
)
from tortoise.ask_lane import (  # noqa: E402, RUF100
    _reset_ask_reader_cache_for_tests,
)
from tortoise.sdk import TortoiseSDK  # noqa: E402, RUF100

CASES = load_distilled_cases()


class _EchoReader:
    """A reader that returns the evidence it was given — the MockReader shape
    the judge's docstring describes, so ``current_state`` is decided by WHAT
    THE READER SAW rather than by a hard-coded string. That is what makes the
    two arms comparable: the same reader, the same judge, one run."""

    last_completion_tokens = 12

    def complete(self, *, system: str, user: str) -> str:
        del system
        return user

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _clean_ask_state(monkeypatch):
    # The ambient shell may carry TORTOISE_API_URL (fleet env) — the local
    # lane is the surface under test here.
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    _reset_ask_reader_cache_for_tests()
    yield
    _reset_ask_reader_cache_for_tests()


@pytest.fixture
def echo_reader(monkeypatch):
    import tortoise.ask_lane as ask_lane

    reader = _EchoReader()
    monkeypatch.setattr(ask_lane, "_default_ask_reader_factory",
                        lambda: reader)
    return reader


def _case(case_id: str) -> dict:
    for case in CASES:
        if case["question_id"] == case_id:
            return case
    raise AssertionError(f"no planted case {case_id!r} in {DISTILLED_FIXTURE}")


# ── 1. The corpus is authored, planted and unambiguous ────────────────────

def test_corpus_is_three_planted_knowledge_update_cases():
    """Three hand-planted cases (the standing method: run it small and by hand
    first), each a knowledge-update pair with a DATED old→new transition, and
    each distilled claim DISTINCT from the raw turn it was distilled from.

    That last property is load-bearing, not cosmetic: distillation is not
    transcription, and it is what lets
    ``superseded_live_in_evidence`` tell the SUPERSEDED CLAIM's block apart
    from the raw turn that carries the same fact (case 4 pins the trap).
    """
    assert len(CASES) == 3, [c["question_id"] for c in CASES]
    assert len({c["question_id"] for c in CASES}) == 3
    for case in CASES:
        spec = case["distilled"]
        assert case["question_type"] == "knowledge-update", case["question_id"]
        assert spec["old"]["valid_from"] < spec["new"]["valid_from"], (
            case["question_id"])
        # the gold IS the current state — the new value, not the old one
        assert spec["new"]["content"].lower().count(
            case["answer"].lower()) == 1, (case["question_id"], case["answer"])
        assert case["answer"].lower() not in spec["old"]["content"].lower()
        # sessions are aligned (the seeder resolves session_index into them)
        assert len(case["haystack_session_ids"]) == len(
            case["haystack_sessions"]) >= 2
        assert len(case["haystack_dates"]) == len(case["haystack_sessions"])
        for role in ("old", "new"):
            claim = spec[role]
            assert 0 <= claim["session_index"] < len(case["haystack_sessions"])
            turns = case["haystack_sessions"][claim["session_index"]]
            assert 0 <= claim["turn_index"] < len(turns)
            # the quote IS the source turn it claims
            assert claim["quote"] in turns[claim["turn_index"]]["content"]
            # ... and the distilled claim is NOT a copy of any turn's text
            for session in case["haystack_sessions"]:
                for turn in session:
                    assert claim["content"].lower() not in (
                        turn["content"].lower()), (
                        "a distilled claim must not be verbatim-identical to a "
                        "raw turn — the D8 check could not tell the two blocks "
                        f"apart ({case['question_id']}/{role})")


# ── 2. The seeder writes the capture EXTRACTION shape ─────────────────────

def test_seed_distilled_corpus_writes_the_extraction_shape(tmp_path):
    """Distilled claims land as the extraction writer writes them: a live
    ``statement`` Point (NOT episodic), carrying its ``validFrom`` window and
    quoted source, wired to its ``:Session`` by the ``CONTAINS`` edge the
    shipping read resolves identity from — and the supersession goes through
    the product's own ``supersede_point`` (CORRECTS + terminal status +
    contiguous window), never a hand-written status flag.
    """
    case = _case(CASES[0]["question_id"])
    spec = case["distilled"]
    sdk = TortoiseSDK(str(tmp_path / "distilled.db"))
    try:
        _seed_memory(sdk, case)
        ids = seed_distilled_corpus(sdk, case)
        proj = sdk._get_proj()

        claims = proj.g.query(
            "MATCH (p:Point) WHERE p.pointKind = 'statement' "
            "RETURN p.id, p.content, p.is_episodic, p.validFrom, "
            "       p.source_turn_id, p.quote ORDER BY p.id").result_set
        assert len(claims) == 2, claims
        by_id = {row[0]: row for row in claims}
        for role in ("old", "new"):
            row = by_id[ids[role]]
            assert row[1] == spec[role]["content"], row
            assert row[2] is False, ("a distilled claim is NOT episodic", row)
            assert row[3] == spec[role]["valid_from"], row
            assert row[4] == spec[role]["turn_index"], row
            assert row[5] == spec[role]["quote"], row

        # CONTAINS provenance to the claim's OWN source session
        wired = proj.g.query(
            "MATCH (s:Session)-[:CONTAINS]->(p:Point) "
            "WHERE p.pointKind = 'statement' RETURN p.id, s.id").result_set
        expect = {
            ids["old"]: case["haystack_session_ids"][
                spec["old"]["session_index"]],
            ids["new"]: case["haystack_session_ids"][
                spec["new"]["session_index"]],
        }
        assert dict((r[0], r[1]) for r in wired) == expect, wired

        # extractedFrom materialises the :Source each claim came from.
        # NOTE: ``supersede_point`` TRANSFERS the predecessor's edges to the
        # successor (its own contract: "ALL edges transferred from old to
        # new"), so after the supersession BOTH sources hang off the NEW claim
        # and the old keeps none. Asserted as the product's real shape rather
        # than the harness's expectation — the harness must not invent a
        # provenance layout the product does not write.
        sources = proj.g.query(
            "MATCH (p:Point)-[:extractedFrom]->(src) "
            "WHERE p.pointKind = 'statement' RETURN p.id, src.url").result_set
        per_point: dict[str, set[str]] = {}
        for pid, url in sources:
            per_point.setdefault(pid, set()).add(url)
        assert per_point == {
            ids["new"]: {f"session:{expect[ids['old']]}",
                         f"session:{expect[ids['new']]}"},
        }, per_point

        # the SUPERSESSION is the product's own: CORRECTS edge (successor →
        # predecessor, the direction ``supersede_point`` writes) + terminal old
        corrected = proj.g.query(
            "MATCH (a:Point {id:$a})-[:CORRECTS]->(b:Point {id:$b}) "
            "RETURN count(*)", params={"a": ids["new"], "b": ids["old"]},
        ).result_set
        assert corrected[0][0] == 1, corrected

        old = sdk.get_point(ids["old"])
        new = sdk.get_point(ids["new"])
        assert old["status"] == "superseded", old["status"]
        assert new["status"] == "live", new["status"]
        # contiguous bitemporal window: the old fact stops being true exactly
        # when the new one became true
        assert old["validTo"] == new["validFrom"] == spec["new"]["valid_from"]
        assert old["expiredAt"], old
    finally:
        sdk.close()


# ── 3. The seeding is ADDITIVE — the raw store does not move ──────────────

def test_seeding_is_additive_the_raw_turn_store_is_unchanged(tmp_path):
    """Target (1): the 21-question composition's store must not move. The
    distilled leg is a SIBLING seeder; ``_seed_memory``'s raw leg is not
    touched, and the only difference in the graph is the two claims (+ their
    provenance edges and the supersession).
    """
    case = _case(CASES[0]["question_id"])

    raw_sdk = TortoiseSDK(str(tmp_path / "raw.db"))
    try:
        _seed_memory(raw_sdk, case)
        both = TortoiseSDK(str(tmp_path / "both.db"))
        try:
            _seed_memory(both, case)
            seed_distilled_corpus(both, case)

            turns_q = ("MATCH (p:Point) WHERE p.pointKind = 'event' "
                       "RETURN p.id, p.content, p.is_episodic, p.speaker, "
                       "       p.createdAt ORDER BY p.id")
            raw_turns = raw_sdk._get_proj().g.query(turns_q).result_set
            both_turns = both._get_proj().g.query(turns_q).result_set
            assert raw_turns, "precondition: the raw leg must seed turns"
            assert both_turns == raw_turns, (
                "the distilled seeder MOVED the raw turn store", both_turns)

            raw_sessions = raw_sdk._get_proj().g.query(
                "MATCH (s:Session) RETURN s.id, s.created_at ORDER BY s.id"
            ).result_set
            both_sessions = both._get_proj().g.query(
                "MATCH (s:Session) RETURN s.id, s.created_at ORDER BY s.id"
            ).result_set
            assert both_sessions == raw_sessions, both_sessions

            raw_edges = raw_sdk._get_proj().g.query(
                "MATCH (:Session)-[r:CONTAINS]->(:Point) RETURN count(*)"
            ).result_set[0][0]
            both_edges = both._get_proj().g.query(
                "MATCH (:Session)-[r:CONTAINS]->(:Point) RETURN count(*)"
            ).result_set[0][0]
            assert both_edges - raw_edges == 2, (raw_edges, both_edges)
        finally:
            both.close()
    finally:
        raw_sdk.close()


# ── 4. The D8 check: body, not substring — and the snippet trap ───────────

def test_superseded_live_reads_the_block_body_and_survives_the_snippet_trap():
    """The check is the D8 product gate, so its edges are the test.

    * a FLAGGED old block (``[SUPERSEDED BY: …]``) → rendered_live 0;
    * an UNFLAGGED old block → rendered_live 1 (the violation);
    * the SUCCESSOR's block carries ``[SUPERSEDES: <old content>]`` — the old
      content sits in the PREFIX and must NOT count, or the gate is a
      guaranteed green on exactly the case it exists to catch;
    * nothing observed at all → ``rate`` is None, never 0.0 (an unmeasured
      gate must not read as a pass).
    """
    old = "The user lives in Chicago."
    new = "The user lives in the suburbs."
    case = {"distilled": {"old": {"content": old},
                          "new": {"content": new}}}
    tagged = "[session s1] (session date 2022-11-05) "
    successor = ("[session s2] (session date 2023-04-18) "
                 f"[SUPERSEDES: {old}] [valid since 2023-04-18] {new}")

    flagged_old = (f"{tagged}[SUPERSEDED BY: {new}] "
                   "[valid 2022-11-05 → 2023-04-18] " + old)
    # (a) flagged old + the successor's snippet-carrying block
    got = superseded_live_in_evidence(
        "\n\n".join([flagged_old, successor]), case)
    assert got == {"seen": 1, "rendered_live": 0, "rate": 0.0}, got

    # (b) the SAME old claim served with no marker = the D8 violation
    live_old = f"{tagged}{old}"
    got = superseded_live_in_evidence(
        "\n\n".join([live_old, successor]), case)
    assert got == {"seen": 1, "rendered_live": 1, "rate": 1.0}, got

    # (c) THE SNIPPET TRAP — only the successor's block is present. The old
    # content IS in the evidence (inside the prefix); it is not RENDERED, so
    # the case was never observed and the rate is unmeasured — not 0%.
    got = superseded_live_in_evidence(successor, case)
    assert got == {"seen": 0, "rendered_live": 0, "rate": None}, got

    # (d) both blocks live — mixed evidence counts both
    got = superseded_live_in_evidence(
        "\n\n".join([live_old, flagged_old, successor]), case)
    assert got == {"seen": 2, "rendered_live": 1, "rate": 0.5}, got


def test_hard_failures_fail_closed_on_a_live_block_or_a_retrieval_gap():
    """``rendered_live > 0`` is a hard failure of the run (the D8 gate); so is
    a case the instrument never observed **and could not retrieve**.

    D8 says "must be flagged or **excluded**", so ``seen == 0`` splits on the
    retrievability probe, and the two branches must NOT be conflated:

    * ``retrievable is True`` ⇒ EXCLUDED ⇒ a reported PASS (pre-fix this was
      a false red on the product's own best-case behaviour);
    * ``retrievable is False`` ⇒ RETRIEVAL GAP ⇒ hard failure (unmeasured is
      never a pass);
    * ``retrievable is None`` ⇒ the probe could not read the store ⇒ hard
      failure, fail CLOSED (an unreadable probe is not evidence of a pass).

    The KU ``current_state`` rate has NO threshold here (D8: the first run
    sets it), so it is never a hard failure.
    """
    clean = {"cases": [{
        "question_id": "ku-a",
        "distilled": {"current_state": {"committed": False, "n": 1,
                                        "rate": 0.0},
                      "superseded_live": {"seen": 1, "rendered_live": 0,
                                          "rate": 0.0, "retrievable": True,
                                          "excluded": False}}}]}
    assert _distilled_hard_failures(clean) == []

    live = json.loads(json.dumps(clean))
    live["cases"][0]["distilled"]["superseded_live"] = {
        "seen": 1, "rendered_live": 1, "rate": 1.0, "retrievable": True,
        "excluded": False}
    fails = _distilled_hard_failures(live)
    assert fails and "ku-a" in fails[0] and "live" in fails[0], fails

    # (a) retrievable but never served = the D8 "excluded" PASS, reported.
    excluded = json.loads(json.dumps(clean))
    excluded["cases"][0]["distilled"]["superseded_live"] = {
        "seen": 0, "rendered_live": 0, "rate": None,
        "retrievable": True, "excluded": True}
    assert _distilled_hard_failures(excluded) == []

    # (b) the claim never came out of the store = a RETRIEVAL gap, not a pass.
    gap = json.loads(json.dumps(clean))
    gap["cases"][0]["distilled"]["superseded_live"] = {
        "seen": 0, "rendered_live": 0, "rate": None,
        "retrievable": False, "excluded": False}
    fails = _distilled_hard_failures(gap)
    assert fails and "ku-a" in fails[0] and "GAP" in fails[0], fails
    assert "observ" in fails[0] or "RETRIEV" in fails[0], fails

    # (c) an unreadable probe is UNKNOWN — fail closed, never a green.
    unread = json.loads(json.dumps(clean))
    unread["cases"][0]["distilled"]["superseded_live"] = {
        "seen": 0, "rendered_live": 0, "rate": None,
        "retrievable": None, "excluded": False}
    fails = _distilled_hard_failures(unread)
    assert fails and "UNMEASURED" in fails[0], fails


# ── 5. End-to-end: both arms, one run, one judge ───────────────────────────

def test_both_arms_from_one_run(echo_reader):
    """D4's paired read: the SAME case, the SAME judge, one invocation, two
    arms. The raw arm has no supersession to render (nothing in the raw turn
    store records one); the distilled arm renders the old claim WITH the
    product's own ``[SUPERSEDED BY:`` marker, so the D8 gate can finally be
    READ — and the reader's answer to the knowledge-update question is
    decided by what it saw, not by the harness.
    """
    from tools.ask_spotcheck import run_distilled_case
    from tools.longmem_eval.judge import MockJudge

    case = _case(CASES[0]["question_id"])
    judge = MockJudge()
    rec = run_distilled_case(case, judge)
    assert rec["question_id"] == case["question_id"]
    assert set(rec) >= {"question_id", "raw", "distilled"}, rec

    # RAW ARM — the store holds turns only, so there is nothing to flag,
    # nothing that carries the current state, and nothing to probe.
    assert rec["raw"]["superseded_live"] == {
        "seen": 0, "rendered_live": 0, "rate": None,
        "retrievable": None, "excluded": False}, rec["raw"]
    assert rec["raw"]["current_state"]["committed"] is False, rec["raw"]

    # DISTILLED ARM — the old claim IS served (seen >= 1) and IS flagged.
    assert rec["distilled"]["superseded_live"]["seen"] >= 1, rec["distilled"]
    assert rec["distilled"]["superseded_live"]["rendered_live"] == 0, (
        rec["distilled"])
    assert "[SUPERSEDED BY:" in rec["distilled"]["evidence"]
    # The instrument MEASURED the claim's presence in the lane's own pool, so
    # "seen >= 1" above is an observation, not a lucky single shot: this is
    # what replaces the retry-on-the-asserted-value helper the file used to
    # carry (a partial pool reads as a retrieval GAP and fails the gate, never
    # as a vacuous pass).
    assert rec["distilled"]["superseded_live"]["retrievable"] is True, (
        rec["distilled"])
    assert rec["distilled"]["superseded_live"]["excluded"] is False, (
        rec["distilled"])
    # ... and the reader, seeing the distilled current state, commits it.
    assert rec["distilled"]["current_state"] == {
        "committed": True, "n": 1, "rate": 1.0}, rec["distilled"]


def test_unmarked_supersession_is_a_hard_failure(echo_reader, monkeypatch):
    """MUTATION PROOF of the leg's RED half: if the supersession is never
    written, the old claim IS served unflagged — and the gate must say so.

    Without this, ``superseded_live == 0`` could be a guaranteed green: the
    old claim might simply never have reached the reader. Patching
    ``supersede_point`` into a no-op is the minimal mutation (the planting,
    the distilled claims and the reader are all unchanged), and the failure
    must name the live render.
    """
    from tools.ask_spotcheck import (
        _distilled_hard_failures,
        _distilled_payload,
        run_distilled_case,
    )
    from tools.longmem_eval.judge import MockJudge

    monkeypatch.setattr(TortoiseSDK, "supersede_point",
                        lambda self, old_id, new_id, **kw: {"invalidated": True})
    rec = run_distilled_case(_case(CASES[0]["question_id"]), MockJudge())
    assert rec["distilled"]["superseded_live"] == {
        "seen": 1, "rendered_live": 1, "rate": 1.0,
        "retrievable": True, "excluded": False}, rec["distilled"]
    assert "[SUPERSEDED BY:" not in rec["distilled"]["evidence"]
    failures = _distilled_hard_failures(_distilled_payload([rec]))
    assert failures and CASES[0]["question_id"] in failures[0], failures
    assert "rendered live" in failures[0], failures


# ── 6. D8's "flagged OR EXCLUDED": exclusion is a pass, a gap is not ────

def test_retrievability_probe_reads_the_lane_pool_and_discriminates(tmp_path):
    """The probe behind the exclusion split must ANSWER, not always say yes:
    the superseded claim's own id is in the lane's ``include_terminal=True``
    pool, an id that is not in the store is not, and a missing id is UNKNOWN
    (``None`` — the caller fails closed on it). A probe that returned ``True``
    unconditionally would turn every retrieval gap into a silent pass.
    """
    from tools.ask_spotcheck import _to_iso_date

    case = _case(CASES[0]["question_id"])
    sdk = TortoiseSDK(str(tmp_path / "probe.db"))
    try:
        _seed_memory(sdk, case)
        ids = seed_distilled_corpus(sdk, case)
        qdate = _to_iso_date(case["question_date"])
        # a TERMINAL point is IN scope for the probe — the lane's own
        # retrieval is called with include_terminal=True, so a filtered probe
        # would report every supersession as a retrieval gap
        assert superseded_claim_retrievable(sdk, case, ids["old"], qdate) is True
        assert superseded_claim_retrievable(sdk, case, ids["new"], qdate) is True
        assert superseded_claim_retrievable(
            sdk, case, "point-not-in-this-store", qdate) is False
        assert superseded_claim_retrievable(sdk, case, None, qdate) is None
    finally:
        sdk.close()


def test_an_excluded_supersession_is_a_reported_pass(echo_reader, monkeypatch):
    """The P2 this fixes: D8's own second option — "must be flagged or
    EXCLUDED" — used to read as a hard failure.

    Mutation: the assembly drops terminal hits (the shape a future/lane-local
    change that excludes superseded points would have). The store still holds
    the claim and the lane's retrieval still returns it, so the only reason it
    never appears in the evidence is that it was EXCLUDED. The instrument must
    report that as its own field and PASS — not call it an unmeasured gate.
    """
    import tortoise.retrieval as retrieval
    from tools.ask_spotcheck import _distilled_payload, run_distilled_case
    from tools.longmem_eval.judge import MockJudge

    original = retrieval.assemble_context

    def _exclude_terminal(hits, *args, **kwargs):
        return original([h for h in hits
                         if (h.get("status") or "live") != "superseded"],
                        *args, **kwargs)

    monkeypatch.setattr(retrieval, "assemble_context", _exclude_terminal)
    rec = run_distilled_case(_case(CASES[0]["question_id"]), MockJudge())
    sl = rec["distilled"]["superseded_live"]
    assert sl["seen"] == 0, rec["distilled"]
    assert "[SUPERSEDED BY:" not in rec["distilled"]["evidence"]
    # ... the claim WAS in the pool: retrievable, hence EXCLUDED, hence pass.
    assert sl["retrievable"] is True, sl
    assert sl["excluded"] is True, sl
    payload = _distilled_payload([rec])
    assert _distilled_hard_failures(payload) == [], payload
    assert payload["arms"]["distilled"]["superseded_live"]["excluded"] == 1


def test_a_retrieval_gap_still_fails_closed(echo_reader, monkeypatch):
    """The other half of the split: when the claim never comes out of the
    store, ``seen == 0`` is still the fail-closed RETRIEVAL gap — the exclusion
    fix must not turn an unmeasured gate into a green.

    Mutation: the retrieval never returns terminal hits at all (an index/gap
    in the store, the way an unfiltered probe cannot see the claim either), so
    the claim is unobservable AND unretrievable.
    """
    from tools.ask_spotcheck import _distilled_payload, run_distilled_case
    from tools.longmem_eval.judge import MockJudge

    original = TortoiseSDK.tortoise_fts_query

    def _live_only(self, *args, **kwargs):
        kwargs["include_terminal"] = False
        return original(self, *args, **kwargs)

    monkeypatch.setattr(TortoiseSDK, "tortoise_fts_query", _live_only)
    rec = run_distilled_case(_case(CASES[0]["question_id"]), MockJudge())
    sl = rec["distilled"]["superseded_live"]
    assert sl["seen"] == 0, rec["distilled"]
    assert sl["retrievable"] is False, sl
    assert sl["excluded"] is False, sl
    failures = _distilled_hard_failures(_distilled_payload([rec]))
    assert failures and "GAP" in failures[0], failures


# ── 7. The machine-readable line, and the composition leg untouched ───────

def test_distilled_leg_emits_one_json_line_and_exits_clean(monkeypatch,
                                                          capsys,
                                                          echo_reader):
    monkeypatch.setenv("TORTOISE_SPOTCHECK_JUDGE", "mock")
    rc = main(["--distilled"])
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.startswith("DISTILLED_JSON ")]
    assert len(lines) == 1, out
    payload = json.loads(lines[0][len("DISTILLED_JSON "):])
    assert set(payload) >= {"cases", "arms", "hard_failures"}, payload
    for arm in ("raw", "distilled"):
        assert set(payload["arms"][arm]) == {"current_state", "superseded_live"}
        assert set(payload["arms"][arm]["current_state"]) == {
            "committed", "n", "rate"}
        assert set(payload["arms"][arm]["superseded_live"]) == {
            "seen", "rendered_live", "rate", "excluded"}
    assert payload["arms"]["distilled"]["superseded_live"]["rendered_live"] == 0
    assert payload["arms"]["distilled"]["superseded_live"]["excluded"] == 0
    assert payload["arms"]["distilled"]["current_state"]["n"] == len(CASES)
    # every case is REPORTED as retrievable, so "rendered_live == 0" is a
    # measured flag, not an absence of evidence
    assert all(c["distilled"]["superseded_live"]["retrievable"] is True
               for c in payload["cases"]), payload["cases"]
    assert rc == 0, (rc, out)


def test_composition_leg_still_fails_fast_without_a_judge_key(monkeypatch,
                                                              capsys):
    """#2071's fail-fast is NOT weakened by the distilled leg: without
    ``--distilled`` (and any judge key) the tool still refuses before grading,
    and the composition fixture is untouched by this issue.
    """
    for key in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY",
                "GEMINI_API_KEY", "VENICE_API_KEY", "TORTOISE_LME_JUDGE_MODEL",
                "TORTOISE_SPOTCHECK_JUDGE"):
        monkeypatch.delenv(key, raising=False)
    rc = main([])
    captured = capsys.readouterr()
    assert rc == 2, captured
    assert "DISTILLED_JSON" not in captured.out
    assert "judge" in captured.err.lower()

    composition = _load_composition()
    assert len(composition) == 21, len(composition)
    distilled_ids = {c["question_id"] for c in CASES}
    assert not distilled_ids & {q.get("question_id") for q in composition}


def test_distilled_leg_without_a_judge_key_fails_fast(monkeypatch, capsys):
    """The distilled leg is not key-free by accident: only an EXPLICIT
    ``TORTOISE_SPOTCHECK_JUDGE=mock`` opts out of the judge-key rule
    (#2071's fail-fast), and a missing key names the prerequisite."""
    for key in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY",
                "GEMINI_API_KEY", "VENICE_API_KEY", "TORTOISE_LME_JUDGE_MODEL",
                "TORTOISE_SPOTCHECK_JUDGE"):
        monkeypatch.delenv(key, raising=False)
    rc = main(["--distilled"])
    captured = capsys.readouterr()
    assert rc == 2, captured
    assert "DISTILLED_JSON" not in captured.out
    assert "judge" in captured.err.lower(), captured.err

"""#5006 — near-duplicate MERGE (D12/O4): the FUSION, and its REFUSALS.

WHAT THIS PINS
--------------
The design of record (`EXTRACTOR-V4-ARCHITECTURE.md` §4.2) splits the
near-duplicate concern across three sites and says so explicitly — *"so no
operation has two homes"*:

    DETECT  S2.2a-β  mechanical   `extractor_v2._boundary`        (#5080)
    VERDICT S2.2b    the arbiter  `vet_gate.OUTCOMES``MERGE`      (#5005)
    FUSION  S3       this test    `tortoise/near_dup_merge.py`    (#5006)

Before this change the third site had no implementation and the verdict had no
consumer, which is why `config/v4-mechanisms.yml` recorded `near_dup_merge` as
`state: not-implemented, tracking_issue: 5006`.

TWO ARMS, AND THE SECOND IS THE ONE THAT MATTERS
------------------------------------------------
1. **The merge arm** — a flagged, near-identical pair lands as ONE entity
   carrying the UNION of both sides' attachments, and the losing document's
   evidence is still reachable (its quote on the merge record, its session on
   the survivor). *Mutant it catches: a fusion that keeps the prior and DROPS
   the candidate's attachments — which is the de-facto merge #5006 says the
   `NOOP_MIN_OVERLAP` band was already performing by accident.*
2. **The refusal arm** — the same pair is NOT merged when the verdict is
   absent, when a value or identity difference is present, when the overlap is
   below the ~0.95 bar, or when the union cannot be computed. *Mutant it
   catches: a merge that fires on the mechanical gate alone (no arbiter), or
   one that fuses across a differing number — the §16.4 never-across list, and
   the outcome the ruling says costs more than a kept near-duplicate.*

⚠️ **The `0.95` bar is deliberately far above the NOOP band (`0.45`).** The
refusal these mechanisms differ on is narrow on purpose: `#9424` made the
paraphrase band REFUSE a fold that would drop an entity, and this mechanism is
the authorised alternative for that case only when the pair is near-IDENTICAL.
A mid-band paraphrase pair still falls through to `ADD` (both claims survive),
and `test_a_mid_band_paraphrase_is_still_not_merged` pins that boundary.

A THIRD PROPERTY, PINNED BECAUSE IT IS THE ONE THE FUSION SILENTLY LOSES
-----------------------------------------------------------------------
The union is COMPARED case-folded and WRITTEN case-preserved. `Object.name` is
stored verbatim (`projection/entities.py`, and capture's own attachment pass
resolves with `name.strip()` at `sdk.py:12123`), so a union emitted in the
lowered form resolves NOTHING on ordinary data: the `MATCH (o:Object {name:$n})`
returns no rows, the candidate's exclusive attachment lands in `unresolved`, and
the record still claims a union that never reached the graph. Every graph-level
fixture here is therefore CAPITALISED, which is the shape real data carries —
lowercase names make the defect an identity operation and hide it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import extractor_v2 as v2  # noqa: E402, RUF100
from tortoise import near_dup_merge as ndm  # noqa: E402, RUF100
from tortoise import vet_gate  # noqa: E402, RUF100

#: ONE claim, two documents. The prior names `team`; the second document names
#: `team AND office`. Identical wording is the near-identical case the bar
#: admits — the #9424 guard refuses it as a fold (the candidate's `office` link
#: would be dropped), and the fusion is the authorised alternative.
PRIOR_CONTENT = "the team meets weekly in main office"
CANDIDATE_CONTENT = "the team meets weekly in main office"


def _prior(**kw) -> dict:
    row = {"id": "pt_prior", "content": PRIOR_CONTENT,
           "about_entities": ["team"]}
    row.update(kw)
    return row


def _candidate(**kw) -> dict:
    row = {"content": CANDIDATE_CONTENT, "about_entities": ["team", "office"]}
    row.update(kw)
    return row


def _classify(prior: dict, candidate: dict, *, flagged: bool):
    return v2.classify_consolidation(
        candidate, [prior], entity_mentions=["team"], merge_flagged=flagged)


# ── 0. The vocabulary is shared, not respelled ─────────────────────────────


def test_the_verdict_token_matches_vet_gate():
    """`near_dup_merge` CONSUMES the S2.2b vocabulary; it does not own it.

    The constant is duplicated (this module must stay importable without
    `vet_gate`) so the drift is possible — this is the pin that makes it loud
    instead of silent.
    """
    assert ndm.MERGE == vet_gate.MERGE
    assert ndm.MERGE in vet_gate.OUTCOMES


def test_the_bar_is_the_evidence_driven_one():
    """§16.4: *"Practical thresholds cluster at ~0.95"* — and the direction of
    error is asymmetric. Lowering this is a DECISION (it re-opens "merging can
    lose information"), not a tuning knob, so the value is pinned."""
    assert ndm.MERGE_MIN_OVERLAP == 0.95
    assert ndm.MERGE_MIN_OVERLAP > v2.NOOP_MIN_OVERLAP


# ── 1. THE MERGE ARM ───────────────────────────────────────────────────────


class TestThePlanMergesANearIdenticalFlaggedPair:
    def test_union_of_both_attachment_sets(self):
        plan = ndm.near_dup_merge(
            _candidate(), _prior(), overlap=1.0, difference=None,
            identity=frozenset(), flagged=True)
        assert plan.merge is True, plan.evidence
        assert plan.prior_id == "pt_prior"
        assert plan.union_entities == ("office", "team")
        assert plan.added_entities == ("office",)
        assert plan.reason == "near_identical"

    def test_the_union_carries_the_spelling_the_graph_stores(self):
        """COMPARISON is case-folded; the WRITE is not.

        `Object.name` is stored case-preserved (`projection/entities.py` MERGEs
        on the verbatim name, capture's own attachment pass resolves with
        `name.strip()` — `sdk.py:12123`). A union carrying the lowered form is
        a union that resolves NOTHING on ordinary data, so the candidate's
        exclusive attachment is dropped while the record claims it landed.
        *Mutant it catches: `_entity_names` returning the normalised form as
        the emitted name — a one-line change that turns every real merge into
        an `unresolved` no-op.*
        """
        plan = ndm.near_dup_merge(
            _candidate(about_entities=["Team", "Office"]),
            _prior(about_entities=["Team"]),
            overlap=1.0, difference=None, identity=frozenset(), flagged=True)
        assert plan.merge is True, plan.evidence
        assert plan.union_entities == ("Office", "Team")
        assert plan.added_entities == ("Office",)

    def test_whitespace_is_stripped_and_case_is_only_identity(self):
        """The two halves of `_entity_names`, each doing its own job.

        Whitespace is presentation and is stripped; case is identity for the
        COMPARISON (a `Team` the prior already carries adds nothing, so the
        pair is NOOP's) but must survive into the spelling that gets written.
        """
        plan = ndm.near_dup_merge(
            _candidate(about_entities=["Team", " OFFICE "]), _prior(),
            overlap=1.0, difference=None, identity=frozenset(), flagged=True)
        assert plan.merge is True, plan.evidence
        # `Team` is the prior's own entity in another case -> not "added";
        # `OFFICE` is new, and it is emitted without its padding.
        assert plan.added_entities == ("OFFICE",)
        assert plan.union_entities == ("OFFICE", "team")

        # …and the prior's spelling wins for a name both sides carry.
        same = ndm.near_dup_merge(
            _candidate(about_entities=["TEAM"]), _prior(),
            overlap=1.0, difference=None, identity=frozenset(), flagged=True)
        assert same.merge is False
        assert same.reason == "nothing_to_union"


class TestTheClassifierProducesMerge:
    def test_flagged_pair_merges_onto_the_prior_and_carries_the_union(self):
        d = _classify(_prior(), _candidate(), flagged=True)
        assert d.decision == "MERGE", d.evidence
        assert d.prior_id == "pt_prior"
        assert d.reason == "near_dup_merge"
        # the union is on the DECISION, because the write path needs it to
        # stamp the survivor's attachments — a MERGE without it would fuse the
        # claim and drop the evidence, which is the forbidden outcome.
        assert d.union_entities == ("office", "team")

    def test_the_claim_is_never_fused(self):
        """§S2.2: *"union the attachments, NEVER fuse the claim"*.

        The decision names the PRIOR and emits no content of its own — the
        survivor's text stays byte-identical. A `MERGE` that produced merged
        text would be `FUSE`, which O4 forbids.
        """
        d = _classify(_prior(), _candidate(), flagged=True)
        assert d.decision == "MERGE"
        assert not hasattr(d, "content")
        # the prior's text is untouched by the decision, and `execute_embed`
        # records exactly that text (pinned below in the write-path test).


class TestTheWritePathEmitsOneEntityAndKeepsTheLosersEvidence:
    """THE END-TO-END CASE: two near-duplicate documents → ONE entity."""

    def test_payload_creates_no_second_point(self):
        # ⚠️ Capitalised on purpose: the store's `Object.name` is
        # case-preserved, so a union that lowercases resolves nothing. Lowercase
        # fixture names made the case bug an identity operation and hid it.
        prior = _prior(pointKind="statement", about_entities=["Team"])
        embed = {
            "entities": [{"name": "Team", "kind": "core:team"},
                         {"name": "Office", "kind": "core:location"}],
            "points": [{"content": CANDIDATE_CONTENT,
                        "pointKind": "statement",
                        "about_entities": ["Team", "Office"],
                        "quote": CANDIDATE_CONTENT}],
            "events": [], "operators": [], "chain_notes": [],
            "link_before_create": [],
        }
        result = v2.execute_embed(
            embed, {"points": [prior], "entities": []},
            session_id="s_second_document", session_date="2026-10-24",
            merge_flagged=frozenset({v2._norm(CANDIDATE_CONTENT)}))

        # ONE entity: no payload point is emitted for the candidate at all.
        assert result["payload"]["points"] == []
        assert result["stats"]["merges"] == 1
        (record,) = result["merges"]

        # …and the survivor is the PRIOR, with the union the write path needs.
        assert record["point_id"] == "pt_prior"
        # the spelling is the GRAPH's, not a normalised form: these names are
        # what `apply_merge_union` resolves with an exact `Object {name:$n}`.
        assert record["about_entities"] == ["Office", "Team"]

        # THE LOSING DOCUMENT IS NOT SILENTLY DROPPED. Its evidence goes:
        #   1. its own quote + content survive on the merge record, so the
        #      source span that argued for the claim is still readable;
        #   2. its session is named (`session_ref`), and the write path turns
        #      that into a `(Session)-[:CONTAINS]->(Point)` edge — the losing
        #      document becomes a member of the claim's memory rather than a
        #      discarded row;
        #   3. the entity it alone named (`Office`) is in the union, so the
        #      attachment it contributed is not dropped either.
        assert record["merged_quote"] == CANDIDATE_CONTENT
        assert record["merged_content"] == CANDIDATE_CONTENT
        assert record["session_ref"] == "s_second_document"
        assert "Office" in record["about_entities"]
        # the surviving claim is the prior's, byte-identical — never fused
        assert record["content"] == PRIOR_CONTENT

    def test_an_unflagged_embed_list_emits_no_merge_record(self):
        """The default is inert — the fusion is verdict-gated, not automatic."""
        prior = _prior(pointKind="statement")
        embed = {
            "entities": [], "events": [], "operators": [], "chain_notes": [],
            "link_before_create": [],
            "points": [{"content": CANDIDATE_CONTENT,
                        "pointKind": "statement",
                        "about_entities": ["team", "office"],
                        "quote": CANDIDATE_CONTENT}],
        }
        result = v2.execute_embed(
            embed, {"points": [prior], "entities": []},
            session_id="s_second_document", session_date="2026-10-24")
        assert result["merges"] == []
        assert result["stats"]["merges"] == 0


# ── 1b. The VERDICT's route (the gap #5006 calls "no consumer yet") ────────


class TestTheVetVerdictReachesTheFusion:
    """D12/O4's three sites are only joined if the middle one is ROUTED.

    `vet_gate` records `MERGE`; `extractor_v2._merge_flagged_contents` is the
    consumer that carries it to S3. Without this, the verdict stays a note in a
    stats dict — which is exactly the state #5006 recorded as the defect.
    """

    def test_a_merge_verdict_is_routed_by_normalised_content(self):
        surface = {"decisions": {
            "points:0": {"outcome": "MERGE",
                         "text": "The Team meets weekly in main office"},
            "points:1": {"outcome": "KEEP", "text": "something else"},
        }}
        flagged = v2._merge_flagged_contents(surface)
        assert flagged == frozenset({v2._norm(PRIOR_CONTENT)})

    def test_both_vet_passes_contribute(self):
        s2 = {"decisions": {"a": {"outcome": "MERGE", "text": "alpha"}}}
        union = {"decisions": {"b": {"outcome": "merge", "text": "beta"}}}
        assert v2._merge_flagged_contents(s2, union) == frozenset(
            {v2._norm("alpha"), v2._norm("beta")})

    def test_malformed_surfaces_contribute_nothing(self):
        """Fail-closed: no readable verdict ⇒ no fusion."""
        for bad in (None, {}, {"decisions": None}, {"decisions": []},
                    {"decisions": {"a": "MERGE"}},
                    {"decisions": {"a": {"outcome": "MERGE"}}}):
            assert v2._merge_flagged_contents(bad) == frozenset(), bad


# ── 2. THE REFUSAL ARM — the mutation that matters ─────────────────────────


class TestTheSamePairIsNotMergedWithoutTheVerdict:
    """D12: the mechanical guard answers *"is anything distinguishing lost?"*.

    It cannot answer *"are these the same claim?"* — that is the arbiter's, so
    a clean mechanical pair with no `MERGE` verdict must NOT fuse. This is the
    arm that stops the mechanism from becoming "dedup by threshold" again, the
    exact accident #5006 describes.
    """

    def test_unflagged_pair_is_not_merged(self):
        d = _classify(_prior(), _candidate(), flagged=False)
        assert d.decision == "ADD", d.evidence
        assert d.union_entities == ()

    def test_the_plan_refuses_and_names_the_missing_verdict(self):
        plan = ndm.near_dup_merge(
            _candidate(), _prior(), overlap=1.0, difference=None,
            identity=frozenset(), flagged=False)
        assert plan.merge is False
        assert plan.reason == "not_flagged"


class TestTheNeverAcrossListIsAbsolute:
    """§16.4: a merge never crosses a number, a name, a negation, a condition,
    a date or a role inversion. Each case here IS a mergeable pair except for
    the one difference, so each is its own mutation test: a fusion that
    ignored the guard would merge two DISTINCT facts."""

    def test_a_differing_number_is_not_merged(self):
        # same shape, different quantity — `27:12` vs `26:40` are two facts.
        # ⚠️ It must not merely avoid MERGE: a differing value is the
        # SUPERSEDE channel (UPDATE), which keeps both nodes. A merge here
        # would silently collapse one fact into the other.
        d = v2.classify_consolidation(
            {"content": "the gym opens at 6pm", "about_entities": ["gym"]},
            [{"id": "pt_prior", "content": "the gym opens at 7pm",
              "about_entities": ["gym", "office"]}],
            entity_mentions=["gym"], merge_flagged=True)
        assert d.decision == "UPDATE", d.evidence
        assert d.union_entities == ()

    def test_a_negation_is_not_merged(self):
        d = v2.classify_consolidation(
            {"content": "the team does not meet weekly in main office",
             "about_entities": ["team"]},
            [_prior()], entity_mentions=["team"], merge_flagged=True)
        assert d.decision == "ADD", d.evidence
        assert d.union_entities == ()

    def test_the_plan_reports_the_dimension_it_refused_across(self):
        plan = ndm.near_dup_merge(
            _candidate(), _prior(), overlap=1.0, difference=None,
            identity=frozenset({"negation"}), flagged=True)
        assert plan.merge is False
        assert plan.reason == "distinguishing_difference"
        assert "negation" in plan.evidence

    def test_a_value_difference_is_not_merged(self):
        plan = ndm.near_dup_merge(
            _candidate(), _prior(), overlap=1.0, difference="number",
            identity=frozenset(), flagged=True)
        assert plan.merge is False
        assert plan.reason == "value_difference"


class TestTheBarAndTheUnionAreEnforcedMechanically:
    def test_a_mid_band_paraphrase_is_still_not_merged(self):
        """⚠️ The boundary of the mechanism, pinned.

        `#9424` refuses this fold (the candidate names `office`, the prior does
        not) and the pair is NOT near-identical, so it is NOT merge-eligible
        either — it falls through to `ADD` and BOTH claims survive. A future
        "lower the bar until the band merges" change reds here, which is the
        intent: §16.4 says a false merge costs more than a kept near-duplicate.
        """
        d = v2.classify_consolidation(
            {"content": "the team meets weekly",
             "about_entities": ["team", "office"]},
            [_prior()], entity_mentions=["team"], merge_flagged=True)
        assert d.decision == "ADD", d.evidence

    def test_below_the_bar_is_refused_by_the_plan(self):
        plan = ndm.near_dup_merge(
            _candidate(), _prior(), overlap=0.90, difference=None,
            identity=frozenset(), flagged=True)
        assert plan.merge is False
        assert plan.reason == "below_bar"

    def test_an_unreadable_attachment_set_refuses_the_merge(self):
        """FAIL-CLOSED here — the opposite of the `#9424` guard's fail-open.

        That guard only DECLINES to fold, so unknown ⇒ allow. This one WRITES:
        an unreadable set means the union cannot be computed, so there is
        nothing safe to write and both claims survive.
        """
        for bad in ({"team": 1}, None, "team"):
            plan = ndm.near_dup_merge(
                _candidate(about_entities=bad), _prior(), overlap=1.0,
                difference=None, identity=frozenset(), flagged=True)
            assert plan.merge is False, bad
            assert plan.reason == "unreadable_attachments", bad

    def test_nothing_to_union_falls_back_to_noop(self):
        """A pair the prior already covers is NOOP's — lossless and cheaper.

        Merging it would take the riskier write path for no gain, which is the
        `#9424` guard's own verdict: the fold is admissible, so it stays a fold.
        """
        plan = ndm.near_dup_merge(
            _candidate(about_entities=["team"]), _prior(), overlap=1.0,
            difference=None, identity=frozenset(), flagged=True)
        assert plan.merge is False
        assert plan.reason == "nothing_to_union"
        d = _classify(_prior(), _candidate(about_entities=["team"]),
                      flagged=True)
        assert d.decision == "NOOP", d.evidence


# ── 3. The FUSION against a real graph ─────────────────────────────────────


class TestApplyMergeUnionWritesTheUnionAndTheLostProvenance:
    """The half that makes "ONE merged entity with a preserved evidence trail"
    TRUE of the store, not just of the decision record."""

    @pytest.fixture
    def sdk(self, tmp_path):
        from tortoise.sdk import TortoiseSDK
        s = TortoiseSDK(str(tmp_path / "merge.db"))
        yield s
        s.close()

    def test_union_edges_and_the_losing_session_are_written(self, sdk):
        # ⚠️ The objects are CAPITALISED because that is the shape the graph
        # stores (`Object.name` is case-preserved) and the shape real data
        # carries. The merge record's `about_entities` is taken from a REAL
        # `near_dup_merge` plan, not hand-written, so this test walks the same
        # spelling the capture path emits: lowercase fixture names made the
        # case defect an identity operation, and a hand-written record could
        # not see it either way.
        team = sdk.create_object("Team", objectKind="core:team")
        office = sdk.create_object("Office", objectKind="core:location")
        pt = sdk.create_point("statement", PRIOR_CONTENT)
        prior_id = pt["id"]
        # The :Session node is S6's to create; the fusion only writes the edge
        # INTO it, so the test mints it directly (the same scoping the other
        # edge-class tests use).
        sdk._get_proj().g.query(
            "CREATE (s:Session {id:$sid})",
            params={"sid": "s_second_document"})

        from tortoise.near_dup_merge import apply_merge_union

        plan = ndm.near_dup_merge(
            {"content": CANDIDATE_CONTENT,
             "about_entities": ["Team", "Office"]},
            {"id": prior_id, "content": PRIOR_CONTENT,
             "about_entities": ["Team"]},
            overlap=1.0, difference=None, identity=frozenset(), flagged=True)
        assert plan.merge is True, plan.evidence

        res = apply_merge_union(
            sdk._get_proj(),
            {"point_id": prior_id,
             "about_entities": list(plan.union_entities),
             "content": PRIOR_CONTENT},
            session_id="s_second_document", sdk=sdk)

        assert res["point_id"] == prior_id
        assert res["contained"] == 1
        assert res["warnings"] == [], res["warnings"]
        # ⚠️ THE ASSERTION THAT MAKES THE SPELLING CONTRACT DISCRIMINATING:
        # every unioned name must RESOLVE. A lowered union reports them here
        # and links nothing, which is the fusion silently not happening.
        assert res["unresolved"] == [], res["unresolved"]

        # the union: the survivor now carries BOTH attachments
        rows = sdk._get_proj().g.query(
            "MATCH (p:Point {id:$pid})-[:aboutObject]->(o:Object) "
            "RETURN o.id ORDER BY o.id", params={"pid": prior_id}).result_set
        linked = {r[0] for r in rows}
        assert linked == {team["id"], office["id"]}, linked

        # …and the resolution is EXACT-match on purpose: the same name in
        # another case is not the same node, and conflating them by
        # `toLower()` would be a behaviour nothing else in the code base uses.
        mismatched = apply_merge_union(
            sdk._get_proj(),
            {"point_id": prior_id, "about_entities": ["office"]},
            session_id="s_second_document", sdk=sdk)
        assert mismatched["linked"] == 0
        assert mismatched["unresolved"] == ["office"]

        # the losing document's provenance: its session now CONTAINS the claim
        contains = sdk._get_proj().g.query(
            "MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point {id:$pid}) "
            "RETURN count(*)",
            params={"sid": "s_second_document", "pid": prior_id}).result_set
        assert contains[0][0] == 1

        # ONE node, not two — the claim was never duplicated
        same = sdk._get_proj().g.query(
            "MATCH (p:Point) WHERE p.content = $c RETURN p.id",
            params={"c": PRIOR_CONTENT}).result_set
        assert len(same) == 1, same

    def test_a_missing_survivor_is_reported_not_fabricated(self, sdk):
        """Fail-closed: no point ⇒ no edges, and the caller is told."""
        from tortoise.near_dup_merge import apply_merge_union

        res = apply_merge_union(
            sdk._get_proj(), {"point_id": "pt_does_not_exist",
                              "about_entities": ["team"]},
            session_id="s_second_document", sdk=sdk)
        assert res["contained"] == 0
        assert res["linked"] == 0

    def test_an_empty_record_is_total(self, sdk):
        from tortoise.near_dup_merge import apply_merge_union

        res = apply_merge_union(sdk._get_proj(), {}, session_id="s_x")
        assert res["point_id"] == ""
        assert res["warnings"]


if __name__ == "__main__":  # pragma: no cover - manual mutation aid
    raise SystemExit(pytest.main([__file__, "-q"]))

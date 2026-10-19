"""#9068 — the residual of #5475: writes that never meet a gated door.

#5475's closure property is *no write may widen the graph's approved pack set*.
Its fix gated every live door that PERSISTS a ``pack_state._KIND_PROP_KEYS``
property. The review of that closing change (#8988 lineage) recorded three
writes that still never met a door, and this file pins each of them:

1. **A ``kind`` mined from ANY label.** ``graph_kind_namespaces`` scanned the
   bare ``kind`` property label-agnostically, and ``:Pipeline`` stores its
   namespaced pipeline KEY there (``pipeline._write_pipeline``), so
   materialising a ``venture:deal`` override granted the ``venture`` pack:
   measured at HEAD, ``['dev']`` → ``['dev', 'venture']``.
2. **A connector's bare ``proj.apply`` fold.** ``FalkorProjection.apply``
   refuses a direct fold only on a JOURNAL-BACKED projection (§17.1), where the
   cache is a derived image; on a journal-less projection the cache IS the
   store and the fold is a real write — measured at HEAD, github's
   ``object_kind='pm:issue'`` widened a ``dev``-only graph with ``pm``.
3. **The deliberately-ungated re-typing writers.** ``migrate_kinds`` (raw
   Cypher over a live graph) and ``retype`` (a dump, offline) widen by DESIGN;
   #9068 requires that to be STATED rather than implied, so the statement is
   pinned here as well as written in the modules.

Class B per the lane's doctrine (the decision is fixed; the tests assert the
CONTRACT), so every test answers FAIL-ON and REACHABLE.

Lane: embedded. Every graph is a fresh ``test_``-prefixed namespace on its own
``db_path`` — hermetically isolated, no shared fixture state.
"""
from __future__ import annotations

import inspect
import os
import subprocess
import sys
import uuid
from pathlib import Path

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import migrate_kinds, pipeline as P  # noqa: I001
from tortoise.pack_state import (
    _KIND_PROP_ALIASES,
    _LABEL_SCOPED_KIND_KEYS,
    _ONTOLOGY_LABELS,
    graph_installed_namespaces,
    graph_kind_namespaces,
    record_names_a_pack,
    refuse_unapproved_kinds_in_records,
    unapproved_kinds_in_record,
)
from tortoise.sdk import TortoiseSDK, UnapprovedKindNamespace

DEV = "dev"
MARKETING = "marketing"
PM = "pm"

#: A real pipeline for a namespaced kind — the #2792 fixture shape, reduced to
#: the two stages the declaration validator needs.
PIPELINE_KEY = "venture:deal"
PIPELINE_DECL = {
    "defaultStage": "stg_contact",
    "stages": [
        {"id": "stg_contact", "label": "Contact", "order": 1, "slot": "entry"},
        {"id": "stg_won", "label": "Won", "order": 3, "slot": "terminal",
         "outcome": "won"},
    ],
}


def _mk_sdk(tmp_path: Path) -> TortoiseSDK:
    """A fresh, JOURNAL-LESS embedded graph (its own namespace ⇒ its own graph).

    Journal-less is the point for the connector half: on a journal-backed
    projection ``proj.apply`` is refused by §17.1, so the live fold this issue
    is about only exists on a projection that IS the store.
    """
    return TortoiseSDK(db_path=str(tmp_path / "g.db"),
                       namespace=f"test_9068_{uuid.uuid4().hex[:8]}")


def _seed_install(sdk, namespace: str) -> None:
    """Write ONE ``:PackInstall`` activation record (the approval set's record
    leg) — ``ensure_tenant_packs`` would activate the whole starter set, and
    these tests need EXACTLY one namespace installed."""
    sdk._get_proj().g.query(
        "MERGE (p:PackInstall {namespace: $ns}) "
        "SET p.version = '0.0.0', p.status = 'active', p.source = 'starter'",
        params={"ns": namespace},
    )


# ══════════════════════════════════════════════════════════════════════════
# 1. The mining leg — a generic ``kind`` is not an ontology kind
# ══════════════════════════════════════════════════════════════════════════

class TestGenericKindIsNotAnOntologyKind:
    def test_pipeline_key_does_not_widen_the_approval_set(self, tmp_path):
        """⭐ The measured defect: a pipeline key granted its pack.

        FAIL-ON: the ``kind`` leg is label-agnostic again, so
        ``:Pipeline {kind:'venture:deal'}`` contributes ``venture``.
        REACHABLE: the write really happens (the node is asserted with its
        namespaced ``kind``) and the baseline set is non-empty (``['dev']``), so
        "unchanged" is distinguishable from "nothing was ever in it".
        """
        sdk = _mk_sdk(tmp_path)
        _seed_install(sdk, DEV)
        assert graph_installed_namespaces(sdk) == frozenset({DEV})

        P.seed_pipeline(sdk._get_proj().g, PIPELINE_KEY, PIPELINE_DECL)

        g = sdk._get_proj().g
        written = g.query(
            "MATCH (p:Pipeline) RETURN p.pipelineKey, p.kind").result_set
        assert [tuple(r) for r in written] == [(PIPELINE_KEY, PIPELINE_KEY)], (
            "fixture: the pipeline write must be the namespaced key in `kind`")
        assert graph_installed_namespaces(sdk) == frozenset({DEV}), (
            "a pipeline KEY is not an ontology kind — writing one must not "
            "grant its namespace")
        assert "venture" not in graph_kind_namespaces(g)

    def test_a_foreign_generic_kind_owner_does_not_widen_either(self, tmp_path):
        """``:Graph {kind}`` is the OTHER foreign owner of the bare slot.

        FAIL-ON: the ``kind`` leg reads any label, so a ``:Graph`` node (the
        tenant graph's own ``kind='default'`` carrier) with a namespaced value
        grants that pack.
        REACHABLE: the same statement's value is namespaced, so a label-agnostic
        scan WOULD find it — the row exists for exactly the failing shape.
        """
        sdk = _mk_sdk(tmp_path)
        _seed_install(sdk, DEV)
        sdk._get_proj().g.query(
            "MERGE (n:Graph {id: 'g-9068'}) SET n.kind = $k",
            params={"k": f"{MARKETING}:default"},
        )
        # The WRITE is asserted, not assumed: if the MERGE silently no-opped,
        # the only assertion below would hold on the PRE-fix (label-agnostic)
        # code too and this test would degenerate into a copy of its sibling.
        written = sdk._get_proj().g.query(
            "MATCH (n:Graph {id: 'g-9068'}) RETURN n.kind").result_set
        assert [tuple(r) for r in written] == [(f"{MARKETING}:default",)], (
            "fixture: the namespaced kind must really be ON the :Graph node — "
            "a label-agnostic scan is what has to find it")
        assert graph_installed_namespaces(sdk) == frozenset({DEV})

    def test_legacy_kind_on_an_ontology_label_still_widens(self, tmp_path):
        """The label scope must not become a blanket drop of the ``kind`` key.

        ``kind`` is in ``_KIND_PROP_KEYS`` for extractor_v2 legacy-compat: an
        ONTOLOGY node carrying a namespaced ``kind`` is real history and
        #2714 indicator 2 requires it to stay writable.

        FAIL-ON: the fix stops reading ``kind`` at all (rather than scoping it),
        so historical data using that spelling becomes un-updatable.
        REACHABLE: a real ``:Point {kind: 'marketing:legacyKind'}`` row is
        written, and the same graph's baseline set is asserted first.
        """
        sdk = _mk_sdk(tmp_path)
        _seed_install(sdk, DEV)
        g = sdk._get_proj().g
        assert graph_kind_namespaces(g) == frozenset()
        g.query(
            "MERGE (n:Point {id: 'p-9068'}) SET n.kind = $k",
            params={"k": f"{MARKETING}:legacyKind"},
        )
        assert graph_kind_namespaces(g) == frozenset({MARKETING})
        assert graph_installed_namespaces(sdk) == frozenset({DEV, MARKETING})

    def test_the_scoped_key_set_is_pinned_to_the_generic_slot(self):
        """The scope is a SECURITY boundary — pinned against an independent list.

        FAIL-ON: a GENERIC-spelled key is added (or ``kind`` dropped) without
        a decision. The qualified keys are ontology-owned spellings and need no
        scope; ``kind`` is the one slot other models also own.

        REACHABLE: the tuples are compared as SETS, so an addition and a
        removal are both observable.
        """
        assert set(_LABEL_SCOPED_KIND_KEYS) == {"kind"}, (
            "only the GENERIC `kind` slot is label-scoped")
        names = set(_ONTOLOGY_LABELS)
        assert {"Object", "Point", "Event", "Subject", "Source"} <= names, (
            "the five live kind-carrying entity classes must all be scoped in, "
            "or their legacy `kind` values stop being writable")
        assert "Pipeline" not in names and "Graph" not in names, (
            "the labels whose `kind` is their OWN identity must never be "
            "treated as ontology carriers")


# ══════════════════════════════════════════════════════════════════════════
# 2. The record predicate — what a fold would write, by canonical prop
# ══════════════════════════════════════════════════════════════════════════

class TestUnapprovedKindsInRecord:
    def test_the_snake_alias_is_caught_and_named_by_its_camel_prop(self):
        """The alias leg is a widening vector: the fold PERSISTS the camel key.

        ``projection/entities.py`` reads ``object_kind`` and writes
        ``objectKind`` — the property the mining leg then reads. A gate that
        screened only camel spellings would let this through.

        FAIL-ON: ``_KIND_PROP_ALIASES`` stops being consulted, so the record
        reads clean while its fold widens the set.
        REACHABLE: the camel twin is asserted too, so the alias case is
        distinguished from a blanket "everything is caught".
        """
        approved = frozenset({DEV})
        via_alias = unapproved_kinds_in_record(
            {"type": "ObjectRegistered", "object_kind": f"{MARKETING}:campaign"},
            approved)
        assert [(o.prop, o.kind, o.namespace) for o in via_alias] == [
            ("objectKind", f"{MARKETING}:campaign", MARKETING)], (
            "the alias must be reported under the prop the fold will persist")
        via_camel = unapproved_kinds_in_record(
            {"objectKind": f"{MARKETING}:campaign"}, approved)
        assert [o.prop for o in via_camel] == ["objectKind"]
        assert set(_KIND_PROP_ALIASES) >= {
            "object_kind", "subject_kind", "document_kind"}

    def test_bare_core_and_uses_legs(self):
        """A bare/approved spelling is admitted; ``uses[].kind`` is not.

        FAIL-ON: a bare kind is refused (a blanket ban, not an input
        constraint), or the ``uses[]`` leg is dropped — the fold MERGEs an
        ``:Object`` stub with ``objectKind = item['kind']``, so a door that
        skipped it re-opens the widening under another name.
        REACHABLE: the same record carries one admitted value and one offending
        value, so both directions are observed on one call.
        """
        approved = frozenset({DEV})
        assert unapproved_kinds_in_record(
            {"objectKind": "other", "eventKind": "deployment"}, approved) == []
        assert unapproved_kinds_in_record(
            {"objectKind": f"{DEV}:epic"}, approved) == []
        offenders = unapproved_kinds_in_record(
            {"uses": [{"name": "x", "kind": f"{MARKETING}:campaign"}]}, approved)
        assert [(o.prop, o.namespace) for o in offenders] == [(
            "objectKind", MARKETING)]

    def test_no_records_means_no_gate(self):
        """#2714 indicator 3: a graph with no ``:PackInstall`` rows is ungated.

        FAIL-ON: ``approved is None`` is treated as an EMPTY set, which would
        refuse every pack kind on every pre-#318 / self-hosted / restored
        graph.

        REACHABLE: the identical record IS refused against a real set, so the
        empty result comes from the ``None`` arm and not from a clean record.
        """
        record = {"objectKind": f"{MARKETING}:campaign"}
        assert unapproved_kinds_in_record(record, None) == []
        assert unapproved_kinds_in_record(record, frozenset({DEV})), (
            "control: the same record must be refused against a set")

    def test_the_pre_check_spends_no_graph_read(self):
        """A record naming NO pack pays no whole-graph read (#9068 review).

        The gate now sits on HOT live doors (``_apply_and_journal`` runs once
        per indexed issue, ``_emit`` once per ingest event, the connector
        batches once per fold), so the cheap pre-check is what keeps them from
        paying an 8-scan approval-set read to learn there is nothing to
        refuse.

        FAIL-ON: the pre-check is dropped (or re-spelled over a narrower key
        list), so the common core-kind record reaches the graph read — the
        ``_Exploding`` handle raises and the call under test fails.
        REACHABLE: the SAME handle IS reached for a namespaced record, so the
        absence of a read above is the pre-check and not a broken fake.
        """
        class _Exploding:
            def query(self, *args, **kwargs):
                raise AssertionError(
                    "the approval set was read for a record naming no pack")

        assert record_names_a_pack({"objectKind": "other",
                                    "eventKind": "deployment"}) is False
        accept = refuse_unapproved_kinds_in_records  # the door's own entry
        accept(_Exploding(), [{"objectKind": "other",
                               "eventKind": "deployment"}], writer="test")

        assert record_names_a_pack({"object_kind": f"{MARKETING}:x"}) is True
        assert record_names_a_pack(
            {"uses": [{"name": "x", "kind": f"{MARKETING}:x"}]}) is True
        with pytest.raises(AssertionError):
            accept(_Exploding(), [{"object_kind": f"{MARKETING}:x"}],
                   writer="test")


# ══════════════════════════════════════════════════════════════════════════
# 3. The connector fold — the gate in front of a journal-less store write
# ══════════════════════════════════════════════════════════════════════════

def _fake_gh(issues: list[dict], prs: list[dict]):
    """A ``subprocess.run`` stand-in for the connector's ``gh`` CLI calls."""
    def fake_run(args, **kwargs):
        argv = list(args)
        payload = prs if "pr" in argv else issues
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout=__import__("json").dumps(payload))
    return fake_run


ISSUE = {
    "number": 42, "title": "Fix login bug", "state": "open",
    "createdAt": "2026-07-10T10:00:00Z", "closedAt": "",
    "url": "https://github.com/test/repo/issues/42",
    "labels": [], "assignees": [], "author": {"login": "alice"},
    "milestone": None,
}


class TestConnectorFoldIsGated:
    def test_github_ingest_refuses_and_writes_nothing(self, tmp_path, monkeypatch):
        """⭐ The measured defect: ``pm`` widened a ``dev``-only graph.

        FAIL-ON: the batch is not gated (or is gated per record AFTER a write),
        so the refusal never happens — or happens with a half-imported graph
        behind it.
        REACHABLE: the CONTROL test below ingests the identical fixture
        successfully once ``pm`` is approved, so the refusal is caused by the
        gate and not by a malformed payload; and the pre-gate measurement at
        HEAD produced the ``pm`` widening this asserts is gone.
        """
        sdk = _mk_sdk(tmp_path)
        _seed_install(sdk, DEV)
        proj = sdk._get_proj()
        assert proj._journal_backed is False, (
            "fixture: the journal-less store is the case under test")
        monkeypatch.setattr(subprocess, "run", _fake_gh([ISSUE], []))

        from tortoise.connectors.github import GitHubConnector
        gh = GitHubConnector(config={"repo": "test/repo"})
        with pytest.raises(UnapprovedKindNamespace) as excinfo:
            gh.ingest(proj)
        assert PM in str(excinfo.value)
        assert "objectKind" in str(excinfo.value) or "object_kind" in str(
            excinfo.value)

        # NOTHING was written: the gate runs before the first apply, so a
        # refusal is not a partial import.
        assert proj.g.query("MATCH (n:Object) RETURN n").result_set == []
        assert proj.g.query("MATCH (n:Event) RETURN n").result_set == []
        assert graph_installed_namespaces(sdk) == frozenset({DEV}), (
            "the refused fold must not have widened the approval set")

    def test_github_ingest_admits_when_the_pack_is_approved(
            self, tmp_path, monkeypatch, force_embedder_on):
        """CONTROL for the refusal above — and the regression control for #1155.

        FAIL-ON: the gate refuses or drops records on a graph that DOES have
        both namespaces, i.e. the gate broke the working path.
        REACHABLE: the Object node is asserted with its ``pm:issue`` kind and
        its single canonical Event, so a silently-skipped apply is observable.

        ``force_embedder_on`` pins the write path's embedder to the suite's
        deterministic stub: this is the only test here that lets a fold LAND,
        so it is the only one that would otherwise pay the cold HF model load
        behind the hermetic-egress guard (measured 124 s of this file's 126 s).
        """
        sdk = _mk_sdk(tmp_path)
        _seed_install(sdk, DEV)
        _seed_install(sdk, PM)
        proj = sdk._get_proj()
        monkeypatch.setattr(subprocess, "run", _fake_gh([ISSUE], []))

        from tortoise.connectors.github import GitHubConnector
        GitHubConnector(config={"repo": "test/repo"}).ingest(proj)

        rows = proj.g.query(
            "MATCH (o:Object) RETURN o.objectKind, o.name").result_set
        assert [(r[0], r[1]) for r in rows] == [("pm:issue", "test/repo#42")]
        assert len(proj.g.query(
            "MATCH (e:Event) WHERE e.eventId STARTS WITH "
            "'github-issue-test/repo-42' RETURN e").result_set) == 1

    def test_github_ingest_is_ungated_on_a_graph_with_no_records(
            self, tmp_path, monkeypatch, force_embedder_on):
        """#2714 indicator 3 on the connector lane: no records ⇒ no gate.

        FAIL-ON: the gate reads ``None`` (no ``:PackInstall`` rows) as an empty
        allowed set, refusing the import on every pre-#318 / self-hosted graph.

        REACHABLE: the identical fixture is REFUSED in the first test of this
        class once a record exists, so this success is the ``None`` arm.

        ``force_embedder_on`` for the same reason as the control above — this
        test LETS A FOLD LAND, so it would otherwise pay the cold HF model load
        behind the hermetic-egress guard.
        """
        sdk = _mk_sdk(tmp_path)
        assert graph_installed_namespaces(sdk) is None, (
            "fixture: this graph must have NO :PackInstall rows")
        proj = sdk._get_proj()
        monkeypatch.setattr(subprocess, "run", _fake_gh([ISSUE], []))
        from tortoise.connectors.github import GitHubConnector
        GitHubConnector(config={"repo": "test/repo"}).ingest(proj)
        assert proj.g.query(
            "MATCH (o:Object {objectKind: 'pm:issue'}) RETURN o").result_set

    def test_linear_and_slack_ingest_are_gated_too(self, tmp_path,
                                                   force_embedder_on):
        """The gate is per CONNECTOR, not a github special case — and it runs
        BEFORE the first ``apply``, not merely somewhere before the return.

        FAIL-ON: a connector's ``ingest`` writes an unapproved namespaced kind
        (the door inventory is only as good as its least-gated member), OR the
        gate is moved to the END of ``ingest`` — a gate after the
        ``for ev in events: proj.apply(ev)`` loop still raises and still passes
        a ``pytest.raises``-only test, while leaving every record folded behind
        the refusal. The ``:Event``/``:Source`` emptiness assertions are what
        make the ORDERING observable.

        REACHABLE: each arm is BUILT BY THE CONNECTOR'S OWN MAPPER (production
        shape — ``sourceKind``/``eventKind``, no ``objectKind`` at all), the
        unapproved spelling is injected into a key the connector really writes
        (``sourceKind``, a mined property), and the second half of each arm
        ingests the SAME record once the namespace is approved and asserts it
        LANDED — so neither arm is passing because of a malformed payload.

        ``force_embedder_on`` pins the write path's embedder to the suite's
        deterministic stub: the pass-controls LET A FOLD LAND.
        """
        from tortoise.connectors import linear as linear_mod
        from tortoise.connectors import slack as slack_mod

        linear_issue = {
            "identifier": "ENG-9068", "title": "Widen the gate",
            "description": "body", "state": {"name": "In Progress"},
            "createdAt": "2026-07-10T10:00:00Z", "completedAt": None,
            "canceledAt": None, "assignee": {"email": "a@b.c"},
            "team": {"key": "ENG"},
            "url": "https://linear.app/ENG/issue/ENG-9068",
        }
        slack_msg = {"text": "hello", "ts": "1690000000.123456",
                     "user": "U1"}

        def _linear_record() -> dict:
            ev = linear_mod.LinearConnector({})._issue_to_event(linear_issue)
            assert ev is not None, "fixture: the mapper must produce a record"
            ev["sourceKind"] = f"{MARKETING}:linear_card"
            return ev

        def _slack_record() -> dict:
            conn = slack_mod.SlackConnector({})
            conn.channel_id = "C9068"
            ev = conn._message_to_event(slack_msg)
            assert ev is not None, "fixture: the mapper must produce a record"
            ev["sourceKind"] = f"{MARKETING}:slack_message"
            return ev

        for module, cls, make_record in (
            (linear_mod, "LinearConnector", _linear_record),
            (slack_mod, "SlackConnector", _slack_record),
        ):
            sub = tmp_path / module.__name__.rsplit(".", 1)[-1]
            sub.mkdir(exist_ok=True)
            sdk = _mk_sdk(sub)
            _seed_install(sdk, DEV)
            record = make_record()
            proj = sdk._get_proj()
            conn = getattr(module, cls)({})
            conn.poll = lambda record=record: [dict(record)]
            with pytest.raises(UnapprovedKindNamespace) as excinfo:
                conn.ingest(proj)
            assert MARKETING in str(excinfo.value)
            # ORDERING: the gate is before the FIRST apply, so the refusal is
            # not a half-import (a gate moved after the loop leaves these
            # nodes behind and this assertion is what catches it).
            assert proj.g.query("MATCH (n:Event) RETURN n").result_set == [], (
                f"{cls}: the refused batch was folded anyway — the gate ran "
                "after an apply")
            assert proj.g.query("MATCH (n:Source) RETURN n").result_set == []
            assert graph_installed_namespaces(sdk) == frozenset({DEV})

            # PASS-CONTROL: the same record LANDS once the namespace is
            # approved, so both arms are gated by the gate and not by the
            # payload shape.
            _seed_install(sdk, MARKETING)
            conn.poll = lambda record=record: [dict(record)]
            assert conn.ingest(proj) == 1
            assert proj.g.query(
                "MATCH (n:Event) RETURN n").result_set, (
                f"{cls}: the record never folded on the approved graph")


# ══════════════════════════════════════════════════════════════════════════
# 3b. The SDK's OWN live doors — the chokepoint the indexer writes through
# ══════════════════════════════════════════════════════════════════════════

class TestSdkLiveDoorsAreGated:
    def test_github_indexer_fold_refuses_and_writes_nothing(self, tmp_path):
        """⭐ #9068: the chokepoint behind the GITHUB INDEXER.

        ``github_indexer`` folds every issue through
        ``TortoiseSDK._apply_and_journal`` and the Object it hands over comes
        from ``github_map.issue_to_object`` — which carries
        ``object_kind='pm:issue'`` (the fold's SNAKE spelling, which the
        camel-only props gate does not screen). So the polling entry point most
        callers actually use widened a ``dev``-only graph with ``pm`` — the
        identical defect this PR closes for the connectors, one file over.

        FAIL-ON: ``_apply_and_journal`` folds without asking (the record gate
        is removed, or reduced to the camel-only props gate).
        REACHABLE: the record is built by the REAL mapper (no hand-written
        dict), the refusal names ``pm``, and the pass-control below lands the
        same record once ``pm`` is approved.
        """
        from tortoise import github_map

        sdk = _mk_sdk(tmp_path)
        _seed_install(sdk, DEV)
        proj = sdk._get_proj()
        record = github_map.issue_to_object(ISSUE, "test/repo")
        assert record is not None and record["object_kind"] == f"{PM}:issue", (
            "fixture: the mapper's kinds must be the namespaced production "
            "shape")
        assert graph_installed_namespaces(sdk) == frozenset({DEV})

        with pytest.raises(UnapprovedKindNamespace) as excinfo:
            sdk._apply_and_journal(proj, dict(record))
        assert PM in str(excinfo.value)
        assert proj.g.query("MATCH (n:Object) RETURN n").result_set == []
        assert proj.g.query("MATCH (n:Event) RETURN n").result_set == []
        assert graph_installed_namespaces(sdk) == frozenset({DEV}), (
            "the refused fold must not have widened the approval set")

    def test_github_indexer_fold_admits_when_pm_is_approved(
            self, tmp_path, force_embedder_on):
        """CONTROL for the refusal above — the indexer path still works.

        FAIL-ON: the gate refuses (or drops) the indexer's fold on a graph
        that DOES hold ``pm`` — i.e. the new door broke the live path.
        REACHABLE: the Object node is asserted with the mapper's own kind, so a
        silently-skipped apply is observable.
        """
        from tortoise import github_map

        sdk = _mk_sdk(tmp_path)
        _seed_install(sdk, DEV)
        _seed_install(sdk, PM)
        proj = sdk._get_proj()
        sdk._apply_and_journal(
            proj, github_map.issue_to_object(ISSUE, "test/repo"))
        rows = proj.g.query(
            "MATCH (o:Object) RETURN o.objectKind, o.name").result_set
        assert [(r[0], r[1]) for r in rows] == [(f"{PM}:issue", "test/repo#42")]

    def test_update_point_cannot_install_a_pack(self, tmp_path,
                                              force_embedder_on):
        """``update_point``'s props land in a live ``SET n +=`` — mined too.

        ``_sanitize_props`` rejects only reserved envelope/actor/belief names,
        so ``update_point(id, pointKind='marketing:x')`` reached the SET and
        widened the graph's approved set.

        FAIL-ON: the update door is ungated, so the namespaced kind is
        persisted and ``marketing`` joins the approved set.
        REACHABLE: the point is created on the SAME graph first (so the refusal
        is about the update and not a missing node), and the control below
        updates with a bare kind on the same graph and succeeds.
        """
        sdk = _mk_sdk(tmp_path)
        _seed_install(sdk, DEV)
        point = sdk.create_point("statement", "gated write")
        assert graph_installed_namespaces(sdk) == frozenset({DEV})

        with pytest.raises(UnapprovedKindNamespace) as excinfo:
            sdk.update_point(point["id"], pointKind=f"{MARKETING}:campaign")
        assert MARKETING in str(excinfo.value)
        assert graph_installed_namespaces(sdk) == frozenset({DEV}), (
            "the refused update must not have widened the approval set")
        stored = sdk._get_proj().g.query(
            "MATCH (n:Point {id: $id}) RETURN n.pointKind",
            params={"id": point["id"]}).result_set
        assert [r[0] for r in stored] == ["statement"], (
            "the refused value must not have been written")

        # CONTROL: a bare (core) kind is an input the gate ADMITS — this is an
        # input constraint, never a blanket ban on updating kind properties.
        sdk.update_point(point["id"], pointKind="hypothesis")
        assert [r[0] for r in sdk._get_proj().g.query(
            "MATCH (n:Point {id: $id}) RETURN n.pointKind",
            params={"id": point["id"]}).result_set] == ["hypothesis"]

    def test_event_api_emit_cannot_install_a_pack(self, tmp_path,
                                                 force_embedder_on):
        """``EventAPI._emit`` is the ingest CLI's write door (same defect).

        FAIL-ON: ``_emit`` appends + folds an unvalidated ``object_kind``, so
        ``marketing`` joins a ``dev``-only graph's approved set.
        REACHABLE: the SAME door writes a bare core kind successfully right
        after (``object_kind='other'``), so the refusal is the gate and not a
        broken emitter.
        """
        from tortoise.api import EventAPI
        from tortoise.log import EventLog

        sdk = _mk_sdk(tmp_path)
        _seed_install(sdk, DEV)
        proj = sdk._get_proj()
        api = EventAPI(EventLog(str(tmp_path / "events.jsonl")),
                       initiated_by="extractor", projection=proj)
        assert graph_installed_namespaces(sdk) == frozenset({DEV})

        with pytest.raises(UnapprovedKindNamespace) as excinfo:
            api.add_object("campaign", object_kind=f"{MARKETING}:campaign")
        assert MARKETING in str(excinfo.value)
        assert proj.g.query("MATCH (n:Object) RETURN n").result_set == []
        assert graph_installed_namespaces(sdk) == frozenset({DEV})

        # CONTROL: the identical door with a bare core kind still writes.
        api.add_object("an object", object_kind="other")
        assert proj.g.query(
            "MATCH (o:Object {objectKind: 'other'}) RETURN o").result_set


# ══════════════════════════════════════════════════════════════════════════
# 3c. The props doors screen the fold's SNAKE aliases too (review P1)
# ══════════════════════════════════════════════════════════════════════════

class TestPropsDoorsScreenTheFoldAliases:
    def test_create_object_snake_alias_cannot_install_a_pack(
            self, tmp_path, force_embedder_on):
        """⭐ The funnel the PR lists as gated was bypassable under the alias.

        ``create_entity`` relays caller ``props`` with ``**props``, so
        ``props={"object_kind": "pm:issue"}`` survived the CAMEL-only screen
        (``_KIND_PROP_KEYS``) and reached the fold, which reads
        ``ev.get("object_kind")`` and writes the MINED ``o.objectKind`` —
        ``pm`` installed by a write, from the SDK's own public door.

        FAIL-ON: ``_refuse_unapproved_kinds_in_props`` drops its
        ``_KIND_PROP_ALIASES`` leg, so the snake spelling folds and widens.
        REACHABLE: an Object node really lands through the same door for a
        bare alias right after (CONTROL), so the refusal is the gate and not a
        broken funnel; and the alias value is the production spelling the fold
        consumes (asserted below on the landing arm).
        """
        sdk = _mk_sdk(tmp_path)
        _seed_install(sdk, DEV)
        assert graph_installed_namespaces(sdk) == frozenset({DEV})

        with pytest.raises(UnapprovedKindNamespace) as excinfo:
            sdk.create_object("x", props={"object_kind": f"{PM}:issue"})
        assert PM in str(excinfo.value)
        assert excinfo.value.prop == "objectKind", (
            "the refusal must name the stored property, not the alias")
        assert sdk._get_proj().g.query(
            "MATCH (n:Object) RETURN n").result_set == []
        assert graph_installed_namespaces(sdk) == frozenset({DEV})

        # CONTROL: the SAME alias path with a bare spelling lands, and it
        # lands AS the camel property (which is why the alias leg is needed).
        sdk.create_object("y", props={"object_kind": "other"})
        rows = sdk._get_proj().g.query(
            "MATCH (o:Object) RETURN o.name, o.objectKind").result_set
        assert [tuple(r) for r in rows] == [("y", "other")]

    def test_update_entity_cannot_install_a_pack(
            self, tmp_path, force_embedder_on):
        """``_update_entity`` is the MCP ``tortoise_update_entity`` door.

        ``_sanitize_props`` is a DENYLIST of reserved envelope/actor/belief
        names, and ``reject_server_managed_props`` at the MCP boundary does not
        list the kind properties — so ``update_entity(id,
        objectKind='marketing:campaign')`` reached a live ``SET n += $p``.

        FAIL-ON: the update door is ungated (the props are persisted and the
        namespace joins the approved set).
        REACHABLE: the Object is created on the SAME graph first, and the
        CONTROL updates it with a bare kind and the value is read back.
        """
        sdk = _mk_sdk(tmp_path)
        _seed_install(sdk, DEV)
        obj = sdk.create_object("thing")
        assert graph_installed_namespaces(sdk) == frozenset({DEV})

        with pytest.raises(UnapprovedKindNamespace) as excinfo:
            sdk.update_entity(obj["id"], objectKind=f"{MARKETING}:campaign")
        assert MARKETING in str(excinfo.value)
        assert graph_installed_namespaces(sdk) == frozenset({DEV})
        assert [r[0] for r in sdk._get_proj().g.query(
            "MATCH (o:Object {id: $id}) RETURN o.objectKind",
            params={"id": obj["id"]}).result_set] == ["other"]

        sdk.update_entity(obj["id"], objectKind="portfolio")
        assert [r[0] for r in sdk._get_proj().g.query(
            "MATCH (o:Object {id: $id}) RETURN o.objectKind",
            params={"id": obj["id"]}).result_set] == ["portfolio"]

    def test_link_source_to_entity_cannot_install_a_pack(
            self, tmp_path, force_embedder_on):
        """``sourceKind`` on the auto-created ``:Source`` is MINED.

        ``link_source_to_entity`` relays ``source_kind`` to
        ``projection/edges.py``'s ``ON CREATE SET s.sourceKind=$sk`` with the
        caller's string, so a namespaced value installed its pack.

        FAIL-ON: the door is ungated.
        REACHABLE: the CONTROL links the same source with a bare kind and the
        ``sourceKind`` is read back off the ``:Source`` node.
        """
        sdk = _mk_sdk(tmp_path)
        _seed_install(sdk, DEV)
        obj = sdk.create_object("target")
        assert graph_installed_namespaces(sdk) == frozenset({DEV})

        with pytest.raises(UnapprovedKindNamespace) as excinfo:
            sdk.link_source_to_entity(
                "https://example.test/refused", obj["id"], "Object",
                f"{MARKETING}:anything")
        assert MARKETING in str(excinfo.value)
        assert sdk._get_proj().g.query(
            "MATCH (s:Source) RETURN s").result_set == []
        assert graph_installed_namespaces(sdk) == frozenset({DEV})

        sdk.link_source_to_entity(
            "https://example.test/ok", obj["id"], "Object", "web")
        assert [r[0] for r in sdk._get_proj().g.query(
            "MATCH (s:Source) RETURN s.sourceKind").result_set] == ["web"]


# ══════════════════════════════════════════════════════════════════════════
# 3d. The webhook folds — the path PR #9073 claimed gated and never tested
# ══════════════════════════════════════════════════════════════════════════

class _ApiStub:
    """The connector's ``api`` seam: ``api.get_proj()`` (see ``ingest(proj)``)."""

    def __init__(self, proj):
        self._proj = proj

    def get_proj(self):
        return self._proj


def _free_port() -> int:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestWebhookFoldsAreGated:
    def test_github_webhook_fold_refuses_and_fires_no_consumer(
            self, tmp_path, force_embedder_on):
        """⭐ #9068 review P2: ``do_POST``'s gate had NO test.

        The gate lived inline in the handler, so deleting it (or moving it
        below ``proj.apply``) red nothing. It now lives in one method the
        handler calls, and this test drives THAT method.

        FAIL-ON: the webhook write step folds without the gate (the record
        lands and ``marketing`` joins the approved set), OR the gate runs after
        ``on_event`` (the consumer callback fires for a write that never
        happens — observable through ``seen``).
        REACHABLE: the record is the shape the gate refuses and the pass-arm
        below folds the SAME record once the namespace is approved.
        """
        from tortoise.connectors.github import GitHubConnector

        sdk = _mk_sdk(tmp_path)
        _seed_install(sdk, DEV)
        proj = sdk._get_proj()
        conn = GitHubConnector(config={"repo": "test/r"}, api=_ApiStub(proj))
        seen: list[dict] = []
        ev = {"type": "EventRecorded", "eventId": "gh-9068",
              "eventKind": "github.issue.opened",
              "sourceKind": f"{MARKETING}:card"}

        with pytest.raises(UnapprovedKindNamespace) as excinfo:
            conn._fold_webhook_event(dict(ev), on_event=seen.append)
        assert MARKETING in str(excinfo.value)
        assert seen == [], "on_event fired for a delivery that never landed"
        assert proj.g.query("MATCH (n:Event) RETURN n").result_set == []
        assert graph_installed_namespaces(sdk) == frozenset({DEV})

        _seed_install(sdk, MARKETING)
        conn._fold_webhook_event(dict(ev), on_event=seen.append)
        assert len(seen) == 1
        assert proj.g.query("MATCH (n:Event) RETURN n").result_set

    def test_slack_webhook_fold_refuses_and_fires_no_consumer(self, tmp_path):
        """The same pin for Slack's handler (it had none either)."""
        from tortoise.connectors.slack import SlackConnector

        sdk = _mk_sdk(tmp_path)
        _seed_install(sdk, DEV)
        proj = sdk._get_proj()
        conn = SlackConnector(config={"token": "x"}, api=_ApiStub(proj))
        seen: list[dict] = []
        ev = {"type": "EventRecorded", "eventId": "sl-9068",
              "eventKind": "slack.message",
              "sourceKind": f"{MARKETING}:slack_message"}

        with pytest.raises(UnapprovedKindNamespace):
            conn._fold_webhook_event(dict(ev), on_event=seen.append)
        assert seen == []
        assert proj.g.query("MATCH (n:Event) RETURN n").result_set == []
        assert graph_installed_namespaces(sdk) == frozenset({DEV})

    def test_github_webhook_answers_a_refusal_422_and_keeps_the_store_empty(
            self, tmp_path, force_embedder_on):
        """Review P3: a DETERMINISTIC refusal must not be answered 500.

        ``UnapprovedKindNamespace`` is the SDK's "you may not write this"
        signal; ``hosted_api`` maps it to 422. Answering 500 told GitHub to
        retry a payload that refuses identically every time, and threw away the
        refusal's own remedy text (``Next steps: …``).

        FAIL-ON: the handler's blanket ``except Exception`` swallows it as 500
        (and the body is empty, so the remedy is lost).
        REACHABLE: the same handler answers 200 for the SAME payload once the
        namespace is approved — so the status is a function of the gate, not of
        a broken server.
        """
        import json as _json
        import urllib.error
        import urllib.request

        from tortoise.connectors.github import GitHubConnector

        sdk = _mk_sdk(tmp_path)
        _seed_install(sdk, DEV)
        proj = sdk._get_proj()
        port = _free_port()
        gh = GitHubConnector(config={"repo": "test/r", "webhook_port": port,
                                     "webhook_secret": ""}, api=_ApiStub(proj))
        # The mapper's production event names no pack; inject the namespaced
        # spelling into the key the fold MINES, so the refusal is the only
        # variable between the two arms.
        gh._webhook_to_event = lambda event_type, payload: {
            "type": "EventRecorded", "eventId": "gh-webhook-9068",
            "eventKind": "github.issue.opened",
            "sourceKind": f"{MARKETING}:card"}
        body = _json.dumps({"action": "opened"}).encode()
        url = f"http://127.0.0.1:{port}/"
        assert gh.start_webhook() == port
        try:
            def _post():
                req = urllib.request.Request(
                    url, data=body,
                    headers={"Content-Type": "application/json",
                             "X-GitHub-Event": "issues"})
                try:
                    with urllib.request.urlopen(req, timeout=5) as r:
                        return r.status, r.read()
                except urllib.error.HTTPError as exc:
                    return exc.code, exc.read()

            status, payload = _post()
            assert status == 422, (
                f"a deterministic refusal was answered {status}")
            assert MARKETING.encode() in payload, (
                "the refusal's remedy text must reach the caller")
            assert proj.g.query("MATCH (n:Event) RETURN n").result_set == []

            _seed_install(sdk, MARKETING)
            status, _ = _post()
            assert status == 200, "the approved arm must be admitted"
            assert proj.g.query("MATCH (n:Event) RETURN n").result_set
        finally:
            gh.stop_webhook()


class TestGateCostOnTheHotPaths:
    def test_a_batch_naming_no_pack_spends_no_graph_read(self):
        """Review P2/P3: the gate is on HOT doors — it must pre-check.

        ``_gate_kinds`` resolves the approval set with
        ``graph_installed_namespaces`` (8 whole-graph scans, O(nodes)), and the
        WEBHOOK arms call it with ONE record per delivered message. A record
        whose kinds name no pack cannot be refused, so the read is pure cost.

        FAIL-ON: the pre-check is dropped, so a bare/core batch reaches the
        graph read (the fake's ``g`` raises).
        REACHABLE: the SAME fake IS reached for a namespaced batch, so the
        first arm's silence is the pre-check and not a broken fake.
        """
        from tortoise.connectors import _READ_THE_SET, _gate_kinds

        class _ExplodingProj:
            @property
            def g(self):
                raise AssertionError(
                    "the approval set was read for a batch naming no pack")

        bare = [{"type": "EventRecorded", "eventKind": "github.issue.opened",
                 "sourceKind": "github_issue", "objectKind": "other"}]
        assert _gate_kinds(_ExplodingProj(), bare, writer="test") is _READ_THE_SET, (
            "the sentinel must pass through unchanged: a batch that read "
            "nothing may not hand the next batch a resolved set")
        with pytest.raises(AssertionError):
            _gate_kinds(_ExplodingProj(),
                        [{"object_kind": f"{MARKETING}:x"}], writer="test")

    def test_slack_ingest_gates_before_the_network_enrichment(
            self, tmp_path, force_embedder_on):
        """Review P3: enrich-after-refusal spent a permalink call per message.

        ``_enrich_source_urls`` costs one ``chat.getPermalink`` per message; on
        a refused batch every one of those calls was spent on records that are
        then dropped wholesale.

        FAIL-ON: the gate is back below ``_enrich_source_urls`` (the enrichment
        runs for a batch that never lands).
        REACHABLE: the pass-arm below shows the same connector DOES enrich once
        the namespace is approved, so an empty ``calls`` is ordering, not a
        broken hook.
        """
        from tortoise.connectors import slack as slack_mod

        sdk = _mk_sdk(tmp_path)
        _seed_install(sdk, DEV)
        proj = sdk._get_proj()
        calls: list[int] = []
        conn = slack_mod.SlackConnector({})
        ev = {"type": "EventRecorded", "eventId": "sl-9068",
              "eventKind": "slack.message",
              "sourceKind": f"{MARKETING}:slack_message"}
        conn.poll = lambda: [dict(ev)]
        conn._enrich_source_urls = lambda events: calls.append(len(events))

        with pytest.raises(UnapprovedKindNamespace):
            conn.ingest(proj)
        assert calls == [], (
            "the permalink enrichment ran for a batch the gate REFUSED")

        _seed_install(sdk, MARKETING)
        assert conn.ingest(proj) == 1
        assert calls == [1], "the approved arm still enriches exactly once"


# ══════════════════════════════════════════════════════════════════════════
# 4. #9068 §3 — the ungated re-typing writers are STATED, not implied
# ══════════════════════════════════════════════════════════════════════════

class TestUngatedReTypingIsStated:
    def test_migrate_kinds_states_that_it_bypasses_the_gate(self):
        """The PR that closed #5475 says "the write path is closed"; a reader
        will take that literally.

        FAIL-ON: the statement is dropped from the module (or its ``_do_migrate``
        docstring), leaving an ungated live ``SET`` with nothing explaining why
        — the reader then either "fixes" it (breaking the migration) or trusts
        a closure that is not total.
        REACHABLE: the assertions read the REAL source of the module that
        performs the raw-Cypher write, and require BOTH the statement and the
        reason it is deliberate.
        """
        module_src = inspect.getsource(migrate_kinds)
        assert "UNGATED BY DESIGN" in module_src
        assert "operator" in module_src.lower(), (
            "the statement must say WHAT makes it acceptable (an operator act)")
        assert "deliberate" in module_src.lower()
        assert "raw Cypher" in module_src

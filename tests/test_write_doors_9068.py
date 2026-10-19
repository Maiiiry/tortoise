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

    def test_linear_and_slack_ingest_are_gated_too(self, tmp_path):
        """The gate is per CONNECTOR, not a github special case.

        FAIL-ON: a connector's ``ingest`` writes an unapproved namespaced kind
        (the door inventory is only as good as its least-gated member).

        REACHABLE: each connector's own ``poll()`` is stubbed to return one
        record naming ``marketing``, and each is asserted to raise — so both
        the linear and slack arms are exercised, not just the shape.
        """
        from tortoise.connectors import linear as linear_mod
        from tortoise.connectors import slack as slack_mod

        for module, cls, record in (
            (linear_mod, "LinearConnector",
             {"type": "EventRecorded", "object_kind": f"{MARKETING}:campaign"}),
            (slack_mod, "SlackConnector",
             {"type": "EventRecorded", "objectKind": f"{MARKETING}:campaign"}),
        ):
            sub = tmp_path / module.__name__.rsplit(".", 1)[-1]
            sub.mkdir(exist_ok=True)
            sdk = _mk_sdk(sub)
            _seed_install(sdk, DEV)
            conn = getattr(module, cls)({})
            conn.poll = lambda record=record: [record]
            with pytest.raises(UnapprovedKindNamespace):
                conn.ingest(sdk._get_proj())


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

"""#9068 — the write paths that never meet a #5475 gated door, MEASURED.

`#5475`'s closure property is **a write may not widen the graph's approved
pack set**. At HEAD that property is enforced at five places — the SDK point
door (`sdk.create_point`), the SDK entity funnel (`sdk._create_entity`, which
`create_object`/`create_subject`/`create_event`/`create_document`/
`create_source`/`create_entity` all relay through), the hosted commit door's
pre-validation, and the `POST /v1/objects` / `POST /v1/subjects` pre-checks
(`hosted_api._require_approved_kind`, #8380) — and **nowhere else**. The
decision is expressed more than once — the SDK's `_unapproved_kind_refusal`,
`pack_state.kind_namespace_is_approved`, and the approval-gated vocabulary
membership (`compile_vocab(installed_namespaces=…)`, which `validate_layer1`
tests for a namespaced `pointKind`/`eventKind`/`sourceKind` at the commit
door's Layer-1) — so a door inventory has to read the CALL SITES, not one
helper. A reader who takes "the write path is closed" literally will not go
looking, and several writers still widen the set today. `#9068` records that;
this module makes it a *measurement* instead of prose, so the claim's true
scope is executable and cannot drift back:

* two tests here PASS and pin the two halves of that scope that are real — the
  gated door refusing (and moving nothing), and the deliberate
  data-mining leg the gate's back-compat rests on;
* two tests are `xfail(strict=True)` on the **fixed** behaviour of the two
  residuals nothing is currently fixing, so closing either one XPASSes its pin
  and FAILS the suite on purpose rather than letting the pin rot — the idiom
  `tests/test_url_keyed_source_write_4649.py` uses for `#5048`;
* one test PASSES and is the **declared** departure (offline re-typing).

The approved set is `:PackInstall` records **∪** the namespaces mined out of
the graph's own data (`pack_state.graph_kind_namespaces`, 8 node properties,
label-agnostic). So "a write widened the set" is a one-line, observable fact:
read the set before, perform ONE write, read it after.

## What is measured here, and what is only NAMED

**Widening at HEAD, pinned here** (nothing is fixing these):

1. **`:Pipeline.kind` is a mined property** — `pipeline._write_pipeline` writes
   ``SET p.kind = $kind`` with the pipeline KEY as the value, and
   `graph_kind_namespaces` scans `kind` label-agnostically, so a procedural
   config row grants its namespace. Gating the writer alone is not available
   (the key IS a real ``ns:kind`` spelling, so a gate there would refuse
   legitimate pipeline edits), and dropping `kind` from the scanned keys
   narrows the back-compat union — so this is a **decision about the pipeline
   model**, recorded on `#9068`, not a mechanical patch. **XFAIL.**

2. **A bare fold on a journal-less projection** — `connectors/github.py` folds
   records with ``proj.apply(...)`` (kind ``pm:issue`` from `github_map`), and
   `FalkorProjection.apply` refuses a direct fold only when ``_journal_backed``
   — correctly, since with no journal there is no truth store for the cache to
   be ahead of. So on a journal-less projection the kind is persisted ungated.
   The fix must reuse `pack_state`'s ONE read core rather than re-deriving
   "records ∪ data" a second time (that re-derivation is the drift
   `graph_installed_namespaces` exists to prevent). **XFAIL.**

**Widening at HEAD, named but NOT pinned** — a fix is already in flight, and a
`strict` pin would red the suite the moment it lands:

* `update_point` / `update_entity` (`#8968`) and the journal-first seam
  (`api.EventAPI._emit` → `projection._upsert_*`, `#8982`). Measured on this
  module's own fixture, both still widen the set at HEAD. Their landings are
  the gates the `#5475` thread calls closed; they are not at HEAD yet
  (`PR #9070`, open).

**Deliberately ungated, DECLARED** (3) — `migrate_kinds` re-types with raw
Cypher, and `retype` / `tools/retype_snapshot` work on a dump. `#9068` residual
3 asks for exactly this and nothing more: *"a migration is a deliberate
re-typing, so this may be acceptable by design — but it must be STATED, because
the PR that closes #5475 says the write path is closed and a reader will take
that literally."* The last test is that statement, made executable.

Lane: **either** — the fixture builds its own embedded ``TortoiseSDK`` on
``tmp_path`` (the `tests/test_pipeline_2792.py` idiom), and an explicit
``db_path`` wins over an ambient ``TORTOISE_DB_URI``, so the file behaves
identically on the docker default leg and the URI-less carve-out leg.
Registered in ``config/ci-surfaces.yml`` (``core``), beside the pack tests it
reads.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import pack_state  # noqa: E402, RUF100
from tortoise.migrate_kinds import migrate  # noqa: E402, RUF100
from tortoise.pipeline import seed_pipeline  # noqa: E402, RUF100
from tortoise.projection import DirectCacheWriteRefused  # noqa: E402, RUF100
from tortoise.sdk import (  # noqa: E402, RUF100
    TortoiseSDK,
    UnapprovedKindNamespace,
)

#: A minimal declared pipeline — the shape ``seed_pipeline`` materialises from a
#: pack default (``pipeline.py::_pipeline_from_decl``).
_PIPELINE_DECL: dict = {
    "defaultStage": "stg_contact",
    "stages": [
        {"id": "stg_contact", "label": "Contact", "order": 1, "slot": "entry"},
        {"id": "stg_won", "label": "Won", "order": 2, "slot": "terminal",
         "outcome": "won"},
    ],
}

#: The one namespace this fixture graph has approved. Every residual test asks
#: the same question — can ONE write add a namespace this graph never approved?
_DEV_ONLY: tuple[dict, ...] = ({"namespace": "dev", "status": "active"},)

#: Either shape a fix takes at a seam: REFUSE the write (the gate's own
#: exception) or the fold path's own guard. Both are tolerated in a pin's body
#: so a refusal-shaped fix XPASSes the pin instead of being recorded as an
#: expected failure forever — `strict=True` only reacts to the assertion, so a
#: body that RAISES would stay silently `xfailed`.
_REFUSAL = (UnapprovedKindNamespace, DirectCacheWriteRefused)


def _dev_only_sdk(tmp_path: Path) -> TortoiseSDK:
    """An embedded graph whose approval set is EXACTLY ``{'dev'}``.

    Deliberately constructed WITHOUT ``event_log_path``: that is what makes the
    projection journal-less (``_journal_backed is False``), i.e. the shape the
    connectors run on and the shape residual 2 is about. The recorded ``dev``
    ``:PackInstall`` is what makes the set a real gate rather than the ``None``
    "no records ⇒ catalog union" back-compat answer (#2714 indicator 3) —
    under ``None`` NOTHING can widen the set and every test here would pass for
    the wrong reason, so the precondition is asserted on every construction.
    """
    sdk = TortoiseSDK(str(tmp_path / "residuals.db"))
    pack_state.set_graph_packs(sdk, packs=[dict(p) for p in _DEV_ONLY])
    approved = pack_state.graph_installed_namespaces(sdk)
    assert approved == frozenset({"dev"}), (
        f"fixture precondition: the graph must start approved for 'dev' only, "
        f"got {sorted(approved or ())}")
    return sdk


def _approved(sdk: TortoiseSDK) -> frozenset[str] | None:
    return pack_state.graph_installed_namespaces(sdk)


# ── The control: the gate holds where the closure claim actually applies ──


def test_a_live_door_refuses_an_unapproved_pack_and_moves_nothing(
        tmp_path: Path) -> None:
    """`#5475`'s gate, at a LIVE door — this is the half the closure claim owns.

    ``create_point`` is one of the gated funnels, so the ``marketing:`` spelling
    that widens the set through the residual seams below must be REFUSED here,
    and the refusal must land BEFORE any write (a refusal that left the widening
    behind would be indistinguishable from no gate at all).
    """
    sdk = _dev_only_sdk(tmp_path)

    with pytest.raises(UnapprovedKindNamespace):
        sdk.create_point("marketing:contentPerformance", "probe content")

    assert _approved(sdk) == frozenset({"dev"}), (
        "the refusal must land before the write: the approved set is unmoved")


# ── The deliberate back-compat the gate rests on (#2714 indicator 2) ──────


def test_the_data_leg_keeps_existing_vocabulary_writable(tmp_path: Path) -> None:
    """The mining is deliberate, so "stop mining" is not a free change.

    A namespace already present in the graph's DATA stays in the approval set —
    otherwise a pack that was uninstalled while its kinds stayed all over the
    graph would make its own rows un-updatable. This test asserts the property
    the mining exists for: after a row carrying an unapproved ``marketing:``
    kind lands, a WRITE of that same kind SUCCEEDS instead of being refused.
    The ``:Pipeline`` residual above is what makes narrowing the scan tempting;
    this is the cost that makes it a decision (the scanned key set is declared
    in ``pack_state._KIND_PROP_KEYS`` and pinned by
    ``tests/test_vocab_gating_by_graph.py``).
    """
    sdk = _dev_only_sdk(tmp_path)
    sdk._get_proj().g.query(
        "CREATE (n:Object {id:'o-existing', name:'ExistingProbe', "
        "objectKind:'marketing:product'})")

    created = sdk.create_object("WritableProbe", objectKind="marketing:product")

    assert created and created.get("id"), (
        "the data leg must keep the graph able to write the vocabulary its own "
        "data already holds (#2714 indicator 2)")


# ── Residual 1: :Pipeline.kind is a mined property ────────────────────────


@pytest.mark.xfail(strict=True, reason=(
    "#9068 residual 1 — `pipeline._write_pipeline` persists the pipeline KEY "
    "into the mined `kind` property (`SET p.kind=$kind`) and "
    "`pack_state.graph_kind_namespaces` scans `kind` label-agnostically, so a "
    ":Pipeline row grants its namespace; the fix is a decision about the "
    "pipeline model, recorded on #9068"))
def test_a_pipeline_row_does_not_grant_its_namespace(tmp_path: Path) -> None:
    """(1) Failing value is the approval set gaining ``venture``; (2) the
    fixture is a graph holding only a ``dev`` ``:PackInstall`` plus ONE
    pipeline seeded for the key ``venture:deal`` — a real ``ns:kind`` spelling,
    which is why the writer cannot simply be gated as well."""
    sdk = _dev_only_sdk(tmp_path)

    with contextlib.suppress(*_REFUSAL):
        seed_pipeline(sdk._get_proj().g, "venture:deal", _PIPELINE_DECL)

    assert "venture" not in (_approved(sdk) or frozenset()), (
        "a :Pipeline row is procedural config, not an ontology-kind write: it "
        "must not install the `venture` pack")


# ── Residual 2: a bare fold on a journal-less projection ──────────────────


@pytest.mark.xfail(strict=True, reason=(
    "#9068 residual 2 — `projection.apply` gates a direct fold only when "
    "`_journal_backed`, so on a journal-less projection (the connectors' "
    "shape) an `object_kind` is persisted ungated and widens the set"))
def test_a_journal_less_fold_does_not_widen_the_approval_set(
        tmp_path: Path) -> None:
    """(1) Failing value is the approval set gaining ``pm``; (2) the fixture is
    a fold through the seam the connectors actually use — ``connectors/github``
    → ``proj.apply(entities["object"])`` — carrying the kind ``github_map``
    stamps on an issue Object (``pm:issue``), on a projection with NO journal.
    """
    sdk = _dev_only_sdk(tmp_path)
    proj = sdk._get_proj()
    assert proj._journal_backed is False, (
        "fixture precondition: this is the journal-less shape residual 2 is "
        "about — a journal-backed projection already refuses a direct fold")

    with contextlib.suppress(*_REFUSAL):
        proj.apply({"type": "ObjectRegistered", "id": "o-fold-1",
                    "name": "FoldProbe", "object_kind": "pm:issue"})

    assert "pm" not in (_approved(sdk) or frozenset()), (
        "a fold persists a mined kind property, so with no journal to gate it "
        "the fold itself must consult the graph's approval set")


# ── Residual 3: offline re-typing is DELIBERATELY ungated ─────────────────


def test_offline_retype_is_declared_ungated_by_design(tmp_path: Path) -> None:
    """The DECLARED departure: a migration DOES widen the set, on purpose.

    ``#9068`` residual 3 asks for exactly this and nothing more — *"a migration
    is a deliberate re-typing, so this may be acceptable by design — but it
    must be STATED, because the PR that closes #5475 says the write path is
    closed and a reader will take that literally."* This test is the statement,
    made executable: the widening is real, and it is an operator's own
    re-typing of vocabulary the graph already stores, not a request-carried
    namespace. Gating it would refuse the repair of data that exists.

    Unlike the two pins above this one PASSES, because the behaviour is the
    declared posture rather than a defect awaiting a fix. If the posture ever
    changes to "gate the migration too", this test will fail — and that is the
    point: the departure cannot become silent again.
    """
    sdk = _dev_only_sdk(tmp_path)
    sdk._get_proj().g.query(
        "CREATE (n:Object {id:'o-legacy', name:'LegacyProbe', "
        "objectKind:'product'})")

    migrate(sdk, dry_run=False)

    assert "product-strategy" in (_approved(sdk) or frozenset()), (
        "the declared posture: an offline re-typing persists the migrated kind "
        "through raw Cypher, with no door and no gate")
    assert sdk._get_proj().g.query(
        "MATCH (n:Object {id:'o-legacy'}) RETURN n.objectKind"
    ).result_set[0][0] == "product-strategy:product", (
        "and the migration actually did the re-typing it is declared to do")

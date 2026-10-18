"""#8093 — the `:Graph.namespace` cell vs the namespace the code derives (doc↔code pin).

The defect: ``docs/registry-graph-schema.md`` §Graph stated the custom-graph
namespace as ``team_<org_id>_<gid>`` and the default graph's namespace as
``team_<name>``. The code derives ``org_{org_id}_{gid}`` for customs
(``acl_graph_users._graph_namespace``, ``sdk._graph_create``,
``hosted_api._provision_graph``) and the org namespace for the default graph —
``org_{org_id}`` / ``org_{name}`` — because #3543 renamed the prefix from
``team_`` to ``org_``. ``team_*`` survives only as the pre-rename shape the
purge guard still recognises and no data migration rewrites.

That cell is the input another lane uses to derive an ACL key pattern or a
namespace lookup (``docs/authorization-architecture.md`` §2.1 cites the ACL
recipe), so a stale shape there is not cosmetic.

Class-B test doctrine:
  (1) *What value makes this test fail?*  Restating ``team_<org_id>_<gid>`` (or
      ``team_<name>`` for the default) as the derived shape; dropping a derived
      shape from the cell; un-marking the legacy prefix; or changing the code
      derivation the cell is pinned to.
  (2) *Does the test reach it?*  Yes — the shapes are read from the committed
      doc cell AND from the live derivation functions, so the assertion is
      doc-against-code, never the doc against itself.

Hermetic: two doc reads plus three pure derivations — no fixture, no DB. That is
why it is registered in ``tier1`` as well as ``core``: a ``docs/``-only diff
selects no Python surface, so a docs-only edit is exactly the change class this
pin exists for.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCHEMA_DOC = REPO / "docs" / "registry-graph-schema.md"
AUTHZ_DOC = REPO / "docs" / "authorization-architecture.md"

# Any `file.py:NNN` citation — a line number rots on the next rename or merge
# (the #7818 precedent: two citations in one section went stale within weeks).
_LINE_CITATION = re.compile(r"[\w/]+\.py:\d+")

CUSTOM_SHAPE = "org_{org_id}_{gid}"   # a custom graph: _graph_namespace / _graph_create
DEFAULT_SHAPES = ("org_{org_id}", "org_{name}")  # the org namespace (the default graph)


def _namespace_cell() -> str:
    """The committed `:Graph` namespace cell — from its key to the next key."""
    doc = SCHEMA_DOC.read_text(encoding="utf-8")
    start = doc.index("  namespace: string,")
    end = doc.index("  status: string,", start)
    return doc[start:end]


def _default_graph_bullet() -> str:
    """The §"Default-graph semantics" bullet that states the derived namespace."""
    doc = SCHEMA_DOC.read_text(encoding="utf-8")
    start = doc.index("Default-graph semantics")
    end = doc.index("\n- **No per-graph keys", start)
    return doc[start:end]


# ── 1. the custom shape is the one the code derives ─────────────────────────

def test_custom_namespace_cell_matches_the_acl_and_provisioning_derivation():
    """§Graph states `org_{org_id}_{gid}` — the exact shape both lanes derive.

    (1) *What value makes this test fail?*  The pre-fix ``team_<org_id>_<gid>``
        restated as the custom shape, or the cell losing the derived shape.
    (2) *Does the test reach it?*  Yes — the expected string is built from
        `acl_graph_users._graph_namespace` on a sample (org, gid), so a code
        change to the derivation reds the doc pin too.
    """
    from tortoise.acl_graph_users import _graph_namespace

    derived = _graph_namespace("team_a", "g_0123abcd")
    assert derived == "org_team_a_g_0123abcd", (
        "the ACL key pattern is the graph's exact namespace "
        f"(got {derived!r}); the doc cell is pinned to this derivation")

    cell = _namespace_cell()
    assert CUSTOM_SHAPE in cell, (
        f"§Graph must state the derived custom shape {CUSTOM_SHAPE!r} — "
        "it is the input another lane derives a key pattern from")
    assert "team_<org_id>_<gid>" not in cell, (
        "the legacy shape is stated as the current one; after #3543 the "
        "derivation is org_{org_id}_{gid}")


def test_default_graph_namespace_cell_names_the_org_namespace_not_legacy_team():
    """The default graph's ns is the org namespace — `org_…`, not `team_<name>`.

    (1) *What value makes this test fail?*  Re-stating ``team_<name>`` as the
        default graph's namespace (the pre-fix cell), or dropping either
        hosted/registry variant.
    (2) *Does the test reach it?*  Yes — `hosted_api._org_graph_name` is the
        shared seam the export/open paths use; its no-stored-name fallback is
        asserted to be `org_{org_id}`, which is what the cell must say.
    """
    from tortoise.hosted_api import _org_graph_name

    assert _org_graph_name({}, "team_x") == "org_team_x", (
        "the org namespace fallback is org_{org_id} (#3543 rename)")
    assert _org_graph_name({"graph_name": "org_team_y"}, "team_x") == "org_team_y", (
        "a stored graph_name wins verbatim — the default graph's namespace "
        "IS that stored name")

    cell = _namespace_cell()
    for shape in DEFAULT_SHAPES:
        assert shape in cell, (
            f"§Graph must state {shape!r} as the default graph's namespace")
    assert "team_<name>" not in cell, (
        "the pre-#3543 `team_<name>` is not the default graph's namespace")

    bullet = _default_graph_bullet()
    assert "graph_name" in bullet and "org_" in bullet, (
        "the no-migration bullet must name the stored graph_name it derives — "
        "a bare 'the team namespace' cannot be resolved to a DB graph name")


# ── 2. the legacy prefix is stated as legacy, and is really still recognised ─

def test_legacy_prefix_is_only_stated_as_legacy():
    """Every `team_` mention in the namespace cell is marked LEGACY.

    A reader who finds `team_` next to the derived shapes cannot tell a
    back-compat shape from a current one — which is exactly how the cell went
    stale in the first place.

    (1) *What value makes this test fail?*  A `team_` mention in the cell
        without the LEGACY marker (e.g. re-introducing `team_<org_id>_<gid>`
        as a "supported" shape).
    (2) *Does the test reach it?*  Yes — every line of the committed cell is
        scanned, not just the ones this test knows about.
    """
    offenders = [
        line.strip() for line in _namespace_cell().splitlines()
        if "team_" in line and "LEGACY" not in line
    ]
    assert offenders == [], (
        f"these namespace-cell lines mention `team_` without marking it "
        f"LEGACY: {offenders}")


def test_legacy_prefix_is_still_recognised_where_the_doc_says_it_is():
    """The doc's "still recognised" claim is anchored to the purge guard + resolution.

    (1) *What value makes this test fail?*  The purge guard dropping the
        pre-rename `team_{org_id}_{gid}` acceptance, or the MCP graph-name
        resolution dropping the legacy `team_{org_id}` alternative, while the
        doc keeps claiming back-compat — the reverse direction of the same
        drift.
    (2) *Does the test reach it?*  Yes — both acceptances are read from the
        committed sources (the two places the cell names), so removing either
        reds the doc's claim.
    """
    source = (REPO / "tortoise" / "backup_sweep.py").read_text(encoding="utf-8")
    assert 'f"team_{org_id}_{gid}"' in source, (
        "the tombstone purge guard must keep accepting the pre-#3543 "
        "`team_{org_id}_{gid}` namespace (no migration rewrites it)")
    assert 'f"org_{org_id}_{gid}"' in source, (
        "and the current derivation must stay alongside it")

    mcp = (REPO / "tortoise" / "mcp_server.py").read_text(encoding="utf-8")
    assert 'f"team_{org_id}"' in mcp, (
        "the legacy graph-name resolution must keep accepting the pre-#3543 "
        "`team_{org_id}` org namespace")
    assert 'f"org_{org_id}"' in mcp, (
        "and the current org namespace alongside it")


# ── 3. the pin itself cannot rot the way the cell did ──────────────────────

def test_namespace_citations_are_by_symbol_never_by_line():
    """Both docs cite this fact by symbol — a `file.py:NNN` here rots silently.

    #8093 was filed off citations that had already rotted: the original evidence
    (``sdk.py:20694-20723``, ``hosted_api.py:16815``) no longer contains
    `_graph_create` / `_provision_graph` at all, so a reader auditing tenancy
    landed on unrelated code.

    (1) *What value makes this test fail?*  Re-adding a line-number citation for
        this fact to the `:Graph` cell or to the §2.1 coarse-isolation row.
    (2) *Does the test reach it?*  Yes — the pattern is applied to the committed
        slices, so a citation added anywhere in them reds.
    """
    assert _LINE_CITATION.findall(_namespace_cell()) == [], (
        "the namespace cell cites code by line number; cite the symbol instead")

    authz = AUTHZ_DOC.read_text(encoding="utf-8")
    row = next(
        line for line in authz.splitlines()
        if line.startswith("| **Coarse isolation**")
    )
    assert _LINE_CITATION.findall(row) == [], (
        "the coarse-isolation row cites code by line number; cite the symbol "
        "(`_graph_create`, `_provision_graph`, `_graph_namespace`) instead")


def test_authz_doc_records_the_finding_as_fixed():
    """The #2082 audit note must not keep claiming the schema doc is stale.

    (1) *What value makes this test fail?*  The note reverting to "still states
        `team_<org_id>_<gid>`" / "Filed as #8093" now that the cell is fixed —
        a doc asserting a defect that no longer exists.
    (2) *Does the test reach it?*  Yes — the note is located by its #8093
        mention and asserted to point at the pin.
    """
    authz = AUTHZ_DOC.read_text(encoding="utf-8")
    note = next(
        line for line in authz.splitlines()
        if "#8093" in line and line.startswith(">")
    )
    assert "still states" not in note, (
        "the note claims the §Graph cell is still stale — it is corrected here")
    assert "test_8093_registry_namespace_doc.py" in note, (
        "the note must point at the pin that keeps the cell honest")

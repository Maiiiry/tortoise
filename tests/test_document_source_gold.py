"""The document-source GOLD fixture — D10's per-document topology and identity facts.

Why this test exists
--------------------
The fixture declares the shapes a document-bearing `:Source` takes under D10
(*a document is a SOURCE, not a graph node*), plus the version/idempotency and
replay invariants epic **#5088** must honour. Production cannot supply that
population: the live graph was measured **all `agentSession`** (2,208 rows,
2026-09-24). A constructed fixture is therefore the only way to *measure* the
declared facts against a real graph instead of merely declaring them.

This test does three jobs:

1. **Fixture integrity** — the gold file is well-formed and every declaration
   it makes is one it can be held to.
2. **Non-vacuity, on a real graph** — the fixture sources are written to an
   embedded FalkorDB and the declared topology/identity facts are read back as
   REAL Cypher. Each source row resolves to exactly ONE node by `url`, with the
   declared label, source kind and genre.
3. **The retirement is EXECUTABLE** — the fixture must not re-assert the retired
   `documents`-cap model (`test_the_retired_documents_cap_model_cannot_return`).

⚠️ **The `documents`-cap predicate ruling (Q-S2) is SUPERSEDED — this file no
longer carries it.** The owner's cost-model direction (2026-09-25, recorded on
`#5013`) makes **a document not a cost unit**: cost is the bytes stored
(Postgres MB/GB + graph MB) and the extraction triggered (LLM usage), with
volume sold as overage rather than gated as a count. So the question *"which
`:Source` record does the `documents` meter count?"* **dissolves rather than
resolves — neither, because nothing counts documents.** `/v1/index/docs` stays
gated, by the storage + extraction allowance instead of a document count.

This was corrected by **#5332**. What it replaced, and why the removal is a
deliberate act rather than a tidy-up: the file previously asserted a
`sourceKind='document'` predicate as *"the ruling under test"* and named the
form `quota.py` implements (`documentKind IS NOT NULL AND …`) **`REJECTED`** —
so a merged, green artifact asserted a model that no longer existed, and the
next lane to read it could reimpose the cap. The **per-document topology and
identity facts are kept** (they are real, and nothing else pins them); the
predicate table, the per-row cap verdicts and the `REJECTED_*` candidates are
gone.

**Boundary, stated so it is not misread:** nothing here EXERCISES the writer.
The graph half loads the fixture rows directly as `:Source` nodes and reads the
declared facts back. Driving the real writer (`_upsert_document` /
`_upsert_source`) is the **integration** layer the epic's test design assigns to
#5088's implementation — this file is a gold *target*, not that test.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "document_source_gold.jsonl"

#: The keys of the RETIRED `documents`-cap model. Named here so the retirement
#: is executable: a row carrying any of them reds the suite (see
#: `test_the_retired_documents_cap_model_cannot_return`).
RETIRED_CAP_KEYS = ("predicate_matrix", "metered_by_documents_cap",
                    "metered_by_sessions_cap")
#: The cap bands, likewise retired. A row rejoining one reds.
RETIRED_CAP_BANDS = ("document-must-count", "session-must-not-count")

#: The rows that describe a DISTINCT `:Source` node to be loaded and read back.
#: Pinned BY NAME: a row silently dropping out of the graph check is the
#: mutation this guard exists to catch.
SOURCE_CASES = frozenset({
    "doc-with-genre", "doc-without-genre", "session-source", "transcript-source",
})

#: Every case's band, pinned BY NAME. A SWAP between two rows leaves the band
#: SET unchanged, so set-equality cannot see it.
BAND_BY_CASE = {
    "doc-with-genre": "document-source",
    "doc-without-genre": "document-source",
    "session-source": "session-source",
    "transcript-source": "session-source",
    "doc-reingest-unchanged": "idempotency",
    "doc-reingest-changed": "versioning",
    "doc-aboutDocument-roundtrip": "replay-identity",
}

#: The band each SOURCE row must sit in, keyed off its declared `sourceKind`.
#: A set-equality check cannot see a document filed under `session-source`.
BAND_BY_SOURCE_KIND = {"document": "document-source",
                       "agentSession": "session-source"}


def _load() -> tuple[dict, list[dict]]:
    lines = [ln for ln in FIXTURE.read_text().splitlines() if ln.strip()]
    head = json.loads(lines[0])
    rows = [json.loads(ln) for ln in lines[1:]]
    return head, rows


def _source_rows(rows: list[dict]) -> list[dict]:
    """The cases that describe a DISTINCT :Source node to be loaded and read.

    Two exclusions, both deliberate:

    * the edge/round-trip case has no ``input.url`` — it is about resolution,
      not about a node; and
    * the two *reingest* cases are STATE TRANSITIONS of an already-present
      source (and deliberately reuse ``doc-with-genre``'s url). Loading them as
      separate nodes would create duplicate url keys and inflate every count.

    The behavioural cases (the two reingests, and the ``aboutDocument``
    round-trip) are NOT exercised by any test here — the behaviour belongs to
    epic #5088's implementation. What this file does assert about them is that
    their declared invariants are internally consistent and complete
    (``test_declared_expected_values_are_all_asserted``), so each row is a
    usable gold target rather than prose.

    The discriminator is therefore ``node_label`` being declared: a case either
    states the topology of a node it describes, or it is not a source row.
    """
    return [
        r for r in rows
        if r.get("input", {}).get("url") and "node_label" in r.get("expected", {})
    ]


# ── Job 1 + 3: fixture integrity, and the retirement is executable ───────────

def test_fixture_is_well_formed() -> None:
    head, rows = _load()
    assert head["__dataset__"] == "document-source-gold"
    assert head["__round__"] == 3, (
        "round 3 is the reduction that retired the documents-cap model (#5332) — "
        "a revert to round 2 would restore the predicate table"
    )
    assert "SYNTHETIC" in head["__provenance__"], (
        "the fixture must declare that it is constructed, not sampled — the "
        "whole reason it exists is that production cannot supply the contrast set"
    )
    assert rows, "no cases"
    for r in rows:
        for key in ("case", "band", "input", "expected",
                    "why_this_case", "label", "reason", "source"):
            assert key in r, f"{r.get('case')}: missing {key}"
        assert r["why_this_case"].strip(), f"{r['case']}: empty rationale"
        assert r["expected"], f"{r['case']}: empty expected block is unassertable"


def test_the_retired_documents_cap_model_cannot_return() -> None:
    """#5332 — the fixture must not re-assert the retired `documents`-cap model.

    A merged artifact that asserts a retired model is worse than a missing one:
    the next lane to read it reimposes the cap. The earlier file did exactly
    that — a per-row `predicate_matrix` over three candidate predicates, a
    `metered_by_documents_cap` verdict per document, and two `REJECTED_*`
    constants naming the form the shipped code implements. Removing the text
    without removing the capability would leave the same trap one edit away, so
    the absence is asserted here: any row that regains a cap key or a cap band
    reds the suite, and the fixture's own rule line must keep naming the
    supersession.
    """
    head, rows = _load()
    offenders = []
    for r in rows:
        for key in RETIRED_CAP_KEYS:
            if key in r or key in r.get("expected", {}):
                offenders.append(f"{r['case']}: carries {key}")
        if r["band"] in RETIRED_CAP_BANDS:
            offenders.append(f"{r['case']}: carries retired band {r['band']!r}")
    assert not offenders, (
        "the retired `documents`-cap model is back:\n  - " + "\n  - ".join(offenders)
    )

    # and the header must keep recording WHY there is no predicate to rule on —
    # an omission-only guard passes over a silently emptied rule line
    for text, what in ((head["__rule__"], "__rule__"),
                       (head["__provenance__"], "__provenance__")):
        assert "SUPERSEDED" in text or "retired" in text, (
            f"the fixture's {what} no longer records that the documents-cap model "
            f"is superseded (#5332) — it reads as though the model were still live"
        )


def test_declared_expected_values_are_all_asserted() -> None:
    """Every declared value is one the file can be held to — and the set is COMPLETE.

    The completeness guard at the end is the load-bearing part: without it, a
    newly added `expected` key is silently unasserted and a mutated value
    survives green (which is exactly what a prior review pass caught here).
    Scope, stated precisely: it compares (case_name, key) pairs drawn from
    `expected` blocks. It cannot see a duplicate case name, an empty `expected`,
    or any field OUTSIDE `expected` — those are pinned separately by
    `test_row_identities_and_the_band_taxonomy_are_complete`.
    """
    _, rows = _load()
    by_case = {r["case"]: r for r in rows}
    checked: set[tuple[str, str]] = set()

    def declared(case: str, key: str):
        """Read a declared value, recording that it was actually asserted."""
        exp = by_case[case]["expected"]
        assert key in exp, f"{case}: declared expected key {key!r} is missing"
        checked.add((case, key))
        return exp[key]

    # ── the SOURCE cases' D10 topology and identity ───────────────────────
    for r in _source_rows(rows):
        case = r["case"]
        assert declared(case, "node_label") == "Source", "D10: a document is a :Source"
        assert declared(case, "identity_key") == "url", "D10: identity is the url"
        assert declared(case, "key_value") == r["input"]["url"], (
            "the resolved key must BE the source's url"
        )
        assert declared(case, "replay_key_resolves") is True

    # ── re-ingest unchanged: a no-op ──────────────────────────────────────
    unc_in = by_case["doc-reingest-unchanged"]["input"]
    assert declared("doc-reingest-unchanged", "reingest_result") == "unchanged"
    assert declared("doc-reingest-unchanged", "version_bumped") is False
    assert declared("doc-reingest-unchanged", "version_after") == unc_in["prior_version"]
    assert declared("doc-reingest-unchanged", "node_count_for_this_url") == 1
    assert declared("doc-reingest-unchanged", "new_derived_facts") == 0
    assert declared("doc-reingest-unchanged", "contentHash_after") == unc_in["contentHash"], (
        "an unchanged re-ingest must leave the SAME hash"
    )
    assert unc_in["contentHash"] == unc_in["reingested_hash"], (
        "the unchanged case must actually re-ingest the same hash"
    )

    # ── re-ingest changed: version +1, stale, additive supersession ───────
    chg_in = by_case["doc-reingest-changed"]["input"]
    assert declared("doc-reingest-changed", "version_bumped") is True
    assert declared("doc-reingest-changed", "version_after") == chg_in["prior_version"] + 1, (
        "version bumps by exactly 1"
    )
    assert declared("doc-reingest-changed", "node_count_for_this_url") == 1, (
        "a change replaces nothing — still one node keyed by url"
    )
    assert declared("doc-reingest-changed", "updatedAt_changed") is True
    assert declared("doc-reingest-changed", "old_version_derived_entities") == (
        "stale (DERIVED predicate, never a stored flag)"
    ), "exact equality: a substring test is invertible ('not stale…' contains 'stale')"
    assert declared("doc-reingest-changed", "supersession") == (
        "additive via CORRECTS when re-inference produces successors — never a delete"
    ), "exact equality: 'never CORRECTS — always DELETE' would pass a substring test"
    assert declared("doc-reingest-changed", "history_location") == (
        "the journal, not the graph"
    ), "exact equality: 'in the graph; the journal is never consulted' would pass a substring test"
    assert chg_in["prior_hash"] != chg_in["reingested_hash"], "the changed case must differ"

    # ── the aboutDocument round-trip: label AND key move together ─────────
    rt_in = by_case["doc-aboutDocument-roundtrip"]["input"]
    assert declared("doc-aboutDocument-roundtrip", "resolved_target_label") == "Source"
    assert declared("doc-aboutDocument-roundtrip", "resolved_by") == "url"
    assert declared("doc-aboutDocument-roundtrip", "resolved_key_value") == rt_in["target"]["url"], (
        "the resolved key must BE the source url — this is the failure the row warns about"
    )
    assert declared("doc-aboutDocument-roundtrip", "pass2b_rebuild_resolves_same_node") is True
    assert declared("doc-aboutDocument-roundtrip", "mis_pointed") is False
    assert rt_in["old_key"] == "coalesce(title, name)", (
        "pin the EXACT old key — `startswith('coalesce')` would accept a different one"
    )
    assert rt_in["new_key"] == declared("doc-aboutDocument-roundtrip", "resolved_by"), (
        "CROSS-FIELD: the key the row says it moves TO must be the key it says it "
        "resolves BY — otherwise the row's headline invariant is unpinned"
    )
    assert rt_in["target"]["url"] == declared("doc-aboutDocument-roundtrip", "resolved_key_value"), (
        "CROSS-FIELD: the resolved key value must BE the target's url"
    )
    assert rt_in["old_key"] != rt_in["new_key"], (
        "the label and the replay key must MOVE TOGETHER — that is the whole step"
    )
    assert rt_in["live_edge_count_in_production"] == 0, (
        "the round-trip exists BECAUSE production has no edges to test against"
    )

    # ── the completeness guard — over every row's declared `expected` ─────
    declared_keys = {(r["case"], k) for r in rows for k in r["expected"]}
    unasserted = declared_keys - checked
    assert not unasserted, (
        f"declared but never asserted: {sorted(unasserted)} — either assert the key "
        f"or delete it from the fixture; an unasserted declaration is prose"
    )


def test_row_identities_and_the_band_taxonomy_are_complete() -> None:
    """Guard the fixture's STRUCTURE, which the (case,key) completeness guard misses.

    The `declared()` guard compares `(case_name, key)` pairs, so it cannot see a
    duplicate case name, or any field outside `expected`. A prior pass
    demonstrated that surviving green. This pins the structure, the band
    taxonomy, and the two classification AXES (Q2 — `sourceKind` is *what kind
    of source*, `documentKind` is *what genre*; they are not interchangeable).
    """
    head, rows = _load()
    cases = [r["case"] for r in rows]

    # unique row identity — otherwise the (case,key) guard is ambiguous
    assert len(cases) == len(set(cases)), f"duplicate case names: {sorted(cases)}"

    # and the case INVENTORY is pinned exactly. Uniqueness catches a duplicate
    # but NOT a deletion — and a deleted row takes its assertions with it,
    # leaving the suite green over a smaller fixture. A review pass demonstrated
    # both `transcript-source` and `doc-with-genre` deleted with 9/9 green.
    assert set(cases) == set(BAND_BY_CASE), (
        f"case inventory changed: {sorted(set(cases))}"
    )

    # every row sits in the band pinned to it BY NAME — a swap between two rows
    # leaves the band SET unchanged
    for r in rows:
        assert r["band"] == BAND_BY_CASE[r["case"]], (
            f"{r['case']}: band must be {BAND_BY_CASE[r['case']]!r}, got {r['band']!r}"
        )

    # the band taxonomy is closed, and every band is populated
    bands = {r["band"] for r in rows}
    known = {"document-source", "session-source", "idempotency",
             "versioning", "replay-identity"}
    assert bands <= known, f"unknown band(s): {sorted(bands - known)}"
    assert bands == known, f"band(s) with no case: {sorted(known - bands)}"

    # the SOURCE rows are pinned by name, and each band must MATCH the row's
    # sourceKind — otherwise a document could be filed under `session-source`
    srcs = _source_rows(rows)
    assert {r["case"] for r in srcs} == SOURCE_CASES, (
        f"the source-row set changed: {sorted(r['case'] for r in srcs)} — a row that "
        f"leaves it escapes every graph-backed check"
    )
    for r in srcs:
        sk = r["input"]["sourceKind"]
        assert sk in BAND_BY_SOURCE_KIND, f"{r['case']}: unexpected sourceKind {sk!r}"
        assert r["band"] == BAND_BY_SOURCE_KIND[sk], (
            f"{r['case']}: sourceKind={sk!r} must sit in band "
            f"{BAND_BY_SOURCE_KIND[sk]!r}, not {r['band']!r}"
        )

    # ── Q2: the two axes are DISJOINT vocabularies ────────────────────────
    # `sourceKind` answers *what kind of source* and `documentKind` answers
    # *what genre*; the ontology's whole point is that they are different
    # questions. A value moving between them is the category error Q2 refuses,
    # and it would be invisible to every count-based guard above.
    used_kinds = {r["input"]["sourceKind"] for r in rows if r["input"].get("sourceKind")}
    declared_source_kinds = set(head["__vocabulary_grounds__"]["sourceKind_seen_in_production"]) \
        | set(head["__vocabulary_grounds__"]["sourceKind_declared_but_unused"])
    declared_genres = set(head["__vocabulary_grounds__"]["documentKind"])
    assert used_kinds <= declared_source_kinds, (
        f"rows use sourceKind(s) absent from the declared vocabulary: "
        f"{sorted(used_kinds - declared_source_kinds)}"
    )
    overlap = declared_source_kinds & declared_genres
    assert not overlap, (
        f"{sorted(overlap)} is declared on BOTH axes — sourceKind (what kind of "
        f"source) and documentKind (what genre) are different questions (Q2, "
        f"#5013) and a value must not answer both"
    )
    used_genres = {r["input"].get("documentKind") for r in rows} - {None}
    assert used_genres <= declared_genres, (
        f"rows use documentKind(s) absent from the declared vocabulary: "
        f"{sorted(used_genres - declared_genres)}"
    )

    # the header schema literal is pinned
    assert head["__schema__"] == (
        "case/band/input/expected/why_this_case/label/reason/source"
    )


# ── Job 2: the declared facts, measured on a REAL graph ─────────────────────

@pytest.fixture
def gold_graph(shared_embedded_db):
    """Write the fixture's :Source rows into an embedded FalkorDB."""
    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK(shared_embedded_db, namespace="gold")
    g = sdk._get_proj().g
    _, rows = _load()
    g.query("MATCH (n) DETACH DELETE n")
    for r in _source_rows(rows):
        inp = r["input"]
        g.query(
            "CREATE (s:Source {url: $url, sourceKind: $sk, documentKind: $dk, "
            "               contentHash: $h, version: $v, title: $t})",
            {"url": inp["url"], "sk": inp.get("sourceKind"),
             "dk": inp.get("documentKind"), "h": inp.get("contentHash"),
             "v": inp.get("version"), "t": inp.get("title")},
        )
    return sdk, g, rows


def _one(g, url: str) -> dict:
    """The single node for `url`, with the fields the fixture declares about it."""
    rows = g.query(
        "MATCH (src:Source {url: $url}) "
        "RETURN src.url, src.sourceKind, src.documentKind",
        {"url": url},
    ).result_set
    assert len(rows) == 1, f"{url}: expected exactly one :Source node, got {len(rows)}"
    return {"url": rows[0][0], "sourceKind": rows[0][1], "documentKind": rows[0][2]}


def test_the_graph_half_measures_every_declared_source_fact(gold_graph) -> None:
    """The proof this fixture exists for.

    Production cannot supply this population (measured all-`agentSession`), so a
    green gate there proves nothing about documents. Here the fixture
    guarantees a non-zero, CONTRASTED population — a document with and without
    a genre, a session with and without one — and every fact the fixture
    declares about a node is read back off the real graph.
    """
    _, g, rows = gold_graph
    srcs = _source_rows(rows)
    assert {r["case"] for r in srcs} == SOURCE_CASES, "fixture selection drifted"

    total = int(g.query("MATCH (src:Source) RETURN count(src)").result_set[0][0])
    assert total == len(srcs) > 0, (
        f"fixture did not load: {total} nodes for {len(srcs)} declared sources"
    )

    for r in srcs:
        inp, exp = r["input"], r["expected"]
        node = _one(g, inp["url"])

        # identity — the replay key is `url`, and it resolves (Trap 1: a
        # retargeted label with a stale key resolves to NOTHING, in silence)
        assert exp["identity_key"] == "url"
        assert node["url"] == exp["key_value"] == inp["url"], (
            f"{r['case']}: the declared key value is not the url the node carries"
        )
        assert exp["replay_key_resolves"] is True

        # the two axes survive the write, independently
        assert node["sourceKind"] == inp.get("sourceKind"), (
            f"{r['case']}: sourceKind {node['sourceKind']!r} != declared "
            f"{inp.get('sourceKind')!r} — the axis is not a genre"
        )
        assert node["documentKind"] == inp.get("documentKind"), (
            f"{r['case']}: documentKind {node['documentKind']!r} != declared "
            f"{inp.get('documentKind')!r} — NULL genre is a VALID document shape, "
            f"not a missing field"
        )

    # the contrast pairs really contrast: a genre-absent document and a
    # genre-carrying session both exist in the population
    genres = {(r["input"].get("sourceKind"), r["input"].get("documentKind"))
              for r in srcs}
    assert ("document", None) in genres and ("document", "brief") in genres, (
        f"the document genre contrast is gone: {sorted(genres)}"
    )
    assert ("agentSession", None) in genres and ("agentSession", "transcript") in genres, (
        f"the session genre contrast is gone: {sorted(genres)}"
    )


def test_no_document_label_is_written_by_the_fixture(gold_graph) -> None:
    """D10: the `:Document` label is RETIRED, and the fixture must not revive it.

    The fixture is a gold target for the D10 topology; if it ever declares or
    loads a `:Document` node it would be re-asserting the model D10 retired
    (PR #5127) — and a lane copying it would write the label back into the
    extractor.
    """
    _, g, rows = gold_graph
    docs = int(g.query("MATCH (d:Document) RETURN count(d)").result_set[0][0])
    assert docs == 0, "the fixture wrote a :Document node — D10 retired that label"

    for r in rows:
        exp = r.get("expected", {})
        for key in ("node_label", "resolved_target_label"):
            if key in exp:
                assert exp[key] == "Source", (
                    f"{r['case']}: {key} is {exp[key]!r} — D10 makes a document a :Source"
                )

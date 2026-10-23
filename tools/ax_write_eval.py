#!/usr/bin/env python3
"""The write-pipeline eval instrument for `how-to-use-tortoise` (#9675).

WHAT THIS MEASURES, and why it is mechanical
--------------------------------------------
Issue #9675 requires that the AX document be **derived from measured agent
failures**, not written from imagination (Step 1: "run ten representative
inputs against an agent with the tools and no skill, recording per input:
which layer each item was written as, which kinds, which edges, whether a
mitigation was used at all, whether a source was attached"). Step 3's
acceptance is the same measurement re-run: "the acceptance is the delta, per
input, not a review verdict".

So the record must come from the GRAPH the agent wrote, never from the agent's
own summary of what it wrote. An agent that mislabels a Subject as a Point is
not a reliable narrator of that fact — the label is read off the node.

Two subcommands:

``record --db <path> --out <json>``
    Read a graph (embedded FalkorDBLite db path, or a ``docker://`` URI) and
    write the structural record: every node with its LABEL (the layer), its
    kind property, the operator points and their op_type, every edge with its
    relation and endpoints, and the Sources. Nothing is scored here.

``score --db <path> --gold <inputs.json> --id <input id> [--out <dir>]``
    Record, then compare against the FROZEN gold for that input, emitting
    per-item findings. The gold is frozen before any run
    (``eval/how-to-use-tortoise/step1/inputs.json``), so the delta cannot be
    retrofitted to whatever the agent happened to do.

    ``--out`` names a DIRECTORY and writes TWO files, because a verdict without
    its evidence cannot be audited: ``score.json`` (the per-item verdicts) and
    ``graph.json`` (the structural record it was read from — nodes, labels,
    kinds, edges). The committed run directories hold both.

The matching is deliberately dumb (token overlap over the node's name/content)
because a clever matcher would hide the very failure being measured: a
"Northwind Logistics Ltd" that became a SECOND node must show as a duplicate,
not be silently unified with the first by a fuzzy matcher.

Usage:
    uv run python tools/ax_write_eval.py record --db /tmp/eval/portfolio_update.db
    uv run python tools/ax_write_eval.py score  --db /tmp/eval/portfolio_update.db \
        --gold eval/how-to-use-tortoise/step1/inputs.json --id portfolio_update
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GOLD = REPO_ROOT / "eval" / "how-to-use-tortoise" / "step1" / "inputs.json"

# The four epistemic/semantic labels, plus Source (provenance, §4.6). Anything
# else on a node (`Meta`, `Session`, …) is infrastructure and is NOT scored.
LAYERS = ("Point", "Subject", "Object", "Event", "Source")

# Kind property per label — §4.1 pointKind, §4.2 subjectKind, §4.3 objectKind,
# §4.5 eventKind, §4.6 sourceKind. A layer whose kind property is absent is a
# finding in its own right ("a kind nothing declares is a kind nothing routes
# to", #9675 step 2).
KIND_PROP = {
    "Point": "pointKind",
    "Subject": "subjectKind",
    "Object": "objectKind",
    "Event": "eventKind",
    "Source": "sourceKind",
}

# ── the declared vocabularies ─────────────────────────────────────────────
#
# A kind is judged against what the product ACTUALLY declares, at three levels,
# and the three are NOT the same finding:
#
#   CORE        — declared by an ONTOLOGY §5 core set (`CANONICAL_*`, imported).
#   PACK        — declared by a loaded expansion pack (bare or namespaced —
#                 `disbursement` and `venture:disbursement` are one declaration,
#                 and the packs are the product's own extension point).
#   UNDECLARED  — nothing declares it, so nothing routes to, finds or classifies
#                 it. This is the failure #9675 names ("a kind nothing declares
#                 is a kind nothing routes to").
#
# Re-spelling the core sets here would let the instrument drift from the product,
# and IGNORING the packs would manufacture failures out of writes that followed a
# declared vocabulary — so the core sets are imported and the pack registry is
# loaded, and the active pack set is recorded beside every verdict.

_PACK_CACHE: dict | None = None


def _pack_vocab() -> dict[str, set[str]]:
    """Every kind the LOADED packs declare, per layer, namespaced and bare.

    Cached: the registry parses six manifests and the eval scores ten graphs.
    A registry that cannot load is NOT swallowed — an empty pack vocabulary would
    report every pack-following write as undeclared, which is the false failure
    this function exists to prevent.
    """
    global _PACK_CACHE
    if _PACK_CACHE is not None:
        return _PACK_CACHE
    from tortoise.pack_registry import PackRegistry

    registry = PackRegistry(REPO_ROOT / "packs")
    registry.load_all()
    by_layer: dict[str, set[str]] = {layer: set() for layer in LAYERS}
    # The manifest is a dataclass, not a dict: the declared kind lists are
    # read off its own attributes (`object_kinds` / `event_kinds` / …), so a
    # rename in the manifest schema fails loudly here instead of quietly
    # reporting every pack kind as undeclared.
    # The manifest schema declares object/event/point kinds (plus a document
    # `document_kinds` genre axis it does not use for nodes); it has NO
    # subject-kind or source-kind list, so those two layers simply have no pack
    # axis — stated here rather than silently asking for an attribute that does
    # not exist.
    attrs = {
        "Object": "object_kinds",
        "Event": "event_kinds",
        "Point": "point_kinds",
    }
    for pack in registry.packs.values():
        ns = pack.namespace
        for layer, attr in attrs.items():
            declared = getattr(pack, attr, None)
            if not declared:
                continue
            for kind in declared:
                by_layer[layer].add(str(kind))
                by_layer[layer].add(f"{ns}:{kind}")
        # `kindDefs` declare the same kinds again (with lifecycle), and are the
        # only declaration for a kind that has no list membership.
        for kind_def in getattr(pack, "kind_defs", None) or {}:
            name = kind_def.get("name") if isinstance(kind_def, dict) else None
            label = (kind_def.get("layer") or kind_def.get("type")) if isinstance(kind_def, dict) else None
            if not name:
                continue
            for layer in LAYERS:
                if label and label.lower() == layer.lower():
                    by_layer[layer].update({str(name), f"{ns}:{name}"})
    _PACK_CACHE = {"by_layer": by_layer, "packs": sorted(registry.packs)}
    return _PACK_CACHE


def _vocab() -> dict[str, frozenset[str]]:
    """The CORE declared vocabularies, imported — never re-spelled here."""
    from tortoise.pack_registry import (
        CANONICAL_EVENT_KINDS,
        CANONICAL_OBJECT_KINDS,
        CANONICAL_POINT_KINDS,
        registered_source_types,
    )
    from tortoise.source_credibility import SOURCE_KIND_DEFAULTS
    from tortoise.subject_binding import subject_kind_names

    # Subject: the product's own declared set (`subject_kind_names()`), because
    # the canonical spelling of a §5 Subject kind is `core:team` and the bare
    # form is an accepted alias (`subject_binding.is_subject_kind`). Hardcoding
    # the bare forms here reported `core:organization` — the DECLARED key — as an
    # invention.
    subject_kinds = frozenset(subject_kind_names()) | frozenset(
        kind.split(":", 1)[1] for kind in subject_kind_names() if kind.startswith("core:")
    )
    return {
        # `documentKind` is its OWN axis (genre, §5) and is deliberately not a
        # pointKind: folding `CANONICAL_DOCUMENT_KINDS` in here would bless the
        # exact confusion the research input measures.
        "Point": frozenset(CANONICAL_POINT_KINDS),
        "Object": canonical_object_kinds(CANONICAL_OBJECT_KINDS),
        "Event": frozenset(CANONICAL_EVENT_KINDS),
        "Source": registered_source_types() | frozenset(SOURCE_KIND_DEFAULTS),
        "Subject": subject_kinds,
    }


def canonical_object_kinds(kinds) -> frozenset[str]:
    """Core object kinds are matched case-insensitively (``Project`` vs ``project``).

    The §5 object vocabulary deliberately mixes cases (`Project`, `WorkItem`,
    `Problem`, `tag`), and the write path stores what the caller passed, so an
    exact-case comparison would fail a write that used the documented spelling
    with different capitalisation. The comparison set is lowercased; the finding
    still quotes the kind as written.
    """
    return frozenset(k.lower() for k in kinds)


def _kind_matches(got: str, allowed: list[str]) -> bool:
    """Does a written kind match the gold's allowed set?

    ``core:organization`` and ``organization`` are ONE kind — the namespaced
    form is the declared key and the bare form its accepted alias
    (``subject_binding.is_subject_kind``). Comparing raw strings reported the
    canonical spelling as a missed kind.
    """
    candidates = {got.lower(), got.split(":", 1)[-1].lower()}
    return bool(candidates & {a.split(":", 1)[-1].lower() for a in allowed})


def kind_status(layer: str, kind: str) -> str:
    """``CORE`` / ``PACK`` / ``UNDECLARED`` for one written kind.

    The three-way answer is the point: "wrong kind" (declared, but not the kind
    this item is) and "kind nothing declares" are different lessons for the
    document, and collapsing them teaches neither.
    """
    if not kind:
        return "NONE"
    raw = kind.strip()
    core = {k.lower() for k in _vocab().get(layer, frozenset())}
    if raw.lower() in core:
        return "CORE"
    pack_kinds = _pack_vocab()["by_layer"].get(layer, set())
    if raw in pack_kinds or raw.lower() in {p.lower() for p in pack_kinds}:
        return "PACK"
    if layer == "Subject":
        from tortoise.subject_binding import is_subject_kind

        if is_subject_kind(raw):
            return "CORE"
    return "UNDECLARED"


_STOP = {
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "we", "our",
    "is", "are", "was", "were", "be", "been", "with", "by", "at", "as", "it",
    "its", "that", "this", "from", "into", "over", "after", "before", "has",
    "have", "had", "will", "would", "should", "must", "not", "no", "their",
    "they", "them", "she", "he", "his", "her", "was", "were", "only", "still",
}


# ── graph read ─────────────────────────────────────────────────────────────


def _open(uri_or_path: str):
    """Open the graph through the product SDK — the same seam the eval agent used.

    Accepts an embedded db path (the eval lane) or a ``docker://`` URI. The env
    is set rather than passed so the SDK's own resolver decides, exactly as it
    does for a real writer.
    """
    if uri_or_path.startswith(("docker://", "redis://", "rediss://")):
        os.environ["TORTOISE_DB_URI"] = uri_or_path
        os.environ.pop("TORTOISE_DB_PATH", None)
    else:
        os.environ.pop("TORTOISE_DB_URI", None)
        os.environ["TORTOISE_DB_PATH"] = uri_or_path

    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK()
    return sdk, sdk._get_proj()


def _rows(proj, cypher: str) -> list[list]:
    return list(proj.query(cypher).result_set)


def record(uri_or_path: str) -> dict:
    """Read the graph and return the structural record (no scoring)."""
    sdk, proj = _open(uri_or_path)
    # One query, all the props we need; FalkorDB returns positional rows.
    rows = _rows(
        proj,
        "MATCH (n) RETURN labels(n), "
        "coalesce(n.name, n.content, n.title, n.url, ''), "
        "coalesce(n.pointKind, n.subjectKind, n.objectKind, n.eventKind, "
        "n.sourceKind, ''), n.is_operator, coalesce(n.op_type, ''), "
        "coalesce(n.status, ''), coalesce(n.url, ''), coalesce(n.id, n.eventId, '')",
    )
    nodes = []
    for labels, text, kind, is_op, op_type, status, url, node_id in rows:
        labels = labels if isinstance(labels, list) else [labels]
        layer = next((label for label in LAYERS if label in labels), None)
        if layer is None:
            continue  # Meta / Session / registry infra — not a written knowledge node
        nodes.append(
            {
                "layer": layer,
                "labels": labels,
                "text": (text or "").strip(),
                "kind": (kind or "").strip(),
                "is_operator": bool(is_op),
                "op_type": (op_type or "").strip(),
                "status": (status or "").strip(),
                "url": (url or "").strip(),
                "id": str(node_id or "").strip(),
            }
        )

    edges = []
    for rel, src, dst, sl, dl, a_id, b_id in _rows(
        proj,
        "MATCH (a)-[e]->(b) RETURN type(e), coalesce(a.name, a.content, ''), "
        "coalesce(b.name, b.content, ''), labels(a), labels(b), "
        "coalesce(a.id, a.eventId, ''), coalesce(b.id, b.eventId, '')",
    ):
        sl = sl if isinstance(sl, list) else [sl]
        dl = dl if isinstance(dl, list) else [dl]
        edges.append(
            {
                "relation": rel,
                "from": (src or "").strip(),
                "to": (dst or "").strip(),
                "from_layer": next((lab for lab in LAYERS if lab in sl), None),
                "to_layer": next((lab for lab in LAYERS if lab in dl), None),
                "from_id": str(a_id or "").strip(),
                "to_id": str(b_id or "").strip(),
            }
        )
    sdk.close()

    operator_ids = {n["id"] for n in nodes if n["is_operator"] and n["id"]}
    counts: dict[str, int] = {layer: 0 for layer in LAYERS}
    for node in nodes:
        counts[node["layer"]] += 1
    return {
        "nodes": nodes,
        "edges": edges,
        "counts": counts,
        "operators": [n for n in nodes if n["is_operator"]],
        "sources": [n for n in nodes if n["layer"] == "Source"],
        "relations": sorted({e["relation"] for e in edges}),
        # A mitigation is the relevance dampener on an OPERATOR: `mitigate_operator`
        # writes `mitigated_by`, and an INVALIDATES edge is the same lane under its
        # epistemic name. Both are "relevance attacked", never "truth attacked" —
        # AND one endpoint must be the operator node, or the count says nothing
        # about relevance (a mitigation edge between two plain claims would satisfy
        # a bare relation-name test while damping nothing).
        "mitigations": [
            e
            for e in edges
            if ("MITIGAT" in e["relation"].upper() or "INVALID" in e["relation"].upper())
            and (e["from_id"] in operator_ids or e["to_id"] in operator_ids)
        ],
    }


# ── scoring ────────────────────────────────────────────────────────────────


_SUFFIXES = ("ings", "ing", "ied", "ies", "ed", "es", "al", "ly", "s")


def _stem(word: str) -> str:
    """Crude stem, so `join`/`joins`/`joined` and `renew`/`renewal` compare equal.

    Without it the matcher reads "joined Northwind Logistics" (item) against an
    Event named "… joins Northwind Logistics …" as a MISS and reports a layer
    failure on a write that was right — measured on three committed runs. A real
    stemmer is a dependency this eval does not need; the failure mode it removes
    is a plural/tense mismatch, and that is what this covers.
    """
    for suffix in _SUFFIXES:
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def _tokens(text: str) -> set[str]:
    """Significant tokens of a node's text.

    Everything that is not alphanumeric (or ``$``/``%``) SEPARATES tokens, so
    ``frozen.``, ``frozen`` and ``(frozen)`` are one token and ``sub-5-minute``
    splits exactly as ``sub 5 minute`` does. Leaving punctuation attached
    silently makes an expectation whose word ends a sentence — or is hyphenated
    in the node — score as a miss (both were measured on committed runs).
    """
    words = re.findall(r"[a-z0-9$%]+", text.lower())
    return {_stem(w) for w in words if len(w) >= 2 and w not in _STOP}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _match(
    expect_item: str, nodes: list[dict], want_layer: str
) -> tuple[dict | None, float, list[dict]]:
    """Find the ONE node that best answers the expected item.

    Returns ``(best, score, duplicates)``. The best node is the single closest
    candidate, ranked by: exact name equality, then a node whose text CONTAINS
    every significant token of the expectation (the tighter node wins — a
    300-character Point that happens to mention "Dana" is not the node that
    represents Dana), then partial overlap at half weight.

    Deliberately ONE best, not "every node that mentions it": judging a layer
    off the union of every node that mentions a name is how a matcher
    manufactures layer-confusion noise out of correct writes. A fuzzy matcher
    would also hide the very failure being measured — a ``Northwind Logistics
    Ltd`` that became a SECOND node must read as a duplicate.
    """
    want = _tokens(expect_item)
    if not want:
        return None, 0.0, []
    scored: list[tuple[float, dict]] = []
    for node in nodes:
        have = _tokens(node["text"])
        if not have:
            continue
        if want <= have:
            exact = _norm(node["text"]) == _norm(expect_item)
            # Containment scores ABOVE any partial overlap but below an exact
            # name match: a 20-word Point that reports the figure is a real
            # candidate for the figure, it just has to lose to a node whose
            # whole job is that item.
            scored.append((1.0 if exact else 0.6 + 0.4 * len(want) / len(have), node))
        else:
            hit = want & have
            if hit:
                scored.append((0.5 * len(hit) / len(want), node))
    scored.sort(key=lambda pair: -pair[0])
    if not scored or scored[0][0] < 0.5:
        return None, scored[0][0] if scored else 0.0, []
    # SAME-LAYER PREFERENCE — WITH A FLOOR. Layer confusion means the item is
    # represented on a DIFFERENT layer and NOT on the expected one. If a node on
    # the expected layer ALSO represents it (the claim AND its Object both name
    # the figure), the item was filed correctly and the duplicate representation
    # is the finding.
    #
    # The floor is load-bearing: without it a same-layer candidate scoring 0.08
    # (one token of six, on a node that merely mentions the subject) outranks a
    # different-layer containment match at 0.83, and the instrument then reports
    # an unrelated node as the item — accusing a node the agent never wrote the
    # item as, and losing the real layer failure underneath. Measured on two
    # committed runs before this guard existed.
    same = [
        pair
        for pair in scored
        if pair[1]["layer"].lower() == want_layer.lower() and pair[0] >= 0.5
    ]
    if same and (scored[0][0] < 0.6 or same[0][0] >= scored[0][0] - 0.2):
        scored = same
    best, best_score = scored[0][1], scored[0][0]
    # Duplicates: another node carrying the SAME identity (one name is the
    # other, or one is a prefix of the other — "Ltd"/"Inc"/"Plc" spellings).
    bnorm = _norm(best["text"])
    dups = [
        node
        for _, node in scored[1:]
        if node is not best
        and node["layer"] == best["layer"]
        and (
            _norm(node["text"]) == bnorm
            or _norm(node["text"]).startswith(bnorm)
            or bnorm.startswith(_norm(node["text"]))
        )
    ]
    return best, best_score, dups


def _expected_layer_twin(item: str, nodes: list[dict], layer: str) -> dict | None:
    """A node on the EXPECTED layer that genuinely carries this item, if any.

    The bar is two thirds of the item's stems (and at least two of them), not a
    third: "the expected layer also names it" is a claim that the graph
    DISPROVES an absence, so a node that merely shares a couple of topic words
    must not qualify. Measured: at a third, the meeting Event "Pricing &
    self-serve decision meeting" qualified for the item "killing the self-serve
    tier" (2 of 4 stems) and turned a real absence into a weaker finding.

    Ties break toward the node that is not superseded and then toward the
    tighter text — on the product-spec input both plan versions name the ship
    date, and the REPLACED plan is not the one holding it.
    """
    want = _tokens(item)
    if len(want) < 2:
        return None
    best_key: tuple | None = None
    best_node: dict | None = None
    for node in nodes:
        if node["layer"].lower() != layer.lower() or node["is_operator"]:
            continue
        hit = len(want & _tokens(node["text"]))
        ratio = hit / len(want)
        if hit < 2 or ratio < 0.6:
            continue
        key = (ratio, node["status"].lower() != "superseded", -len(node["text"]))
        if best_key is None or key > best_key:
            best_key, best_node = key, node
    return best_node


def score(record_doc: dict, gold: dict, input_id: str) -> dict:
    entry = next((i for i in gold["inputs"] if i["id"] == input_id), None)
    if entry is None:
        raise SystemExit(f"no gold entry with id {input_id!r} in the gold file")
    nodes = record_doc["nodes"]
    findings: list[dict] = []
    item_rows: list[dict] = []

    def add(sev: str, item: str, what: str, fix: str) -> None:
        findings.append(
            {"severity": sev, "item": item, "finding": what, "fix": fix}
        )

    for exp in entry.get("expect", []):
        item = exp["item"]
        layer = exp["layer"]
        best, best_score, dups = _match(item, nodes, layer)
        fuzzy = best is not None and best_score < 0.75

        if layer == "edge":
            rels = [r.lower() for r in exp.get("kind", [])]
            got = [e for e in record_doc["edges"] if e["relation"].lower() in rels]
            if not got:
                add(
                    "P0",
                    item,
                    f"expected an EDGE with relation in {rels} — none written; "
                    f"relations present: {record_doc['relations']}",
                    "write the relationship as a graph edge (§3), not as a node "
                    "or a string property",
                )
                item_rows.append(
                    {"item": item, "expected": "edge", "written": None, "verdict": "MISSING"}
                )
            else:
                item_rows.append(
                    {
                        "item": item,
                        "expected": f"edge {got[0]['relation']}",
                        "written": f"{got[0]['from'][:40]} -[{got[0]['relation']}]-> {got[0]['to'][:40]}",
                        "verdict": "OK",
                    }
                )
            continue

        if best is None:
            add(
                "P0",
                item,
                f"expected {layer} — no node represents it (best overlap {best_score:.2f})",
                "the item was absorbed into prose or written under a name nothing "
                "can find",
            )
            item_rows.append(
                {"item": item, "expected": layer, "written": "(nothing)", "verdict": "MISSING"}
            )
            continue

        if best["layer"].lower() != layer.lower():
            # TWO HOMES vs ONE WRONG HOME. If the expected layer ALSO carries a
            # node naming this item, the item was not filed on the wrong layer —
            # it was filed TWICE, and neither copy answers alone (the Object can
            # never be "what is true now" while a Point holds the same fact, and
            # vice versa). That is a real finding, but a weaker one than "the
            # occurrence has no Event at all", and reporting it as an absence
            # accuses the agent of something its graph disproves.
            twin = _expected_layer_twin(item, nodes, layer)
            if twin is not None:
                add(
                    "P1",
                    item,
                    f"two homes: {best['layer']} {best['text'][:50]!r} holds this "
                    f"item, and so does {layer} {twin['text'][:50]!r} — one fact, two "
                    f"layers, so neither answers alone",
                    f"keep the {layer} node and express the {best['layer']} reading "
                    f"as a claim ABOUT it (§3 edges), never as a second home",
                )
                item_rows.append(
                    {
                        "item": item,
                        "expected": layer,
                        "written": f"BOTH {layer}:{twin['kind'] or '(none)'} and "
                        f"{best['layer']}:{best['kind'] or '(none)'}",
                        "node": best["text"][:80],
                        "verdict": "TWO-HOMES",
                    }
                )
                continue
            add(
                "P0",
                item,
                f"LAYER confusion: expected {layer}, written as {best['layer']} "
                f"({best['text'][:70]!r}) — {exp.get('why','')}",
                f"re-file as {layer}; a wrong layer cannot be rescued downstream "
                f"(kind_classifier partitions by type before choosing a kind)",
            )
            item_rows.append(
                {
                    "item": item,
                    "expected": layer,
                    "written": f"{best['layer']}:{best['kind'] or '(none)'}",
                    "node": best["text"][:80],
                    "verdict": "LAYER",
                }
            )
            continue

        allowed = [k.lower() for k in exp.get("kind", [])]
        got_kind = (best["kind"] or "").lower()
        status = kind_status(best["layer"], best["kind"])
        if status == "UNDECLARED":
            findings.append(
                {
                    "severity": "P1",
                    "item": item,
                    "layer": best["layer"],
                    "kind": got_kind,
                    "finding": (
                        f"UNDECLARED kind {got_kind!r} on a {best['layer']} "
                        f"({best['text'][:50]!r}) — neither a core §5 kind nor one "
                        f"any loaded pack declares; nothing routes to, finds or "
                        f"classifies it"
                    ),
                    "fix": (
                        f"use a declared {best['layer']} kind (§5 core, or a pack "
                        f"kind spelled as the pack declares it)"
                    ),
                }
            )
            verdict = "KIND-UNDECLARED"
        elif allowed and not _kind_matches(got_kind, allowed):
            add(
                "P1",
                item,
                f"kind outside the frozen set: expected one of {exp['kind']}, "
                f"written {got_kind!r} ({best['text'][:60]!r}) — declared, but "
                f"not the kind this item is",
                "pick a kind the active vocabulary declares for this layer (§5)",
            )
            verdict = "KIND"
        else:
            verdict = "OK"
        if dups:
            add(
                "P1",
                item,
                f"duplicate nodes for one identity: {len(dups) + 1} "
                f"({[n['text'][:40] for n in [best, *dups]]})",
                "a re-mention must UPDATE the existing node, never mint a second one",
            )
            verdict = verdict if verdict != "OK" else "DUP"
        item_rows.append(
            {
                "item": item,
                "expected": f"{layer}:{'|'.join(exp.get('kind', []))}",
                "written": f"{best['layer']}:{best['kind'] or '(none)'}",
                "node": best["text"][:80],
                "fuzzy": fuzzy,
                "verdict": verdict,
            }
        )

    # Provenance — §4.6. A Source node is the floor; the edge is the standard.
    if entry.get("expect_source"):
        if not record_doc["sources"]:
            add(
                "P0",
                "(provenance)",
                "no Source node written; every claim is uncheckable",
                "write the source (§4.6) and tie claims to it with extractedFrom",
            )
        else:
            # One endpoint must BE the Source. A bare relation-name test passes on
            # any `references`/`wasDerivedFrom` edge between two claim nodes, which
            # says nothing about provenance.
            provenance_edges = [
                e
                for e in record_doc["edges"]
                if e["relation"].lower()
                in {"extractedfrom", "citedfrom", "sourcedfrom", "references", "wasderivedfrom"}
                and "Source" in (e["from_layer"], e["to_layer"])
            ]
            if not provenance_edges:
                add(
                    "P1",
                    "(provenance)",
                    f"{len(record_doc['sources'])} Source node(s) but ZERO claim→source "
                    f"edges (relations present: {record_doc['relations']})",
                    "attach each extracted claim to its source; a Source with no edge "
                    "is a scrapbook entry, not provenance",
                )

    # Truth vs relevance — §3.1 / step 5 of the write pipeline.
    nand_present = any(
        (op.get("op_type") or "").upper() == "NAND" for op in record_doc["operators"]
    )
    if entry.get("expect_nand") is False and nand_present:
        add(
            "P1",
            "(truth-vs-relevance)",
            "a NAND was written where nothing contradicts — a contradiction "
            "operator on a claim that was never attacked computes a wrong "
            "confidence, not a missing one",
            "NAND is for a claim that is WRONG; anything else is either plain "
            "support (IMPL) or a relevance question (mitigation on the operator)",
        )
    if entry.get("expect_nand") and not any(
        (op.get("op_type") or "").upper() == "NAND" for op in record_doc["operators"]
    ):
        add(
            "P0",
            "(truth-vs-relevance)",
            "a contradicting claim was filed with NO NAND against the attacked "
            "Point (operators present: "
            f"{[o['op_type'] for o in record_doc['operators']] or 'none'}; mitigations: "
            f"{len(record_doc['mitigations'])}) — truth was not attacked",
            "a contradiction attacks the TRUTH of the Point (NAND); mitigation "
            "dampens an operator's RELEVANCE (w_eff = w x (1-strength))",
        )
    # Three-state, deliberately: ABSENT means "not scored" (most inputs neither
    # need nor forbid a dampener — the research input legitimately mitigates an
    # under-evidenced inference, and a boilerplate `false` on every input turned
    # that sanctioned write into a P0). Only an explicit `false` forbids one.
    if entry.get("expect_mitigation") is False and record_doc["mitigations"]:
        add(
            "P0",
            "(truth-vs-relevance)",
            f"{len(record_doc['mitigations'])} INVALIDATES/mitigation edge(s) written on an "
            f"input that needs none — relevance dampening applied where truth is at issue",
            "do not dampen an operator when the claim itself is wrong; NAND the Point",
        )
    if entry.get("expect_supersede") and not any(
        n["status"].lower() == "superseded" for n in nodes
    ):
        add(
            "P0",
            "(change)",
            f"expected supersession ({entry['expect_supersede']}) — no node carries "
            f"status=superseded, so 'what did we believe then' is unanswerable",
            "supersede with edge transfer (§4.1 supersede_point), never edit in place",
        )

    # Duplicate identity — the gold's own measurable for the duplicate input.
    dup_rule = entry.get("duplicate_expected")
    if dup_rule:
        target = _norm(dup_rule["of"])
        # A second node for one identity means the NAME is duplicated — a
        # sentence that merely STARTS with the name ("Northwind Logistics
        # confirmed …") is a claim about it, not a second entity. So: same
        # normalised name, or the name plus a legal-form suffix ("Ltd", "Inc"),
        # and only on an entity layer. Without those two constraints the check
        # reports every claim mentioning the company as a duplicate.
        suffixes = {
            "ltd", "limited", "inc", "incorporated", "plc", "llc", "gmbh",
            "corp", "corporation", "co", "sa", "bv", "ag",
        }

        def _same_identity(text: str) -> bool:
            norm = _norm(text)
            if not norm:
                return False
            if norm == target:
                return True
            for form in (target, norm):
                other = norm if form == target else target
                if other.startswith(form + " "):
                    residual = set(other[len(form) + 1 :].split()) - suffixes
                    if not residual:
                        return True
            return False

        same_identity = [
            n for n in nodes if n["layer"] in ("Object", "Subject") and _same_identity(n["text"])
        ]
        if len(same_identity) > dup_rule["max_nodes"]:
            add(
                "P0",
                dup_rule["of"],
                f"{len(same_identity)} nodes carry one identity "
                f"({[n['text'][:40] for n in same_identity]}) — a re-mention minted "
                f"a second node, so nothing ever updates",
                "match the existing node and update it (alias on the same node)",
            )

    # Graph-wide kind census — INDEPENDENT of the gold. Score() otherwise audits
    # only the nodes the gold names, so a run can read "clean" while writing
    # undeclared kinds the gold never asked about (measured: the correction run,
    # whose two gold items both landed and whose other nodes carried undeclared
    # kinds). The census keeps "clean" honest.
    census: dict[str, list[str]] = {}
    # The census exists to catch what the GOLD did not name, so it skips the
    # nodes a gold item already resolved to (`item_rows[*]["node"]`). Keying it
    # on kind-bearing findings instead let a gold-named node — already reported
    # as a layer P0 — contribute its kind to the census, which overstated the
    # class on two inputs.
    named_texts = {
        row.get("node") for row in item_rows if row.get("node")
    }
    for node in nodes:
        if node["is_operator"]:
            continue
        if kind_status(node["layer"], node["kind"]) != "UNDECLARED":
            continue
        if node["text"][:80] in named_texts:
            continue
        census.setdefault(node["layer"], []).append(node["kind"] or "(none)")
    if census:
        add(
            "P1",
            "(kinds)",
            "graph-wide: kinds no vocabulary declares, beyond the gold items — "
            + "; ".join(f"{layer}: {sorted(set(kinds))}" for layer, kinds in census.items()),
            "every node needs a kind from the core §5 vocabulary or a loaded pack",
        )

    # Kind completeness — a node with no kind property routes nowhere.
    unkinded = [n for n in nodes if not n["kind"] and not n["is_operator"]]
    if unkinded:
        add(
            "P1",
            "(kinds)",
            f"{len(unkinded)} node(s) carry no kind property: "
            f"{[n['text'][:30] for n in unkinded][:5]}",
            "every node needs a kind from the active vocabulary (§5)",
        )

    return {
        "input": input_id,
        "cell": entry.get("cell"),
        "confuses": entry.get("confuses", []),
        "counts": record_doc["counts"],
        "item_rows": item_rows,
        "findings": findings,
        "relations": record_doc["relations"],
        "operators": [n["op_type"] or "(none)" for n in record_doc["operators"]],
        "sources": [s["url"] or s["text"] for s in record_doc["sources"]],
        "kind_census": census,
        "packs": _pack_vocab()["packs"],
        "severity_counts": {
            sev: sum(1 for f in findings if f["severity"] == sev)
            for sev in ("P0", "P1", "P2")
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    rec = sub.add_parser("record", help="write the structural record for a graph")
    rec.add_argument("--db", required=True, help="embedded db path or docker:// URI")
    rec.add_argument("--out", default=None)

    sc = sub.add_parser("score", help="score a graph against the frozen gold")
    sc.add_argument("--db", required=True)
    sc.add_argument("--gold", default=str(DEFAULT_GOLD))
    sc.add_argument("--id", required=True, help="gold input id")
    sc.add_argument(
        "--out",
        default=None,
        help="directory: writes score.json (verdicts) and graph.json (the record read)",
    )

    args = ap.parse_args(argv)
    if args.cmd == "record":
        doc = record(args.db)
        text = json.dumps(doc, indent=2)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(text + "\n")
            print(f"recorded {len(doc['nodes'])} nodes / {len(doc['edges'])} edges -> {args.out}")
        else:
            print(text)
        return 0

    gold = json.loads(Path(args.gold).read_text())
    doc = record(args.db)
    result = score(doc, gold, args.id)
    text = json.dumps(result, indent=2)
    if args.out:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "score.json").write_text(text + "\n")
        (out_dir / "graph.json").write_text(json.dumps(doc, indent=2) + "\n")
        print(f"scored {args.id}: P0={result['severity_counts']['P0']} "
              f"P1={result['severity_counts']['P1']} -> {out_dir}")
    print(text)
    return 1 if result["severity_counts"]["P0"] else 0


if __name__ == "__main__":
    sys.exit(main())

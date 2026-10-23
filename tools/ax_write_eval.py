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

``score --db <path> --gold <inputs.json> --id <input id> [--out <json>]``
    Record, then compare against the FROZEN gold for that input, emitting
    per-item findings in the P0/P1/P2 shape the review skills use. The gold is
    frozen before any run (``eval/how-to-use-tortoise/step1/inputs.json``), so
    the delta cannot be retrofitted to whatever the agent happened to do.

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

# The DECLARED vocabularies the write path validates against — imported, never
# re-spelled here, so this instrument cannot drift from the product's own kind
# sets. A kind outside every one of these is "a kind nothing declares", which
# §5 of #9675 names as a write failure in its own right (nothing routes to it).
def _vocab() -> dict[str, frozenset[str]]:
    from tortoise.pack_registry import (
        CANONICAL_DOCUMENT_KINDS,
        CANONICAL_EVENT_KINDS,
        CANONICAL_OBJECT_KINDS,
        CANONICAL_POINT_KINDS,
        registered_source_types,
    )
    from tortoise.source_credibility import SOURCE_KIND_DEFAULTS

    subject_kinds = frozenset(
        {"organization", "team", "role", "legalPerson", "naturalPerson", "other"}
    )  # ONTOLOGY §4.2/§5, the closed Subject vocabulary
    return {
        "Point": CANONICAL_POINT_KINDS,
        "Object": CANONICAL_OBJECT_KINDS,
        "Event": CANONICAL_EVENT_KINDS,
        "Source": registered_source_types() | frozenset(SOURCE_KIND_DEFAULTS),
        "Subject": subject_kinds,
    }


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
        "coalesce(n.status, ''), coalesce(n.url, '')",
    )
    nodes = []
    for labels, text, kind, is_op, op_type, status, url in rows:
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
            }
        )

    edges = []
    for rel, src, dst, sl, dl in _rows(
        proj,
        "MATCH (a)-[e]->(b) RETURN type(e), coalesce(a.name, a.content, ''), "
        "coalesce(b.name, b.content, ''), labels(a), labels(b)",
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
            }
        )
    sdk.close()

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
        # epistemic name. Both are "relevance attacked", never "truth attacked".
        "mitigations": [
            e
            for e in edges
            if "MITIGAT" in e["relation"].upper() or "INVALID" in e["relation"].upper()
        ],
    }


# ── scoring ────────────────────────────────────────────────────────────────


def _tokens(text: str) -> set[str]:
    """Significant tokens of a node's text.

    Trailing sentence punctuation is stripped — ``frozen.`` and ``frozen`` are
    the same token, and leaving the dot on silently makes every expectation
    whose word ends a sentence score as a miss.
    """
    words = re.findall(r"[a-z0-9][a-z0-9$%.\-]*", text.lower())
    out = {w.rstrip(".-") for w in words}
    return {w for w in out if len(w) >= 2 and w not in _STOP}


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
    # SAME-LAYER PREFERENCE. Layer confusion means the item is represented on a
    # DIFFERENT layer and NOT on the expected one. If a node on the expected
    # layer also mentions it (e.g. the claim AND its Object both name the
    # figure), the item was filed correctly and the duplicate representation is
    # the finding — not a layer error. Without this rule the matcher reports the
    # shorter mention as a confusion and buries the real one.
    same = [pair for pair in scored if pair[1]["layer"].lower() == want_layer.lower()]
    if same:
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
        allowed = [k.lower() for k in exp.get("kind", [])]
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
        got_kind = (best["kind"] or "(none)").lower()
        declared = _vocab()[best["layer"]]
        declared_lower = {k.lower() for k in declared}
        if got_kind and got_kind not in declared_lower:
            add(
                "P1",
                item,
                f"UNDECLARED kind {got_kind!r} on a {best['layer']} "
                f"({best['text'][:50]!r}) — no vocabulary declares it "
                f"(declared {best['layer']} kinds: {len(declared)}); nothing "
                f"routes to, finds or classifies it",
                f"use a declared {best['layer']} kind (§5 / pack kindDefs)",
            )
            verdict = "KIND-UNDECLARED"
        elif allowed and got_kind not in allowed:
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
            provenance_edges = [
                e
                for e in record_doc["edges"]
                if e["relation"].lower()
                in {"extractedfrom", "citedfrom", "sourcedfrom", "references", "derivedfrom"}
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
    sc.add_argument("--out", default=None)

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
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n")
    print(text)
    return 1 if result["severity_counts"]["P0"] else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""#9077 (E4) — is this operator-lane receipt ARTIFACT-GRADE evidence?

The epic's closing rule (#7949) is that *a child closing is a claim; the epic
is where the claim is checked against the pipeline running end to end*. #2552
and #4716 are both CLOSED, and the operator lane that satisfies them is
structurally blind to the question E4 asks: it monkeypatches
``extract_session_v2`` with a gold-derived emission, so its 15/15 grades the
WIRE and never the extractor's behaviour (the lane says so itself, verbatim,
in its module docstring).

A receipt is what turns a claim into evidence — but only if it carries the
things a reader cannot re-derive: which extractor produced the number, against
which denominator, and per edge whether the node was wired, retrievable, and
ref-remapped at commit. This checker is the gate on exactly that. It is the
difference between "15/15" as a number and "15/15, measured at <sha>, on the
real extractor, 15 edges each witheld-or-explained, endpoint refs remapped".

⛔ WHAT exit 0 MEANS — AND DOES NOT MEAN
----------------------------------------
**exit 0 = the receipt is valid evidence**, NOT "the extractor is perfect".
A RED E4 with every miss individually named is the outcome the epic needs
when the real path misses edges (#9077 is explicit: do not "fix" the
extractor to make the number green). So this tool checks:

* the denominator is the CURRENT gold's count (15), never the discarded
  4-edge one (a 4-edge denominator swung 0,1,1,1,2/4 on identical code, so it
  cannot separate a fix from LLM variance — the lane already discarded it),
  and an ``--denominator`` override may never CONTRADICT a readable gold —
  the live gold is authoritative, so an override is not a quieter gold;
* the per-edge verdicts ARE the gold's edge set, not merely the same COUNT,
  and each edge's ``expected_kind`` matches the kind the gold plants (a
  length check cannot tell "the real 15" from "any 15");
* every planted edge is either ``edge_correct`` or carries a named ``cause``
  (an unexplained miss is a receipt defect, not a result);
* every ``edge_correct`` edge ALSO ``persisted`` — the committed nodes are in
  the eventId-keyed retrieval layer, which is the half the historical 0/4
  failed structurally (``operator_counts`` was silently ``{}``);
* every ``edge_correct`` edge carries, per side, a pre- and post-commit
  endpoint ref that resolves, with ``remapped`` consistent with the #4716
  re-key it reports;
* the extractor was the REAL ``tortoise.extractor_v2.extract_session_v2`` (a
  monkeypatched seam is rejected outright);
* the run was the product (``llm``) lane — the m2 echo lane has no product
  relation extraction by construction, so its edge score is not comparable
  and is never an E4 number;
* the receipt names the sha it was measured at.

Usage::

    python tools/operator_receipt_check.py <receipt.json>
    python tools/operator_receipt_check.py <receipt.json> --json

Exit codes: ``0`` evidence is valid · ``1`` evidence is insufficient or
malformed (the failing rules are named) · ``2`` usage/environment error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # run as a script, not as a module
    sys.path.insert(0, str(REPO_ROOT))

# The denominator the corpus discarded (#2552 grew the gold 4 -> 15). Named as
# a constant so the failure message can say WHY a 4 is rejected instead of
# only that it is unexpected.
DISCARDED_DENOMINATOR = 4
PRODUCT_LANE = "llm"


def current_edge_map() -> dict[str, str] | None:
    """The committed gold's planted-operator edges: ``{id: expected_kind}``.

    One read serves BOTH bindings the checker makes — the denominator (the
    map's length) and the id/kind set a receipt's ``results`` must reproduce.
    Read separately they could disagree, which is the substitution this tool
    exists to refuse (#9077 review finding: a count-only check cannot tell
    "the real 15" from "any 15").
    """
    try:
        from tests.eval.write_path import corpus
    except Exception:  # pragma: no cover - corpus unimportable (packaging)
        return None
    try:
        out: dict[str, str] = {}
        for session_id in corpus.operator_session_ids():
            for planted in corpus.load_gold(session_id).get("planted_operators") or []:
                edge_id = planted.get("id")
                if isinstance(edge_id, str) and edge_id:
                    out[edge_id] = planted.get("expected_kind")
    except Exception:  # pragma: no cover - unreadable gold is "unverifiable"
        return None
    return out or None


def current_denominator() -> int | None:
    """The committed corpus's planted-operator edge count (the live gold)."""
    edge_map = current_edge_map()
    return len(edge_map) if edge_map is not None else None


def _fail(rules: list[dict], rule: str, detail: str) -> None:
    rules.append({"rule": rule, "ok": False, "detail": detail})


def _pass(rules: list[dict], rule: str, detail: str) -> None:
    rules.append({"rule": rule, "ok": True, "detail": detail})


def _non_empty_str(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def check_receipt(
    receipt: object,
    *,
    denominator: int | None = None,
    edge_ids: dict[str, str] | None = None,
) -> dict:
    """Evaluate one receipt → ``{"ok", "rules", "summary", "edges"}``.

    Pure: no graph, no network, no LLM.

    ``denominator`` / ``edge_ids`` are for an OFFLINE or fixture caller (a
    test's synthetic edges are not claims about the committed gold). They are
    deliberately NOT a way to weaken the gate (#9077 review findings): a
    ``denominator`` that contradicts the readable live gold is itself a
    failure, an AGREEING one still binds the gold's edge set, and only
    ``edge_ids`` — a gold declared in the call, never reachable from the CLI —
    replaces the committed gold.
    """
    rules: list[dict] = []
    edges: list[dict] = []
    summary: dict = {}
    live_gold = current_edge_map()
    live_count = len(live_gold) if live_gold is not None else None
    # The committed gold binds whenever it is readable: a bare `denominator`
    # may only agree with it (a contradicting one is refused below), so an
    # agreeing override must not drop the edge-set binding (#9077 round-2
    # finding: `--denominator 15` silently skipped E4-COVERAGE-IDS / E4-KIND,
    # so 15 WRONG edge ids passed). `edge_ids` is the only way to declare a
    # DIFFERENT gold, and the CLI never passes it.
    binding = edge_ids if edge_ids is not None else live_gold

    if not isinstance(receipt, dict):
        _fail(rules, "E4-SHAPE", "receipt: not an object")
        return {"ok": False, "rules": rules, "summary": summary, "edges": edges}

    audit = receipt.get("operator_audit")
    if not isinstance(audit, dict):
        _fail(rules, "E4-SHAPE",
              "receipt.operator_audit: missing — a receipt without the "
              "operator audit is not an E4 measurement")
        return {"ok": False, "rules": rules, "summary": summary, "edges": edges}

    # ── E4-COMMIT: the sha the number was measured at ────────────────────
    commit = receipt.get("commit")
    if _non_empty_str(commit):
        _pass(rules, "E4-COMMIT", f"measured at {commit.strip()}")
    else:
        _fail(rules, "E4-COMMIT", f"receipt.commit: expected a sha, got {commit!r}")

    # ── E4-EXTRACTOR: the real one, not a monkeypatched seam ─────────────
    extractor = receipt.get("extractor")
    if not isinstance(extractor, dict):
        _fail(rules, "E4-EXTRACTOR",
              "receipt.extractor: missing — a receipt that does not name the "
              "extractor cannot rule out the monkeypatched WIRE lane (#2552)")
    elif extractor.get("real") is not True:
        _fail(rules, "E4-EXTRACTOR",
              "receipt.extractor.real is not true "
              f"(module={extractor.get('module')!r}, "
              f"qualname={extractor.get('qualname')!r}) — this run did not use "
              "the shipped extractor_v2.extract_session_v2, so its number "
              "grades the WIRE and is never an E4 result")
    else:
        _pass(rules, "E4-EXTRACTOR",
              f"real extractor ({extractor.get('module')}.{extractor.get('qualname')})")

    # ── E4-LANE: the product (llm) lane ─────────────────────────────────
    posture = (receipt.get("resolved_config") or {}).get("extractor_posture")
    if posture == PRODUCT_LANE:
        _pass(rules, "E4-LANE", f"product lane (extractor_posture={posture})")
    else:
        _fail(rules, "E4-LANE",
              f"receipt.resolved_config.extractor_posture={posture!r} — E4 is "
              "a product-lane question (the m2 echo lane has no product "
              "relation extraction, so its edge score is never an E4 number)")

    # ── E4-DENOM: the CURRENT gold's denominator, never the discarded 4 ──
    planted = audit.get("planted")
    want = denominator if denominator is not None else (
        len(edge_ids) if edge_ids is not None else live_count
    )
    summary["denominator"] = planted
    summary["denominator_expected"] = want
    if isinstance(planted, bool) or not isinstance(planted, int):
        _fail(rules, "E4-DENOM",
              f"operator_audit.planted: expected an int denominator, got {planted!r}"
              " — a rate without its denominator is not a measurement")
    elif planted == DISCARDED_DENOMINATOR:
        _fail(rules, "E4-DENOM",
              f"operator_audit.planted == {DISCARDED_DENOMINATOR} — that is the "
              "denominator the lane DISCARDED (it swung 0,1,1,1,2/4 on identical "
              "code, so it could not separate a fix from LLM variance); a receipt "
              "reporting x/4 measures a denominator nobody accepts")
    elif edge_ids is not None and len(edge_ids) != (
            planted if isinstance(planted, int) and not isinstance(planted, bool)
            else -1):
        _fail(rules, "E4-DENOM",
              f"operator_audit.planted == {planted!r} but the declared gold holds "
              f"{len(edge_ids)} edges — a denominator that contradicts its own "
              "edge set is not a measurement of it")
    elif denominator is not None and edge_ids is None \
            and live_count is not None and denominator != live_count:
        # #9077 review finding: a bare count override must not be a quieter
        # gold. When the committed gold is readable it is authoritative; a
        # caller who wants a DIFFERENT gold must declare its edge set
        # (`edge_ids`), which is a visible claim rather than a silenced check.
        _fail(rules, "E4-DENOM",
              f"declared denominator {denominator} contradicts the committed "
              f"gold ({live_count} planted edges) — the live gold wins when it "
              "is readable; declare the gold's edge set explicitly to grade "
              "against a different one")
    elif want is not None and planted != want:
        _fail(rules, "E4-DENOM",
              f"operator_audit.planted == {planted} but the committed gold holds "
              f"{want} planted edges — the receipt was measured against a "
              "different corpus than the one it is filed with")
    elif want is None:
        _fail(rules, "E4-DENOM",
              f"operator_audit.planted == {planted} but the corpus denominator "
              "could not be read (pass --denominator); an unverifiable "
              "denominator is not evidence")
    else:
        _pass(rules, "E4-DENOM", f"denominator {planted} == gold")

    # ── per-edge rules ──────────────────────────────────────────────────
    results = audit.get("results")
    if not isinstance(results, dict) or not results:
        _fail(rules, "E4-SHAPE",
              "operator_audit.results: missing/empty — a receipt without the "
              "per-edge verdicts restates the structural lane instead of "
              "answering E4")
        results = {}

    wired = 0
    persisted = 0
    named_misses: list[str] = []
    for edge_id, edge in results.items():
        if not isinstance(edge, dict):
            _fail(rules, "E4-SHAPE", f"operator_audit.results[{edge_id!r}]: not an object")
            continue
        row = {
            "id": edge.get("id") or edge_id,
            "kind": edge.get("expected_kind"),
            "verdict": edge.get("verdict"),
            "edge_correct": bool(edge.get("edge_correct")),
            "persisted": edge.get("persisted"),
            "cause": edge.get("cause"),
        }
        edges.append(row)
        if not _non_empty_str(edge.get("verdict")):
            _fail(rules, "E4-SHAPE",
                  f"{edge_id}: no verdict — the edge was not graded at all")
            continue
        if not edge.get("edge_correct"):
            # ── E4-CAUSE: a miss must be NAMED, never silently counted ──
            if not _non_empty_str(edge.get("cause")):
                _fail(rules, "E4-CAUSE",
                      f"{edge_id} ({edge.get('expected_kind')}): verdict "
                      f"{edge.get('verdict')!r} with NO cause — an unexplained "
                      "miss is a receipt defect")
            else:
                named_misses.append(f"{edge_id}: {edge.get('cause')}")
            continue
        wired += 1

        # ── E4-PERSIST: wired AND retrievable (the historical 0/4 half) ──
        if edge.get("persisted") is True:
            persisted += 1
        else:
            _fail(rules, "E4-PERSIST",
                  f"{edge_id}: edge_correct but persisted={edge.get('persisted')!r} "
                  "— the committed nodes are not in the eventId-keyed retrieval "
                  "layer (the silent-drop signature #2552 closed)")

        # ── E4-REFS: pre/post-commit endpoint refs, per side ────────────
        endpoints = edge.get("endpoints")
        if not isinstance(endpoints, dict):
            _fail(rules, "E4-REFS",
                  f"{edge_id}: no endpoint refs — the #4716 remap is asserted, "
                  "not shown")
            continue
        for side in ("from", "to"):
            refs = endpoints.get(side)
            if not isinstance(refs, list) or not refs:
                _fail(rules, "E4-REFS",
                      f"{edge_id}.{side}: no endpoint ref — a wired edge must "
                      "carry the refs the commit crossed")
                continue
            for ref in refs:
                if not isinstance(ref, dict):
                    _fail(rules, "E4-REFS", f"{edge_id}.{side}: ref is not an object")
                    continue
                pre, post = ref.get("pre_ref"), ref.get("post_ref")
                if not (_non_empty_str(pre) and _non_empty_str(post)):
                    _fail(rules, "E4-REFS",
                          f"{edge_id}.{side}: pre_ref/post_ref must both be "
                          f"non-empty (got {pre!r}/{post!r})")
                    continue
                if ref.get("resolves") is not True:
                    _fail(rules, "E4-REFS",
                          f"{edge_id}.{side}: post_ref {post!r} does not resolve "
                          "to a real node")
                if bool(ref.get("remapped")) != (pre != post):
                    _fail(rules, "E4-REFS",
                          f"{edge_id}.{side}: remapped={ref.get('remapped')!r} "
                          f"contradicts the refs (pre={pre!r}, post={post!r})")

    # ── E4-PERSIST (receipt level): operators entered the memory layer ──
    total = audit.get("operators_total")
    provenanced = audit.get("operators_provenanced")
    summary.update({
        "wired": wired,
        "persisted": persisted,
        "named_misses": len(named_misses),
        "operators_total": total,
        "operators_provenanced": provenanced,
    })
    if isinstance(total, int) and not isinstance(total, bool) and total > 0:
        if provenanced == total:
            _pass(rules, "E4-PERSIST",
                  f"{provenanced}/{total} committed operator nodes are retrievable"
                  " (eventId-stamped)")
        else:
            _fail(rules, "E4-PERSIST",
                  f"operators_provenanced={provenanced!r}/{total} — operators "
                  "committed but NOT retrievable is the pre-fix drop")
    else:
        _fail(rules, "E4-PERSIST",
              f"operator_audit.operators_total={total!r} — a completed product-lane "
              "capture of a corpus carrying planted operators must commit at least "
              "one operator node")

    if named_misses:
        _pass(rules, "E4-CAUSE",
              f"{len(named_misses)} miss(es) individually named")

    # ── E4-COVERAGE: the per-edge set must be the whole denominator ──────
    if isinstance(planted, int) and not isinstance(planted, bool) and results \
            and len(results) != planted:
        _fail(rules, "E4-COVERAGE",
              f"{len(results)} per-edge verdicts for a denominator of {planted} — "
              "edges are missing from the receipt")
    elif isinstance(planted, int) and results:
        _pass(rules, "E4-COVERAGE", f"all {planted} planted edges carry a verdict")

    # ── E4-COVERAGE-IDS / E4-KIND: the count is not the corpus ───────────
    # #9077 review finding: a length check cannot tell "the real 15" from "any
    # 15". Bound against the declared/live gold id->kind map, a receipt whose
    # verdicts are for other edges — or that re-labels an edge's kind — is
    # named instead of counted. Skipped only when the caller declared an
    # offline binding, which is not evidence about the committed gold.
    if binding is not None and results:
        gold_ids = set(binding)
        got_ids = set(results)
        missing = sorted(gold_ids - got_ids)
        extra = sorted(got_ids - gold_ids)
        if missing or extra:
            _fail(rules, "E4-COVERAGE-IDS",
                  "the per-edge verdicts are not the gold's edge set — "
                  f"missing={missing[:5]}{'…' if len(missing) > 5 else ''} "
                  f"not_planted={extra[:5]}{'…' if len(extra) > 5 else ''}"
                  " (a receipt grading other edges proves nothing about these)")
        else:
            _pass(rules, "E4-COVERAGE-IDS",
                  f"the {len(got_ids)} per-edge verdicts ARE the gold's edge set")
        kind_mismatch = [
            f"{edge_id}: gold {binding[edge_id]!r} vs receipt "
            f"{(results.get(edge_id) or {}).get('expected_kind')!r}"
            for edge_id in sorted(gold_ids & got_ids)
            if isinstance(results.get(edge_id), dict)
            and results[edge_id].get("expected_kind") != binding[edge_id]
        ]
        if kind_mismatch:
            _fail(rules, "E4-KIND",
                  "the receipt grades a DIFFERENT operator kind than the gold "
                  f"plants ({'; '.join(kind_mismatch[:5])}"
                  f"{'…' if len(kind_mismatch) > 5 else ''})")
        else:
            _pass(rules, "E4-KIND",
                  "every per-edge expected_kind matches the gold's plant")

    summary["misses"] = named_misses
    return {
        "ok": all(r["ok"] for r in rules),
        "rules": rules,
        "summary": summary,
        "edges": edges,
    }


def _print_human(path: Path, result: dict) -> None:
    s = result["summary"]
    print(f"receipt: {path}")
    print(f"  denominator: {s.get('denominator')} (expected {s.get('denominator_expected')})")
    print(f"  wired+persisted: {s.get('persisted')}/{s.get('denominator')}")
    print(f"  named misses: {s.get('named_misses')}")
    for miss in s.get("misses") or []:
        print(f"    - {miss}")
    print("  rules:")
    for rule in result["rules"]:
        mark = "PASS" if rule["ok"] else "FAIL"
        print(f"    [{mark}] {rule['rule']}: {rule['detail']}")
    print("VERDICT:", "EVIDENCE VALID" if result["ok"] else "EVIDENCE INSUFFICIENT")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="#9077 (E4): check that an operator-lane receipt is "
                    "artifact-grade evidence (not a numerator).")
    parser.add_argument("receipt", type=Path, help="path to a runner receipt JSON")
    parser.add_argument("--denominator", type=int, default=None,
                        help="override the live corpus denominator (offline "
                             "use only — refused when it contradicts the "
                             "readable gold, which is authoritative)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    try:
        receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"error: no such receipt: {args.receipt}", file=sys.stderr)
        return 2
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: unreadable receipt {args.receipt}: {exc}", file=sys.stderr)
        return 2

    result = check_receipt(receipt, denominator=args.denominator)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        _print_human(args.receipt, result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

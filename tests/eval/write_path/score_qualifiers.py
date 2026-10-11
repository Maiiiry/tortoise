"""Scoring for the raw-vs-narrative falsifier (#7947).

Pre-registered endpoint (issue comment, fixed before any run):

    per planted qualifier, classify the arm's extracted output bound to the same
    claim as preserved | weakened | strengthened | flattened | absent | unknown
    PRIMARY metric = loss rate = (flattened + absent) / planted qualifiers, per arm

**Instrument.** The primary classification is a deterministic marker instrument:
for each qualifier it asks two questions and combines them.

  * ``claim_present`` — do the bound claim's content words (from its
    ``verbatim_anchor``, or the qualifier's own words when unbound) appear among
    the arm's extracted claims?
  * ``marker_present`` — does the arm's output carry a same-class marker, i.e. a
    hedge marker for a hedge, a scope marker for a scope, a stance marker for a
    stance?

  = ``preserved`` if both; ``flattened`` if the claim survived with no same-class
  marker; ``absent`` if the claim did not survive.

This is deliberately crude and deliberately reproducible: it cannot vary between
runs, and it cannot favour an arm. Its known weakness is paraphrase — an arm may
preserve a hedge with vocabulary the marker list lacks, which would read as
``flattened``. That error is only possible in the DANGEROUS direction (it can
under-credit preservation), which is why the decisive cases get a second
opinion: every ``flattened`` finding can be arbitrated by the repo's judge
(``--judge``), and the arbitration is reported separately rather than folded in.

The marker lists are part of the registered instrument: they are printed in the
output so a reader can audit them.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

WRITE_PATH_DIR = Path(__file__).resolve().parent
GOLD_DIR = WRITE_PATH_DIR / "gold"

ARMS = ("raw", "narrative")
CLASSES = ("hedge", "scope", "stance")

# ── the instrument: same-class markers ──────────────────────────────────────
MARKERS: dict[str, tuple[str, ...]] = {
    "hedge": (
        "probably", "maybe", "might", "may ", "perhaps", "i think", "i believe",
        "roughly", "about ", "mostly", "seems", "appears", "suggests", "likely",
        "uncertain", "not sure", "as far as i recall", "hypothesis", "tentativ",
        "circumstantial", "correlates with", "realistically", "my read", "i'd guess",
        "i would guess", "believe", "assume", "assumption", "plausible",
        "possibl", "could be", "suspect", "impression", "no evidence",
        "double check", "double-check", "verify", "unconfirmed",
    ),
    "scope": (
        "only", "just ", "except", "until", "before ", "not for", "limited to",
        "as long as", "provided", "unless", "in scope", "out of scope",
        "on my machine", "hosted lane", "eu region", "single ", "one ", "prior to",
    ),
    "stance": (
        "agree", "agreed", "push back", "comfortable", "risky", "tempting",
        "rather", "signed off", "decision", "concern", "worried", "prefer",
        "wants", "against", "support", "oppose", "review", "accepted", "rejected",
        "endorse", "sign-off", "signoff",
    ),
}

#: Words too common to identify a claim. Kept small and visible on purpose.
STOP = frozenset(
    "the a an and or of to in on for with is are was were be been that this it its "
    "we our you your they their he she his her as at by from into not no yes so but "
    "if then than there here what which who whom when where why how all any both "
    "each few more most other some such only own same too very can will just should "
    "now also has have had do does did doing i me my".split()
)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower())


def _content_words(s: str) -> set[str]:
    return {w for w in _norm(s).split() if w and w not in STOP and len(w) > 2}


def render_claims(embed_list: dict) -> list[str]:
    """One string PER EXTRACTED CLAIM for one arm.

    Per-claim, not pooled: the marker test must be bound to the claim the
    qualifier modifies. Pooling the whole output would let a hedge anywhere in
    a 20-point list "preserve" every qualifier in the session, which would
    report near-zero loss for BOTH arms and make the delta noise.
    """
    out: list[str] = []
    for p in embed_list.get("points") or []:
        if isinstance(p, dict):
            c = _norm(str(p.get("content") or ""))
            if c.strip():
                out.append(c)
    for op in embed_list.get("operators") or []:
        if isinstance(op, dict):
            c = _norm(json.dumps(op))
            if c.strip():
                out.append(c)
    return out


def _claims_matching(anchor_words: set[str], claims: list[str],
                     threshold: float = 0.6) -> list[str]:
    """The extracted claims that identify the same claim as the anchor."""
    hits: list[str] = []
    for c in claims:
        if not anchor_words:
            continue
        cw = set(c.split())
        overlap = len(anchor_words & cw) / len(anchor_words)
        if overlap >= threshold:
            hits.append(c)
    return hits


def classify(qualifier: dict, units: dict[str, dict], claims: list[str]) -> dict:
    """Deterministic classification for one qualifier against one arm.

    The marker is looked for IN THE MATCHED CLAIM(S) ONLY — never the whole
    output — so "preserved" means *this qualifier survived on its own claim*.
    """
    qclass = qualifier["class"]
    bound = qualifier.get("bound_claim")
    unit = units.get(bound) if bound else None

    # What must appear for "the same claim survived"?
    anchor = ""
    if unit:
        anchor = unit.get("verbatim_anchor") or ""
    if not anchor:
        anchor = qualifier["verbatim_qualifier"]
    want = _content_words(anchor)
    matched = _claims_matching(want, claims)
    claim_present = bool(matched)

    # Marker must be IN the matched claim, not somewhere in the session.
    matched_text = " \n ".join(matched)
    marker_present = any(m in matched_text for m in MARKERS[qclass])

    if claim_present and marker_present:
        outcome = "preserved"
    elif claim_present:
        outcome = "flattened"
    else:
        outcome = "absent"

    return {
        "id": qualifier["id"],
        "class": qclass,
        "bound_claim": bound,
        "verbatim_qualifier": qualifier["verbatim_qualifier"],
        "claim_present": claim_present,
        "marker_present": marker_present,
        "outcome": outcome,
        "matching_claims": len(matched),
        "matched_excerpt": matched_text[:220],
        "anchor_words_total": len(want),
    }


def score_session(session_id: str, arm: str, arms_dir: Path) -> dict:
    arms = json.loads((arms_dir / f"{session_id}.arms.json").read_text())
    gold = json.loads((GOLD_DIR / f"{session_id}.gold.json").read_text())
    units = {u["id"]: u for u in gold.get("planted_units", [])}
    qualifiers = gold.get("planted_qualifiers", [])

    embed_list = arms["embed_list"].get(arm) or {}
    claims = render_claims(embed_list)

    rows = [classify(q, units, claims) for q in qualifiers]
    n = len(rows)
    matched = sum(1 for r in rows if r["claim_present"])
    lost = sum(1 for r in rows if r["outcome"] in ("flattened", "absent"))
    fl = sum(1 for r in rows if r["outcome"] == "flattened")
    ab = sum(1 for r in rows if r["outcome"] == "absent")
    return {
        "session_id": session_id,
        "arm": arm,
        "n_qualifiers": n,
        "preserved": sum(1 for r in rows if r["outcome"] == "preserved"),
        "flattened": fl,
        "absent": ab,
        "matched": matched,
        # Among qualifiers whose claim SURVIVED extraction, how often was the
        # qualifier itself flattened? This is the sharpest reading of §10's
        # question and it is not confounded by claim-drop volume.
        "distortion_rate": (fl / matched) if matched else None,
        "loss": lost,
        "loss_rate": (lost / n) if n else None,
        "points": len(embed_list.get("points") or []),
        "operators": len(embed_list.get("operators") or []),
        "rows": rows,
    }


def precision_recall(session_id: str, arm: str, arms_dir: Path) -> dict:
    """Registered secondary guard: did the arm keep the planted facts?

    Lexical, same idiom as the qualifier instrument: a planted unit counts as
    RECALLED when some extracted claim carries >=60% of its anchor's content
    words. A claim counts as a HIT (precision numerator) when it matches some
    planted unit. Crude on purpose — it is the volume guard, not the endpoint:
    E1 asks that volume fall *without losing the facts that matter*.
    """
    arms = json.loads((arms_dir / f"{session_id}.arms.json").read_text())
    gold = json.loads((GOLD_DIR / f"{session_id}.gold.json").read_text())
    units = gold.get("planted_units", [])
    embed_list = arms["embed_list"].get(arm) or {}
    claims = render_claims(embed_list)

    recalled = 0
    for u in units:
        want = _content_words(u.get("verbatim_anchor") or "")
        if want and _claims_matching(want, claims):
            recalled += 1

    hits = 0
    for c in claims:
        cw = set(c.split())
        for u in units:
            want = _content_words(u.get("verbatim_anchor") or "")
            if want and len(want & cw) / len(want) >= 0.6:
                hits += 1
                break

    n_units = len(units)
    n_claims = len(claims)
    return {
        "session_id": session_id,
        "arm": arm,
        "planted_units": n_units,
        "recalled": recalled,
        "recall": (recalled / n_units) if n_units else None,
        "claims_emitted": n_claims,
        "claims_hit": hits,
        "precision": (hits / n_claims) if n_claims else None,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="score the raw-vs-narrative arms (#7947)")
    ap.add_argument("--sessions", nargs="+", required=True)
    ap.add_argument("--arms-dir", default=str(Path.home() / ".cache" / "pi-7947" / "arms"))
    ap.add_argument("--out", default=str(Path.home() / ".cache" / "pi-7947" / "score.json"))
    args = ap.parse_args(argv)

    arms_dir = Path(args.arms_dir)
    results: list[dict] = []
    pr: list[dict] = []
    for sid in args.sessions:
        for arm in ARMS:
            results.append(score_session(sid, arm, arms_dir))
            pr.append(precision_recall(sid, arm, arms_dir))

    by_session: dict[str, dict[str, dict]] = {}
    for r in results:
        by_session.setdefault(r["session_id"], {})[r["arm"]] = r

    print(f"{'session':24} {'arm':10} {'n':>3} {'pres':>5} {'flat':>5} {'abs':>4} {'loss%':>7}")
    for sid, arms in by_session.items():
        for arm in ARMS:
            r = arms.get(arm)
            if not r:
                continue
            lr = "n/a" if r["loss_rate"] is None else f"{100 * r['loss_rate']:.0f}%"
            print(f"{sid:24} {arm:10} {r['n_qualifiers']:>3} {r['preserved']:>5} "
                  f"{r['flattened']:>5} {r['absent']:>4} {lr:>7}")

    # Aggregate loss rate over pooled qualifiers (equal weight per qualifier).
    agg: dict[str, dict] = {}
    for arm in ARMS:
        rows = [r for r in results if r["arm"] == arm]
        n = sum(r["n_qualifiers"] for r in rows)
        lost = sum(r["loss"] for r in rows)
        matched = sum(r["matched"] for r in rows)
        flat = sum(r["flattened"] for r in rows)
        agg[arm] = {
            "n": n, "loss": lost,
            "loss_rate": (lost / n) if n else None,
            "flattened": flat,
            "absent": sum(r["absent"] for r in rows),
            "matched": matched,
            "distortion_rate": (flat / matched) if matched else None,
            "points": sum(r["points"] for r in rows),
        }
    print()
    for arm in ARMS:
        a = agg[arm]
        lr = "n/a" if a["loss_rate"] is None else f"{100 * a['loss_rate']:.1f}%"
        dr = "n/a" if a["distortion_rate"] is None else f"{100 * a['distortion_rate']:.1f}%"
        print(f"POOLED {arm:10} n={a['n']:<3} loss={a['loss']:<3} "
              f"(flat={a['flattened']} absent={a['absent']}) loss_rate={lr}  "
              f"| matched={a['matched']} distortion={dr}")

    delta = None
    ddelta = None
    if agg["raw"]["loss_rate"] is not None and agg["narrative"]["loss_rate"] is not None:
        delta = agg["narrative"]["loss_rate"] - agg["raw"]["loss_rate"]
        print(f"\nDELTA loss  (narrative - raw) = {100 * delta:+.1f} pp"
              f"   [pre-registered band: |delta| <= 10 pp]")
    if agg["raw"]["distortion_rate"] is not None and agg["narrative"]["distortion_rate"] is not None:
        ddelta = agg["narrative"]["distortion_rate"] - agg["raw"]["distortion_rate"]
        print(f"DELTA flat  (narrative - raw) = {100 * ddelta:+.1f} pp"
              f"   [distortion among claims that survived]")

    for arm in ARMS:
        rows = [r for r in pr if r["arm"] == arm]
        u = sum(r["planted_units"] for r in rows)
        rec = sum(r["recalled"] for r in rows)
        nc = sum(r["claims_emitted"] for r in rows)
        h = sum(r["claims_hit"] for r in rows)
        rr = f"{100 * rec / u:.1f}%" if u else "n/a"
        pp = f"{100 * h / nc:.1f}%" if nc else "n/a"
        print(f"PLANTED {arm:10} recall={rr} ({rec}/{u})  precision={pp} "
              f"({h}/{nc} claims match a planted unit)")

    out = {
        "instrument": {
            "kind": "deterministic-same-class-markers",
            "markers": {k: list(v) for k, v in MARKERS.items()},
            "claim_present_threshold": 0.6,
            "marker_scope": "matched claim only (not the whole output)",
            "note": "can only under-credit preservation; flattened is the arbitrable class",
        },
        "aggregate": agg,
        "precision_recall": pr,
        "delta_pp": None if delta is None else 100 * delta,
        "delta_distortion_pp": None if ddelta is None else 100 * ddelta,
        "band_pp": 10,
        "per_session": by_session,
    }
    Path(args.out).write_text(json.dumps(out, indent=2))
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

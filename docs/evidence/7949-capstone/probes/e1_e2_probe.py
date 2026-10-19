"""E1 + E2 on ONE run, one corpus (#9076 — epic #7949).

**What this probe is for.** The epic's central statement is that *E1 and E2 pull
against each other*: a selection filter tuned to drop `^the <X>` Objects can hit
its E1 bar while quietly dropping the state values E2 exists to preserve. Two
separately-run lanes would each report green. So E1 (volume falls) and E2
(correctness preserved) are measured **in one process, over one corpus, through
one pipeline**, and this file prints every numerator and every denominator it
uses.

**Which pipeline "after" means — stated, because an unstated arm makes the
metric uninterpretable.** Four arms, all driven through the REAL v2 entry point
(``extractor_v2.extract_session_v2``: S0→S1→S2.1→S2.2→S2.3→S3→S4→S5/S6), with
the SAME scripted candidate set (the corpus) feeding every arm:

============================  ==========  ==========================================
arm                           scored?     what it is
============================  ==========  ==========================================
``before``                    baseline    VET and the value gate OFF — the shipped
                                          DEFAULT arm (both flags default off).
``after-vet-shipped``         **YES**     ``TORTOISE_VET=1`` + ``TORTOISE_VALUE_GATE=1``,
                                          ``arbiter=None`` — exactly what production
                                          wires today.
``after-vet-arbiter``         no          the same, plus an INJECTED arbiter answering
                                          S2.2's *"is this an entity or a reference?"*
                                          with the in-repo predicate
                                          ``definite_description_backfill
                                          .is_positively_bare_definite_description``.
                                          Counterfactual: the ``vet_arbiter=`` seam
                                          exists but **no production caller injects
                                          one** (``vet_gate``'s own docstring).
``counterfactual-mint-skip``  no          what a MINT-TIME rule would produce: the
                                          positively-classified definite-description
                                          entities are never minted and their names are
                                          stripped from the statements'
                                          ``about_entities`` (the claim survives; only
                                          the reference goes).
============================  ==========  ==========================================

Only ``after-vet-shipped`` is scored, because it is the only arm that is the
product. The two counterfactual arms are reported as **estimates of what a
mechanism that does not exist would buy** — never as green. ``tools
/e1_e2_census_check.py`` fails if a counterfactual arm is ever the scored one.

⚠️ **The extractor is held constant on purpose.** The candidate set is
``docs/evidence/7949-capstone/corpus/e1e2_corpus.json``, produced by a scripted
model so the pipeline runs with no network and no provider key. That is not a
weakness in the E1 measurement — it is the control: the only variable between
``before`` and ``after`` is the gate, and the probe ASSERTS the pre-gate
population is byte-identical across the three gate arms. What it cannot claim is
anything about extraction quality on live traffic; the corpus is the population
(see the README's limits).

**Why a pre-registration gate (§4.4 / #9075).** Thresholds chosen after the run
are the failure the epic's pre-registration rule exists to stop. ``--prereg
<sha>`` must name a commit whose TREE carries ``preregistration.json`` with the E1
and E2 thresholds; it is read with ``git show <sha>:<path>``, never from the
worktree, so a threshold edited after the numbers are in cannot be served. With no
``--prereg`` the probe still MEASURES — and reports ``UNPREREGISTERED`` instead of
a verdict, exiting 3. Measured numbers are evidence; a verdict without a frozen
threshold is not.

Exit codes: ``0`` scored and every criterion green · ``1`` scored and something
red · ``3`` measured but UNPREREGISTERED (no frozen thresholds) · ``2`` usage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

# The pack registry emits declaration notices at import time; they are not this
# measurement's subject and would bury the numbers in the evidence log. The mute
# is applied INSIDE `main()`, never at module scope: `logging.disable` sets
# `logging.Manager.disable`, which is PROCESS-global, and
# `tests/test_e1_e2_measurement.py` imports this module at COLLECTION time — so
# a module-scope call outlives the run and silences every `caplog` assertion at
# WARNING/INFO in every later test of the same pytest process (`caplog.at_level`
# sets only the logger and handler levels, never `Manager.disable`).

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

DEFAULT_CORPUS = ROOT / "docs/evidence/7949-capstone/corpus/e1e2_corpus.json"
PREREG_ARTIFACT = "docs/evidence/7949-capstone/preregistration.json"

SCORED_ARM = "after-vet-shipped"
BEFORE_ARM = "before"

#: The E1 numerator, restated from the architecture document (§1:85): a
#: definite description is an Object whose name begins with `the `. The
#: denominator is every Object admitted in that arm. The production figure
#: (62.3%) is reconstructed from the G5 census as 4,901 / 7,870 = 62.27% — the
#: same rule, a different (all-Objects) population.
THE_PREFIX = re.compile(r"^the\s", re.IGNORECASE)

#: ── E2's state-value detector, pinned and printed ──────────────────────────
#:
#: A value is a literal that must survive VERBATIM: a measurement/unit, a
#: percentage, a money amount, a clock time, a month-day date, a duration, or a
#: spelled-out quantity bound to a unit ("forty rows", "ten-minute TTL"). This is
#: a HEURISTIC and it is stated as one: it is deliberately generous (it would
#: rather over-count a value than miss one, because the criterion it feeds is
#: "nothing was lost"), and every value it finds is printed so a reader can audit
#: the match instead of trusting the count.
VALUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("percent", re.compile(r"\b\d+(?:\.\d+)?\s?(?:%|percent)\b", re.IGNORECASE)),
    ("measure", re.compile(r"\b\d+(?:\.\d+)?\s?(?:ms|s|sec|secs|seconds|min|mins|"
                           r"minutes|h|hrs|hours|MB|GB|KB|TB|k|rps|rows|tokens|"
                           r"bytes|days|weeks)\b", re.IGNORECASE)),
    ("clock", re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")),
    ("money", re.compile(r"[$€£]\s?\d[\d,.]*")),
    ("date", re.compile(r"\b(?:January|February|March|April|May|June|July|August|"
                        r"September|October|November|December)\s+\d{1,2}\b")),
    ("version", re.compile(r"\bv\d+\.\d+(?:\.\d+)?\b")),
    ("count", re.compile(r"\b\d{2,}\b")),
    ("spelled-quantity", re.compile(
        r"\b(?:two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|"
        r"thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred)\b[\s-]"
        r"(?:percent|minutes?|hours?|days?|weeks?|rows?|workers?|times?|requests?|"
        r"seconds?)", re.IGNORECASE)),
)

#: Sentence terminators for the atomicity proxy. §7 requires an atomic `Point` so
#: an operator can attach to ONE claim; a fused claim gives one confidence number
#: to several beliefs. The proxy measures SENTENCE-level fusion, which is the
#: honest reach of a text rule: two claims joined inside one sentence by "and"
#: read as atomic here. Stated as a limit, not hidden.
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\s*;\s*|\s+—\s+|\s+--\s+")


# ── corpus → the candidate set the pipeline sees ──────────────────────────


def load_corpus(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_embed_list(corpus: dict) -> dict:
    """The S2.1-shaped candidate list, exactly as the extractor emits one."""
    return {
        "entities": [
            {"name": o["name"], "kind": "core:other", "lifecycle": "created",
             "supersedes": None, "note": None}
            for o in corpus["objects"]
        ],
        "events": [],
        "points": [
            {"content": p["content"], "pointKind": "statement",
             "about_entities": list(p.get("about") or [])}
            for p in corpus["points"]
        ],
        "operators": [],
        "chain_notes": [],
        "link_before_create": [],
    }


def mint_skip_transform(embed_list: dict) -> tuple[dict, dict]:
    """What a MINT-TIME definite-description rule would have produced.

    The entity is never minted; the statement that referenced it KEEPS its content
    and loses the reference. That is the difference that decides E2: a design that
    drops the referencing statement as well would be a different (and strictly
    worse) policy, and the probe prints how many statements that would cost.
    """
    from tortoise import definite_description_backfill as dd

    dropped = sorted(
        e["name"] for e in embed_list["entities"]
        if dd.is_positively_bare_definite_description(str(e["name"]))
    )
    gone = set(dropped)
    minted = dict(embed_list)
    minted["entities"] = [e for e in embed_list["entities"]
                          if str(e["name"]) not in gone]
    points = []
    stripped = 0
    would_drop_statements = 0
    for p in embed_list["points"]:
        refs = [str(n) for n in (p.get("about_entities") or [])]
        kept = [n for n in refs if n not in gone]
        stripped += len(refs) - len(kept)
        if refs and not kept:
            would_drop_statements += 1
        q = dict(p)
        q["about_entities"] = kept
        points.append(q)
    minted["points"] = points
    return minted, {
        "dropped_objects": dropped,
        "stripped_references": stripped,
        "statements_left_unreferenced": would_drop_statements,
    }


def definite_description_arbiter(candidates: list[dict], narrative: str) -> dict:
    """S2.2's semantic question, answered by the ONLY detector the repo has.

    ``is_positively_bare_definite_description`` is the conservative gate over the
    owner-confirmed label set (9 of 15 positively classified). Using it here means
    the counterfactual arm assumes no capability the tree does not already carry —
    and its 6 deferred names show up as *dropped: none* rather than as a guess.
    """
    from tortoise import definite_description_backfill as dd

    verdicts = []
    for c in candidates:
        if c.get("section") != "entities":
            continue
        if dd.is_positively_bare_definite_description(str(c.get("text") or "")):
            verdicts.append({
                "id": c["id"],
                "outcome": "DISCARD",
                "rule_id": "e1.counterfactual.definite_description",
                "reason": ("positively-classified bare definite description "
                           "(tortoise/definite_description_backfill.py)"),
            })
    return {"verdicts": verdicts}


# ── the scripted model: ONE candidate set, held constant across arms ───────


class ScriptedModel:
    """Deterministic `complete(system=, user=)` adapter over the corpus.

    S1 (system carries ``STORY SUMMARIZER``) → a narrative. The first S2 call →
    the corpus. Every later call (S4's gap review) → an empty delta, so the union
    is exactly the corpus and the four arms' populations are comparable. Counts
    its calls so the log can show the pipeline actually ran end to end.
    """

    def __init__(self, embed_list: dict):
        self._payload = json.dumps(embed_list)
        self._empty = json.dumps({"entities": [], "events": [], "points": [],
                                  "operators": [], "chain_notes": [],
                                  "link_before_create": []})
        self.calls = 0
        self.stages: list[str] = []
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0

    def complete(self, *, system: str, user: str, max_tokens: int | None = None) -> str:
        self.calls += 1
        if "STORY SUMMARIZER" in system:
            self.stages.append("S1")
            return "A session over the pre-registered E1/E2 corpus."
        if self.stages.count("S2.1") == 0:
            self.stages.append("S2.1")
            return self._payload
        self.stages.append("S4")
        return self._empty


# ── one arm ────────────────────────────────────────────────────────────────


def run_arm(embed_list: dict, *, vet: bool, arbiter=None) -> dict:
    """Drive the real pipeline once, with the gate flags set for this arm."""
    from tortoise import extractor_v2 as v2

    env_keys = ("TORTOISE_VET", "TORTOISE_VALUE_GATE", "TORTOISE_CLASSIFY_LATER")
    saved = {k: os.environ.get(k) for k in env_keys}
    for k in env_keys:
        os.environ.pop(k, None)
    if vet:
        os.environ["TORTOISE_VET"] = "1"
        os.environ["TORTOISE_VALUE_GATE"] = "1"
    model = ScriptedModel(embed_list)
    try:
        out = v2.extract_session_v2(
            model,
            [{"role": "user", "content": "E1/E2 measurement corpus (#9076)."}],
            session_id="e1e2-9076",
            vet_arbiter=arbiter,
        )
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return {
        "payload": out.get("payload") or {},
        "warnings": list(out.get("warnings") or []),
        "errors": list(out.get("errors") or []),
        # #5005: the VET evidence surface is a TOP-LEVEL result key, not a
        # stats key (``result["vet"]`` in extractor_v2).
        "vet": out.get("vet") or {},
        "stages": model.stages,
        "model_calls": model.calls,
    }


# ── the census ─────────────────────────────────────────────────────────────


def _norm(text: object) -> str:
    return str(text or "").strip()


def find_values(text: str) -> list[tuple[str, str]]:
    """Every value token in ``text`` as ``(class, literal)``, in match order."""
    found: list[tuple[str, str]] = []
    seen: set[tuple[int, int]] = set()
    for name, pat in VALUE_PATTERNS:
        for m in pat.finditer(text):
            span = (m.start(), m.end())
            if span in seen:
                continue
            seen.add(span)
            found.append((name, m.group(0).strip()))
    found.sort(key=lambda x: text.find(x[1]))
    return found


def claim_units(text: str) -> list[str]:
    return [u.strip() for u in SENTENCE_SPLIT.split(_norm(text)) if u.strip()]


def census(result: dict, corpus: dict) -> dict:
    """Count what LANDED — the Layer-1 commit payload, i.e. the node set.

    The payload is what the write path creates node-for-node (``execute_embed``
    maps entities→`:Object`, points→`:Point`), so a payload census and a graph
    census agree by construction; this one needs no server, which keeps the run
    hermetic. Stated as a limit in the README rather than left implicit.
    """
    payload = result["payload"]
    meta = {_norm(p["content"]): p for p in corpus["points"]}
    objects = [_norm(e.get("name")) for e in (payload.get("entities") or [])]
    points = [_norm(p.get("content")) for p in (payload.get("points") or [])]

    the_prefix = [n for n in objects if THE_PREFIX.match(n)]
    must_keep = [t for t in points if (meta.get(t) or {}).get("must_keep")]
    state_points = []
    values = []
    for t in points:
        vals = find_values(t)
        if vals:
            state_points.append(t)
            values.extend(v for _c, v in vals)
    atomic = [t for t in points if len(claim_units(t)) == 1]
    fused = [t for t in points if len(claim_units(t)) > 1]

    return {
        "objects": objects,
        "points": points,
        "objects_total": len(objects),
        "objects_the_prefix": len(the_prefix),
        "share": (len(the_prefix) / len(objects)) if objects else None,
        "points_total": len(points),
        "real_facts": len(must_keep),
        "real_fact_points": must_keep,
        "state_value_points": len(state_points),
        "state_value_count": len(values),
        "state_values": values,
        "atomic_points": len(atomic),
        "fused_points": len(fused),
        "fused_list": fused,
        "vet": result["vet"],
    }


def _sha(obj: object) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


# ── pre-registration binding ───────────────────────────────────────────────


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                          text=True, check=True).stdout


def load_prereg(sha: str) -> dict:
    """Read the frozen thresholds from the COMMIT's tree, never the worktree."""
    raw = _git("show", f"{sha}:{PREREG_ARTIFACT}")
    return json.loads(raw)


def measurement_sha() -> str:
    """The sha the run is measured at, plus a marker when the TRACKED tree differs.

    ``--untracked-files=no`` is deliberate: an untracked file is not part of the
    sha at all, and the log's own sibling artifacts are untracked while the log is
    being written — a check that counted them would mark every honest log dirty.
    A MODIFIED tracked file does change what was measured, so it is marked.
    """
    dirty = _git("status", "--porcelain", "--untracked-files=no").strip()
    sha = _git("rev-parse", "HEAD").strip()
    return sha + ("+dirty" if dirty else "")


# ── coverage honesty ───────────────────────────────────────────────────────


def coverage(corpus: dict) -> dict:
    """Which salience classes the shipped detectors reach — the partial-coverage table.

    A gate measured only on the class it can detect reads as a pass, so this is
    printed on every run whether or not the arms moved. Two detectors are
    reported apart because they answer different questions: the
    definite-description predicate (semantic half, #4894's spec) and the
    identifier-only value gate (#4899, mechanical).
    """
    from tortoise import definite_description_backfill as dd
    from tortoise import value_gate as vg

    rows = [o for o in corpus["objects"] if o.get("class")]
    by_class: dict[str, dict] = {}
    for o in rows:
        c = by_class.setdefault(str(o["class"]), {"rows": 0, "dd_positive": 0,
                                                  "value_gate": 0})
        c["rows"] += 1
        name = str(o["name"])
        if dd.is_positively_bare_definite_description(name):
            c["dd_positive"] += 1
        if vg.identifier_only(name):
            c["value_gate"] += 1
    in_scope = sum(1 for o in rows if dd.is_bare_definite_description(str(o["name"])))
    positively = sum(1 for o in rows
                     if dd.is_positively_bare_definite_description(str(o["name"])))
    undetected = sorted(k for k, v in by_class.items()
                        if not v["dd_positive"] and not v["value_gate"])
    return {"by_class": by_class, "rows": len(rows), "in_scope": in_scope,
            "positively": positively, "undetected": undetected}


# ── the run ────────────────────────────────────────────────────────────────


def _fmt(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.6f}"


def _f(x: bool) -> str:
    return "yes" if x else "no"


def main(argv: list[str] | None = None) -> int:
    """Run the measurement to stdout, with the registry's notices muted.

    The mute is SCOPED to this call and restored in a ``finally`` (#9110 review
    P1). At module scope it leaked into the whole pytest process — see the note
    beside the ``logging`` import. Only the PRINTED log needs it quiet; the
    measurement never did.
    """
    previous = logging.root.manager.disable
    logging.disable(logging.WARNING)
    try:
        return _run(argv)
    finally:
        logging.disable(previous)


def _run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="E1+E2 on one run, one corpus (#9076)")
    ap.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    ap.add_argument("--prereg", default="")
    ap.add_argument("--json-out", default="")
    args = ap.parse_args(argv)

    corpus_path = Path(args.corpus)
    corpus = load_corpus(corpus_path)
    corpus_bytes = corpus_path.read_bytes()
    corpus_sha = hashlib.sha256(corpus_bytes).hexdigest()

    embed = build_embed_list(corpus)
    embed_sha = _sha(embed)
    minted, mint_meta = mint_skip_transform(embed)
    minted_sha = _sha(minted)

    arms = [
        # (name, embed_list, vet, arbiter, counterfactual, drop_cause)
        (BEFORE_ARM, embed, False, None, False, "n/a"),
        (SCORED_ARM, embed, True, None, False, "vet-discard"),
        ("after-vet-arbiter", embed, True, definite_description_arbiter, True,
         "vet-discard"),
        ("counterfactual-mint-skip", minted, True, None, True,
         "mint-time-transform"),
    ]
    results: dict[str, dict] = {}
    lines: list[str] = []

    sha = measurement_sha()
    lines.append("E1E2-PROBE 1")
    lines.append(f"sha={sha}")
    lines.append(f"corpus={corpus_path.name} corpus_sha256={corpus_sha} "
                 f"objects={len(corpus['objects'])} points={len(corpus['points'])}")
    lines.append(f"preg_embed_sha={embed_sha} mint_skip_embed_sha={minted_sha}")
    lines.append(f"mint_skip dropped_objects={len(mint_meta['dropped_objects'])} "
                 f"stripped_references={mint_meta['stripped_references']} "
                 f"statements_left_unreferenced={mint_meta['statements_left_unreferenced']}")
    lines.append(f"scored_arm={SCORED_ARM}")

    for name, emb, vet, arb, cf, cause in arms:
        res = run_arm(emb, vet=vet, arbiter=arb)
        cen = census(res, corpus)
        results[name] = {"census": cen, "result": res}
        pre_objects = len(emb["entities"])
        pre_points = len(emb["points"])
        lines.append(
            f"arm={name} counterfactual={_f(cf)} scored={_f(name == SCORED_ARM)} "
            f"preg_objects={pre_objects} preg_points={pre_points} "
            f"preg_embed_sha={_sha(emb)}")
        lines.append(
            f"arm={name} metric=objects total={cen['objects_total']} "
            f"numerator={cen['objects_the_prefix']} "
            f"denominator={cen['objects_total']} share={_fmt(cen['share'])}")
        lines.append(f"arm={name} metric=real_facts count={cen['real_facts']} "
                     f"points={cen['points_total']}")
        lines.append(f"arm={name} metric=state_value_points "
                     f"points={cen['state_value_points']} "
                     f"values={cen['state_value_count']}")
        lines.append(f"arm={name} metric=atomic_points atomic={cen['atomic_points']} "
                     f"fused={cen['fused_points']}")
        vet_stats = cen["vet"] or {}
        # Two passes run per session (S2 and the post-S4 union). The union pass
        # is the one whose removals reach the payload, so it is reported apart
        # from the S2 pass rather than summed — summing would double-count the
        # same candidates.
        for pass_name in ("s2", "union"):
            blk = vet_stats.get(pass_name) or {}
            st = blk.get("stats") or {}
            lines.append(
                f"arm={name} metric=vet pass={pass_name} "
                f"enabled={_f(bool(vet_stats.get('enabled')))} "
                f"arbiter={st.get('arbiter', 'n/a')} "
                f"candidates={st.get('candidates', 0)} "
                f"discarded={st.get('discarded', 0)} "
                f"by_outcome={json.dumps(st.get('by_outcome') or {}, sort_keys=True)} "
                f"mechanical={json.dumps((st.get('mechanical') or {}), sort_keys=True)}")
        lines.append(f"arm={name} metric=vet-downgrades "
                     f"count={sum(1 for w in res['warnings'] if 'kept (Layer-1 referential integrity)' in w)}")
        # What this arm removed, and why — the drop table E2's checklist demands.
        before = results[BEFORE_ARM]["census"] if BEFORE_ARM in results else cen
        for obj in before["objects"]:
            if obj not in cen["objects"]:
                lines.append(f"arm={name} dropped kind=object name={obj} "
                             f"cause={cause}")
        for pt in before["points"]:
            if pt not in cen["points"]:
                vals = [v for _c, v in find_values(pt)]
                lines.append(f"arm={name} dropped kind=point content={pt[:90]!r} "
                             f"cause={cause} values={vals}")
        for w in res["warnings"]:
            if w.startswith("vet:"):
                lines.append(f"arm={name} vet-warning={w[:160]}")
        lines.append(f"arm={name} pipeline_stages={','.join(res['stages'])} "
                     f"model_calls={res['model_calls']} "
                     f"errors={len(res['errors'])}")

    # ── coverage honesty (printed on every run) ────────────────────────────
    cov = coverage(corpus)
    lines.append(f"coverage rows={cov['rows']} in_scope={cov['in_scope']} "
                 f"positively_classified={cov['positively']} "
                 f"undetected_classes={','.join(cov['undetected'])}")
    for cls in sorted(cov["by_class"]):
        c = cov["by_class"][cls]
        lines.append(f"coverage class={cls} rows={c['rows']} "
                     f"dd_positive={c['dd_positive']} value_gate={c['value_gate']}")
    lines.append("split-implemented=no "
                 "note=SPLIT is absent from vet_gate's O4 vocabulary, so §S2.2 "
                 "Level-1 q2 cannot be satisfied by any arm here")

    # ── the criteria ───────────────────────────────────────────────────────
    before_c, after_c = results[BEFORE_ARM]["census"], results[SCORED_ARM]["census"]
    prereg = None
    prereg_note = "UNPREREGISTERED"
    if args.prereg:
        try:
            prereg = load_prereg(args.prereg)
            prereg_note = args.prereg
        except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
            lines.append(f"prereg-error={type(e).__name__}: {e} "
                         f"artifact={PREREG_ARTIFACT} sha={args.prereg}")
            prereg = None
    lines.insert(4, f"prereg={prereg_note} artifact={PREREG_ARTIFACT}")

    threshold = None
    if isinstance(prereg, dict):
        threshold = prereg.get("e1", {}).get("min_share_reduction")

    # The threshold USED is printed twice on purpose: once as what the artifact
    # froze, once as what this run scored against. The checker fails if the two
    # disagree, which is the only mechanical way a log can show it did not pick
    # its own bar. E2's criteria are "no loss at all" (>= the before side), which
    # satisfies any preservation floor the pre-registration states — so no E2
    # number is consumed, and that is said rather than implied.
    frozen = "n/a"
    if isinstance(prereg, dict):
        frozen = prereg.get("e1", {}).get("min_share_reduction", "ABSENT")
    lines.append(f"prereg_e1_threshold={frozen}")
    lines.append("prereg_e2_floor=not-consumed note=the E2 criteria are "
                 "no-loss (after >= before), which satisfies any stated floor")
    delta = (after_c["share"] - before_c["share"]) if (
        before_c["share"] is not None and after_c["share"] is not None) else None
    reduction = (-delta) if delta is not None else None

    def verdict(ok: bool) -> str:
        # No frozen threshold ⇒ no verdict. A criterion is only PASS/FAIL against
        # a threshold that was fixed BEFORE the run (#9075); anything else is
        # UNPREREGISTERED — including the ones that happen to be green.
        if threshold is None:
            return "UNPREREGISTERED"
        return "PASS" if ok else "FAIL"

    e1_ok = (reduction is not None and threshold is not None
             and reduction >= float(threshold))
    control_ok = after_c["real_facts"] >= before_c["real_facts"]
    state_ok = after_c["state_value_count"] >= before_c["state_value_count"]
    atomic_ok = after_c["atomic_points"] >= before_c["atomic_points"]

    # Threshold-free observation: "did the share fall AT ALL on the scored arm?"
    # needs no pre-registration to answer, and it is the finding that decides
    # whether any threshold could ever be met by the shipped machinery.
    observed = ("NO_REDUCTION" if (reduction is None or reduction <= 0)
                else "REDUCTION_OBSERVED")
    lines.append(f"e1 before_share={_fmt(before_c['share'])} "
                 f"after_share={_fmt(after_c['share'])} "
                 f"reduction={_fmt(reduction)} "
                 f"threshold={('n/a' if threshold is None else threshold)} "
                 f"observed={observed} verdict={verdict(e1_ok)}")
    lines.append(f"e1-control before_real_facts={before_c['real_facts']} "
                 f"after_real_facts={after_c['real_facts']} verdict={verdict(control_ok)}")
    lines.append(f"e2-state before={before_c['state_value_count']} "
                 f"after={after_c['state_value_count']} "
                 f"before_points={before_c['state_value_points']} "
                 f"after_points={after_c['state_value_points']} verdict={verdict(state_ok)}")
    lines.append(f"e2-atomic before={before_c['atomic_points']} "
                 f"after={after_c['atomic_points']} "
                 f"fused_after={after_c['fused_points']} verdict={verdict(atomic_ok)}")
    # §7's atomicity property, per fused Point, with SPLIT named as the reason it
    # survives: `SPLIT` is not in the O4 vocabulary, so no arm can split a fused
    # claim — the count is expected to be equal, and that equality is NOT a pass.
    for fused in after_c["fused_list"]:
        lines.append(f"e2-atomic fused content={fused[:90]!r} "
                     f"units={len(claim_units(fused))} "
                     f"cause=SPLIT-not-implemented")
    for name in ("after-vet-arbiter", "counterfactual-mint-skip"):
        c = results[name]["census"]
        r = (before_c["share"] - c["share"]) if (
            c["share"] is not None and before_c["share"] is not None) else None
        lines.append(f"counterfactual arm={name} reduction={_fmt(r)} "
                     f"real_facts={c['real_facts']} "
                     f"state_values={c['state_value_count']} "
                     f"note=NOT SCORED (mechanism absent from production)")

    scored = prereg is not None
    green = scored and e1_ok and control_ok and state_ok and atomic_ok
    exit_code = 0 if green else (1 if scored else 3)
    lines.append(f"result={'GREEN' if green else ('RED' if scored else 'UNPREREGISTERED')}")
    lines.append(f"exit={exit_code}")

    text = "\n".join(lines) + "\n"
    sys.stdout.write(text)
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps({"sha": sha, "corpus_sha256": corpus_sha,
                        "prereg": prereg_note,
                        "arms": {k: v["census"] for k, v in results.items()},
                        "exit": exit_code}, indent=1, sort_keys=True) + "\n",
            encoding="utf-8")
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

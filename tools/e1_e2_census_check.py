"""The E1+E2 evidence log's structural gate (#9076).

**What this closes.** An evidence log is a claim. This is the mechanical check
that the CLAIM IS STRUCTURED so it cannot be read as more than it is: every
numerator has a denominator, the denominators are the same corpus, the anti-gaming
control is present, every dropped value names its cause, the coverage deficit is
stated, and no criterion is called PASS without a pre-registered threshold. It
does **not** re-derive the numbers — the probe owns them — and it cannot detect a
forged log (a plausible number would pass), which is the same stated limit the
sibling capstone gate carries. What it CAN do, and does, is refuse a log whose
numbers disagree with each other: every criterion line is reconciled against the
``arm=`` lines it summarizes — ``e1`` (V3), ``e1-control`` (V5), ``e2-state`` (V6)
and ``e2-atomic`` (V7) — each E2 criterion's verdict is checked against its own
before/after counts (V5-V7), and the ``e1`` line's ``observed`` conclusion against
its own ``reduction`` (V10). A reduction borrowed from a counterfactual arm, or a
state-value table that shrank under a ``PASS``, is therefore a finding rather than
a pass (#9110 reviews).

Printing is not enough on its own: every finding below fails the run, because a
log that omits its denominator is exactly the artifact this epic exists to refuse.

Usage:
    python tools/e1_e2_census_check.py docs/evidence/7949-capstone/e1-e2.log
    python tools/e1_e2_census_check.py <log> --require-green   # exit 1 unless GREEN
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from pathlib import Path

ARM_LINE = re.compile(r"^arm=(?P<arm>[a-z0-9-]+)\s+(?P<rest>.*)$")
KV = re.compile(r"(?P<key>[a-z0-9_]+)=(?P<val>\"[^\"]*\"|'[^']*'|\S+)")
OBJECTS_LINE = re.compile(r"^arm=(?P<arm>[a-z0-9-]+) metric=objects ")
DROP_POINT_LINE = re.compile(
    r"^arm=(?P<arm>[a-z0-9-]+) dropped kind=point content=(?P<content>.*) "
    r"cause=(?P<cause>\S*)\s+values=(?P<values>\[.*\])$")
FUSED_LINE = re.compile(r"^e2-atomic fused content=.* cause=(?P<cause>\S+)$")
CRITERIA = ("e1", "e1-control", "e2-state", "e2-atomic before=")
VERDICTS = {"PASS", "FAIL", "UNPREREGISTERED"}


class Log:
    def __init__(self, text: str):
        self.text = text
        self.lines = [ln.rstrip("\n") for ln in text.splitlines() if ln.strip()]

    def find(self, prefix: str) -> list[str]:
        return [ln for ln in self.lines if ln.startswith(prefix)]

    def one(self, prefix: str) -> str | None:
        hits = self.find(prefix)
        return hits[-1] if hits else None

    @staticmethod
    def kv(line: str | None) -> dict[str, str]:
        if not line:
            return {}
        return {m.group("key"): m.group("val").strip("\"'")
                for m in KV.finditer(line)}


def check(text: str) -> tuple[list[str], dict]:
    findings: list[str] = []
    log = Log(text)
    state: dict = {}

    # ── V1: identity of the run ───────────────────────────────────────────
    if not log.one("E1E2-PROBE "):
        findings.append("[V1] the log does not start with an E1E2-PROBE header")
    head = log.kv(log.one("sha="))
    if not log.one("sha=") or len(head.get("sha", "")) < 7:
        findings.append("[V1] the log does not record the sha it was measured at")

    # ── V2: one corpus, one scored arm ────────────────────────────────────
    corpus = log.kv(log.one("corpus="))
    if not corpus.get("corpus_sha256"):
        findings.append("[V2] the log does not record the corpus's sha256 — the "
                        "before and after sides could be different corpora")
    for key in ("objects", "points"):
        if not corpus.get(key, "").isdigit():
            findings.append(f"[V2] the corpus population is not stated ({key}=)")
    scored = log.kv(log.one("scored_arm=")).get("scored_arm", "")
    if not scored:
        findings.append("[V2] the log does not name the scored arm — an unstated "
                        "arm makes the metric uninterpretable")
    arms = sorted({m.group("arm") for ln in log.lines
                   for m in [ARM_LINE.match(ln)] if m})
    state["arms"] = arms
    # The per-arm first line carries counterfactual=; keep the first occurrence.
    seen: dict[str, str] = {}
    for ln in log.find("arm="):
        kv = log.kv(ln)
        a = kv.get("arm")
        if a and "counterfactual" in kv and a not in seen:
            seen[a] = kv["counterfactual"]
    if seen.get(scored) == "yes":
        findings.append(f"[V2] the SCORED arm ({scored}) is a counterfactual — a "
                        "mechanism that does not exist in production cannot be "
                        "the product's measurement")
    if not any(v == "no" for v in seen.values()):
        findings.append("[V2] no non-counterfactual arm is present")

    # ── V3 + V4: numerators, denominators, and their equality ─────────────
    shares: dict[str, float] = {}
    totals: dict[str, int] = {}
    pre_shas: dict[str, str] = {}
    for arm in arms:
        line = next((ln for ln in log.find(f"arm={arm} metric=objects ")), None)
        kv = log.kv(line)
        if line is None:
            findings.append(f"[V3] arm {arm} prints no objects line")
            continue
        try:
            num = int(kv["numerator"])
            den = int(kv["denominator"])
            share = float(kv["share"])
        except (KeyError, ValueError):
            findings.append(f"[V3] arm {arm}: numerator/denominator/share missing "
                            f"or unparseable ({kv})")
            continue
        if den <= 0:
            findings.append(f"[V3] arm {arm}: denominator is {den}")
        elif num > den:
            findings.append(f"[V3] arm {arm}: numerator {num} exceeds denominator {den}")
        elif not math.isclose(share, num / den, rel_tol=0, abs_tol=5e-6):
            findings.append(f"[V3] arm {arm}: share {share} is not {num}/{den} "
                            f"({num / den:.6f}) — the share is not the printed counts")
        shares[arm] = share
        totals[arm] = den
        pre = log.kv(next((ln for ln in log.find(f"arm={arm} ")
                           if "preg_embed_sha=" in ln), None))
        if pre.get("preg_embed_sha"):
            pre_shas[arm] = pre["preg_embed_sha"]
    state["shares"] = shares

    # ── V3 (cont.): the `e1` line reconciles with the arm lines ───────────
    # The `e1` line is the one line the VERDICT is read from (V9/V10), and it
    # prints every operand the arm lines print: the before and after shares and
    # the reduction between them. Without this reconciliation the log could
    # claim the shipped arm reduced E1 by the COUNTERFACTUAL's margin — the
    # number this bundle exists to attribute to a mechanism that does not exist —
    # and pass every other check, because V3 reconciled only the arm lines with
    # themselves. Within-log arithmetic on printed counts, the same class of
    # check V3 already does: it still cannot detect a wholly forged log.
    before_arm = next((a for a in arms
                       if seen.get(a) == "no" and a != scored), None)
    e1_kv = log.kv(log.one("e1 "))
    if before_arm is None or scored not in shares or before_arm not in shares:
        findings.append("[V3] the e1 line cannot be reconciled: the before arm "
                        f"({before_arm!r}) or the scored arm ({scored!r}) prints "
                        "no objects share")
    else:
        try:
            e1_before = float(e1_kv["before_share"])
            e1_after = float(e1_kv["after_share"])
            e1_reduction = float(e1_kv["reduction"])
        except (KeyError, ValueError):
            findings.append("[V3] the e1 line does not print before_share, "
                            f"after_share and reduction ({e1_kv})")
        else:
            if not math.isclose(e1_before, shares[before_arm], rel_tol=0,
                                abs_tol=5e-6):
                findings.append(
                    f"[V3] the e1 line's before_share={e1_before} is not the "
                    f"{before_arm} arm's share={shares[before_arm]} — the line "
                    f"the verdict is read from disagrees with its own counts")
            if not math.isclose(e1_after, shares[scored], rel_tol=0,
                                abs_tol=5e-6):
                findings.append(
                    f"[V3] the e1 line's after_share={e1_after} is not the "
                    f"scored arm's ({scored}) share={shares[scored]}")
            expected_reduction = shares[before_arm] - shares[scored]
            if not math.isclose(e1_reduction, expected_reduction, rel_tol=0,
                                abs_tol=5e-6):
                findings.append(
                    f"[V3] the e1 line's reduction={e1_reduction} is not the "
                    f"arm lines' difference (before - after = "
                    f"{expected_reduction:.6f}) — the movement claimed is not "
                    f"the printed counts")

    # ── V3 (cont.) / V5-V7: EVERY criterion line reconciles with its arm lines ──
    # `e1` is reconciled above. The three sibling criteria are printed by the
    # probe from the same `census()` and were, until the #9110 round-6 review,
    # never checked against the `arm=` lines they summarize — so the anti-gaming
    # control could print one number while its arm table printed another, and a
    # state-value table could shrink under a `PASS`, with every V-check green.
    # Same within-log arithmetic class as V3: it still cannot detect a wholly
    # forged log, only a log that disagrees with itself.
    criteria_pairs = (
        # (finding tag, criterion, arm metric line, arm metric key,
        #  criterion before key, criterion after key)
        ("V5", "e1-control", "real_facts", "count",
         "before_real_facts", "after_real_facts"),
        ("V6", "e2-state", "state_value_points", "values", "before", "after"),
        ("V7", "e2-atomic", "atomic_points", "atomic", "before", "after"),
    )

    def arm_metric(arm: str | None, metric: str) -> dict[str, str]:
        return log.kv(next((ln for ln in log.find(f"arm={arm} metric={metric} ")),
                           None))

    for tag, name, metric, arm_key, kb, ka in criteria_pairs:
        prefix = "e2-atomic before=" if name == "e2-atomic" else f"{name} "
        crit = log.kv(log.one(prefix))
        for crit_key, arm in ((kb, before_arm), (ka, scored)):
            printed = crit.get(crit_key, "")
            if not printed.isdigit():
                continue  # V5/V6/V7 already report the missing side
            arm_line = arm_metric(arm, metric)
            arm_value = arm_line.get(arm_key, "")
            if not arm_value.isdigit():
                findings.append(
                    f"[{tag}] the {name} line cannot be reconciled: arm {arm!r} "
                    f"prints no {metric} {arm_key}=")
            elif int(printed) != int(arm_value):
                findings.append(
                    f"[{tag}] the {name} line's {crit_key}={printed} is not the "
                    f"{arm} arm's {metric} {arm_key}={arm_value} — the line the "
                    f"criterion is read from disagrees with its own counts")

    # The two E2 criteria carry the anti-gaming control, so each verdict is checked
    # against its own printed counts: a loss may never read as a pass (#9110 P1).
    for tag, name, _metric, _key, kb, ka in criteria_pairs[1:]:
        prefix = "e2-atomic before=" if name == "e2-atomic" else f"{name} "
        crit = log.kv(log.one(prefix))
        if crit.get(kb, "").isdigit() and crit.get(ka, "").isdigit():
            before_n, after_n = int(crit[kb]), int(crit[ka])
            if after_n < before_n:
                findings.append(
                    f"[{tag}] the {name} count FELL ({before_n} → {after_n}) — "
                    f"the property this criterion exists to protect was lost")
            verdict = crit.get("verdict", "")
            if verdict == "PASS" and after_n < before_n:
                findings.append(
                    f"[{tag}] {name} is PASS while its own counts fell "
                    f"({before_n} → {after_n})")
            if verdict == "FAIL" and after_n >= before_n:
                findings.append(
                    f"[{tag}] {name} is FAIL while its own counts did not fall "
                    f"({before_n} → {after_n})")

    gate_arms = [a for a in arms if seen.get(a) == "no"]
    cf = [a for a in arms if seen.get(a) == "yes"]
    gate_shas = {pre_shas.get(a) for a in gate_arms if pre_shas.get(a)}
    if len(gate_shas) > 1:
        findings.append(f"[V4] the non-counterfactual arms do not share one "
                        f"pre-gate population ({sorted(gate_shas)}) — different "
                        f"corpora make the before/after delta meaningless")
    if not gate_shas:
        findings.append("[V4] no pre-gate population sha is recorded for the "
                        "non-counterfactual arms")
    mint = log.kv(log.one("mint_skip "))
    if cf and not mint.get("dropped_objects"):
        findings.append("[V4] a counterfactual arm is present but the mint-skip "
                        "transform does not state how many objects it removed")
    if mint.get("dropped_objects", "").isdigit():
        n_drop = int(mint["dropped_objects"])
        declared = sum(1 for ln in log.find("arm=") if "dropped kind=object" in ln
                       and "mint-time-transform" in ln)
        if declared != n_drop:
            findings.append(f"[V4] mint-skip removed {n_drop} objects but the drop "
                            f"table attributes {declared} to the mint-time "
                            f"transform — the removal is not fully accounted for")

    # ── V5: the anti-gaming control, both sides ───────────────────────────
    control = log.kv(log.one("e1-control "))
    for key in ("before_real_facts", "after_real_facts"):
        if not control.get(key, "").isdigit():
            findings.append(f"[V5] the real-fact control is not printed on both "
                            f"sides ({key}= missing) — the share could fall only "
                            f"because fewer facts were extracted")
    if (control.get("before_real_facts", "").isdigit()
            and control.get("after_real_facts", "").isdigit()
            and int(control["after_real_facts"]) < int(control["before_real_facts"])):
        findings.append("[V5] the real-fact count FELL "
                        f"({control['before_real_facts']} → "
                        f"{control['after_real_facts']})")

    # ── V6: state values, both sides, and every loss with its cause ───────
    st = log.kv(log.one("e2-state "))
    for key in ("before", "after"):
        if not st.get(key, "").isdigit():
            findings.append(f"[V6] the state-value count is not printed on both "
                            f"sides ({key}= missing)")
    for ln in log.lines:
        m = DROP_POINT_LINE.match(ln)
        if not m:
            continue
        cause = m.group("cause")
        vals = m.group("values")
        if cause in ("", "n/a", "None"):
            findings.append(f"[V6] a dropped Point names no cause: {ln[:120]}")
        if vals not in ("[]", "") and cause == "":
            findings.append(f"[V6] a dropped Point carried values but no cause: "
                            f"{ln[:120]}")

    # ── V7: atomicity, and SPLIT named as the reason it cannot move ───────
    at = log.kv(log.one("e2-atomic before="))
    for key in ("before", "after", "fused_after"):
        if not at.get(key, "").isdigit():
            findings.append(f"[V7] the atomic-Point count is not printed ({key}= "
                            f"missing)")
    if not log.one("split-implemented=no"):
        findings.append("[V7] the log does not state that SPLIT is unimplemented — "
                        "an atomicity failure could then be read as a pass")
    for ln in log.find("e2-atomic fused "):
        m = FUSED_LINE.match(ln)
        if not m or m.group("cause") in ("", "n/a", "None"):
            findings.append(f"[V7] a fused Point names no cause: {ln[:120]}")

    # ── V8: coverage honesty ──────────────────────────────────────────────
    cov = log.kv(log.one("coverage rows="))
    if not cov:
        findings.append("[V8] no coverage table — a gate measured only on the "
                        "class it can detect reads as a pass")
    else:
        for key in ("rows", "in_scope", "positively_classified"):
            if not cov.get(key, "").isdigit():
                findings.append(f"[V8] coverage does not state {key}=")
        # ABSENT is not EMPTY (#9110 review P2). The table is complete when every
        # class has a detector — that is the outcome the semantic half (#4894) is
        # meant to produce — so an empty list is a PASS. Only a field that is not
        # there at all is a finding: the pair is indistinguishable in `kv()` (the
        # KV regex needs a non-space value), so presence is read off the raw line.
        cov_line = log.one("coverage rows=") or ""
        if "undetected_classes=" not in cov_line:
            findings.append("[V8] coverage does not state undetected_classes= — a "
                            "missing field cannot be read as an all-detected table")
            state["undetected_classes"] = []
        else:
            undetected = [c for c in cov.get("undetected_classes", "").split(",")
                          if c]
            state["undetected_classes"] = undetected
        per_class = [log.kv(ln) for ln in log.find("coverage class=")]
        declared = sum(int(kv.get("rows", "0") or 0) for kv in per_class)
        if per_class and cov.get("rows", "").isdigit() and \
                declared != int(cov["rows"]):
            findings.append(f"[V8] the per-class rows sum to {declared} but the "
                            f"coverage header says {cov['rows']}")

    # ── V9: pre-registration binding ──────────────────────────────────────
    pre = log.kv(log.one("prereg="))
    if not pre.get("prereg"):
        findings.append("[V9] the log does not record the pre-registration commit "
                        "it is scored against")
    if not pre.get("artifact"):
        findings.append("[V9] the log does not name the pre-registration artifact")
    unprereg = pre.get("prereg") == "UNPREREGISTERED"
    # The threshold the run USED must be the one the artifact FROZE. Without this
    # pair a log could name a pre-registration commit and still score itself
    # against a bar it chose — which is the whole failure the pre-registration
    # exists to prevent.
    frozen = log.kv(log.one("prereg_e1_threshold=")).get("prereg_e1_threshold", "")
    used = log.kv(log.one("e1 ")).get("threshold", "")
    if not unprereg:
        if frozen in ("", "n/a", "ABSENT"):
            findings.append("[V9] the log names a pre-registration but does not "
                            "print the E1 threshold that artifact froze")
        elif used != frozen:
            findings.append(f"[V9] the run scored against threshold={used} but the "
                            f"frozen artifact states {frozen} — the bar moved")
    if not log.one("prereg_e2_floor="):
        findings.append("[V9] the log does not state whether an E2 threshold was "
                        "consumed")
    for name in CRITERIA:
        prefix = name if name.endswith("=") else f"{name} "
        line = log.one(prefix)
        verdict = log.kv(line).get("verdict", "")
        if verdict not in VERDICTS:
            findings.append(f"[V9] criterion {name} carries no verdict "
                            f"(got {verdict!r})")
        if unprereg and verdict == "PASS":
            findings.append(f"[V9] criterion {name} is PASS with no frozen "
                            f"threshold — a verdict was invented, not measured")
    state["verdicts"] = {
        name: log.kv(log.one(name if name.endswith("=") else f"{name} "))
        .get("verdict")
        for name in CRITERIA
    }

    # ── V10: the result line agrees with the criteria ─────────────────────
    result = log.kv(log.one("result=")).get("result", "")
    exit_kv = log.kv(log.one("exit=")).get("exit", "")
    if result not in ("GREEN", "RED", "UNPREREGISTERED"):
        findings.append(f"[V10] no result line (got {result!r})")
    if result == "GREEN" and any(v != "PASS" for v in state["verdicts"].values()):
        findings.append("[V10] the log reports GREEN while a criterion is not PASS")
    # ... and the two REVERSE directions. Without them a malformed
    # pre-registration that left every criterion UNPREREGISTERED could still be
    # reported as RED ("scored and something red") when nothing was scored, and
    # a scored log could print `FAIL` under an UNPREREGISTERED result.
    if result == "RED" and not any(v == "FAIL" for v in state["verdicts"].values()):
        findings.append("[V10] the log reports RED while no criterion is FAIL — "
                        "nothing was scored red")
    if result == "UNPREREGISTERED" and any(
            v != "UNPREREGISTERED" for v in state["verdicts"].values()):
        findings.append("[V10] an UNPREREGISTERED log carries a scored criterion "
                        "verdict — no frozen bar existed")
    if result == "UNPREREGISTERED" and exit_kv != "3":
        findings.append(f"[V10] an UNPREREGISTERED log must exit 3, not {exit_kv}")
    if result in ("GREEN", "RED") and exit_kv not in ("0", "1"):
        findings.append(f"[V10] a scored log must exit 0 or 1, not {exit_kv}")
    # The threshold-free observation is a CONCLUSION about the reduction, so it
    # must agree with the reduction printed beside it: "the share fell" and
    # "NO_REDUCTION" cannot both be true.
    observed = e1_kv.get("observed", "")
    try:
        reduction = float(e1_kv["reduction"])
    except (KeyError, ValueError):
        reduction = None
    if observed not in ("NO_REDUCTION", "REDUCTION_OBSERVED"):
        findings.append(f"[V10] the e1 line carries no observation "
                        f"(got {observed!r})")
    elif reduction is None:
        findings.append("[V10] the e1 line carries no reduction to observe")
    elif observed == "REDUCTION_OBSERVED" and reduction <= 0:
        findings.append(
            f"[V10] the e1 line observes a REDUCTION with reduction="
            f"{reduction} — the observation contradicts the arithmetic beside it")
    elif observed == "NO_REDUCTION" and reduction > 0:
        findings.append(
            f"[V10] the e1 line observes NO_REDUCTION with reduction="
            f"{reduction} — the observation contradicts the arithmetic beside it")
    state["result"] = result
    return findings, state


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="structurally check an E1+E2 log (#9076)")
    ap.add_argument("log")
    ap.add_argument("--require-green", action="store_true",
                    help="exit 1 unless the log's result is GREEN")
    args = ap.parse_args(argv)

    path = Path(args.log)
    if not path.exists():
        print(f"no such log: {path}", file=sys.stderr)
        return 2
    findings, state = check(path.read_text(encoding="utf-8"))
    for f in findings:
        print(f)
    if findings:
        print(f"\n{len(findings)} finding(s) — the evidence log is not structurally sound")
        return 1
    print(f"structurally sound: {len(state.get('arms') or [])} arm(s), "
          f"result={state.get('result')}, "
          f"undetected classes={','.join(state.get('undetected_classes') or [])}")
    if args.require_green and state.get("result") != "GREEN":
        print(f"[V11] the log's result is {state.get('result')}, not GREEN")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

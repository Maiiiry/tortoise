"""The E1+E2 evidence log's structural gate (#9076).

**What this closes.** An evidence log is a claim. This is the mechanical check
that the CLAIM IS STRUCTURED so it cannot be read as more than it is: every
numerator has a denominator, the denominators are the same corpus, the anti-gaming
control is present, every dropped value names its cause, the coverage deficit is
stated, and no criterion is called PASS without a pre-registered threshold. It
does **not** re-derive the numbers — the probe owns them — and it cannot detect a
forged log (a plausible number would pass), which is the same stated limit the
sibling capstone gate carries.

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
        undetected = [c for c in cov.get("undetected_classes", "").split(",") if c]
        if not undetected:
            findings.append("[V8] coverage claims every class has a detector")
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
    if result == "UNPREREGISTERED" and exit_kv != "3":
        findings.append(f"[V10] an UNPREREGISTERED log must exit 3, not {exit_kv}")
    if result in ("GREEN", "RED") and exit_kv not in ("0", "1"):
        findings.append(f"[V10] a scored log must exit 0 or 1, not {exit_kv}")
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

#!/usr/bin/env python3
"""The capstone pre-registration gate (#9075) — the check behind ``#7949``'s method constraint.

The epic states the constraint verbatim: *"Pre-register before running — corpus, split, scoring —
the discipline that made `#3011` usable."* A registration that is a document only is a document that
can be edited after the numbers land, and a threshold chosen after the run is the exact failure the
constraint exists to prevent. This tool is what makes the artefact executable.

It verifies four things, and it fails on each of them for a specific reason:

1. **Every criteria row carries a metric, a denominator AND a numeric threshold.** A row with a
   placeholder (*"to be decided"*, *"TBD"*, an em dash) is a row that will be decided later — which is
   the same failure as no registration at all. E1–E5 must each be present (E2 may be split into
   sub-rows, e.g. ``E2a``/``E2b``, and every sub-row must itself be complete).
2. **The registration strictly precedes every evidence log.** The commit that *adds*
   ``PRE-REGISTRATION.md`` must be **older** than the commit that adds each ``*.log`` beside it.
   "Same second" is not "before" — an ordering that cannot be shown is not an ordering.
3. **E4's denominator is 15, never 4.** The planted-operator gold is 15 edges across all seven
   sessions; the 4-edge denominator it replaced swung 0–2 of 4 on *identical* code, so a four-edge
   rate is not evidence of anything. The phrase is rejected wherever it appears in the document,
   including in a historical aside — a document that writes the bad denominator down is a document a
   later reader will copy it out of.
4. **The pins still describe the tree.** Every denominator in the registration (``fixtures_hash``,
   90 planted units, 15 operator edges, 14 value-bearing units, 29 marker sentences, the 25/15/9/6
   salience measurement, and the TUNE/HELD-OUT split) is **re-derived from the corpus** and compared
   with the pins block. Without this, a corpus change silently re-bases every threshold and the
   pre-registration becomes decoration.

What it deliberately does NOT do: it does not judge whether the thresholds are *right*. That is the
argument in the document, and an argument is reviewed, not grepped.

Usage::

    uv run python tools/capstone_preregistration_check.py --dir docs/evidence/7949-capstone
    uv run python tools/capstone_preregistration_check.py --dir <dir> --no-git   # pre-commit
    uv run python tools/capstone_preregistration_check.py --dir <dir> --json

Exit 0 only when every check passes. Exit 1 on any failure, 2 on a usage/parse error (no document,
unparseable pins) — a check that cannot read its subject must never report success.
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

PREREG_NAME = "PRE-REGISTRATION.md"
PINS_FENCE = "```yaml prereregistration-pins"
EVIDENCE_LOG_GLOB = "*.log"

#: The rows the epic requires. ``E2`` may be carried by sub-rows (``E2a``/``E2b``); a required id is
#: satisfied when at least one row's id equals it or starts with it.
REQUIRED_CRITERIA = ("E1", "E2", "E3", "E4", "E5")

#: Cell values that mean "decided later", i.e. not decided. Compared case-folded, stripped of
#: markdown emphasis and punctuation.
PLACEHOLDER_CELLS = frozenset(
    {"", "tbd", "tbc", "todo", "to be decided", "to be determined", "n/a", "na", "none",
     "-", "--", "—", "–", "?", "??", "..."}
)

#: A threshold must carry a comparator AND a number. Prose without both is not a threshold.
COMPARATORS = ("<=", ">=", "<", ">", "=", "≤", "≥")

#: The four-edge denominator, in any rate phrasing. ``\\d+ / 4`` — and never ``4 / 40``, hence the
#: trailing guard.
FOUR_EDGE_RATE = re.compile(r"(?<![\d.])(\d+)\s*/\s*4(?![\d.])")


class CheckError(Exception):
    """A check could not be performed (distinct from a check that failed)."""


# ── document parsing ────────────────────────────────────────────────────────


def _cells(line: str) -> list[str]:
    """Split a markdown table row into stripped cells (leading/trailing pipes dropped)."""
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [c.strip() for c in body.split("|")]


def _plain(cell: str) -> str:
    """Cell text with markdown emphasis/code markers removed, for placeholder comparison."""
    return re.sub(r"[`*_]", "", cell).strip().strip(".").strip()


def is_placeholder(cell: str) -> bool:
    return _plain(cell).casefold() in PLACEHOLDER_CELLS


def parse_criteria_rows(text: str) -> list[dict[str, str]]:
    """The criteria table: the one whose header names metric, denominator and threshold.

    Returns one dict per row: ``{"id", "metric", "denominator", "threshold", "line"}``. A document
    with no such table returns ``[]`` — which the caller reports as a missing-table failure rather
    than silently passing an empty row set.
    """
    lines = text.splitlines()
    rows: list[dict[str, str]] = []
    for i, line in enumerate(lines):
        if not line.lstrip().startswith("|"):
            continue
        header = [c.casefold() for c in _cells(line)]
        if not any("metric" in c for c in header):
            continue
        if not any("denominator" in c for c in header):
            continue
        if not any("threshold" in c for c in header):
            continue
        # Column positions, so a re-ordered table still parses.
        idx_metric = next(n for n, c in enumerate(header) if "metric" in c)
        idx_denom = next(n for n, c in enumerate(header) if "denominator" in c)
        idx_thresh = next(n for n, c in enumerate(header) if "threshold" in c)
        for j in range(i + 1, len(lines)):
            row = lines[j]
            if not row.lstrip().startswith("|"):
                break
            cells = _cells(row)
            if all(re.fullmatch(r":?-{2,}:?", _plain(c)) for c in cells if c):
                continue  # the |---|---| separator
            if len(cells) <= max(idx_metric, idx_denom, idx_thresh):
                rows.append({"id": _plain(cells[0]) if cells else "?", "metric": "",
                             "denominator": "", "threshold": "", "line": str(j + 1)})
                continue
            rows.append(
                {
                    "id": _plain(cells[0]),
                    "metric": cells[idx_metric],
                    "denominator": cells[idx_denom],
                    "threshold": cells[idx_thresh],
                    "line": str(j + 1),
                }
            )
    return rows


def parse_pins(text: str) -> dict:
    """The ``yaml prereregistration-pins`` fenced block, parsed as YAML."""
    start = text.find(PINS_FENCE)
    if start < 0:
        raise CheckError(f"no `{PINS_FENCE}` block — the pins cannot be read")
    body_start = text.find("\n", start)
    if body_start < 0:
        raise CheckError("pins fence is the last line — no YAML body")
    end = text.find("\n```", body_start)
    if end < 0:
        raise CheckError("pins fence is unterminated")
    try:
        import yaml  # declared dependency (pyproject: pyyaml>=6.0)
    except ImportError as exc:  # pragma: no cover - environment guard
        raise CheckError(f"pyyaml is required to read the pins: {exc}") from exc
    try:
        pins = yaml.safe_load(text[body_start:end])
    except Exception as exc:  # noqa: BLE001 - any YAML error is a parse error here
        raise CheckError(f"pins block is not valid YAML: {exc}") from exc
    if not isinstance(pins, dict):
        raise CheckError("pins block did not parse to a mapping")
    return pins


# ── the corpus re-derivation ────────────────────────────────────────────────


def _import_from_repo(dotted: str):
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    return importlib.import_module(dotted)


def _sentence_count(content: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", content) if s.strip()]


def _marker_pattern(lexemes: list[str]) -> re.Pattern[str]:
    """Word-bounded, case-insensitive; each lexeme tolerates a plural/past suffix.

    The registration declares the LEXEMES; the suffixes are mechanics (a measurement that missed
    "seems" because it keyed on "seem" would understate the denominator in one direction only).
    """
    parts = [re.escape(lex) + r"(?:s|es|d|ed)?" for lex in lexemes]
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b", re.IGNORECASE)


def derive_corpus_facts(pins: dict) -> dict:
    """Re-derive every pinned corpus quantity from the tree at HEAD."""
    corpus = _import_from_repo("tests.eval.write_path.corpus")
    dd = _import_from_repo("tortoise.definite_description_backfill")

    root = REPO_ROOT / str(pins.get("corpus", {}).get("root", "tests/eval/write_path"))
    if not root.is_dir():
        raise CheckError(f"corpus root not found: {root}")

    session_ids = sorted(corpus.session_ids(root))
    units_total = 0
    operator_edges = 0
    excluded: set[str] = set(pins.get("corpus", {}).get("value_excluded_unit_ids") or [])
    value_ids: list[str] = []
    per_session_units: dict[str, int] = {}
    per_session_ops: dict[str, int] = {}
    per_session_values: dict[str, int] = {}
    for sid in session_ids:
        gold = corpus.load_gold(sid, root)
        units = gold.get("planted_units") or []
        per_session_units[sid] = len(units)
        units_total += len(units)
        per_session_ops[sid] = len(gold.get("planted_operators") or [])
        operator_edges += per_session_ops[sid]
        n_value = 0
        for unit in units:
            anchor = str(unit.get("verbatim_anchor") or "")
            if not re.search(r"\d", anchor):
                continue
            if str(unit.get("id")) in excluded:
                continue
            n_value += 1
            value_ids.append(str(unit.get("id")))
        per_session_values[sid] = n_value

    # An exclusion that does not exist, or that excludes nothing digit-bearing, is a stale pin.
    all_ids = set()
    for sid in session_ids:
        for unit in corpus.load_gold(sid, root).get("planted_units") or []:
            all_ids.add(str(unit.get("id")))
    missing_exclusions = sorted(excluded - all_ids)

    lexemes = list(pins.get("corpus", {}).get("hedge_scope_lexemes") or [])
    pattern = _marker_pattern(lexemes) if lexemes else None
    per_session_markers: dict[str, int] = {}
    sentences_total = 0
    markers_total = 0
    for sid in session_ids:
        fixture = json.loads((root / "fixtures" / f"{sid}.json").read_text())
        n = 0
        for turn in fixture.get("conversation") or []:
            for sentence in _sentence_count(str(turn.get("content") or "")):
                sentences_total += 1
                if pattern is not None and pattern.search(sentence):
                    n += 1
        per_session_markers[sid] = n
        markers_total += n

    rows = [
        json.loads(line)
        for line in (root / "salience_labels.jsonl").read_text().splitlines()
        if line.strip()
    ]
    in_scope = [r for r in rows if dd.is_bare_definite_description(r["object_name"])]
    positive = [r for r in rows if dd.is_positively_bare_definite_description(r["object_name"])]
    detected = sorted({r["class"] for r in positive})
    classes = {r["class"] for r in rows}

    return {
        "fixtures_hash": corpus.compute_fixtures_hash(root),
        "sessions": session_ids,
        "planted_units": units_total,
        "planted_operator_edges": operator_edges,
        "value_bearing_units": len(value_ids),
        "value_bearing_ids": sorted(value_ids),
        "hedge_scope_sentences": markers_total,
        "sentences_total": sentences_total,
        "missing_exclusions": missing_exclusions,
        "salience": {
            "rows": len(rows),
            "in_scope": len(in_scope),
            "positively_classified": len(positive),
            "deferred": len(in_scope) - len(positive),
            "detected_classes": detected,
            "undetected_classes": sorted(classes - set(detected)),
        },
        "per_session": {
            sid: {
                "units": per_session_units[sid],
                "operator_edges": per_session_ops[sid],
                "value_bearing_units": per_session_values[sid],
                "hedge_scope_sentences": per_session_markers[sid],
            }
            for sid in session_ids
        },
    }


def _half_counts(derived: dict, ids: list[str]) -> dict[str, int]:
    return {
        "units": sum(derived["per_session"][i]["units"] for i in ids),
        "operator_edges": sum(derived["per_session"][i]["operator_edges"] for i in ids),
        "value_bearing_units": sum(
            derived["per_session"][i]["value_bearing_units"] for i in ids
        ),
        "hedge_scope_sentences": sum(
            derived["per_session"][i]["hedge_scope_sentences"] for i in ids
        ),
    }


# ── git ordering ────────────────────────────────────────────────────────────


def _added_epochs(path: Path) -> list[int]:
    """Commit epochs at which ``path`` was ADDED, oldest-first. Empty when never added."""
    try:
        rel = path.resolve().relative_to(REPO_ROOT)
    except ValueError:
        return []
    try:
        out = subprocess.run(
            ["git", "log", "--diff-filter=A", "--format=%ct", "--", str(rel)],
            cwd=str(REPO_ROOT), capture_output=True, text=True, check=False,
        )
    except OSError:  # pragma: no cover - git absent
        return []
    if out.returncode != 0:
        return []
    epochs = [int(x) for x in out.stdout.split() if x.strip().isdigit()]
    return sorted(epochs)


def _is_tracked(path: Path) -> bool:
    try:
        rel = path.resolve().relative_to(REPO_ROOT)
    except ValueError:
        return False
    out = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(rel)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=False,
    )
    return out.returncode == 0


def check_ordering(prereg: Path, logs: list[Path]) -> list[str]:
    """``[]`` when the registration precedes every log; failure strings otherwise."""
    failures: list[str] = []
    prereg_epochs = _added_epochs(prereg)
    if not prereg_epochs:
        if not logs:
            return failures  # not committed yet, and nothing to be ordered against
        if not _is_tracked(prereg):
            failures.append(
                f"{PREREG_NAME} is not committed, but {len(logs)} evidence log(s) exist — "
                "the ordering cannot be proved, and an unprovable ordering is not an ordering"
            )
            return failures
        failures.append(
            f"{PREREG_NAME} has no add-commit (git log --diff-filter=A found nothing) — "
            "cannot establish that it precedes the evidence logs"
        )
        return failures
    prereg_epoch = min(prereg_epochs)
    for log in logs:
        log_epochs = _added_epochs(log)
        if not log_epochs:
            if _is_tracked(log):
                failures.append(
                    f"{log.name}: tracked but with no add-commit — ordering unprovable"
                )
            continue  # untracked log: it cannot predate a commit that already exists
        log_epoch = min(log_epochs)
        if log_epoch <= prereg_epoch:
            failures.append(
                f"{log.name}: added at {log_epoch} which is not strictly after the "
                f"pre-registration's add-commit {prereg_epoch} — the log may have been scored "
                "against thresholds chosen after it was read"
            )
    return failures


# ── the checks ──────────────────────────────────────────────────────────────


def check_completeness(rows: list[dict[str, str]]) -> list[str]:
    failures: list[str] = []
    if not rows:
        return [
            "no criteria table found — the document must carry a table whose header names "
            "metric, denominator and threshold"
        ]
    for row in rows:
        missing = [
            name
            for name in ("metric", "denominator", "threshold")
            if is_placeholder(row[name])
        ]
        if missing:
            failures.append(
                f"row {row['id']!r} (line {row['line']}) is missing {', '.join(missing)} — "
                "a criteria row must name all three before any run"
            )
    for required in REQUIRED_CRITERIA:
        if not any(r["id"] == required or r["id"].startswith(required) for r in rows):
            failures.append(f"criterion {required} has no row")
    for row in rows:
        if is_placeholder(row["threshold"]):
            continue  # already reported above
        plain = _plain(row["threshold"])
        if not re.search(r"\d", plain):
            failures.append(
                f"row {row['id']!r} (line {row['line']}): threshold {row['threshold']!r} carries "
                "no number"
            )
        elif not any(c in plain for c in COMPARATORS):
            failures.append(
                f"row {row['id']!r} (line {row['line']}): threshold {row['threshold']!r} carries "
                "no comparator (<=, >=, <, >, =) — a number without a direction is not a bar"
            )
    return failures


def check_e4_denominator(rows: list[dict[str, str]], text: str, pins: dict) -> list[str]:
    failures: list[str] = []
    e4_rows = [r for r in rows if r["id"] == "E4"]
    if not e4_rows:
        failures.append("no E4 row — the planted-operator denominator cannot be checked")
    for row in e4_rows:
        plain = _plain(row["denominator"])
        if not re.search(r"\b15\b", plain):
            failures.append(
                f"E4 (line {row['line']}): denominator {row['denominator']!r} does not state 15 — "
                "the planted-operator gold is 15 edges"
            )
        if FOUR_EDGE_RATE.search(plain):
            failures.append(
                f"E4 (line {row['line']}): a four-edge denominator rate appears in the denominator "
                "cell — that denominator cannot separate a fix from variance"
            )
    pinned = pins.get("e4_denominator")
    if pinned != 15:
        failures.append(f"pins.e4_denominator is {pinned!r}, must be 15")
    for match in FOUR_EDGE_RATE.finditer(text):
        line_no = text[: match.start()].count("\n") + 1
        failures.append(
            f"line {line_no}: four-edge denominator rate {match.group(0)!r} — the E4 denominator "
            "is 15; the 4-edge history is stated in words, never as a rate to be copied"
        )
    return failures


def check_pins(pins: dict, derived: dict) -> list[str]:
    failures: list[str] = []
    corpus = pins.get("corpus") or {}
    salience_pins = pins.get("salience_labels") or {}
    split = pins.get("split") or {}

    def cmp(label: str, want, got) -> None:
        if want != got:
            failures.append(f"pin {label} is {want!r} but the tree gives {got!r}")

    cmp("corpus.fixtures_hash", corpus.get("fixtures_hash"), derived["fixtures_hash"])
    cmp("corpus.sessions", corpus.get("sessions"), len(derived["sessions"]))
    cmp("corpus.planted_units", corpus.get("planted_units"), derived["planted_units"])
    cmp("corpus.planted_operator_edges", corpus.get("planted_operator_edges"),
        derived["planted_operator_edges"])
    cmp("corpus.value_bearing_units", corpus.get("value_bearing_units"),
        derived["value_bearing_units"])
    cmp("corpus.hedge_scope_sentences", corpus.get("hedge_scope_sentences"),
        derived["hedge_scope_sentences"])
    cmp("salience_labels.rows", salience_pins.get("rows"), derived["salience"]["rows"])
    cmp("salience_labels.in_scope", salience_pins.get("in_scope"),
        derived["salience"]["in_scope"])
    cmp("salience_labels.positively_classified", salience_pins.get("positively_classified"),
        derived["salience"]["positively_classified"])
    cmp("salience_labels.deferred", salience_pins.get("deferred"),
        derived["salience"]["deferred"])
    cmp("salience_labels.detected_classes", salience_pins.get("detected_classes"),
        derived["salience"]["detected_classes"])

    if derived["missing_exclusions"]:
        failures.append(
            "corpus.value_excluded_unit_ids names ids absent from the gold: "
            f"{derived['missing_exclusions']}"
        )

    tune = list(split.get("tune") or [])
    held = list(split.get("held_out") or [])
    sessions = list(derived["sessions"])
    if sorted(tune + held) != sorted(sessions):
        failures.append(
            f"the split is not a partition of the corpus: tune={tune} held_out={held} "
            f"sessions={sessions}"
        )
    if set(tune) & set(held):
        failures.append(f"the split halves overlap: {sorted(set(tune) & set(held))}")
    unknown = sorted(set(tune + held) - set(sessions))
    if unknown:
        failures.append(f"the split names sessions that are not in the corpus: {unknown}")
    if not failures:
        cmp("split.tune_counts", split.get("tune_counts"), _half_counts(derived, tune))
        cmp("split.held_out_counts", split.get("held_out_counts"), _half_counts(derived, held))

    rows = pins.get("required_criteria_rows")
    if list(rows or []) != list(REQUIRED_CRITERIA):
        failures.append(
            f"pins.required_criteria_rows is {rows!r}, must be {list(REQUIRED_CRITERIA)}"
        )
    return failures


# ── entry point ─────────────────────────────────────────────────────────────


def run(directory: Path, use_git: bool = True, use_corpus: bool = True) -> dict:
    prereg = directory / PREREG_NAME
    if not prereg.is_file():
        raise CheckError(f"no {PREREG_NAME} in {directory}")
    text = prereg.read_text()
    rows = parse_criteria_rows(text)
    pins = parse_pins(text)

    report: dict = {
        "document": str(prereg.relative_to(REPO_ROOT)) if prereg.is_relative_to(REPO_ROOT)
        else str(prereg),
        "rows": [{"id": r["id"], "line": r["line"]} for r in rows],
        "checks": {},
    }
    failures: list[str] = []
    failures += check_completeness(rows)
    failures += check_e4_denominator(rows, text, pins)

    logs = sorted(directory.glob(EVIDENCE_LOG_GLOB))
    report["evidence_logs"] = [p.name for p in logs]
    if use_git:
        failures += check_ordering(prereg, logs)

    if use_corpus:
        derived = derive_corpus_facts(pins)
        report["derived"] = {
            "fixtures_hash": derived["fixtures_hash"],
            "planted_units": derived["planted_units"],
            "planted_operator_edges": derived["planted_operator_edges"],
            "value_bearing_units": derived["value_bearing_units"],
            "hedge_scope_sentences": derived["hedge_scope_sentences"],
            "sentences_total": derived["sentences_total"],
            "salience": derived["salience"],
        }
        failures += check_pins(pins, derived)

    report["failures"] = failures
    report["ok"] = not failures
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Verify the extractor-v4 capstone pre-registration (#9075)."
    )
    ap.add_argument("--dir", default="docs/evidence/7949-capstone",
                    help="directory holding PRE-REGISTRATION.md and its evidence logs")
    ap.add_argument("--no-git", action="store_true",
                    help="skip the commit-ordering check (pre-commit authoring)")
    ap.add_argument("--no-corpus", action="store_true",
                    help="skip the corpus pin re-derivation")
    ap.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = ap.parse_args(argv)

    directory = Path(args.dir)
    if not directory.is_absolute():
        directory = REPO_ROOT / directory
    try:
        report = run(directory, use_git=not args.no_git, use_corpus=not args.no_corpus)
    except CheckError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"pre-registration: {report['document']}")
        print(f"criteria rows: {', '.join(r['id'] for r in report['rows']) or '(none)'}")
        print(f"evidence logs: {len(report['evidence_logs'])}"
              + (f" ({', '.join(report['evidence_logs'])})" if report["evidence_logs"] else ""))
        if "derived" in report:
            d = report["derived"]
            print(
                "re-derived: units={planted_units} operators={planted_operator_edges} "
                "values={value_bearing_units} markers={hedge_scope_sentences}"
                "/{sentences_total} salience={salience[in_scope]}-in-scope"
                "/{salience[positively_classified]}-classified".format(**d)
            )
        if report["ok"]:
            print("OK — every criteria row is complete, the ordering holds, "
                  "the E4 denominator is 15, and the pins match the tree")
        else:
            print(f"FAIL — {len(report['failures'])} problem(s):")
            for f in report["failures"]:
                print(f"  - {f}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

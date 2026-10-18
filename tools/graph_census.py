#!/usr/bin/env python3
"""Census a logical graph dump OFFLINE — #8882 **Phase 1**.

Read-only. It never connects to a graph, never writes one, and never drops a
node: the input is a ``tortoise-logical-dump-v1`` snapshot (what
``hosted_backup.dump_graph`` emits, and what ``tortoise export`` wraps) and the
output is a report of counts per class plus the proposed keep/drop predicate.

    # measure one snapshot, with 5 sample nodes per rule for a human to eyeball
    python tools/graph_census.py --dump snapshot.json --samples 5 --out census.json

    # the plan's own comparison: the curated snapshot against the hosted graph's
    python tools/graph_census.py --dump local.json --diff hosted.json --out diff.json

    # re-cost the predicate at a different fragment cut, or with a subset of rules
    python tools/graph_census.py --dump snapshot.json --fragment-max-chars 60
    python tools/graph_census.py --dump snapshot.json --rules extracted,isolated

Exit codes: ``0`` ok · ``2`` refused (problems on stderr) · ``1`` usage/IO.

Why the refusals exist: a census that silently measured a different population
than the re-typing pass will consume is worse than no census — it would be a
number an owner decides on. So a malformed dump, an unknown rule id, a dangling
edge or a duplicate node identity is refused with the full problem list rather
than reported around.
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/graph_census.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/graph_census.py …`"
    )

import argparse
import json
from pathlib import Path

# The tool ships in the repo it operates on: put the checkout ahead of any
# installed copy, so the mechanics that run are the mechanics under test.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.graph_census import (
    DEFAULT_RULES,
    FRAGMENT_MAX_CHARS,
    CensusError,
    census,
    diff_census,
)


def _load_json(path: Path, what: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"{what} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{what} is not valid JSON ({path}): {exc}") from exc


def _dump_json(payload: object, path: Path | None, what: str) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if path is None:
        sys.stdout.write(text)
        return
    path.write_text(text, encoding="utf-8")
    print(f"wrote {what}: {path}", file=sys.stderr)


def _parse_rules(raw: str) -> list[str]:
    rules = [item.strip() for item in raw.split(",") if item.strip()]
    unknown = [rule for rule in rules if rule not in DEFAULT_RULES]
    if unknown:
        raise SystemExit(
            f"--rules: unknown id(s) {sorted(unknown)!r}; known ids are "
            f"{list(DEFAULT_RULES)!r}"
        )
    return rules


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Census a logical graph dump offline (#8882 Phase 1)."
    )
    parser.add_argument(
        "--dump",
        required=True,
        type=Path,
        help="tortoise-logical-dump-v1 JSON (the snapshot to measure)",
    )
    parser.add_argument(
        "--diff",
        type=Path,
        metavar="OTHER_DUMP",
        help="census OTHER_DUMP as well and report every count that moved",
    )
    parser.add_argument(
        "--rules",
        type=_parse_rules,
        default=list(DEFAULT_RULES),
        help=f"comma-separated subset of the proposed predicate (default: {','.join(DEFAULT_RULES)})",
    )
    parser.add_argument(
        "--fragment-max-chars",
        type=int,
        default=FRAGMENT_MAX_CHARS,
        metavar="N",
        help=f"fragment threshold in characters (default: {FRAGMENT_MAX_CHARS})",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=0,
        metavar="N",
        help="include up to N dropped nodes per rule for human review (default: 0)",
    )
    parser.add_argument("--out", type=Path, help="write the report here (default: stdout)")
    args = parser.parse_args(argv)

    dump = _load_json(args.dump, "dump")
    if not isinstance(dump, dict):
        raise SystemExit(f"dump is not a JSON object: {args.dump}")

    try:
        if args.diff:
            other = _load_json(args.diff, "diff dump")
            if not isinstance(other, dict):
                raise SystemExit(f"--diff is not a JSON object: {args.diff}")
            report = diff_census(
                dump,
                other,
                rules=args.rules,
                fragment_max_chars=args.fragment_max_chars,
            )
        else:
            report = census(
                dump,
                rules=args.rules,
                fragment_max_chars=args.fragment_max_chars,
                samples=args.samples,
            )
    except CensusError as exc:
        print("REFUSED — no report was written:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2

    _dump_json(report, args.out, "census")

    if args.diff:
        print(f"changed counts: {report['changed_paths']}", file=sys.stderr)
        return 0
    predicate = report["predicate"]
    print(
        f"nodes: {report['totals']['nodes']} · edges: {report['totals']['edges']} · "
        f"predicate keeps {predicate['keep']} / drops {predicate['drop']} "
        f"(droppable {predicate['droppable']})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

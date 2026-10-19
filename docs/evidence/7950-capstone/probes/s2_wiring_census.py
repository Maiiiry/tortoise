#!/usr/bin/env python3
"""S2 census — is truth<->cache divergence OBSERVED, or merely checkable?

Counts PRODUCTION (non-test) call sites of the three modules the two-store
chain needs wired together:

  * tortoise/reconciliation.py::reconcile      — compares the two stores
  * tortoise/record_projection.py::replay      — journal -> Postgres
  * tortoise/cache_projection.py::read_record_view — Postgres -> cache input
  * FalkorProjection.rebuild_from_record       — the cache rebuild

The census is a COUNT of call sites, so a line of prose that mentions a call
is not one: `record_projection.replay(` inside `record_writer.py`'s docstring
matched the original line filter and inflated the count (#9189 review). Lines
that fall inside a string literal (docstrings included) are therefore skipped,
read from `tokenize`'s STRING/FSTRING spans rather than guessed from indentation.

A second precision fix, same review: `reconciliation.reconcile` used to match
ANY `from .reconciliation import …`, so an import of `reconcile_views` — a
different function, and what the #9189 trigger imports — counted as a caller of
`reconcile` (the pattern `reconciliation import reconcile` is a PREFIX of
`…import reconcile_views`). The pattern now requires the NAME `reconcile`, and
counts a CALL `reconcile(` as well, because the multi-line import form
(`from tortoise.reconciliation import (… reconcile, …)`) carries the name on a
line of its own. A definition — `def reconcile(` / `async def reconcile(` — is
not a call site.

A checkable mechanism with zero production callers cannot observe anything.
Populations are stated (which directories were searched).
"""
from __future__ import annotations

import io
import pathlib
import re
import tokenize

PROD_DIRS = ["tortoise", "tools", "scripts"]
TESTS = pathlib.Path("tests")
TARGETS = {
    "reconciliation.reconcile": re.compile(
        r"(?<![\w.])reconcile\("
        r"|from [\w.]*reconciliation import[^\n]*\breconcile\b(?!_)"),
    "record_projection.replay": re.compile(r"record_projection\.replay\(|\brp\.replay\(|from tortoise.record_projection import[^\n]*\breplay\b"),
    "cache_projection.read_record_view": re.compile(r"read_record_view\("),
    "FalkorProjection.rebuild_from_record": re.compile(r"\.rebuild_from_record\("),
}


def _prose_lines(text: str) -> set[int]:
    """Lines lying INSIDE a multi-line string literal — docstrings and prose.

    Only continuation lines are excluded, never a line that merely CONTAINS a
    string: `view = cp.read_record_view(conn, org_id="o")` is a call site whose
    line also carries string literals, and a line-granularity exclusion would
    silently drop it (measured: it took the test population's
    `read_record_view` count from 2 to 0).
    """
    covered: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.STRING and tok.end[0] > tok.start[0] + 1:
                covered.update(range(tok.start[0] + 1, tok.end[0] + 1))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # An unparseable file is not this census's subject; its lines simply
        # keep the conservative (over-counting) reading.
        return set()
    return covered


def census(roots, pattern):
    hits = []
    for root in roots:
        for f in pathlib.Path(root).rglob("*.py"):
            if "__pycache__" in str(f):
                continue
            text = f.read_text(errors="replace")
            prose = _prose_lines(text)
            for i, line in enumerate(text.splitlines(), 1):
                if pattern.search(line) and not line.strip().startswith("#") \
                        and not re.match(r"\s*(?:async\s+)?def\s", line) \
                        and i not in prose:
                    hits.append(f"{f}:{i}: {line.strip()[:100]}")
    return hits


print(f"production population: {PROD_DIRS}/**/*.py")
for name, pat in TARGETS.items():
    hits = census(PROD_DIRS, pat)
    print(f"\n== {name}: {len(hits)} production call site(s) ==")
    for h in hits:
        print(f"  {h}")

print(f"\ntest population: {TESTS}/*.py")
for name, pat in TARGETS.items():
    hits = census([str(TESTS)], pat)
    print(f"  {name}: {len(hits)} test-file hit line(s)")

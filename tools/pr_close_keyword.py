#!/usr/bin/env python3
"""A closing keyword GitHub cannot act on must fail BEFORE the merge (#8183).

THE DEFECT THIS EXISTS FOR
--------------------------
GitHub closes an issue from a pull request only when the closing keyword
(``Closes`` / ``Fixes`` / ``Resolves`` + ``#N``) is REAL MARKDOWN PROSE in the PR
description or a commit message. Inside an inline code span or a fenced code
block GitHub reads it as a quotation, not a directive, and does nothing.

That is not a theory. PR #2949 (merged into ``main`` as ``0643bba86`` on
2026-09-21) ended its body with::

    `Closes #2892`.

The keyword is wrapped in an inline code span, so GitHub never closed #2892 —
its event list carries 19 ``referenced`` events and no ``closed`` event until a
lane noticed a month later (2026-10-12), with the fix already in ``main``. The
cost is not the stale issue; it is the DUPLICATE LANE dispatched at #2892 to fix
an already-fixed defect.

Every in-repo reader of the keyword is a substring match, so all three read the
inert form as present and none of them can tell the difference:

* ``tools/merge_throughput.py`` (linked-issue parsing),
* ``.github/scripts/postmerge-dedup.js`` (``ISSUE_RE``, the #559 flag),
* ``.github/workflows/post-merge-validation.yml`` (via the module above).

A green keyword check is not evidence that the issue can close. This tool is the
check that is evidence: it strips exactly the markdown GitHub ignores and then
requires that, **if the body declares a closing keyword anywhere, at least one
instance survives the strip**.

WHAT IS STRIPPED, AND WHY ONLY THAT
-----------------------------------
Fenced code blocks (``` or ~~~ fences) and inline code spans — the two forms the
#2949 body and the re-measured corpus contain. Nothing else is stripped:

* blockquote (``> Closes #5``) and indented (4-space) lines are treated as LIVE.
  Both are individually plausible code/quotation forms, but neither is a form
  this repo has ever shipped and both have a live reading (a list continuation
  paragraph is an INDENTED paragraph, not a code block). Calling them inert
  would red a PR that would in fact close — a false red, which is worse than the
  false negative it buys.
* an UNCLOSED backtick run is literal text in CommonMark and stays LIVE. A
  stripper that treated every backtick as an opener would eat the rest of the
  body and red every PR that merely mentions a backtick.

CONVENTION (the cheap half of the fix)
--------------------------------------
Put the keyword on its OWN BARE LINE — ``Closes #8183``, no backticks, no fence.
`.github/pull_request_template.md` shows the bare line, and this tool fails CI
when a body wraps it.

WHAT "DECLARED" MEANS, AND WHY NEGATION STILL COUNTS
----------------------------------------------------
``declared`` is a plain keyword match, not an intent extraction — because
GitHub's is. Measured: PR #8907's body says ``**This does not close #8882.**``
and #8882 was auto-closed two seconds after that PR merged (2026-10-18
06:20:06Z -> 06:20:08Z). A disclaimer does not disarm the keyword, so a tool
that "understood" the negation would report a body as safe that GitHub closes.
The conservative reading is also the correct one: treating a negated mention as
live can only ever produce a GREEN, and the failure this guard exists for is a
directive the author meant but quoted.

USAGE
-----
    uv run python tools/pr_close_keyword.py --body-file /tmp/description.md
        exit 0 = no closing keyword declared, OR at least one survives the strip
        exit 1 = a closing keyword is declared but EVERY instance is inert
        exit 2 = could not check (unreadable file / bad args) — never a pass

    uv run python tools/pr_close_keyword.py --pr 2949 [--repo owner/name]
        Fetches the body via the REST API (``GITHUB_TOKEN`` / ``GH_TOKEN``).

    uv run python tools/pr_close_keyword.py --verify-closed --pr 2949
        Post-merge safety net: for every issue the body declares, check the
        issue is not still OPEN with no close from this merge. This is the only
        check that observes what GitHub ACTUALLY did, rather than what the body
        says — the issue's own lesson.
        exit 0 = every declared issue is closed (or was closed by this merge)
        exit 1 = at least one declared issue is still OPEN → this merge did not
                 close it
        exit 2 = could not measure (API failure) — never a pass

``--json`` emits the verdict as a single JSON object for an instrument.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# #8183: the keyword grammar is GitHub's, not ours — `close|closes|closed`,
# `fix|fixes|fixed`, `resolve|resolves|resolved`, optionally followed by a colon
# (docs: "The keywords can be followed by colons or in uppercase"), optionally
# qualified by `owner/repo#N` for a cross-repository issue. Parsing only the
# three spellings the old regexes used would silently miss `Fixed #N` and
# `CLOSES: #N`, both of which GitHub honours.
KEYWORD = r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)"
CLOSING_RE = re.compile(
    rf"\b{KEYWORD}\b\s*:?\s*(?:(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+))?#(?P<num>\d+)",
    re.IGNORECASE,
)

_FENCE_OPEN = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
_BACKTICK_RUN = re.compile(r"`+")
# A code span cannot cross a PARAGRAPH break (CommonMark), so pairing a stray
# opening backtick with a much later closing one is not what GitHub renders. The
# search for a closing run therefore stops at the first blank line.
_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n")


@dataclass(frozen=True)
class Verdict:
    """What a PR body declares, and what survives GitHub's markdown rules."""

    declared: tuple[int, ...]
    live: tuple[int, ...]

    @property
    def inert(self) -> tuple[int, ...]:
        """Declared issue numbers with NO surviving instance — the silent case."""
        return tuple(n for n in self.declared if n not in self.live)

    @property
    def ok(self) -> bool:
        """A body with no keyword is fine; a body with one needs a LIVE one."""
        return bool(self.live) or not self.declared


def strip_fenced_blocks(body: str) -> str:
    """Drop fenced code blocks (```` ``` ```` and ``~~~``), keeping the rest.

    CommonMark closing rule: the closing fence is the same character, at least
    as long as the opening one, with nothing but whitespace after it. An
    UNCLOSED fence runs to the end of the document — that is correct markdown,
    and it is also the live failure (a stray opening fence swallows every later
    line, including the keyword).
    """
    out: list[str] = []
    fence_char: str | None = None
    fence_len = 0
    for line in body.splitlines():
        if fence_char is None:
            m = _FENCE_OPEN.match(line)
            # A backtick fence's info string may not contain a backtick
            # (CommonMark), else `` ```Closes #5``` `` on one line is a span.
            if m and not (m.group("fence")[0] == "`" and "`" in m.group("info")):
                fence_char = m.group("fence")[0]
                fence_len = len(m.group("fence"))
                continue
            out.append(line)
            continue
        stripped = line.strip()
        if stripped and set(stripped) == {fence_char} and len(stripped) >= fence_len:
            fence_char = None
            fence_len = 0
        # every line inside the fence is dropped, closing fence included
    return "\n".join(out)


def strip_inline_code(text: str) -> str:
    """Remove inline code spans; leave an UNCLOSED backtick run as literal text.

    A span opens with a run of N backticks and closes with the next run of
    EXACTLY N (CommonMark) — a shorter or longer run inside is content, which is
    what makes ``` ``a ` b`` ``` one span. No closing run ⇒ literal backticks,
    so the text after it is still scanned as prose.
    """
    out: list[str] = []
    pos = 0
    while True:
        opening = _BACKTICK_RUN.search(text, pos)
        if opening is None:
            out.append(text[pos:])
            break
        run_len = opening.end() - opening.start()
        closing = _find_run(text, opening.end(), run_len)
        if closing is None:
            out.append(text[pos : opening.end()])
            pos = opening.end()
            continue
        out.append(text[pos : opening.start()])
        pos = closing
    # Collapse what the spans left behind: `Closes #2892`. -> `.`, and a span
    # used as a whole line leaves an empty line. Neither can re-form a keyword.
    return "".join(out)


def _find_run(text: str, start: int, run_len: int) -> int | None:
    """Index just past the next run of EXACTLY ``run_len`` backticks, or None.

    The search is bounded by the enclosing PARAGRAPH: a code span never crosses a
    blank line, so a stray backtick must not swallow prose a paragraph later.
    """
    boundary = _PARAGRAPH_BREAK.search(text, start)
    limit = boundary.start() if boundary else len(text)
    pos = start
    while True:
        m = _BACKTICK_RUN.search(text, pos)
        if m is None or m.end() > limit:
            return None
        if (m.end() - m.start()) == run_len:
            return m.end()
        pos = m.end()


def strip_markdown_code(body: str) -> str:
    """The text GitHub reads for closing keywords: fences then inline spans."""
    return strip_inline_code(strip_fenced_blocks(body))


def _numbers(text: str) -> list[int]:
    return [int(m.group("num")) for m in CLOSING_RE.finditer(text)]


def check_body(body: str | None) -> Verdict:
    """Declared vs live closing keywords for one PR body."""
    declared = _numbers(body or "")
    live = _numbers(strip_markdown_code(body or ""))
    # de-dup, order-preserving
    return Verdict(tuple(dict.fromkeys(declared)), tuple(dict.fromkeys(live)))


def format_report(verdict: Verdict) -> str:
    """Operator-facing explanation — readable WITHOUT knowing markdown's rules."""
    if not verdict.declared:
        return "no closing keyword declared — nothing to close (Refs-style body): OK"
    if verdict.ok:
        return (
            "closing keyword is live: "
            + ", ".join(f"#{n}" for n in verdict.live)
            + " — GitHub will close it on merge"
        )
    return (
        "INERT closing keyword: "
        + ", ".join(f"#{n}" for n in verdict.inert)
        + " — declared only inside a code span or fenced block, so GitHub will NOT "
        "close it on merge and the issue stays OPEN after your fix lands.\n"
        "Fix: put the keyword on its own bare line, e.g.\n\n    Closes #" + str(verdict.inert[0])
    )


# --- post-merge safety net -------------------------------------------------------------------


@dataclass(frozen=True)
class StillOpen:
    """A declared issue that this merge did NOT close."""

    number: int
    state: str


@dataclass(frozen=True)
class ClosedCheck:
    """Result of the post-merge check. ``unavailable`` is NEVER a pass."""

    still_open: tuple[StillOpen, ...] = ()
    unavailable: str = ""

    @property
    def ok(self) -> bool:
        return not self.unavailable and not self.still_open


def _events_closed_by(events: list[dict], merge_sha: str) -> bool:
    return any(e.get("event") == "closed" and e.get("commit_id") == merge_sha for e in events)


def _recently_closed(events: list[dict], merged_at: str, window_s: int = 3600) -> bool:
    """A `closed` event near the merge — another PR may legitimately have closed it."""
    for e in events:
        if e.get("event") != "closed":
            continue
        if abs(_age_s(e.get("created_at", ""), merged_at)) <= window_s:
            return True
    return False


def _parse_ts(value: str) -> float | None:
    from datetime import datetime

    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _age_s(created_at: str, reference: str) -> float:
    a, b = _parse_ts(created_at), _parse_ts(reference)
    if a is None or b is None:
        # Unparseable timestamps fall to "not recent" — the conservative side
        # for the FLAG only because `_events_closed_by` still runs first.
        return float("inf")
    return a - b


def verify_closed(pr_number: int, fetch: Callable[[str], object], repo: str) -> ClosedCheck:
    """Check every issue the merged PR declared is actually closed.

    ``fetch(path)`` returns decoded JSON for a REST path (``/repos/o/r/...``);
    injecting it keeps this function hermetic for tests. Any fetch failure is
    reported as ``unavailable`` — an unmeasured state is never a pass.
    """
    try:
        pr = fetch(f"/repos/{repo}/pulls/{pr_number}")
    except Exception as exc:  # every transport error is "unavailable", never a pass
        return ClosedCheck(unavailable=f"could not read PR #{pr_number}: {exc}")
    if not isinstance(pr, dict):
        return ClosedCheck(unavailable=f"PR #{pr_number} payload is not an object")
    merge_sha = str(pr.get("merge_commit_sha") or "")
    merged_at = str(pr.get("merged_at") or "")
    if not merge_sha or not merged_at:
        # An unmerged PR has nothing to verify — and saying "fine" would make
        # the check vacuous on the only tree that matters.
        return ClosedCheck(unavailable=f"PR #{pr_number} is not merged (or has no merge SHA)")

    declared = check_body(pr.get("body") or "").declared
    still_open: list[StillOpen] = []
    for number in declared:
        try:
            issue = fetch(f"/repos/{repo}/issues/{number}")
            events = fetch(f"/repos/{repo}/issues/{number}/events?per_page=100")
        except Exception as exc:
            return ClosedCheck(unavailable=f"could not read issue #{number}: {exc}")
        if not isinstance(issue, dict) or not isinstance(events, list):
            return ClosedCheck(unavailable=f"issue #{number} payload has the wrong shape")
        state = str(issue.get("state", "")).lower()
        if state != "open":
            continue
        if _events_closed_by(events, merge_sha) or _recently_closed(events, merged_at):
            continue
        still_open.append(StillOpen(number, state))
    return ClosedCheck(still_open=tuple(still_open))


def _http_fetch(repo: str, token: str):
    """Build the REST reader used by the CLI. Stdlib only — no `gh` dependency."""

    def fetch(path: str):
        req = urllib.request.Request(
            "https://api.github.com" + path,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "pr-close-keyword",
            },
        )
        # The URL is built from a FIXED host plus a repo-relative path, never
        # from user input, so there is no scheme-injection surface here.
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    return fetch


def _token() -> str:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Reject a closing keyword GitHub cannot act on (#8183)."
    )
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--body-file", type=Path, help="read the PR body from this file")
    src.add_argument("--pr", type=int, help="read the PR body from the REST API")
    ap.add_argument("--repo", default="daniel-ospina/tortoise", help="owner/name (with --pr)")
    ap.add_argument(
        "--verify-closed",
        action="store_true",
        help="post-merge net: every issue the PR declared must actually be closed",
    )
    ap.add_argument("--json", action="store_true", help="emit one JSON object")
    args = ap.parse_args(argv)

    if args.verify_closed:
        if args.pr is None:
            print("--verify-closed needs --pr N", file=sys.stderr)
            return 2
        token = _token()
        if not token:
            print("no GITHUB_TOKEN/GH_TOKEN — cannot measure; not a pass", file=sys.stderr)
            return 2
        check = verify_closed(args.pr, _http_fetch(args.repo, token), args.repo)
        if args.json:
            print(
                json.dumps(
                    {
                        "mode": "verify-closed",
                        "ok": check.ok,
                        "unavailable": check.unavailable,
                        "still_open": [s.number for s in check.still_open],
                    }
                )
            )
        elif check.unavailable:
            print(f"UNMEASURABLE: {check.unavailable} — not a pass", file=sys.stderr)
        elif check.still_open:
            print(
                "STILL OPEN after this merge: "
                + ", ".join(f"#{s.number}" for s in check.still_open)
                + " — the closing keyword did not close it (inert keyword, or a "
                "keyword in the wrong place). Close it by hand, or re-state it on a "
                "bare line in a follow-up PR."
            )
        else:
            print("every declared issue is closed — the closing keyword landed")
        if check.unavailable:
            return 2
        return 0 if check.ok else 1

    if args.body_file is not None:
        try:
            body = args.body_file.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"cannot read {args.body_file}: {exc}", file=sys.stderr)
            return 2
    else:
        token = _token()
        if not token:
            print("no GITHUB_TOKEN/GH_TOKEN — cannot fetch the PR; not a pass", file=sys.stderr)
            return 2
        try:
            pr = _http_fetch(args.repo, token)(f"/repos/{args.repo}/pulls/{args.pr}")
            body = str(pr.get("body") or "")
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            print(f"cannot fetch PR #{args.pr}: {exc}", file=sys.stderr)
            return 2

    verdict = check_body(body)
    if args.json:
        print(
            json.dumps(
                {
                    "mode": "body",
                    "ok": verdict.ok,
                    "declared": list(verdict.declared),
                    "live": list(verdict.live),
                    "inert": list(verdict.inert),
                }
            )
        )
    else:
        print(format_report(verdict))
    return 0 if verdict.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

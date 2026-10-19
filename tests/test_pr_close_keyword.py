"""An inert closing keyword must be impossible to ship silently (#8183).

WHAT BROKE
----------
PR #2949 merged into ``main`` with a body ending in `` `Closes #2892`. `` — the
keyword inside an INLINE CODE SPAN. GitHub does not process closing keywords
inside code spans or fenced blocks, so it never closed #2892: the issue's event
list carries no ``closed`` event until a month later, while the fix was already
in ``main``. A lane was then dispatched at the fixed issue (2026-10-11) and its
verdict was ALREADY_FIXED.

Every in-repo reader of the keyword was a substring regex, so all of them read
the inert form as present. Nothing verified the one thing that mattered —
whether GitHub can ACT on it. ``tools/pr_close_keyword.py`` is that check, and
this module is its contract:

* the confirmed #2949 body is REJECTED (the issue's own fixture requirement);
* the re-measured corpus body (#8669) is REJECTED too — GitHub closed #4161
  only because a COMMIT MESSAGE carried a bare keyword, so that body's keyword
  was inert exactly as #2949's was and nothing in-repo could tell;
* every live spelling GitHub honours (``Closes``/``Fixes``/``Resolved``,
  colon, uppercase, cross-repo) is ACCEPTED — a guard that reds a body that
  would have closed is worse than the blind spot it replaces;
* an UNCLOSED backtick is literal text and stays LIVE (a stripper that treats
  every backtick as an opener eats the rest of the body and reds everything);
* a body with NO closing keyword (``Refs #N``) is not this check's business;
* the post-merge net reports "could not measure" as its own outcome — never a
  pass — and does not flag an issue that was closed by this merge or reopened
  afterwards.

* `test_the_guard_grades_this_pulls_requests_own_body` is the in-matrix GATE: on a
  GitHub PR event it grades THIS PR's own body, so the verdict lands inside
  `python-ci-gate` (required) while the dedicated workflow cannot be pushed
  (#9141 — the lane PAT has no `workflow` scope). Its limit is stated there.

Hermetic: no DB, no network, no falkordb. The REST reader is injected; the CLI
is exercised through ``--body-file``. The one test that shells out to ``node``
(agreement with ``postmerge-dedup.js``'s ISSUE_RE) skips when node is absent,
the same way ``tests/test_postmerge_dedup.py`` does.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = REPO_ROOT / "tools" / "pr_close_keyword.py"
sys.path.insert(0, str(REPO_ROOT))

from tools.pr_close_keyword import (  # noqa: E402 — after the sys.path insert
    ClosedCheck,
    check_body,
    format_report,
    strip_inline_code,
    strip_markdown_code,
    verify_closed,
)

# --- the confirmed instance: PR #2949's body tail, verbatim ------------------
# The exact bytes that shipped the inert close. PR #2949 merged as 0643bba86 on
# 2026-09-21; #2892 stayed OPEN until 2026-10-12.
BODY_2949_TAIL = "`Closes #2892`.\n\n🤖 Generated with [Pi](https://pi.dev)\n"

# The re-measured corpus instance (PR #8669, merged 2026-10-15). Its body's only
# keyword is a code span; GitHub closed #4161 because two COMMIT MESSAGES in the
# PR carried a bare `Closes #4161`. So the body keyword was inert and nothing
# in-repo could have said so.
BODY_8669_TAIL = "`Closes #4161`\n\nreview recorded: reviews/8669.json verdict=clean\n"


def cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOL), *args], capture_output=True, text=True, timeout=60
    )


# --- the confirmed inert bodies ---------------------------------------------


def test_confirmed_2949_body_is_rejected():
    """The issue's own fixture: PR #2949's body must FAIL the check."""
    v = check_body(BODY_2949_TAIL)
    assert v.declared == (2892,), "the keyword must still be READ — that is why nothing caught it"
    assert v.live == (), "a code-span keyword is inert to GitHub; it must not survive the strip"
    assert v.inert == (2892,)
    assert not v.ok


def test_confirmed_8669_body_is_rejected():
    """Re-measured from the corpus: an inert body keyword, rescued only by a commit message."""
    v = check_body(BODY_8669_TAIL)
    assert v.declared == (4161,)
    assert v.inert == (4161,)
    assert not v.ok, (
        "a commit-message keyword must not excuse an inert BODY keyword: the body is "
        "the artifact this gate grades, and #8669 shows the two can disagree"
    )


def test_a_whole_paragraph_of_prose_around_the_span_still_rejects():
    """Prose does not make a code span live — the #2949 shape with more context."""
    body = (
        "## Scope note\n\nThis PR closes the product-persistence half only.\n\n"
        "The rest is separate work.\n\n`Closes #2892`.\n"
    )
    assert not check_body(body).ok


# --- live forms GitHub honours (no false reds) -------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "Closes #8183",
        "closes #8183",
        "CLOSES #8183",
        "Closes: #8183",
        "CLOSES: #8183",
        "Closed #8183",
        "Fix #8183",
        "Fixes #8183",
        "Fixed #8183",
        "Resolve #8183",
        "Resolves #8183",
        "Resolved #8183",
        "Fixes octo-org/octo-repo#8183",
        "This PR closes #8183 because the seam was forked.",
    ],
)
def test_every_live_spelling_is_accepted(line: str):
    v = check_body(f"## Summary\n\n{line}\n")
    assert v.ok, f"{line!r} is a live closing keyword — GitHub honours it"
    assert 8183 in v.live


def test_no_keyword_at_all_is_not_this_checks_business():
    """`Refs #N` is the deliberate not-a-close form; it must never red."""
    v = check_body("Refs #2813\n\nSee also #2892 for context.\n")
    assert v.declared == () and v.live == () and v.ok


def test_body_with_both_an_inert_and_a_live_keyword_passes():
    """The rule is 'at least one survives' — an inert example may stay quoted."""
    v = check_body("The old body said `Closes #5`; this one says\n\nCloses #6\n")
    assert v.declared == (5, 6)
    assert v.live == (6,)
    assert v.ok, "a live instance exists, so the PR can close — the inert one is harmless"


# --- what markdown actually makes inert --------------------------------------


def test_fenced_block_is_inert():
    for fence in ("```", "~~~", "````"):
        body = f"## Summary\n\n{fence}\nCloses #5\n{fence}\n"
        assert not check_body(body).ok, f"{fence} fence must make the keyword inert"


def test_fenced_block_with_language_and_indent_is_inert():
    assert not check_body("  ```sh\n  Closes #5\n  ```\n").ok


def test_unclosed_fence_swallows_the_rest_of_the_body():
    """CommonMark: an unclosed fence runs to EOF — so a later bare keyword is inert."""
    assert not check_body("```\nsome log\n\nCloses #5\n").ok


def test_inline_span_and_multi_backtick_span_are_inert():
    assert not check_body("`Closes #5`\n").ok
    assert not check_body("``Closes #5``\n").ok
    # CommonMark: a shorter run inside a longer span is CONTENT, not a close.
    assert not check_body("``has a ` tick and Closes #5``\n").ok


def test_unclosed_backtick_is_literal_text_not_a_span():
    """A backtick that never closes is prose — treating it as an opener reds everything."""
    assert check_body("The ` separator is documented; Closes #5\n").ok


def test_an_inline_span_does_not_cross_a_paragraph_break():
    """A stray backtick must not swallow prose in a LATER paragraph."""
    body = "A stray ` backtick here.\n\nCloses #5\n"
    assert check_body(body).ok, "a code span never crosses a blank line"


def test_a_backtick_span_quoting_the_keyword_alone_on_one_line_is_the_2949_shape():
    """The #2949 tail reduces to a stray period — nothing GitHub can read."""
    assert strip_markdown_code("`Closes #2892`.").strip() == "."
    assert strip_markdown_code("`Closes #2892`").strip() == ""
    assert strip_inline_code("a `b` c") == "a  c"


# --- the CLI contract (exit codes + JSON) ------------------------------------


def test_cli_exit_codes_and_json(tmp_path: Path):
    inert = tmp_path / "inert.md"
    inert.write_text(BODY_2949_TAIL, encoding="utf-8")
    live = tmp_path / "live.md"
    live.write_text("Closes #8183\n", encoding="utf-8")
    none = tmp_path / "none.md"
    none.write_text("Refs #2813\n", encoding="utf-8")

    p = cli("--body-file", str(inert))
    assert p.returncode == 1, p.stderr
    assert "INERT" in p.stdout and "#2892" in p.stdout
    assert "bare line" in p.stdout, "the message must say HOW to fix it"

    assert cli("--body-file", str(live)).returncode == 0
    assert cli("--body-file", str(none)).returncode == 0

    data = json.loads(cli("--body-file", str(inert), "--json").stdout)
    assert data == {"mode": "body", "ok": False, "declared": [2892], "live": [], "inert": [2892]}


def test_cli_unreadable_body_file_is_not_a_pass(tmp_path: Path):
    p = cli("--body-file", str(tmp_path / "missing.md"))
    assert p.returncode == 2, "an unread body is UNMEASURABLE — never exit 0"


def test_cli_requires_a_source():
    assert cli().returncode == 2


# --- the post-merge net ------------------------------------------------------


def _fetcher(payloads: dict[str, object]):
    def fetch(path: str):
        key = next((k for k in payloads if path.startswith(k)), None)
        if key is None:
            raise RuntimeError(f"unexpected path {path}")
        value = payloads[key]
        if isinstance(value, Exception):
            raise value
        return value

    return fetch


PR_MERGED = {
    "number": 2949,
    "merged_at": "2026-09-21T06:24:20Z",
    "merge_commit_sha": "0643bba8674e153340a3187b4adf02d33252c819",
    "body": BODY_2949_TAIL,
}


def test_verify_closed_flags_an_issue_the_merge_did_not_close():
    check = verify_closed(
        2949,
        _fetcher(
            {
                "/repos/o/r/pulls/2949": PR_MERGED,
                "/repos/o/r/issues/2892/events": [
                    {
                        "event": "referenced",
                        "commit_id": "0643bba8674e153340a3187b4adf02d33252c819",
                        "created_at": "2026-09-21T06:24:22Z",
                    },
                ],
                "/repos/o/r/issues/2892": {"state": "open", "number": 2892},
            }
        ),
        "o/r",
    )
    assert not check.ok
    assert [s.number for s in check.still_open] == [2892]


def test_verify_closed_accepts_a_merge_that_closed_its_issue():
    check = verify_closed(
        2949,
        _fetcher(
            {
                "/repos/o/r/pulls/2949": PR_MERGED,
                "/repos/o/r/issues/2892/events": [
                    {
                        "event": "closed",
                        "commit_id": PR_MERGED["merge_commit_sha"],
                        "created_at": "2026-09-21T06:24:22Z",
                    },
                ],
                "/repos/o/r/issues/2892": {"state": "closed", "number": 2892},
            }
        ),
        "o/r",
    )
    assert check.ok and check.still_open == ()


def test_verify_closed_does_not_flag_an_issue_reopened_after_being_closed():
    """An issue reopened ON PURPOSE after the merge is not an inert-keyword escape."""
    check = verify_closed(
        2949,
        _fetcher(
            {
                "/repos/o/r/pulls/2949": PR_MERGED,
                "/repos/o/r/issues/2892/events": [
                    {
                        "event": "closed",
                        "commit_id": "other-sha",
                        "created_at": "2026-09-21T06:24:30Z",
                    },
                    {"event": "reopened", "commit_id": None, "created_at": "2026-09-22T00:00:00Z"},
                ],
                "/repos/o/r/issues/2892": {"state": "open", "number": 2892},
            }
        ),
        "o/r",
    )
    assert check.ok, "closed within the merge window, then reopened — not this failure"


def test_verify_closed_is_unavailable_rather_than_green_on_an_api_error():
    check = verify_closed(2949, _fetcher({"/repos/o/r/pulls/2949": RuntimeError("boom")}), "o/r")
    assert isinstance(check, ClosedCheck)
    assert check.unavailable and not check.ok


def test_verify_closed_is_unavailable_for_an_unmerged_pr():
    unmerged = dict(PR_MERGED, merged_at=None, merge_commit_sha=None)
    check = verify_closed(2949, _fetcher({"/repos/o/r/pulls/2949": unmerged}), "o/r")
    assert check.unavailable and not check.ok


def test_verify_closed_cli_without_a_token_is_unmeasurable(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    p = cli("--verify-closed", "--pr", "2949")
    assert p.returncode == 2, "no credential = UNMEASURABLE, never exit 0"


# --- cross-parser agreement + the wiring pin --------------------------------

node = shutil.which("node")


@pytest.mark.skipif(node is None, reason="node not on PATH")
def test_the_existing_keyword_parser_agrees_with_this_one_on_a_bare_keyword(tmp_path: Path):
    """The #559 flag's regex and this tool must not disagree about what is LIVE.

    They answer different questions (that one: "which issue do I flag?"; this
    one: "can GitHub act on it?"), but a body the older parser sees and this one
    calls inert is exactly the silent case — so on the forms the older regex
    supports, the two must agree. Divergence here means one of them drifted.

    SCOPE: only the three bare spellings `ISSUE_RE` covers. The legacy regex is
    NARROWER than GitHub's grammar — it misses `Closes: #N` (colon), `Closed` /
    `Resolved`, and the qualified `Closes owner/repo#N` — measured on the last
    298 merged PRs: 15 of the 187 bodies this tool reads as LIVE are invisible to
    it. That gap is filed separately (#9137) and is NOT this tool's regression:
    pinning it here would forbid fixing it.

    The script is copied to a `.cjs` in a temp dir: node's module type is decided
    by the NEAREST `package.json`, and a `"type": "module"` one anywhere above
    the checkout makes `require` a ReferenceError in a `.js` file — an ambient
    property of the machine, not of the script under test.
    """
    script = tmp_path / "postmerge-dedup.cjs"
    script.write_text(
        (REPO_ROOT / ".github" / "scripts" / "postmerge-dedup.js").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    for body in ("Closes #8183\n", "Fixes #7\n", "Resolves #12\n"):
        proc = subprocess.run(
            [node, str(script), "parse-issue", body],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        parsed = json.loads(proc.stdout)["issue_number"]
        live = check_body(body).live
        assert parsed == live[0], f"{body!r}: parser said {parsed}, tool said {live}"


def test_the_guard_grades_this_pulls_requests_own_body():
    """The in-matrix gate: on a PR run, THIS PR's body must be gradeable and pass.

    WHY THIS LIVES IN A TEST AND NOT (YET) IN ITS OWN WORKFLOW. The intended home
    is `.github/workflows/pr-close-keyword.yml`, with
    `types: [opened, edited, synchronize, reopened]` so that a body-edit fix
    re-triggers its own run. That file cannot be pushed: the lane credential is a
    fine-grained PAT without the **Workflows** permission, and GitHub refuses the
    WHOLE push ("refusing to allow a Personal Access Token to create or update a
    workflow … without `workflow` scope") — see #9141, where the finished
    workflow is staged on a local branch. This test is the STOPGAP that puts the
    same verdict inside a check the merge already waits for: `python-ci`'s `test`
    shards run this file whenever `core` is selected, and `python-ci-gate` is the
    required aggregate.

    ITS LIMIT, stated so nobody reads more into it than it earns: a body EDIT does
    not re-run PR CI, so the remedy after a red is "fix the body, then push any
    commit (or re-run the job)". The workflow removes that wrinkle.

    Non-vacuous by construction: the payload is parsed, `body` must be PRESENT (a
    shape change fails closed rather than skipping), and the same `check_body` is
    what the 37 other tests in this file exercise.
    """
    raw_path = os.environ.get("GITHUB_EVENT_PATH")
    if not raw_path or not Path(raw_path).is_file():
        pytest.skip("no GitHub event payload — local run, push run, or non-Actions CI")
    payload = json.loads(Path(raw_path).read_text(encoding="utf-8"))
    pr = payload.get("pull_request")
    if not pr:
        pytest.skip(f"not a pull_request event ({payload.get('event_name', '?')})")
    assert "body" in pr, (
        "the event payload's `pull_request` object carries no `body` key — the shape "
        "changed, so this gate would silently never examine anything"
    )
    verdict = check_body(pr.get("body"))
    assert verdict.ok, (
        f"{format_report(verdict)}\n\n"
        "(If you just fixed the body: PR CI does not re-run on a body edit — push a "
        "commit or re-run this job.)"
    )


def test_the_guard_is_registered_in_a_surface_that_runs_on_pull_requests():
    """Non-vacuity for the stopgap above: an unregistered file never runs at all.

    `core` is the always-on smoke surface, so this file is selected for every
    non-docs diff; an entry in no surface would make the gate latent, which is the
    exact failure this module exists to detect one level up.
    """
    manifest = yaml.safe_load((REPO_ROOT / "config" / "ci-surfaces.yml").read_text())
    members = manifest["surfaces"]["core"]
    assert "test_pr_close_keyword.py" in members, (
        "tests/test_pr_close_keyword.py is not registered in the `core` surface — the "
        "PR-body gate would never be selected, and #8183 returns to nothing-verifies-it"
    )

"""Tests for .github/scripts/check-migration-append-only (#1095).

Builds fixture git repos and drives the SHARED script via subprocess so the
test exercises the shipped logic (not a Python copy). Two modes:
- prefix: duplicate prefixes + non-conforming filenames
- diff: append-only (M/R/D rejected, A allowed) against a base SHA

⚠ Not ALL of this file is hermetic, and the exceptions say so in their own
docstrings rather than leaving the reader to discover it: the arms that guard the
header's `CHOOSING A NEW PREFIX` paragraph and the `<version>_<leaf>` mentions in
this repo's prefix guidance read THIS checkout. In a sparse checkout without
`supabase/migrations/` they red — read that as the missing tree it is, not as a
bad guard header. Everything else builds its own fixture repo in `$TMPDIR`.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / ".github" / "scripts" / "check-migration-append-only"
_FIXTURE_TMP = None


def _tmp() -> Path:
    global _FIXTURE_TMP
    if _FIXTURE_TMP is None:
        _FIXTURE_TMP = Path(tempfile.mkdtemp(prefix="migration-append-only-"))
    return _FIXTURE_TMP


FIXTURES = _tmp()

MIG = 'supabase/migrations'


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _make_repo(base_migrations: list[str]) -> Path:
    """Create a fixture repo with an initial commit containing base migrations."""
    d = FIXTURES / "repo"
    if d.exists():
        subprocess.run(["rm", "-rf", str(d)], check=True)
    d.mkdir(parents=True)
    _git(d, "init", "-q", "-b", "main")
    _git(d, "config", "user.email", "t@t")
    _git(d, "config", "user.name", "t")
    (d / MIG).mkdir(parents=True)
    for f in base_migrations:
        (d / MIG / f).write_text("-- base\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "base")
    return d


def _run_script(mode: str, repo: Path, base_sha: str | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["DRIFT_REPO"] = str(repo)
    if base_sha:
        env["DRIFT_BASE_SHA"] = base_sha
    return subprocess.run(
        ["bash", str(SCRIPT), mode],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
    )


def test_prefix_duplicates_rejected():
    d = _make_repo(["0012_a.sql", "0012_b.sql"])
    r = _run_script("prefix", d)
    assert r.returncode == 1, r.stdout
    assert "0012" in r.stdout


def test_prefix_unique_ok():
    d = _make_repo(["0001_a.sql", "20260813000001_b.sql"])
    r = _run_script("prefix", d)
    assert r.returncode == 0, r.stderr


def test_prefix_non_conforming_rejected():
    d = _make_repo(["0001_a.sql", "scratch.sql"])
    r = _run_script("prefix", d)
    assert r.returncode == 1, r.stdout
    assert "scratch.sql" in r.stdout


def test_diff_modified_base_migration_rejected():
    d = _make_repo(["0001_a.sql"])
    base = _git_sha(d)
    (d / MIG / "0001_a.sql").write_text("-- changed\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "edit")
    r = _run_script("diff", d, base)
    assert r.returncode == 1, r.stdout
    assert "0001_a.sql" in r.stdout


def test_diff_deleted_base_migration_rejected():
    d = _make_repo(["0001_a.sql"])
    base = _git_sha(d)
    (d / MIG / "0001_a.sql").unlink()
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "delete")
    r = _run_script("diff", d, base)
    assert r.returncode == 1, r.stdout
    assert "0001_a.sql" in r.stdout


def test_diff_renamed_base_migration_rejected():
    d = _make_repo(["0001_a.sql"])
    base = _git_sha(d)
    (d / MIG / "0001_a.sql").rename(d / MIG / "20260813000099_a.sql")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "rename")
    r = _run_script("diff", d, base)
    assert r.returncode == 1, r.stdout


def test_diff_added_migration_allowed():
    d = _make_repo(["0001_a.sql"])
    base = _git_sha(d)
    (d / MIG / "20260813000099_new.sql").write_text("-- new\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "add")
    r = _run_script("diff", d, base)
    assert r.returncode == 0, r.stderr


def test_diff_same_pr_added_then_edited_allowed():
    # A file ADDED in this PR (not in base) may be edited pre-merge.
    d = _make_repo(["0001_a.sql"])
    base = _git_sha(d)
    (d / MIG / "20260813000099_new.sql").write_text("-- new v1\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "add v1")
    (d / MIG / "20260813000099_new.sql").write_text("-- new v2 (fix)\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "fix own new migration")
    r = _run_script("diff", d, base)
    assert r.returncode == 0, r.stderr


def test_diff_missing_base_sha_exit_2():
    d = _make_repo(["0001_a.sql"])
    r = _run_script("diff", d)
    assert r.returncode == 2, r.stdout


def _git_sha(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


# ── #1235: pure-prefix rename of a PROVABLY UNAPPLIED migration is allowed ──
# (token present → Management API stub says old version NOT applied)
# #2240 widens it: the prod state is the authority, so the rename MAY also edit
# content — which is what the drift gate's "re-land as a FORWARD migration"
# remedy does when it corrects the file's self-referencing header.


def _stub_curl(versions: list[str], http_code: int = 201) -> Path:
    """Write a stub curl returning the fixture remote version list."""
    stub = FIXTURES / "stub-curl-append.sh"
    rows = "".join(f'{{"version":"{v}"}},' for v in versions).rstrip(",")
    body = f"[{rows}]"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'printf \'%s\\n{http_code}\\n\' \'{body}\'\n'
    )
    stub.chmod(0o755)
    return stub


def _run_script_with_token(mode: str, repo: Path, base_sha: str,
                           versions: list[str]) -> subprocess.CompletedProcess:
    stub = _stub_curl(versions)
    env = dict(os.environ)
    env["DRIFT_REPO"] = str(repo)
    env["DRIFT_BASE_SHA"] = base_sha
    env["DRIFT_CURL"] = str(stub)
    env["DRIFT_API_URL"] = "https://api.supabase.invalid"
    env["DRIFT_TOKEN"] = "test-token"
    return subprocess.run(
        ["bash", str(SCRIPT), mode],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
    )


def test_diff_unapplied_rename_allowed_with_token():
    # Old version 20260813000099 is NOT in the remote applied set → the pure
    # prefix rename is the #1235 exception and must be ALLOWED.
    d = _make_repo(["20260813000099_a.sql"])
    base = _git_sha(d)
    (d / MIG / "20260813000099_a.sql").rename(d / MIG / "20260813100100_a.sql")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "rename unapplied")
    r = _run_script_with_token("diff", d, base, versions=["0001"])
    assert r.returncode == 0, r.stdout
    assert "exception" in r.stdout.lower(), r.stdout


def test_diff_unapplied_renumber_with_edited_content_allowed():
    # #2240: the drift gate's remedy is "re-land its DDL as a FORWARD migration",
    # which corrects the file's own self-referencing header — i.e. a rename that
    # ALSO edits content. A FORWARD prefix rename of an unapplied version has no
    # schema_migrations row and sorts after REMOTE_MAX, so it cannot diverge prod
    # and must not be blocked. A realistic migration body keeps the similarity
    # high enough for git to report R<sim> (as the live R092 case does).
    body = (
        "-- Migration 20260813000099: metering capture tokens (#2240)\n"
        + "\n".join(
            f"-- explanatory line {i} about the CAPTURE lane workload"
            for i in range(12)
        )
        + "\nCREATE TABLE IF NOT EXISTS t (id int);\n"
    )
    d = _make_repo(["20260813000099_a.sql"])
    (d / MIG / "20260813000099_a.sql").write_text(body)
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "realistic base")
    base = _git_sha(d)
    (d / MIG / "20260813000099_a.sql").rename(d / MIG / "20260813100100_a.sql")
    (d / MIG / "20260813100100_a.sql").write_text(
        body.replace("20260813000099", "20260813100100")
        + "-- re-landed forward (#2240)\n"
    )
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "renumber unapplied, header corrected")
    r = _run_script_with_token("diff", d, base, versions=["0001"])
    assert r.returncode == 0, r.stdout
    assert "exception" in r.stdout.lower(), r.stdout


def test_diff_applied_renumber_with_edited_content_still_blocked():
    # The safety property the byte-identity gate was reaching for is kept by the
    # PROD STATE test instead: if the old version IS applied, no rename —
    # identical or edited — may pass. Both directions are asserted.
    d = _make_repo(["20260813000099_a.sql"])
    base = _git_sha(d)
    (d / MIG / "20260813000099_a.sql").rename(d / MIG / "20260813100100_a.sql")
    body = (d / MIG / "20260813100100_a.sql").read_text()
    (d / MIG / "20260813100100_a.sql").write_text(body + "-- edited\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "renumber applied, edited")
    r = _run_script_with_token("diff", d, base, versions=["20260813000099"])
    assert r.returncode == 1, r.stdout
    assert "append-only" in r.stdout.lower(), r.stdout


def test_diff_applied_rename_still_blocked_with_token():
    # Old version IS applied in prod (stub lists it) → rename must stay blocked.
    d = _make_repo(["20260813000099_a.sql"])
    base = _git_sha(d)
    (d / MIG / "20260813000099_a.sql").rename(d / MIG / "20260813100100_a.sql")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "rename applied")
    r = _run_script_with_token("diff", d, base, versions=["20260813000099"])
    assert r.returncode == 1, r.stdout
    assert "20260813000099_a.sql" in r.stdout or "20260813100100_a.sql" in r.stdout


def test_diff_unapplied_rename_blocked_without_token():
    # No token → cannot verify remote → fail-closed strict block (fork-safe).
    d = _make_repo(["20260813000099_a.sql"])
    base = _git_sha(d)
    (d / MIG / "20260813000099_a.sql").rename(d / MIG / "20260813100100_a.sql")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "rename unapplied, no token")
    r = _run_script("diff", d, base)
    assert r.returncode == 1, r.stdout
    assert "append-only" in r.stdout.lower(), r.stdout


def test_diff_applied_content_edit_never_exempt_with_token():
    # #1001 protection, PRESERVED under the #2240 rule: an edit to a migration
    # prod HAS applied must stay blocked — that divergence is the whole reason
    # this guard exists.
    d = _make_repo(["20260813000099_a.sql"])
    base = _git_sha(d)
    (d / MIG / "20260813000099_a.sql").write_text("-- changed content\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "edit applied")
    r = _run_script_with_token("diff", d, base, versions=["20260813000099"])
    assert r.returncode == 1, r.stdout
    assert "append-only" in r.stdout.lower(), r.stdout


def test_diff_unapplied_content_edit_is_blocked_with_token():
    # #2240 scope (c)+(a), never (b): an in-place edit (M) has NO destination
    # version, so there is no content-independent forward bound that can make it
    # safe against the applied-set API's documented multi-day lag. It is a
    # violation even when the edited version reads as unapplied.
    d = _make_repo(["20260813000099_a.sql"])
    base = _git_sha(d)
    (d / MIG / "20260813000099_a.sql").write_text("-- changed content\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "edit unapplied")
    r = _run_script_with_token("diff", d, base, versions=["0001"])
    assert r.returncode == 1, r.stdout
    assert "append-only" in r.stdout.lower(), r.stdout
    assert "20260813000099" in r.stdout, r.stdout


def _run_with_body(repo: Path, base_sha: str, raw_body: str,
                   http_code: int = 200) -> subprocess.CompletedProcess:
    """Drive the guard with an arbitrary (possibly malformed) API body."""
    stub = FIXTURES / "stub-raw.sh"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n{http_code}\\n' '{raw_body}'\n"
    )
    stub.chmod(0o755)
    env = dict(os.environ)
    env["DRIFT_REPO"] = str(repo)
    env["DRIFT_BASE_SHA"] = base_sha
    env["DRIFT_CURL"] = str(stub)
    env["DRIFT_API_URL"] = "https://api.supabase.invalid"
    env["DRIFT_TOKEN"] = "test-token"
    return subprocess.run(
        ["bash", str(SCRIPT), "diff"],
        capture_output=True, text=True, env=env, cwd=REPO_ROOT,
    )


def _edited_applied_fixture() -> tuple[Path, str]:
    d = _make_repo(["20260813000099_a.sql"])
    base = _git_sha(d)
    (d / MIG / "20260813000099_a.sql").write_text("-- edited\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "edit")
    return d, base


def test_api_body_that_is_not_a_json_array_fails_closed():
    # The #2240 widening admits M/D/T as well as R*, so an EMPTY applied set is
    # no longer harmless: it reads as "nothing is applied" and exempts an edit
    # to an APPLIED migration — the #1001 class. An HTTP 200 whose body is not a
    # JSON array must therefore keep the strict block, not silently pass.
    for body in ('{"message":"boom"}', "[]", '[{"name":"20260813000099"}]',
                 "[1,2]", '{"version":"20260813000099"}', "not json at all"):
        d, base = _edited_applied_fixture()
        r = _run_with_body(d, base, body)
        assert r.returncode == 1, (
            f"body={body!r} must fail CLOSED (got {r.returncode})\n{r.stdout}"
        )


def test_whitespace_in_applied_version_still_blocks():
    # A serialization difference (padding here) must not demote an APPLIED
    # version to exempt.
    d, base = _edited_applied_fixture()
    r = _run_with_body(d, base, '[{"version":" 20260813000099 "}]')
    assert r.returncode == 1, r.stdout


def test_multi_element_applied_set_still_blocks():
    # Guards a stream-joining regression: normalising the applied set with a
    # stream-wide `tr -d '[:space:]'` deletes the NEWLINE between versions and
    # merges them into one token, so `grep -qxF` matches NOTHING and the guard
    # exempts EVERYTHING as soon as prod has two or more migrations. A
    # single-element fixture cannot detect that — so this one has several.
    d, base = _edited_applied_fixture()
    r = _run_with_body(
        d, base,
        '[{"version":"20260813000098"},{"version":"20260813000099"},'
        '{"version":"20260813100100"}]',
    )
    assert r.returncode == 1, r.stdout
    assert "append-only" in r.stdout.lower(), r.stdout


def test_multi_element_unapplied_set_still_exempts():
    # The same multi-element shape in the safe direction: a FORWARD prefix rename
    # whose endpoints are genuinely absent must still be exempt. (A stream-wide
    # `tr -d '[:space:]'` would merge these tokens into one and make the match
    # fail, silently blocking every rename — so the multi-element case matters.)
    d = _make_repo(["20260813000099_a.sql"])
    base = _git_sha(d)
    (d / MIG / "20260813000099_a.sql").rename(d / MIG / "20260813100100_a.sql")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "forward renumber, multi-element applied set")
    r = _run_with_body(
        d, base,
        '[{"version":"20200101000000"},{"version":"20260813000001"}]',
    )
    assert r.returncode == 0, r.stdout
    assert "exception" in r.stdout.lower(), r.stdout


def test_wellformed_applied_body_still_blocks():
    # Control: the normal path is unchanged.
    d, base = _edited_applied_fixture()
    r = _run_with_body(d, base, '[{"version":"20260813000099"}]')
    assert r.returncode == 1, r.stdout


def test_rename_to_an_already_applied_new_version_is_blocked():
    # Both endpoints must be unapplied. Renaming an unapplied migration ONTO a
    # version prod already ran would put different content at that version.
    # (Byte-identical content here, so git reports R100 and this exercises the
    # R* branch; the D+A branch is pinned separately below.)
    d = _make_repo(["20260813000099_a.sql"])
    base = _git_sha(d)
    (d / MIG / "20260813000099_a.sql").rename(d / MIG / "20260813100100_a.sql")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "rename onto applied version")
    r = _run_script_with_token("diff", d, base, versions=["20260813100100"])
    assert r.returncode == 1, r.stdout
    assert "append-only" in r.stdout.lower(), r.stdout


def test_d_plus_a_renumber_onto_an_applied_version_is_blocked():
    # The PRIMARY form of the #2240 remedy: a re-land with a real content delta
    # degrades to D+A rather than R<sim> (git pairs by similarity). The
    # destination must still be checked — an R*-only check misses this entirely.
    d = _make_repo(["20260813000099_a.sql"])
    base = _git_sha(d)
    (d / MIG / "20260813000099_a.sql").unlink()
    (d / MIG / "20260813100100_a.sql").write_text(
        "-- a wholly different body, so git reports D+A and not R\n" * 4
    )
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "D+A renumber onto applied version")
    r = _run_script_with_token("diff", d, base, versions=["20260813100100"])
    assert r.returncode == 1, r.stdout
    assert "append-only" in r.stdout.lower(), r.stdout


def test_d_plus_a_renumber_onto_an_unapplied_version_is_blocked():
    # A re-land whose content delta is large enough that git reports it as D+A
    # (not R<sim>) is NOT a prefix rename: the `D` line carries no destination
    # version and therefore no forward bound, so it cannot be exempted under
    # #2240 scope (c)+(a). Only a git-detected R* forward rename is admissible.
    d = _make_repo(["20260813000099_a.sql"])
    base = _git_sha(d)
    (d / MIG / "20260813000099_a.sql").unlink()
    (d / MIG / "20260813100100_a.sql").write_text(
        "-- a wholly different body, so git reports D+A and not R\n" * 4
    )
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "D+A renumber onto free version")
    r = _run_script_with_token("diff", d, base, versions=["20260813000098"])
    assert r.returncode == 1, r.stdout
    assert "append-only" in r.stdout.lower(), r.stdout


def test_added_file_at_an_already_applied_version_is_blocked():
    # An added file is not a violation by itself, but placing one at a version
    # prod has ALREADY applied is the #1001 divergence: prod skips it, so the
    # repo's content for that version never ran.
    d = _make_repo(["20260813000099_a.sql"])
    base = _git_sha(d)
    (d / MIG / "20260813100100_new.sql").write_text("-- new\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "add at applied version")
    r = _run_script_with_token("diff", d, base, versions=["20260813100100"])
    assert r.returncode == 1, r.stdout
    assert "append-only" in r.stdout.lower(), r.stdout


def test_added_file_at_an_unapplied_version_is_allowed():
    # A normal new migration must not be reported as an exception, and must pass.
    d = _make_repo(["20260813000099_a.sql"])
    base = _git_sha(d)
    (d / MIG / "20260813100100_new.sql").write_text("-- new\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "add at free version")
    r = _run_script_with_token("diff", d, base, versions=["0001"])
    assert r.returncode == 0, r.stdout
    # It is an ordinary new migration, not an "exception".
    assert "exception" not in r.stdout.lower(), r.stdout


def test_added_file_without_token_is_allowed():
    # Fork PRs have no token. Adding migrations is their normal case and must
    # NOT become a violation just because the applied set is unreadable.
    d = _make_repo(["20260813000099_a.sql"])
    base = _git_sha(d)
    (d / MIG / "20260813100100_new.sql").write_text("-- new\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "add, no token")
    r = _run_script("diff", d, base)
    assert r.returncode == 0, r.stdout


# ── #2240 scope fix: only a FORWARD R* prefix rename is exempt ─────────────
# The widened exemption admitted M/D/T because `new_ver` was empty for every
# non-R status; a `D` also had no direction check. Both are now closed, and an
# addition with no numeric prefix is no longer misread as an edit.


def test_diff_unapplied_deletion_is_a_violation():
    # Deleting an unapplied merged migration carries NO destination version, so
    # there is no forward bound that could make it safe against the applied-set
    # API's lag. It must be reported as a violation, never silently exempted.
    d = _make_repo(["20260813000099_a.sql"])
    base = _git_sha(d)
    (d / MIG / "20260813000099_a.sql").unlink()
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "delete unapplied")
    r = _run_script_with_token("diff", d, base, versions=["20260813000098"])
    assert r.returncode == 1, r.stdout
    assert "append-only" in r.stdout.lower(), r.stdout
    assert "20260813000099" in r.stdout, r.stdout


def test_diff_unapplied_migration_rewrite_is_a_violation():
    # An in-place rewrite (M) of an unapplied migration was the primary
    # fail-open: `new_ver` stayed empty, `[ -z "$new_ver" ]` was true, and the
    # branch exempted a content rewrite to arbitrary DDL. No destination version
    # means no forward bound, so M is never exempt.
    d = _make_repo(["20260813000099_a.sql"])
    base = _git_sha(d)
    (d / MIG / "20260813000099_a.sql").write_text("DROP TABLE users;\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "rewrite unapplied")
    r = _run_script_with_token("diff", d, base, versions=["20260813000098"])
    assert r.returncode == 1, r.stdout
    assert "append-only" in r.stdout.lower(), r.stdout
    assert "20260813000099" in r.stdout, r.stdout


def test_diff_backward_renumber_is_a_violation():
    # A prefix rename whose destination sorts BEFORE the newest applied version
    # is a backward renumber: it could land under a version prod already ran,
    # and the applied-set API's lag means "absent" alone cannot prove it did not.
    # The forward bound must reject it even with both endpoints unapplied.
    d = _make_repo(["20260813000099_a.sql"])
    base = _git_sha(d)
    (d / MIG / "20260813000099_a.sql").rename(d / MIG / "20260813000001_a.sql")
    (d / MIG / "20260813000001_a.sql").write_text(
        (d / MIG / "20260813000001_a.sql").read_text() + "-- content edit\n"
    )
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "backward renumber")
    r = _run_script_with_token("diff", d, base, versions=["20260813000098"])
    assert r.returncode == 1, r.stdout
    assert "append-only" in r.stdout.lower(), r.stdout


def test_diff_added_non_prefixed_file_is_allowed():
    # An ADDED file with no numeric prefix (e.g. a README) is not a migration: the
    # CLI cannot apply it, so diff mode must not invent a violation for it.
    # Pre-fix, its empty old_ver fell through to a violation with a misleading
    # "edit/rename/delete" message.
    d = _make_repo(["20260813000099_a.sql"])
    base = _git_sha(d)
    (d / MIG / "README.md").write_text("# migrations\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "add README")
    r = _run_script_with_token("diff", d, base, versions=["20260813000098"])
    assert r.returncode == 0, r.stdout
    assert "::error::" not in r.stdout, r.stdout
    assert "exception" not in r.stdout.lower(), r.stdout


# ── #6862: the forward bound is not enough on its own ──────────────────────
# FIX 1 — REMOTE_MAX is derived from the SAME lagging applied set the bound
# exists to compensate for (#2240 records a ~4.6-day window in which a deploy
# reported success while the schema_migrations row was still missing), so a
# version prod HAS applied but the API omits still satisfies `new_ver >
# REMOTE_MAX`. The destination must therefore also sort strictly after EVERY
# version present in the BASE TREE — a renumber must never land on a version the
# repository has already carried. The base tree is read locally (`git ls-tree`
# against the ref already used for the diff), so it is not another view of the
# lagging API.


def test_rename_onto_a_version_already_in_the_base_tree_is_blocked():
    # The API stub LAGS: it omits 20260901000000, which the base tree already
    # carries (via _later.sql). `new_ver > REMOTE_MAX` therefore HOLDS, and the
    # pre-change guard exempted the rename (rc=0); the base-tree bound must
    # reject it.
    d = _make_repo(["20260813000099_src.sql", "20260901000000_later.sql"])
    base = _git_sha(d)
    (d / MIG / "20260813000099_src.sql").rename(d / MIG / "20260901000000_src.sql")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "rename onto base-tree version, API lagging")
    r = _run_script_with_token("diff", d, base, versions=["20260813000098"])
    assert r.returncode == 1, r.stdout
    assert "append-only" in r.stdout.lower(), r.stdout
    assert "20260901000000" in r.stdout, r.stdout


def test_backward_in_numeric_forward_in_lex_rename_is_blocked():
    # A destination that is numerically EARLIER than the base-tree max but
    # lexicographically LATER than the lagging REMOTE_MAX: the old forward bound
    # admitted it (rc=0), the base-tree bound must reject it. The approved live
    # case (forward past base max) is pinned by
    # test_approved_live_forward_renumber_still_allowed.
    d = _make_repo(["20260926000001_src.sql", "20260927000001_later.sql"])
    base = _git_sha(d)
    (d / MIG / "20260926000001_src.sql").rename(d / MIG / "20260901000000_src.sql")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "backward renumber past the lagging API")
    r = _run_script_with_token("diff", d, base, versions=["20260813000098"])
    assert r.returncode == 1, r.stdout
    assert "append-only" in r.stdout.lower(), r.stdout


# FIX 2 — the exempt condition never required a strict renumber, so a
# same-version relabel escaped as an "effective deletion", contradicting the
# header's "M/T/D are NEVER exempt". The destination must be a real migration
# at a DIFFERENT version.


def test_same_version_relabel_to_bak_is_blocked():
    # Vector G4: `X_a.sql` → `X_a.sql.bak` applies NOTHING at that version — the
    # exact `D` outcome this guard reports as a violation — yet the pre-change
    # exemption admitted it (rc=0). In THIS fixture the blocker is the
    # destination-not-a-migration clause (basename must match ^[0-9]+_.*\.sql$):
    # a `.bak` fails that pattern whatever `new_ver` is, so this test does NOT
    # exercise the strict-renumber clause. That clause is defence-in-depth
    # against a base tree that does not contain the renamed source — pinned by
    # test_same_version_rename_from_a_base_tree_missing_the_source_is_blocked.
    d = _make_repo(["20260813000099_a.sql"])
    base = _git_sha(d)
    (d / MIG / "20260813000099_a.sql").rename(d / MIG / "20260813000099_a.sql.bak")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "same-version relabel to .bak")
    r = _run_script_with_token("diff", d, base, versions=["20260813000098"])
    assert r.returncode == 1, r.stdout
    assert "append-only" in r.stdout.lower(), r.stdout


def test_same_version_rename_from_a_base_tree_missing_the_source_is_blocked():
    # P2-1 (#6862): the `new_ver != old_ver` clause has exactly ONE load-bearing
    # world, and this fixture is it. The bound reads tree(DRIFT_BASE_SHA) while
    # `git diff "$DRIFT_BASE_SHA"...HEAD` diffs from merge-base(A,HEAD); when
    # the renamed source is DELETED between the merge-base and A, tree(A) no
    # longer carries the source's version, so BASE_VERSION_MAX sorts BELOW it
    # and a same-version destination satisfies `new_ver > BASE_VERSION_MAX`.
    # With the strict-renumber clause removed this rename is EXEMPTED (rc=0);
    # only that clause blocks it. In an ordinary history the source IS in the
    # base tree, so old_ver <= BASE_VERSION_MAX and the clause can never fire —
    # hence this fixture deliberately diverges the two trees.
    d = FIXTURES / "repo-divergent"
    if d.exists():
        subprocess.run(["rm", "-rf", str(d)], check=True)
    d.mkdir(parents=True)
    _git(d, "init", "-q", "-b", "main")
    _git(d, "config", "user.email", "t@t")
    _git(d, "config", "user.name", "t")
    (d / MIG).mkdir(parents=True)
    (d / MIG / "20260813000000_early.sql").write_text("-- early\n")
    (d / MIG / "20260813000099_src.sql").write_text("-- src body\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "merge-base: early + src")
    # The PR's BASE (A) deletes the source, so tree(A) lacks its version.
    _git(d, "checkout", "-q", "-b", "base")
    (d / MIG / "20260813000099_src.sql").unlink()
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "base deletes the source")
    base = _git_sha(d)
    # HEAD descends from the merge-base, not from A → A is not an ancestor of
    # HEAD, and the same version is renamed to a still-valid `.sql` file.
    _git(d, "checkout", "-q", "main")
    (d / MIG / "20260813000099_src.sql").rename(d / MIG / "20260813000099_dst.sql")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "same-version rename")
    r = _run_script_with_token("diff", d, base, versions=["20260813000000"])
    assert r.returncode == 1, r.stdout
    assert "append-only" in r.stdout.lower(), r.stdout


def test_rename_to_a_non_sql_destination_is_blocked():
    # A destination the Supabase CLI cannot pick up (basename not
    # ^[0-9]+_.*\.sql$) is not a migration: exempting the rename would drop the
    # old file while adding nothing prod can apply. Pre-change: rc=0.
    d = _make_repo(["20260813000099_a.sql"])
    base = _git_sha(d)
    (d / MIG / "20260813000099_a.sql").rename(d / MIG / "20260901000000_a.txt")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "rename to non-sql destination")
    r = _run_script_with_token("diff", d, base, versions=["20260813000098"])
    assert r.returncode == 1, r.stdout
    assert "append-only" in r.stdout.lower(), r.stdout


def test_approved_live_forward_renumber_still_allowed():
    # The APPROVED live case (#2240): rename 20260926000001 → 20261001000001 when
    # the base tree's max is 20260927000001. It must stay ALLOWED — the FIX 1
    # base-tree bound tightens the exemption without breaking the forward renumber
    # the drift gate prescribes.
    d = _make_repo(["20260926000001_live.sql", "20260927000001_b.sql"])
    base = _git_sha(d)
    (d / MIG / "20260926000001_live.sql").rename(d / MIG / "20261001000001_live.sql")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "approved forward renumber")
    r = _run_script_with_token("diff", d, base, versions=["20260813000098"])
    assert r.returncode == 0, r.stdout
    assert "exception" in r.stdout.lower(), r.stdout


#: The header's double-booking EXAMPLE, as the guard states it: the version and the
#: two migrations that both claimed it. The paragraph exists so a lane trusts it when
#: picking a prefix (#9680), so a party that is not one of the racers is worse than no
#: example at all: a lane reading `blog_indexnow` there concludes the `20261023` peer
#: was THIS migration (which has since been renumbered) and that `20261023000001` is
#: therefore free — while `#9707` is still open on it. That is the double-booking the
#: paragraph exists to prevent, reintroduced by its own evidence.
#: The header is a comment block, so the example is read from the text with the
#: leading `#` markers stripped (the paragraph wraps mid-sentence). A party may carry
#: a trailing PR reference — `blog_author_backfill_atomic #9707` — and that marker is
#: load-bearing rather than decorative: it is the ONLY honest way to name a racer that
#: is not in the tree, because an open PR's claim lives on a branch, not in `main`.
_COMMENT_PREFIX = re.compile(r"(?m)^#\s?")
_PARTY = r"(\w+)(?:\s*#(\d+))?"
_DOUBLE_BOOKED = re.compile(
    rf"which is how (\d{{14}}) was double-booked\s*\(\s*{_PARTY}\s+vs\s+{_PARTY}\s*\)",
    re.S,
)


def _shipped_version(stem_fragment: str) -> str | None:
    """The version this repo's tree carries for a migration whose basename names it."""
    found = sorted(
        p.name.split("_", 1)[0]
        for p in (REPO_ROOT / MIG).glob(f"*_{stem_fragment}*.sql")
    )
    return found[0] if found else None


def _versions_for_leaf(leaf: str) -> set[str]:
    """Every version this tree ships for the EXACT migration leaf `leaf`.

    Exact, not a substring glob: a guidance scan keyed on substrings would red a
    fixture name that merely contains a real leaf (`_a` inside `_alpha`).
    """
    return {
        p.name.split("_", 1)[0]
        for p in (REPO_ROOT / MIG).glob("*.sql")
        if p.name.split("_", 1)[-1].removesuffix(".sql") == leaf
    }


def _example_problems(
    header: str, shipped: Callable[[str], str | None] = _shipped_version
) -> list[str]:
    """What is wrong with the header's double-booking example — empty when it is right.

    The rule, and why each half exists:

    * A party the tree ships at a version OTHER than the double-booked one is the
      fail-open shape (#9693's first wording): a lane concludes the real holder has
      moved and takes the version.
    * A party the tree does not ship at all must CITE ITS PR (`name #1234`). Without
      a reference the example is unverifiable prose — the reader cannot tell "not
      merged yet" from "never existed" — and it certifies nothing.
    * A party that DOES cite a PR is deliberately NOT version-checked. That is the
      post-merge state of every live race: once `#9707` lands its renumbered file,
      its version is legitimately no longer the double-booked one, and requiring the
      two to agree would red `main` the moment the racer fixed itself — a false red
      with no repair, since the example cannot be made true again without rewriting
      the paragraph. The claim being checked is "these were the two claimants", not
      "these are their versions today".
    """
    matches = list(_DOUBLE_BOOKED.finditer(header))
    if not matches:
        return [
            "the guard header no longer states the double-booking example in the form "
            "`which is how <version> was double-booked (<a> vs <b>)` — if the wording "
            "moved, re-point this test rather than deleting it: the paragraph is the "
            "only place a lane is told the scan's answer is a snapshot"
        ]
    problems: list[str] = []
    # EVERY occurrence is read, not just the first (`finditer`, never `search`).
    # `search` would read the first example and leave a second, wrong one unread
    # behind it — a false PASS of exactly the shape `_stated_counts` in the sibling
    # census guard was fixed for (a count stated twice, one copy read). One example
    # is the normal case, so the label is only disambiguated when there are more.
    for index, match in enumerate(matches, start=1):
        where = (
            f"example {index} of {len(matches)}"
            if len(matches) > 1
            else "the header's double-booking example"
        )
        version = match.group(1)
        parties = [(match.group(2), match.group(3)), (match.group(4), match.group(5))]
        if parties[0][0] == parties[1][0]:
            problems.append(
                f"{where} names `{parties[0][0]}` twice — it "
                "must name two DIFFERENT migrations, or it is not evidence of a race"
            )
        for name, pr in parties:
            if pr:
                continue
            observed = shipped(name)
            if observed is None:
                problems.append(
                    f"{where}: `{name}` is named as a racer for {version}, but no "
                    "migration in this tree carries that name and no PR reference "
                    "says where it lives — cite the open PR (`name #1234`) or name a "
                    "migration that exists"
                )
            elif observed != version:
                problems.append(
                    f"{where}: `{name}` is the leaf of a migration this tree ships "
                    f"at {observed}, not at the double-booked {version}"
                )
    return problems


def test_the_double_booking_example_names_actual_racers():
    """The header's example must not name a renamed-away party as a claimant.

    Measured on the shipped header before the #9693 review: it named `blog_indexnow`
    at `20261023000001`, while this repo's `blog_indexnow` migration has only ever
    been `20261022000001`/`20261024000001` — and `20261023000001` is held by
    `record_session_narrative` on `main` and by `#9707`
    (`blog_author_backfill_atomic`) still open. Pre-fix this test reds; the fix is to
    name the actual pair, with the unmerged half's PR cited.
    """
    header = _COMMENT_PREFIX.sub("", SCRIPT.read_text(encoding="utf-8"))
    problems = _example_problems(header)
    assert not problems, (
        "the guard header's double-booking example does not hold against this tree:\n  "
        + "\n  ".join(problems)
        + "\nA lane that trusts it concludes the real holder has moved and takes the "
        "version. Name the migrations that actually raced, and cite the PR for a "
        "racer that is not in the tree yet."
    )


def test_the_double_booking_rule_reds_on_a_party_shipped_at_another_version():
    """Non-vacuity: the wording this test was written for must still red.

    `blog_indexnow` is carried at `20261024` here, so naming it as a `20261023`
    claimant is exactly the fail-open the paragraph was added to prevent.
    """
    header = (
        "which is how 20261023000001 was double-booked "
        "(blog_indexnow vs record_session_narrative #9707)."
    )
    shipped = {
        "blog_indexnow": "20261024000001",
        "record_session_narrative": "20261023000001",
    }
    problems = _example_problems(header, shipped.get)
    assert any("blog_indexnow" in p and "20261024000001" in p for p in problems), (
        f"the rule accepted a party the tree carries at another version: {problems}"
    )


def test_the_double_booking_rule_survives_the_racer_landing():
    """The time bomb: an open racer that lands RENUMBERED must not red this suite.

    `#9707` cannot merge at `20261023000001` (`main` already holds it), so it must
    renumber. When it does, its leaf is shipped at a version that is no longer the
    double-booked one — and before this rule was scoped, that turned this file into a
    false red on `main`, on the very PR that repairs the collision. The PR reference
    is what licenses the mismatch, so a header that keeps it stays green either way.
    """
    header = (
        "which is how 20261023000001 was double-booked "
        "(record_session_narrative vs blog_author_backfill_atomic #9707)."
    )
    shipped = {
        "record_session_narrative": "20261023000001",
        "blog_author_backfill_atomic": "20261025000001",  # #9707 after its renumber
    }
    assert _example_problems(header, shipped.get) == [], (
        "a cited open racer that has since landed at a different version reds the "
        "example — the false red this rule exists to avoid"
    )


def test_the_double_booking_rule_refuses_an_uncited_party_the_tree_does_not_ship():
    """The converse hole: a party with no PR and no file certifies nothing.

    `shipped is None` used to be skipped outright, so a header could name a
    migration that never existed and still pass — which is exactly the "not merged
    yet / never existed" ambiguity the example is supposed to resolve.
    """
    header = (
        "which is how 20261023000001 was double-booked "
        "(record_session_narrative vs total_nonsense)."
    )
    shipped = {"record_session_narrative": "20261023000001"}
    problems = _example_problems(header, shipped.get)
    assert any("total_nonsense" in p for p in problems), (
        f"an uncited, non-existent party passed the rule: {problems}"
    )


def test_the_rule_reads_EVERY_example_in_the_header_not_just_the_first():
    """Non-vacuity for the `finditer`: a second, WRONG example must red.

    Measured before this arm, by reverting the matcher to `_DOUBLE_BOOKED.search`:
    every arm in this file stayed green, because each fixture (and the shipped
    header) states exactly ONE example — so the first-match defect had no reader
    and could be reintroduced silently. The sibling census guard was repaired the
    same way and shipped a fixture stating its figure TWICE, precisely so that a
    first-match reader reds; this is that shape: the first example is correct and
    the SECOND is the fail-open one a lane greps just as readily.
    """
    header = (
        "which is how 20261023000001 was double-booked "
        "(record_session_narrative vs blog_author_backfill_atomic #9707). "
        "A second paragraph, wrongly: which is how 20261023000001 was "
        "double-booked (blog_indexnow vs record_session_narrative)."
    )
    shipped = {
        "record_session_narrative": "20261023000001",
        "blog_author_backfill_atomic": "20261025000001",  # #9707 after its renumber
        "blog_indexnow": "20261024000001",
    }
    problems = _example_problems(header, shipped.get)
    assert any("example 2 of 2" in p and "blog_indexnow" in p for p in problems), (
        "the rule read only the first double-booking example, so a second, wrong "
        f"example passed unread: {problems}"
    )


#: The files whose prefix guidance a lane reads before choosing a version. A
#: `<version>_<leaf>` mention in their PROSE that the tree no longer carries at that
#: version is a stale pointer with the same harm as a stale example: the reader greps
#: the version, finds nothing, and cannot tell whether the guidance or the tree is
#: wrong. Code is deliberately out of scope — a fixture may legitimately construct a
#: hypothetical duplicate pair out of real leaf names, which is not a claim about the
#: tree, while a comment is.
_PREFIX_GUIDANCE = (
    SCRIPT,
    REPO_ROOT / "tests" / "test_migration_drift_gate.py",
)
_MIG_MENTION = re.compile(r"\b(\d{14})_([a-z0-9_]+)")


def _prose_lines(path: Path) -> list[tuple[int, str]]:
    """The `#`-comment lines of a file — the half that states claims about the tree."""
    return [
        (n, line)
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if line.lstrip().startswith("#")
    ]


def test_prefix_guidance_names_no_version_the_tree_no_longer_carries():
    """A renamed migration must not be named at the version it was renumbered away from.

    Checks a prose mention only when the tree ships a migration with that exact leaf,
    so fixture names and historical prose that names a leaf without a version are both
    out of scope. `#9693` renumbered `blog_indexnow` off `20261022000001`, and a
    guidance comment still pairing them is the stale pointer this arm reds.
    """
    problems = []
    for path in _PREFIX_GUIDANCE:
        for lineno, line in _prose_lines(path):
            for version, leaf in _MIG_MENTION.findall(line):
                versions = _versions_for_leaf(leaf)
                if versions and version not in versions:
                    problems.append(
                        f"{path.relative_to(REPO_ROOT)}:{lineno}: names "
                        f"`{version}_{leaf}`, but the tree ships that migration at "
                        f"{sorted(versions)}"
                    )
    assert not problems, (
        "prefix guidance points at a version this tree no longer carries:\n  "
        + "\n  ".join(problems)
        + "\nName the version the file actually sits at, or drop the figure — a "
        "renamed migration makes the old pairing unverifiable."
    )

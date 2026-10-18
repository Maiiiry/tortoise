"""#3429 — a provisioned repo's AGENTS.md block must be upgradeable, and say so.

THE DEFECT
----------
`install-tortoise-skills.sh` writes a marker-delimited Tortoise block into the
repo-root ``AGENTS.md`` of a project it provisions. The markers made a re-run
IDEMPOTENT — but nothing made a STALE block DETECTABLE. So a repo provisioned
before the block's text changed kept re-reading the old text in EVERY later
session, and there was no way for it or its user to tell: the installer was the
only writer, it rewrote the block silently, and the block itself carried no
version. Fixing what the installer WRITES (that is #3232) does not repair
repos that already carry the old block — mutating a behavioural contract inside
someone else's repo is a migration, and this file is its acceptance test.

THE FIX THIS PINS
-----------------
1. The emitted block carries a version line INSIDE the marker window
   (``<!-- tortoise:block vN -->``), so the ordinary marker-delimited replace
   refreshes it and a stale block is greppable.
2. The installer READS the version it finds and says what it did — created,
   upgraded (``v1 -> v2`` or ``unversioned -> v2``), or already current. A
   silent rewrite is the failure this replaces.
3. Re-running over an old block does not duplicate it and does not touch
   content outside the markers.

Every test drives the REAL installer end-to-end — a temporary copy with ONLY
its ``SKILLS_BASE`` URL substituted for a local ``file://`` fixture — and
asserts the bytes on disk plus the installer's own stdout, never source text.

Mutations that turn these RED (each verified RED before merge, restored byte-exact):
  * delete the version line from the emitted block        -> every version assertion
    fails (nothing to grep, no upgrade branch);
  * blank the ``prev_version`` read                       -> the migration and
    reinstall assertions fail (unversioned / already-current both misreport);
  * make the "already current" branch unreachable         -> the reinstall test fails;
  * edit the block's TEXT without bumping the version      -> the prose pin fails
    (the only thing that makes a stale ``AGENTS_BLOCK_VERSION`` visible);
  * decide the pre-state with an unanchored match          -> the malformed tests
    fail: the anchored split finds no pair and the block is duplicated;
  * make ``near()`` return 1 for any mention               -> the prose test fails
    (precision: quoting the marker is documentation, not a block);
  * make ``_emit_fail`` RETURN instead of exit             -> the unreadable-file
    test fails: ``cmd || helper`` is an errexit-exempt list, so a returning
    helper lets the next statement run and the run reports SUCCESS at exit 0
    over an untouched file;
  * drop the mangled-marker detection                      -> the internal-spacing
    test fails (that marker appended a second block);
  * strip ALL CRs when comparing (instead of CRLF only)    -> the CR-inside-a-line
    test fails (a real content change read as "already current");
  * restore the process-substitution comparison            -> the failing-``sed``
    test fails: two failed substitutions yield two EMPTY streams, ``cmp`` calls
    them EQUAL, and a stale block is reported "already current" at exit 0;
  * count markers inside a fenced code block               -> the fenced-example
    test fails (a project that DOCUMENTS the markers was refused, exit 3, and the
    pre-PR installer handled that very file);
  * make the non-numeric version fall through to ``unversioned`` -> that test
    fails, and the shell then prints ``integer expression expected`` and reports
    "same version, updated text";
  * make the unparsed-state arm a no-op                    -> the awk-crash test
    fails: nothing is written and the run claims ``upgraded`` at exit 0;
  * stage the block through a predictable ``$target.tmp``  -> the planted-symlink
    test fails (the write follows the link, clobbers an unrelated file, and turns
    ``AGENTS.md`` into a symlink to it — #4327's threat model).

Cycle 2 also measured a version token in prose outside the block (read as
"current") and an older installer overwriting a newer block (called an
"upgrade"); both are tests here. Cycle 3 measured the false-success path
end-to-end: an unreadable ``AGENTS.md`` returned 0 and printed an "upgrade" the
pre-PR installer would have failed closed on. Cycle 4 measured three more: the
line-ending comparison failing OPEN on a broken ``sed``, a fenced EXAMPLE of the
markers hard-refusing the install (a regression against the pre-PR installer),
and a documented ``not a number`` report branch with no test behind it.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALLER = (REPO_ROOT / "website" / "apps" / "dashboard" / "public"
             / "install-tortoise-skills.sh")

#: The 3 reusable capabilities the installer ships (#4365). Onboarding is
#: delivered as INSTRUCTIONS, never installed as a skill.
SKILLS = ("how-to-use-tortoise", "tortoise-decide", "tortoise-file-finding")

START = "<!-- tortoise:start -->"
END = "<!-- tortoise:end -->"
FOREIGN_BEFORE = "## House rules\n\n- Foreign line ABOVE the block, never ours.\n"
FOREIGN_AFTER = "\n## More house rules\n\n- Foreign line BELOW the block.\n"

#: What a repo provisioned BEFORE #3429 carries: the same markers, the old
#: ordering text, and NO version line.
LEGACY_BLOCK = f"""{START}
# Tortoise — agent memory (this project is connected to Tortoise Cloud)
- When the user says "Set up Tortoise", follow the onboarding instructions at
  https://example.invalid/tortoise-onboarding/SKILL.md. Write the MCP config,
  restart the client so the new session reads it, then verify with
  tortoise_health before reporting done.
{END}
"""


def _payload(name: str) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: fixture payload for {name}\n"
        "domain: capability\n"
        "type: Workflow\n"
        "status: live\n"
        "updated: 2026-10-18\n"
        "allowed-tools: read write bash grep\n"
        "---\n"
        f"\n# {name}\n\nFixture body for {name}.\n"
    )


def _fixture_tree(tmp_path: Path) -> str:
    base = tmp_path / "served-skills"
    for name in SKILLS:
        d = base / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(_payload(name), encoding="utf-8")
    return f"file://{base}"


def _patched_installer(tmp_path: Path, base_uri: str) -> Path:
    """The REAL installer with only its download base swapped."""
    src = INSTALLER.read_text(encoding="utf-8")
    patched, n = re.subn(r'^SKILLS_BASE=.*$', f'SKILLS_BASE="{base_uri}"', src,
                         count=1, flags=re.M)
    assert n == 1, "installer SKILLS_BASE anchor not found — update this test"
    out = tmp_path / "installer.sh"
    out.write_text(patched, encoding="utf-8")
    return out


def _run(installer: Path, home: Path, cwd: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["HOME"] = str(home)
    # The installer must not need a terminal; keep it offline and quiet.
    env.pop("TORTOISE_API_KEY", None)
    return subprocess.run(
        ["bash", str(installer), "--harness", "codex"],
        cwd=str(cwd), env=env, capture_output=True, text=True, timeout=300,
    )


def _agents_report(stdout: str) -> str:
    """The AGENTS.md line only — the skills lines print "(already current)" too,
    which is a different claim about a different artifact."""
    lines = [ln for ln in stdout.splitlines() if "AGENTS.md" in ln]
    assert lines, f"installer printed no AGENTS.md line:\n{stdout}"
    return "\n".join(lines)


def _versioned(text: str) -> list[int]:
    return [int(v) for v in re.findall(r"tortoise:block v(\d+)", text)]


def test_fresh_install_writes_a_versioned_block(tmp_path):
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()

    done = _run(installer, home, project)
    assert done.returncode == 0, f"installer failed:\n{done.stdout}\n{done.stderr}"

    agents = (project / "AGENTS.md").read_text(encoding="utf-8")
    versions = _versioned(agents)
    assert len(versions) == 1, f"expected exactly one version line, got {agents!r}"
    assert START in agents and END in agents
    # The version must sit INSIDE the marker window, or the replace below
    # leaves it stale the first time the block is refreshed.
    assert agents.index(START) < agents.index("tortoise:block v") < agents.index(END)
    assert "created" in _agents_report(done.stdout), (
        f"a fresh install must say it CREATED the block:\n{done.stdout}")


def test_installer_upgrades_a_legacy_block_without_duplicating_or_clobbering(tmp_path):
    """The migration itself: an unversioned block from before this change."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text(
        FOREIGN_BEFORE + LEGACY_BLOCK + FOREIGN_AFTER, encoding="utf-8")

    done = _run(installer, home, project)
    assert done.returncode == 0, f"installer failed:\n{done.stdout}\n{done.stderr}"

    agents = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert agents.count(START) == 1, f"the block was duplicated:\n{agents}"
    assert agents.count(END) == 1
    versions = _versioned(agents)
    assert len(versions) == 1, f"expected one version line, got {versions}\n{agents}"
    # The old text is GONE — that is the point of the migration.
    assert "restart the client so the new session reads it, then verify with" not in agents, (
        "the legacy ordering survived the refresh")
    # Foreign content is untouched, in order, on both sides.
    assert agents.startswith(FOREIGN_BEFORE), agents[:200]
    assert agents.endswith(FOREIGN_AFTER), agents[-200:]
    assert "unversioned" in _agents_report(done.stdout), (
        f"the upgrade must be REPORTED, not silent:\n{done.stdout}")


def test_installer_upgrades_a_versioned_block_and_names_both_versions(tmp_path):
    """v1 → v2 when the block already carried a version line."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text(
        FOREIGN_BEFORE
        + LEGACY_BLOCK.replace(START, f"{START}\n<!-- tortoise:block v1 -->")
        + FOREIGN_AFTER,
        encoding="utf-8",
    )

    done = _run(installer, home, project)
    assert done.returncode == 0, f"installer failed:\n{done.stdout}\n{done.stderr}"

    agents = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert agents.count("tortoise:block v") == 1, (
        f"the old version line must be REPLACED, not kept alongside the new:\n{agents}")
    upgraded = re.search(r"upgraded v(\d+) → v(\d+)", done.stdout)
    assert upgraded, f"expected a named upgrade in stdout:\n{done.stdout}"
    assert upgraded.group(1) == "1" and upgraded.group(2) != "1"


def test_reinstall_reports_already_current_and_changes_nothing(tmp_path):
    """A re-run is the documented upgrade path, so it must be a no-op when
    there is nothing to upgrade — and it must say so instead of claiming work."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()

    first = _run(installer, home, project)
    assert first.returncode == 0, first.stdout + first.stderr
    before = (project / "AGENTS.md").read_text(encoding="utf-8")

    second = _run(installer, home, project)
    assert second.returncode == 0, second.stdout + second.stderr
    after = (project / "AGENTS.md").read_text(encoding="utf-8")

    assert after == before, "a second run must not change the block"
    report = _agents_report(second.stdout)
    assert "already current" in report, (
        f"a re-run over a current block must say so:\n{report}")
    assert "created" not in report and "upgraded" not in report, report


# ── The report must be derived from what was WRITTEN, not from the version ──
#
# Cycle 1 found the message came from the version token alone: "already current"
# was printed while the body was being rewritten (drifted text, same version),
# and "upgraded"/"created" was printed while a malformed marker pair made the
# write duplicate the block and keep the stale one. Each test below is a
# reachable pre-state that produced a false report.


def _install(tmp_path: Path, agents: str | None):
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    if agents is not None:
        (project / "AGENTS.md").write_text(agents, encoding="utf-8")
    done = _run(installer, home, project)
    return done, (project / "AGENTS.md").read_text(encoding="utf-8")


def _drift_installer(tmp_path: Path) -> Path:
    """A copy whose block TEXT differs while the VERSION is unchanged — the
    hand-discipline failure the version line cannot see."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    src = installer.read_text(encoding="utf-8")
    drifted = src.replace(
        "- First-time MCP calls may prompt for approval",
        "- First-time MCP calls may prompt for approval (DRIFTED SENTENCE)",
        1,
    )
    assert drifted != src, "drift anchor not found — update this test"
    installer.write_text(drifted, encoding="utf-8")
    return installer


def test_same_version_different_text_is_not_already_current(tmp_path):
    """A version line is evidence of ORDER, not proof the text matches."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()

    assert _run(installer, home, project).returncode == 0
    drifted = _drift_installer(tmp_path)
    done = _run(drifted, home, project)

    assert done.returncode == 0, done.stdout + done.stderr
    report = _agents_report(done.stdout)
    assert "already current" not in report, (
        f"the body WAS rewritten, so 'already current' is false:\n{report}")
    assert "updated text" in report, (
        f"a same-version text change must be reported as a refresh:\n{report}")
    agents = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert "DRIFTED SENTENCE" in agents
    assert _versioned(agents) == [_current_version()]


def test_malformed_markers_are_refused_not_silently_duplicated(tmp_path):
    """A stray end marker made the anchored awk split swallow the tail: the
    report said 'upgraded' while user content was deleted and the stale block
    survived. The installer must refuse and say so."""
    agents_in = (
        FOREIGN_BEFORE
        + END                      # stray end BEFORE any start
        + "\nTAIL THAT MUST SURVIVE\n"
        + LEGACY_BLOCK.replace("OLD", "OLD")
        + FOREIGN_AFTER
    )
    done, _ = _install(tmp_path, agents_in)

    assert done.returncode != 0, (
        f"a malformed marker pair must fail the run, not report success:\n{done.stdout}")
    assert "MALFORMED" in done.stderr, done.stderr
    assert agents == agents_in, (
        f"the file must be left byte-identical when it is refused:\n{agents}")
    assert "TAIL THAT MUST SURVIVE" in agents


def test_start_marker_without_end_is_refused(tmp_path):
    done, agents = _install(tmp_path, LEGACY_BLOCK.replace(END + "\n", ""))

    assert done.returncode == 3, done.stdout
    assert "MALFORMED" in done.stderr
    assert agents.count(START) == 1, f"the block was duplicated:\n{agents}"
    assert agents == LEGACY_BLOCK.replace(END + "\n", "")


def test_version_token_outside_the_window_does_not_mean_current(tmp_path):
    """A repo that DOCUMENTS the marker in prose would otherwise read as
    versioned while carrying an unversioned, stale block."""
    agents_in = "We use a `tortoise:block v2` marker here.\n\n" + LEGACY_BLOCK
    done, agents = _install(tmp_path, agents_in)

    assert done.returncode == 0, done.stdout + done.stderr
    report = _agents_report(done.stdout)
    assert "already current" not in report, (
        f"the token was OUTSIDE the block, so nothing was current:\n{report}")
    assert "unversioned" in report, report
    assert "restart the client so the new session reads it, then verify with" not in agents


def test_an_older_installer_says_so_instead_of_calling_it_an_upgrade(tmp_path):
    """v3 block + v2 installer (pinned/cached/rolled-back copy)."""
    newer = _current_version() + 1
    agents_in = LEGACY_BLOCK.replace(START, f"{START}\n<!-- tortoise:block v{newer} -->")
    done, agents = _install(tmp_path, agents_in)

    assert done.returncode == 0, done.stdout + done.stderr
    report = _agents_report(done.stdout)
    assert "upgraded" not in report, (
        f"writing v2 over v3 is a DOWNGRADE, not an upgrade:\n{report}")
    assert "OLDER than the block" in report, report
    assert _versioned(agents) == [_current_version()]


#: sha256 of the block's PROSE with the version line removed. The version's only
#: job is ORDER (v1 → v2); the decision compares bytes. But editing the text
#: without a bump still silently changes what every provisioned repo re-reads in
#: every future session, and no other test can see it — the installer would
#: report "refreshed (same version, updated text)" and mean it. So the text is
#: pinned: edit it → update this pin AND bump AGENTS_BLOCK_VERSION, in one commit.
#: The version line is excluded so a bump alone does not red this pin.
BLOCK_PROSE_SHA256 = "d32104d5819bed5a77e6925379d27e05533c952bf0ecde6370fe9730c6067eea"


def _current_version() -> int:
    """Read AGENTS_BLOCK_VERSION from the installer — a legitimate bump must not
    red this suite for the wrong reason (cycle 2: the downgrade fixture was
    hard-coded to v3, so bumping to v3 made it a v3-onto-v3 "downgrade")."""
    m = re.search(r'^AGENTS_BLOCK_VERSION="(\d+)"', INSTALLER.read_text(encoding="utf-8"), re.M)
    assert m, "AGENTS_BLOCK_VERSION anchor not found — update this test"
    return int(m.group(1))


def _block_prose() -> str:
    src = INSTALLER.read_text(encoding="utf-8")
    head = "  printf '%s\\n' \\\n"
    start = src.index(head) + len(head)
    body = src[start:src.index('${me}" > "$block"')]
    assert body.startswith('"${ms}'), body[:80]
    lines = body.split("\n")[1:]          # drop the `"${ms}` line
    # Drop the VERSION line by POSITION — a substring filter also dropped any
    # later line that merely mentioned the marker, so adding such a line shipped
    # a text change past the pin with no bump (cycle 2, mutation d2).
    # In the SOURCE the line still holds the shell expansion, so match the shape.
    assert lines[0].startswith("<!-- tortoise:block v") and lines[0].endswith(" -->"), lines[:2]
    return "\n".join(lines[1:])


def test_block_text_edit_requires_a_version_bump():
    import hashlib

    got = hashlib.sha256(_block_prose().encode("utf-8")).hexdigest()
    assert got == BLOCK_PROSE_SHA256, (
        "the AGENTS.md block's TEXT changed. That text is re-read by every agent "
        "in every provisioned repo, so the change needs a VERSION BUMP to be "
        "reachable there — the installer only upgrades what it can compare.\n"
        f"  new sha256: {got}\n"
        "  -> bump AGENTS_BLOCK_VERSION, update BLOCK_PROSE_SHA256, and check the "
        "message examples in docs/quickstart-cloud.md + both served SKILL copies.")


# ── Pre-states that had no test, and were wrong because of it ────────────────


def test_indented_markers_are_refused_not_duplicated(tmp_path):
    """The mildest edit — a formatter indenting the marker lines — made the
    anchored detector see NO marker, so `none` APPENDED a second block: the stale
    copy stayed first and every later run compared the appended pair to itself
    and said "already current". A near-anchor marker is a MALFORMED marker."""
    legacy = LEGACY_BLOCK.replace(START, "  " + START).replace(END, "  " + END)
    done, agents = _install(tmp_path, FOREIGN_BEFORE + legacy)

    assert done.returncode == 3, (
        f"a refusal exits 3 (skills installed, block not upgraded), got {done.returncode}:\n{done.stdout}")
    assert "MALFORMED" in done.stderr, done.stderr
    assert agents.count("tortoise:start") == 1, f"the block was duplicated:\n{agents}"


def test_trailing_space_markers_are_refused_not_duplicated(tmp_path):
    legacy = LEGACY_BLOCK.replace(START, START + " ").replace(END, END + " ")
    done, agents = _install(tmp_path, legacy)

    assert done.returncode == 3, (
        f"a refusal exits 3 (skills installed, block not upgraded), got {done.returncode}:\n{done.stdout}")
    assert "MALFORMED" in done.stderr, done.stderr
    assert agents.count("tortoise:start") == 1, f"the block was duplicated:\n{agents}"


def test_an_existing_agents_file_without_markers_is_appended_once(tmp_path):
    """The common case: the project already HAS an AGENTS.md with its own rules."""
    done, agents = _install(tmp_path, FOREIGN_BEFORE)

    assert done.returncode == 0, done.stdout + done.stderr
    assert agents.startswith(FOREIGN_BEFORE)
    assert agents.count(START) == 1 and agents.count(END) == 1
    assert "created" in _agents_report(done.stdout)


def test_a_pure_line_ending_difference_does_not_claim_a_text_change(tmp_path):
    """CRLF file: a pure EOL difference must not be reported as an updated text
    (and must not be rewritten into mixed endings)."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    assert _run(installer, home, project).returncode == 0

    agents = (project / "AGENTS.md").read_text(encoding="utf-8")
    (project / "AGENTS.md").write_bytes(agents.replace("\n", "\r\n").encode())
    done = _run(installer, home, project)

    assert done.returncode == 0, done.stdout + done.stderr
    report = _agents_report(done.stdout)
    assert "updated text" not in report, (
        f"only the line endings differed, nothing was rewritten:\n{report}")
    assert "already current" in report, report


def test_a_numeric_version_that_does_not_compare_never_reads_as_current(tmp_path):
    """An out-of-range version made `-lt`/`-gt` error and fall through to the
    refresh branch: a DOWNGRADE announced as a same-version text update."""
    huge = "9" * 25
    agents_in = LEGACY_BLOCK.replace(START, f"{START}\n<!-- tortoise:block v{huge} -->")
    done, _agents = _install(tmp_path, agents_in)

    assert done.returncode == 0, done.stdout + done.stderr
    report = _agents_report(done.stdout)
    assert "already current" not in report, report
    assert "refreshed (v" not in report and "updated text" not in report, (
        f"a downgrade must never be reported as a text refresh:\n{report}")
    assert ("too large to compare" in report or "unreadable" in report), report


# ── Cycle 3: fail-closed under a `||` wrapper, near() precision, mangled markers


def test_an_unreadable_agents_file_fails_instead_of_reporting_success(tmp_path):
    """`emit … || { exit 3; }` means bash does NOT apply errexit inside the
    function, so an internal read failure used to be reported as a SUCCESSFUL
    upgrade over an untouched file — the #3429 harm, at exit 0."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    agents = project / "AGENTS.md"
    agents.write_text(LEGACY_BLOCK, encoding="utf-8")
    agents.chmod(0o000)

    try:
        done = _run(installer, home, project)
    finally:
        agents.chmod(0o600)

    if done.returncode == 0:
        # Running as root makes the file readable anyway; nothing to assert.
        import os as _os
        if _os.geteuid() == 0:
            return
    assert done.returncode != 0, (
        f"an unreadable AGENTS.md must fail, not report success:\n{done.stdout}")
    assert "AGENTS.md" in (done.stderr + done.stdout)
    # No success line at all: the failure is reported on stderr.
    assert "✓ AGENTS.md" not in done.stdout, done.stdout


def test_prose_mentioning_the_marker_is_not_a_marker(tmp_path):
    """near() must be PRECISE: quoting the marker in prose is documentation, not
    a block, and must neither refuse nor be treated as the block."""
    agents_in = (
        "- Our block delimiters are <!-- tortoise:start --> and <!-- tortoise:end -->.\n"
        "- Foreign rule that must survive.\n"
    )
    done, agents = _install(tmp_path, agents_in)

    assert done.returncode == 0, done.stdout + done.stderr
    assert agents.startswith(agents_in), agents[:200]
    # The prose keeps its mentions; what must appear exactly once is the block,
    # i.e. markers on their OWN line.
    own_line = [ln for ln in agents.splitlines() if ln in (START, END)]
    assert own_line == [START, END], agents
    assert "created" in _agents_report(done.stdout)


def test_a_marker_with_different_internal_spacing_is_refused(tmp_path):
    """`<!--  tortoise:start  -->` is a BROKEN marker, not prose: left as
    `none` it appends and leaves the stale block first."""
    legacy = LEGACY_BLOCK.replace(START, "<!--  tortoise:start  -->")
    done, agents = _install(tmp_path, FOREIGN_BEFORE + legacy)

    assert done.returncode == 3, f"must refuse, not append:\n{done.stdout}"
    assert "MALFORMED" in done.stderr, done.stderr
    assert "internal spacing" in done.stderr, done.stderr
    assert agents.count("tortoise:start") == 1, agents


def test_the_refusal_names_the_marker_counts(tmp_path):
    """The docs say it prints how many and which markers — pin the shape."""
    done, _agents = _install(tmp_path, LEGACY_BLOCK + "\n" + LEGACY_BLOCK)

    assert done.returncode == 3
    assert "start markers: 2" in done.stderr, done.stderr
    assert "end markers: 2" in done.stderr, done.stderr
    assert "ran twice" in done.stderr, done.stderr


def test_a_cr_inside_a_line_is_a_real_difference(tmp_path):
    """Only a CR immediately before the newline is an EOL difference. A CR in the
    MIDDLE of a line is content, so the block must not read as current."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    assert _run(installer, home, project).returncode == 0

    agents = (project / "AGENTS.md").read_text(encoding="utf-8")
    altered = agents.replace("agent memory", "agent\r memory", 1)
    assert altered != agents
    (project / "AGENTS.md").write_text(altered, encoding="utf-8")
    done = _run(installer, home, project)

    assert done.returncode == 0, done.stdout + done.stderr
    report = _agents_report(done.stdout)
    assert "already current" not in report, (
        f"a CR inside a line is content, not an EOL difference:\n{report}")
    assert "updated text" in report, report


# ── Cycle 4: fail-open comparison, fenced doc examples, non-numeric versions,
#    an unparsed state, and a planted staging symlink


def _install_with_stub(tmp_path: Path, stub: str, body: str, agents: str | None):
    """Run the REAL installer with one stubbed command first on PATH."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / stub).write_text(body, encoding="utf-8")
    (bindir / stub).chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    if agents is not None:
        (project / "AGENTS.md").write_text(agents, encoding="utf-8")
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    done = subprocess.run(
        ["bash", str(installer), "--harness", "codex"],
        cwd=str(project), env=env, capture_output=True, text=True, timeout=300)
    return done, (project / "AGENTS.md")


def test_a_failing_sed_cannot_read_as_already_current(tmp_path):
    """Process substitution that fails yields two EMPTY streams, so `cmp` says
    "equal" and a stale block is reported as current. The normalisation is done
    into real files and each step is checked."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    assert _run(installer, home, project).returncode == 0
    target = project / "AGENTS.md"
    # Drifted text AND CRLF, so the exact `cmp` misses and the normalisation path
    # is the only thing that can decide — which is what the stub breaks.
    target.write_bytes(
        target.read_text(encoding="utf-8")
        .replace("agent memory", "agent  memory").replace("\n", "\r\n").encode())

    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "sed").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    (bindir / "sed").chmod(0o755)
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    done = subprocess.run(["bash", str(installer), "--harness", "codex"],
                          cwd=str(project), env=env, capture_output=True, text=True, timeout=300)

    assert done.returncode != 0, f"a failed normalisation must not report success:\n{done.stdout}"
    # Scope to the AGENTS line: the SKILLS lines legitimately say "(already current)".
    assert "✓ AGENTS.md" not in done.stdout, done.stdout


def test_a_fenced_documentation_example_does_not_refuse_the_install(tmp_path):
    """A FENCE is an example, not content. Refused before: a repo that documents
    the markers in a fence was told its markers were malformed — and the remedy
    said to delete the example. A file with ONLY a fenced example has no block
    at all, so it must be created, not refused."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    example = "## How this file is structured\n\n```\n  " + START + "\n  ...\n  " + END + "\n```\n"
    agents = project / "AGENTS.md"
    agents.write_text("# House rules\n\n" + example, encoding="utf-8")

    done = _run(installer, home, project)

    assert done.returncode == 0, (
        f"a fenced EXAMPLE of the markers is documentation, not a broken block:\n{done.stderr}")
    text = agents.read_text(encoding="utf-8")
    assert text.count(START) == 2, "expected the example plus one real block"
    assert text.count(END) == 2, "the created block must be closed exactly once"
    assert example in text, "the example was eaten"
    assert "✓ AGENTS.md" in done.stdout, done.stdout

    # Second run: current, and the example is still not a second block.
    again = _run(installer, home, project)
    assert again.returncode == 0, again.stderr
    assert "already current" in _agents_report(again.stdout), again.stdout
    assert agents.read_text(encoding="utf-8") == text


def test_a_non_numeric_version_is_reported_as_such(tmp_path):
    """`v2-rc1` used to fall through to `unversioned` — a false statement about
    the file — and with the guard removed both comparisons ERROR and fall
    through to `refreshed (same version, updated text)`."""
    agents_in = LEGACY_BLOCK.replace(START, f"{START}\n<!-- tortoise:block v2-rc1 -->")
    done, agents = _install(tmp_path, agents_in)

    assert done.returncode == 0, done.stdout + done.stderr
    report = _agents_report(done.stdout)
    assert "not a number this installer can compare" in report, report
    assert "unversioned" not in report, report
    assert "updated text" not in report, report
    assert "integer expression expected" not in (done.stdout + done.stderr), (
        "a shell comparison must not be fed a non-number")


def test_an_unparsed_state_fails_instead_of_claiming_an_upgrade(tmp_path):
    """If the state awk prints nothing (crash), nothing must be written and no
    claim may be made — the earlier failure mode was `upgraded` at exit 0."""
    done, agents = _install_with_stub(tmp_path, "awk", "#!/bin/sh\nexit 0\n", LEGACY_BLOCK)

    assert done.returncode != 0, f"an unparsed state must fail:\n{done.stdout}"
    assert "✓ AGENTS.md" not in done.stdout, done.stdout
    if agents.exists():
        assert agents.read_text(encoding="utf-8") == LEGACY_BLOCK


def test_a_planted_staging_symlink_cannot_redirect_the_write(tmp_path):
    """#4327's threat model, applied to the block writer: a predictable
    `AGENTS.md.tmp` allowed a planted symlink to clobber an arbitrary file."""
    base = _fixture_tree(tmp_path)
    installer = _patched_installer(tmp_path, base)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    victim = project / "victim.txt"
    victim.write_text("ORIGINAL — must not change\n", encoding="utf-8")
    (project / "AGENTS.md").write_text(LEGACY_BLOCK, encoding="utf-8")
    (project / "AGENTS.md.tmp").symlink_to(victim)

    done = _run(installer, home, project)

    assert done.returncode == 0, done.stdout + done.stderr
    assert victim.read_text(encoding="utf-8") == "ORIGINAL — must not change\n", (
        "the write followed a planted symlink and clobbered an unrelated file")
    assert not (project / "AGENTS.md").is_symlink(), "AGENTS.md became a symlink"
    assert (project / "AGENTS.md").read_text(encoding="utf-8").count(START) == 1

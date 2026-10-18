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

Mutations that turn these RED (verified before merge):
  * delete the version line from the emitted block        -> the version
    assertions in every test fail (nothing to grep, no upgrade message);
  * drop the ``prev_version`` read (always empty)         -> the upgrade
    message becomes "created"/"upgraded (unversioned …)" for an already-current
    block, failing ``test_reinstall_reports_already_current``;
  * echo the old ``refreshed/created`` line unconditionally -> both upgrade
    tests fail on the message they require;
  * emit the block only on a FRESH file (skip the marker branch) -> the
    upgrade and idempotence tests fail on duplicate/no-version content.
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
    assert "created" in done.stdout, (
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
    assert "unversioned" in done.stdout, (
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
    assert "already current" in second.stdout, (
        f"a re-run over a current block must say so:\n{second.stdout}")
    assert "created" not in second.stdout and "upgraded" not in second.stdout

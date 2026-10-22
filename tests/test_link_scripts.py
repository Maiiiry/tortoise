"""Tests for tools/link_scripts.py (#9327).

The failure this covers, measured 2026-10-21 in a fresh worktree: the tracked
``scripts`` symlink names ONE host's absolute path
(``git cat-file -p HEAD:scripts``), so on any other host it dangles, and the
mandated rail — ``scripts/admin-merge.sh`` — does not resolve. A lane discovers
that only when it needs to merge.

Every case below is a filesystem fixture: no git, no network.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "link_scripts.py"


def _load():
    spec = importlib.util.spec_from_file_location("link_scripts_under_test", TOOL)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ls = _load()


@pytest.fixture()
def shared(tmp_path: Path) -> Path:
    """A stand-in for $AGENT_INFRA_PATH/scripts, holding the rail."""
    target = tmp_path / "agent-infra" / "scripts"
    target.mkdir(parents=True)
    rail = target / "admin-merge.sh"
    rail.write_text("#!/usr/bin/env bash\nexit 0\n")
    rail.chmod(0o755)
    return target


@pytest.fixture()
def checkout(tmp_path: Path) -> Path:
    d = tmp_path / "wt"
    d.mkdir()
    return d


def _run(checkout: Path, *args: str, env_target: Path | None = None,
         unset: bool = False) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("AGENT_INFRA_PATH", None)
    if not unset and env_target is not None:
        env["AGENT_INFRA_PATH"] = str(env_target.parent)
    return subprocess.run(
        [sys.executable, str(TOOL), "--checkout", str(checkout), *args],
        capture_output=True, text=True, env=env, check=False)


# ── the measured #9327 shape ────────────────────────────────────────────────

def test_a_dangling_committed_symlink_is_reported_as_an_unreachable_rail(
        checkout: Path, shared: Path):
    """THE REGRESSION. This is what `git cat-file -p HEAD:scripts` produced."""
    (checkout / "scripts").symlink_to("/Users/danielospina/Documents/GitHub/agent-infra/scripts")
    res = _run(checkout, env_target=shared)
    assert res.returncode == 1, res.stdout + res.stderr
    assert "DANGLING" in res.stdout
    assert "UNREACHABLE" in res.stdout


def test_apply_repairs_the_dangling_symlink_so_the_rail_resolves(
        checkout: Path, shared: Path):
    (checkout / "scripts").symlink_to("/nowhere/at/all/scripts")
    res = _run(checkout, "--apply", env_target=shared)
    assert res.returncode == 0, res.stdout + res.stderr
    assert os.readlink(checkout / "scripts") == str(shared)
    assert (checkout / "scripts" / "admin-merge.sh").is_file()
    assert os.access(checkout / "scripts" / "admin-merge.sh", os.X_OK)


def test_an_absent_symlink_is_reported_then_created_by_apply(checkout: Path, shared: Path):
    assert not (checkout / "scripts").exists()
    dry = _run(checkout, env_target=shared)
    assert dry.returncode == 1
    assert "NEEDED" in dry.stdout
    assert not (checkout / "scripts").exists(), "a dry run must not create the link"
    applied = _run(checkout, "--apply", env_target=shared)
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert (checkout / "scripts" / "admin-merge.sh").is_file()


def test_a_symlink_to_the_wrong_live_directory_is_repointed(checkout: Path, shared: Path,
                                                           tmp_path: Path):
    """A stale-but-resolvable target is still wrong: `scripts/` must be the SHARED root."""
    other = tmp_path / "elsewhere"
    other.mkdir()
    (checkout / "scripts").symlink_to(other)
    assert _run(checkout, env_target=shared).returncode == 1
    assert _run(checkout, "--apply", env_target=shared).returncode == 0
    assert os.readlink(checkout / "scripts") == str(shared)


def test_the_two_verdicts_are_independent(checkout: Path, tmp_path: Path):
    """A repaired symlink does NOT make the rail reachable (#9327's whole point).

    Measured on the fleet host: ``$AGENT_INFRA_PATH/scripts`` holds 16 helper
    scripts and **no** ``admin-merge.sh``. Inferring "rail reachable" from
    "symlink correct" would be the false pass this tool exists to refuse.
    """
    bare = tmp_path / "agent-infra" / "scripts"
    bare.mkdir(parents=True)
    (checkout / "scripts").symlink_to("/somewhere/stale")
    res = _run(checkout, "--apply", env_target=bare)
    assert res.returncode == 1, res.stdout + res.stderr
    assert os.readlink(checkout / "scripts") == str(bare)   # symlink repaired
    assert "UNREACHABLE" in res.stdout                       # rail still not there


# ── refusals (fail closed, nothing changed) ─────────────────────────────────

def test_a_real_scripts_directory_is_never_clobbered(checkout: Path, shared: Path):
    real = checkout / "scripts"
    real.mkdir()
    (real / "lane-local.sh").write_text("lane tree\n")
    res = _run(checkout, "--apply", env_target=shared)
    assert res.returncode == 2, res.stdout + res.stderr
    assert "REFUSED" in res.stdout
    assert not real.is_symlink()
    assert (real / "lane-local.sh").read_text() == "lane tree\n"


def test_a_regular_file_named_scripts_is_refused(checkout: Path, shared: Path):
    (checkout / "scripts").write_text("not a link\n")
    res = _run(checkout, "--apply", env_target=shared)
    assert res.returncode == 2, res.stdout + res.stderr
    assert (checkout / "scripts").read_text() == "not a link\n"


def test_an_unset_agent_infra_path_is_a_refusal_not_a_verdict(checkout: Path):
    res = _run(checkout, unset=True)
    assert res.returncode == 2, res.stdout + res.stderr
    assert "AGENT_INFRA_PATH" in res.stdout
    assert not (checkout / "scripts").exists()


def test_a_target_that_is_not_a_directory_is_a_refusal(checkout: Path, tmp_path: Path):
    """Never *create* a dangling link where the operator had none."""
    missing = tmp_path / "agent-infra" / "scripts"
    res = _run(checkout, "--apply", env_target=missing)
    assert res.returncode == 2, res.stdout + res.stderr
    assert not (checkout / "scripts").exists()


def test_an_absolute_checkout_is_required(tmp_path: Path, shared: Path):
    res = subprocess.run([sys.executable, str(TOOL), "--checkout", str(tmp_path / "nope")],
                         capture_output=True, text=True, check=False)
    assert res.returncode == 3


def test_rail_name_must_be_a_bare_filename(checkout: Path, shared: Path):
    res = _run(checkout, "--rail", "../etc/passwd", env_target=shared)
    assert res.returncode == 3


# ── idempotence / purity ────────────────────────────────────────────────────

def test_apply_is_idempotent_and_lands_on_the_same_target(checkout: Path, shared: Path):
    (checkout / "scripts").symlink_to("/nowhere")
    assert _run(checkout, "--apply", env_target=shared).returncode == 0
    before = os.readlink(checkout / "scripts")
    second = _run(checkout, "--apply", env_target=shared)
    assert second.returncode == 0, second.stdout + second.stderr
    assert os.readlink(checkout / "scripts") == before
    # No temp link is left behind in the checkout.
    assert sorted(p.name for p in checkout.iterdir()) == ["scripts"]


def test_a_dry_run_never_mutates_the_checkout(checkout: Path, shared: Path):
    (checkout / "scripts").symlink_to("/nowhere")
    _run(checkout, env_target=shared)
    assert os.readlink(checkout / "scripts") == "/nowhere"


def test_classify_reports_the_state_without_following_into_a_real_tree(checkout: Path):
    (checkout / "scripts").mkdir()
    info = ls.classify(checkout / "scripts")
    assert info["state"] == ls.STATE_REAL_DIR
    assert info["recorded"] is None


def test_json_verdict_carries_both_halves(checkout: Path, shared: Path):
    (checkout / "scripts").symlink_to("/nowhere")
    res = _run(checkout, "--apply", "--json", env_target=shared)
    payload = json.loads(res.stdout)
    assert payload["applied"] is True
    assert payload["rail"]["reachable"] is True
    assert payload["exit"] == 0

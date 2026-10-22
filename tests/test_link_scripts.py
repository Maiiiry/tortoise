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


def test_a_wrong_but_rail_working_symlink_is_not_a_clean_verdict(
        checkout: Path, shared: Path, tmp_path: Path):
    """#9564 review P2: exit 0 must mean ONE thing.

    A `scripts` link pointing at a stale-but-LIVE scripts directory that happens
    to CONTAIN the rail is still wrong (it is not the wanted shared root), yet it
    used to print `repair: NEEDED` and exit 0: "the shared root is in place" and
    "the rail is reachable" were two readings of one code. Exit 0 now requires
    both, so this shape is 1 with the repair still reported.
    """
    other = tmp_path / "stale-replica" / "scripts"
    other.mkdir(parents=True)
    rail = other / "admin-merge.sh"
    rail.write_text("#!/usr/bin/env bash\nexit 0\n")
    rail.chmod(0o755)
    (checkout / "scripts").symlink_to(other)

    dry = _run(checkout, env_target=shared)
    assert dry.returncode == 1, dry.stdout + dry.stderr
    assert "NEEDED" in dry.stdout
    assert "REACHABLE" in dry.stdout and "UNREACHABLE" not in dry.stdout
    # The link is untouched by the dry run — the verdict is a report, not an act.
    assert os.readlink(checkout / "scripts") == str(other)

    applied = _run(checkout, "--apply", env_target=shared)
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert os.readlink(checkout / "scripts") == str(shared)


def test_apply_refuses_to_trade_a_working_rail_for_a_rail_less_shared_root(
        checkout: Path, tmp_path: Path):
    """#9564 review P2: the repair itself can be the destructive direction.

    The fleet shape: ``$AGENT_INFRA_PATH/scripts`` holds the helper scripts and
    **no** ``admin-merge.sh`` (the rail lives in lane replicas). A lane whose
    ``scripts`` points at a rail-bearing replica is REACHABLE today, and a blind
    ``--apply`` re-points it at the shared root — taking the lane from "rail
    reachable" to "no sanctioned merge route", with the working target recorded
    only in stdout. ``resolve_target`` already refuses the cheaper version of
    this (creating a dangling link where there was none), so the expensive one
    is refused here too: exit 2, link and rail untouched, ``--force`` overrides.
    """
    replica = tmp_path / "lane-replica" / "scripts"
    replica.mkdir(parents=True)
    rail = replica / "admin-merge.sh"
    rail.write_text("#!/usr/bin/env bash\nexit 0\n")
    rail.chmod(0o755)
    (checkout / "scripts").symlink_to(replica)

    bare = tmp_path / "agent-infra" / "scripts"   # the shared root: no rail
    bare.mkdir(parents=True)

    # A dry run is a report: REACHABLE, with the repair still owed.
    dry = _run(checkout, env_target=bare)
    assert dry.returncode == 1, dry.stdout + dry.stderr
    assert "REACHABLE" in dry.stdout and "NEEDED" in dry.stdout

    refused = _run(checkout, "--apply", env_target=bare)
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert "REFUSED" in refused.stdout
    assert "REACHABLE to unreachable" in refused.stdout
    assert "--force" in refused.stdout
    # Nothing changed, and the rail is still there.
    assert os.readlink(checkout / "scripts") == str(replica)
    assert (checkout / "scripts" / "admin-merge.sh").is_file()

    # The operator can still take that decision deliberately.
    forced = _run(checkout, "--apply", "--force", env_target=bare)
    assert forced.returncode == 1, forced.stdout + forced.stderr
    assert "APPLIED" in forced.stdout
    assert os.readlink(checkout / "scripts") == str(bare)
    assert "UNREACHABLE" in forced.stdout


def test_a_real_scripts_directory_can_still_be_a_reachable_verdict(
        checkout: Path, shared: Path):
    """#9564 review P2: a legitimate lane tree must not be a REFUSAL on a dry run.

    A hand-built `scripts/` directory is legitimate (the tool never clobbers it).
    The unconditional refusal made a plain read-only run over a lane tree that
    DOES contain the rail report REFUSED / exit 2 — a repair nobody asked for
    deciding the verdict of the primary question. On a dry run the rail verdict
    alone decides; only `--apply` refuses.
    """
    real = checkout / "scripts"
    real.mkdir()
    rail = real / "admin-merge.sh"
    rail.write_text("#!/usr/bin/env bash\nexit 0\n")
    rail.chmod(0o755)

    dry = _run(checkout, env_target=shared)
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert "REACHABLE" in dry.stdout and "UNREACHABLE" not in dry.stdout
    assert "REFUSED" not in dry.stdout
    assert "NOT OURS TO DO" in dry.stdout
    assert not real.is_symlink()

    # Without the rail it is a plain NOT-reachable verdict (1), still not 2.
    rail.unlink()
    assert _run(checkout, env_target=shared).returncode == 1

    # `--apply` still refuses to replace it — that half is unchanged.
    refused = _run(checkout, "--apply", env_target=shared)
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert "REFUSED" in refused.stdout
    assert not real.is_symlink()


def test_a_real_scripts_directory_is_never_clobbered_a_dry_run_does_nothing(
        checkout: Path, shared: Path):
    """A dry run over a lane tree is mutation-free (no temp link, no repoint)."""
    real = checkout / "scripts"
    real.mkdir()
    (real / "lane-local.sh").write_text("lane tree\n")
    _run(checkout, env_target=shared)
    assert sorted(p.name for p in checkout.iterdir()) == ["scripts"]
    assert sorted(p.name for p in real.iterdir()) == ["lane-local.sh"]


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


def test_a_nonexistent_checkout_is_a_usage_error(tmp_path: Path, shared: Path):
    """P3 (#9564 round 2): the rule is "checkout must exist", not "must be absolute".

    ``main()`` normalises with ``Path(...).absolute()`` and only rejects a path
    that is not a directory, so a RELATIVE existing checkout is accepted; naming
    this `test_an_absolute_checkout_is_required` asserted a contract the tool
    does not have.
    """
    res = subprocess.run([sys.executable, str(TOOL), "--checkout", str(tmp_path / "nope")],
                         capture_output=True, text=True, check=False)
    assert res.returncode == 3
    # A relative checkout that DOES exist is accepted (the tool makes it absolute).
    (tmp_path / "wt").mkdir()
    rel = subprocess.run([sys.executable, str(TOOL), "--checkout", "wt",
                          "--target", str(shared)], cwd=str(tmp_path),
                         capture_output=True, text=True, check=False)
    assert rel.returncode == 1, rel.stdout + rel.stderr   # absent link, rail owed
    assert "scripts: absent" in rel.stdout


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


def test_apply_warns_that_the_repair_must_not_be_committed(checkout: Path, shared: Path):
    """#9564 review P3: `scripts` is a TRACKED symlink, so a repair is host-local.

    Committing the repaired link re-writes the blob to this host's absolute path
    — the #9327 bug, re-created for every other host while looking like a fix.
    The action site must say so (and so must AGENTS.md's `scripts/` row).
    """
    (checkout / "scripts").symlink_to("/nowhere")
    res = _run(checkout, "--apply", env_target=shared)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "TRACKED symlink" in res.stdout
    assert "Do NOT commit it" in res.stdout
    assert "#9327" in res.stdout


def test_json_verdict_carries_both_halves(checkout: Path, shared: Path):
    (checkout / "scripts").symlink_to("/nowhere")
    res = _run(checkout, "--apply", "--json", env_target=shared)
    payload = json.loads(res.stdout)
    assert payload["applied"] is True
    assert payload["repair_needed"] is False
    assert payload["repairable"] is True
    assert payload["rail"]["reachable"] is True
    # The pre-action rail state is reported too (#9564 review P2): the repair
    # decision is not made from the target alone.
    assert payload["rail_before"]["reachable"] is False
    assert payload["exit"] == 0


def test_json_reports_the_unrepairable_state_without_claiming_a_refusal(
        checkout: Path, shared: Path):
    """#9564 review P2: `repairable`/`repair_note` are the documented dimension."""
    real = checkout / "scripts"
    real.mkdir()
    payload = json.loads(_run(checkout, "--json", env_target=shared).stdout)
    assert payload["repairable"] is False
    assert payload["repair_note"] and "refusing to replace it" in payload["repair_note"]
    assert payload["refusal"] is None
    assert payload["exit"] == 1        # no rail in the lane tree
    assert payload["repair_needed"] is False

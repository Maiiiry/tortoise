"""The e2e `Pre-warm wrangler toolchain` steps stay pinned AND retry-protected.

WHY THIS EXISTS
---------------
On 2026-10-15 the required `legal-e2e` check went red before it ran a single
assertion: the pre-warm step's first `npx --yes wrangler@4 --version` died with

    npm error network Invalid response body while trying to fetch
    https://registry.npmjs.org/wrangler: read ECONNRESET

a transient npm-registry reset on a cold runner. The step had no retry, so the
flakiest part of the job — a network fetch — was also its least protected, and
the retry that DOES exist lives in the boot loop below, which the failed
pre-warm never reached. A cold-runner fetch is exactly the thing a bounded retry
is for, so the step now retries (3 attempts, best-effort: the boot loop is the
real gate and surfaces wrangler's own log).

The pre-warm also floated `wrangler@4` while every boot step pins `4.127.0`, so
it warmed a DIFFERENT tarball than the one the boot actually fetched — the
agent-infra#414 drift class, and it also made npm download the whole packument
to resolve the range. The two versions must agree.

WHAT THIS PINS (a gate is worth what its wiring is worth — the #4311 convention)
  * every `Pre-warm wrangler toolchain` step pins an EXACT version
    (`wrangler@X.Y.Z`), never a floating `wrangler@4`;
  * the pinned version matches the version the SAME job's boot step starts, so
    the warm-up warms the tarball that runs;
  * the step is bounded-retry-protected (`for attempt in 1 2 3`);
  * the three e2e jobs that serve `website/` still HAVE the step — deleting it
    would silently push the cold fetch back onto the boot budget that #2178
    added it to protect.

This is parsed YAML, not a raw-text scan: the step's `run` string is read from
the loaded document, so a commented-out or renamed step cannot satisfy it.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"

PREWARM_STEP = "Pre-warm wrangler toolchain"

#: Captures the WHOLE version token after `wrangler@` (`4`, `4.127`, `4.127.0`).
#: A token is classified by its dot count, never by a lookahead that backtracks
#: mid-token — a lookahead happily matched `4.12` inside `4.127.0`.
WRANGLER_REF = re.compile(r"wrangler@(\d+(?:\.\d+)*)")


def _exact_wranglers(run: str) -> set[str]:
    """An EXACT pin (`4.127.0`) — the version that actually boots."""
    return {v for v in WRANGLER_REF.findall(run) if v.count(".") == 2}


def _floating_wranglers(run: str) -> set[str]:
    """A range (`4`, `4.127`) — never acceptable in these e2e steps."""
    return {v for v in WRANGLER_REF.findall(run) if v.count(".") != 2}

#: The e2e jobs that serve this repo's `website/` (marketing + dashboard) and so
#: share the pre-warm. A rename shows up here as a failure, not as silent loss.
PREWARM_JOBS = frozenset({"welcome-e2e", "legal-e2e", "dashboard-e2e"})


def _jobs() -> dict:
    return yaml.safe_load(CI.read_text())["jobs"]


def _steps() -> list[tuple[str, dict]]:
    return [(job, step)
            for job, spec in _jobs().items()
            for step in (spec.get("steps") or [])]


def _prewarm_steps() -> list[tuple[str, dict]]:
    return [(job, step) for job, step in _steps()
            if step.get("name") == PREWARM_STEP]


def test_the_three_website_e2e_jobs_still_prewarm() -> None:
    """Deleting the step re-exposes the boot budget #2178 added it to protect."""
    assert {job for job, _ in _prewarm_steps()} == PREWARM_JOBS


def test_every_prewarm_pins_an_exact_wrangler_and_never_floats() -> None:
    steps = _prewarm_steps()
    assert steps, "no pre-warm step found — the guard has nothing to check"
    for job, step in steps:
        run = step.get("run") or ""
        assert _exact_wranglers(run), (
            f"{job}: pre-warm does not pin an exact `wrangler@X.Y.Z` — a "
            f"floating range warms a different tarball than the boot step. run={run!r}")
        assert not _floating_wranglers(run), (
            f"{job}: pre-warm still floats a wrangler range — {sorted(_floating_wranglers(run))}")


def test_every_prewarm_retries_the_registry_fetch() -> None:
    """A single ECONNRESET reded the required legal-e2e check (2026-10-15)."""
    for job, step in _prewarm_steps():
        run = step.get("run") or ""
        assert "for attempt in 1 2 3" in run, (
            f"{job}: pre-warm is not bounded-retry-protected. run={run!r}")


def test_prewarm_pins_the_same_version_the_job_boots() -> None:
    """Warming `@4` while booting `@4.127.0` misses the cold fetch entirely."""
    for job, step in _prewarm_steps():
        warm = _exact_wranglers(step.get("run") or "")
        boots: set[str] = set()
        for other_job, other in _steps():
            if other_job != job or other.get("name") == PREWARM_STEP:
                continue
            boots.update(_exact_wranglers(other.get("run") or ""))
        assert boots, f"{job}: no other step pins an exact wrangler version to compare against"
        assert warm <= boots, (
            f"{job}: pre-warm pins {sorted(warm)} but the job boots {sorted(boots)}")


def test_no_e2e_boot_step_floats_the_wrangler_version() -> None:
    """The dashboard boot steps floated `@4` while the pre-warm pinned 4.127.0
    — the drift class the parity check above exists to catch."""
    for job in sorted(PREWARM_JOBS):
        for other_job, other in _steps():
            if other_job != job:
                continue
            floats = _floating_wranglers(other.get("run") or "")
            assert not floats, (
                f"{job}: step {other.get('name')!r} floats wrangler {sorted(floats)}")

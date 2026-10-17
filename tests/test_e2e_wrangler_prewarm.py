"""The wrangler pin is one artefact, ASSERTED against what actually resolved (#8816).

The pin used to be a string repeated in twelve `run:` blocks, and the guard that
"checked" it read that text. Reading text cannot see a spec written as
`wran\\gler@4`, `"wrangler"@4` or `wrangler$X@4`, cannot see a bare `wrangler`,
and never looked at `deploy-pages.yml` at all — where `wrangler@4` floated in
production. Two measurements decided the replacement (bash 5, node 22):

  * `wrangler --version` prints the bare resolved version on stdout;
  * a float declaration can never equal its own resolution — `wrangler@4`
    resolves to 4.151.1, so `[ "$got" = "$WRANGLER_VERSION" ]` fails.

So the version now lives in ONE file, `config/wrangler-version`, and the lane
ASSERTS the version it got against it. That is exact for every spelling,
including the ones no text scan can see, because it compares an observation to a
declaration. The boot steps then run bare `wrangler`, so there is no spec at the
call site to float, and the deploy steps assert before they upload.

What is left for THIS file is the shape that keeps those properties true:

  1. one artefact, full `X.Y.Z`, and no second copy of the value in any workflow;
  2. no workflow declares the version in `env:` (the spelling that would shadow
     nothing and let a consuming step act on a different version than the one
     verified) and no inline assignment other than the file read;
  3. every wrangler spec is the asserted `@${WRANGLER_VERSION:?...}` form;
  4. every e2e job has an unconditional install-and-VERIFY step;
  5. boot steps carry no spec and keep their retry;
  6. every deploy step asserts before it uploads;
  7. every job that installs wrangler pins node 22 — `npm install` only WARNS
     (`EBADENGINE`) on an engines mismatch, so a node-20 job would install
     "successfully" and fail later, inside the readiness loop.

The removed machinery is the point, not an accident: with no spec at the call
site there is no shell word-splitting to model. A residual remains and is stated
rather than hidden — a NEW step can always be written with `wrangler@4`; arms 2-3
refuse that text in every workflow file, and the assertion covers the binary that
actually runs.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
VERSION_FILE = REPO_ROOT / "config" / "wrangler-version"
VERSION_SOURCE = "config/wrangler-version"

PREWARM_JOBS = frozenset({"welcome-e2e", "legal-e2e", "dashboard-e2e"})
VERIFY_STEP = "Install and verify the wrangler toolchain (#8816)"
BOOT_STEP_PREFIX = "Start wrangler pages dev preview"
DEPLOY_STEP_PREFIX = "Deploy to Cloudflare Pages"

EXACT_VERSION = re.compile(r"\d+\.\d+\.\d+")

#: The only legal spelling of a wrangler spec: the declared variable, with `:?`
#: so an empty declaration aborts instead of expanding to `wrangler@` (= latest).
LEGAL_SPEC = re.compile(r"wrangler@\$\{WRANGLER_VERSION:\?")

#: The only legal way the variable may be SET.
DECLARATION = 'WRANGLER_VERSION="$(cat config/wrangler-version)"'

#: Tokens that may legitimately accompany `${WRANGLER_VERSION…}` on a line: the
#: `:?` guard, an equality comparison, or a diagnostic. Anything else — notably
#: an inline `WRANGLER_VERSION="4.151.1"` — is an offender.
LEGAL_TOKENS = (
    "${WRANGLER_VERSION:?",
    '= "$WRANGLER_VERSION"',
    '!= "$WRANGLER_VERSION"',
    "declared ${WRANGLER_VERSION}",
    "wrangler ${WRANGLER_VERSION}",
)



#: `if:` values that are false by construction: the boolean, the integer 0, and
#: their `${{ }}` spellings. `str(gate)` alone cannot tell `0` from `"0"`.
_CONSTANT_FALSE = re.compile(
    r"^(?:\$\{\{\s*)?(?:false|0(?:\.0*)?)(?:\s*\}\})?$", re.IGNORECASE
)


def _is_constant_false(gate: object) -> bool:
    if isinstance(gate, bool):
        return not gate
    if isinstance(gate, (int, float)):
        return not gate
    return bool(_CONSTANT_FALSE.match(str(gate).strip()))

def _workflow_paths() -> list:
    paths = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
    assert paths, "no workflows found — the repo root probe is wrong"
    return paths


def _load(path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _declared_version() -> str:
    return VERSION_FILE.read_text(encoding="utf-8").strip()


def _all_steps(jobs: dict) -> list[tuple[str, dict]]:
    return [
        (name, step)
        for name, job in jobs.items()
        for step in (job.get("steps") or [])
    ]


def _run(step: dict) -> str:
    return step.get("run") or ""


def _steps_named(name: str) -> list[tuple[str, dict]]:
    out = []
    for path in _workflow_paths():
        for job_name, step in _all_steps(_load(path).get("jobs") or {}):
            if (step.get("name") or "") == name:
                out.append((job_name, step))
    return out


# ---------------------------------------------------------------- 1. one artefact


def test_the_version_lives_in_one_artefact_and_is_a_full_version() -> None:
    assert VERSION_FILE.is_file(), f"{VERSION_SOURCE} is missing — the pin has no home"
    declared = _declared_version()
    assert EXACT_VERSION.fullmatch(declared), (
        f"{VERSION_SOURCE} holds {declared!r}, which is not a full X.Y.Z — a partial "
        "version resolves to whatever npm serves, which is the drift this replaces"
    )
    for path in _workflow_paths():
        text = path.read_text(encoding="utf-8")
        assert declared not in text, (
            f"{path.name} restates the pinned version verbatim ({declared}); it must read "
            f"{VERSION_SOURCE} instead, or a bump edits one place and drifts in another"
        )


def test_no_workflow_declares_the_version_as_an_env_key() -> None:
    """`env: {WRANGLER_VERSION: 4.151.1}` is the spelling a text scan cannot see."""

    def walk(node, where):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "env" and isinstance(value, dict):
                    assert "WRANGLER_VERSION" not in value, (
                        f"{where}: `env` sets WRANGLER_VERSION={value['WRANGLER_VERSION']!r}. "
                        "It must be read from "
                        f"{VERSION_SOURCE} in the step that uses it — an env declaration "
                        "lets a consuming step act on a different version than the one "
                        "the verify step asserted"
                    )
                walk(value, f"{where}.{key}")
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{where}[{i}]")

    for path in _workflow_paths():
        walk(_load(path), path.name)


def test_every_version_mention_is_a_read_or_an_asserted_spec() -> None:
    offenders = []
    for path in _workflow_paths():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "WRANGLER_VERSION" not in line:
                continue
            if DECLARATION in line:
                continue
            if any(token in line for token in LEGAL_TOKENS):
                continue
            offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, "WRANGLER_VERSION used outside the declared shapes:\n" + "\n".join(
        offenders
    )


def test_every_wrangler_spec_is_the_asserted_form() -> None:
    offenders = []
    for path in _workflow_paths():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0] if line.lstrip().startswith("#") else line
            for match in re.finditer(r"wrangler@\S*", code):
                token = match.group(0)
                if LEGAL_SPEC.match(token):
                    continue
                # A bare `wrangler@` followed by prose punctuation is a mention,
                # not a spec — anything shaped like a spec (digit, letter, `^`,
                # `~`, `$`, quote, backslash, `{`) is an offender.
                if len(token) > len("wrangler@") and token[len("wrangler@")] in "`).,;:":
                    continue
                offenders.append(f"{path.name}:{lineno}: {token}")
    assert not offenders, (
        "wrangler spec is not the asserted variable form (a literal spec cannot be "
        "verified against what resolved):\n" + "\n".join(offenders)
    )


def test_each_workflow_reads_the_version_at_least_once() -> None:
    for path in _workflow_paths():
        text = path.read_text(encoding="utf-8")
        if "wrangler@" not in text:
            continue
        assert DECLARATION in text, (
            f"{path.name} uses a wrangler spec but never reads {VERSION_SOURCE}"
        )


# ------------------------------------------------- 2. the lane asserts, per job


def test_the_three_website_e2e_jobs_install_and_verify() -> None:
    found = _steps_named(VERIFY_STEP)
    jobs = {job for job, _step in found}
    assert jobs >= PREWARM_JOBS, (
        f"jobs without a {VERIFY_STEP!r} step: {sorted(PREWARM_JOBS - jobs)} — the pin is "
        "unverified there, so any spec that resolves elsewhere runs unnoticed"
    )
    for job, step in found:
        run = _run(step)
        assert DECLARATION in run, f"{job}: the verify step does not read {VERSION_SOURCE}"
        assert "npm install -g" in run, f"{job}: the verify step does not install"
        assert "--version" in run, f"{job}: the verify step never asks what resolved"
        assert '"$WRANGLER_VERSION"' in run, (
            f"{job}: the verify step installs/comares something other than the declared value"
        )
        # The retry must be the REAL three-attempt loop, as CODE. An optional-group
        # regex (`(?:1 )?2(?: 3)?`) also matched a one-attempt `for attempt in 2;`,
        # a two-attempt `for attempt in 1 2;`, and a commented-out
        # `# was: for attempt in 1 2 3;` — a guard satisfiable by exactly the
        # degenerations it exists to reject, so it asserted nothing.
        code = "\n".join(
            ln for ln in run.splitlines() if not ln.lstrip().startswith("#")
        )
        assert "for attempt in 1 2 3;" in code, (
            f"{job}: the install lost its three-attempt retry loop — one npm-registry "
            "ECONNRESET (2026-10-15) reded the required legal-e2e check when the "
            "fetch had none"
        )


def test_the_verify_step_precedes_every_boot_in_the_same_job() -> None:
    """The boot must run the binary the verify step proved — asserted as an ORDER.

    ``test_the_three_website_e2e_jobs_install_and_verify`` proves a verify step
    EXISTS in each job, and ``test_boot_steps_carry_no_spec_and_keep_their_retry``
    proves the boot carries no spec. Neither asserts that the verify runs FIRST:
    with the order unasserted, moving the verify after the boot leaves the boot
    running whatever global ``wrangler`` happens to be on PATH, unverified, while
    every test in this file stays green.
    """
    verify_at: dict[str, list[int]] = {}
    boot_at: dict[str, list[int]] = {}
    steps = _all_steps(_load(WORKFLOWS / "ci.yml").get("jobs") or {})
    for idx, (job, step) in enumerate(steps):
        name = step.get("name") or ""
        if name == VERIFY_STEP:
            verify_at.setdefault(job, []).append(idx)
        elif name.startswith(BOOT_STEP_PREFIX):
            boot_at.setdefault(job, []).append(idx)

    for job in sorted(PREWARM_JOBS):
        verifies = verify_at.get(job) or []
        assert verifies, f"{job}: no {VERIFY_STEP!r} step at all"
        for boot in boot_at.get(job) or []:
            assert any(v < boot for v in verifies), (
                f"{job}: the boot at step {boot} runs BEFORE every verify step "
                f"{verifies} — the boot would use whatever wrangler is on PATH, "
                "which nothing asserted"
            )


def test_the_verify_step_is_unconditional() -> None:
    for job, step in _steps_named(VERIFY_STEP):
        assert "if" not in step, (
            f"{job}: the install-and-verify step is gated by `if: {step['if']}` — a gate "
            "that skips it leaves the pin unverified while the run is green"
        )
        job_cfg = _load(WORKFLOWS / "ci.yml")["jobs"][job]
        gate = job_cfg.get("if")
        if gate is not None:
            assert not _is_constant_false(gate), (
                f"{job}: job-level `if: {gate!r}` is constant-false — the whole job, "
                "including the verify, can never run"
            )


def test_the_version_check_is_an_equality_against_the_declared_value() -> None:
    """The assertion must compare, not merely print — a print cannot fail a job."""

    for job, step in _steps_named(VERIFY_STEP):
        run = _run(step)
        assert re.search(r'\[ "\$got" = "\$WRANGLER_VERSION" \]', run), (
            f"{job}: no `[ \"$got\" = \"$WRANGLER_VERSION\" ]` comparison — the resolved "
            "version is not asserted"
        )
        assert "exit 1" in run, f"{job}: a mismatch would not fail the step"


# ------------------------------------------------------ 3. boot and deploy shape


def test_boot_steps_carry_no_spec_and_keep_their_retry() -> None:
    boots = [
        (job, step)
        for job, step in _all_steps(_load(WORKFLOWS / "ci.yml")["jobs"])
        if (step.get("name") or "").startswith(BOOT_STEP_PREFIX)
    ]
    assert len(boots) == 5, f"expected 5 boot steps, found {len(boots)}"
    for job, step in boots:
        run = _run(step)
        assert "wrangler pages dev" in run, f"{job}: boot does not start wrangler"
        assert "wrangler@" not in run, (
            f"{job}: the boot carries a spec — it must run the binary the verify step "
            "asserted, so that a spec has nowhere to float"
        )
        assert re.search(r"for attempt in 1 2;", run), (
            f"{job}: the boot lost its two-attempt retry"
        )


def test_each_deploy_step_asserts_before_it_uploads() -> None:
    deploys = [
        (job, step)
        for job, step in _all_steps(_load(WORKFLOWS / "deploy-pages.yml")["jobs"])
        if (step.get("name") or "").startswith(DEPLOY_STEP_PREFIX)
    ]
    assert len(deploys) == 2, f"expected 2 deploy steps, found {len(deploys)}"
    for job, step in deploys:
        run = _run(step)
        assert DECLARATION in run, f"{job}: the deploy step does not read {VERSION_SOURCE}"
        assert "--version" in run, f"{job}: the deploy step never asserts what resolved"
        assert run.index("--version") < run.index("pages deploy"), (
            f"{job}: `pages deploy` runs before the version is asserted — the upload "
            "would go out on an unverified spec"
        )
        assert '"$WRANGLER_VERSION"' in run, (
            f"{job}: the assertion compares something other than the value being used"
        )


# ------------------------------------------------------------- 4. node floor, derived


def test_every_job_that_installs_wrangler_pins_node_22() -> None:
    """Derived, not a list: a new wrangler job joins the arm automatically."""

    offenders = []
    checked = 0
    for path in _workflow_paths():
        for job_name, job in (_load(path).get("jobs") or {}).items():
            steps = job.get("steps") or []
            installs = any(
                "wrangler@" in _run(step) or "npm install -g wrangler" in _run(step)
                for step in steps
            )
            if not installs:
                continue
            checked += 1
            node = None
            for step in steps:
                uses = step.get("uses") or ""
                if uses.startswith("actions/setup-node"):
                    node = str((step.get("with") or {}).get("node-version", ""))
            # No `setup-node` at all is an offender, not a crash: the job would
            # run whatever node the image ships, and `npm install` only WARNS on
            # an engines mismatch.
            if "22" not in (node or ""):
                offenders.append(f"{path.name}:{job_name} node-version={node!r}")
    assert checked >= 4, f"only {checked} wrangler-installing jobs found — the probe is wrong"
    assert not offenders, (
        "a job installs wrangler without node 22; `npm install` only WARNS on an engines "
        "mismatch, so it would 'succeed' and break later in the readiness loop:\n"
        + "\n".join(offenders)
    )


# ------------------------------------------------ 5. the manual deploy script


def test_the_manual_deploy_script_pins_the_same_version() -> None:
    """`website/apps/dashboard/deploy.sh` is the documented manual deploy.

    It is outside the workflows, so no other arm reaches it — and a bare
    `npx wrangler pages deploy` there ships whatever npm serves, with nothing
    asserting what that was. It reads the same artefact.
    """

    script = REPO_ROOT / "website" / "apps" / "dashboard" / "deploy.sh"
    assert script.is_file(), f"{script} is missing"
    text = script.read_text(encoding="utf-8")
    code = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    # The read must go through an ABSOLUTE script dir resolved BEFORE any `cd`.
    # This arm used to pin the literal `cat "$(dirname "$0")/…config/wrangler-version"`,
    # which pinned the BUG: after `cd "$(dirname "$0")"`, a RELATIVE `$0` makes that
    # path double-apply the prefix, so the documented
    # `bash website/apps/dashboard/deploy.sh` aborted with "No such file or
    # directory" — and no arm could see it, because the arm asserted the broken
    # text verbatim (#8816 review).
    assert 'SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"' in code, (
        "the manual deploy does not resolve an ABSOLUTE script dir — a relative `$0` "
        "re-derived after `cd` points at a path that does not exist"
    )
    assert 'cat "$SCRIPT_DIR/../../../config/wrangler-version"' in code, (
        "the manual deploy does not read config/wrangler-version through SCRIPT_DIR — "
        "it would deploy an unasserted wrangler"
    )
    assert 'cat "$(dirname "$0")/../../../config/wrangler-version"' not in code, (
        "the relative-`$0` read is back: after `cd`, it double-applies the prefix and "
        "the documented manual deploy aborts"
    )
    assert "--version" in code, "the manual deploy never asserts what resolved"
    for match in re.finditer(r"wrangler@\S*", code):
        assert LEGAL_SPEC.match(match.group(0)), (
            f"manual deploy spec {match.group(0)!r} is not the asserted variable form"
        )

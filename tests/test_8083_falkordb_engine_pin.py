"""#8083: no FalkorDB lane/test default in this repo may float, and the pinned
engine must not be the v6 core that a READ-ONLY aggregate query kills.

The defect
----------
On the FalkorDB v6 core, ``count(*)`` in the same ``RETURN`` list as a property
that is ABSENT from every matched row panics the module
(``src/reply.rs:655`` — ``Option::unwrap()`` on ``None``) and the server EXITS 1,
taking every graph in that container with it. The trigger is the PAIR; either
half alone is fine (measured 2026-10-18, see below).

Measured, one fresh container per image, one
``CREATE (:Source {url:'a', sourceKind:'document'})`` first, then the query:

===============================  =========  ===========================
image                            graph ver  ``MATCH (s:Source) RETURN
                                            s.sourceKind, s.kind, count(*)``
===============================  =========  ===========================
``falkordb/falkordb:latest``     60002      ``Error: Server closed the
                                            connection``; ``exited exit=1``
``falkordb/falkordb:6.0.1``      60001      same — ``exited exit=1``
``falkordb/falkordb-server:``    42006      row ``document | (null) | 1``;
``  v4.20.6`` (the CI pin)                  container still ``running``
===============================  =========  ===========================

Why this file exists
--------------------
The v6 image was the DEFAULT of every local tool that starts a FalkorDB for a
lane or a test, so a lane could be destroyed by an ad-hoc read-only checkpoint
query and the crash would read as a diff regression — measured on the T11 index
smoke (#1037), which lost its first 5,411-file cold run that way. The fix in
#8083 is a PIN, so this file holds two things the pin cannot hold by itself:

  * the four defaulting sites and the CI action's own ``image`` default must all
    name the SAME non-floating engine (a literal repeated four times has no way
    to notice it drifted), and
  * a FLOATING tag that arrives through ``TORTOISE_TEST_LANE_IMAGE`` is
    ANNOUNCED on stderr before the container starts — the seam stays, the
    silence does not.

The live test at the bottom is the only one that talks to Docker. It SKIPS
(never fails) when the CLI or the pinned image is unavailable, so this file
costs a lane nothing while still executing the reproducer wherever the image is
already present — a guard that can only be run by hand is not a guard.
"""
from __future__ import annotations

import ast
import importlib.util
import inspect
import os
import re
import shutil
import subprocess
import sys
import time
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import edge_census  # noqa: E402
from tools import test_lane as tl  # noqa: E402

ACTION = ROOT / ".github" / "actions" / "falkordb-provision" / "action.yml"
TEST_LANE = ROOT / "tools" / "test_lane.py"
EDGE_CENSUS = ROOT / "tools" / "edge_census.py"
GRAPH_SCRIPTS_SETUP = ROOT / "graph-scripts" / "setup.py"
GRAPH_VIZ_COMPOSE = ROOT / "apps" / "graph-viz" / "docker-compose.yml"

#: The reproducer, verbatim from the issue. `kind` is deliberately a property
#: absent from every `Source` row; any name not set on the matched rows behaves
#: the same.
PANICKING_QUERY = "MATCH (s:Source) RETURN s.sourceKind, s.kind, count(*)"

#: An image REFERENCE (registry/repo[:tag]) that names FalkorDB. Bare
#: ``falkordb`` is a USERNAME in this repo (ACL tests, ``docker://:falkordb@``),
#: so a reference is only a reference when it carries a path separator.
_IMAGE_REF = re.compile(r"^(?:[\w.-]+/)+[\w.-]+(?:[\w.-]+)?(?::[\w.-]+)?$")


def _tag(ref: str) -> str:
    """The tag of an image reference — the last path component's suffix."""
    last = ref.rsplit("/", 1)[-1]
    return last.rsplit(":", 1)[1] if ":" in last else ""


def _floating(ref: str) -> bool:
    """True when nothing pins what this reference resolves to."""
    return _tag(ref) in ("", "latest")


def _ci_pin() -> str:
    """The ``image`` input's default in the CI provisioning action.

    This is the engine the repo actually TESTS against, so it is the value every
    local tool must agree with. Read from the action rather than restated here:
    a restated pin is a second place to drift.
    """
    text = ACTION.read_text()
    # The `image:` input block, up to the next input at the same indent.
    start = text.index("\n  image:\n")
    block = text[start:]
    end = block.find("\n  legacy:")
    assert end > 0, "the `image` input block could not be delimited in the action"
    found = re.search(r"^\s+default:\s*(\S+)\s*$", block[:end], re.M)
    assert found, "the `image` input has no `default:` in the action"
    return found.group(1)


# ── the pin is one value, everywhere ───────────────────────────────────────


def test_the_ci_action_pin_is_not_floating():
    pin = _ci_pin()
    assert not _floating(pin), (
        f"the CI provisioning action defaults to {pin!r}, a FLOATING tag — what "
        f"a lane tests against would change between runs (#8083)"
    )


def test_the_ci_pin_is_not_the_v6_core_that_panics():
    """A tripwire, not a taste: v6 is where the read-only aggregate kills the
    server. Move this assertion ONLY together with a measurement that the
    reproducer above no longer exits the server on that engine — otherwise the
    whole pin is pointless and every lane is one checkpoint query from losing
    its graphs."""
    pin = _ci_pin()
    major = _tag(pin).lstrip("v").split(".", 1)[0]
    assert major.isdigit(), f"cannot read an engine major from {pin!r}"
    assert int(major) < 6, (
        f"the CI pin is {pin!r} (engine v{major}) — the v6 core exits the "
        f"server on {PANICKING_QUERY!r} (#8083). Re-measure before pinning "
        f"lanes to it."
    )


def test_the_lane_tool_default_is_the_ci_pin():
    assert tl.PINNED_IMAGE == _ci_pin(), (
        f"tools/test_lane.py pins {tl.PINNED_IMAGE!r} but CI provisions "
        f"{_ci_pin()!r} — a lane on a different engine reports engine drift as "
        f"a product regression (#8083)"
    )


def test_the_edge_census_probe_defaults_are_the_ci_pin():
    """Both the function default and the ``--image`` default: the CLI parses its
    own literal, so pinning only one leaves the other floating."""
    assert edge_census.PROBE_IMAGE == _ci_pin()
    default = inspect.signature(edge_census.run_probe).parameters["image"].default
    assert default == _ci_pin(), default
    parsed = edge_census._build_parser().parse_args(["probe"])
    assert parsed.image == _ci_pin(), parsed.image


def test_graph_scripts_setup_uses_the_ci_pin():
    """``graph-scripts/setup.py`` deliberately never imports ``tortoise.*``, so
    it carries the value as a literal — and a literal is exactly what drifts."""
    spec = importlib.util.spec_from_file_location(
        "_setup_8083", GRAPH_SCRIPTS_SETUP)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module._FALKORDB_IMAGE == _ci_pin()


def test_graph_viz_compose_uses_the_ci_pin():
    text = GRAPH_VIZ_COMPOSE.read_text()
    images = re.findall(r"^\s+image:\s*(\S+)\s*$", text, re.M)
    assert images == [_ci_pin()], images


def test_no_executed_image_literal_in_the_lane_tooling_floats():
    """A drift guard with a POPULATION, not a list of four remembered sites: any
    FalkorDB image reference that appears as a STRING CONSTANT under ``tools/``
    or ``graph-scripts/`` — a default, an assignment, a literal argument — must
    be pinned.

    Docstrings and prose are excluded by shape: a reference is a single token,
    while a docstring mentioning one is a whole sentence.
    """
    offenders: list[str] = []
    population: list[str] = []
    for base in (ROOT / "tools", ROOT / "graph-scripts"):
        for path in sorted(base.rglob("*.py")):
            source = path.read_text()
            if "falkordb" not in source:
                # A substring test is the cheap half of the filter: a file that
                # never spells the name cannot carry a reference, and parsing
                # every tool here would cost more than the guard is worth.
                continue
            with warnings.catch_warnings():
                # `ast.parse` re-emits the files' OWN invalid-escape warnings;
                # this scan is a reader, not their owner, so keep them out of
                # this suite's output (they are not failures of the pin).
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(source)
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Constant)
                        and isinstance(node.value, str)
                        and "falkordb" in node.value
                        and _IMAGE_REF.match(node.value)):
                    continue
                population.append(f"{path.relative_to(ROOT)}:{node.lineno} "
                                  f"{node.value}")
                if _floating(node.value):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} "
                                     f"{node.value!r}")
    assert population, (
        "the scan found NO FalkorDB image constants — it is measuring nothing, "
        "which is not the same as measuring zero offenders"
    )
    assert not offenders, (
        "floating FalkorDB image default(s) — a v6 tag that floats here can be "
        f"killed by {PANICKING_QUERY!r} and lose the lane's graphs (#8083): "
        + ", ".join(offenders)
    )


# ── the seam stays, the silence does not ──────────────────────────────────


def test_a_pinned_reference_is_silent():
    assert tl._image_drift_warning(_ci_pin()) is None
    assert tl._image_drift_warning() is None, (
        "the DEFAULT must be silent — a warning on every start trains the "
        "reader to ignore it"
    )


@pytest.mark.parametrize("ref", [
    "falkordb/falkordb:latest",
    "falkordb/falkordb",            # no tag at all == :latest
])
def test_a_floating_reference_warns_and_names_the_panic(ref):
    warning = tl._image_drift_warning(ref)
    assert warning, f"{ref!r} must be announced, not silently accepted"
    assert ref in warning
    assert "#8083" in warning
    assert tl.PINNED_IMAGE in warning, (
        "the warning must name the value to use instead, or the reader has to "
        "go find it"
    )


def test_a_registry_host_with_a_port_is_not_mistaken_for_a_floating_tag():
    ref = "mirror.gcr.io:5000/falkordb/falkordb-server:v4.20.6"
    assert tl._image_drift_warning(ref) is None


def test_start_announces_an_image_that_arrives_floating_through_the_env_seam(
        monkeypatch, capsys):
    """The seam is kept on purpose (CI may pin a digest). The property is that a
    floating value through it is VISIBLE before the container exists — which is
    the difference between a lane crash that is attributable and one that reads
    as a diff regression."""
    class _R:
        returncode, stdout, stderr = 0, "PONG", ""

    monkeypatch.setenv("TORTOISE_TEST_LANE_REAP", "0")
    monkeypatch.setattr(tl, "IMAGE", "falkordb/falkordb:latest")
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-8083test")
    monkeypatch.setattr(tl, "container_state", lambda _n: "absent")
    monkeypatch.setattr(tl, "pick_port", lambda *a, **k: 16399)
    monkeypatch.setattr(tl, "_published_port", lambda _n: (True, 16400))
    # The readiness probe has its own suite (#8180); what is under test here is
    # that the warning precedes the start, so do not wait on a real socket.
    monkeypatch.setattr(tl, "_wait_ready", lambda *_a, **_k: True)
    monkeypatch.setattr(tl, "_docker", lambda *a, **k: _R())
    tl.start()
    _out, err = capsys.readouterr()
    assert "#8083" in err, err
    assert "falkordb/falkordb:latest" in err, err


# ── the reproducer, on the engine this repo actually ships ────────────────


def _docker(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True,
                          timeout=timeout, check=False)


@pytest.mark.skipif(shutil.which("docker") is None,
                    reason="docker CLI is not available")
def test_the_reproducer_returns_a_null_row_instead_of_killing_the_server():
    """Indicator (1) of #8083: the read-only aggregate returns a row (with
    ``null``) rather than terminating the process.

    Runs ONLY when the pinned image is already on the daemon: this must never be
    the reason a lane waits on a 260 MB pull. ``--network none`` keeps it
    hermetic — the probe talks to its own container through ``docker exec``.
    """
    pin = _ci_pin()
    if _docker("image", "inspect", pin).returncode != 0:
        pytest.skip(f"{pin} is not present locally (this test never pulls)")
    name = f"fdb-8083-repro-{os.getpid()}"
    _docker("rm", "-f", name)
    try:
        assert _docker("run", "-d", "--network", "none", "--name", name,
                       pin).returncode == 0, "the probe container did not start"
        deadline = time.time() + 60
        while time.time() < deadline:
            if "PONG" in _docker("exec", name, "redis-cli", "PING").stdout:
                break
            time.sleep(0.5)
        else:
            pytest.skip("the probe container never answered PING")

        assert _docker(
            "exec", name, "redis-cli", "GRAPH.QUERY", "probe",
            "CREATE (:Source {url:'a', sourceKind:'document'})",
        ).returncode == 0

        reply = _docker("exec", name, "redis-cli", "--raw", "GRAPH.QUERY",
                        "probe", PANICKING_QUERY)
        state = _docker("inspect", "-f", "{{.State.Status}} {{.State.ExitCode}}",
                        name).stdout.strip()
        assert state == "running 0", (
            f"the pinned engine {pin!r} did not survive the read-only aggregate "
            f"({state!r}) — that is the #8083 panic, on the engine this repo "
            f"ships"
        )
        cells = reply.stdout.splitlines()
        assert cells[:3] == ["s.sourceKind", "s.kind", "count(*)"], cells
        assert cells[3] == "document", cells
        assert cells[4] == "", (
            f"the absent property must come back as null (an empty raw cell), "
            f"got {cells[4]!r} — full reply {cells!r}"
        )
        assert cells[5] == "1", cells
    finally:
        _docker("rm", "-f", name)

"""tortoise-hermes-plugin — the Hermes read seam for volunteering memory.

Issue #2121, epic #2080 (end-state platform seams). Installed by
``tortoise install hermes`` (agent-first onboarding — nothing auto-registers).

WHAT HERMES GIVES THIS PLUGIN (verified against NousResearch/hermes-agent @
main, 2026-10-11 — ``plugins/AGENTS.md``, ``website/docs/developer-guide/
plugins/index.md`` §"``pre_llm_call`` context injection", and the calling code
``agent/turn_context.py::_collect_pre_llm_call_context``):

* ``register(ctx)`` + ``ctx.register_hook("pre_llm_call", cb)`` — the hook
  fires once per user turn, before the tool loop.
* The callback receives keyword fields (``session_id``, ``turn_id``,
  ``user_message``, ``conversation_history``, ``model``, ``platform``, …).
  Callbacks must accept ``**kwargs`` for forward compatibility.
* Returning ``{"context": "<text>"}`` (or a plain string) injects that text
  into the CURRENT TURN's user message. Returning ``None`` injects nothing.
* Fail-open is the host contract: a raising callback is logged and skipped,
  and the agent loop continues. This plugin never relies on that — it
  catches its own failures so a reflex outage is silent, not noisy.

TWO-WAY SEAM. The read half is here; the write/capture half is the existing
``tortoise sessions import --harness hermes`` backfill (``tortoise/
session_import/parsers.py::parse_hermes``), which reads ``$HERMES_HOME/
state.db`` read-only. Nothing in this plugin writes to the graph.

ONE PIPELINE. The injected block is produced by the SAME reflex every other
harness seam uses — the ``tortoise volunteer`` CLI over ``tortoise/
volunteer.py`` — so Hermes is a thin registration over the shared surface,
not a second recall implementation.

MARKER: tortoise-hermes-plugin — ``tortoise install hermes`` and its
``--uninstall`` both key on this line; do not remove it.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Hermes caps per-hook ``pre_llm_call`` context at 10,000 characters by
# default (``hooks.output_spill.max_chars``; ``tools/hook_output_spill.py::
# DEFAULT_MAX_CHARS = 10_000``) and SPILLS anything above it to
# ``$HERMES_HOME/hook_outputs/<session_id>/<uuid>.txt``, replacing the block
# with a head/tail preview. A spilled block is not the block, so this seam
# bounds itself well below the cap (the issue's ≤8KB block budget) and lets
# the reflex's own pointer budget keep the real blocks far smaller.
MAX_INJECT_CHARS = 8000

# The reflex is a local CLI invocation; it must never turn an agent turn into
# a wait. A timeout failure is a clean no-injection, exactly like an empty
# graph (content fail-open).
_REFLEX_TIMEOUT_S = 5.0

_TRUTHY = {"1", "true", "yes", "on"}


def _disabled() -> bool:
    return os.environ.get("TORTOISE_VOLUNTEER_DISABLE", "").strip().lower() in _TRUTHY


def _tortoise_candidates() -> list[list[str]]:
    """The reflex entry points to try, in the same order the shipped shell
    hook (``tortoise/claude-hooks/volunteer-turn.sh``) walks them.

    Hermes' per-turn hook is a Python PLUGIN, not a shell command, so the
    chain is re-expressed here rather than shelled out: PATH install → the
    module dir the installer recorded (``~/.tortoise/hook-src-dir``, with
    ``TORTOISE_SRC_DIR`` overriding it) → the ambient venv. Each candidate is
    only used if it exists: a wrong candidate is a skipped attempt, never a
    traceback on the agent path.
    """
    cmds: list[list[str]] = []
    exe = shutil.which("tortoise")
    if exe:
        cmds.append([exe, "volunteer"])
    src = os.environ.get("TORTOISE_SRC_DIR", "").strip()
    if not src:
        try:
            src = (Path(os.environ.get("HOME") or "") / ".tortoise"
                   / "hook-src-dir").read_text(encoding="utf-8").strip()
        except OSError:
            src = ""
    if src:
        venv_bin = Path(src) / ".venv" / "bin" / "tortoise"
        if venv_bin.is_file():
            cmds.append([str(venv_bin), "volunteer"])
    venv = os.environ.get("VIRTUAL_ENV", "").strip()
    if venv:
        vb = Path(venv) / "bin" / "tortoise"
        if vb.is_file():
            cmds.append([str(vb), "volunteer"])
    return cmds


def _reflex_block(user_message: str, session_id: str) -> str:
    """Run the shared reflex over one turn; "" on any failure (fail-open).

    ``--session-id`` is Hermes' ``session_id`` — the reflex's continuity
    input, which is what suppresses re-mentioning a pointer it already
    surfaced earlier in the same session (cross-turn dedupe). No session id
    means no continuity, never an error.
    """
    if not user_message.strip():
        return ""
    for argv in _tortoise_candidates():
        cmd = list(argv)
        if session_id:
            cmd += ["--session-id", session_id]
        try:
            proc = subprocess.run(
                cmd, input=user_message, capture_output=True, text=True,
                timeout=_REFLEX_TIMEOUT_S,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode == 0:
            return (proc.stdout or "").strip()
    return ""


def on_pre_llm_call(user_message: str = "", session_id: str = "", **kwargs):
    """``pre_llm_call`` handler — return the reflex block, or None.

    Returns ``None`` (no injection) for: the kill switch, an empty turn, an
    empty graph, nothing above the confidence gate, or any reflex failure.
    Returns ``{"context": block}`` — the documented injection shape — only
    when there is a block to inject. Never raises: a read seam must not be
    able to break an agent turn.
    """
    try:
        if _disabled():
            return None
        block = _reflex_block(str(user_message or ""),
                              str(session_id or ""))
        if not block.strip():
            return None
        return {"context": block[:MAX_INJECT_CHARS]}
    except Exception:
        logger.warning("tortoise-volunteer: reflex failed; injecting nothing",
                       exc_info=True)
        return None


def register(ctx):
    """Hermes plugin entry point (``plugin.yaml`` declares ``pre_llm_call``)."""
    ctx.register_hook("pre_llm_call", on_pre_llm_call)

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

import json
import logging
import os
import shutil
import subprocess
import time
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

#: The breadcrumb kind the shipped shell hooks write when the install leg is
#: inert (``hook_install.KIND_INSTALL_INERT``). An inert install must leave
#: EVIDENCE, never silence (#4314): fail-open injects nothing on a healthy
#: empty graph AND on a dead seam, so without this the two are
#: indistinguishable to a user or to ``session verify``.
_INSTALL_INERT_KIND = "install-inert"


def _disabled() -> bool:
    return os.environ.get("TORTOISE_VOLUNTEER_DISABLE", "").strip().lower() in _TRUTHY


def _record_install_inert(detail: str) -> None:
    """Best-effort ``install-inert`` breadcrumb, mirroring the shell hooks.

    Same slot layout as ``hook_install.local_state_dir`` /
    ``breadcrumb_name``: ``<receipt-dir>.parent/capture-errors/
    <harness>-install.json``, where the receipt dir is
    ``$TORTOISE_IMPORT_RECEIPT_DIR`` or ``$HOME/.tortoise/import-receipts``.

    Stdlib-only by construction — this module is loaded by the HOST
    interpreter and must not import ``tortoise.*``. A failed write can never
    raise into the agent turn, and an unset ``$HOME`` falls to the
    ``/nonexistent`` sentinel (never ``Path("")``, which is the process CWD —
    an agent-writable workspace, #2121 review).
    """
    try:
        override = os.environ.get("TORTOISE_IMPORT_RECEIPT_DIR")
        if override:
            receipt_dir = Path(override)
        else:
            receipt_dir = (Path(os.environ.get("HOME") or "/nonexistent")
                           / ".tortoise" / "import-receipts")
        crumb = receipt_dir.parent / "capture-errors" / "hermes-install.json"
        crumb.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "harness": "hermes",
            "detail": detail,
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "kind": _INSTALL_INERT_KIND,
        }
        crumb.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except Exception:  # evidence is best-effort, never fatal
        pass


def _tortoise_candidates() -> list[list[str]]:
    """The reflex entry points to try, PATH first then the venv fallbacks.

    Hermes' per-turn hook is a Python PLUGIN, not a shell command, so the
    chain is re-expressed here rather than shelled out: PATH install → the
    module dir the installer recorded (``~/.tortoise/hook-src-dir``, with
    ``TORTOISE_SRC_DIR`` overriding it) → the ambient venv. Each candidate is
    only used if it exists: a wrong candidate is a skipped attempt, never a
    traceback on the agent path.

    This is a SUBSET of the shipped shell hook's chain
    (``tortoise/claude-hooks/volunteer-turn.sh``): the shell hook additionally
    walks up for the ``pyvenv.cfg`` that OWNS a wheel's ``site-packages``
    (#2385 item 3) and falls back to running the module directly. The middle
    rung here is populated only by ``TORTOISE_SRC_DIR`` or a record some other
    seam's install wrote — ``install hermes`` writes no record. A machine
    whose only Tortoise install is Hermes and which lacks a ``tortoise`` on
    PATH/VIRTUAL_ENV therefore injects nothing; that is why the no-candidate
    path leaves an ``install-inert`` breadcrumb instead of silence.
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

    A turn where NO reflex resolves, or where every resolved reflex FAILS, is
    an inert install or a broken one — not an empty graph — so it leaves an
    ``install-inert`` breadcrumb before returning "" (an empty graph exits 0
    and stays silent, which is the documented content fail-open).
    """
    if not user_message.strip():
        return ""
    candidates = _tortoise_candidates()
    if not candidates:
        _record_install_inert(
            "the installed Hermes plugin resolved no tortoise reflex (checked "
            "PATH, the installer's ~/.tortoise/hook-src-dir record, and "
            "$VIRTUAL_ENV) and injected nothing")
        return ""
    last_error = ""
    for argv in candidates:
        cmd = list(argv)
        if session_id:
            cmd += ["--session-id", session_id]
        try:
            proc = subprocess.run(
                cmd, input=user_message, capture_output=True, text=True,
                timeout=_REFLEX_TIMEOUT_S,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            continue
        if proc.returncode == 0:
            return (proc.stdout or "").strip()
        last_error = f"exit {proc.returncode}"
    _record_install_inert(
        "every resolved tortoise reflex failed "
        f"({last_error or 'no candidate ran'}) and injected nothing")
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

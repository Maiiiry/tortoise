"""#3515 piece 5 — the recovery sweep, as one command: ``tortoise store-sync``.

Two substrates hold a captured-but-not-yet-filed session, and they are drained
TOGETHER because a sweep that drains one and not the other leaves half the
death modes unhandled:

1. **The capture spool** (``~/.tortoise/session-events/`` + the per-session
   ``*.turns.jsonl`` entries) — written by the in-process hooks, including the
   turns they managed to observe before the process died. Drained by
   :func:`tortoise.capture_spool.flush_spool`, the SAME function
   ``tortoise session drain`` calls: this module adds no second drainer.
2. **Pi's own session log** (``~/.pi/agent/sessions/**/*.jsonl``) — the only
   copy that exists when the hook never ran at all. Drained by the store-sync
   worker (``tortoise/pi-hooks/store-sync.ts``) through its per-file cursor.

The worker is TypeScript because that is what the epic pins (``§4 #3515``:
"the Pi leg, ``tortoise/pi-hooks/store-sync.ts``") and because it must share
the hook's payload builder rather than re-implement it. This Python entry point
exists for the two reasons §C pins: it is the artifact the tests drive
(``subprocess.run``, so a cursor/restart property is proven at PROCESS level),
and it gives self-hosted users a manual sync command.

An unavailable ``node`` is not a capture failure to hide: the spool half still
runs and the receipt says the log half was skipped and why. A Pi user always
has node (Pi is a node program); a non-Pi user has no Pi session log to ship.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

__all__ = [
    "RECEIPT_SENTINEL",
    "default_cursor_dir",
    "default_session_dir",
    "run_sweep",
    "worker_path",
]

RECEIPT_SENTINEL = "RECEIPT_JSON:"
#: Must EXCEED the worker's own ceiling, or the enclosing kill discards a run's
#: whole cursor progress (it is saved per-file AND at the end). Ceiling =
#: MAX_REQUESTS_PER_RUN (200) × REQUEST_TIMEOUT_MS (10 s) + per-file backoff.
WORKER_TIMEOUT_S = 3600


def worker_path() -> Path:
    """The shipped worker artifact (the object the artifact-guard test pins)."""
    return Path(__file__).resolve().parent / "pi-hooks" / "store-sync.ts"


def default_session_dir() -> Path:
    return Path.home() / ".pi" / "agent" / "sessions"


def default_cursor_dir() -> Path:
    return Path.home() / ".tortoise" / "store-sync"


def _parse_receipt(stdout: str) -> dict[str, Any] | None:
    for line in reversed(stdout.splitlines()):
        if line.startswith(RECEIPT_SENTINEL):
            try:
                return json.loads(line[len(RECEIPT_SENTINEL):])
            except json.JSONDecodeError:
                return None
    return None


def run_worker(
    *,
    session_dir: Path | str | None = None,
    cursor_dir: Path | str | None = None,
    files: list[str] | None = None,
    max_files: int | None = None,
    max_requests: int | None = None,
    dry_run: bool = False,
    api_url: str | None = None,
    exclude_session_id: str | None = None,
    node: str | None = None,
    timeout: int = WORKER_TIMEOUT_S,
) -> dict[str, Any]:
    """Run the store-sync worker as a PROCESS and return its receipt.

    Process-level on purpose: the cursor's crash-consistency claim is about a
    process that exits and a later process that resumes, which an in-process
    call cannot produce.
    """
    artifact = worker_path()
    if not artifact.exists():  # pragma: no cover - the artifact guard covers this
        return {
            "ok": False,
            # `worker_status` is the NOT-RUN reason; `skipped` on a real receipt
            # is a COUNT (files already up to date), so overloading it made a
            # failed half report ok whenever any file was current.
            "worker_status": "worker_missing",
            "detail": f"the store-sync worker is absent at {artifact}",
            "files": [],
        }
    exe = node or shutil.which("node")
    if not exe:
        return {
            "ok": False,
            "worker_status": "node_unavailable",
            "detail": "no `node` on PATH — the Pi log half of the sweep did not run",
            "files": [],
        }
    argv = [exe, str(artifact)]
    if session_dir:
        argv += ["--session-dir", str(session_dir)]
    if cursor_dir:
        argv += ["--cursor-dir", str(cursor_dir)]
    for f in files or []:
        argv += ["--file", str(f)]
    if max_files is not None:
        argv += ["--max-files", str(max_files)]
    if max_requests is not None:
        argv += ["--max-requests", str(max_requests)]
    if api_url:
        argv += ["--api-url", str(api_url)]
    if exclude_session_id:
        argv += ["--exclude-session-id", str(exclude_session_id)]
    if dry_run:
        argv += ["--dry-run"]
    argv += ["--json"]
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            argv, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "worker_status": "timeout",
                "detail": f"worker exceeded {timeout}s", "files": []}
    receipt = _parse_receipt(proc.stdout or "")
    if receipt is None:
        return {
            "ok": False,
            "worker_status": "no_receipt",
            "detail": (proc.stderr or proc.stdout or "worker produced no receipt").strip()[-2000:],
            "files": [],
        }
    receipt["ok"] = proc.returncode == 0 and not receipt.get("errors")
    if proc.returncode != 0:
        receipt["detail"] = (proc.stderr or "").strip()[-2000:]
    return receipt


def run_sweep(
    *,
    api_key: str | None = None,
    api_url: str | None = None,
    spool: bool = True,
    session_dir: Path | str | None = None,
    cursor_dir: Path | str | None = None,
    files: list[str] | None = None,
    max_files: int | None = None,
    max_requests: int | None = None,
    dry_run: bool = False,
    exclude_session_id: str | None = None,
    node: str | None = None,
    post=None,
) -> dict[str, Any]:
    """The recovery sweep: drain the SPOOL, then the Pi session LOG.

    ``post``/``spool=False`` exist so tests can drive either half in isolation
    (a spool drain with a stubbed transport must not reach the network).
    """
    receipt: dict[str, Any] = {"spool": None, "log": None, "ok": True}
    if spool:
        summary: dict[str, Any] = {"attempted": 0}
        try:
            from tortoise.capture_spool import flush_spool, spool_dir

            if post is None:
                if not api_key:
                    receipt["spool"] = {
                        "skipped": "no_credential",
                        "detail": "no API key — the spool half was not drained",
                    }
                else:
                    from tortoise.__main__ import _session_post  # noqa: PLC0415

                    summary_obj = flush_spool(spool_dir(), _session_post(api_key, api_url or ""))
                    receipt["spool"] = _summary_dict(summary_obj)
            else:
                summary_obj = flush_spool(spool_dir(), post)
                receipt["spool"] = _summary_dict(summary_obj)
        except Exception as exc:  # a broken spool must not kill the log half
            receipt["spool"] = {"error": f"{type(exc).__name__}: {exc}"}
            receipt["ok"] = False
    receipt["log"] = run_worker(
        session_dir=session_dir if session_dir is not None else default_session_dir(),
        cursor_dir=cursor_dir if cursor_dir is not None else default_cursor_dir(),
        files=files,
        max_files=max_files,
        max_requests=max_requests,
        dry_run=dry_run,
        api_url=api_url,
        exclude_session_id=exclude_session_id,
        node=node,
    )
    # Gate on the worker's OWN verdict, not on a key that also carries a count.
    if receipt["log"] is not None and receipt["log"].get("ok") is False:
        receipt["ok"] = False
    return receipt


def _summary_dict(summary) -> dict[str, Any]:
    return {
        "filed": getattr(summary, "filed", 0),
        "deferred": getattr(summary, "deferred", 0),
        "skipped": getattr(summary, "skipped", 0),
        "held_back": getattr(summary, "held_back", 0),
        "held_by_backoff": getattr(summary, "held_by_backoff", 0),
        "terminal": getattr(summary, "terminal", 0),
        "discarded": len(getattr(summary, "discarded", []) or []),
    }


def main(args) -> int:
    """``tortoise store-sync`` — print a receipt; never raise into the CLI."""
    receipt = run_sweep(
        api_key=getattr(args, "api_key", None),
        api_url=getattr(args, "api_url", None),
        spool=not getattr(args, "no_spool", False),
        session_dir=getattr(args, "session_dir", None),
        cursor_dir=getattr(args, "cursor_dir", None),
        files=getattr(args, "file", None),
        max_files=getattr(args, "max_files", None),
        max_requests=getattr(args, "max_requests", None),
        dry_run=getattr(args, "dry_run", False),
        exclude_session_id=getattr(args, "exclude_session_id", None),
    )
    if getattr(args, "json", False):
        print(json.dumps(receipt, indent=1, sort_keys=True))
    else:
        log = receipt.get("log") or {}
        print(
            "store-sync: "
            f"{log.get('shipped', 0)} shipped, "
            f"{log.get('skipped', 0)} up to date, "
            f"{log.get('deadLettered', 0)} dead-lettered, "
            f"{log.get('dropped', 0)} dropped"
        )
        for err in (log.get("errors") or [])[:10]:
            print(f"store-sync error: {err}", file=sys.stderr)
        # The durability signals must be VISIBLE on the human path too: a
        # production caller spawns this with stdio ignored, so a count that only
        # exists under --json is a count nobody reads.
        if log.get("cursorRecovered"):
            print(f"store-sync: cursor — {log['cursorRecovered']}", file=sys.stderr)
        if log.get("cursorWriteError"):
            print(f"store-sync: cursor write FAILED — {log['cursorWriteError']} "
                  "(the next run re-ships)", file=sys.stderr)
        if log.get("worker_status"):
            print(f"store-sync: log half not run — {log.get('detail', log.get('worker_status'))}",
                  file=sys.stderr)
        if receipt.get("spool"):
            print(f"store-sync: spool — {receipt['spool']}", file=sys.stderr)
    return 0 if receipt.get("ok") else 1

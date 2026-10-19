"""#8483 — is the code THIS process loaded still the code in the checkout?

An MCP server is spawned per ``pi`` session and then lives for the whole life
of that session, with no supervisor, no respawn and no reload (measured
2026-10-13: five ``python -m tortoise.mcp_server`` children started within
eight seconds of each other, none supervised, one 12h53m old). Nothing
revalidates the revision a running server serves, so a read-path fix that
lands *after* a server starts is invisible to that session for as long as it
lives. The observed failure was an ``ImportError`` for a name added to
``tortoise/vector_write.py`` hours after the servers started — which reads,
from inside the session, exactly like a graph with nothing in it, and so
invites the one write ``how-to-use-tortoise`` warns about: re-creating data
that is already there.

This module is the **staleness signal**, deliberately not a reload and not a
supervisor:

* **Not a reload.** Re-importing a live server is a different risk class —
  open FalkorDB handles, background threads, request-scoped context vars.
  Reporting the condition is safe; mutating a live process is not.
* **Not a supervisor.** Changing the spawn model (spawn-per-call, a bounded
  lifetime, one supervised shared server) is the owner's call in #8483's body
  and is explicitly not a drop-in: a *shared* server changes the
  multi-tenant/isolation story.

Detection is a comparison of two instants we already have: when this package
began loading (``LOAD_INSTANT``, captured here, which ``tortoise/__init__.py``
imports before any other submodule) and the on-disk mtime of every ``*.py`` in
the package. A file whose mtime is later than ``LOAD_INSTANT`` changed after
this process began loading it, so the copy in memory may be older than the
checkout — including a *new* file, which is why the scan walks the directory
rather than ``sys.modules``: the module that breaks a stale server is often one
it never imported.

Why mtime and not a content digest: the content this process actually READ is
gone by the time the question is asked — the file was overwritten, which is
what makes the process stale — so there is nothing left to diff against and
the mtime is the only surviving evidence. The trade-off is deliberate and errs
toward **recall**: a bare ``touch`` with no content change reports stale, which
costs a needless restart; the opposite error is the silent stale read this
module exists to expose, which costs duplicated graph writes.

Every public helper is best-effort and never raises: this is called from a
failure path (``mcp_server._safe``), and a diagnostic that can itself fail
would replace the original error with a worse one.
"""

from __future__ import annotations

import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

__all__ = [
    "LOAD_INSTANT",
    "PACKAGE_ROOT",
    "changed_sources",
    "report",
    "reset_cache",
    "revision",
    "stale_code_hint",
    "staleness",
]

#: The instant this package began loading. Captured at import of this module,
#: which ``tortoise/__init__.py`` imports BEFORE any other tortoise submodule —
#: the comparison below is only sound while this instant predates the code it is
#: compared against. ``time.time`` (not ``monotonic``) because it is compared
#: with file mtimes, which are wall-clock.
LOAD_INSTANT: float = time.time()

#: The package directory whose ``*.py`` files are the code this process loaded.
PACKAGE_ROOT: Path = Path(__file__).resolve().parent

#: How long a scan result is reused (seconds). The health surface can be polled;
#: a directory walk over ~240 files per call is cheap but not free.
_SCAN_TTL = 2.0

#: How long the checkout revision lookup is reused (seconds). It shells out to
#: ``git``, which is far more expensive than the walk.
_REVISION_TTL = 5.0

#: Bound on the ``git`` subprocess, so a wedged checkout cannot wedge a health call.
_GIT_TIMEOUT = 2.0

#: How many changed paths a hint names before summarising the remainder.
_HINT_FILES = 2

_scan_cache: dict[tuple[str, float], tuple[float, int, tuple[str, ...], str | None]] = {}
_revision_cache: tuple[float, str | None] | None = None


def _iso(instant: float) -> str:
    """ISO-8601 UTC rendering of a wall-clock instant."""
    return datetime.fromtimestamp(instant, UTC).isoformat()


def _human_duration(seconds: float) -> str:
    """Compact, human-readable duration ("48s", "12m04s", "12h53m", "3d04h")."""
    total = int(max(0.0, seconds))
    if total < 60:
        return f"{total}s"
    minutes, total = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m{total:02d}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h{minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d{hours:02d}h"


def _scan(root: Path, instant: float) -> tuple[int, tuple[str, ...], str | None]:
    """Walk ``root`` for ``*.py`` files newer than ``instant``.

    Returns ``(count, changed_relative_paths, error)``. Paths are relative to
    ``root``'s PARENT so a hint reads like the file a reader would open
    (``tortoise/vector_write.py``), falling back to the bare filename when the
    root is not a checkout-shaped directory. Newest first, so the file most
    likely to matter is the one a hint names.
    """
    changed: list[tuple[float, str]] = []
    try:
        for path in root.rglob("*.py"):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue  # vanished mid-walk (a checkout can rewrite the tree)
            if mtime <= instant:
                continue
            try:
                rel = path.relative_to(root.parent)
            except ValueError:
                rel = Path(path.name)
            changed.append((mtime, rel.as_posix()))
    except OSError as exc:  # pragma: no cover - unreadable tree is environment, not logic
        return 0, (), f"{type(exc).__name__}: {exc}"
    changed.sort(key=lambda pair: (-pair[0], pair[1]))
    return len(changed), tuple(rel for _, rel in changed), None


def changed_sources(
    root: Path | str | None = None,
    instant: float | None = None,
    ttl: float = _SCAN_TTL,
) -> tuple[int, list[str], str | None]:
    """``(count, changed_relative_paths, error)`` for files changed after ``instant``.

    ``root`` defaults to this package and ``instant`` to :data:`LOAD_INSTANT`;
    both are parameters so a test can point the scan at a fixture tree without
    touching real files. Results are cached for ``ttl`` seconds (pass ``ttl=0``
    to force a fresh scan).
    """
    root_path = PACKAGE_ROOT if root is None else Path(root)
    since = LOAD_INSTANT if instant is None else float(instant)
    key = (str(root_path), since)
    now = time.monotonic()
    cached = _scan_cache.get(key)
    if cached is not None and ttl > 0 and (now - cached[0]) < ttl:
        return cached[1], list(cached[2]), cached[3]
    count, files, error = _scan(root_path, since)
    _scan_cache[key] = (now, count, files, error)
    return count, list(files), error


def staleness(
    root: Path | str | None = None,
    instant: float | None = None,
    ttl: float = _SCAN_TTL,
) -> dict:
    """The staleness verdict for the code this process loaded.

    ``{stale, changed_count, changed_files, loaded_at, age_seconds, scan_error}``.
    ``stale`` is ``True`` when any package source is newer than the instant this
    process began loading; ``scan_error`` carries a walk failure (``None`` on a
    clean scan) so "nothing changed" is distinguishable from "could not look" —
    the same honesty rule ``tortoise_health`` already applies to ``graph_size``.
    """
    since = LOAD_INSTANT if instant is None else float(instant)
    count, files, error = changed_sources(root, since, ttl)
    return {
        "stale": count > 0,
        "changed_count": count,
        "changed_files": files,
        "loaded_at": _iso(since),
        "age_seconds": round(max(0.0, time.time() - since), 1),
        "scan_error": error,
    }


def _git_head() -> str | None:
    """Short HEAD of the checkout this package was loaded from, or ``None``.

    Best-effort by construction: a wheel install has no ``.git``, a container may
    have no ``git`` binary, and a locked repository can time out. The revision is
    orientation for the reader ("which code is this?") and never feeds the
    ``stale`` verdict, which is why a failure here degrades to ``None`` instead
    of an error.
    """
    try:
        completed = subprocess.run(
            ["git", "-C", str(PACKAGE_ROOT.parent), "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - environment
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def revision(ttl: float = _REVISION_TTL) -> str | None:
    """Cached :func:`_git_head` (the checkout's short HEAD)."""
    global _revision_cache
    now = time.monotonic()
    if _revision_cache is not None and ttl > 0 and (now - _revision_cache[0]) < ttl:
        return _revision_cache[1]
    head = _git_head()
    _revision_cache = (now, head)
    return head


def report() -> dict:
    """The ``code`` block served on the health surface.

    ``{pid, revision, stale, changed_count, changed_files, loaded_at,
    age_seconds, scan_error}`` — this process's identity and whether the code it
    holds still matches the checkout. Always emitted (it is the point), and it
    deliberately does NOT change ``status``: the database can be perfectly
    healthy while the client's code is stale, so folding staleness into
    ``status`` would make every routine code change look like an outage.
    """
    block = {"pid": os.getpid(), "revision": revision()}
    block.update(staleness())
    return block


def stale_code_hint(limit: int = _HINT_FILES) -> str | None:
    """One-line attribution to append to a stale-loaded failure, or ``None``.

    Returns ``None`` for a fresh process, which is what keeps every error
    response byte-identical to the pre-#8483 text when nothing is stale. When it
    does return, the caller must treat it as an ADDITION: the original message
    stays first, so an agent still sees the real error before the explanation.

    Only local, non-secret facts are named (relative source paths, a duration) —
    never a host, a port or an absolute path, so the text needs no scrubbing and
    leaks nothing a caller could not already read from the checkout's name.
    """
    state = staleness()
    if not state["stale"]:
        return None
    files = state["changed_files"]
    named = ", ".join(files[:limit]) if limit > 0 else ""
    remainder = len(files) - min(len(files), max(limit, 0))
    if remainder > 0:
        named = f"{named} (+{remainder} more)" if named else f"{remainder} files"
    plural = "s" if state["changed_count"] != 1 else ""
    return (
        f"code-stale (#8483): this MCP process has been up "
        f"{_human_duration(state['age_seconds'])} and {state['changed_count']} package "
        f"source file{plural} changed on disk after it began loading ({named}) — it is "
        "serving code OLDER than the checkout. Restart the MCP server and re-run this "
        "call BEFORE treating an empty result or a missing name as absent data."
    )


def reset_cache() -> None:
    """Drop both caches. For tests, and for a caller that must see the truth now."""
    global _revision_cache
    _scan_cache.clear()
    _revision_cache = None

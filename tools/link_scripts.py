#!/usr/bin/env python3
"""link_scripts — is the mandated toolchain REACHABLE from this checkout? (#9327)

Why this exists
---------------
`scripts/` is a **tracked symlink** (git mode 120000) whose committed target is
one absolute, machine-specific path::

    $ git cat-file -p HEAD:scripts
    /Users/danielospina/Documents/GitHub/agent-infra/scripts

`AGENTS.md` documents the intent as ``scripts/ → $AGENT_INFRA_PATH/scripts``,
but a committed symlink stores *literal text*, so the path a fresh checkout
materializes is whatever host wrote the blob last. On this fleet the two do not
agree: measured 2026-10-21 in a freshly created worktree,
``/Users/danielospina/Documents/GitHub/agent-infra/scripts`` **does not exist**
and ``scripts/admin-merge.sh`` therefore does not resolve. The mandated merge
path (``scripts/admin-merge.sh``, the ONLY merge path per the skill) is
unreachable — not because a worktree was reaped, but because the entry point the
rail is reached *through* is a host-specific pointer nobody verified.

That is the same consequence #9327 records by a different route: a lane that
cannot reach the rail is pushed to merge by hand or abandon merged-ready work.
The reaper half of #9327 (a live lane's worktree removed out from under it) is
NOT fixable from inside this repository — see the issue — but *this* half is: the
repository owns the symlink, and it can be verified and repaired without
operator authorization.

What it does
------------
One read-only verdict per invocation, then (only with ``--apply``) a repair:

  1. resolve the wanted scripts root — ``--target``, else
     ``$AGENT_INFRA_PATH/scripts``;
  2. classify what ``scripts`` is here: ``absent`` | ``symlink`` (with its
     target, resolved and dangling-or-not) | ``real-directory`` | ``file``;
  3. ``--apply``: re-point a wrong/dangling/absent ``scripts`` symlink at the
     wanted root, atomically (a temp symlink in the same directory, then
     ``os.replace``). A REAL directory (or a regular file) is refused, never
     clobbered — it may be a hand-built lane tree. Without ``--apply`` such a
     state is NOT a refusal: it is simply not ours to repair, so the rail
     verdict alone decides the exit code;
  4. report whether the rail entry point resolves *through* ``scripts``
     (``--rail``, default ``admin-merge.sh``). This is the verdict that would
     have told a lane, at dispatch time, that its merge path was gone.

Exit 0 is a single reading: it requires BOTH that ``scripts`` already resolves to
the wanted root (nothing left to repair) AND that the rail resolves through it. A
wrong-but-live ``scripts`` link that still finds the rail exits 1 with
``repair: NEEDED`` rather than 0 — otherwise one exit code would carry two
meanings ("the shared root is in place" vs "the rail is reachable") and a caller
keying on the first would be misled.

Side effect of ``--apply`` (read this before committing)
--------------------------------------------------------
``scripts`` is a **tracked** symlink (git mode 120000), so a repair leaves a
MODIFIED TRACKED FILE in the worktree. The repair is host-local: committing it
re-writes the blob to *this* host's absolute path, which is precisely how #9327
was created, so it must not be committed (a broad `git add`/`git commit -a`
would sweep it into an unrelated PR), and `git checkout -- scripts` silently
restores the committed link.

Why the rail check is separate from the symlink check
-----------------------------------------------------
Repairing ``scripts`` to ``$AGENT_INFRA_PATH/scripts`` restores the documented
shared-script surface, but that directory is not guaranteed to *contain* the
rail: measured on this host it holds 16 helper scripts and **no**
``admin-merge.sh`` (the rail lives in the lane replicas). Making the symlink
"correct" and calling the rail reachable would be exactly the false pass this
tool exists to refuse, so the two are reported separately and the rail verdict is
never inferred from the symlink verdict.

Exit codes
----------
  0  reachable AND nothing left to do — ``scripts`` already resolves to the
     wanted root AND the rail entry point resolves through it. Exit 0 is
     therefore a single reading, not two: a `scripts` link that a repair would
     still re-point (``repair: NEEDED``) is NOT 0, even when the rail happens to
     resolve through it
  1  NOT reachable — a repair is known and not yet applied (dry run), or
     ``--apply`` ran but the rail still does not resolve (see the verdict)
  2  REFUSED / could not evaluate — no usable target, or ``--apply`` was asked
     to replace a real directory / regular file (never clobbered). A DRY RUN
     over a real directory is NOT a refusal: a hand-built lane tree is
     legitimate, so the rail verdict alone decides 0/1 and the ``repair:`` line
     says it is not ours to replace
  3  usage error

Usage
-----
    uv run python tools/link_scripts.py                  # dry run: verdict only
    uv run python tools/link_scripts.py --apply          # repair the symlink
    uv run python tools/link_scripts.py --json
    uv run python tools/link_scripts.py --target /path/to/scripts --rail admin-merge.sh
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — this file is a
# `tools/` entry point, and the guard must precede every other import so a
# scheduled/hand invocation on an old interpreter fails with the documented
# message instead of an ImportError from a 3.12-only construct.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/link_scripts.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/link_scripts.py`"
    )

import argparse
import json
import os
from pathlib import Path

EXIT_REACHABLE = 0
EXIT_UNREACHABLE = 1
EXIT_REFUSED = 2
EXIT_USAGE = 3

#: The rail's own entry point (the ONLY merge path per the merge skill).
DEFAULT_RAIL = "admin-merge.sh"

STATE_ABSENT = "absent"
STATE_SYMLINK = "symlink"
STATE_REAL_DIR = "real-directory"
STATE_FILE = "file"


class Refused(Exception):
    """The tool cannot evaluate, or must not act. Nothing was changed."""


def resolve_target(explicit: str | None) -> Path:
    """The scripts root ``scripts`` should point at — or ``Refused``.

    ``$AGENT_INFRA_PATH/scripts`` is the documented intent (AGENTS.md §Paths).
    An unresolvable target is a REFUSAL, not an empty verdict: reporting "not
    reachable" over a target that does not exist would blame the checkout for a
    broken environment, and repairing the symlink to it would *create* a
    dangling link where the operator had none.
    """
    if explicit:
        raw = explicit
        origin = "--target"
    else:
        base = os.environ.get("AGENT_INFRA_PATH", "").strip()
        if not base:
            raise Refused(
                "AGENT_INFRA_PATH is unset and no --target was given, so the wanted "
                "scripts root is unknown — export AGENT_INFRA_PATH (see AGENTS.md "
                "§Prerequisites) or pass --target")
        raw = os.path.join(base, "scripts")
        origin = "$AGENT_INFRA_PATH/scripts"
    path = Path(os.path.expanduser(raw)).absolute()
    if not path.is_dir():
        raise Refused(f"the wanted scripts root ({origin} = {path}) is not a directory")
    return path


def classify(link: Path) -> dict:
    """What ``<checkout>/scripts`` is right now. Never follows into a real tree."""
    info: dict = {"state": STATE_ABSENT, "recorded": None, "resolved": None,
                  "dangling": False}
    if link.is_symlink():
        info["state"] = STATE_SYMLINK
        info["recorded"] = os.readlink(link)
        # `Path.resolve()` on a DANGLING link still yields the would-be path, so
        # existence is a separate question. A dangling link is the #9327 shape:
        # the committed text names another host's directory.
        resolved = Path(os.path.realpath(link))
        info["resolved"] = str(resolved)
        info["dangling"] = not resolved.exists()
        return info
    if link.is_dir():
        info["state"] = STATE_REAL_DIR
        return info
    if link.exists():
        info["state"] = STATE_FILE
    return info


def needs_repair(info: dict, target: Path) -> bool:
    """True when ``scripts`` does not already resolve to ``target``."""
    if info["state"] == STATE_ABSENT:
        return True
    if info["state"] != STATE_SYMLINK:
        # A real directory / a regular file is never "repaired" by us.
        return False
    if info["dangling"]:
        return True
    return Path(info["resolved"] or "") != Path(os.path.realpath(target))


def repair(link: Path, target: Path) -> None:
    """Point ``scripts`` at ``target`` atomically. Never clobbers a real tree.

    The repaired path is a **tracked** symlink (git mode 120000), so a repair
    leaves a modified tracked file in the worktree. See ``NOTE`` in ``_render``:
    the repair is host-local and must not be committed.
    """
    if link.is_dir() and not link.is_symlink():
        raise Refused(
            f"{link} is a REAL directory (not a symlink) — refusing to replace it. "
            f"A hand-built lane scripts tree is legitimate; move it aside yourself "
            f"if the shared root is what you want")
    tmp = link.with_name(f".{link.name}.link_scripts.{os.getpid()}")
    try:
        if tmp.is_symlink() or tmp.exists():
            tmp.unlink()
        os.symlink(str(target), tmp)
        # `os.replace` onto a symlink replaces the LINK (rename(2)), never the
        # directory it points at — so this cannot touch the shared root.
        os.replace(tmp, link)
    finally:
        if tmp.is_symlink() or tmp.exists():
            tmp.unlink()


def rail_verdict(checkout: Path, rail: str) -> dict:
    """Does the rail entry point resolve THROUGH ``scripts`` from here?"""
    path = checkout / "scripts" / rail
    real = Path(os.path.realpath(path))
    return {"rail": rail, "path": str(path), "resolved": str(real),
            "reachable": real.exists() and os.access(real, os.X_OK)}


def _render(checkout: Path, target: Path | None, info: dict, repair_needed: bool,
            applied: bool, rail: dict, refusal: str | None,
            not_repairable: str | None = None) -> None:
    print(f"link_scripts: checkout={checkout}")
    print(f"  target: {target if target is not None else '(unresolved)'}")
    recorded = info.get("recorded")
    state = info["state"]
    print(f"  scripts: {state}"
          + (f" -> {recorded}" if recorded else "")
          + (" (DANGLING)" if info.get("dangling") else ""))
    if refusal:
        print(f"  REFUSED: {refusal}")
    if applied:
        print("  repair: APPLIED")
        # #9564 review P3: `scripts` is a TRACKED symlink (git mode 120000), so
        # a repair modifies a tracked file. Committing this host's absolute path
        # would re-create #9327 for every other host while looking like a fix —
        # say so where the repair happens, not only in the docstring.
        print("  NOTE: `scripts` is a TRACKED symlink — this repair is HOST-LOCAL. "
              "Do NOT commit it (and do not `git add -A`/`git commit -a` near it): "
              "committing re-writes the blob to THIS host's absolute path, "
              "re-creating #9327 for every other host. `git checkout -- scripts` "
              "restores the committed (possibly dangling) link.")
    elif repair_needed:
        print("  repair: NEEDED — re-run with --apply")
    elif not_repairable:
        print(f"  repair: NOT OURS TO DO — {not_repairable}")
    verdict = "REACHABLE" if rail["reachable"] else "UNREACHABLE"
    print(f"  rail: {rail['path']} -> {verdict}")
    if not rail["reachable"]:
        print(f"  NOTE: the rail is the mandated merge path; a lane that cannot reach "
              f"{rail['rail']} has no sanctioned merge route. Repairing `scripts` does "
              f"not create it — the target directory must contain it.")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="link_scripts",
        description="Verify (and with --apply repair) the reachability of the "
                    "checkout's `scripts/` surface and the mandated rail entry "
                    "point (#9327). Dry-run by default.",
        allow_abbrev=False,
    )
    p.add_argument("--checkout", default=".",
                   help="checkout whose `scripts` symlink to inspect (default: cwd)")
    p.add_argument("--target", default=None,
                   help="wanted scripts root (default: $AGENT_INFRA_PATH/scripts)")
    p.add_argument("--rail", default=DEFAULT_RAIL,
                   help=f"rail entry point to verify through `scripts` "
                        f"(default: {DEFAULT_RAIL})")
    p.add_argument("--apply", action="store_true",
                   help="re-point a wrong/dangling/absent `scripts` symlink")
    p.add_argument("--json", action="store_true", help="machine-readable verdict")
    args = p.parse_args(argv)

    checkout = Path(os.path.expanduser(args.checkout)).absolute()
    if not checkout.is_dir():
        print(f"link_scripts: --checkout is not a directory: {checkout}", file=sys.stderr)
        return EXIT_USAGE
    if not args.rail.strip() or "/" in args.rail or args.rail in (".", ".."):
        print(f"link_scripts: --rail must be a bare file name, got {args.rail!r}",
              file=sys.stderr)
        return EXIT_USAGE

    link = checkout / "scripts"
    info = classify(link)
    target: Path | None = None
    refusal: str | None = None
    applied = False
    try:
        target = resolve_target(args.target)
    except Refused as exc:
        refusal = str(exc)

    repair_needed = False
    not_repairable: str | None = None
    if info["state"] in (STATE_REAL_DIR, STATE_FILE):
        # A real directory / a regular file is LEGITIMATE (a hand-built lane
        # tree), so it must not decide the RAIL verdict of a read-only dry run:
        # this used to set `refusal` unconditionally, which made a plain
        # `uv run python tools/link_scripts.py` over a lane tree report REFUSED
        # / exit 2 even when `scripts/admin-merge.sh` resolved fine. The refusal
        # belongs to the only action that would clobber it — `--apply`.
        not_repairable = f"{link} is a {info['state']} — refusing to replace it"
        if args.apply:
            refusal = refusal or not_repairable
    elif target is not None:
        repair_needed = needs_repair(info, target)
        if repair_needed and args.apply:
            try:
                repair(link, target)
            except Refused as exc:
                refusal = str(exc)
            else:
                applied = True
                info = classify(link)
                # Recompute: after a successful repair the `repair:` dimension is
                # SATISFIED, so exit 0 stays one reading (#9564 review P2).
                repair_needed = needs_repair(info, target)

    rail = rail_verdict(checkout, args.rail)
    # #9564 review P2: exit 0 means BOTH "the shared root is in place" and "the
    # rail resolves". `repair_needed` used to be ignored, so a `scripts` link to
    # a stale-but-live directory that happens to hold the rail printed
    # `repair: NEEDED` and still exited 0 — two meanings for one code.
    if refusal:
        exit_code = EXIT_REFUSED
    elif repair_needed or not rail["reachable"]:
        exit_code = EXIT_UNREACHABLE
    else:
        exit_code = EXIT_REACHABLE

    if args.json:
        print(json.dumps({
            "checkout": str(checkout),
            "target": str(target) if target else None,
            "scripts": info,
            "repair_needed": repair_needed,
            "repairable": not_repairable is None,
            "repair_note": not_repairable,
            "applied": applied,
            "rail": rail,
            "refusal": refusal,
            "exit": exit_code,
        }, indent=2))
    else:
        _render(checkout, target, info, repair_needed, applied, rail, refusal,
                not_repairable)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())

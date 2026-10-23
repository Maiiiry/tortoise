#!/usr/bin/env python3
"""Build the ChatGPT-installable `.skill` archives for the docs-origin skills.

Why an archive at all (#9660): the ChatGPT skill uploader accepts ONLY a file
named `SKILL.md`, a `.zip`, or a `.skill` (verified in the live UI — a file
named `tortoise-file-finding.md` is refused: "Choose a .zip, .skill, or
SKILL.md file"). Four skills therefore cannot ship as four `SKILL.md`
downloads: they collide in the user's Downloads folder, and the renamed copies
are rejected. A `.skill` archive carries the skill folder inside it, so each
file downloads with its own name and uploads as-is.

One archive = one skill: a `.zip` holding several skill folders is rejected by
the uploader ("Couldn't upload this skill"). Do not merge them.

Layout inside the archive mirrors the uploader's expectation — the skill's root
directory is named after the skill and holds `SKILL.md`:

    <name>.skill
      └── <name>/SKILL.md

The archives are derived artifacts (checked in because the docs site has no
build step); `tests/test_onboarding_variants.py` fails if one drifts from its
source `SKILL.md`.

Usage:
    python3 tools/build_skill_archives.py            # write changed archives
    python3 tools/build_skill_archives.py --check    # exit 1 if any is stale
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "website" / "skills"

#: Every skill the ChatGPT card hands over. Keep in step with
#: tests/test_onboarding_variants.py::SERVED_SKILL_NAMES + tortoise-onboarding.
SKILL_NAMES = (
    "how-to-use-tortoise",
    "tortoise-decide",
    "tortoise-file-finding",
    "tortoise-onboarding",
)

#: A fixed timestamp keeps the archive byte-stable, so regenerating without a
#: source change produces no diff.
_FIXED_DATE = (1980, 1, 1, 0, 0, 0)


def archive_path(name: str) -> Path:
    return SKILLS_DIR / f"{name}.skill"


def source_path(name: str) -> Path:
    return SKILLS_DIR / name / "SKILL.md"


def build(name: str) -> bytes:
    """Bytes of the `.skill` archive for `name` (a zip with ONE entry)."""
    src = source_path(name)
    if not src.is_file():
        raise SystemExit(f"missing source skill: {src}")
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        info = zipfile.ZipInfo(f"{name}/SKILL.md", date_time=_FIXED_DATE)
        info.external_attr = 0o644 << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(info, src.read_bytes())
    return buf.getvalue()


def main(argv: list[str]) -> int:
    check = "--check" in argv
    stale: list[str] = []
    for name in SKILL_NAMES:
        target = archive_path(name)
        wanted = build(name)
        current = target.read_bytes() if target.is_file() else None
        if current == wanted:
            continue
        stale.append(name)
        if not check:
            target.write_bytes(wanted)
    for name in stale:
        print(f"{'stale' if check else 'wrote'}: {archive_path(name).name}")
    if check and stale:
        print("run: python3 tools/build_skill_archives.py", file=sys.stderr)
        return 1
    if not stale:
        print(f"all {len(SKILL_NAMES)} archives are current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

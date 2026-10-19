"""Sealed ask corpus access — fixtures, SEALED gold, and the fixture/gold hash.

Two access surfaces, deliberately split so the sealing is structural rather
than a convention (issue #9097, epic #2281 D4 → extend W2-b):

* **runner side** — :func:`load_fixtures` returns the corpus the instrument
  ingests. It carries the planted claim texts (it must: they ARE the corpus the
  distilled arm writes) and the question, and it carries **no grading key**.
  A fixture that grows an ``answer``/``gold`` field fails
  :func:`fixture_issues`, so the run path cannot start reading the key by
  accident.
* **scorer side** — :func:`load_gold` / :func:`load_gold_all` return the SEALED
  key (what a correct answer must say). Only the post-run scorer calls them;
  `tools/longmem_eval/ask_sealed.get_sealed_run` never does, and
  `tests/test_ask_sealed_benchmark.py` proves it by making the gold loader
  explode for the duration of a run.

``fixtures_hash`` covers fixtures AND gold, so a gold-only edit invalidates a
published number exactly as a fixture edit does (the W2-b discipline: the hash
is the pre-registration's corpus identity, and a mismatch is `inconclusive`,
never a silent re-bless).

Hermetic: pure stdlib.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ASK_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = ASK_DIR / "fixtures"
GOLD_DIR = ASK_DIR / "gold"
MANIFEST_PATH = ASK_DIR / "_manifest.json"

FIXTURE_GLOB = "fixtures/*.json"
GOLD_GLOB = "gold/*.gold.json"

#: Case-class vocabulary. `knowledge-update` is the D7/D8 class (a planted
#: old→new pair); `verbatim` is the no-regression control (a question whose
#: answer is stated verbatim in a turn — adding the distilled corpus must not
#: make the reader worse at recalling what was actually said).
CLASS_KNOWLEDGE_UPDATE = "knowledge-update"
CLASS_VERBATIM = "verbatim"
CASE_CLASSES = (CLASS_KNOWLEDGE_UPDATE, CLASS_VERBATIM)

#: Fields that belong to the SEALED side only. A fixture carrying any of them
#: would hand the runner the grading key.
GOLD_ONLY_FIELDS = ("answer", "current_state", "verbatim_answer", "gold")

#: Fields that belong to the RUNNER side only (the corpus the reader is shown).
#: A gold file carrying them would be a second copy of the corpus, and the two
#: copies could drift.
CORPUS_ONLY_FIELDS = ("haystack_sessions", "haystack_session_ids",
                      "haystack_dates", "old_claim", "new_claim")


def _sha256_bytes(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, payload: Any) -> None:
    """Deterministic bytes: sorted keys, 2-space indent, trailing newline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def fixture_path(case_id: str, root: Path = ASK_DIR) -> Path:
    return root / "fixtures" / f"ask-{case_id}.json"


def gold_path(case_id: str, root: Path = ASK_DIR) -> Path:
    return root / "gold" / f"ask-{case_id}.gold.json"


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def corpus_file_paths(root: Path = ASK_DIR) -> list[Path]:
    """Every fixture + gold file (the `fixtures_hash` scope)."""
    return sorted(list((root / "fixtures").glob("*.json"))
                  + list((root / "gold").glob("*.gold.json")))


def case_ids(root: Path = ASK_DIR) -> list[str]:
    """Case ids from the fixture files (`ask-<case_id>.json` → `<case_id>`)."""
    return sorted(p.name[len("ask-"):-len(".json")]
                  for p in (root / "fixtures").glob("*.json"))


# ── runner side (never touches the sealed key) ─────────────────────────────

def load_fixture(case_id: str, root: Path = ASK_DIR) -> dict:
    return read_json(fixture_path(case_id, root))


def load_fixtures(root: Path = ASK_DIR) -> list[dict]:
    """The whole corpus as the runner sees it, sorted by case id.

    ⛔ This function must stay free of the sealed key: the run path's only
    input is this corpus, and `fixture_issues` is what keeps it that way.
    """
    return [load_fixture(case_id, root) for case_id in case_ids(root)]


def fixture_issues(fixture: dict) -> list[str]:
    """Why this fixture may not be handed to a runner (empty = it may)."""
    issues: list[str] = []
    for field in GOLD_ONLY_FIELDS:
        if field in fixture:
            issues.append(
                f"{fixture.get('case_id')}: fixture carries the SEALED field "
                f"{field!r} — the runner's input must not contain the grading "
                "key (sealed-gold discipline)")
    case_class = fixture.get("case_class")
    if case_class not in CASE_CLASSES:
        issues.append(
            f"{fixture.get('case_id')}: case_class {case_class!r} is not one "
            f"of {list(CASE_CLASSES)}")
    for key in ("case_id", "question", "question_date", "question_type",
                "haystack_sessions", "haystack_session_ids", "haystack_dates",
                "old_claim", "new_claim"):
        if key not in fixture:
            issues.append(f"{fixture.get('case_id')}: missing {key!r}")
    old = fixture.get("old_claim") or {}
    new = fixture.get("new_claim") or {}
    if old.get("text") and new.get("text") and old.get("text") == new.get("text"):
        issues.append(
            f"{fixture.get('case_id')}: old_claim and new_claim are identical — "
            "there is no knowledge update to plant")
    if str(old.get("valid_from") or "") >= str(new.get("valid_from") or ""):
        issues.append(
            f"{fixture.get('case_id')}: old_claim.valid_from must precede "
            "new_claim.valid_from")
    return issues


# ── scorer side (the sealed key) ──────────────────────────────────────────

def load_gold(case_id: str, root: Path = ASK_DIR) -> dict:
    """The SEALED grading key for one case. Scorer-side only."""
    return read_json(gold_path(case_id, root))


def load_gold_all(root: Path = ASK_DIR) -> dict[str, dict]:
    return {case_id: load_gold(case_id, root) for case_id in case_ids(root)}


def gold_issues(gold: dict) -> list[str]:
    issues: list[str] = []
    if not str(gold.get("answer") or "").strip():
        issues.append(f"{gold.get('case_id')}: gold carries no answer")
    for field in CORPUS_ONLY_FIELDS:
        if field in gold:
            issues.append(
                f"{gold.get('case_id')}: gold carries the corpus field "
                f"{field!r} — the sealed key must not duplicate the corpus")
    if gold.get("case_class") not in CASE_CLASSES:
        issues.append(
            f"{gold.get('case_id')}: case_class {gold.get('case_class')!r} is "
            f"not one of {list(CASE_CLASSES)}")
    return issues


# ── corpus identity (the pre-registration's pinned bytes) ─────────────────

def compute_fixtures_hash(root: Path = ASK_DIR) -> str:
    """sha256 over every fixture + gold file, path-prefixed, sorted.

    Scope is fixture AND gold: a gold-only edit changes the hash and therefore
    invalidates a published number (the W2-b rule, and the reason this tree
    keeps its own manifest instead of sharing write-path's).
    """
    digests = [(rel, _sha256_file(root / rel))
               for rel in (_relative(p, root) for p in corpus_file_paths(root))]
    payload = "\n".join(f"{rel}:{digest}"
                        for rel, digest in sorted(digests)).encode("utf-8")
    return _sha256_bytes(payload)


def load_manifest(root: Path = ASK_DIR) -> dict:
    return read_json(root / "_manifest.json")


def verify_manifest(root: Path = ASK_DIR) -> dict:
    """Verify `_manifest.json` against the on-disk fixture + gold files.

    Returns ``{ok, missing, extra, mismatched, malformed, fixtures_hash}``.
    ``ok`` is true only when the manifest covers EXACTLY the fixture + gold
    files with matching digests and its ``fixtures_hash`` recomputes.
    """
    actual_hash = compute_fixtures_hash(root)
    manifest = load_manifest(root)
    if not isinstance(manifest, dict):
        return {"ok": False, "missing": [], "extra": [], "mismatched": [],
                "malformed": "manifest document is not an object",
                "fixtures_hash": actual_hash}
    manifest_files = manifest.get("files")
    if not isinstance(manifest_files, dict):
        return {"ok": False, "missing": [], "extra": [], "mismatched": [],
                "malformed": "manifest.files is not an object",
                "fixtures_hash": actual_hash}
    on_disk = {_relative(p, root): p for p in corpus_file_paths(root)}
    missing = sorted(rel for rel in manifest_files if rel not in on_disk)
    extra = sorted(rel for rel in on_disk if rel not in manifest_files)
    mismatched = sorted(
        rel for rel, path in on_disk.items()
        if rel in manifest_files and manifest_files[rel] != _sha256_file(path))
    expected = manifest.get("fixtures_hash")
    ok = (not missing and not extra and not mismatched
          and bool(expected) and expected == actual_hash)
    return {"ok": ok, "missing": missing, "extra": extra,
            "mismatched": mismatched, "malformed": None,
            "fixtures_hash": actual_hash}

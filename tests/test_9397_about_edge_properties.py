"""#9397 — the "property-free `aboutObject` edge" evidence is FALSE, and the ruling leans on it.

THE DEFECT THIS PINS
--------------------
The owner's 2026-09-23 ruling on stored-vs-derived (`#7948`) rests on a
*code-level* claim recorded in two live architecture documents:

  * `STORAGE-ARCHITECTURE.md` §13 — *"**no** `aboutObject` edge carries **any
    property** … we lose nothing semantically by deriving later"*;
  * `STORAGE-ARCHITECTURE.md` §11.1 — *"Our `aboutObject` links are
    **property-free**: the write path emits `MERGE (s)-[:aboutObject]->(t)` and
    **never a `SET`**"*;
  * and the same fact in `EXTRACTOR-V4-ARCHITECTURE.md` §13.8 q2 and the
    decision row at `EXTRACTOR-V4-ARCHITECTURE-CHANGELOG.md`.

At HEAD an `about*` edge carries `confidence`, written by **three** shipped
sites, and the ingestion path reaches one of them by default:

  1. `session_link.link_entity` — `MERGE (s)-[r:{edge_type}]->(t)
     SET r.confidence=$conf` (`edge_type` defaults to `aboutObject`);
  2. `subject_binding.bind_point_subjects` — `SET r.confidence=$conf` on an
     already-present about edge; the `object` slot role binds via `aboutObject`;
  3. `projection/entities._fold_entity_linked_reason` — the REPLAY fold
     re-SETs it, i.e. the property is journaled and reproduced (`live == rebuild`).

ROOT CAUSE OF THE FALSE CLAIM — the method, not the measurement
---------------------------------------------------------------
The evidence was gathered with a Cypher **property-MAP** pattern,
``rg -n 'aboutObject \\{'``. A property can also be written by a
``SET r.<prop>`` clause on a relationship variable, and that form cannot match
a search for ``aboutObject {…}``. So the search returned zero — and a zero from
a pattern that structurally cannot match the writer was recorded as a measured
absence. That is why this file checks the *writer census*, not another grep.

WHAT IS PINNED
--------------
1. `test_the_absolute_property_free_sentences_do_not_reappear` — the retracted
   sentences are matched as literals against the live documents. This is the
   non-vacuity half: all of them were present before this fix.
2. `test_the_corrected_passages_name_the_property_bearing_class` — the
   corrected passages must POSITIVELY name the writers and the property, so a
   silent revert reds here.
3. `test_the_code_still_agrees_with_the_corrected_evidence` — each recorded
   writer still writes `confidence` onto an `about*` edge. If that code is
   removed, the document has re-rotted in the other direction and this reds
   instead of the doc quietly lying again.
4. `test_no_unrecorded_property_bearing_about_edge_writer` — the census half:
   every relationship-variable `SET <var>.<prop>` inside a function that builds
   an `about*` edge must be one of the recorded writers. A NEW property-bearing
   about-edge writer cannot appear without being recorded.
5. `test_the_default_ingestion_path_reaches_the_binder` — the corrected text
   says "default-ON, ingestion path"; if `_extract_session_v2` stops calling
   the binder, that sentence becomes false while every other test stays green.

NOT promised: this does not re-open stored-vs-derived (the ruling stands, and
the edge stays), and it does not decide §11.4 rule 1's tension with `#1370`
(that is a design question, recorded, not resolved here). Line numbers are not
asserted — the SYMBOL is authoritative, per the documents' own rule.

Static, stdlib-only. No DB, no graph, no network.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "tortoise"
STORAGE = ROOT / "docs" / "architecture" / "STORAGE-ARCHITECTURE.md"
EXTRACTOR = ROOT / "docs" / "architecture" / "EXTRACTOR-V4-ARCHITECTURE.md"
EXTRACTOR_CHANGELOG = (
    ROOT / "docs" / "architecture" / "EXTRACTOR-V4-ARCHITECTURE-CHANGELOG.md"
)
STORAGE_CHANGELOG = (
    ROOT / "docs" / "architecture" / "STORAGE-ARCHITECTURE-CHANGELOG.md"
)

#: The sentences that were TRUE only because the search could not see a `SET`.
#: Each was live in the pre-fix documents (this file is red on them).
RETRACTED: dict[str, str] = {
    "§11.1 calls the links property-free and reports a zero search": (
        "Our `aboutObject` links are **property-free**"
    ),
    "§13 closes the structural door with 'any property'": (
        "**no** `aboutObject` edge carries **any property**"
    ),
    "§13's inference of semantic freedom": (
        "we lose nothing semantically by deriving later"
    ),
    "§11.1's cost sentence presents the zero as measured": (
        "a full-repo search for a property-bearing edge finds **none**"
    ),
    "§13.8 q2 repeats the property-free claim": (
        "no `aboutObject` edge carries any property"
    ),
    "the decision ledger's reason is property-freedom": (
        "Cheap because edges are **property-free**"
    ),
}

#: The writers the corrected passages must name, and the code shape that makes
#: each one a property-bearing `about*` writer.
RECORDED_WRITERS: dict[tuple[str, str], tuple[str, ...]] = {
    ("tortoise/session_link.py", "link_entity"): (
        'MERGE (s)-[r:{edge_type}]->(t) SET r.confidence=$conf',
    ),
    ("tortoise/subject_binding.py", "bind_point_subjects"): (
        'SET r.confidence=$conf',
        '"object": ("Object", "aboutObject")',
    ),
    ("tortoise/projection/entities.py", "_fold_entity_linked_reason"): (
        'MERGE (s)-[e:{rel}]->(t) SET e.confidence=$conf',
    ),
}

#: The property. One name, because a second one would need its own row.
PROPERTY = "confidence"

_SET_ON = re.compile(r"SET\s+(\w+)\.(\w+)\s*=")
#: A relationship pattern, captured as (variable, relationship-token).
_PATTERN = re.compile(r"\[(\w+):([^\]]{0,120})\]")
_ABOUT_REL = re.compile(
    r"about(Subject|Object|Event|Point|Document|Action|Source)"
)
#: The shared about-edge vocabularies. A relationship name built by INTERPOLATION
#: is about-bearing only when the writer validates/derives it from one of these
#: (`link_entity` → `ENTITY_LINKED_RELS`, the replay fold → the record's `rel`,
#: the binder → `_ROLE_TARGET`). Without this clause the census also catches
#: unrelated interpolated writes (`sdk.ingest`'s `IMPL|NAND|hasPart` `r.idx`),
#: and a false positive is how a guard gets deleted instead of fixed.
_ABOUT_VOCAB: tuple[str, ...] = (
    "ENTITY_LINKED_RELS",
    "_ABOUT_REPLAY_RELS",
    "_VALID_ABOUT_EDGE_PREDICATES",
    "ABOUT_EDGE_TYPES",
    "_ABOUT_TYPES",
    "STRUCTURAL_REL_LABELS",
    "_ROLE_TARGET",
)


def _doc(path: Path) -> str:
    assert path.exists(), f"{path} is missing"
    return path.read_text(encoding="utf-8")


def _own_body(node: ast.AST, lines: list[str]) -> str:
    """The node's source with any NESTED definition blanked out."""
    start = node.lineno - 1  # type: ignore[attr-defined]
    end = node.end_lineno or node.lineno  # type: ignore[attr-defined]
    seg = list(lines[start:end])
    for child in ast.walk(node):
        if child is node or not isinstance(
            child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        for i in range(child.lineno - 1, child.end_lineno or child.lineno):
            if start <= i < end:
                seg[i - start] = ""
    return "\n".join(seg)


def _qualified(tree: ast.Module):
    def walk(node: ast.AST, prefix: str = ""):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield prefix + child.name, child
                yield from walk(child, prefix + child.name + ".")
            elif isinstance(child, ast.ClassDef):
                yield from walk(child, prefix + child.name + ".")

    yield from walk(tree)


def _about_edge_property_writers() -> dict[tuple[str, str], set[str]]:
    """{(module, symbol): {property, …}} for every property-bearing about-edge write."""
    found: dict[tuple[str, str], set[str]] = {}
    for path in sorted(PKG.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "about" not in text:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:  # pragma: no cover — a broken module fails elsewhere
            continue
        lines = text.splitlines()
        module = path.relative_to(ROOT).as_posix()
        for qualname, node in _qualified(tree):
            body = _own_body(node, lines)
            about_vars: set[str] = set()
            interpolated = "{" in body and any(
                vocab in body for vocab in _ABOUT_VOCAB
            )
            for var, rel in _PATTERN.findall(body):
                if _ABOUT_REL.search(rel) or ("{" in rel and interpolated):
                    about_vars.add(var)
            if not about_vars:
                continue
            for var, prop in _SET_ON.findall(body):
                if var in about_vars:
                    found.setdefault(
                        (module, qualname.split(".")[-1]), set()
                    ).add(prop)
    return found


# ── 1. the retracted sentences ─────────────────────────────────────────────


def test_the_absolute_property_free_sentences_do_not_reappear():
    #: The LIVE design documents. A retracted absolute claim may not survive in
    #: them — not even as a quotation, which is the prior art's rule in
    #: `tests/test_extractor_v4_citation_rot.py` (the retracted text belongs in
    #: the TEST and in the CHANGELOG's correction entry, not in the design doc,
    #: where a reader would take it as current).
    live = {
        "STORAGE-ARCHITECTURE.md": _doc(STORAGE),
        "EXTRACTOR-V4-ARCHITECTURE.md": _doc(EXTRACTOR),
    }
    #: The decision ledger's own reason sentence is a live claim too, so it is
    #: checked — but only for ITS literal, because the same file legitimately
    #: quotes the retracted sentences inside this correction's entry.
    ledger = _doc(EXTRACTOR_CHANGELOG)
    survivors = [
        f"{label}: {sentence!r}"
        for label, sentence in RETRACTED.items()
        for name, text in live.items()
        if sentence in text
    ]
    if RETRACTED["the decision ledger's reason is property-freedom"] in ledger:
        survivors.append(
            "EXTRACTOR-V4-ARCHITECTURE-CHANGELOG.md: the `aboutObject` decision "
            "row still gives property-freedom as its reason"
        )
    assert not survivors, (
        "a retracted 'property-free aboutObject edge' claim is live again:\n  "
        + "\n  ".join(survivors)
        + "\n\nAn `about*` edge carries `confidence` "
        f"({', '.join(f'{m}::{s}' for m, s in sorted(RECORDED_WRITERS))}). "
        "The claim was recorded from a search (`rg 'aboutObject {'`) that "
        "structurally cannot see a `SET r.<prop>` writer — re-deriving it with "
        "the same pattern would reproduce the defect."
    )


# ── 2. the corrected passages must say what is true ────────────────────────


def test_the_corrected_passages_name_the_property_bearing_class():
    storage = _doc(STORAGE)
    extractor = _doc(EXTRACTOR)
    for name, text in (
        ("STORAGE-ARCHITECTURE.md", storage),
        ("EXTRACTOR-V4-ARCHITECTURE.md", extractor),
    ):
        for symbol in ("link_entity", "bind_point_subjects"):
            assert symbol in text, (
                f"{name} does not name `{symbol}` — the corrected evidence must "
                "name the writers that put a property on an about edge"
            )
        assert f"`{PROPERTY}`" in text or f"r.{PROPERTY}" in text, (
            f"{name} does not name the `{PROPERTY}` property — the claim "
            "'property-free' is replaced by a named, bounded exception, not by "
            "silence"
        )
    # The surviving true half must stay stated: the GENERIC writer sets nothing.
    assert "create_about_edge" in storage, (
        "STORAGE-ARCHITECTURE.md no longer distinguishes the generic writer "
        "(`create_about_edge`, no SET) from the binder's property-bearing one"
    )


# ── 3. the code must still agree with the corrected evidence ───────────────


def test_the_code_still_agrees_with_the_corrected_evidence():
    writers = _about_edge_property_writers()
    stale: list[str] = []
    for key, shapes in RECORDED_WRITERS.items():
        module, symbol = key
        text = (ROOT / module).read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
        except SyntaxError:  # pragma: no cover
            stale.append(f"{module}::{symbol} — module does not parse")
            continue
        if not any(True for name, node in _qualified(tree)
                   if name.split(".")[-1] == symbol):
            stale.append(f"{module}::{symbol} — symbol no longer exists")
            continue
        for shape in shapes:
            if shape not in text:
                stale.append(f"{module}::{symbol} — lost {shape!r}")
        if PROPERTY not in writers.get(key, set()):
            stale.append(
                f"{module}::{symbol} — no longer SETs `{PROPERTY}` on a "
                "relationship variable"
            )
    assert not stale, (
        "the CODE moved away from the corrected evidence — re-derive the "
        "documented claim instead of leaving it stale:\n  " + "\n  ".join(stale)
    )


# ── 4. no unrecorded property-bearing writer ───────────────────────────────


def test_no_unrecorded_property_bearing_about_edge_writer():
    writers = _about_edge_property_writers()
    assert writers, (
        "the census found no property-bearing about-edge writer at all — the "
        "relationship-variable/SET rule or the about* filter was changed, and "
        "this guard has stopped measuring anything"
    )
    unrecorded = sorted(
        f"{module}::{symbol} SETs {sorted(props)}"
        for (module, symbol), props in writers.items()
        if (module, symbol) not in RECORDED_WRITERS
    )
    assert not unrecorded, (
        "these functions put a property on an `about*` edge and are recorded "
        "nowhere:\n  " + "\n  ".join(unrecorded)
        + "\nRecord each one in the documents' corrected evidence (and in "
        "RECORDED_WRITERS here), because the ruling's *cost-only* argument "
        "depends on the property set being known and bounded."
    )


# ── 5. the reach claim the corrected text makes ────────────────────────────


def test_the_default_ingestion_path_reaches_the_binder():
    sdk = (PKG / "sdk.py").read_text(encoding="utf-8")
    extract = sdk[sdk.index("def _extract_session_v2"):]
    extract = extract[: extract.index("\n    def ", 10)]
    assert "bind_point_subjects(" in extract, (
        "`_extract_session_v2` no longer calls `bind_point_subjects` — the "
        "corrected evidence's reach claim ('default-ON, ingestion path') "
        "depends on this call"
    )


# ── 6. the changelogs must carry the correction, not the old claim ─────────


def test_the_storage_changelog_records_the_correction():
    changelog = _doc(STORAGE_CHANGELOG)
    assert "#9397" in changelog, (
        "STORAGE-ARCHITECTURE-CHANGELOG.md does not record this correction — "
        "the changelog is where a reader learns a §11.1/§13 claim moved, and an "
        "unrecorded correction is the drift #9397 exists to stop"
    )

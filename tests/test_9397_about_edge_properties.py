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
   sentences are matched MARKER-INSENSITIVELY (`**`, backticks, whitespace) —
   never as raw substrings, because a `**no**` bolding once made an entry here
   match nothing at all: a pin that cannot match is a pin that cannot red. Each
   was live in the pre-fix documents; the entries a reader can still find are
   quoted in the changelog, and
   `test_every_retracted_literal_is_still_findable_where_it_is_allowed_to_live`
   checks that, so a dead literal cannot hide in this dict again.
2. `test_the_corrected_passages_name_the_property_bearing_class` — the
   corrected passages must POSITIVELY name the writers and the property, so a
   silent revert reds here. Each assertion is scoped to the CORRECTED SECTION
   (§11.1, §13, the extractor's §13.8 q2 paragraph), not to the whole file: a
   file-wide membership test cannot red on a section's silent deletion — the
   lesson `config/ci-surfaces.yml` records from the `#8639` review.
3. `test_the_code_still_agrees_with_the_corrected_evidence` — each recorded
   writer still writes `confidence` onto an `about*` edge. If that code is
   removed, the document has re-rotted in the other direction and this reds
   instead of the doc quietly lying again.
4. `test_no_unrecorded_property_bearing_about_edge_writer` — the census half:
   every relationship-variable `SET <var>.<prop>` inside a function that builds
   an `about*` edge must be one of the recorded writers, so a NEW writer in that
   FORM cannot appear without being recorded. ⚠️ **The form is the limit.** A
   property written by a property MAP (`SET r += $props` / `SET r = $p`) is
   invisible to this leg, so those sites are pinned separately by
   `test_map_form_relationship_writes_are_a_recorded_residual`: they are
   GENERIC (the property set is the caller's or the record row's, on any
   allowlisted predicate, `about*` included), which is why they are a recorded
   residual rather than a second *named* about-edge property.
5. `test_the_default_ingestion_path_reaches_the_binder` — the corrected text
   says "default-ON, ingestion path"; if `_extract_session_v2` stops calling
   the binder, **or stops being the fall-through branch** of the
   `TORTOISE_SESSION_EXTRACTOR == "m2"` selection, that sentence becomes false
   while every other test stays green.

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
#: Each was live in the pre-fix documents (this file is red on them). One
#: sentence was said twice — bolded in §13, unbolded in the extractor's §13.8
#: q2 and in §11.3 — and matching is marker-insensitive, so it is ONE entry.
RETRACTED: dict[str, str] = {
    "§11.1 calls the links property-free and reports a zero search": (
        "Our `aboutObject` links are **property-free**"
    ),
    "the 'no property on an aboutObject edge' absolute (§13, §13.8 q2)": (
        "**no** `aboutObject` edge carries **any property**"
    ),
    "§13's inference of semantic freedom": (
        "we lose nothing semantically by deriving later"
    ),
    "§11.1's cost sentence presents the zero as measured": (
        "a full-repo search for a property-bearing edge finds **none**"
    ),
    "§11.2's cost footnote calls the retracted finding measured": (
        "from the measured property-free finding"
    ),
    "§11.3's table states the retracted absolute as the cost basis": (
        "**property-free rows**"
    ),
    "§11.3's prose states the retracted absolute about both predicates": (
        "the entity links stay bare"
    ),
    "the decision ledger's reason is property-freedom": (
        "Cheap because edges are **property-free**"
    ),
}

#: Where each retracted sentence is still quoted, once the corrected live docs no
#: longer carry it: the APPEND-ONLY changelog is the one place it is allowed to
#: live. `test_every_retracted_literal_is_still_findable_where_it_is_allowed_to_live`
#: requires each of these to be findable there (marker-insensitively), because a
#: literal that matches nothing is a dead pin — and this dict already shipped one.
RETRACTED_QUOTED_IN: dict[str, Path] = {
    "§11.1 calls the links property-free and reports a zero search": (
        STORAGE_CHANGELOG
    ),
    "the 'no property on an aboutObject edge' absolute (§13, §13.8 q2)": (
        STORAGE_CHANGELOG
    ),
    "§13's inference of semantic freedom": STORAGE_CHANGELOG,
    "§11.1's cost sentence presents the zero as measured": STORAGE_CHANGELOG,
    "§11.2's cost footnote calls the retracted finding measured": (
        STORAGE_CHANGELOG
    ),
    "§11.3's table states the retracted absolute as the cost basis": (
        STORAGE_CHANGELOG
    ),
    "§11.3's prose states the retracted absolute about both predicates": (
        STORAGE_CHANGELOG
    ),
}

#: The one retracted sentence that this correction REWROTE rather than quoted, so
#: it has no home to be found in today: its pre-fix form is the extractor ledger's
#: `aboutObject` decision row (`git show <merge-base>:docs/architecture/`
#: `EXTRACTOR-V4-ARCHITECTURE-CHANGELOG.md`). Listed so that EVERY entry above is
#: either checkable (``RETRACTED_QUOTED_IN``) or declared un-checkable, instead of
#: silently being neither.
RETRACTED_PRE_FIX_ONLY: frozenset[str] = frozenset({
    "the decision ledger's reason is property-freedom",
})

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
#: The OTHER form a property is written in: a property MAP on a relationship
#: variable. `_SET_ON` cannot see it — which is the same "the pattern cannot match
#: the writer" defect this whole file exists to correct, one level down.
_MAP_SET_ON = re.compile(r"SET\s+(\w+)\s*(?:\+=|=)\s*(?:\$\w+|properties\(|\{)")
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

#: The three corrected passages, as (label, doc, start marker, end marker, the
#: literals that must survive INSIDE the slice). Section-scoped on purpose: the
#: same symbol names appear in the other corrected section of the same file, so a
#: whole-file membership test cannot red on one passage's silent deletion.
CORRECTED_PASSAGES: tuple[tuple[str, Path, str, str, tuple[str, ...]], ...] = (
    (
        "STORAGE-ARCHITECTURE.md §11.1",
        STORAGE,
        "\n### 11.1 ",
        "\n### 11.2 ",
        (
            "link_entity",
            "bind_point_subjects",
            "_fold_entity_linked_reason",
            f"`{PROPERTY}`",
            # The surviving true half: the GENERIC writer sets nothing.
            "create_about_edge",
        ),
    ),
    (
        "STORAGE-ARCHITECTURE.md §13",
        STORAGE,
        "\n## 13. ",
        "\n## 14. ",
        (
            "link_entity",
            "bind_point_subjects",
            "_fold_entity_linked_reason",
            f"`{PROPERTY}`",
            "create_about_edge",
        ),
    ),
    (
        "EXTRACTOR-V4-ARCHITECTURE.md §13.8 q2",
        EXTRACTOR,
        "\n2. **Should `aboutObject` be a stored edge or a derived join?**",
        "\n3. **Should every expansion have a hard fan-out cap**",
        ("link_entity", "bind_point_subjects", f"`{PROPERTY}`"),
    ),
)

#: Relationship-variable property-MAP writes (`SET r += $props`, `SET r = $p`,
#: `ON CREATE SET r2 = properties(r)`) — the form `_SET_ON` cannot see, and the
#: reason the census above is a bound on ONE form rather than on "properties on
#: about edges". Every recorded site is GENERIC: the property set is the caller's
#: or the record row's, applied to whatever allowlisted predicate it is handed
#: (`about*` included), so it is a bounded residual of the cost argument rather
#: than a second *named* property. A NEW one reds this leg — the pattern that
#: cannot see a writer must never again be the reason a writer goes unrecorded.
MAP_FORM_RESIDUAL: frozenset[tuple[str, str]] = frozenset({
    ("tortoise/cache_projection.py", "relation_statement"),
    ("tortoise/hosted_backup.py", "restore_graph"),
    ("tortoise/projection/__init__.py", "_rebuild_all_replay"),
    ("tortoise/sdk.py", "supersede_point"),
    ("tortoise/sdk.py", "create_direct_edge"),
    ("tortoise/session_source_migration.py", "_move_out"),
    ("tortoise/session_source_migration.py", "_move_in"),
})


def _doc(path: Path) -> str:
    assert path.exists(), f"{path} is missing"
    return path.read_text(encoding="utf-8")


_MARKUP = re.compile(r"[*`]")
_WHITESPACE = re.compile(r"\s+")


def _flat(text: str) -> str:
    """Marker-insensitive form of a sentence, for pinning RETRACTED literals.

    `**no** `aboutObject` edge carries any property` and the unbolded variant are
    the SAME retracted claim; matching raw substrings made one of them a dead pin
    that could never red (it shipped that way once). Emphasis and code markers
    are decoration on the claim, not part of it.
    """
    return _WHITESPACE.sub(" ", _MARKUP.sub("", text))


def _section(text: str, start: str, end: str, label: str) -> str:
    """One section slice, with BOTH markers and real content asserted.

    A correction can only be pinned inside the passage that carries it: the same
    literals appear elsewhere in a 1,400-line document, so a whole-file
    membership test stays green while the corrected passage is deleted.
    """
    assert start in text, (
        f"{label}: the section heading {start.strip()!r} is gone — the passage "
        "this guard is scoped to no longer exists"
    )
    body = text[text.index(start):]
    assert end in body, (
        f"{label}: {end.strip()!r} is gone, so the passage cannot be bounded — "
        "the document was restructured and this guard must be re-aimed, not "
        "left measuring the rest of the file"
    )
    body = body[: body.index(end)]
    assert len(body) > 200, (
        f"{label}: the slice is {len(body)} characters — the passage is empty, "
        "so every assertion below would be vacuous"
    )
    return body


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


def _map_form_relationship_writes() -> dict[tuple[str, str], set[str]]:
    """{(module, symbol): {"r += $props", …}} for property-MAP writes on an edge.

    Deliberately NOT filtered by `about*`: the relationship name at these sites is
    a caller's or a record row's, so the predicate is not visible in the text at
    all — which is exactly why they cannot be classified as "about" or "not".
    """
    found: dict[tuple[str, str], set[str]] = {}
    for path in sorted(PKG.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "SET" not in text:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:  # pragma: no cover — a broken module fails elsewhere
            continue
        lines = text.splitlines()
        module = path.relative_to(ROOT).as_posix()
        for qualname, node in _qualified(tree):
            body = _own_body(node, lines)
            rel_vars = {var for var, _rel in _PATTERN.findall(body)}
            if not rel_vars:
                continue
            for match in _MAP_SET_ON.finditer(body):
                var = match.group(1)
                if var in rel_vars:
                    found.setdefault((module, qualname.split(".")[-1]), set()).add(
                        f"SET {var} <map>"
                    )
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
        if _flat(sentence) in _flat(text)
    ]
    if _flat(RETRACTED["the decision ledger's reason is property-freedom"]) in _flat(ledger):
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
    for label, path, start, end, required in CORRECTED_PASSAGES:
        passage = _section(_doc(path), start, end, label)
        for literal in required:
            assert literal in passage, (
                f"{label} no longer names `{literal}` — the corrected passage "
                "must POSITIVELY name the writers and the property, so that "
                "deleting the correction reds here instead of leaving the "
                "false 'property-free' claim to be re-derived"
            )


def test_every_retracted_literal_is_still_findable_where_it_is_allowed_to_live():
    #: A literal that matches nothing can never red — and this dict shipped one
    #: (`**no**` was unpinned, so restoring the whole retracted sentence into the
    #: extractor doc left six legs green). Every entry must therefore be either
    #: findable in the changelog that quotes it, or declared pre-fix-only.
    assert set(RETRACTED) == set(RETRACTED_QUOTED_IN) | set(RETRACTED_PRE_FIX_ONLY), (
        "a RETRACTED entry is classified nowhere: it is neither quoted in a "
        "changelog (RETRACTED_QUOTED_IN) nor declared pre-fix-only "
        "(RETRACTED_PRE_FIX_ONLY), so nothing proves it can ever match text"
    )
    dead = [
        f"{label}: {sentence!r} is not in {path.name}"
        for label, sentence in RETRACTED.items()
        if label in RETRACTED_QUOTED_IN
        for path in [RETRACTED_QUOTED_IN[label]]
        if _flat(sentence) not in _flat(_doc(path))
    ]
    assert not dead, (
        "these retracted sentences are pinned but findable nowhere — a pin that "
        "cannot match the pre-fix text cannot red on a re-rot:\n  "
        + "\n  ".join(dead)
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


# ── 5. the other property form: a MAP on a relationship variable ────────────


def test_map_form_relationship_writes_are_a_recorded_residual():
    found = _map_form_relationship_writes()
    assert found, (
        "the map-form census found no relationship-variable `SET <var> += $props` "
        "at all — the pattern or the relationship-pattern rule was changed and "
        "this leg has stopped measuring anything"
    )
    unrecorded = sorted(
        f"{module}::{symbol} ({', '.join(sorted(forms))})"
        for (module, symbol), forms in found.items()
        if (module, symbol) not in MAP_FORM_RESIDUAL
    )
    assert not unrecorded, (
        "these functions write a property MAP onto a relationship variable and "
        "are recorded nowhere:\n  " + "\n  ".join(unrecorded)
        + "\nA generic map write can land on an `about*` predicate — the edge "
        "is chosen by the caller or the record row, not by this code — so it "
        "is part of the property evidence. Record it in MAP_FORM_RESIDUAL with "
        "the reason it is generic, and re-derive the documents' property claim."
    )
    disappeared = sorted(
        f"{module}::{symbol}"
        for module, symbol in MAP_FORM_RESIDUAL
        if (module, symbol) not in found
    )
    assert not disappeared, (
        "these recorded map-form writers no longer exist — delete them from "
        "MAP_FORM_RESIDUAL in the same change, so the residual stays a "
        "measurement instead of a memory:\n  " + "\n  ".join(disappeared)
    )


# ── 6. the reach claim the corrected text makes ────────────────────────────


def test_the_default_ingestion_path_reaches_the_binder():
    sdk = (PKG / "sdk.py").read_text(encoding="utf-8")
    extract = sdk[sdk.index("def _extract_session_v2"):]
    extract = extract[: extract.index("\n    def ", 10)]
    assert "bind_point_subjects(" in extract, (
        "`_extract_session_v2` no longer calls `bind_point_subjects` — the "
        "corrected evidence's reach claim ('default-ON, ingestion path') "
        "depends on this call"
    )
    # The DEFAULT-ON half is not in `_extract_session_v2` at all: it is the
    # extractor selection. `m2` is the opt-out, and v2 is the fall-through — so
    # flipping the default to m2 would make the corrected sentence false while
    # the call above stayed in place.
    tree = ast.parse(sdk)
    dispatch = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and "TORTOISE_SESSION_EXTRACTOR" in ast.unparse(node.test)
    ]
    assert dispatch, (
        "the `TORTOISE_SESSION_EXTRACTOR` selection is gone from `sdk.py`, so "
        "the corrected evidence's 'default-ON' claim no longer has a mechanism"
    )
    selected = [node for node in dispatch if "m2" in ast.unparse(node.test)]
    assert selected, (
        "no `TORTOISE_SESSION_EXTRACTOR == 'm2'` branch remains — re-derive the "
        "corrected reach claim before deleting the opt-out it describes"
    )
    node = selected[0]
    assert "_extract_session_llm(" in ast.unparse(node.body), (
        "the `m2` branch no longer selects `_extract_session_llm`, so 'm2 is "
        "the opt-out' is stale"
    )
    assert "_extract_session_v2(" in ast.unparse(node.orelse), (
        "`_extract_session_v2` is no longer the FALL-THROUGH extractor — the "
        "corrected evidence's 'default-ON, ingestion path' sentence is false"
    )


# ── 7. the changelogs must carry the correction, not the old claim ─────────

def test_the_storage_changelog_records_the_correction():
    changelog = _doc(STORAGE_CHANGELOG)
    assert "#9397" in changelog, (
        "STORAGE-ARCHITECTURE-CHANGELOG.md does not record this correction — "
        "the changelog is where a reader learns a §11.1/§13 claim moved, and an "
        "unrecorded correction is the drift #9397 exists to stop"
    )

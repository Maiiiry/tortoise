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

At HEAD an `about*` edge carries `confidence`, ORIGINATED by **three** shipped
sites, and the ingestion path reaches one of them by default:

  1. `session_link.link_entity` — `MERGE (s)-[r:{edge_type}]->(t)
     SET r.confidence=$conf` (`edge_type` defaults to `aboutObject`);
  2. `subject_binding.bind_point_subjects` — `SET r.confidence=$conf` on an
     already-present about edge; the `object` slot role binds via `aboutObject`;
  3. `projection/entities._fold_entity_linked_reason` — the REPLAY fold
     re-SETs it, i.e. the property is journaled and reproduced (`live == rebuild`).

Four further shipped paths RE-MATERIALIZE the same attribute set by property
MAP — `SET r += $props` / `SET r = $p` — over a predicate allowlist that
includes `aboutObject`, so they reproduce `confidence` and add no new name:
`cache_projection.relation_statement` (the record-layer projection; the
`public.relation` row carries `confidence`), `projection._rebuild_all_replay`
and `sdk.supersede_point` (rebuilding/copying it from the journal or the
superseded point), and `hosted_backup.restore_graph` (`SET r = $p` over the
dump's stored edge properties).

ROOT CAUSE OF THE FALSE CLAIM — the method, not the measurement
---------------------------------------------------------------
The evidence was gathered with a Cypher **property-MAP** pattern,
``rg -n 'aboutObject \\{'``. A property can also be written by a
``SET r.<prop>`` clause on a relationship variable, and that form cannot match
a search for ``aboutObject {…}``. So the search returned zero — and a zero from
a pattern that structurally cannot match the writer was recorded as a measured
absence. That is why this file checks the *writer census*, not another grep.

THE SAME BLINDNESS, ONE LEVEL DOWN (review cycle on this file's first version)
-----------------------------------------------------------------------------
A census that reads only `SET r.<prop>` repeats the error: the property-MAP
form `SET r += $props` / `SET r = $p` is structurally invisible to it, and four
shipped sites already use that form on an allowlisted `aboutObject` predicate
(listed above). So the census reads ALL THREE write forms, and an interpolated
predicate counts as about-bearing when its writer takes it from the shared about
/ predicate vocabulary — including the allowlist itself (`validate_rel_type`,
`KNOWN_REL_TYPES`, `_sanitize_label`), because "validated against the whole
allowlist, which contains `aboutObject`" is exactly how a map-form writer
names its edge without writing `aboutObject` anywhere.

⚠️ THAT is a heuristic, and the promise it supports is bounded by it: the census
cannot see a writer whose predicate comes from a vocabulary named nowhere in
`_ABOUT_VOCAB` (or whose module `_module_in_scope` skips). The two forms that
ARE provably read — the allowlist path and the inline pattern map — are pinned
by synthetic probes; the residual gap is a list of names, and a list cannot be
checked for a token nobody has written yet. Adding a predicate vocabulary is a
change to `_ABOUT_VOCAB`, not only to the code that uses it.

WHAT IS PINNED
--------------
1. `test_the_absolute_property_free_sentences_do_not_reappear` — the retracted
   sentences are matched MARKER-INSENSITIVELY (`*`, backticks, whitespace) against
   the live documents. Each entry records the document it was measured in on the
   pre-fix tree; the first version of this file claimed all of them were live
   pre-fix and one was not (its literal was unbolded while the document bolds
   it), so the claim is now per entry — and leg 7 machine-checks that no entry is
   a dead pin by requiring it to be findable, marker-insensitively, in the
   changelog that quotes it.
2. `test_the_corrected_passages_name_the_property_bearing_class` — the
   corrected passages must POSITIVELY name the writers and the property, so a
   silent revert reds here. The assertions are SECTION-scoped (§11.1, §13,
   §13.8 q2), not file-wide: the literals appear in more than one section, so a
   file-wide membership test cannot red on one section's silent deletion
   (`#8639` review, the `test_entity_attachment_brief_claims.py` precedent).
3. `test_the_code_still_agrees_with_the_corrected_evidence` — each recorded
   writer still writes `confidence` onto an `about*` edge. If that code is
   removed, the document has re-rotted in the other direction and this reds
   instead of the doc quietly lying again.
4. `test_no_unrecorded_property_bearing_about_edge_writer` — the census half:
   every relationship-variable property write inside a function that builds an
   `about*` edge — named (`SET r.<prop>`), map-assigned (`SET r += $props` /
   `SET r = $p`), or an inline pattern map (`[r:aboutObject {idx:$idx}]`, which
   assigns with no `SET` clause at all) — must be one of the recorded writers.
   ⚠️ **The bound on that promise, stated rather than assumed:** an `about*`
   edge is recognised by an `about*` LITERAL, or by an INTERPOLATED predicate in
   a function whose body names one of the `_ABOUT_VOCAB` vocabularies (and, at
   the file-walk level, in a module `_module_in_scope` admits). A writer that
   derives its predicate from a vocabulary named NOWHERE in that list is still
   invisible — so adding a predicate vocabulary, or a new way of naming one, is
   a change to `_ABOUT_VOCAB` / `_module_in_scope`, not just a change to the
   code. What IS machine-checked is that the three shipped forms are all read
   (`test_the_census_reads_the_three_write_forms`) and that the allowlist path
   is not skipped by the file walk (`test_the_census_reads_a_writer_that_never_
   says_about`); the remaining gap is a vocabulary list, and a list cannot be
   checked for a token nobody has written yet.
5. `test_the_default_ingestion_path_reaches_the_binder` — the corrected text
   says "default-ON, ingestion path"; if `_extract_session_v2` stops calling
   the binder, **or stops being the fall-through branch** of the
   `TORTOISE_SESSION_EXTRACTOR == "m2"` selection, that sentence becomes false
   while every other test stays green.
6. `test_the_storage_changelog_records_the_correction` — the changelog is where
   a reader learns that a §11.1/§13 claim moved.
7. `test_every_retracted_literal_is_still_findable_where_it_is_allowed_to_live`
   — every RETRACTED literal is findable in the changelog that quotes it, or is
   declared pre-fix-only. A pin that cannot match the pre-fix text protects
   nothing, and that is not a hypothesis: it shipped.

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

#: label → (retracted literal, the live document it was measured in on the
#: PRE-FIX tree). The document is recorded per entry because it is a checkable
#: claim, not decoration: the `§13.8 q2` row in this file's first version pinned
#: an UNBOLDED literal where the document bolds it, so it matched nothing — a
#: dead entry that would have passed on a verbatim revert. The literal is the
#: document's own text, bold markers included.
RETRACTED: dict[str, tuple[str, str]] = {
    "§11.1 calls the links property-free and reports a zero search": (
        "Our `aboutObject` links are **property-free**",
        "STORAGE-ARCHITECTURE.md",
    ),
    "§13 closes the structural door with 'any property'": (
        "**no** `aboutObject` edge carries **any property**",
        "STORAGE-ARCHITECTURE.md",
    ),
    "§13's inference of semantic freedom": (
        "we lose nothing semantically by deriving later",
        "STORAGE-ARCHITECTURE.md",
    ),
    "§11.1's cost sentence presents the zero as measured": (
        "a full-repo search for a property-bearing edge finds **none**",
        "STORAGE-ARCHITECTURE.md",
    ),
    "§11.3's table row still calls the rows property-free": (
        "**property-free rows**",
        "STORAGE-ARCHITECTURE.md",
    ),
    "§11's estimate footnote leans on the property-free finding": (
        "the measured property-free finding",
        "STORAGE-ARCHITECTURE.md",
    ),
    "§13.8 q2 repeats the property-free claim (BOLDED, as written)": (
        "**no** `aboutObject` edge carries any property",
        "EXTRACTOR-V4-ARCHITECTURE.md",
    ),
    "§11.3's prose says the links stay bare": (
        "the entity links stay bare",
        "STORAGE-ARCHITECTURE.md",
    ),
    "the corrected claim's own 'exactly one name' absolute": (
        "The property set is exactly one name — `confidence`",
        "STORAGE-ARCHITECTURE.md",
    ),
    "§13.8 q2's 'never a second property name'": (
        "never a second property name",
        "EXTRACTOR-V4-ARCHITECTURE.md",
    ),
    "the decision ledger's reason is property-freedom": (
        "Cheap because edges are **property-free**",
        "EXTRACTOR-V4-ARCHITECTURE-CHANGELOG.md",
    ),
}

#: Where each retracted sentence is still QUOTED once the live documents no
#: longer carry it: the append-only changelog, which is the one place the
#: retracted text is allowed to live. `test_every_retracted_literal_is_still_
#: findable_where_it_is_allowed_to_live` requires each of these to be findable
#: there, MARKER-INSENSITIVELY — because a literal that matches nothing is a
#: dead pin, and this dict shipped exactly one before (the unbolded §13.8 q2
#: literal, which matched neither the pre-fix nor the fixed document).
RETRACTED_QUOTED_IN: dict[str, Path] = {
    "§11.1 calls the links property-free and reports a zero search": (
        STORAGE_CHANGELOG
    ),
    "§13 closes the structural door with 'any property'": STORAGE_CHANGELOG,
    "§13's inference of semantic freedom": STORAGE_CHANGELOG,
    "§11.1's cost sentence presents the zero as measured": STORAGE_CHANGELOG,
    "§11.3's table row still calls the rows property-free": STORAGE_CHANGELOG,
    "§11's estimate footnote leans on the property-free finding": (
        STORAGE_CHANGELOG
    ),
    "§13.8 q2 repeats the property-free claim (BOLDED, as written)": (
        STORAGE_CHANGELOG
    ),
    "§11.3's prose says the links stay bare": STORAGE_CHANGELOG,
    "the corrected claim's own 'exactly one name' absolute": STORAGE_CHANGELOG,
    "§13.8 q2's 'never a second property name'": STORAGE_CHANGELOG,
}

#: The one retracted sentence this correction REWROTE rather than quoted, so no
#: file holds it today: its pre-fix form is the extractor ledger's `aboutObject`
#: decision row (`git show d8d050099:docs/architecture/`
#: `EXTRACTOR-V4-ARCHITECTURE-CHANGELOG.md`). Declared here so that EVERY entry
#: above is either machine-checkable or explicitly un-checkable, instead of
#: silently being neither.
RETRACTED_PRE_FIX_ONLY: frozenset[str] = frozenset({
    "the decision ledger's reason is property-freedom",
})

#: The writers the corrected passages must NAME, and the code shape that makes
#: each one a property-bearing `about*` writer. These are the ORIGINATORS — the
#: sites that put the named property on the edge.
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

#: The RE-MATERIALIZING writers: they carry the edge's whole attribute set by
#: property MAP over an allowlisted predicate (which includes `aboutObject`).
#: They are recorded as the MAP form because a map assignment's NAMES are
#: schema-driven and unreadable statically — `_SET_MAP` cannot read them, and
#: inventing one would be the same guess this file exists to stop. ⚠️ One of
#: them ALSO writes a name in the INLINE pattern-map form
#: (`cache_projection.relation_statement` stamps `{idx:$idx}`,
#: `cache_projection.py:717`), and that `idx` is read by the census's inline
#: rule — so the documents' set is `confidence` (originated) plus `idx`
#: (re-materialized), not one name.
MAP_WRITERS: dict[tuple[str, str], tuple[str, ...]] = {
    ("tortoise/cache_projection.py", "relation_statement"): (
        "MERGE (src)-[r:{rel.rel_type} {{idx:$idx}}]->(tgt) SET r += $props",
    ),
    ("tortoise/projection/__init__.py", "_rebuild_all_replay"): (
        'SET r += $attrs',
        '("direction", "confidence", "weight", "label",',
    ),
    ("tortoise/sdk.py", "supersede_point"): (
        'SET r += $attrs',
        'if k in ("direction", "confidence", "weight",',
    ),
    ("tortoise/hosted_backup.py", "restore_graph"): (
        'CREATE (a)-[r:{safe_type}]->(b) SET r = $p',
    ),
}

#: The property. One ORIGINATED name; `idx` is the second recorded name, written
#: by the record-layer projection as an INLINE pattern map (see `INLINE_FORM`).
PROPERTY = "confidence"
#: The second name, and the form that writes it.
SECOND_PROPERTY = "idx"

#: Sentinel for an edge write whose property NAMES cannot be read statically:
#: a property-MAP assignment (`SET r += $props`) carries whichever columns the
#: record/edge schema has. For the bound entity links here that set includes
#: `confidence` (`record_projection.RelationUpsert.attrs`; `public.relation`
#: has a `confidence` column), so a map-form writer reproduces the SAME one
#: name — it does not introduce a second one. Recorded as a form, not a name.
MAP_FORM = "<map:$props>"

_SET_ON = re.compile(r"SET\s+(\w+)\.(\w+)\s*=")
#: Property-MAP assignment on a relationship variable: `SET r += $props`,
#: `SET r = $p`. Structurally invisible to `_SET_ON`, and — like the
#: `aboutObject {` grep this PR exists to correct — a census that cannot match
#: it reports a measured absence. Four shipped sites already use this form.
_SET_MAP = re.compile(r"SET\s+(\w+)\s*(\+=|=)\s*(\$|\{)")
#: The THIRD form: a property written by an INLINE pattern map —
#: `MERGE (s)-[r:aboutObject {idx:$idx}]->(t)`. Its NAMES are statically readable
#: (unlike `SET r += $props`), and it appears in NO `SET` clause (unlike
#: `SET r.<prop>`), so both rules above were blind to it while
#: `cache_projection.relation_statement` shipped it on every edge it
#: materializes — including an allowlisted `aboutObject` one. Found by the
#: third review cycle: the first census missed the MAP form, the second missed
#: this, and each time the census's own blindness was the defect.
INLINE_FORM = "<inline-map>"
_INLINE_BRACE = re.compile(r"\{([^{}]*)\}")
_INLINE_NAME = re.compile(r"(\w+)\s*:")
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
#:
#: The PREDICATE ALLOWLIST (`validate_rel_type` / `KNOWN_REL_TYPES`) and the
#: restorer's `_sanitize_label` belong here for the map-form writers: they do
#: not name `aboutObject` at the write site at all — they take the predicate
#: from the allowlist that contains it (or from the dump, sanitized). Verified
#: against the tree: adding them raises the census from 3 writers to exactly 7,
#: the four map-form sites above plus the three named-form ones.
_ABOUT_VOCAB: tuple[str, ...] = (
    "ENTITY_LINKED_RELS",
    "_ABOUT_REPLAY_RELS",
    "_VALID_ABOUT_EDGE_PREDICATES",
    "ABOUT_EDGE_TYPES",
    "_ABOUT_TYPES",
    "STRUCTURAL_REL_LABELS",
    "_ROLE_TARGET",
    "KNOWN_REL_TYPES",
    "validate_rel_type",
    "_sanitize_label",
)


def _doc(path: Path) -> str:
    assert path.exists(), f"{path} is missing"
    return path.read_text(encoding="utf-8")


_MARKUP = re.compile(r"[*`]")
_WHITESPACE = re.compile(r"\s+")


def _flat(text: str) -> str:
    """Marker-insensitive form of a sentence, for the RETRACTED pins.

    `**no** `aboutObject` edge carries any property` and the unbolded variant are
    the SAME claim; matching raw substrings is how one entry here became a dead
    pin that could never red. Emphasis and code markers are decoration on a
    claim, not part of it.
    """
    return _WHITESPACE.sub(" ", _MARKUP.sub("", text))


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


def _writers_in_module(
    module: str, text: str
) -> dict[tuple[str, str], set[str]]:
    """The census over ONE module's source. Split out so a SYNTHETIC module can be
    put through it: the two fail-open forms found in review (the inline pattern
    map, and a writer that names its predicate from the allowlist instead of
    spelling `about`) are only pinned non-vacuously by feeding this function a
    module that uses them — no shipped file has to exist for the rule to be
    proved live."""
    found: dict[tuple[str, str], set[str]] = {}
    if True:  # one flat body, so the file/rglob caller below stays a loop
        try:
            tree = ast.parse(text)
        except SyntaxError:  # pragma: no cover — a broken module fails elsewhere
            return found
        lines = text.splitlines()
        for qualname, node in _qualified(tree):
            body = _own_body(node, lines)
            about_vars: set[str] = set()
            inline: dict[str, set[str]] = {}
            interpolated = "{" in body and any(
                vocab in body for vocab in _ABOUT_VOCAB
            )
            for var, rel in _PATTERN.findall(body):
                if _ABOUT_REL.search(rel) or ("{" in rel and interpolated):
                    about_vars.add(var)
                    # An inline pattern map (`{idx:$idx}`) is a property write
                    # whose NAME is right there — read it rather than assume.
                    for brace in _INLINE_BRACE.findall(rel):
                        inline.setdefault(var, set()).update(
                            _INLINE_NAME.findall(brace)
                        )
            if not about_vars:
                continue
            for var, names in inline.items():
                # A brace with no `name: value` pair contributes nothing: record
                # no empty set, which would report a writer for writing nothing.
                if var in about_vars and names:
                    found.setdefault(
                        (module, qualname.split(".")[-1]), set()
                    ).update(names)
            for var, prop in _SET_ON.findall(body):
                if var in about_vars:
                    found.setdefault(
                        (module, qualname.split(".")[-1]), set()
                    ).add(prop)
            # The property-MAP form (review finding on this file's first
            # version): `SET r += $props` / `SET r = $p` writes an edge's whole
            # attribute set and is invisible to `_SET_ON`. Recorded as the FORM
            # (`MAP_FORM`), because the names are schema-driven and unreadable
            # statically — see `MAP_WRITERS`.
            if any(var in about_vars for var, _op, _rhs in _SET_MAP.findall(body)):
                found.setdefault(
                    (module, qualname.split(".")[-1]), set()
                ).add(MAP_FORM)
    return found


def _module_in_scope(text: str) -> bool:
    """Can this module NAME an about predicate — by spelling one, or by taking it
    from the shared predicate vocabulary a map-form writer uses INSTEAD of
    spelling it (`validate_rel_type` / `KNOWN_REL_TYPES` / `_sanitize_label`)?

    The prefilter used to be the literal word "about", which is exactly the word
    `_ABOUT_VOCAB`'s own justification says such a writer does NOT contain — so
    the vocabulary widening never reached the files it exists for (third review
    cycle; measured: a map-form module with no "about" anywhere was skipped).
    Its own leg pins both halves, because a prefilter is a fail-open by
    construction: anything it skips is reported as a measured absence.
    """
    return "about" in text or any(v in text for v in _ABOUT_VOCAB)


def _about_edge_property_writers() -> dict[tuple[str, str], set[str]]:
    """{(module, symbol): {property, …}} for every property-bearing about-edge write.

    BOUND: an edge counts as `about*` by an `about*` literal in the relationship
    token, or by INTERPOLATION in a function body that names one of
    `_ABOUT_VOCAB` (and, above that, in a module `_module_in_scope` admits). A
    writer that names its predicate some other way is invisible here — the
    promise is "nothing NEW appears unrecorded among the forms and vocabularies
    we read", never "nothing can appear".
    """
    found: dict[tuple[str, str], set[str]] = {}
    for path in sorted(PKG.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if not _module_in_scope(text):
            continue
        found.update(_writers_in_module(path.relative_to(ROOT).as_posix(), text))
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
        f"{label}: {sentence!r} (was live in {home} pre-fix)"
        for label, (sentence, home) in RETRACTED.items()
        for name, text in live.items()
        if _flat(sentence) in _flat(text)
    ]
    ledger_literal = RETRACTED[
        "the decision ledger's reason is property-freedom"
    ][0]
    if _flat(ledger_literal) in _flat(ledger):
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


def _section(text: str, start: str, end: str) -> str:
    """The slice of `text` from `start` up to (not including) `end`.

    A SECTION slice, not the whole file: `link_entity`, `bind_point_subjects`
    and `` `confidence` `` each appear in more than one section, so a file-wide
    membership test stays green when ONE section is silently reverted — the
    failure this leg exists to catch (`#8639` review, the
    `test_entity_attachment_brief_claims.py` precedent: *"the §5 row carries the
    same literals, so a file-wide membership test cannot red on §6.7's silent
    deletion"*).
    """
    i = text.index(start)
    j = text.index(end, i + len(start))
    return text[i:j]


#: The corrected passages, located by heading/lead-in text (not by line number,
#: per the documents' own rule). Each must name both originating writers and the
#: property, INSIDE its own section.
CORRECTED_SECTIONS: tuple[tuple[str, str, str, str], ...] = (
    (
        "STORAGE-ARCHITECTURE.md §11.1",
        "### 11.1 Why the connections are not the cost problem",
        "### 11.2 What actually costs money",
        "STORAGE",
    ),
    (
        "STORAGE-ARCHITECTURE.md §13",
        "## 13. Filed from this document",
        "## 14. What this document does not decide",
        "STORAGE",
    ),
    (
        "EXTRACTOR-V4-ARCHITECTURE.md §13.8 q2",
        "2. **Should `aboutObject` be a stored edge or a derived join?**",
        "3. **Should every expansion have a hard fan-out cap**",
        "EXTRACTOR",
    ),
)


def test_the_corrected_passages_name_the_property_bearing_class():
    docs = {
        "STORAGE": _doc(STORAGE),
        "EXTRACTOR": _doc(EXTRACTOR),
    }
    for label, start, end, which in CORRECTED_SECTIONS:
        section = _section(docs[which], start, end)
        for symbol in ("link_entity", "bind_point_subjects"):
            assert symbol in section, (
                f"{label} does not name `{symbol}` — the corrected evidence must "
                "name the writers that put a property on an about edge, IN the "
                "section that makes the claim"
            )
        assert f"`{PROPERTY}`" in section or f"r.{PROPERTY}" in section, (
            f"{label} does not name the `{PROPERTY}` property — the claim "
            "'property-free' is replaced by a named, bounded exception, not by "
            "silence"
        )
        # The SECOND recorded name, and the third write form: `confidence` alone
        # is the ORIGINATED set. A record-projected about edge also carries `idx`
        # (`cache_projection.relation_statement`, an inline pattern map), so a
        # section naming only `confidence` re-asserts an absolute the code
        # disproves — the exact defect this PR exists to correct.
        assert f"`{SECOND_PROPERTY}`" in section, (
            f"{label} no longer names `{SECOND_PROPERTY}` — the record-layer "
            "projection stamps it inline on an allowlisted `aboutObject` edge "
            "(`cache_projection.py:717`), so the measured set is `confidence` "
            "(originated) plus `idx` (re-materialized), not one name"
        )
    # The surviving true half must stay stated: the GENERIC writer sets nothing.
    storage = docs["STORAGE"]
    assert "create_about_edge" in storage, (
        "STORAGE-ARCHITECTURE.md no longer distinguishes the generic writer "
        "(`create_about_edge`, no SET) from the binder's property-bearing one"
    )
    # The MAP form must be named too, or the documents describe only the half of
    # the property-bearing population that the first census could see.
    for label in ("§13", "§13.8 q2"):
        section = next(
            _section(docs[w], s, e) for lab, s, e, w in CORRECTED_SECTIONS
            if lab.endswith(label)
        )
        assert "property MAP" in section or "SET r += $props" in section, (
            f"{label} no longer records the property-MAP form of the write — "
            "the documented property set then covers only `SET r.<prop>` "
            "writers, which is the blindness this file exists to stop"
        )


# ── 3. the code must still agree with the corrected evidence ───────────────


def test_the_code_still_agrees_with_the_corrected_evidence():
    writers = _about_edge_property_writers()
    stale: list[str] = []
    recorded = {
        **{k: (shapes, PROPERTY) for k, shapes in RECORDED_WRITERS.items()},
        **{k: (shapes, MAP_FORM) for k, shapes in MAP_WRITERS.items()},
    }
    for key, (shapes, expected) in recorded.items():
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
        if expected not in writers.get(key, set()):
            stale.append(
                f"{module}::{symbol} — the census no longer sees it writing "
                f"{expected!r} on a relationship variable"
            )
    assert not stale, (
        "the CODE moved away from the corrected evidence — re-derive the "
        "documented claim instead of leaving it stale:\n  " + "\n  ".join(stale)
    )


def test_the_named_and_map_form_writer_populations_are_disjoint():
    #: A row in both dicts would mean one site is recorded twice, and the
    #: `MAP_FORM` sentinel would then be asserted against a named-form row (or
    #: the reverse) — the row would pass while checking nothing.
    both = sorted(set(RECORDED_WRITERS) & set(MAP_WRITERS))
    assert not both, (
        "these sites are recorded as BOTH a named-form and a map-form writer, "
        "so neither row pins what it claims:\n  "
        + "\n  ".join(f"{m}::{s}" for m, s in both)
    )
    assert len(MAP_WRITERS) >= 4, (
        "the map-form population shrank below the four shipped sites the "
        "documents now record — if a writer was genuinely removed, re-derive "
        "the documents' count with it"
    )


# ── 4. no unrecorded property-bearing writer ───────────────────────────────


def test_no_unrecorded_property_bearing_about_edge_writer():
    writers = _about_edge_property_writers()
    assert writers, (
        "the census found no property-bearing about-edge writer at all — the "
        "relationship-variable/SET rule or the about* filter was changed, and "
        "this guard has stopped measuring anything"
    )
    #: Both forms must be present in the population, or the census has silently
    #: narrowed to one of them (how the first version of this file missed four
    #: shipped writers).
    assert any(PROPERTY in props for props in writers.values()), (
        "the census sees no NAMED property write (`SET r.<prop>`) at all"
    )
    assert any(MAP_FORM in props for props in writers.values()), (
        "the census sees no property-MAP write (`SET r += $props` / "
        "`SET r = $p`) at all — `_SET_MAP` or the widened `_ABOUT_VOCAB` was "
        "changed, and the map-form half of the population is unmeasured again"
    )
    #: The THIRD form. `cache_projection.relation_statement` stamps `idx` in an
    #: inline pattern map; if the census stops reading it, this population goes
    #: back to claiming an about edge carries one name — the version of this
    #: guard that missed four shipped writers, one form further along.
    assert writers[("tortoise/cache_projection.py", "relation_statement")] >= {
        SECOND_PROPERTY,
        MAP_FORM,
    }, (
        "the census no longer reports `idx` (the INLINE pattern-map form) on "
        "the record-layer projection — `MERGE (src)-[r:{rel.rel_type} "
        "{idx:$idx}]->(tgt) SET r += $props` (`cache_projection.py:717`) "
        "writes a SECOND property name on an allowlisted `aboutObject` edge, "
        "which is why the documents state `confidence` + `idx`"
    )
    assert any(SECOND_PROPERTY in props for props in writers.values()), (
        "no writer in the population is seen writing `idx` — the inline-map "
        "rule has stopped measuring anything"
    )
    unrecorded = sorted(
        f"{module}::{symbol} SETs {sorted(props)}"
        for (module, symbol), props in writers.items()
        if (module, symbol) not in RECORDED_WRITERS
        and (module, symbol) not in MAP_WRITERS
    )
    assert not unrecorded, (
        "these functions put a property on an `about*` edge and are recorded "
        "nowhere:\n  " + "\n  ".join(unrecorded)
        + "\nRecord each one in the documents' corrected evidence (and in "
        "RECORDED_WRITERS/MAP_WRITERS here), because the ruling's *cost-only* "
        "argument depends on the property set being known and bounded."
    )


# ── 4b. the two forms the census was fail-open on (synthetic probes) ───────


def test_the_census_reads_the_inline_pattern_map_form():
    """A property stamped INSIDE the pattern (`-[r:aboutObject {c:$v}]->`) is a
    write; a census that only reads `SET` clauses reports it as a measured
    absence — the blindness this file exists to stop, one form along. Probed on
    a synthetic module so the rule is proved live without a shipped writer's
    cooperation, and so a regression reds here rather than in production."""
    probe = (
        "def link(g, s, t, c):\n"
        "    g.query('MERGE (s)-[r:aboutObject {confidence:$c}]->(t)', "
        "params={'c': c})\n"
    )
    found = _writers_in_module("tortoise/probe.py", probe)
    assert found == {("tortoise/probe.py", "link"): {"confidence"}}, (
        "the census does not read the INLINE pattern-map form — a property "
        f"written there is invisible again (found {found!r})"
    )


def test_the_census_reads_a_writer_that_never_says_about():
    """The module prefilter was the literal word "about" — which is exactly what
    `_ABOUT_VOCAB`'s justification says an allowlist-driven map writer does NOT
    contain, so the widened vocabulary never reached those files. The probe
    names no about predicate at all: it takes it from `validate_rel_type`, the
    allowlist that contains every `about*` type (`security.py:85`)."""
    probe = (
        "from tortoise.security import validate_rel_type\n"
        "def project_edge(g, rel_type, props):\n"
        "    validate_rel_type(rel_type)\n"
        "    g.query(f'MERGE (s)-[r:{rel_type}]->(t) SET r += $props', "
        "params={'props': props})\n"
    )
    assert "about" not in probe
    assert _module_in_scope(probe), (
        "the module prefilter still requires the literal word 'about', so the "
        "allowlist-driven map writer is skipped by the FILE WALK even though "
        "the census rule would see it"
    )
    assert not _module_in_scope("x = 1\n"), (
        "the prefilter admits every module — it no longer narrows anything, so "
        "an unrelated module's writes would count as about-edge writers"
    )
    found = _writers_in_module("tortoise/probe.py", probe)
    assert found == {("tortoise/probe.py", "project_edge"): {MAP_FORM}}, (
        "the census does not read a map-form writer whose predicate comes from "
        f"the allowlist instead of the word 'about' (found {found!r})"
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
    # The DEFAULT-ON half is not in `_extract_session_v2` at all: it is the
    # extractor selection. `m2` is the opt-out and v2 is the fall-through, so
    # flipping the default would falsify the corrected sentence while the call
    # above stayed in place — an unpinned half of a claim this leg advertises.
    tree = ast.parse(sdk)
    selected = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and "TORTOISE_SESSION_EXTRACTOR" in ast.unparse(node.test)
        and "m2" in ast.unparse(node.test)
    ]
    assert selected, (
        "the `TORTOISE_SESSION_EXTRACTOR == 'm2'` selection is gone from "
        "`sdk.py` — re-derive the corrected reach claim before deleting the "
        "opt-out it describes"
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


# ── 7. no dead retracted pin ──────────────────────────────────────────────


def test_every_retracted_literal_is_still_findable_where_it_is_allowed_to_live():
    #: A literal that matches nothing can never red. Every entry must therefore be
    #: either findable in the changelog that quotes it, or declared pre-fix-only.
    assert set(RETRACTED) == set(RETRACTED_QUOTED_IN) | set(RETRACTED_PRE_FIX_ONLY), (
        "a RETRACTED entry is classified nowhere: it is neither quoted in a "
        "changelog (RETRACTED_QUOTED_IN) nor declared pre-fix-only "
        "(RETRACTED_PRE_FIX_ONLY), so nothing proves it can ever match text"
    )
    dead = [
        f"{label}: {RETRACTED[label][0]!r} is not in {path.name}"
        for label, path in sorted(RETRACTED_QUOTED_IN.items())
        if _flat(RETRACTED[label][0]) not in _flat(_doc(path))
    ]
    assert not dead, (
        "these retracted sentences are pinned but findable nowhere — a pin "
        "that cannot match the pre-fix text cannot red on a re-rot:\n  "
        + "\n  ".join(dead)
    )


# ── 6. the changelogs must carry the correction, not the old claim ─────────


def test_the_storage_changelog_records_the_correction():
    changelog = _doc(STORAGE_CHANGELOG)
    assert "#9397" in changelog, (
        "STORAGE-ARCHITECTURE-CHANGELOG.md does not record this correction — "
        "the changelog is where a reader learns a §11.1/§13 claim moved, and an "
        "unrecorded correction is the drift #9397 exists to stop"
    )

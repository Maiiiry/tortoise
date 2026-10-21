"""The call-time + empty-pattern census for `aboutObject` - derivation only, no pins.

Why this is a support module and not a test file: `#9398` landed the census with
these two derivations as its *tests*, pinned to the prose of its own table
(a row count and a `<from> -> <to> sites` change chain). `#9400` restructured
§13 - narration moved to the changelog, the table re-keyed on `module.py::symbol`
- so those prose pins cannot survive, while the DERIVATION they carried (a
relationship type supplied at CALL TIME, and the two spellings of an empty
relationship pattern) is exactly the part that must not be lost: it is the class
no name-based scan can see.

So the derivation lives here, verbatim from `#9398`'s guard, and
`tests/test_7948_about_object_read_sites.py` consumes it - one implementation,
two consumers, no second definition of what a reader is. Nothing here is a pin:
the pins (every derived site is a §13 row or a registered exclusion, and every
register entry still derives) belong to the guard that owns the table. The prose
regexes and the `test_*` arms of the original file are deliberately NOT here.
"""
from __future__ import annotations

"""#7948 — the `aboutObject` read-site declaration must be COMPLETE, not just
well-formed.

THE DEFECT THIS PINS
--------------------
`docs/architecture/STORAGE-ARCHITECTURE.md` §13's read-site table is the
**whole evidence base** for a P1-severe storage ruling — the document says so in
its own words: *"⚠️ The count is the whole argument — the table must name every
customer-callable surface"*. The ruling it supports (KEEP the stored edge,
owner, 2026-09-23) is safe; the **table** is not.

It is a hand-maintained prose list of `file.py (symbol)` pinned to
`c79ba1cf2`, and it has been found incomplete **four separate times**:

1. the issue body (`#7948`) — the envelope, whose site count is stated in that
   issue's owner-decision comment and recorded in
   `docs/architecture/STORAGE-ARCHITECTURE-CHANGELOG.md`, NOT in the body and not
   here: this line restated the numeral and it was wrong (arm 24 bans the shape);
2. review finding `B2.7` (`REVIEW-CONSOLIDATED-2026-09-23.md`) — named three
   further readers and under-classed two of them;
3. the `#7956` erratum on `B2.7`, which found that finding *itself* incomplete
   in the direction that matters (the customer-callable count was four, not
   two);
4. the review of *this* file's first revision (`PR #9398`) — five further
   readers, two of them customer-callable, **all** invisible to a census that
   only read Cypher *string literals*.

THE ROOT CAUSE, AND WHY THIS IS A CENSUS RATHER THAN A LONGER LIST
------------------------------------------------------------------
Nothing executable compared the table to the code. A **symbol resolution** check
cannot see a *missing* row, so the table could only ever be audited by hand — and
hand audits are what produced the four passes above.

The first revision of this guard was itself a hand audit in disguise: it read
`ast.Constant` only, so it could not see a relationship name **built at
runtime** from a repo constant. Five live readers were built that way
(`StateRanker._fetch_object_signals` joins `ranking.ABOUT_EDGE_TYPES`; both
`TortoiseSDK._state_related_*` concatenate `_ABOUT_TYPES`; `supersede_point` and
`_preview_supersede` loop over `SUPERSEDE_STRUCTURAL_RELS`) and every arm stayed
green over them — the census and the table agreed perfectly *because* the
census could only see what a single literal names. That agreement was circular.

So the census resolves the *expression*, not just the leaf:

* `Constant` strings — the plain case;
* `+`-concatenation and **f-strings** (`JoinedStr`, even with no placeholders),
  joined into one string;
* `Name`/`Attribute` references to a **string constant or tuple of strings**,
  module-level or class-level, in this file or (when the name is unique
  repo-wide) in the module that defines it — so an `import`ed constant resolves;
* `NAME = "a|b|…"` joined via `"|".join(NAME)`, and `for rel in NAME` loop
  variables, expanded to the constant's members — including a **`frozenset`**
  (`frozenset({...})`, `ast.Set`), which is the idiomatic shape for a rel family
  here (`DERIVABLE_STRUCTURAL_RELS`, `_ABOUT_REPLAY_RELS`, `ENTITY_LINKED_RELS`)
  and the one that escaped the resolver until the fourth review.

THREE derivations, because a reader can reach the edge in three shapes: by NAME
(the named census), through a pattern that names NO type (the empty-pattern class),
and through a type slot the resolver cannot fill (the call-time class).

WHAT THIS PINS
--------------
1. **`test_every_code_site_that_traverses_the_edge_is_declared`** — COMPLETENESS.
   The census is derived from the source's AST, not from a list: every Cypher
   string that traverses the `aboutObject` relationship *by name* — written
   literally or built from a constant — is a site, and every site must appear in
   §13's table. A new reader added anywhere under `tortoise/` reds this until it
   is declared.
2. **`test_every_declared_symbol_still_performs_the_read`** — ROT. Every symbol
   the table names must be a site of one of the three derivations below. A row
   whose symbol resolves but does not read misleads every re-verifier —
   `EXTRACTOR-V4-ARCHITECTURE`'s own rule is *"the symbol name is authoritative,
   the line number is a convenience"*.
3. **`test_the_empty_pattern_class_is_declared`** — the SECOND derivation.
   An empty relationship pattern (`MATCH (n)-[r]->(m)`) names no type, so it
   reads the edge without naming it and no scan for the name can find it —
   `recall_subgraph`'s `completeness="full"`, `entityProfile`/`tortoise_traverse`'s
   per-hop query, and the backfill census's unlabelled inbound count are all
   this shape. It is derived as a **class** from the pattern's own shape: every
   such reader under `tortoise/` must be either a §13 row or a named member of
   `EMPTY_PATTERN` (with the reason it is not an `aboutObject` read for a
   referent), and every member must still derive. One example declared by name
   was the shape's defect: it let three further members — two of them
   customer-callable — go absent while every arm stayed green.
4. **`test_the_table_is_not_vacuous`** — the census, the empty-pattern class,
   the call-time class, the table and the customer-callable count are all
   non-trivially populated, so none of the arms above can pass by finding
   nothing.
5. **`test_the_census_derives_a_relationship_name_built_at_runtime`** — the
   MECHANISM, on synthetic sources (`_MECHANISM_CASES`): a literal, a
   `+`-concatenation, an f-string, a constant FRAGMENT interpolated into the type
   slot (`{_REL}Object`), `"|".join(TUPLE)`, a `for rel in TUPLE` loop and a
   parameter default must each derive; a `MERGE` writer and a docstring that only
   QUOTES the pattern must not. One pair is about the CLAUSE rather than the name:
   a lowercase `match` must derive and a lowercase `merge` must not, because the
   clause detector was case-sensitive and such a reader derived in NO class —
   neither derivable nor declarable — while every arm stayed green. The
   rel-FAMILY constructors (`ast.Set`, `frozenset({...})`, `set({...})`,
   `tuple([...])`, `list([...])`) are the same resolver one branch further on and
   are pinned by their own arm and corpus (`_FROZENSET_CASES`,
   `test_the_resolver_follows_a_frozenset_rel_family`) — this docstring credited
   them to `_MECHANISM_CASES`, which never held them. Without these arms the
   resolver could regress to literal-only and the table would silently go back to
   being un-checked in the class that produced the fourth correction.
6. **`test_the_call_time_shape_is_derived_and_declared`** — the THIRD
   derivation, and the one this file used to DECLARE instead. A relationship
   type written as an expression (a function argument, a dict lookup, a journal
   field) leaves the type slot EMPTY when the resolver gives up on it, so the
   resolved candidate carries `-[r:]->` — a shape, like `-[r]->`, and therefore
   derivable as a class: every member must be a §13 row or an entry in
   `CALL_TIME_REL` (with the reason its gap cannot carry `aboutObject` for a
   referent), and every entry must still derive. Declaring the class by hand is
   what let `TortoiseSDK.traverse` and `TortoiseSDK.ingest` — both
   customer-callable — sit outside the census while every arm stayed green, and
   what made a NEW reader of the shape invisible by construction.
7. **`test_the_prose_counts_match_the_table`** — the NUMBERS. §13 states its
   counts in prose and the other arms check only the rows, so the numerals are
   read out of the sentences that state them and compared to the table. A
   demoted `reach` cell with a stale headline is the `#7956` erratum's direction
   — the one the ruling rests on. ⚠️ **The counts are checked WITH their units**:
   the headline used to read *"TWENTY read sites, and SIX of them are
   CUSTOMER-CALLABLE"*, mixing a **symbol** count with a **row** count, so "of
   them" could not hold — the six customer-callable rows carried nine symbols.
   It now states sites, rows, customer-callable rows and the sites inside them
   separately, and each numeral is checked against its own unit.
8. **`test_the_census_prose_carries_no_line_citations`** — the LINE HINTS. §13
   states its own rule (*the symbols are authoritative and the line numbers are
   deliberately omitted*) and then, one document over, the sibling paragraph went
   on carrying fifteen `file.py:NNNN` hints — six of them pointing at unrelated
   code at the head that rewrote it. A line number is not a claim this guard can
   check (asserting it would red on every unrelated edit), so the claim is
   removed instead of trusted: the census prose carries none.
9. **`test_the_prose_names_every_row_in_the_empty_pattern_class`** — the CLASS
   SENTENCE. §13 declares the empty-pattern class *"by rows"*, and that list is
   a prose restatement of arm 3's derived set: it said *"rows 3 and 17"* while
   the class had three members (row 18 is the backfill census). An
   under-counted declaration is the same defect as an under-counted table, one
   level up, so the sentence is compared to the derived set.
10. **`test_the_reach_column_names_only_symbols_that_exist`** — the `reach`
   COLUMN'S SYMBOLS. Every backticked identifier in `reach` must exist in the
   package: row 7 cited `QueryOptions`, which is defined nowhere. The arms above
   resolved the `site` column only, so an unguarded column could name a symbol
   that never existed and stay green.
11. **`test_the_change_chain_ends_at_the_headline_counts`** — the CHRONOLOGY.
   §13's change chain (*eleven → fifteen → twenty → …*) explains why the count
   moved, and at the head that added rows 19 and 20 it stopped at **twenty-two
   sites** under a headline of **TWENTY-FOUR**: the paragraph narrating the number
   disagreed with the number. The last increment of each unit must arrive at the
   table's total, so a chain that stops short reds. It cannot be folded into arm
   7 — the history numerals match none of the four asserted sentence forms, and
   must not, because an increment is a historical claim rather than a total.
12. **`test_the_numeral_parsing_accepts_digits`** — the SPELLINGS. `_numeral`
   documented that digits are accepted while all five captures were words-only,
   so `token.isdigit()` was dead code and a digit rewrite of the headline matched
   nothing, redding arm 7 with *"no longer states its site count"* — a false
   cause. Both spellings of the same claim are now fed through every unit and
   through the chain form, so the arm reds if the captures regress.
13. **`test_the_empty_pattern_register_count_matches_the_prose`** — the SIZE of the
   `EMPTY_PATTERN` register. §13 states its size in the same sentence that
   declares the class (`(<N> members today`), and nothing read that numeral: the
   register was only floor-checked, so adding or removing a member falsified the
   sentence silently — the same rot class the arms above close for the headline
   counts.
14. **`test_the_empty_pattern_class_derivation_holds`** — the MECHANISM for arm 3,
   on the synthetic sources in `_EMPTY_PATTERN_CASES`: a bracketed traversal with
   no type must derive, and the near-misses must NOT (a named type, a `MERGE`
   writer, a docstring quoting Cypher). The lowercase spelling of the governing
   clause is in here too, for the same reason as arm 5's: `match (n)-[r]->(m)` is
   the same read as the uppercase form, and the clause detector could not see it. Without it the shape walk could regress to
   "finds nothing" and arm 3 would report a class of size zero as complete. This
   entry used to state a count of its own (`seven`) against a corpus of nine — the
   exact defect class the arms below exist for, in the one place nobody read it —
   so the count is gone rather than re-stated; arm 19 pins the corpus instead.
15. **`test_the_sibling_paragraph_names_every_customer_callable_row`** — the SIBLING
   SET. `EXTRACTOR-V4-ARCHITECTURE.md` §13.8 question 2 enumerates the
   customer-callable surfaces, and at the head that added rows 21/22 it was two
   rows short of §13 — the two customer doors whose addition is what moved the
   numeral the KEEP ruling rests on. Every customer-callable row's symbol must
   appear in that paragraph.
16. **`test_the_callable_enumeration_lists_each_surface_once`** — the SIBLING LIST.
   §13's `⇒` sentence is the reviewer's checklist for "did every customer-callable
   surface get named", and it named the `tortoise_supersede` dry-run preview
   twice — ten entries for nine surfaces, read by nothing. A duplicate entry reads
   as a surface checked twice while another went unchecked, so the entries are
   checked twice: no duplicate, and the distinct count against the numeral the
   sentence itself states (`are nine of them` refers to the surfaces just listed).
   Comparing them to the TABLE's row count was the units error this file exists
   for, one sentence over.
17. **`test_a_pattern_built_entirely_from_a_variable_is_unreachable_and_says_so`**
   — the stated LIMIT. A Cypher pattern assembled wholly from variables carries no
   bracket text for any of the three walks, AND is undeclarable: arm 2 requires
   every §13 row to be a site of a walk, and arm 6's reverse direction requires
   every register entry to still derive. The docstring used to promise the
   opposite ("must be declared by name — arm 6 checks that declaration"); the arm
   measures the gap and pins the honest statement, so the claim cannot drift back.
18. **`test_the_file_filter_un_sees_nothing`** — the FILTER. `_files()` is a
   text-predicate gate in front of three walks, and the PR body's claim that it is
   a strict superset was measured once by hand and pinned nowhere: a NARROWED gate
   would un-see a reader silently instead of reding. The three walks are run over
   the modules the gate SKIPPED (a gated module's sites are already the cached
   walks' output) and must find nothing in any class, and a deliberately narrowed
   filter (the literal carriers only) must be shown to DISAGREE — so the arm
   proves it can see a narrowing rather than asserting it, at ~2 s rather than the
   ~19 s a full 242-module re-walk cost.
19. **`test_the_case_corpora_are_not_vacuous`** — the CORPORA. Every synthetic
   case tuple must hold both directions (something that derives and a near-miss
   that must not), no repeated labels, and a floor on its size: a corpus silently
   shrunk to one direction leaves the arm above it green and unmeasured.
20. **`test_the_call_time_shape_derivation_holds`** — the MECHANISM for arm 6, on
   the synthetic sources in `_CALL_TIME_CASES`: the exact analogue of arm 14 for
   the third class, and it was missing from this list while arm 14 was not. An
   empty type slot must derive, and the near-misses must NOT (a named type, an
   empty PATTERN, a `MERGE` writer). Without it the call-time walk could regress to
   "finds nothing" and arm 6 would report a class of size zero as complete.
21. **`test_a_new_call_time_reader_cannot_ship_green`** — the NEW-MEMBER direction
   of that class, run on `_NEW_READER_CASES`: sources the tree does not contain are
   pushed through the completeness arm's OWN predicate, so *"a new member of the
   shape cannot ship green"* is a measurement rather than a claim about the
   derivation. Also missing from this list, and it is the arm the PR body cites as
   the sixth review's headline fix.
22. **`test_the_docstring_names_every_arm`** — the NUMBERING OF RECORD, checked
   against the arms themselves. This list called itself the numbering of record
   while two arms had no entry at all (20 and 21 above, which is how they were
   found), so the promise is now executable: every `test_` function is either a
   numbered entry here or a declared collapse (`_COLLAPSED_ARMS`), the numbers are
   contiguous with no duplicates, and every collapsed name is a real arm still
   named in the prose. A count in a comment is exactly the claim class this file
   exists to stop being unread.
23. **`test_the_extractor_section_keeps_its_three_questions`** — the SIBLING
   DOCUMENT'S OWN LIST. `EXTRACTOR-V4-ARCHITECTURE.md` §13.8 prefaces its list with
   *"The three questions below"* and §15 cross-references *"question 3"*, and this
   PR's rewrite of item 2 removed the newline before item 3 — the question became
   the tail of the paragraph above it (`…cheap to keep.**3. **Should every
   expansion…`), so the list rendered one item short while the sentence above it
   still claimed three. Nothing here could see it: the line-citation arm scans
   that line for `file.py:NNNN` and the sibling arm reads backticked symbols,
   and both survive a missing newline. So the count the sentence states, the
   items that start their own line and their numbering are compared — and the
   predicate is run on a glued sample and a clean one, so it is measured rather
   than trusted.
24. **`test_the_file_states_no_count_it_does_not_measure`** — this FILE'S OWN
   NUMERALS. The register's size lives in §13's sentence and in the
   `EMPTY_PATTERN` tuple, and the arm above reads it from the DOCUMENT — so three
   present-tense copies of the old number sat in this file's comments while the
   arm stayed green, and `_MIN_CALL_TIME` carried the same kind of copy for the
   call-time walk's member count, one numeral behind. Both are now stated as
   properties that point at the arm which measures them, and this arm bans the
   copies so they cannot come back: a count is asserted, or it is rot. The same
   ban covers the `#7948` envelope's site count, which lives in that issue's
   owner-decision comment and in the changelog — the docstring restated it in two
   spellings the original ban could not see. The tenth review then found two more
   copies in two more spellings (the anti-vacuity floors' census pair, and the
   customer-callable row count one increment behind the table), which is why the
   ban now states the PROPERTY instead of listing shapes: a COUNT with a census
   UNIT, scoped as current or as a dated reading, in either order, over the
   UNWRAPPED text — a comment wraps, and a copy whose scope word lands on the next
   line is still a claim.

WHAT THE CENSUS CANNOT SEE (stated, not silently excluded)
---------------------------------------------------------
* **An empty relationship pattern** names no type, so nothing literal or
  constant carries `aboutObject` — no scan for the NAME can find it. It is not
  left undeclared: the class is derived by arm 3's shape walk, and every member
  must be a row or an `EMPTY_PATTERN` entry. The walk covers **both legal
  spellings**, the bracketed `-[r]->` and the bracket-less `(n)--()`: the second
  one used to be outside it, and a live member of it —
  `domain_validators.validate_chain_integrity`'s `AND NOT (n)--()`, the
  `orphaned_draft` degree check — sat undeclared while §13 claimed every such
  reader was enumerated, and the register was only floor-checked so nothing
  noticed. A pattern assembled **entirely from
  a variable** (no bracket text anywhere in the source) is out of reach of that
  walk — and it is **neither derived nor declarable**: a §13 row for it reds arm 2
  ("declared as a reader but no Cypher in it traverses `aboutObject`") and a
  `CALL_TIME_REL`/`EMPTY_PATTERN` entry for it reds arm 6's reverse direction ("the
  call-time walk no longer derives it"). Closing that would need a NEW shape walk
  for how the string is assembled, or a row/register exemption — not a new hand
  entry. **This is a stated limit of the census, not a covered case**: arm 17
  measures it and pins this sentence, because an earlier revision claimed the
  declaration route existed and a reader who trusted it believed the census was
  total. A parameter DEFAULT (the shape `link_entity` uses) is resolvable and is
  derived instead.
* **Resolution is one level deep per binding.** A rel name assembled from a
  value that is itself computed at call time (a function argument, a dict
  lookup, a value stored on an instance at construction) is not resolved; the
  placeholder it leaves behind is a *gap the literal around it still fills*, so
  a query whose rel pattern is readable still derives. Anything assembled
  entirely from such values must be declared by name (arm 6) or a row will be
  missing.
* **Write arms are out of scope.** A `MERGE`/`CREATE` that mints the edge is a
  writer, separated from a reader by the governing Cypher clause — not by a
  keyword appearing anywhere in the statement. A read on the *write path*
  (e.g. `supersede_point` moving the edge) IS a row: it reads before it writes.

NOT promised, and deliberately so:
* Line numbers are NOT asserted. The documents' own rule makes the symbol the
  authority and the line a convenience, and pinning line numbers would make
  this guard red on every unrelated edit — a different way to be dead.
* Whether a declared site is *reachable* (default-ON, flag-gated, eval-only) is
  NOT asserted: that column is prose with reasons, and a regex over the whole
  of it would pin wording rather than behaviour.

The customer-callable **count** IS asserted (arm 7 — the `WHAT THIS PINS` list
below is the numbering of record; earlier insertions moved this arm and a stale
"arm 6" survived here for one revision), because §13 calls it *"the
whole argument"*: the arm reads the numeral out of the sentence that states it
and compares it to the `reach` column, so demoting two rows without moving the
number — the `#7956` erratum's exact direction, and the error the KEEP ruling
rests on — reds instead of shipping. The same arm checks the census-size
numerals (sites, rows and the sites inside the customer-callable rows) against
their own units, so the numerals are never restated here a second time: a copy in
this docstring is the same circular agreement the table's sibling copy was. Only
the numeral is read, never the prose around it, so the sentence can be rewritten
freely as long as it stays true.

Static, stdlib-only, no network / DB / graph — it reads source text and a
document.
"""


import ast
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TORTISE = ROOT / "tortoise"
STORAGE = ROOT / "docs" / "architecture" / "STORAGE-ARCHITECTURE.md"
EXTRACTOR = ROOT / "docs" / "architecture" / "EXTRACTOR-V4-ARCHITECTURE.md"

#: The edge the §13 ruling is about.
EDGE = "aboutObject"

#: The Cypher clauses that can govern a relationship pattern. `MERGE`/`CREATE`
#: mint the edge, so they are writers; `MATCH`/`OPTIONAL MATCH` are the readers.
#: `re.IGNORECASE` is load-bearing, not decoration: Cypher keywords are
#: case-insensitive, so `match (n)-[:aboutObject]->(m)` is the same read as the
#: uppercase spelling. Without the flag such a reader derived in NO class — both
#: consumers `.upper()` a capture the regex could never lowercase, so the
#: normalization was dead code — and the completeness arm stayed green over it.
#: The trailing `\(` is load-bearing for the same reason and was measured, not
#: assumed: a clause in Cypher is always followed by a node pattern, while a prose
#: sentence is not. Case-insensitivity alone derived a FALSE site in
#: `value_extractor.GROUNDING_SYSTEM` — an LLM prompt whose prose says "match
#: summary items" and whose JSON schema then carries `[{"edge":
#: "aboutObject|…"}]`, so the English verb governed a bracket. A guard that reads
#: prose as Cypher is the same defect pointing the other way, and both
#: directions are pinned in `_MECHANISM_CASES`.
_CLAUSE = re.compile(r"\b(OPTIONAL\s+MATCH|MATCH|MERGE|CREATE)\b\s*\(", re.IGNORECASE)
#: A traversal: an opening `[` or `-` shortly before the relationship name, so
#: prose ("an ``aboutObject`` edge") is not mistaken for Cypher.
_TRAVERSAL = re.compile(rf"[-\[][^\n]{{0,140}}?{EDGE}", re.IGNORECASE)
#: A bracketed traversal, whatever its type slot holds (`-[r]->`, `<-[r]-`,
#: `-[r:aboutObject]->`, `-[r:{rel}]->`). `_type_slot` decides what the bracket
#: names; this only finds the brackets. The bracket-free spelling of the same
#: traversal is `_BARE_TRAVERSAL` — both must be walked, or the class is half-seen.
_EMPTY_TRAVERSAL = re.compile(r"[-<]\s*\[([^\]]*)\]\s*[-<>]")
#: The OTHER legal spelling of a relationship pattern that names no type: a
#: BRACKET-LESS traversal — `(n)--()`, `(n)-->(m)`, `(n)<--(m)`, `(n)<-->(m)`.
#: It is the SAME shape as `-[r]->`: no type is written, so it walks every
#: relationship type and `aboutObject` is one of them. The bracket detector above
#: cannot see it, and that was not academic — a live member existed while this
#: regex did not (`domain_validators.validate_chain_integrity`'s
#: `AND NOT (n)--()`), so §13's *"every such reader is enumerated by its own shape
#: walk"* was false at that head. Anchored between two node patterns (`)` … `(`)
#: on purpose: a bare `--` in prose, a CLI flag or a `->` in a log message is not
#: a traversal, and a Cypher relationship always sits between two nodes.
_BARE_TRAVERSAL = re.compile(r"(?<=\))\s*(?:<-->|<--|-->|--)\s*(?=\()")
#: The text signature of a CALL-TIME type slot — a traversal whose relationship
#: type is written as an expression rather than a name: an f-string interpolation
#: inside the bracket (`-[r:{rel}]->`, `-[:{EDGE}]->`, including one that carries a
#: property map: `-[r:{rel} {confidence:$c}]->`), or a `+`-concatenated fragment
#: where the literal ENDS inside the pattern (`"MATCH (n)-[r:" + rel + "](m)"`).
#: Only a file FILTER (a file that cannot be parsed cheaply is not walked);
#: `_is_gap_type_read` decides, on the resolved candidate.
_GAP_TYPE_HINT = re.compile(
    r"[\[<][^\]\n]*:[^\]\n]*\{[^\]\n]*\]|[\[<][^\]\n]*:\s*[\"']"
)
#: The most candidate strings one expression may expand to. A rel list is at most
#: a handful of members, so this only bounds pathological input.
_MAX_CANDIDATES = 64
#: What an unresolvable interpolation contributes: a GAP, so the literal around
#: it still derives. Deliberately empty rather than a marker string — the point
#: is to keep the surrounding Cypher intact, not to guess the value.
_UNKNOWN = ""
#: A backticked token in §13's `site` column that can be a symbol.
_SYMBOL = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*(?:\(\))?\Z")

#: Floor for the empty-pattern class walk, so the class arm cannot pass by
#: deriving nothing. The exact size is asserted by the class arm, never here: a
#: floor is not a count, and a numeral restated in a comment is the claim class
#: this file exists to stop being unread
#: (`test_the_file_states_no_count_it_does_not_measure`).
_MIN_EMPTY_PATTERN = 5

#: Floor arms: deliberately BELOW the census the TABLE declares, so a legitimate
#: contraction — a row deleted together with its reader — is not a reason to red,
#: and so a broken parser cannot pass by finding nothing. A floor is not a count,
#: and pinning it at the table's own figure would turn that contraction into a
#: failure whose message says "the parser broke". This comment states the
#: PROPERTY and restates no numeral: the exact counts are asserted against the
#: table by `test_the_prose_counts_match_the_table`, one arm down, and a copy of
#: them here is what `test_the_file_states_no_count_it_does_not_measure` bans.
_MIN_SITES = 10
_MIN_ROWS = 10
#: `FOUR` was the floor when this arm was written, i.e. the count BEFORE the
#: fifth correction. Kept at that historical value so a *contraction* of the
#: prose is visible even before the exact-count arm below reads the numeral
#: itself; that arm — `test_the_prose_counts_match_the_table` — is the only place
#: the current customer-callable count may appear, so this comment stops at the
#: property rather than quoting the table's figure.
_MIN_CUSTOMER_CALLABLE = 4

#: Floor for the call-time class walk, for the same reason as
#: `_MIN_EMPTY_PATTERN`: the shape detector breaking is a failure, a legitimate
#: contraction is not. No numeral here — the class arm measures the size.
_MIN_CALL_TIME = 4

#: The empty-pattern readers that are NOT an `aboutObject` read for a referent,
#: declared by name with the reason, so a NEW member of the class cannot appear
#: silently: `test_the_empty_pattern_class_is_declared` derives the class from
#: the pattern's shape and requires every member to be either a §13 row or an
#: entry here. Each entry is `(filename, symbol, why it is not an about read)`.
EMPTY_PATTERN: tuple[tuple[str, str, str], ...] = (
    (
        "tortoise/domain_validators.py",
        "validate_chain_integrity",
        "the `orphaned_draft` rule's zero-edge check (`AND NOT (n)--()`) — a "
        "degree count over every edge type, used to decide whether a Point is "
        "orphaned; the same read `session_source_migration._degree` is registered "
        "for one file over. It asks nothing about a referent, and its bracket-less "
        "spelling is why the class walk had to cover both spellings: it was live "
        "and invisible while only the bracketed form derived",
    ),
    (
        "tortoise/hosted_api.py",
        "_export_graph_snapshot",
        "a whole-graph export — it copies every node and edge, so it carries the "
        "edge as data, not as a read for a referent",
    ),
    (
        "tortoise/hosted_api.py",
        "_load_commit_graph_state",
        "restore/rollback bookkeeping — it rewires every edge type it finds, "
        "`aboutObject` included, but asks nothing about a referent",
    ),
    (
        "tortoise/hosted_backup.py",
        "_independent_counts",
        "an edge-count cross-check (`MATCH ()-[r]->() RETURN count(r)`) — it "
        "counts the edge, it does not read it for a referent",
    ),
    (
        "tortoise/hosted_backup.py",
        "_restore_copy_settled",
        "the post-restore settle check, again a total-edge count",
    ),
    (
        "tortoise/hosted_backup.py",
        "dump_graph",
        "the dump cursor walks every edge type to serialise them",
    ),
    (
        "tortoise/hosted_backup.py",
        "restore_graph",
        "the restore replay that writes those edges back",
    ),
    (
        "tortoise/mcp_server.py",
        "_preview_delete_edges",
        "the delete preview enumerates every edge touching the node — a "
        "deletion inventory, not an about* read",
    ),
    (
        "tortoise/mining.py",
        "verify_no_auto_wire",
        "the no-auto-wire verifier walks every edge type to assert a shape is "
        "absent",
    ),
    (
        "tortoise/projection/__init__.py",
        "_rebuild_all_replay",
        "the rebuild/rollback replay — every edge is deleted and rewritten by "
        "type, so it carries the edge rather than reading it",
    ),
    (
        "tortoise/projection/entities.py",
        "_materialize_connector_source",
        "connector-source materialisation rewires the node's edges by type",
    ),
    (
        "tortoise/sdk.py",
        "_connected_pairs",
        "a connectivity inventory (which node pairs are joined at all) — the "
        "edge TYPE is deliberately ignored",
    ),
    (
        "tortoise/sdk.py",
        "_promote_incident_operators",
        "an endpoint-liveness gate for operator promotion: it reads every "
        "outgoing edge of the incident operator to decide whether all its "
        "endpoints are live, and asks nothing about a referent",
    ),
    (
        "tortoise/sdk.py",
        "backfill_v25",
        "a migration — it counts all edges as a baseline",
    ),
    (
        "tortoise/sdk.py",
        "provenance",
        "the provenance walk reports which edge types reach a Subject; an "
        "`aboutObject` edge is not a provenance link and is not classified as "
        "one",
    ),
    (
        "tortoise/session_source_migration.py",
        "_degree",
        "a degree count over every edge type, used to decide whether a Source "
        "is orphaned",
    ),
    (
        "tortoise/session_source_migration.py",
        "_delete_if_orphan",
        "the orphan sweep deletes every edge of a degree-0 Source",
    ),
    (
        "tortoise/session_source_migration.py",
        "_rel_types",
        "an inventory of the edge TYPES on a node, reported as labels",
    ),
    (
        "tortoise/subgraph.py",
        "_hub_degrees",
        "hub-degree counting for the fan-out cap — it counts every edge of a "
        "node, whatever its type",
    ),
)

#: The readers of the THIRD shape: a relationship type that is an EXPRESSION, not
#: a name — a function argument, a dict lookup, a journal field, a value imported
#: from a module this census does not parse. The resolver follows a name one level
#: deep and then leaves a GAP, so the resolved candidate carries an EMPTY type
#: slot (`-[r:]->`) and the shape walk (`_call_time_sites`) derives the class the
#: same way `_is_empty_pattern_read` derives the no-type one.
#:
#: ⚠️ This register is NOT the class. The class is derived; this is the list of
#: its members that are NOT an `aboutObject` read for a referent — gated to the
#: operator rels, a fixed non-about* edge, or a by-type edge rewrite. Each entry
#: is `(filename, symbol, why the gap cannot carry `aboutObject` for a referent)`,
#: and `test_the_call_time_shape_is_derived_and_declared` checks BOTH directions:
#: every derived member is a §13 row or an entry here (so a NEW reader of the
#: shape cannot ship green), and every entry still derives (so a stale entry
#: cannot make the next reader's absence invisible).
CALL_TIME_REL: tuple[tuple[str, str, str], ...] = (
    (
        "tortoise/analyze.py",
        "_bfs_select_operators",
        '`safe_rel`, a `for rel in rel_types` loop variable — the loop body skips '
        'every rel that is not `"IMPL"`/`"NAND"`, so the expansion queries cannot '
        "carry `aboutObject`; they walk the operator graph, not a referent",
    ),
    (
        "tortoise/commit_ops.py",
        "apply_payload_operators",
        '`_edge_type`, `"hasPart"` or the operator `op_type` — the `#4971` '
        "idempotency probe reads the exact edge `create_operator` would have "
        "written (operator → input), and the property map in the bracket "
        "(`[:{_edge_type} {idx:0}]`) is why the type slot stays a gap",
    ),
    (
        "tortoise/projection/__init__.py",
        "_capture_onboarding_snapshot",
        "`onboarding.state.COMPLETED_STEP_EDGE` (`\"COMPLETED_STEP\"`) — a fixed, "
        "non-about* step edge; imported INSIDE the function, and the defining "
        "module carries no edge literal, so the resolver cannot follow the "
        "binding and the slot stays a gap. It also does not derive the named "
        "census, because the value it resolves to contains no `aboutObject`",
    ),
    (
        "tortoise/projection/__init__.py",
        "_rebuild_all_replay",
        "`etype`, the `edge_type` field of a journaled replay event — the "
        "rebuild/rollback replay deletes and rewrites every replayed edge BY "
        "TYPE, so it carries the edge rather than reading it for a referent "
        "(registered for its empty patterns too: an edge can be replayed "
        "without its type being named)",
    ),
    (
        "tortoise/projection/entities.py",
        "_fold_edge_message_changed",
        '`rel`, from the event, gated to `("IMPL", "NAND")` before the query — '
        "an operator-edge attribute fold, not a referent read",
    ),
    (
        "tortoise/sdk.py",
        "_find_operator",
        '`edge_rel`, `"hasPart"` or the operator `op_type` — the dedupe probe '
        "collects an operator's targets, never a referent",
    ),
    (
        "tortoise/sdk.py",
        "create_direct_edge",
        "`rel_type`, `op_type` (IMPL / NAND, `create_operator`'s edge set) — the "
        "existence pre-count and attribute SET for an operator edge",
    ),
)






class _Index:
    """The string-constant bindings one module offers.

    Module level and per class, kept separately: `self._ABOUT_TYPES` is an
    attribute lookup, `SUPERSEDE_STRUCTURAL_RELS` a module global, and a
    resolver that merged the two namespaces would answer the wrong question for
    a class attribute shadowing a module name.
    """

    def __init__(self, tree: ast.Module):
        self.consts: dict[str, ast.AST] = {}
        self.classes: dict[str, dict[str, ast.AST]] = {}
        self._collect(tree.body, self.consts)
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                attrs: dict[str, ast.AST] = {}
                self._collect(node.body, attrs)
                self.classes[node.name] = attrs

    @staticmethod
    def _collect(body: list[ast.stmt], into: dict[str, ast.AST]) -> None:
        for stmt in body:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        into.setdefault(target.id, stmt.value)
            elif (
                isinstance(stmt, ast.AnnAssign)
                and stmt.value is not None
                and isinstance(stmt.target, ast.Name)
            ):
                into.setdefault(stmt.target.id, stmt.value)


class _File:
    """One parsed module: its tree, its parent map, its constant index."""

    def __init__(self, path: Path, rel: str, source: str):
        self.path = path
        self.rel = rel
        self.source = source
        self.tree = ast.parse(source)
        self.index = _Index(self.tree)
        self.parents: dict[ast.AST, ast.AST] = {}
        for node in ast.walk(self.tree):
            for child in ast.iter_child_nodes(node):
                self.parents[child] = node
        self._assigns: dict[int, dict[str, ast.AST]] = {}
        self._users: dict[str, list[str]] | None = None

    def own_assigns(self, fn: ast.AST) -> dict[str, ast.AST]:
        """A function's own assignments, resolved once per function."""
        key = id(fn)
        if key not in self._assigns:
            self._assigns[key] = _own_assigns(fn)
        return self._assigns[key]

    def const_users(self) -> dict[str, list[str]]:
        """`{name: [functions referencing it]}` — resolved once per module.

        The module-constant attribution below needs this for every constant
        literal; recomputing it per literal walked `sdk.py`'s 30k nodes several
        times over.
        """
        if self._users is None:
            users: dict[str, set[str]] = {}
            for node in ast.walk(self.tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Name):
                        users.setdefault(inner.id, set()).add(node.name)
            self._users = {name: sorted(found) for name, found in users.items()}
        return self._users


@lru_cache(maxsize=1)
def _sources() -> dict[Path, str]:
    """Every module under `tortoise/`, read once (text only — parsing is lazy)."""
    return {
        path: path.read_text(encoding="utf-8") for path in sorted(TORTISE.rglob("*.py"))
    }


@lru_cache(maxsize=1)
def _literal_files() -> tuple[_File, ...]:
    """The modules whose TEXT carries the edge name — the constant source.

    Any constant whose value contains the edge name has a literal for it in the
    module that DEFINES it (`SUPERSEDE_STRUCTURAL_RELS` in `sdk`, `ABOUT_EDGE_TYPES`
    in `ranking`), so this set is complete for the constant index below and 8x
    smaller than the package — parsing all 242 modules cost ~4 s of the suite's
    wall clock for nothing.
    """
    return tuple(
        _File(path, str(path.relative_to(ROOT)), source)
        for path, source in _sources().items()
        if EDGE in source
    )


@lru_cache(maxsize=1)
def _files() -> tuple[_File, ...]:
    """The modules the census walks: literal carriers, plus constant consumers.

    A module can build its rel pattern out of `SUPERSEDE_STRUCTURAL_RELS` without
    ever writing the literal, so the second half of this set is what keeps the
    census from reproducing its old blindness one level up. The third half is
    the empty-pattern class: a module that walks EVERY relationship type names
    neither the edge nor a constant, so it is found by the shape of its pattern
    (`_may_traverse_every_type`) — which is how `navigation.py` and the backfill
    census were missing from a census that had already declared the class. The
    fourth half is the call-time class, whose module may name neither the edge
    nor any type at all (`_may_have_gap_type`).
    """
    parsed = {file.path: file for file in _literal_files()}
    names = _rel_constant_names()
    for path, source in _sources().items():
        if path in parsed:
            continue
        if (
            any(name in source for name in names)
            or _may_traverse_every_type(source)
            or _may_have_gap_type(source)
        ):
            parsed[path] = _File(path, str(path.relative_to(ROOT)), source)
    return tuple(sorted(parsed.values(), key=lambda file: file.path))


@lru_cache(maxsize=1)
def _module_consts() -> dict[str, tuple[ast.AST, _File]]:
    """Module-level constants defined **exactly once** in the package.

    The census falls back to this index for a name it cannot bind in its own
    module — an `import`ed constant (`mcp_server` imports
    `SUPERSEDE_STRUCTURAL_RELS` from `sdk`). The uniqueness rule is what keeps
    the fallback from guessing between two different constants that happen to
    share a name: an ambiguous name resolves to nothing and is reported by the
    completeness arm as an undeclared empty-pattern site until it is declared.
    """
    seen: dict[str, list[tuple[ast.AST, _File]]] = {}
    for file in _literal_files():
        for name, value in file.index.consts.items():
            seen.setdefault(name, []).append((value, file))
    return {name: hits[0] for name, hits in seen.items() if len(hits) == 1}


@lru_cache(maxsize=1)
def _attr_consts() -> dict[str, tuple[ast.AST, _File]]:
    """Class-level constants defined exactly once (e.g. `_ABOUT_TYPES`)."""
    seen: dict[str, list[tuple[ast.AST, _File]]] = {}
    for file in _literal_files():
        for attrs in file.index.classes.values():
            for name, value in attrs.items():
                seen.setdefault(name, []).append((value, file))
    return {name: hits[0] for name, hits in seen.items() if len(hits) == 1}


def _own_assigns(fn: ast.AST) -> dict[str, ast.AST]:
    """Assignments made directly in a function body (nested scopes excluded)."""
    out: dict[str, ast.AST] = {}

    def visit(stmts: list[ast.stmt]) -> None:
        for stmt in stmts:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        out.setdefault(target.id, stmt.value)
            elif (
                isinstance(stmt, ast.AnnAssign)
                and stmt.value is not None
                and isinstance(stmt.target, ast.Name)
            ):
                out.setdefault(stmt.target.id, stmt.value)
            for _field, value in ast.iter_fields(stmt):
                if isinstance(value, list) and value and all(
                    isinstance(item, ast.stmt) for item in value
                ):
                    visit(value)

    visit(fn.body)
    return out


def _param_defaults(fn: ast.AST) -> dict[str, ast.AST]:
    """A function's parameter DEFAULTS, by name.

    `link_entity(..., edge_type="aboutObject")` interpolates the *parameter*, not
    a local, and the default is where its value lives when every caller takes it
    — which is the case for the whole default capture path. Without this table
    the existence pre-probe that reads the edge on the write path is invisible
    even though the edge name is spelled one line above the query.
    """
    out: dict[str, ast.AST] = {}
    args = fn.args  # type: ignore[attr-defined]
    positional = list(args.posonlyargs) + list(args.args)
    paired = positional[len(positional) - len(args.defaults):]
    for arg, default in zip(paired, args.defaults, strict=True):
        out.setdefault(arg.arg, default)
    for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        if default is not None:
            out.setdefault(arg.arg, default)
    return out


def _scope(file: _File, node: ast.AST | None) -> dict[str, dict[str, ast.AST]]:
    """The bindings visible at `node`: loop vars, locals, params, attrs, globals.

    Innermost-first on every table: a loop variable shadows a local of the same
    name, which shadows a parameter default, which shadows the class attribute,
    which shadows the module global.
    """
    scope: dict[str, dict[str, ast.AST]] = {
        "loop": {},
        "local": {},
        "param": {},
        "cls": {},
        "module": file.index.consts,
    }
    if node is None:
        return scope
    seen_fn = False
    cur: ast.AST | None = node
    while cur is not None:
        if isinstance(cur, ast.For) and isinstance(cur.target, ast.Name):
            scope["loop"].setdefault(cur.target.id, cur.iter)
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)) and not seen_fn:
            seen_fn = True
            for name, value in file.own_assigns(cur).items():
                scope["local"].setdefault(name, value)
            scope["param"] = _param_defaults(cur)
        if isinstance(cur, ast.ClassDef):
            scope["cls"] = {**file.index.classes.get(cur.name, {}), **scope["cls"]}
        cur = file.parents.get(cur)
    return scope


def _product(left: list[str], right: list[str]) -> list[str]:
    out: list[str] = []
    for head in left:
        for tail in right:
            out.append(head + tail)
            if len(out) >= _MAX_CANDIDATES:
                return out
    return out


def _resolve(
    node: ast.AST,
    scope: dict[str, dict[str, ast.AST]],
    file: _File,
    depth: int = 0,
) -> list[str]:
    """Every concrete string this expression can evaluate to.

    Unresolvable leaves contribute `_UNKNOWN` (a gap) rather than nothing, so a
    query that interpolates a value this resolver cannot follow still derives
    from the Cypher around it — the alternative (dropping the whole candidate)
    is exactly the literal-only blindness this function exists to remove.
    """
    if depth > 8:
        return [_UNKNOWN]
    if isinstance(node, ast.Constant):
        return [node.value] if isinstance(node.value, str) else [_UNKNOWN]
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        out: list[str] = []
        for element in node.elts:
            out.extend(_resolve(element, scope, file, depth + 1))
        return out[:_MAX_CANDIDATES]
    if isinstance(node, ast.JoinedStr):
        candidates = [""]
        for part in node.values:
            inner = part.value if isinstance(part, ast.FormattedValue) else part
            candidates = _product(candidates, _resolve(inner, scope, file, depth + 1))
        return candidates
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _product(
            _resolve(node.left, scope, file, depth + 1),
            _resolve(node.right, scope, file, depth + 1),
        )
    if isinstance(node, ast.Name):
        if node.id in scope["loop"]:
            return _resolve(scope["loop"][node.id], scope, file, depth + 1)
        for table in ("local", "param", "cls", "module"):
            bound = scope[table].get(node.id)
            if bound is not None:
                return _resolve(bound, scope, file, depth + 1)
        imported = _module_consts().get(node.id)
        if imported is not None:
            value, owner = imported
            return _resolve(value, _scope(owner, None), owner, depth + 1)
        return [_UNKNOWN]
    if isinstance(node, ast.Attribute):
        attr = _attr_consts().get(node.attr)
        if attr is not None:
            value, owner = attr
            return _resolve(value, _scope(owner, None), owner, depth + 1)
        return [_UNKNOWN]
    if isinstance(node, ast.Call):
        # `frozenset({...})` / `set({...})` / `tuple([...])` / `list([...])` over
        # a resolvable member list — `frozenset` is the IDIOMATIC shape for a rel
        # family in this package (`DERIVABLE_STRUCTURAL_RELS`, `_ABOUT_REPLAY_RELS`,
        # `ENTITY_LINKED_RELS`), and a `for rel in CONSTANT` loop over one is the
        # same read as a loop over a tuple. Without this branch those loops fell
        # through to the gap and the family constants escaped the census.
        if (
            isinstance(node.func, ast.Name)
            and node.func.id in ("frozenset", "set", "tuple", "list")
            and len(node.args) == 1
        ):
            return _resolve(node.args[0], scope, file, depth + 1)
        # `"|".join(ABOUT_EDGE_TYPES)` — the other call shape that builds a rel
        # pattern: a separator constant joined over a constant's members.
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "join"
            and len(node.args) == 1
        ):
            separators = _resolve(node.func.value, scope, file, depth + 1)
            members = _resolve(node.args[0], scope, file, depth + 1)
            return [sep.join(members) for sep in separators][:_MAX_CANDIDATES]
        return [_UNKNOWN]
    if isinstance(node, ast.IfExp):
        return (
            _resolve(node.body, scope, file, depth + 1)
            + _resolve(node.orelse, scope, file, depth + 1)
        )[:_MAX_CANDIDATES]
    return [_UNKNOWN]


def _may_traverse_every_type(source: str) -> bool:
    """Text pre-filter for the empty-pattern class: is there a relationship
    pattern with no type in it?

    Both spellings count: the bracketed `-[r]->` (no type slot at all) and the
    bracket-less `(n)--()`. Cheap, and only a filter — `_is_empty_pattern_read`
    decides. It must stay a SUPERSET of the class, or a filtered-out file is a
    reader that ships green — which is exactly how the bracket-less member was
    missed while `EMPTY_PATTERN` was floor-checked.
    """
    if any(_type_slot(m.group(1)) is None for m in _EMPTY_TRAVERSAL.finditer(source)):
        return True
    return bool(_BARE_TRAVERSAL.search(source))


def _may_have_gap_type(source: str) -> bool:
    """Text pre-filter for the call-time class: is any type slot an EXPRESSION?

    Also only a filter (`_is_gap_type_read` decides) — but it has to be a
    SUPERSET of the class, or the walk never reaches the site it exists for: a
    filtered-out file is a reader that ships green.
    """
    return bool(_GAP_TYPE_HINT.search(source))


def _docstrings(file: _File) -> set[int]:
    """The `id()`s of every module/class/function docstring Constant.

    A docstring is prose that may QUOTE Cypher (this package's docs do, e.g. a
    "tried `MATCH (n)<-[r]-(m)` and it scans" note), so it is not a site: only an
    executed string literal is. Without this, the class walk would derive a
    function whose docstring merely mentions the pattern.
    """
    found: set[int] = set()
    for node in ast.walk(file.tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = getattr(node, "body", [])
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            found.add(id(body[0].value))
    return found


def _is_read(text: str) -> bool:
    """Does this Cypher string traverse the edge under a reading clause?

    Per traversal, the *last* governing clause before it decides: `MATCH`/
    `OPTIONAL MATCH` read, `MERGE`/`CREATE` mint.
    """
    for match in _TRAVERSAL.finditer(text):
        clauses = _CLAUSE.findall(text[: match.start()])
        if not clauses:
            continue
        governing = " ".join(clauses[-1].split()).upper()
        if governing in ("MATCH", "OPTIONAL MATCH"):
            return True
    return False


def _type_slot(content: str) -> str | None:
    """What TYPE a bracketed traversal names, decided once for both shapes.

    The type slot is the text after the first `:` in the part BEFORE any property
    map — a colon inside `{...}` is a map key, not a type (which is how
    `MATCH (n)-[r {idx:$i}]->(m)` names no type while carrying a colon).

    * `None` — the bracket carries no type slot at all: `[r]`, `<-[r]-`,
      `[r*1..2]`, `[r {idx:$i}]`. It walks EVERY relationship type.
    * `""` — the slot exists and is EMPTY: `[r:]`, `[:]`, `[r: {c:$c}]`. That is
      what an interpolation the resolver could not fill leaves behind, so this is
      the call-time class's signature.
    * anything else — the type the pattern NAMES (`aboutObject`, `A|B`).
    """
    head = content.split("{", 1)[0]
    if ":" not in head:
        return None
    return re.split(r"[{*\s]", head.split(":", 1)[1].strip(), maxsplit=1)[0]


def _reads_at(text: str, start: int) -> bool:
    """Is the traversal at `start` governed by a reading clause?

    Per traversal, the *last* clause before it decides: `MATCH`/`OPTIONAL MATCH`
    read, `MERGE`/`CREATE` mint. Shared by both shape walks, so neither can drift
    from the other (or from `_is_read`).
    """
    clauses = _CLAUSE.findall(text[:start])
    if not clauses:
        return False
    return " ".join(clauses[-1].split()).upper() in ("MATCH", "OPTIONAL MATCH")


def _is_empty_pattern_read(text: str) -> bool:
    """Does this Cypher traverse a relationship it does NOT name?

    Same governing-clause rule as `_is_read` — only a `MATCH`/`OPTIONAL MATCH`
    makes it a read — but the subject is the pattern's SHAPE: a traversal with no
    type slot at all (`-[r]->`, `<-[r]-`, `-[r {c:$c}]->`, and their bracket-less
    spelling `(n)--()`) has no type, so it walks every relationship type and
    `aboutObject` is one of them. Both spellings are checked, because a reader
    written in the bracket-less one is the same read and was invisible here.
    """
    for match in _EMPTY_TRAVERSAL.finditer(text):
        if _type_slot(match.group(1)) is not None:
            continue
        if _reads_at(text, match.start()):
            return True
    return any(_reads_at(text, match.start()) for match in _BARE_TRAVERSAL.finditer(text))


def _is_gap_type_read(text: str) -> bool:
    """Does this Cypher traverse a relationship whose type is a GAP?

    The third shape, and the one no name-based scan can see: the type slot is
    filled by an expression (a parameter, a dict lookup, a journal field), which
    `_resolve` follows one level and then leaves empty — `-[:{rel}]->` resolves to
    `-[:]->`. A bracketed traversal with an empty type slot is therefore the
    signature of a call-time relationship name, and `aboutObject` may be what the
    expression evaluates to at run time.

    A literal `[r:]` would be invalid Cypher, so an empty slot on a resolved
    candidate is always an interpolation the resolver could not fill.
    """
    for match in _EMPTY_TRAVERSAL.finditer(text):
        if _type_slot(match.group(1)) != "":
            continue
        if _reads_at(text, match.start()):
            return True
    return False


def _roots(file: _File) -> list[ast.AST]:
    """The outermost string expressions — the unit a site is derived from.

    One Cypher statement is often several AST nodes: adjacent literals and
    f-string parts concatenate into a `BinOp`/`JoinedStr`, so descending to each
    `Constant` would split a statement and see neither the clause nor the
    relationship name. Only the outermost node is a root; its parts are joined
    by `_resolve`.
    """
    roots: list[ast.AST] = []
    for node in ast.walk(file.tree):
        parent = file.parents.get(node)
        inside_concat = isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.Add)
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, str):
                continue
            if isinstance(parent, (ast.JoinedStr, ast.FormattedValue)) or inside_concat:
                continue
            roots.append(node)
        elif (
            isinstance(node, ast.JoinedStr)
            or (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add))
        ) and not inside_concat:
            roots.append(node)
    return roots


def _site_symbol(node: ast.AST, file: _File) -> list[str]:
    """The symbol(s) a Cypher expression belongs to.

    The innermost function/method is the site; a module-level constant is
    attributed to the function(s) that consume it, because that is where the
    read happens (`subgraph._CYPHER_ABOUT` is executed by `_collect_raw`).
    """
    chain: list[str] = []
    cur = file.parents.get(node)
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            chain.append(cur.name)
        cur = file.parents.get(cur)
    if chain:
        # innermost function wins; a `Klass.method` qualifier is a convenience
        # path, not a symbol (the same rule `citation_rot` applies).
        return [chain[0]]

    const_name = None
    for stmt in file.tree.body:
        if not isinstance(stmt, ast.Assign):
            continue
        if any(target is node for target in ast.walk(stmt.value)):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    const_name = target.id
    if const_name is None:
        return ["<module>"]

    return file.const_users().get(const_name, []) or [f"<module:{const_name}>"]


def _sites_in(file: _File) -> set[tuple[str, int]]:
    """`{(symbol, line)}` — every READ site this file derives.

    NO text gate of its own. `_files()` already admitted this module — on the
    edge's name, a rel-family constant, an every-type pattern, or a gap type —
    and re-testing the first two predicates here re-imposed on the walk exactly
    the blindness `_files()` exists to refute: a module admitted BECAUSE OF ITS
    SHAPE was parsed and then deliberately not walked. The live shape such a gate
    drops is a constant FRAGMENT interpolated into the type slot
    (`_REL = "about"` -> `f"...[:{_REL}Object]..."`), which `_resolve` follows
    and the gate hid — against §13's own promise that a reader *"that names the
    edge — literally, or through a constant the resolver can follow — reds the
    guard"*. It also made the superset arm unfalsifiable: `test_the_file_filter_
    un_sees_nothing` re-uses THIS walk as its proof, so a gate narrowing here was
    invisible to it as well.

    Docstrings are skipped for the reason `_docstrings()` states — prose that
    QUOTES a pattern is not an executed read. The empty-pattern and call-time
    walks both apply that rule; this one did not, so a docstring quoting
    `MATCH (n)-[:aboutObject]->(m)` would have demanded a §13 row for a comment.
    """
    docstrings = _docstrings(file)
    sites: set[tuple[str, int]] = set()
    for node in _roots(file):
        if id(node) in docstrings:
            continue
        candidates = _resolve(node, _scope(file, node), file)
        if not any(EDGE in text and _is_read(text) for text in candidates):
            continue
        line = getattr(node, "lineno", 0)
        for symbol in _site_symbol(node, file):
            sites.add((symbol, line))
    return sites


@lru_cache(maxsize=1)
def _rel_constant_names() -> frozenset[str]:
    """Constants whose own value carries the edge name.

    The census's file filter: a module can build its rel pattern out of
    `SUPERSEDE_STRUCTURAL_RELS` without ever writing the literal, so reading only
    files that contain `aboutObject` would reproduce the blindness one level up.
    """
    names: set[str] = set()
    for table in (_module_consts(), _attr_consts()):
        for name, (value, owner) in table.items():
            if any(EDGE in text for text in _resolve(value, _scope(owner, None), owner)):
                names.add(name)
    return frozenset(names)


@lru_cache(maxsize=1)
def _census() -> dict[tuple[str, str], tuple[int, ...]]:
    """Every site under `tortoise/` whose Cypher traverses the edge.

    `{(relative_path, symbol): (line numbers)}` — READ sites only.

    Cached: three tests read it and it parses the whole package with `ast`.
    """
    sites: dict[tuple[str, str], list[int]] = {}
    for file in _files():
        for symbol, line in sorted(_sites_in(file)):
            sites.setdefault((file.rel, symbol), []).append(line)
    return {key: tuple(value) for key, value in sites.items()}


@lru_cache(maxsize=1)
def _empty_pattern_sites() -> dict[tuple[str, str], tuple[int, ...]]:
    """Every site that reads the edge through a pattern naming NO type.

    A separate walk from `_census()` because the site is not identified by a
    name at all — the class is derived from the SHAPE of the pattern. Kept apart
    so the completeness arm can say which class a missing site belongs to, and
    so the class's own declaration (`EMPTY_PATTERN`) is checked against what the
    code actually does rather than against itself.
    """
    sites: dict[tuple[str, str], list[int]] = {}
    for file in _files():
        if not _may_traverse_every_type(file.source):
            continue
        docstrings = _docstrings(file)
        for node in _roots(file):
            if id(node) in docstrings:
                continue
            candidates = _resolve(node, _scope(file, node), file)
            if not any(_is_empty_pattern_read(text) for text in candidates):
                continue
            line = getattr(node, "lineno", 0)
            for symbol in _site_symbol(node, file):
                sites.setdefault((file.rel, symbol), []).append(line)
    return {key: tuple(sorted(value)) for key, value in sites.items()}


def _call_time_sites_in(file: _File) -> set[tuple[str, int]]:
    """`_call_time_sites()` restricted to one (usually synthetic) module.

    The mechanism arm's harness: it lets a synthetic source — a reader of a shape
    the tree does not contain — be run through the SAME derivation the real
    census uses, so "a new member of this class cannot ship green" is measured on
    a source that would be a new member, not on today's tree.
    """
    docstrings = _docstrings(file)
    sites: set[tuple[str, int]] = set()
    for node in _roots(file):
        if id(node) in docstrings:
            continue
        candidates = _resolve(node, _scope(file, node), file)
        if not any(_is_gap_type_read(text) for text in candidates):
            continue
        line = getattr(node, "lineno", 0)
        for symbol in _site_symbol(node, file):
            sites.add((symbol, line))
    return sites


@lru_cache(maxsize=1)
def _call_time_sites() -> dict[tuple[str, str], tuple[int, ...]]:
    """Every site that reads an edge whose relationship TYPE is an expression.

    The third class, and the one the census used to DECLARE instead of derive: a
    relationship name assembled at call time (a parameter, a dict lookup, a
    journal field) leaves the type slot empty when the resolver gives up on it, so
    the shape `-[r:]->` is the class's signature — exactly as `-[r]->` is the
    empty-pattern class's. Deriving it is what makes a NEW reader of the shape red
    instead of shipping green: a hand list can only check the entries it was
    given, which is how `traverse`, `ingest` and the replay reads were missed.

    Kept apart from the other two walks because each class answers a different
    question, and a missing site has to be reported against the class it belongs
    to ("this names no type" and "this names a type nobody can see" are
    different defects with different fixes).
    """
    sites: dict[tuple[str, str], list[int]] = {}
    for file in _files():
        if not _may_have_gap_type(file.source):
            continue
        for symbol, line in sorted(_call_time_sites_in(file)):
            sites.setdefault((file.rel, symbol), []).append(line)
    return {key: tuple(sorted(value)) for key, value in sites.items()}

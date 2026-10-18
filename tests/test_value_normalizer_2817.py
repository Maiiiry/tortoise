"""#2817 — the S5 prose→value derivation, table-driven per failure mode.

Design of record: ``docs/plans/2026-09-10-ws-c-numeric-normalization.md``
(``v3``), whose **Option (a)** ruling (owner, 2026-10-07, recorded on ``#2817``)
is the predicate every case below tests:

> **OVERRIDES:** we do **not** ship a locale-aware numeric parser or a
> separator/locale pattern ladder — the value is derived from, and kept beside,
> the raw sentence; a value that cannot be read unambiguously is not typed.

The eight failure modes the issue enumerates are tested one table each, and
*"not representable → prose only"* is the expected answer wherever the design
withholds the reading. The three mutation pins at the bottom of each section
are the ones the issue names — separator ladder, float instead of minor units,
verbatim not carried — each reds a specific test (see the PR body's
red-then-green evidence).

Sibling contract: ``tortoise/money.py`` (#2782, PR #8641) is the validate +
consume half and owns ``MONEY_VALUE_FIELDS``, the pinned ISO 4217 table and the
Layer-1 refusal. This file pins the DERIVE half against it — it does not
re-derive it.

Runnable (no graph, no network, no clock):
    uv run python -m pytest tests/test_value_normalizer_2817.py -q
"""
from __future__ import annotations

import ast
import os
import string
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.commit_schema import Point
from tortoise.extractor_v2 import _resolve_source_turn
from tortoise.money import (
    AMOUNT_MINOR,
    AS_OF,
    CURRENCY,
    ISO_4217_MINOR_UNITS,
    MINOR_UNIT_EXPONENT,
    MONEY_VALUE_FIELDS,
    VERBATIM,
    MoneyValueError,
    money_props,
    read_money_value,
)
from tortoise.value_normalizer import (
    DROP_REASONS,
    PROSE_FIELDS,
    Normalization,
    normalize_money,
    normalize_money_props,
)

#: Every reason a table below legitimately expects — and no others. Keeping
#: this closed is the point: a reason is a decision, not a message.
EXPECTED_REASONS = frozenset({
    "no_candidate", "no_currency_anchor", "ambiguous_currency_symbol",
    "not_an_iso4217_code", "ambiguous_separator", "magnitude_word",
    "not_money_percent", "not_money_multiplier", "range_or_qualifier",
    "signed_amount", "year_shaped_amount", "unsupported_token", "no_minor_unit",
    "multiple_candidates", "refused_by_layer1",
})


def typed(source: str) -> dict:
    result = normalize_money(source)
    assert result.typed, f"{source!r} dropped ({result.drop_reason}) — expected a value"
    return result.props


def dropped(source: str, *reasons: str) -> Normalization:
    result = normalize_money(source)
    assert not result.typed, f"{source!r} typed as {result.props} — must abstain"
    assert result.drop_reason in (reasons or tuple(DROP_REASONS)), (
        f"{source!r} dropped with {result.drop_reason!r}, expected {reasons}")
    return result


# ── the closed vocabulary ──────────────────────────────────────────────────
def test_the_drop_vocabulary_is_closed():
    assert DROP_REASONS == EXPECTED_REASONS


# ── the unambiguous forms: what the design DOES commit to typing ────────────
@pytest.mark.parametrize("source,amount,currency,exponent", [
    ("EUR 800", 80_000, "EUR", 2),
    ("800 EUR", 80_000, "EUR", 2),
    ("800EUR", 80_000, "EUR", 2),
    ("BRL 25000000", 2_500_000_000, "BRL", 2),
    ("The tranche released EUR 800 last week.", 80_000, "EUR", 2),
    # mode 4 — only a DISAMBIGUATED symbol is a currency anchor
    ("R$ 800", 80_000, "BRL", 2),
    ("US$ 800", 80_000, "USD", 2),
    ("CA$ 800", 80_000, "CAD", 2),
    ("A$ 800", 80_000, "AUD", 2),
    ("AU$ 800", 80_000, "AUD", 2),
    ("MX$ 800", 80_000, "MXN", 2),
])
def test_an_anchored_plain_integer_types(source, amount, currency, exponent):
    props = typed(source)
    assert props[AMOUNT_MINOR] == amount
    assert props[CURRENCY] == currency
    assert props[MINOR_UNIT_EXPONENT] == exponent


def test_an_iso_code_is_matched_in_uppercase_only():
    # Case folding alone is not enough: ``all``, ``top``, ``pen``, ``try``,
    # ``bob``, ``cup``, ``mad``, ``gel``, ``mop`` and ``sos`` are ordinary
    # English words that are ALSO active ISO 4217 codes, so the uppercase form
    # is just as ambiguous as the lowercase one.
    dropped("800 all", "no_currency_anchor")
    dropped("the policy covers all 800 accounts", "no_currency_anchor")
    dropped("800 ALL", "ambiguous_currency_symbol")
    dropped("the 800 ALL cohort", "ambiguous_currency_symbol")
    dropped("The audit found 800 PEN issues", "ambiguous_currency_symbol")
    dropped("800 TOP quality", "ambiguous_currency_symbol")
    # a code that is not a word stays an anchor, which is the whole contract.
    assert typed("800 SEK")[CURRENCY] == "SEK"
    assert typed("800 EUR")[CURRENCY] == "EUR"


# ── mode 1: separator ambiguity — DROP, never guess ────────────────────────
@pytest.mark.parametrize("source", [
    "1.234",                 # PT-BR thousands OR EN decimal — both plausible
    "1,234",                 # EN thousands OR PT-BR decimal — both plausible
    "R$ 1.234,56",           # the issue's named 1000x regression
    "EUR 1,234.56",          # the mirror form
    "EUR 1.234",
    "USD 800.00",            # 800.00 EN / 80000 DE — still ambiguous
    "BRL 1 234",             # whitespace grouping
    "R$ 1'234",              # Swiss grouping (ASCII)
    "R$ 1\u2019234,56",        # the SAME form with a typographic apostrophe —
                             # typed as 1.00 before the review found it
    "USD 1\u2019000\u2019000",
    "EUR 1_000",              # underscore grouping
    "EUR 1\u00a0234",        # NBSP grouping
    # the cycle-3 P1 forms: separators OUTSIDE the charset the tokenizer knew —
    # each typed the truncated prefix (EUR 1.00 → 100 minor) before the guard
    # was inverted to "any punctuation, then a digit"
    "EUR 1\u00b7234",        # middle dot U+00B7
    "EUR 1\u2027234",        # hyphenation point U+2027
    "EUR 1\u2219234",        # bullet operator U+2219
    "EUR 1\u2010234",        # hyphen U+2010
    "EUR 1\u066b234",        # Arabic decimal separator U+066B
    "EUR 1:234",
    "EUR 1|234",
])
def test_separator_forms_never_type_never_guess(source):
    """⛔ THE decision (mode 1). Mutation pin: adding a separator rung — e.g.
    stripping dots as thousands in :func:`_classify_token` — reds this test."""
    # a form with no anchor at all abstains for that reason; with one, the
    # separator is the reason — either way the separator reading is refused
    result = normalize_money(source)
    assert not result.typed
    assert result.drop_reason in ("ambiguous_separator", "no_currency_anchor")


def test_the_named_regression_is_neither_read_as_en_nor_as_ptbr():
    """``R$ 1.234,56`` must not be ACCEPTED AS ``1.234`` — it is not typed at
    all (design §6/T2 restated, and §6/T3's posture for the anchor-free case)."""
    result = normalize_money("R$ 1.234,56")
    assert result.props is None, "no reading may be produced at all"
    assert result.drop_reason == "ambiguous_separator"


#: Separator characters by family — none may END the number at its first digit
#: group. Deliberately a set of CLASSES (ASCII, apostrophes, spaces, dots,
#: dashes-as-separators), not a list of "known bad" characters: the class is
#: what is under test, so a separator nobody thought of must abstain too.
_SEPARATOR_CHARS = [
    ".", ",", "'", "\u2018", "\u2019", "_", " ", "\u00a0", "\u202f",
    "\u00b7",        # middle dot
    "\u2027",        # hyphenation point
    "\u2219",        # bullet operator
    "\u2010",        # hyphen (not the range dash)
    "\u066b",        # Arabic decimal separator
]


@pytest.mark.parametrize("sep", _SEPARATOR_CHARS, ids=lambda c: f"U+{ord(c):04X}")
def test_any_separator_family_abstains(sep):
    """Cycle-3 P1: the separator rule was an ALLOWLIST, so any grouping
    separator outside it truncated the number and typed the prefix (``EUR
    1·234`` → ``1.00``, the named 1000× class). The guard is inverted — the
    token ends before punctuation-then-digit — so the CLASS abstains."""
    result = normalize_money(f"EUR 1{sep}234")
    assert not result.typed, f"{sep!r} typed {result.props}"
    assert result.drop_reason == "ambiguous_separator", result.drop_reason


@pytest.mark.parametrize("sep", _SEPARATOR_CHARS, ids=lambda c: f"U+{ord(c):04X}")
@pytest.mark.parametrize("gap", [" ", "  ", "\n", "\u00a0"], ids=repr)
def test_any_separator_family_abstains_through_whitespace(sep, gap):
    """Cycle-4 P1: the class was closed only for an ADJACENT separator, so a
    stray space reopened it — ``EUR 1 .234`` and ``R$ 1\n.234,56`` (a number
    broken across a line, which is how a real extract delivers it) both typed
    ``1.00``, the same truncated prefix the class names. Whitespace is not an
    escape hatch: the guard skips it on both sides of the punctuation, so the
    invariant holds for every separator × every gap that can precede it."""
    result = normalize_money(f"EUR 1{gap}{sep}234")
    assert not result.typed, f"{gap!r}{sep!r} typed {result.props}"
    assert result.drop_reason == "ambiguous_separator", result.drop_reason


@pytest.mark.parametrize("text", [
    "EUR 1 .234", "R$ 1 .234,56", "EUR 800 ,00", "R$ 1\n.234,56",
    "EUR 1 . 234",
])
def test_whitespace_never_reopens_the_separator_class(text):
    """The named regression, verbatim: each of these typed a value before
    cycle 4. A false TYPE is the one outcome this module must never produce,
    and ``1.00`` from ``1.234`` is exactly it."""
    result = normalize_money(text)
    assert not result.typed, f"{text!r} typed {result.props}"
    assert result.drop_reason == "ambiguous_separator", result.drop_reason


def test_every_ascii_punctuation_separator_abstains():
    """The same invariant over the whole ASCII punctuation class (the reason
    may differ — ``-`` and ``/`` are range connectors — but no value appears)."""
    for ch in sorted(set(string.punctuation)):
        result = normalize_money(f"EUR 1{ch}234")
        assert not result.typed, f"{ch!r} typed {result.props}"


def test_every_unicode_punctuation_or_space_separator_abstains():
    """... and over the Unicode punctuation/space space between U+00A0 and
    U+206F, which is where the separators the allowlist kept missing live.

    A property, not another instance: it fails for ANY character in the class
    that lets the number end at its first group, including ones added by a
    future Unicode version."""
    checked = 0
    for cp in range(0x00A0, 0x2070):
        ch = chr(cp)
        if unicodedata.category(ch)[0] not in "PSZC":
            continue
        checked += 1
        result = normalize_money(f"EUR 1{ch}234")
        assert not result.typed, (
            f"U+{cp:04X} {unicodedata.name(ch, '?')} typed {result.props}")
    assert checked > 100, "the sweep stopped covering the class"


# ── mode 2: magnitude words — no table, so no value ────────────────────────
@pytest.mark.parametrize("source", [
    "EUR 12M",               # M is 10^3 (fixed income) and 10^6 (SI) — and
                             # _value_signature already reads bare M as metres
    "USD 5k",
    "EUR 8MM",
    "BRL 3 bn",
    "R$ 5 million",
    "R$ 1 milhão",
    "12 million EUR",
    "EUR 12 thousand",
    # the review-P1 forms: a magnitude SUFFIX written as its own word
    "EUR 800 M",
    "EUR 800 MM",
    "USD 5 K",
    "EUR 800 m",
    "BRL 3 bn",
])
def test_magnitude_forms_never_type(source):
    dropped(source, "magnitude_word")


# ── mode 3: percent vs multiplier — not money, no valueKind ────────────────
@pytest.mark.parametrize("source,reason", [
    ("EUR 12%", "not_money_percent"),
    ("12% of the tranche", "not_money_percent"),
    ("0.5-2%", "not_money_percent"),
    ("EUR 12x", "not_money_multiplier"),
    ("12x", "not_money_multiplier"),
])
def test_percent_and_multiplier_are_not_money(source, reason):
    # design §4 puts the five valueKinds out of scope: a ratio is prose-only
    # here, and #2521 owns numeric aggregation.
    dropped(source, reason)


# ── mode 4: currency-symbol collisions — never a graph default ─────────────
@pytest.mark.parametrize("source", ["$ 800", "¥ 800", "€ 800", "£ 800",
                                    "the cost was $ 1.4m"])
def test_a_bare_currency_symbol_is_not_a_currency(source):
    """`$` ∈ {USD, CAD, AUD, BRL, MXN} and `£` ∈ {GBP, EGP, LBP, ...} — a
    symbol alone names no currency, and the design forbids a graph default
    (§3/OD5, §6/T4). Mutation pin: treating `$` as USD reds this test."""
    dropped(source, "ambiguous_currency_symbol")


def test_an_unknown_alpha3_token_is_refused_not_silently_unanchored():
    dropped("800 XYZ", "not_an_iso4217_code")


@pytest.mark.parametrize("source", [
    "EUR 800 VAT inclusive.",
    "EUR 800 CEO bonus",
    "EUR 800 ARR contract.",
])
def test_an_uppercase_neighbour_does_not_discard_a_valid_anchor(source):
    """Cycle-2 P2: ``VAT``/``CEO``/``ARR`` are not currencies, but they are not
    reasons to throw away the amount ``EUR 800`` either."""
    assert typed(source)[AMOUNT_MINOR] == 80_000


@pytest.mark.parametrize("source", ["800 AMD shares", "800 PHP requests"])
def test_a_ticker_shaped_code_is_an_ambiguous_currency(source):
    dropped(source, "ambiguous_currency_symbol")


@pytest.mark.parametrize("source", [
    "the IRR 800 target",            # internal rate of return
    "the SAR 800 delegates",        # Special Administrative Region
    "AED 800 units were deployed",  # automated external defibrillator
    "800 SAR",
])
def test_a_high_collision_acronym_is_an_ambiguous_currency(source):
    """Cycle-3 P2 (design gap): ``_WORD_CODES`` closed the collision only for
    word-shaped codes, so an uppercase ACRONYM that is also an active ISO 4217
    code typed a foreign currency out of prose (``the IRR 800 target`` → 800.00
    IRR). The abstain-on-ambiguous-anchor posture (§3/OD5, §6/T4) covers this
    register too, so the collision is refused rather than guessed."""
    dropped(source, "ambiguous_currency_symbol")


def test_the_anchor_collision_residual_is_stated_not_implied_closed():
    """The acronym list is CURATED and cannot close the class (an unnamed
    acronym still types a foreign currency). The module says so explicitly —
    pinning the statement keeps it from being tidied away, which would let the
    posture read as fully closed when it is only as wide as the list."""
    from tortoise import value_normalizer
    doc = " ".join((value_normalizer.__doc__ or "").split())
    assert "Recorded residual" in doc
    assert "a curated list cannot close that class" in doc


def test_an_amount_is_not_fabricated_across_a_line_break():
    """Cycle-2 P2: the anchor↔number gap must not cross a newline."""
    dropped("budget in EUR\n800 people were hired", "no_currency_anchor")


def test_a_value_without_any_anchor_is_not_typed():
    dropped("800", "no_currency_anchor")
    dropped("the tranche released 800 last week", "no_currency_anchor")


# ── mode 5: ranges and qualifiers — prose only ─────────────────────────────
@pytest.mark.parametrize("source", [
    "EUR 5-10",
    "EUR 800 to 900",
    "under EUR 800",
    "about EUR 800",
    "approximately EUR 800",
    "up to EUR 800",
    "at least EUR 800",
    "~EUR 800",
    "EUR 800+",
    "EUR 800 post-money",
    # the review-P1 forms: a bound/approximation written AFTER the amount
    "USD 800 or less",
    "USD 800 or more",
    "EUR 800 approx",
    "EUR 800 approximately",
    "EUR 800 (approx)",
    "EUR 800 maximum",
    "EUR 800 minimum",
    "EUR 800-ish",
    "EUR 800 or so",
    # the cycle-2 P1 forms: a range whose connector is not a hyphen
    "between EUR 800 and 900",
    "EUR 800 or 900",
    "EUR 800/900",
    "EUR 800 to 900",
    # ... and the qualifiers the comma/word forms hid
    "EUR 800, approx.",
    "almost EUR 800",
    "an estimated EUR 800",
    "circa EUR 800",
    # the cycle-3 P1 forms: the connector is separated from the lower bound by
    # an approximation word, which the previous ±6-character window could not
    # see — the lower bound was typed as a point value
    "EUR 800 or roughly 900",
    "EUR 800 and about 900",
    "EUR 800 to roughly 900",
    "EUR 800 or approximately 900",
    "EUR 800 and nearly 900",
    # the mirror: the AMOUNT is the upper bound, the connector sits before it
    "800 or roughly 900 EUR",
    "800 to about 900 EUR",
    # the cycle-3 P2 forms: the pre-position abbreviation carried its dot
    "approx. EUR 800",
    "approx., EUR 800",
    "est. EUR 800",
    "about, say, EUR 800",
])
def test_ranges_and_qualifiers_never_type(source):
    dropped(source, "range_or_qualifier")


@pytest.mark.parametrize("source", [
    "-EUR 800", "\u2212EUR 800", "(-EUR 800)", "the delta was -EUR 800",
    "EUR -800",
    # the cycle-2 P1 forms: the sign is separated from the number
    "- 800 EUR", "\u2212 800 EUR", "( 800 EUR)", "pilot - 800 EUR",
])
def test_a_signed_amount_is_not_typed_positive(source):
    """A sign convention is not defined by the design (§3's register), and
    typing ``-EUR 800`` as +800.00 is the worst false type available — so an
    adjacent sign abstains (review P1)."""
    dropped(source, "signed_amount")


@pytest.mark.parametrize("source", [
    "EUR " + "9" * 5000,          # Python 3.12 refuses int() past 4300 digits
    "EUR 9" * 3 + "9" * 40,
])
def test_an_implausibly_long_number_abstains_instead_of_raising(source):
    dropped(source, "unsupported_token", "multiple_candidates",
            "ambiguous_separator")


def test_a_range_elsewhere_in_the_sentence_is_not_an_amount_range():
    """The range check is scoped to the amount's neighbourhood (review P2): an
    unrelated ``3-4`` / time range / year range must not drop it."""
    for source in ("The budget of EUR 800 covers 3-4 hires",
                   "EUR 800 at 09:00-10:00",
                   "EUR 800 for 2026-2027"):
        assert typed(source)[AMOUNT_MINOR] == 80_000, source


def test_a_date_adjacent_to_the_amount_is_not_read_as_one():
    """A date component is not an amount (review P2): ``as of 2026-03-31 EUR
    800`` must type one value, not abstain on ``31 EUR``."""
    props = typed("as of 2026-03-31 EUR 800")
    assert props[AMOUNT_MINOR] == 80_000
    assert props[AS_OF] == "2026-03-31"
    dropped("EUR 2026-03-31", "no_currency_anchor")


def test_a_date_range_is_not_an_amount_range():
    """A dated sentence still types its amount — the range check runs on the
    text with ISO dates removed, so `2026-01-01 to 2026-03-31` is not a range."""
    props = typed("EUR 800 committed between 2026-01-01 to 2026-03-31")
    assert props[AMOUNT_MINOR] == 80_000


# ── mode 6: the minor-unit exponent travels WITH the amount ────────────────
@pytest.mark.parametrize("source,amount,currency,exponent", [
    ("JPY 800", 800, "JPY", 0),          # 0 minor digits
    ("KWD 800", 800_000, "KWD", 3),      # 3 minor digits
    ("EUR 800", 80_000, "EUR", 2),
    ("BRL 800", 80_000, "BRL", 2),
    ("CLF 800", 8_000_000, "CLF", 4),
])
def test_the_exponent_is_the_pinned_iso4217_one(source, amount, currency,
                                                exponent):
    props = typed(source)
    assert props[MINOR_UNIT_EXPONENT] == exponent
    assert ISO_4217_MINOR_UNITS[currency] == exponent
    assert props[AMOUNT_MINOR] == amount


@pytest.mark.parametrize("source", [
    "the 2026 EUR/USD forecast",      # a year, not 2026 euros
    "In the 2026 EUR budget we spend it.",
    "the 2026 USD forecast",
    "JPY 2026",
])
def test_a_year_shaped_token_is_not_an_amount(source):
    dropped(source, "year_shaped_amount")
    # a non-year 4-digit amount still types
    assert typed("USD 8000")[AMOUNT_MINOR] == 800_000


@pytest.mark.parametrize("source", ["XAU 800", "XTS 800"])
def test_a_currency_with_no_minor_unit_has_no_amount(source):
    dropped(source, "no_minor_unit")


# ── mode 7: the verbatim round-trip ────────────────────────────────────────
@pytest.mark.parametrize("source", [
    "R$ 800",
    "The tranche released EUR 800 last week.",
    "EUR 12",
    "as of 2026-03-31, EUR 800 committed",
])
def test_the_verbatim_is_a_byte_exact_substring_and_recovers_the_amount(source):
    """Mutation pin: dropping ``verbatim=`` from the ``money_props`` call (or
    storing a normalized/digested text) reds this test."""
    props = typed(source)
    verbatim = props[VERBATIM]
    assert verbatim in source, "the exact source text must be carried"
    assert set(props) <= set(MONEY_VALUE_FIELDS)
    # the value is recoverable FROM the stored value: digits x 10^exponent
    assert int(verbatim) * 10 ** props[MINOR_UNIT_EXPONENT] \
        == props[AMOUNT_MINOR]


def test_the_value_is_traceable_to_its_source_turn():
    """The typed value + the turn it came from: ``verbatim`` locates the turn
    through the repo's own resolver (``_resolve_source_turn``), so a consumer
    can re-read the sentence instead of trusting a distillation."""
    edus = [
        {"index": 0, "text": "We agreed the pilot scope with the vendor."},
        {"index": 1, "text": "The tranche released EUR 800 last week."},
        {"index": 2, "text": "Next review is in April."},
    ]
    props = typed(edus[1]["text"])
    turn = _resolve_source_turn(
        {"content": edus[1]["text"], "quote": props[VERBATIM]}, edus,
        warnings=[])
    assert turn == 1
    assert props[VERBATIM] in edus[turn]["text"]


# ── mode 8: asOf ≠ when ────────────────────────────────────────────────────
def test_as_of_is_only_an_asserted_date():
    props = typed("as of 2026-03-31, EUR 800 committed")
    assert props[AS_OF] == "2026-03-31"


@pytest.mark.parametrize("source", [
    "on 2026-03-31 we committed EUR 800",     # an occurrence time, not validity
    "EUR 800",                                # no date stated → absent (OD3)
    "as of next tuesday, EUR 800",            # junk is not a date
    "as of 30/06/2026, EUR 800",              # not ISO
])
def test_as_of_is_absent_when_the_text_states_no_assertion_date(source):
    assert AS_OF not in typed(source)


@pytest.mark.parametrize("shape_only", ["2026-99-99", "2026-02-31"])
def test_as_of_is_shape_only_in_parity_with_point_when(shape_only):
    """``Point._iso_date_prefix`` is a SHAPE gate (``YYYY-MM-DD...``), not a
    calendar: ``2026-99-99`` passes both contracts. Pinning the agreement is
    the point — a stricter calendar check here would break the parity this
    issue asks for."""
    assert _point(when=shape_only).when == shape_only
    assert money_props(100, "USD", minor_unit_exponent=2,
                       as_of=shape_only)[AS_OF] == shape_only


def test_as_of_never_borrows_the_occurrence_slot():
    props = typed("on 2026-03-31 we committed EUR 800")
    assert AS_OF not in props
    assert "when" not in props


def test_two_asserted_dates_do_not_pick_one():
    assert AS_OF not in typed(
        "as of 2026-03-31 and as of 2026-06-30, EUR 800 committed")


# ── one sentence, several amounts: never pick one ──────────────────────────
def test_two_different_amounts_abstain():
    dropped("EUR 800 and USD 900", "multiple_candidates")
    dropped("EUR 8000 EUR 9000", "multiple_candidates")


def test_the_same_amount_stated_twice_is_one_value():
    assert typed("EUR 800 now and EUR 800 in April")[AMOUNT_MINOR] == 80_000


# ── the S5 call pattern: a drop is a WARNING, never silence ────────────────
def test_a_drop_warns_with_its_reason():
    warnings: list[str] = []
    assert normalize_money_props("R$ 1.234,56", warnings=warnings) is None
    assert len(warnings) == 1
    assert "ambiguous_separator" in warnings[0]
    assert "R$ 1.234,56" in warnings[0]


def test_a_typed_value_warns_nothing():
    warnings: list[str] = []
    props = normalize_money_props("EUR 800", warnings=warnings)
    assert props is not None and warnings == []


# ── Layer 1: the value's contract, in parity with Point._iso_date_prefix ────
def _point(**overrides) -> Point:
    payload = {
        "id": "pt_" + "0" * 64,
        "content": "a claim",
        "pointKind": "statement",
        "reason": "NEW",
        "confidence": 0.5,
        "c_cal": 0.5,
        "source_ref": "session.md",
    }
    payload.update(overrides)
    return Point(**payload)


@pytest.mark.parametrize("value", [
    "2026-03-31", "2026-03-31T00:00:00Z", "2026-03-31 12:00", " 2026-03-31 ",
    "", "next tuesday", "2026-6-30", "2026-06-30junk", "31/03/2026", "null",
])
def test_as_of_accepts_exactly_what_point_when_accepts(value):
    """For every non-empty candidate, acceptance is identical (the ``""`` row
    is asserted below as the one documented divergence, not as parity).

    No symmetric parity test existed for the date contract: the asOf rules
    were asserted inline in ``tests/test_money_value_2782.py`` (``:181-204``)
    with a comment pointing at ``Point._iso_date_prefix``. This is the mirror
    for the VALUE: for every candidate the Layer-1 validator
    (``money_props``) accepts iff ``Point.when`` accepts. It reds the moment
    either regex drifts."""
    if not value:
        # The ONE documented divergence, not a parity failure: ``when=""``
        # MEANS "undated" on a Point, while an ``asOf`` that is present must be
        # a date (absence is expressed by omitting the field, per OD3). The
        # derivation never produces an empty asOf, so this row cannot be hit.
        assert _point(when=value).when == value
        with pytest.raises(MoneyValueError):
            money_props(100, "USD", minor_unit_exponent=2, as_of=value)
        return
    try:
        _point(when=value)
        point_accepts = True
    except Exception:
        point_accepts = False
    try:
        money_props(100, "USD", minor_unit_exponent=2, as_of=value)
        money_accepts = True
    except MoneyValueError:
        money_accepts = False
    assert point_accepts == money_accepts, (
        f"{value!r}: Point.when accepts={point_accepts} but asOf "
        f"accepts={money_accepts} — the two Layer-1 contracts have drifted")


@pytest.mark.parametrize("bad", [
    {AMOUNT_MINOR: 800.0, CURRENCY: "EUR", MINOR_UNIT_EXPONENT: 2},   # float
    {AMOUNT_MINOR: True, CURRENCY: "EUR", MINOR_UNIT_EXPONENT: 2},    # bool
    {AMOUNT_MINOR: 800, CURRENCY: "eur", MINOR_UNIT_EXPONENT: 2},     # case
    {AMOUNT_MINOR: 800, CURRENCY: "XYZ", MINOR_UNIT_EXPONENT: 2},     # not ISO
    {AMOUNT_MINOR: 800, CURRENCY: "JPY", MINOR_UNIT_EXPONENT: 2},     # 100x
    {AMOUNT_MINOR: 800, CURRENCY: "USD", MINOR_UNIT_EXPONENT: 3},
])
def test_a_malformed_typed_value_is_refused_at_layer1(bad):
    """The Layer-1 gate is ``tortoise/money.py`` (``money_props`` /
    ``read_money_value``), which the write path shares with this derivation —
    exactly the posture ``_iso_date_prefix`` gives ``when``. Mutation pin: a
    normalizer that emitted a float amount (or skipped the table check) reds
    this test, because its props are then refused at the boundary."""
    with pytest.raises(MoneyValueError):
        read_money_value(bad)
    with pytest.raises(MoneyValueError):
        money_props(bad[AMOUNT_MINOR], bad[CURRENCY],
                    minor_unit_exponent=bad[MINOR_UNIT_EXPONENT])


def test_every_derived_value_round_trips_through_layer1():
    for source in ("EUR 800", "JPY 800", "KWD 800", "R$ 25000000"):
        props = typed(source)
        assert read_money_value(props) == (
            props[CURRENCY], props[AMOUNT_MINOR], props[MINOR_UNIT_EXPONENT])
        assert type(props[AMOUNT_MINOR]) is int, "minor units are NEVER a float"


# ── dual-write: the derivation is additive, prose is untouched ─────────────
def test_no_money_field_can_shadow_a_prose_field():
    assert not (set(MONEY_VALUE_FIELDS) & PROSE_FIELDS)
    assert set(PROSE_FIELDS) == {"content", "quote", "when"}


def test_merging_the_value_leaves_prose_byte_identical():
    source = "The tranche released EUR 800 last week."
    point = {"content": source, "quote": source, "source_turn_id": 1}
    before = dict(point)
    point.update(typed(source))
    assert point["content"] == before["content"] == source
    assert point["quote"] == before["quote"] == source
    assert set(point) - set(before) <= set(MONEY_VALUE_FIELDS)


@pytest.mark.parametrize("anchor_sentence", [
    # the sealed gold anchors spell the number as WORDS — a typed value must
    # never replace them, or the retention metric silently zeroes
    # (tests/eval/write_path/gold/wp01_quarry_debug.gold.json)
    "Halcyon booked twelve million events",
    "thirty percent of alerts are unactionable because the ownership lookup "
    "fails",
    # ... plus a DIGIT-bearing neighbour, so the assertion cannot pass merely
    # because the sentence holds no digits at all (cycle-2 P2)
    "Halcyon booked twelve million events for 2026 and 3 regions",
])
def test_a_number_word_anchor_is_never_typed(anchor_sentence):
    assert normalize_money_props(anchor_sentence) is None


def test_a_long_input_is_not_quadratic():
    """Cycle-2 P2: ``_as_of`` was recomputed per candidate (24 KB took ~10 s).
    A generous bound: a structural regression shows up in orders of magnitude."""
    import time
    source = " ".join(f"EUR {i}" for i in range(1, 3001))
    started = time.monotonic()
    result = normalize_money(source)
    elapsed = time.monotonic() - started
    assert not result.typed
    assert elapsed < 3.0, f"40 KB took {elapsed:.1f}s — the per-candidate rescan is back"


# ── determinism (design §6/T8) ─────────────────────────────────────────────
def test_the_derivation_is_pure():
    source = "as of 2026-03-31, EUR 800 committed"
    assert typed(source) == typed(source) == typed(source)


def test_the_module_imports_no_clock_network_or_locale():
    module_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "tortoise", "value_normalizer.py")
    with open(module_path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"__future__", "re", "dataclasses", "typing",
                        "tortoise"}, imported
    assert "locale" not in imported

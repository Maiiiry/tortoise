"""S5 prose → value derivation (#2817) — money, abstention-first.

This is the **derive** half of the WS-C design
(``docs/plans/2026-09-10-ws-c-numeric-normalization.md``). Its sibling
``tortoise/money.py`` (#2782, PR #8641) is the **validate + consume** half:
it owns the flat field shape (``amountMinor``/``currency``/
``minorUnitExponent``/``verbatim``/``asOf``), the pinned ISO 4217 minor-unit
table, the Layer-1 refusal of a malformed amount, and the sum/ratio
arithmetic. **Nothing here rebuilds any of that** — this module turns raw text
into :func:`tortoise.money.money_props` and invents no second canonicaliser
(``docs/epics/2026-08-20-1509-extractor-v3/00-scope.md:28``).

## The decision this module is built to

> **OVERRIDES:** we do **not** ship a locale-aware numeric parser or a
> separator/locale pattern ladder (the industry default for money and number
> normalization) — the value is derived from, and kept beside, the raw
> sentence; a value that cannot be read unambiguously is not typed, and
> **detecting** a wrong reading is owned by the span-carrying slice, **#2684**.

The owner selected that Option (on ``#2817``, 2026-10-07; folded into design
§2.3 by #7645). Three consequences are load-bearing here:

1. **No separator rule, at all.** ``1.234``, ``1,234``, ``1.234,56``,
   ``1,234.56``, ``800.00`` and ``1 234`` are *all* dropped. There is no
   "PT-BR reads dots as thousands" rung and no "EN reads commas as thousands"
   rung to add one — deciding that ``R$ 1.234,56`` is ``1234.56`` **is** the
   locale question the design excludes. So the named regression is handled by
   the only safe route: ``R$ 1.234,56`` is **not typed at all** (design §6/T2
   as restated, §6/T3).
2. **No magnitude table.** ``milhão``/``bilhão``/``mil``/``k``/``M``/``MM``/
   ``bn``/``million`` are dropped as a class. ``M`` is 10³ in fixed-income
   notation and 10⁶ in SI, and ``_value_signature`` (``extractor_v2.py``)
   already reads a bare ``M`` as *metres* — a second interpretation of the
   same token is how a third opinion gets invented. (Design §3: bare ``M``
   magnitude is "dropped, not open".)
3. **Only an explicit currency anchor types a value.** An ISO 4217 alpha-3
   code (``EUR 800``, ``800 EUR``) or a *disambiguated* dollar prefix
   (``R$``, ``US$``, ``CA$``, ``A$``/``AU$``, ``MX$``). A bare ``$`` is
   USD/CAD/AUD/BRL/MXN and is **never** a currency; a bare ``¥``/``€``/``£``
   is likewise refused; currency is never a graph default (design §3/OD5,
   §6/T4).

The values that survive are the unambiguous ones the design commits to: an
integer with **no separator and no magnitude word**, attached to an explicit
currency. Everything else stays prose — which is a *decision*, not a gap: the
raw sentence is untouched and the retention graders read prose only
(``tests/eval/write_path/schema.py``, ``tests/eval/harness/grading.py``), so
this module is additive and never moves a number out of ``content``/``quote``.

## Contract

* :func:`normalize_money` → :class:`Normalization` (``props`` or a
  ``drop_reason`` from the closed :data:`DROP_REASONS` set). Deterministic: no
  clock, no network, no process-global locale (design §6/T8).
* :func:`normalize_money_props` — the S5 call pattern, mirroring
  ``_valid_iso_date`` in ``extractor_v2.py``: returns ``None`` and *appends a
  warning* rather than guessing.
* Every ``verbatim`` is a **byte-exact substring of the source text**, so the
  value is recoverable from what was stored (design §6/T1) — normalize-then-
  lose-the-source is the silent-corruption path this exists to prevent.
* ``asOf`` is only an **asserted** date (``as of``/``as at``/``effective``),
  never an occurrence time: extracting a date from the sentence into
  ``Point.when``'s meaning would conflate the two (design §3/OD3, mode 8).

Wiring this into ``execute_embed`` is **not** done here: the field's
declaration on the Point property lane is #2782's (PR #8976), and design §7.2
puts declaration (step 2) before derivation (step 3). Writing undeclared props
first would set off the projection's "not declared" drift warning on every
rebuild (design §7.1).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from tortoise.money import (
    ISO_4217_MINOR_UNITS,
    MoneyValueError,
    money_props,
)

__all__ = [
    "DROP_REASONS",
    "Normalization",
    "normalize_money",
    "normalize_money_props",
]

#: The closed set of drop reasons — a reason is a *decision*, so the vocabulary
#: is fixed (a caller keys on these; an invented one is a typo, and
#: ``tests/test_value_normalizer_2817.py`` pins the set). Every reason means
#: "no typed value; the raw prose remains the record".
DROP_REASONS: frozenset[str] = frozenset({
    # nothing to read
    "no_candidate",           # no number in the text at all
    "no_currency_anchor",      # a number, but no explicit currency anchor
    "ambiguous_currency_symbol",  # bare $ / ¥ / € / £ — never a graph default
    "not_an_iso4217_code",     # e.g. "800 XYZ" — looks like money, is not ISO
    # the number itself cannot be read without the excluded locale rule
    "ambiguous_separator",     # 1.234 / 1,234 / 1.234,56 / 800.00 / 1 234
    "magnitude_word",          # million / milhão / mil / k / M / MM / bn
    "not_money_percent",       # 12% — a valueKind outside this design (§4)
    "not_money_multiplier",    # 12x — a multiple, not an amount
    "range_or_qualifier",      # 0.5-2 / under / about / up to / post-money
    "signed_amount",           # -EUR 800 / EUR -800 — a sign convention this
                               # design does not define (OD register, §3)
    "year_shaped_amount",      # "the 2026 EUR/USD forecast" — a 4-digit
                               # 1900..2199 token is a year as often as an amount
    "unsupported_token",       # digits + something this parser will not guess
    # the value exists but cannot be canonically represented
    "no_minor_unit",           # XAU/XTS/... — the standard gives no exponent
    "multiple_candidates",     # two different amounts — never pick one
    "refused_by_layer1",       # money_props() refused the canonical value
})


@dataclass(frozen=True)
class Normalization:
    """One derivation result: either the canonical props, or a drop reason."""

    props: dict[str, Any] | None
    drop_reason: str | None = None
    detail: str = ""

    @property
    def typed(self) -> bool:
        return self.props is not None


# ── anchors ────────────────────────────────────────────────────────────────
#: A dollar prefix that names its currency. A BARE ``$`` is in none of these
#: on its own (mode 4): ``$`` ∈ {USD, CAD, AUD, BRL, MXN}. Only the forms the
#: text disambiguates are anchors.
_SYMBOL_ANCHORS: tuple[tuple[str, str], ...] = (
    (r"R\$", "BRL"),
    (r"US\$", "USD"),
    (r"CA\$", "CAD"),
    (r"AU?\$", "AUD"),   # A$ and AU$
    (r"MX\$", "MXN"),
)

#: Active ISO 4217 codes that are also ordinary English words. ``800 PEN
#: issues`` is prose, not Peruvian soles — and an uppercase-only match does not
#: help, because these ARE the uppercase spellings. They are refused as an
#: ambiguous currency rather than guessed (mode 4's posture, applied to the
#: word-shaped codes).
_WORD_CODES = frozenset({
    "ALL", "AMD", "BOB", "COP", "CUP", "GEL", "MAD", "MOP", "PEN", "PHP",
    "RON", "SOS", "TOP", "TRY",
})

#: Symbols that never become a currency on their own (mode 4 / design §6/T4).
_BARE_SYMBOLS = ("$", "¥", "€", "£")

#: An ISO 4217 alpha-3 code, uppercase ONLY. No case folding: ``all``, ``top``,
#: ``pen``, ``try``, ``bob``, ``cup``, ``mad``, ``gel`` and ``sos`` are ordinary
#: English words that are ALSO active ISO 4217 codes, so a lowercase or
#: case-insensitive match would mint a currency out of prose (tested).
_CODE = r"(?<![A-Z])[A-Z]{3}(?![A-Z])"

_SYM_BEFORE = "|".join(p for p, _ in _SYMBOL_ANCHORS)
_ANCHOR = rf"(?P<a>{_SYM_BEFORE}|{_CODE})"

#: A numeric token: digits, then only characters that can belong to one
#: (separators, and a short trailing word so ``8M``/``12x``/``800ms`` are
#: *seen* rather than silently split).
_NUM = (r"[\u2212+\-]?\d(?:[\d.,'\u00a0\u202f]{0,34}\d)?"
        r"[A-Za-z%]{0,8}")

_GAP = r"[ \t\u00a0\u202f]*"      # never a newline: see _GAP's note below
_ANCHOR_THEN_NUM = re.compile(rf"{_ANCHOR}{_GAP}(?P<n>{_NUM})")
_NUM_THEN_ANCHOR = re.compile(rf"(?P<n>{_NUM}){_GAP}{_ANCHOR}")

_MAGNITUDE_SUFFIX = re.compile(r"(?i)(?:k|m|mm|bn|tn|b|trn)$")
_MAGNITUDE_WORD = re.compile(
    r"(?i)^(?:thousand|million|billion|trillion|mil|milhao|milhão|milhoes|"
    r"milhões|bilhao|bilhão|bilhoes|bilhões|bn|mm|tn|trn|[kmb])$")

_RANGE = re.compile(
    r"\d[\d.,'\u202f]*\s*(?:-|–|—|\bto\b|\band\b|\bor\b|/)\s*\d")
_DATE_SPAN = re.compile(r"\d{4}-\d{2}-\d{2}")
#: Anything that LOOKS like a date is masked before the range check: a
#: malformed date ("2026-6-30") must not make its sentence's amount look like
#: a range, and a date range is not an amount range.
_DATE_LIKE = re.compile(r"\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}")

_QUALIFIER_BEFORE = re.compile(
    r"(?i)(?:\b(?:under|over|about|around|approximately|approx|roug\s*"
    r"hly|roughly|nearly|almost|estimated|est\.?|circa|between|up\s+to|"
    r"at\s+least|at\s+most|more\s+than|less\s+than|no\s+more\s+than|"
    r"no\s+less\s+than|give\s+or\s+take|min|max|exactly)\b|[~≈≤≥<>+±])"
    r"\s*$")

_QUALIFIER_AFTER = re.compile(
    r"(?i)^[\s,;:(\[\u2014\u2013-]*(?:post-money|pre-money|postmoney|"
    r"premoney|or\s+less|or\s+more|or\s+so|approx|approximately|roughly|"
    r"about|ish|max|min|maximum|minimum|at\s+most|at\s+least|and\s+up|"
    r"and\s+above|plus|almost|estimated|est\.?|circa|give\s+or\s+take|"
    r"±|-?ish|\+)")

_YEAR_SHAPED = re.compile(r"(?:19|20|21)\d{2}")

_AS_OF = re.compile(
    r"(?i)\b(?:as\s+of|as\s+at|as-of|effective|valid\s+as\s+of)\s+"
    r"(?P<d>\d{4}-\d{2}-\d{2})\b")

_TRAILING_PUNCT = ".,;:!?)]}\"'"

#: A minus/plus/bracket immediately before a LEADING number, optionally spaced.
_SIGN_BEFORE = re.compile(r"[-\u2212+(]\s*$")

#: A number whose digit groups are split by whitespace ("1 234", "1\u00a0234")
#: is a separator form, not ``1`` — dropping it is the same decision as
#: ``1.234`` (never guess which grouping convention is meant).
_CONTINUES_AS_GROUP = re.compile(
    r"(?:[\s\u00a0\u202f]+|[.,'\u2018\u2019_]\s*)\d")


def _drop(reason: str, detail: str = "") -> Normalization:
    assert reason in DROP_REASONS, f"unknown drop reason {reason!r}"
    return Normalization(None, reason, detail)


@dataclass(frozen=True)
class _Candidate:
    anchor: str
    token: str
    start: int          # first character of the NUMBER (the verbatim's start)
    end: int            # one past the number
    span_start: int     # first character of the whole anchored form
    span_end: int       # one past the whole anchored form
    anchor_first: bool  # the currency precedes the number ("EUR 800")


def _mask_dates(text: str) -> str:
    """Blank every date-like span, preserving length and positions.

    A date component is not an amount: without this, ``as of 2026-03-31 EUR
    800`` yields the candidate ``31 EUR`` beside ``EUR 800`` (two "amounts",
    so abstention on a perfectly clear sentence), and ``EUR 2026-03-31`` types
    ``2026`` as euros.
    """
    return _DATE_LIKE.sub(lambda m: " " * len(m.group(0)), text)


def _candidates(text: str) -> list[_Candidate]:
    """Every anchored money FORM in ``text``, deduped by its numeric span."""
    found: dict[tuple[int, int, int], _Candidate] = {}
    for rx in (_ANCHOR_THEN_NUM, _NUM_THEN_ANCHOR):
        for m in rx.finditer(text):
            anchor = m.group("a")
            tok = m.group("n")
            start = m.start("n")
            end = start + len(tok)
            found[(start, end, m.start("a"))] = _Candidate(
                anchor=anchor, token=tok, start=start, end=end,
                span_start=m.start(), span_end=m.end(),
                anchor_first=m.start("a") < start)
    return sorted(found.values(), key=lambda c: (c.start, c.end, c.anchor))


def _currency_for(anchor: str) -> str | None:
    """The ISO code an anchor names, or ``None`` when it names none."""
    for pattern, code in _SYMBOL_ANCHORS:
        if re.fullmatch(pattern, anchor):
            return code
    return anchor if anchor in ISO_4217_MINOR_UNITS else None


def _has_magnitude_word_after(text: str, end: int) -> bool:
    nxt = text[end:end + 24].split()
    return bool(nxt) and bool(_MAGNITUDE_WORD.match(nxt[0].strip(".,;:")))


#: How far from the amount a range marker still belongs to it: ``EUR 5-10`` /
#: ``EUR 5 to 10`` are ~4 chars, while "EUR 800 covers 3-4 hires" is 15+.
_RANGE_WINDOW_BEFORE = 6
_RANGE_WINDOW_AFTER = 6


def _has_range(text: str, candidates: list[_Candidate]) -> bool:
    """Whether an amount RANGE touches one of the anchored forms.

    Scoped to the neighbourhood on purpose: an amount is not disqualified by an
    unrelated hyphenated pair elsewhere in the sentence ("the budget of EUR 800
    covers 3-4 hires"), which a sentence-wide check would wrongly drop.
    """
    masked = _mask_dates(text)
    for cand in candidates:
        window = masked[max(0, cand.start - _RANGE_WINDOW_BEFORE):
                        cand.end + _RANGE_WINDOW_AFTER]
        if _RANGE.search(window):
            return True
    return False


def _classify_token(token: str, *, text: str, end: int,
                    detail: str) -> Normalization | None:
    """Classify one numeric token; ``None`` means "a plain integer".

    ``detail`` is the whole anchored form, so a warning quotes what the reader
    actually saw ("R$ 1.234,56"), not a fragment of it.
    """
    t = token.strip().rstrip(_TRAILING_PUNCT)
    if not t:
        return _drop("unsupported_token", detail)
    if t[0] in "-\u2212+":
        return _drop("signed_amount", detail)
    if _MAGNITUDE_SUFFIX.search(t) or _MAGNITUDE_WORD.match(t) \
            or _has_magnitude_word_after(text, end):
        # No magnitude table (design §3: bare M is "dropped, not open").
        return _drop("magnitude_word", detail)
    if "%" in t:
        return _drop("not_money_percent", detail)   # §4: no valueKinds
    if re.search(r"(?i)x$", t):
        return _drop("not_money_multiplier", detail)
    if re.search(r"[.,'\u00a0\u202f]", t):
        # ⛔ THE decision, in one line: any separator at all is ambiguous
        # without the locale rule the design excludes. 1.234 / 1,234 /
        # 1.234,56 / 1,234.56 / 800.00 / 1 234 all land here — never a guess.
        return _drop("ambiguous_separator", detail)
    if not t.isdigit() or len(t) > 18:
        # 19+ digits is not a plausible amount, and Python 3.12 refuses
        # int("9"*4300) outright — abstain instead of raising.
        return _drop("unsupported_token", detail)
    if _CONTINUES_AS_GROUP.match(text[end:]):
        # "BRL 1 234" — the number does not end at the token (mode 1).
        return _drop("ambiguous_separator", text[end - 8:end + 12].strip())
    return None


def _as_of(text: str) -> str | None:
    """The ASSERTED date, or ``None`` — never an occurrence time (OD3/mode 8).

    Only an explicit assertion marker counts, so ``on 2026-03-31 we committed
    EUR 800`` yields no ``asOf``: that date is the occurrence time
    (``Point.when``), and reusing it as assertion validity is the mode-8
    error. ``money_props`` then validates the ISO shape at Layer 1.
    """
    dates = {m.group("d") for m in _AS_OF.finditer(text)}
    if len(dates) > 1:
        return None          # several asserted dates — never pick one
    return next(iter(dates)) if dates else None


def normalize_money(text: str) -> Normalization:
    """Derive the canonical money value from one raw sentence.

    Returns the money props (:func:`tortoise.money.money_props`, so the value
    is already Layer-1 valid) or a :class:`Normalization` carrying a reason
    from :data:`DROP_REASONS`. Never guesses, never mutates the text, and
    never emits a float: ``amountMinor`` is an int at the currency's pinned
    exponent (``JPY``=0, ``BRL``/``USD``/``EUR``=2, ``KWD``=3).
    """
    if not isinstance(text, str) or not text.strip():
        return _drop("no_candidate")

    masked = _mask_dates(text)
    candidates = _candidates(masked)
    if not candidates:
        if not any(ch.isdigit() for ch in text):
            return _drop("no_candidate", text[:60])
        if "%" in text:
            return _drop("not_money_percent", text[:60])
        if re.search(r"(?i)\d[a-z]*x\b", text):
            return _drop("not_money_multiplier", text[:60])
        if any(_MAGNITUDE_WORD.match(w.strip(".,;:"))
               for w in text.split()):
            return _drop("magnitude_word", text[:60])
        if any(sym in text for sym in _BARE_SYMBOLS):
            return _drop("ambiguous_currency_symbol", text[:60])
        return _drop("no_currency_anchor", text[:60])

    if _has_range(text, candidates):
        return _drop("range_or_qualifier", text[:60])

    props: list[dict[str, Any]] = []
    as_of = _as_of(text)          # once, not per candidate
    unresolved_anchor: str | None = None
    for cand in candidates:
        before = text[max(0, cand.span_start - 24):cand.span_start]
        after = text[cand.span_end:cand.span_end + 24]
        if _QUALIFIER_BEFORE.search(before) or _QUALIFIER_AFTER.search(after):
            return _drop("range_or_qualifier", text[:60])
        # A sign the design defines no convention for (mode 5/§3): "-EUR 800"
        # must not be typed as +800.00 — a negative typed as a positive is the
        # worst false type available, so an adjacent sign abstains.
        if cand.anchor_first:
            # "EUR -800" is caught by the token's own leading sign below; here
            # only a sign attached to the CURRENCY counts ("-EUR 800"), so a
            # prose dash ("pilot - EUR 800") is not read as a sign.
            signed = (cand.span_start
                      and text[cand.span_start - 1] in "-\u2212+(")
        else:
            # the number leads: a minus before it IS a sign even when spaced
            # ("- 800 EUR"), and reading it as +800.00 is the worst false type.
            signed = bool(_SIGN_BEFORE.search(
                text[max(0, cand.start - 4):cand.start]))
        if signed:
            return _drop("signed_amount",
                         text[max(0, cand.span_start - 2):cand.span_end])
        if cand.anchor in _WORD_CODES:
            return _drop("ambiguous_currency_symbol", cand.anchor)
        currency = _currency_for(cand.anchor)
        if currency is None:
            # "EUR 800 VAT inclusive": an uppercase neighbour that is not a
            # currency must not discard an amount another candidate resolved.
            unresolved_anchor = unresolved_anchor or cand.anchor
            continue
        exponent = ISO_4217_MINOR_UNITS.get(currency)
        if exponent is None:
            return _drop("no_minor_unit", currency)
        classified = _classify_token(
            cand.token, text=text, end=cand.end,
            detail=text[cand.span_start:cand.span_end].strip())
        if classified is not None:
            return classified
        if _YEAR_SHAPED.match(cand.token):
            return _drop("year_shaped_amount", cand.token)
        token = cand.token.strip().rstrip(_TRAILING_PUNCT)
        verbatim = text[cand.start:cand.start + len(token)]
        try:
            amount_minor = int(token) * (10 ** exponent)
        except (ValueError, OverflowError):  # unreachable guard, kept explicit
            return _drop("unsupported_token", verbatim)
        try:
            props.append(money_props(
                amount_minor, currency, minor_unit_exponent=exponent,
                verbatim=verbatim, as_of=as_of))
        except MoneyValueError as exc:      # Layer 1 refuses → we abstain
            return _drop("refused_by_layer1", str(exc))

    if not props:
        return _drop("not_an_iso4217_code", unresolved_anchor or text[:60])
    first = props[0]
    if any(p != first for p in props[1:]):
        # Two different amounts in one sentence: picking one is a guess
        # (design §4: multi-value points are out of scope).
        return _drop("multiple_candidates", text[:60])
    return Normalization(props=first)


def normalize_money_props(text: str, *,
                          warnings: list[str] | None = None) -> dict | None:
    """The S5 call pattern: props, or ``None`` + a warning (never a guess).

    Mirrors ``_valid_iso_date``'s posture in ``extractor_v2.py``: a caller
    that cannot type the value drops it and says so, so an abstention is
    visible in the payload instead of being silent.
    """
    result = normalize_money(text)
    if result.typed:
        return result.props
    if warnings is not None:
        warnings.append(
            f"value-normalizer: no typed money value ({result.drop_reason}) "
            f"for {result.detail!r} — prose only")
    return None


#: Fields a caller may rely on this module never writing: the derivation is
#: additive, and prose is never moved out of ``content``/``quote`` (design §4
#: "Prose is load-bearing"; the retention graders read prose only).
PROSE_FIELDS: frozenset[str] = frozenset({"content", "quote", "when"})

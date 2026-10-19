#!/usr/bin/env python3
"""#1987 Task 12 (b)/(d): eval-only ask-lane known-answer smoke + QA spot-check.

Runs the REAL eval-only lane (``ask_lane.run_ask_lane`` → ``build_reader_model``
— the RoutingModel transport delta vs the eval's OpenAICompatModel) over:
  * (b) the gold-verbatim known-answer fixture (MUST commit),
  * (d) a bounded QA spot-check over real LongMemEval dataset questions
    (temporal / preference / KU / MSR / abstention (_abs) /
    single-session-assistant samples — the plan's composition).

Usage notes (#2280):
  * Run with a STRONG, non-collapsing reader — the ask lane escalates its
    output budget once when a reasoning model collapses to empty output
    (``TORTOISE_ASK_ESCALATION_TOKENS``, default 2000), but a capable
    model is the point of the measurement: ``TORTOISE_ASK_MODEL=qwen/
    qwen3.8-max`` (family-prefixed, #2069).
  * The run also prints a ctx gold-session recall readout (retrieval
    measured SEPARATELY from reading — every gold session present in the
    evidence the reader saw), so a retrieval regression is never masked
    as a reading miss.

Grading (issue #2071 owner decision 2026-08-31 — full semantic): EVERY
question is graded by the benchmark-standard semantic judge
(``build_judge()`` → the official gpt-4o anscheck judge — the same judge
the graded eval lane uses for every question). The old word-overlap bar
(``max(2, len(gold_words)//2)`` on UNIQUE words) is REMOVED from this
path — it was structurally unreachable for the 3 SSP long-gold questions
(d6233ab6 79w / 1d4e3b97 68w / b0479f84 63w; a correct paraphrase never
clears a ≥½-unique-word overlap bar) and is demoted to the key-free CI
(MockJudge) substitute only. The ``_abs`` marker path is unchanged and
precedes the judge call (a deterministic short-circuit for
abstained-with-marker answers; any other ``_abs`` answer falls through to
the judge's abstention template). There is NO silent fallback to the
removed bar: the tool exits fail-fast (before any grading) when the judge
provider key is absent (see ``_require_judge_key``).

Requires live provider keys: DEEPSEEK_API_KEY / OPENROUTER_API_KEY /
VENICE_API_KEY for the reader AND the judge provider key
(``TORTOISE_LME_JUDGE_MODEL`` — default ``openai:gpt-4o-2024-08-06`` →
``OPENAI_API_KEY``) for grading. Seeding (``_seed_memory``, #3910)
reproduces the memory the question was asked about in the CAPTURE shape a
captured session's turn store actually has: one episodic turn Point per
windowed turn of every session that passes the shared blank gate (a session
with no extractable line writes nothing — capture's pre-mutation gate)
(deterministic ``f"{sid}_t{i}"`` id, ``pointKind='event'``,
``is_episodic=true``, ``speaker``, no ``sessionId``/``eventId`` prop, and the
product's own turn EMBEDDING via the STORE seam
(``encode_batch_for_store`` + ``proj.required_embedding_dim``, #4304) — #4194;
dense ON by default, ``embed=False`` for the #4197 backlog state) wired
to its ``:Session`` (id = the fixture's own ``haystack_session_ids[i]``, or
the synthetic ``sess-{i}`` placeholder when the fixture carries none) by
the ``(:Session)-[:CONTAINS]->(:Point)`` edge — the provenance mechanism the
shipping read resolves identity from — plus the per-session ``:Event``
(startedAt from haystack_dates), retained but not joined to the turns.

Composition fixture: the committed ``tests/fixtures/ask_spotcheck_composition.json``
(21 questions — the reproducibility gap closed by issue #2071 step 1).
Path resolution order: ``--fixture`` CLI arg → ``TORTOISE_SPOTCHECK_FIXTURE``
env → committed fixture → ``/tmp/ask_spotcheck.json`` (legacy compat).

DISTILLED + PLANTED-SUPERSESSION leg (``--distilled``, #9095): the epic
#2281 harness could only seed RAW turns, so its D7 measurement ("does the
reader commit the current state?") and the D8 product gate ("0% of
superseded points appear as live answers" — ``docs/product-success-eval.md``)
had no instrument at all. This leg seeds the authored, hermetic corpus at
``tests/fixtures/ask_spotcheck_distilled.json`` — three planted
knowledge-update pairs (residence / diet / employer) — through
``seed_distilled_corpus`` (the capture EXTRACTION write shape + the
product's own ``supersede_point``), and reports, from ONE run per case,
both arms of the paired read (D4: no parallel benchmark): ``raw``
(``_seed_memory`` only) and ``distilled`` (+ distilled claims +
supersession). Numbers: ``current_state`` (the knowledge-update class — the
reader committed the CURRENT value) and ``superseded_live`` (the D8 gate —
how many superseded claims rendered as their own block body with no
``[SUPERSEDED BY:`` marker). One machine-readable ``DISTILLED_JSON`` line
carries both, per arm. ``current_state`` has NO pass threshold (D8: the first
run sets it); ``superseded_live.rendered_live > 0`` is a hard failure of the
run, and so is a case the instrument never observed — an unmeasured gate is
never a pass. ``TORTOISE_SPOTCHECK_JUDGE=mock`` grades the leg key-free
(CI); without it the composition path's fail-fast judge-key rule applies.
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/ask_spotcheck.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/ask_spotcheck.py`"
    )

import argparse
import json
import os
import re
import tempfile
from datetime import datetime, timezone

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tools.longmem_eval.judge import (  # noqa: E402
    DEFAULT_JUDGE_MODEL,
    MockJudge,
    build_judge,
)
from tools.longmem_eval.reader import _parse_model_spec  # noqa: E402
from tortoise.ask_lane import run_ask_lane  # noqa: E402
from tortoise.ingest import _PROVIDERS  # noqa: E402
from tortoise.sdk import (  # noqa: E402
    _SESSION_LLM_PROVIDER_PRIORITY,
    TortoiseSDK,
    _capture_gate_window,
    _capture_turn_embeddings,
    _capture_turn_texts_with_redactions,
    _capture_turn_window,
    _content_hash,
    _normalize_turn_role,
    _session_llm_transcript,
)

_COMMITTED_FIXTURE = os.path.join(
    _REPO_ROOT, "tests", "fixtures", "ask_spotcheck_composition.json")
_LEGACY_FIXTURE = "/tmp/ask_spotcheck.json"
_FIXTURE_ENV = "TORTOISE_SPOTCHECK_FIXTURE"

#: #9095: the authored, hermetic DISTILLED corpus (three planted
#: knowledge-update pairs). Resolution order: ``--distilled-corpus`` CLI arg →
#: ``TORTOISE_SPOTCHECK_DISTILLED_CORPUS`` env → this committed file.
DISTILLED_FIXTURE = os.path.join(
    _REPO_ROOT, "tests", "fixtures", "ask_spotcheck_distilled.json")
_DISTILLED_FIXTURE_ENV = "TORTOISE_SPOTCHECK_DISTILLED_CORPUS"

#: #9095: key-free grading for the distilled leg only
#: (``TORTOISE_SPOTCHECK_JUDGE=mock``). It grades through the benchmark's own
#: ``MockJudge`` so the planted-supersession gate can run in CI without a
#: provider key; the composition path is deliberately NOT widened by it —
#: #2071's fail-fast (no key → exit before grading) stays exactly as it was.
DISTILLED_JUDGE_ENV = "TORTOISE_SPOTCHECK_JUDGE"

#: #9095: the product's OWN flag vocabulary, mirrored — NOT invented. A
#: superseded hit is annotated ``[SUPERSEDED BY: <newest superseding
#: claim>]`` by ``tortoise/retrieval._validity_marker`` (#1353/#1367), and
#: that marker IS the D8 product gate ("must be flagged or excluded"): the
#: superseded claim may still reach the reader, but never unflagged. A single
#: literal so the gate and its report can never disagree about what "flagged"
#: means; ``tests/test_ask_spotcheck_distilled.py`` pins it against REAL
#: rendered evidence (not this constant) so a renderer drift fails the suite
#: instead of silently turning the gate into a guaranteed green.
SUPERSEDED_MARKER = "[SUPERSEDED BY:"


def _to_iso_date(raw: str) -> str:
    """'2023/02/01 (Wed) 10:20' → '2023-02-01' (YYYY-MM-DD)."""
    m = re.match(r"(\d{4})/(\d{2})/(\d{2})", raw or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else "2020-01-01"


def _fixture_session_date(raw: str) -> str:
    """The fixture's own session date as ``YYYY-MM-DD``, or ``""`` when it is
    absent or unparseable — NEVER a placeholder (#4106).

    This value is written as the session's RECORDED time, which the ask-path
    date annotation renders to the reader, so a placeholder here would put a
    FABRICATED date in front of a temporal question. Accepts the dataset's
    ``YYYY/MM/DD`` form and an ``YYYY-MM-DD`` one; anything else is unknown.
    """
    m = re.match(r"(\d{4})[/-](\d{2})[/-](\d{2})", raw or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


class _CaptureClock:
    """Sentinel: *"this caller models a CAPTURE, which always HAS a capture
    time"* (#4156).

    ``now`` used to default to a plain ``None`` that was then substituted with
    the run clock, so ``None`` silently MEANT "the run clock" and a caller that
    wanted to record **no** time could not say so — it had to erase the
    fabricated value afterwards (the ``_clear_recorded_time`` convention both
    seeders carried, and the trap the next seeder would re-discover). The
    default is now this sentinel: passing nothing still models a capture's own
    clock, and ``now=None`` is an explicit *"no recorded time"* — which the
    writers ENFORCE ON THE NODE (any recorded time already present is removed,
    not merely left unwritten).
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return "CAPTURE_CLOCK"


#: The capture-shaped seeders' default ``now``: the run clock, NAMED so that
#: "no recorded time" (an explicit ``None``) and "the capture simulation's
#: recorded time" are two distinguishable values rather than one (#4156).
CAPTURE_CLOCK = _CaptureClock()


def merge_capture_session(sdk: TortoiseSDK, session_id: str, turn_count: int,
                          now: str | _CaptureClock | None = CAPTURE_CLOCK,
                          capture_redactions: int | None = None,
                          ) -> str | None:
    """MERGE a ``(:Session)`` node in the shape BOTH capture writers write.

    The Session is never a bare ``{id}`` in production: both capture surfaces
    SET ``created_at`` (coalesced, so an idempotent re-capture preserves the
    ORIGINAL capture time), ``turn_count`` and ``is_episodic=true``
    (``TortoiseSDK.capture_session`` / ``hosted_api._capture_session_impl``).
    A bare-id Session is a node shape capture never produces, and a consumer
    that reads those props (the hosted session listing reads ``s.turn_count``;
    commit reads ``s.is_episodic``) sees ``None`` on any graph seeded without
    them — so no fixture seeded that way can guard those surfaces.

    ``now`` is the session's RECORDED time. The default (``CAPTURE_CLOCK``)
    models a capture, which always has one, and resolves to the run clock.
    An explicit ``now=None`` means the session records **NO** time, and that is
    enforced against the node, not merely against this write: any
    ``created_at`` already on it is REMOVED. That is the SESSION half of the
    job the deleted ``_clear_recorded_time`` helper did (#4154 → #4156); its
    turn half lives in :func:`seed_capture_turn_store`, which sweeps every
    stored ``CONTAINS`` Point. Calling this function ALONE with ``now=None``
    therefore undates the session node only — it is not a drop-in replacement
    for the helper. "Skip the write" alone would have left a stale, possibly
    fabricated, date on a re-seeded node, i.e. exactly the trap #4156 exists
    to remove. Any other ``str`` is recorded verbatim (including ``""`` —
    silently coercing a falsy string to the run clock is the conflation #4156
    removes; the read path renders a non-date as UNKNOWN).

    Shared by every ask-lane seeder so the Session side cannot drift either.
    Returns the ``now`` used (``None`` when no time was recorded), so a caller
    writing several sessions or turns can hold ONE timestamp across them.
    """
    if isinstance(now, _CaptureClock):
        # The sentinel is resolved HERE, so a caller passing no ``now`` (or any
        # ``_CaptureClock`` instance the annotation admits) gets the capture
        # simulation's clock and nothing downstream sees the sentinel.
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017
    sets = "s.turn_count=$tc, s.is_episodic=true"
    params: dict[str, object] = {"sid": session_id, "tc": turn_count}
    # #4911: the live capture writer also SETs this, inside its batched turn
    # statement. Written only when the caller knows the count (the seeder does
    # — it composes the text) so a bare Session-half call is not forced to
    # invent one.
    if capture_redactions is not None:
        sets += ", s.capture_redactions=$redact"
        params["redact"] = capture_redactions
    if now is None:
        sets = "s.created_at=null, " + sets
    else:
        sets = "s.created_at=coalesce(s.created_at, $now), " + sets
        params["now"] = now
    sdk._get_proj().g.query(
        f"MERGE (s:Session {{id:$sid}}) SET {sets}",
        params=params,
    )
    return now


#: W7A: the ask lane's DEFAULT seeding mode. ``True`` = the seeder stores the
#: product's own turn vector (the post-#4194 shape the instrument measures);
#: ``False`` = the pre-#4194 / no-embedder store (#4197's backlog). Single
#: source so a receipt can NAME the mode it was produced in without restating
#: it by hand (a receipt that does not name its seeding mode is not evidence).
SEED_TURNS_EMBEDDED_BY_DEFAULT = True

#: #5534: whether the ask FIXTURE seeder (``_seed_memory``) stores the E3
#: ``search_keys`` substrate the A4 PRF gate harvests. Single source so a
#: receipt can NAME the mode it was produced in (a receipt that does not name
#: its seeding mode is not evidence).
#:
#: **``False`` is the faithful CAPTURE shape** (the default): ``search_keys``
#: is an indexed Point FTS field (``tortoise/projection/__init__.py`` —
#: ``("Point", ["content", "search_keys"])``) and capture's TURN write stores
#: none (E3 writes it on extracted claim Points), so a capture-exact turn
#: store has none — exactly why the SHARED primitive
#: ``seed_capture_turn_store`` defaults OFF. Flipping this default ON would
#: silently change the store of EVERY non-A4 caller that passes nothing
#: (``tools/profile_read_path.py``, ``tools/ask_pool_admission_probe.py``,
#: the ``w6c_*`` / ``w7a_*`` / ``4107_*`` runbook diagnostics), so their
#: previously recorded numbers would stop reproducing.
#:
#: A caller that NEEDS A4's input opts in EXPLICITLY with ``search_keys=True``
#: (the diagnostic and the ask-shape ruler do; both default their own CLI to
#: the A4 behaviour). ``True`` = the store carries the aliases A4 needs and
#: the A/B can be non-zero; ``False`` = #5534's defect shape (A4 structurally
#: inert, the A/B a guaranteed zero).
SEED_SEARCH_KEYS_BY_DEFAULT = False

#: commit_schema.Point.search_keys entry bound (1-60 chars, at most 4
#: entries).
_MAX_SEARCH_KEY_LEN = 60
_MAX_SEARCH_KEYS = 4


def _derive_search_keys(text: str, *,
                        max_entries: int = _MAX_SEARCH_KEYS) -> str:
    """#5534: deterministic E3-shaped ``search_keys`` for ONE seeded turn.

    A4 (``TORTOISE_ASK_SEARCH_KEYS_PRF``) is implemented entirely as
    ``sdk._search_keys_prf_expansion`` → ``tortoise.sparse.expansion_tokens``,
    whose output depends ONLY on the ``search_keys`` harvested from the
    first-pass top-5 hits (``expansion_tokens`` opens with ``if not aliases:
    return []``). A store with no ``search_keys`` therefore makes the FTS leg
    byte-identical on/off — every A4 A/B returns a guaranteed zero, which is
    #5534's defect: not "A4 has no effect", but "the instrument has no A4
    input".

    This supplies that input from the turn's OWN text — the same substrate
    E3's extractor writes for a claim: up to 4 distinct tokens, longest
    first (the specificity rule ``expansion_tokens`` itself applies), each
    within the schema's 60-char entry bound, non-stopword and >= 2 chars by
    construction (``tokenize_sparse_query``). Returns the FLAT space-joined
    STRING the product stores after ``sdk._flatten_search_keys_prop``
    (#1541 D3: FalkorDB's fulltext index does not index array-valued
    properties). Empty when the turn yields no storable token.
    """
    if not text:
        return ""
    from tortoise.sparse import tokenize_sparse_query

    seen: set[str] = set()
    uniq: list[str] = []
    for tok in tokenize_sparse_query(str(text), keep_numeric=True):
        if tok in seen or len(tok) > _MAX_SEARCH_KEY_LEN:
            continue
        seen.add(tok)
        uniq.append(tok)
    uniq.sort(key=len, reverse=True)
    return " ".join(uniq[:max_entries])


def seed_capture_turn_store(sdk: TortoiseSDK, session_id: str,
                            conversation: list[dict], *,
                            now: str | _CaptureClock | None = CAPTURE_CLOCK,
                            embed: bool = SEED_TURNS_EMBEDDED_BY_DEFAULT,
                            search_keys: bool = False,
                            ) -> list[str]:
    """Write ONE session's turns in the CAPTURE shape (#3914, #3910).

    The single seeder every ask-lane fixture writes through, so no fixture
    can teach a graph shape the real capture path cannot produce. It
    reproduces the turn-store sub-step of ``TortoiseSDK.capture_session`` /
    ``hosted_api._capture_session_impl`` and nothing else:

      * ``MERGE (s:Session {id:$session_id})`` with capture's own
        ``created_at`` (coalesce — an idempotent re-capture preserves the
        original time), ``turn_count`` and ``is_episodic=true``. A bare-id
        Session is a node shape capture never writes, so leaving those off
        would make every fixture seeded through this helper unusable for the
        consumers that read them (the hosted session listing reads
        ``s.turn_count``; commit reads ``s.is_episodic``);
      * per windowed turn, a ``:Point`` with the deterministic
        ``f"{session_id}_t{i}"`` id, ``pointKind='event'``,
        ``is_episodic=true``, ``is_operator=false``, a role-normalized
        ``speaker``, the ``[role] <content>`` text and a ``content_hash`` —
        and NO ``sessionId`` / ``eventId`` prop (capture writes neither, so
        no prop can satisfy an identity read that the ``CONTAINS`` edge
        alone carries);
      * the product's OWN turn vector (``embed=True``, the DEFAULT): the
        stored ``[role] <content>`` text routed through the product's
        STORE-scoped encoder (``embeddings.encode_batch_for_store`` with
        ``proj.required_embedding_dim`` — #4304's seam, never the raw
        encoder) and stored as ``vecf32``. The product's turn write now embeds
        every turn (#4194), so a fixture seeded without a vector models a
        store the product no longer writes and BLINDS every retrieval
        measurement to the dense leg — the frozen instrument reported
        ``retrieval_degraded`` 21/21 for that reason (W7A). ``embed=False``
        seeds a PRE-#4194 / no-embedder store instead: a FRESH turn gets
        ``NULL``, and — mirroring the product's own three-way guard — an
        unchanged re-seed PRESERVES its vector while a changed one CLEARS it.
        That is the shape a capture made before #4194 (or with no embedder)
        actually has; re-embedding such a backlog is part of the saved-backlog
        decision, #4197;
      * the ``(:Session)-[:CONTAINS]->(:Point)`` edge — the provenance
        mechanism the shipping read resolves identity from
        (``OPTIONAL MATCH (sess:Session)-[:CONTAINS]->(n)``).

    The window/coercion and the whole-session BLANK GATE are capture's own
    shared primitives (``_capture_turn_window`` / ``_session_llm_transcript``
    — capture's gate is PRE-MUTATION), so a degenerate session contributes no
    Session stub, no turn and no edge, exactly as in capture.

    ``search_keys`` (#5534) is OFF by default and stays a fixture-seeder
    concern: capture's turn write stores no ``search_keys`` (E3 writes it on
    extracted claim Points), so a capture-exact turn store has none — and
    the committed transcript goldens seed through this function. When ON,
    each turn's aliases are DERIVED from its own stored text via
    :func:`_derive_search_keys` and written as one flat space-joined string
    (the product's post-``_flatten_search_keys_prop`` shape); the write is
    deterministic, and therefore a re-seed of the same turn converges.

    ``now`` follows :func:`merge_capture_session` exactly: the default
    (``CAPTURE_CLOCK``) models a capture's own clock and is resolved ONCE so
    the session and every turn share it. An explicit ``now=None`` means the
    session records NO time, and that is enforced against the node — the
    session's ``created_at`` and the ``createdAt``/``updatedAt`` properties of
    every Point it ``CONTAINS`` are REMOVED, not merely left unwritten
    (#4156).

    ⚠️ The whole contract is contingent on the blank gate below ADMITTING the
    session: a degenerate conversation returns before any write, so a
    re-seed of a previously-timed session with a blank conversation is left
    completely untouched (its recorded time included). That is deliberate —
    capture's gate is pre-mutation and a real capture of a blank session
    writes nothing either; do not "fix" it by moving the gate.

    Returns the turn ids WRITTEN, in window order. An EMPTY list means the
    blank gate skipped the session — the caller must not assume a Point
    exists. Callers own any non-capture furniture (the ask fixtures' date-only
    ``:Event`` marker): turn Points carry no ``eventId``, so nothing joins a
    turn to an ``:Event`` — in capture either.
    """
    windowed = _capture_turn_window(conversation or [])
    # Same marker-stripped view as capture's own gates (#4897 review round 12): this mirror
    # previously admitted an over-cap blank turn and seeded a marker-only session, while capture
    # refused it — contradicting this function's own docstring ("exactly as in capture").
    #
    # ⛔ ``_capture_gate_window`` is a MODULE-LEVEL function in ``tortoise.sdk``, not a
    # ``TortoiseSDK`` method, and ``sdk`` here is an SDK INSTANCE. Round 12 wrote
    # ``sdk._capture_gate_window(windowed)``, which raised ``AttributeError`` for EVERY caller —
    # caught by ``tests/test_ask_seed_shape.py`` in review round 14. Import it and call it
    # directly; the SDK class has no ``__getattr__``.
    transcript, _est = _session_llm_transcript(_capture_gate_window(windowed))
    if not transcript.strip():
        return []
    proj = sdk._get_proj()
    # #4911: the stored text comes from the PRODUCT's own definition —
    # ``_capture_turn_texts`` is where the capture path scrubs credentials — so
    # a seeded fixture cannot teach a shape (or a credential policy) the real
    # write path no longer produces. The count rides the Session MERGE below,
    # as it does in the live writer.
    turn_texts, redaction_counts = _capture_turn_texts_with_redactions(windowed)
    now = merge_capture_session(sdk, session_id, len(windowed), now=now,
                                capture_redactions=sum(redaction_counts.values()))
    # #4156: the SAME contract as the Session side — the default models a
    # capture's clock (already resolved above into one ``now`` shared by the
    # session and every turn), while an explicit ``now=None`` means the
    # session records NO time: the two time properties are not written here,
    # and after the loop the WHOLE stored ``CONTAINS`` set is swept — the
    # deleted ``_clear_recorded_time`` cleared every stored turn, and a
    # shorter re-seed must not leave an earlier call's timestamps behind.
    sk_sets = ""
    if search_keys:
        # #5534: the E3 alias substrate the A4 PRF gate harvests. Written in
        # the same SET as the turn so a search_keys write can never be a
        # separate, skippable step.
        sk_sets = "t.search_keys=$sk, "
    time_sets = ""
    time_params: dict[str, object] = {}
    if now is not None:
        time_sets = ("t.createdAt=coalesce(t.createdAt, $now), "
                     "t.updatedAt=$now, ")
        time_params = {"now": now}
    # The stored text is composed ONCE — the same string the node stores and
    # the string that is encoded, so a dense hit always resolves to the turn
    # whose text was embedded (#4194's own rule). ``turn_texts`` was composed
    # above from the product's shared definition (#4911).
    # SWITCHABLE SEEDING (W7A): embedded by DEFAULT, because the product's
    # write path now embeds every episodic turn (#4194) and a fixture without
    # a vector BLINDS every retrieval measurement to the dense leg — the
    # frozen instrument reported ``retrieval_degraded`` 21/21 for exactly that
    # reason. ``embed=False`` RETAINS the pre-#4194 / no-embedder store so the
    # un-backfilled backlog stays measurable; re-embedding that saved backlog
    # is the user-facing choice owned by #4197. A receipt produced in either
    # mode must NAME the mode.
    # The vectors come from the PRODUCT's own store seam via the SHARED
    # primitive ``tortoise.sdk._capture_turn_embeddings`` — never a local copy
    # and never the raw encoder. It applies the STORE's width
    # (``proj.required_embedding_dim``: ``EMBEDDING_DIM`` on an indexed store,
    # ``None`` on the index-less brute-force lane, where any self-consistent
    # width is usable and must NOT be dropped — #4280) and fails soft to
    # ``None`` per turn when no embedder is installed.
    embeddings = (
        _capture_turn_embeddings(turn_texts, proj.required_embedding_dim)
        if embed else [None] * len(turn_texts))
    turn_ids: list[str] = []
    for i, turn in enumerate(windowed):
        role = _normalize_turn_role(turn.get("role"))
        turn_id = f"{session_id}_t{i}"
        # `_capture_turn_window` applied the cap and its marker to the
        # pre-redaction text; the shared `_capture_turn_texts` (#4911) scrubs
        # that window and does NOT re-clip it (#4897), exactly as the live
        # store loop does.
        turn_text = turn_texts[i]
        # Node MERGE BEFORE the edge MERGE — capture's #490 ordering rule: a
        # full-path MERGE whose edge is missing makes FalkorDB create the
        # whole path from scratch, duplicating the Point node.
        from tortoise.vector_write import (  # #4520: the ONE vector write seam
            clear_then_set_prefix,
        )
        proj.g.query(
            "MERGE (t:Point {id:$id}) "
            # #4194: capture the node's PRE-write content_hash before the SET
            # reassigns it, so the vector preserve/clear decision is made
            # against the real prior (the product's own turn write does this).
            "WITH t, t.content_hash AS prior_ch "
            # #4520: the shared clear-then-set seam — this hand-copied turn
            # write is the pre-fix `_TURN_WRITE_CYPHER` shape, which the
            # embedded engine silently discards on a re-seeded turn.
            + clear_then_set_prefix("t", "$emb")
            + "SET t.content=$c, t.pointKind=$k, t.is_operator=false, "
            "    t.speaker=$speaker, "
            "    t.is_episodic=true, "
            "    t.status=coalesce(t.status, $s), "
            f"    {time_sets}"
            f"    {sk_sets}"
            "    t.content_hash=$ch, "
            # The product's own vector, vecf32-wrapped so the stored shape
            # matches every other server-owned writer's (a bare List node is absent from the
            # Point index cohort, #8429; the read boundary normalises a
            # right-width List since #8316, and a wrong width or a non-numeric
            # value still aborts one — #8430),
            # and the product's OWN three-way guard: new vector when one was
            # encoded; else PRESERVE an unchanged turn's vector (so
            # ``embed=False`` models the no-embedder RE-capture too); else
            # CLEAR it (a preserved vector for changed text is the dense-leg
            # lie).
            "    t.embedding=CASE WHEN $emb IS NOT NULL THEN vecf32($emb) "
            "        WHEN prior_ch = $ch THEN t.embedding ELSE NULL END",
            params={"id": turn_id, "c": turn_text, "k": "event",
                    "speaker": role, "s": "draft",
                    "ch": _content_hash(turn_text), "emb": embeddings[i],
                    **({"sk": _derive_search_keys(turn_text)}
                       if search_keys else {}),
                    **time_params},
        )
        proj.g.query(
            "MATCH (s:Session {id:$sid}), (t:Point {id:$tid}) "
            "MERGE (s)-[:CONTAINS]->(t)",
            params={"sid": session_id, "tid": turn_id},
        )
        turn_ids.append(turn_id)
    if now is None:
        # #4156: the sweep is over the session's STORED ``CONTAINS`` Point
        # set — not over the window rewritten above, and not only over turns:
        # the deleted helper ran the same unrestricted match, and a re-seed
        # with a SHORTER conversation must clear the older turns too, or the
        # session records a fabricated date again through the back door.
        # (Capture CONTAINS-wires extracted claim Points as well, so those are
        # swept with the turns — their ``createdAt`` here, and their
        # ``updatedAt`` too, which the deleted helper left alone. No caller
        # passes ``now=None`` for a session holding extracted claims today.)
        proj.g.query(
            "MATCH (s:Session {id:$sid})-[:CONTAINS]->(t:Point) "
            "SET t.createdAt = null, t.updatedAt = null",
            params={"sid": session_id},
        )
    return turn_ids


def _seed_memory(sdk: TortoiseSDK, question: dict, *,
                 embed: bool = SEED_TURNS_EMBEDDED_BY_DEFAULT,
                 search_keys: bool = SEED_SEARCH_KEYS_BY_DEFAULT) -> None:
    """Seed the haystack in the CAPTURE shape (#3910).

    Mirrors the turn-store sub-step of ``_capture_session_impl`` — the part
    of a capture this fixture reproduces — and nothing the real read path
    cannot consume:

      * a ``(:Session {id})`` node, id = the question's own
        ``haystack_session_ids[i]``. A fixture with NO id list (or a blank
        entry) falls back to the synthetic ``sess-{i}``: that value is a
        PLACEHOLDER, not a capture id — capture's session id is either the
        client's own or the server-minted ``session_<hex12>`` form — so an
        identity comparison against gold ids is only meaningful for fixtures
        that carry ``haystack_session_ids``, and a misaligned (short) id list
        is silently padded with placeholders;
      * ONE episodic turn ``:Point`` PER windowed turn — no blank skip,
        exactly as capture — with the deterministic id ``f"{sid}_t{i}"``,
        ``pointKind='event'``, ``is_episodic=true``, ``is_operator=false``,
        ``speaker`` and the ``[role] <content>`` text — and NO
        ``sessionId`` / ``eventId`` prop (capture writes neither);
      * the ``(:Session)-[:CONTAINS]->(:Point)`` edge — the provenance
        mechanism the shipping read resolves identity from
        (``OPTIONAL MATCH (sess:Session)-[:CONTAINS]->(n)``).

    The turn window, the whole-session blank gate and the node/edge write all
    live in ``seed_capture_turn_store`` — the SAME function the other ask
    fixtures seed through (#3914), so this lane cannot drift from them. That
    function is still a THIRD copy of capture's per-turn store (the two live
    writers being ``TortoiseSDK.capture_session`` and
    ``hosted_api._capture_session_impl``); the NOTE at ``tortoise/sdk.py`` /
    ``tortoise/hosted_api.py`` names it — a comment, not an enforced check;
    #3551 tracks collapsing all three onto one shared primitive.

    Pre-#3910 this seeder instead made plain ``statement`` Points and wrote
    ``p.sessionId`` / ``p.eventId`` PROPS with NO edge — a graph the capture
    path cannot produce. The shipping fetch PREFERS a renderable
    ``p.sessionId`` prop over the CONTAINS edge, so that fixture read GREEN
    on a graph where the edge path was entirely broken.

    Reproduced IN ADDITION to capture's own write (#5534): callers that opt
    in (``search_keys=True``) get the E3 ``search_keys`` substrate on each
    turn Point, DERIVED deterministically from that turn's own text, because
    A4 (``TORTOISE_ASK_SEARCH_KEYS_PRF``) harvests ``search_keys`` from the
    first-pass top-5 hits and is STRUCTURALLY INERT on a store with none —
    the instrument's A/B then returns a guaranteed zero and cannot clear
    A4. This is the same class of instrument repair as W7A's embedding
    default: capture's TURN write stores no ``search_keys`` (E3 writes it on
    extracted claim Points), so a pure turn store leaves the lever with no
    input. ``search_keys`` defaults OFF because that IS the capture shape;
    the A4-bearing runs turn it on EXPLICITLY (see
    ``SEED_SEARCH_KEYS_BY_DEFAULT``) so no non-A4 caller's store moves.

    Deliberately NOT reproduced (this seeds a TURN STORE, it is not a
    capture): no ``:Source``
    materialization, no extracted claim Points. The turn EMBEDDING **IS**
    reproduced (``embed=True``, the default) because the product's write path
    now stores one (#4194) and a fixture without it blinds every retrieval
    measurement to the dense leg (W7A); ``embed=False`` reproduces the
    pre-#4194 / no-embedder store (re-embedding that saved backlog is the
    #4197 owner decision). The per-session
    ``:Event`` write is RETAINED for fixture compatibility (nothing in the
    repo reads ``ev-s{i}``, and it is NOT capture's ``sessionCaptured``
    Event — different id, different prop set): it is a date-only marker, and
    nothing joins a turn Point to it. That last part IS faithful to capture
    — turn Points carry no ``eventId`` there either.

    #4106: the session's own RECORDED time (``:Session.created_at``, which
    capture writes) now carries the fixture's ``haystack_dates[i]`` date,
    not the seeding wall clock. Capture-shaped turn Points carry no
    ``eventId``, so the ONLY recorded date a turn can reach is the session's
    own — seeding it as ``now`` is what makes that recorded time TRUE. It
    was previously ``datetime.now()``, i.e. the wall clock of the test run,
    which would have made every turn's date a FABRICATION the moment the
    read path started rendering it (#4106 safety rule: a wrong date is worse
    than no date). Seeding it as the fixture's date is the same convention
    the eval ingest uses (``tools/longmem_eval/ingest_v2.py`` sets
    ``s.created_at`` from ``session_date``) and keeps the turn ``createdAt``
    equal to the session's, exactly as capture writes both from one ``now``.
    """
    proj = sdk._get_proj()
    sessions = question.get("haystack_sessions") or []
    dates = question.get("haystack_dates") or []
    session_ids = question.get("haystack_session_ids") or []
    for i, session in enumerate(sessions):
        raw_sid = session_ids[i] if i < len(session_ids) else None
        sid = (raw_sid.strip()
               if isinstance(raw_sid, str) and raw_sid.strip()
               else f"sess-{i}")
        # #4106: the fixture's own date, or UNKNOWN. A placeholder would be
        # rendered to the reader as a real session date.
        sdate = _fixture_session_date(dates[i]) if i < len(dates) else ""
        # The SAME window, blank gate and store write both capture surfaces
        # run (#1532 D1 / #1529 D3) — a session with no extractable line
        # contributes NO Session, NO turn Point and NO Event. The session's
        # recorded time IS the fixture's session date (#4106) — one ``now``
        # for the session and all of its turns, as capture writes them.
        turn_ids = seed_capture_turn_store(
            sdk, sid, session or [],
            now=f"{sdate}T10:00:00Z" if sdate else None,
            embed=embed, search_keys=search_keys)
        if not turn_ids:
            continue
        if not sdate:
            # #4106/#4156: the fixture records NO date for this session, so
            # the seeder is told exactly that (``now=None``) and writes NO
            # recorded time — the read path reports UNKNOWN instead of a
            # fabricated run date, with nothing to erase afterwards.
            continue
        proj.g.query(
            "MERGE (e:Event {eventId: $eid}) SET e.startedAt = $st",
            params={"eid": f"ev-s{i}", "st": f"{sdate}T10:00:00Z"},
        )


def _load_composition(path: str | None = None) -> list[dict]:
    """Load the spot-check composition (issue #2071 step 1).

    Resolution order: explicit ``path`` (CLI) → ``TORTOISE_SPOTCHECK_FIXTURE``
    env → the committed fixture → ``/tmp/ask_spotcheck.json`` (legacy
    compat). Accepts a bare list or a ``{"questions": [...]}`` wrapper.
    Raises FileNotFoundError when none exists.
    """
    candidates = [p for p in (path, os.environ.get(_FIXTURE_ENV))
                  if p] + [_COMMITTED_FIXTURE, _LEGACY_FIXTURE]
    for cand in candidates:
        if cand and os.path.exists(cand):
            with open(cand) as f:
                data = json.load(f)
            if isinstance(data, dict) and "questions" in data:
                return data["questions"]
            if isinstance(data, list):
                return data
            raise ValueError(
                f"spot-check composition {cand!r}: expected a list of "
                f"questions or a {{\"questions\": [...]}} wrapper")
    raise FileNotFoundError(
        "no spot-check composition found (tried "
        f"{candidates}; set --fixture or {_FIXTURE_ENV})")


def _normalize(text) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


def _require_judge_key() -> str:
    """Fail-fast pre-flight (issue #2071 step 6): the semantic judge grades
    EVERY question, so a judge provider key is required BEFORE any question
    is graded. Returns the key env var name when present; raises
    RuntimeError/ValueError naming the prerequisite when absent.

    NEVER falls back to the removed word-overlap bar — a silent fallback
    would re-False the 3 long-gold questions under an unreachable bar and
    produce a misleading aggregate (the pre-#2071 defect).
    """
    raw_spec = os.environ.get("TORTOISE_LME_JUDGE_MODEL", "").strip() or DEFAULT_JUDGE_MODEL
    provider, _model = _parse_model_spec(raw_spec)
    if provider is not None:
        if provider not in _PROVIDERS:
            raise ValueError(
                f"unknown judge provider {provider!r} in {raw_spec!r}; "
                f"known: {sorted(_PROVIDERS)}")
        key_env = _PROVIDERS[provider][1]
        if not os.environ.get(key_env):
            raise RuntimeError(
                f"spot-check grading requires {key_env} — the semantic "
                f"judge grades EVERY question (issue #2071 owner decision "
                f"2026-08-31; judge spec {raw_spec!r}). Set {key_env} (or "
                f"TORTOISE_LME_JUDGE_MODEL naming a provider whose key is "
                f"set). There is NO silent fallback to the old word-overlap "
                f"bar.")
        return key_env
    # bare model id: mirror build_judge's _resolve_provider — any configured
    # provider key satisfies the endpoint; the check only fails when NONE is
    # configured.
    for p in _SESSION_LLM_PROVIDER_PRIORITY:
        if os.environ.get(_PROVIDERS[p][1]):
            return _PROVIDERS[p][1]
    raise RuntimeError(
        "no LLM provider key configured for the spot-check judge (set "
        "OPENROUTER_API_KEY / DEEPSEEK_API_KEY / OPENAI_API_KEY / "
        "GEMINI_API_KEY) — the semantic judge grades EVERY question "
        "(issue #2071 owner decision 2026-08-31); there is NO silent "
        "fallback to the old word-overlap bar.")


def _turn_present(evidence_norm: str, content) -> bool:
    """True when a distinctive window of a turn's verbatim content appears
    in the normalized evidence string (the reader's assembled context). The
    first ~200 chars are probed (a long turn truncated by the 32 KiB byte
    cap still matches via its head); a <20-char probe is too weak to count.
    """
    c = _normalize(content)
    if not c:
        return False
    probe = c[:200]
    return len(probe) >= 20 and probe in evidence_norm


def _gold_sessions_covered(evidence: str, question: dict) -> bool | None:
    """Gold-session coverage of the READER's context (retrieval+assembly
    recall readout — the #2280 harness leg, field practice: LongMemEval
    ships ``answer_session_ids``; mem0/gbrain measure retrieval separately
    from reading).

    True  → EVERY gold session has >=1 turn present in the evidence the
            reader saw (a reading miss is never a retrieval miss).
    False → at least one gold session is entirely outside the assembled
            context (retrieval/assembly starved the reader).
    None  → the question carries no gold ids (legacy fixture).
    """
    hay_ids = question.get("haystack_session_ids") or []
    gold_ids = question.get("answer_session_ids") or []
    sessions = question.get("haystack_sessions") or []
    if not gold_ids or not hay_ids:
        return None
    pos = {sid: i for i, sid in enumerate(hay_ids)}
    evidence_norm = _normalize(evidence)
    for gid in gold_ids:
        i = pos.get(gid)
        if i is None or i >= len(sessions):
            return False  # conservative: unknown gold mapping = uncovered
        turns = sessions[i] or []
        if not any(_turn_present(evidence_norm, t.get("content"))
                   for t in turns):
            return False
    return True


def _grade(question: dict, result: dict, judge=None) -> tuple[bool, str, str]:
    """Semantic grading (issue #2071 owner decision 2026-08-31).

    EVERY question is graded by the benchmark-standard semantic judge
    (``build_judge()`` → official gpt-4o anscheck — the same judge the
    graded eval lane uses). The word-overlap bar is REMOVED from this
    path (demoted to the key-free CI MockJudge substitute only).

    Returns ``(ok, note, kind)`` where ``kind`` is the scoring method:
    ``"llm"`` for the semantic judge call, ``"marker"`` for the ``_abs``
    deterministic marker short-circuit (unchanged, precedes the judge
    call — any other ``_abs`` answer falls through to the judge's
    abstention template).
    """
    if judge is None:
        judge = build_judge()
    qid = question.get("question_id") or ""
    if "_abs" in qid:
        marker_ok = result["abstained"] and any(
            m in _normalize(result["answer"]) for m in MockJudge._ABSTRACTION_MARKERS)
        if marker_ok:
            return True, f"abstain={result['abstained']} answer={result['answer'][:80]!r}", "marker"
        # fall through to the semantic judge (abstention template)
    verdict = judge.judge(
        question_type=question.get("question_type") or "",
        question=question.get("question") or "",
        answer=question.get("answer") or "",
        hypothesis=result.get("answer") or "",
        abstention="_abs" in qid,
    )
    return verdict, f"semantic judge={verdict} answer={result['answer'][:80]!r}", "llm"


def _record(question: dict, result: dict, judge) -> dict:
    """Per-question output record with scoring provenance (issue #2071 step
    4): ``judge`` (``llm`` on the live semantic path / ``marker`` for the
    deterministic ``_abs`` short-circuit) + ``judge_model`` (the judge's
    model id — e.g. the official gpt-4o id on the live path)."""
    ok, note, kind = _grade(question, result, judge)
    return {
        "question_id": question.get("question_id") or "",
        "ok": ok,
        "abstained": result["abstained"],
        "qtype_detected": result["question_type"],
        "judge": kind,
        "judge_model": judge.model_id,
        "note": note,
    }


# ── #9095: the distilled + planted-supersession leg ──────────────────────


def load_distilled_cases(path: str | None = None) -> list[dict]:
    """Load the authored distilled/supersession corpus (#9095).

    Resolution order: explicit ``path`` → ``TORTOISE_SPOTCHECK_DISTILLED_CORPUS``
    env → the committed ``tests/fixtures/ask_spotcheck_distilled.json``.
    Accepts a bare list or a ``{"cases": [...]}`` wrapper. Raises
    ``FileNotFoundError`` when none exists — a missing corpus is NEVER an
    empty run (an empty run reads as a green gate).
    """
    candidates = [p for p in (path, os.environ.get(_DISTILLED_FIXTURE_ENV))
                  if p] + [DISTILLED_FIXTURE]
    for cand in candidates:
        if cand and os.path.exists(cand):
            with open(cand) as f:
                data = json.load(f)
            if isinstance(data, dict) and "cases" in data:
                cases = data["cases"]
            elif isinstance(data, list):
                cases = data
            else:
                raise ValueError(
                    f"distilled corpus {cand!r}: expected a list of cases or "
                    f"a {{\"cases\": [...]}} wrapper")
            if not cases:
                raise ValueError(
                    f"distilled corpus {cand!r} carries no cases — an empty "
                    f"corpus cannot measure the D8 gate")
            return cases
    raise FileNotFoundError(
        "no distilled corpus found (tried "
        f"{candidates}; set --distilled-corpus or {_DISTILLED_FIXTURE_ENV})")


def _claim_session_id(case: dict, claim: dict, role: str) -> str:
    """The haystack session a distilled claim was EXTRACTED FROM."""
    ids = case.get("haystack_session_ids") or []
    idx = claim.get("session_index")
    if not isinstance(idx, int) or isinstance(idx, bool) \
            or not 0 <= idx < len(ids):
        raise ValueError(
            f"planted case {case.get('question_id')!r}: distilled.{role}"
            f".session_index {idx!r} does not name a haystack session "
            f"({len(ids)} present)")
    sid = ids[idx]
    if not isinstance(sid, str) or not sid.strip():
        raise ValueError(
            f"planted case {case.get('question_id')!r}: distilled.{role} "
            f"names session index {idx} whose id is {sid!r}")
    return sid


def _claim_turn_index(case: dict, claim: dict, role: str) -> int:
    """The SOURCE TURN ordinal of a distilled claim (``source_turn_id``)."""
    idx = claim.get("turn_index")
    if not isinstance(idx, int) or isinstance(idx, bool) or idx < 0:
        raise ValueError(
            f"planted case {case.get('question_id')!r}: distilled.{role}"
            f".turn_index {idx!r} is not a turn ordinal")
    return idx


def seed_distilled_corpus(sdk: TortoiseSDK, case: dict) -> dict[str, str]:
    """Write ONE planted case's distilled claim pair (#9095) — ADDITIVE.

    ``_seed_memory``'s raw turn leg is not touched: this is a SIBLING seeder,
    so the 21-question composition keeps its store and its number (the same
    reason ``SEED_SEARCH_KEYS_BY_DEFAULT`` exists — a mode flag on the shared
    seeder would move every non-opt-in caller).

    The write is the capture EXTRACTION writer's own call, not a new shape:
    ``create_point(kind='statement', is_episodic=False, session_id=…,
    extractedFrom='session:<sid>', validFrom=…, source_turn_id=…, quote=…)``
    (a distilled claim is not an episodic turn — the raw turn store already
    holds that), then the ``(:Session)-[:CONTAINS]->(:Point)`` provenance edge
    capture wires itself, then the supersession through the product's own
    :meth:`TortoiseSDK.supersede_point` — CORRECTS edge + the old point's
    terminal status + the contiguous bitemporal window (``validTo`` = the
    successor's ``validFrom``). Nothing here writes a status flag by hand: a
    harness that faked the marking could not measure whether the PRODUCT's
    marking is what the reader sees.

    Idempotent for a re-seed (``dedup=True`` folds on ``(pointKind,
    content_hash)``, and an already-superseded predecessor is not
    re-superseded). Returns ``{"old": <id>, "new": <id>}``.
    """
    spec = case.get("distilled")
    if not isinstance(spec, dict) or not spec.get("old") or not spec.get("new"):
        raise ValueError(
            f"planted case {case.get('question_id')!r} carries no "
            f"distilled.old / distilled.new pair")
    proj = sdk._get_proj()
    ids: dict[str, str] = {}
    for role in ("old", "new"):
        claim = spec[role]
        content = claim.get("content")
        valid_from = claim.get("valid_from")
        if not isinstance(content, str) or not content.strip():
            raise ValueError(
                f"planted case {case.get('question_id')!r}: distilled.{role}"
                f".content is empty")
        if not isinstance(valid_from, str) or not valid_from.strip():
            raise ValueError(
                f"planted case {case.get('question_id')!r}: distilled.{role}"
                f".valid_from is empty — a supersession needs a WINDOW")
        sid = _claim_session_id(case, claim, role)
        turn_index = _claim_turn_index(case, claim, role)
        written = sdk.create_point(
            "statement",
            content,
            is_episodic=False,
            dedup=True,
            session_id=sid,
            extractedFrom=f"session:{sid}",
            validFrom=valid_from,
            source_turn_id=turn_index,
            quote=claim.get("quote") or "",
        )
        pid = str(written["id"])
        # Node MERGE BEFORE the edge MERGE (capture's #490 ordering rule).
        proj.g.query(
            "MATCH (s:Session {id:$sid}), (p:Point {id:$pid}) "
            "MERGE (s)-[:CONTAINS]->(p)",
            params={"sid": sid, "pid": pid},
        )
        ids[role] = pid
    old_point = sdk.get_point(ids["old"]) or {}
    if (old_point.get("status") or "") != "superseded":
        sdk.supersede_point(ids["old"], ids["new"])
    return ids


def superseded_live_in_evidence(evidence: str, case: dict) -> dict:
    """The D8 gate read out of the ASSEMBLED READER EVIDENCE (#9095).

    ``docs/product-success-eval.md``: "0% of superseded points appear as live
    answers (must be flagged or excluded)" — so the check reads exactly what
    the reader saw, not package stats. ``_render_block`` renders one hit as
    ``<annotation prefix> <body>``: the body is the TAIL of its block, while
    every marker and snippet lives in the PREFIX. Two consequences, both
    load-bearing:

    * a block is the OLD claim when its normalized text ENDS WITH the claim's
      content — a bracketed prefix can never satisfy that, so **the snippet
      trap is closed**: the SUCCESSOR's block carries ``[SUPERSEDES: <old
      content>]`` and holds the old content verbatim, and a substring check
      would count it (a guaranteed green on exactly the case the gate exists
      to catch);
    * of the blocks that ARE the old claim, one that carries no
      ``[SUPERSEDED BY:`` marker was served as a live answer — the violation.

    Identity note: an authored case keeps its distilled claim content
    DISTINCT from every raw turn's text (distillation is not transcription),
    which is what lets the superseded claim's block be told apart from the
    turn it was distilled from. ``tests/test_ask_spotcheck_distilled.py``
    pins that corpus property, so a corpus edit cannot silently make this
    check ambiguous.

    Returns ``{"seen": int, "rendered_live": int, "rate": float|None}``.
    ``seen`` is how many blocks rendered the old claim at all; ``rate`` is
    ``None`` when ``seen == 0`` — an unobserved case is UNMEASURED, never
    0.0 (that would be a green gate on a harness that saw nothing). Whole
    blocks are split on the blank line ``render_context`` joins them with; a
    multi-PARAGRAPH claim body spans blocks (both authored and product claims
    here are single-line, and a claim that is not found simply reads as
    ``seen == 0`` — failing CLOSED, never open).
    """
    old = ((case.get("distilled") or {}).get("old") or {})
    target = _normalize(old.get("content"))
    seen = rendered_live = 0
    for block in (evidence or "").split("\n\n"):
        if not target or not _normalize(block).endswith(target):
            continue
        seen += 1
        if SUPERSEDED_MARKER not in block:
            rendered_live += 1
    return {"seen": seen, "rendered_live": rendered_live,
            "rate": (rendered_live / seen) if seen else None}


def _distilled_judge():
    """The distilled leg's judge: key-free ``MockJudge`` when
    ``TORTOISE_SPOTCHECK_JUDGE=mock``, else the composition path's rule."""
    if (os.environ.get(DISTILLED_JUDGE_ENV, "").strip().lower()
            == "mock"):
        return build_judge(mock=True)
    _require_judge_key()
    return build_judge()


def run_distilled_case(case: dict, judge) -> dict:
    """ONE planted case, BOTH arms, one invocation (D4: paired, never a
    parallel benchmark).

    ``raw`` = ``_seed_memory`` only; ``distilled`` = ``_seed_memory`` +
    ``seed_distilled_corpus``. Same case, same judge, same reader — the only
    variable is whether the store carries a distilled, superseded claim.
    Each arm gets its own temp DB, so neither can contaminate the other.
    """
    record: dict = {"question_id": case.get("question_id") or ""}
    for arm in ("raw", "distilled"):
        db = os.path.join(tempfile.mkdtemp(prefix=f"ask_spot_{arm}_"), "t.db")
        sdk = TortoiseSDK(db)
        try:
            _seed_memory(sdk, case)
            if arm == "distilled":
                seed_distilled_corpus(sdk, case)
            qdate = _to_iso_date(case.get("question_date") or "")
            try:
                result = run_ask_lane(sdk, case["question"],
                                      question_date=qdate)
            except Exception as e:  # noqa: BLE001, RUF100 — same per-question
                # rule as the composition leg: an unavailable reader is a
                # FAILED measurement, recorded (never a silent pass) rather
                # than aborting the remaining cases.
                record[arm] = {
                    "answer": "",
                    "evidence": "",
                    "error": f"ask unavailable: {type(e).__name__}",
                    "current_state": {"committed": False, "n": 0,
                                      "rate": None},
                    "superseded_live": superseded_live_in_evidence(
                        "", case),
                }
                continue
            ok, note, kind = _grade(case, result, judge)
            record[arm] = {
                "answer": result.get("answer") or "",
                "evidence": result.get("evidence") or "",
                "judge": kind,
                "note": note,
                "current_state": {"committed": bool(ok), "n": 1,
                                  "rate": 1.0 if ok else 0.0},
                "superseded_live": superseded_live_in_evidence(
                    result.get("evidence") or "", case),
            }
        finally:
            sdk.close()
    return record


def _arm_summary(records: list[dict], arm: str) -> dict:
    """Aggregate ONE arm's two numbers across the planted cases."""
    committed = measured = seen = rendered_live = 0
    for rec in records:
        whole = rec.get(arm) or {}
        cs = whole.get("current_state") or {}
        n = int(cs.get("n") or 0)
        measured += n
        committed += 1 if (n and cs.get("committed")) else 0
        sl = whole.get("superseded_live") or {}
        seen += int(sl.get("seen") or 0)
        rendered_live += int(sl.get("rendered_live") or 0)
    return {
        "current_state": {
            "committed": committed, "n": measured,
            "rate": (committed / measured) if measured else None},
        "superseded_live": {
            "seen": seen, "rendered_live": rendered_live,
            "rate": (rendered_live / seen) if seen else None},
    }


def _distilled_payload(records: list[dict]) -> dict:
    """The machine-readable distilled report (one ``DISTILLED_JSON`` line)."""
    return {
        "cases": [
            {"question_id": rec.get("question_id") or "",
             "raw": {"current_state": (rec.get("raw") or {}).get(
                 "current_state"),
                     "superseded_live": (rec.get("raw") or {}).get(
                         "superseded_live"),
                     "error": (rec.get("raw") or {}).get("error")},
             "distilled": {
                 "current_state": (rec.get("distilled") or {}).get(
                     "current_state"),
                 "superseded_live": (rec.get("distilled") or {}).get(
                     "superseded_live"),
                 "error": (rec.get("distilled") or {}).get("error")}}
            for rec in records
        ],
        "arms": {arm: _arm_summary(records, arm)
                 for arm in ("raw", "distilled")},
    }


def _distilled_hard_failures(payload: dict) -> list[str]:
    """The D8 gate's failure list — FAIL CLOSED.

    Two hard failures, both per case, both on the DISTILLED arm (the raw arm
    has no superseded point, so there is nothing there to flag — that
    contrast IS the measurement):

    1. ``rendered_live > 0`` — a superseded claim reached the reader as a live
       answer. The product gate is 0%, so any occurrence fails the run.
    2. ``seen == 0`` — the superseded claim never reached the reader, so the
       gate was never exercised. That is a RETRIEVAL gap in the instrument,
       not a pass, and an unmeasured gate must never read green.

    Plus an unavailable reader (``n == 0`` with an error): no measurement was
    taken. ``current_state`` alone is NEVER a hard failure — D8 gives the
    knowledge-update rate no threshold; the first run sets it.
    """
    failures: list[str] = []
    for case in payload.get("cases") or []:
        qid = case.get("question_id") or "<unnamed>"
        arm = case.get("distilled") or {}
        if arm.get("error"):
            failures.append(
                f"{qid}: no measurement — {arm['error']} (the D8 gate cannot "
                f"be read off a failed reader)")
            continue
        sl = arm.get("superseded_live") or {}
        if int(sl.get("rendered_live") or 0) > 0:
            failures.append(
                f"{qid}: {sl['rendered_live']} superseded claim(s) rendered "
                f"live (no {SUPERSEDED_MARKER} marker) — the D8 gate is 0%")
        elif not int(sl.get("seen") or 0):
            failures.append(
                f"{qid}: the superseded claim was never observed in the "
                f"reader's evidence — the D8 gate is UNMEASURED, not passed")
    return failures


def _format_distilled_line(idx: int, total: int, rec: dict) -> str:
    """One human line per planted case: both arms side by side."""
    parts = []
    for arm in ("raw", "distilled"):
        whole = rec.get(arm) or {}
        if whole.get("error"):
            parts.append(f"{arm}: {whole['error']}")
            continue
        cs = whole.get("current_state") or {}
        sl = whole.get("superseded_live") or {}
        parts.append(
            f"{arm}: committed={cs.get('committed')} "
            f"superseded_live={sl.get('rendered_live')}/{sl.get('seen')}")
    return (f"[distilled {idx}/{total}] "
            f"{(rec.get('question_id') or '')[:34]:34s} " + " | ".join(parts))


def _run_distilled_leg(corpus_path: str | None) -> int:
    """Run the distilled + planted-supersession leg (``--distilled``)."""
    try:
        cases = load_distilled_cases(corpus_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ask_spotcheck: {exc}", file=sys.stderr)
        return 2
    try:
        judge = _distilled_judge()
    except (RuntimeError, ValueError) as exc:
        print(f"ask_spotcheck: {exc}", file=sys.stderr)
        return 2
    records: list[dict] = []
    try:
        for i, case in enumerate(cases):
            rec = run_distilled_case(case, judge)
            records.append(rec)
            print(_format_distilled_line(i + 1, len(cases), rec))
    except ValueError as exc:
        print(f"ask_spotcheck: {exc}", file=sys.stderr)
        return 2
    payload = _distilled_payload(records)
    failures = _distilled_hard_failures(payload)
    payload["hard_failures"] = failures
    for arm in ("raw", "distilled"):
        cs = payload["arms"][arm]["current_state"]
        sl = payload["arms"][arm]["superseded_live"]
        rate = "unmeasured" if cs["rate"] is None else f"{cs['rate']:.2f}"
        live = ("unmeasured" if sl["rate"] is None
                else f"{sl['rate']:.2f}")
        print(f"distilled[{arm}]: current_state {cs['committed']}/{cs['n']} "
              f"({rate}; D8 sets no threshold — this run sets it) — "
              f"superseded_live {sl['rendered_live']}/{sl['seen']} ({live}; "
              f"product gate 0%)")
    for failure in failures:
        print(f"distilled: HARD FAILURE — {failure}", file=sys.stderr)
    print("DISTILLED_JSON " + json.dumps(payload, sort_keys=True))
    print(f"distilled: {len(cases)} planted case(s), judge={judge.model_id}, "
          f"{len(failures)} hard failure(s) (#9095)")
    return 0 if not failures else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="eval-lane ask QA spot-check (issue #2071: full-semantic "
                    "grading; fail-fast on missing judge key; #9095: "
                    "--distilled runs the planted-supersession leg)")
    ap.add_argument(
        "--fixture", default=None,
        help=f"composition path (default: {_FIXTURE_ENV} env, then the "
             f"committed tests/fixtures/ask_spotcheck_composition.json, "
             f"then {_LEGACY_FIXTURE})")
    ap.add_argument(
        "--distilled", action="store_true",
        help="run the DISTILLED + planted-supersession leg (#9095) instead "
             "of the composition: the authored 3-case corpus, both arms "
             "per case, emitting one DISTILLED_JSON line. The two legs are "
             "kept apart on purpose — their classes have different targets "
             "(0.8 aggregate vs a 0% rendered-live gate) and folding them "
             "would make both numbers unreadable")
    ap.add_argument(
        "--distilled-corpus", default=None,
        help=f"distilled corpus path (default: {_DISTILLED_FIXTURE_ENV} env, "
             f"then the committed tests/fixtures/"
             f"ask_spotcheck_distilled.json)")
    args = ap.parse_args(argv)

    if args.distilled:
        return _run_distilled_leg(args.distilled_corpus)

    spotcheck = _load_composition(args.fixture)
    # Fail-fast pre-flight (issue #2071 step 6): no judge key → exit BEFORE
    # any question is graded, naming the prerequisite. NEVER a silent
    # fallback to the removed word-overlap bar.
    try:
        key_env = _require_judge_key()
    except (RuntimeError, ValueError) as exc:
        print(f"ask_spotcheck: {exc}", file=sys.stderr)
        return 2
    judge = build_judge()

    results = []
    for i, q in enumerate(spotcheck):
        db = os.path.join(tempfile.mkdtemp(prefix="ask_spot_"), "t.db")
        sdk = TortoiseSDK(db)
        try:
            _seed_memory(sdk, q)
            qdate = _to_iso_date(q.get("question_date") or "")
            try:
                result = run_ask_lane(sdk, q["question"], question_date=qdate)
            except Exception as e:  # noqa: BLE001, RUF100 — a per-question
                # reader/retrieval malfunction (#2280: the empty-output
                # fail-loud path raises AskReaderUnavailable) is a MISS for
                # THIS question, never a whole-run abort — record it and
                # keep aggregating the remaining questions.
                rec = {"question_id": q.get("question_id") or "",
                       "ok": False, "abstained": True,
                       "qtype_detected": None,
                       "judge": "unavailable", "judge_model": "",
                       "note": f"ask unavailable: {type(e).__name__}",
                       "ctx_recall": None}
                results.append(rec)
                print(f"[{i+1}/{len(spotcheck)}] "
                      f"{q['question_id'][:34]:34s} ok=False "
                      f"abstained=unavailable ctx=None — "
                      f"ask unavailable: {type(e).__name__}: {e}")
                continue
            rec = _record(q, result, judge)
            # #2280 harness leg: gold-session coverage of the reader's
            # context (retrieval measured separately from reading).
            rec["ctx_recall"] = _gold_sessions_covered(
                result.get("evidence") or "", q)
            results.append(rec)
            print(f"[{i+1}/{len(spotcheck)}] {q['question_id'][:34]:34s} "
                  f"ok={rec['ok']} abstained={rec['abstained']} "
                  f"detected={rec['qtype_detected']} "
                  f"ctx={rec['ctx_recall']} "
                  f"judge={rec['judge']}/{rec['judge_model']} — {rec['note']}")
        finally:
            sdk.close()
    n_ok = sum(1 for r in results if r["ok"])
    print(f"\nspot-check: {n_ok}/{len(results)} correct "
          f"(aggregate {n_ok / len(results):.2f}, target >= 0.8; "
          f"graded by the semantic judge ({judge.model_id}) — issue #2071; "
          f"judge key: {key_env})")
    with_gold = [r for r in results if r.get("ctx_recall") is not None]
    if with_gold:
        hit = sum(1 for r in with_gold if r["ctx_recall"])
        misses = [r["question_id"] for r in with_gold
                  if not r["ctx_recall"]]
        print(f"ctx gold-session recall: {hit}/{len(with_gold)} "
              f"({hit / len(with_gold):.2f}) — gold sessions outside the "
              f"reader's context: {misses or 'none'} (#2280)")
    return 0 if (len(results) and n_ok / len(results) >= 0.8) else 1


if __name__ == "__main__":
    sys.exit(main())

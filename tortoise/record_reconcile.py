"""The record↔cache reconciler's PRODUCTION TRIGGER (#9189 / §17.2).

``tortoise/reconciliation.py`` is the COMPARISON: it takes two keyed views and
reports divergence as integer counts, refusing when a side cannot be read.
Measured at ``f96a18da1`` it had **no production caller at all** — it was
exercised by tests and by the #7950 evidence probes only. §17.2 requires
*continuous reconciliation*: **divergence must be OBSERVED, not merely
recoverable**, and a check with no caller observes nothing.

This module is the trigger. It supplies the two production READERS, and one
pure composition that answers §17.2's two open questions per call: *what is
divergence* and *what is a residency outcome*.

THE TRIGGER DECISION, AND ITS DETECTION LATENCY
===============================================
**An on-demand / periodically-scheduled sweep, invoked through the CLI**
(``python -m tortoise record-reconcile``), not a call inside the capture turn.

* §17.2 says *continuous*; capture latency says *not on the turn*. A Postgres
  round-trip inside the capture turn is the same latency cost #9188 weighs for
  the write side, spent on a check that can be made later without changing what
  the user sees.
* The capture boundary is **not** the available alternative yet: #9187 (the
  record-write seam) and #9188 (the production writer) are the boundary, and
  until they land there is no capture turn that writes a record to observe.
  When they land, the hook is :func:`reconcile_record_and_cache` — one call,
  already pure, so the trigger can move without the comparison moving.
* **Detection latency: the sweep interval, named by whoever schedules it.**
  Nothing here chooses that number; the CLI is the thing a scheduler drives
  (the ``tools/install-*-schedule.sh`` precedent this repo already uses for the
  reaper and the queue reconcile).
* **NOT observed under this trigger:** (1) divergence created AND repaired
  between two sweeps; (2) divergence introduced by a direct cache write between
  sweeps, which is observed at the NEXT sweep rather than as it happens; (3)
  RELATIONS — the comparison is over node keys, because ``reconcile_views`` is
  (relation identity is a separate decision with its own contract, §Interface E);
  (4) **nodes under a label OUTSIDE the record placement** — the cache side is
  read over ``NODE_TABLES`` (the labels the projection writes), so a second
  writer's node under a label of its own (or under the cache's ``:Meta``
  bookkeeping label, which the residency declaration legitimately uses) is not
  an ``extra_in_cache``. ``extra_in_cache`` is the signature for keys the
  record PLACES, not a whole-graph census, and this trigger deliberately does
  not count or refuse unknown labels: refusing would make every cache
  unreadable (``:Meta`` is always there) and counting would report the
  declaration as a second writer.

WHERE THE NUMBER LANDS
======================
Three destinations, and a number nobody reads is not one of them:

1. **The structured log line** — :func:`log_result` emits
   :meth:`ReconciliationReport.render` through the ``tortoise.record_reconcile``
   logger, at a severity that follows the VERDICT: ``ERROR`` when the comparison
   could not be made (``unanswerable`` — a run that produced no verdict must not
   look like a quiet one), ``WARNING`` when anything is divergent, and ``INFO``
   only for ``clean``. A scheduled run's log is where an operator already looks,
   so the severity is part of the contract, not a formatting choice.
2. **The process EXIT CODE** — ``0`` clean · ``3`` divergence observed · ``2``
   the comparison could not be made. A scheduler alerts on non-zero, and the
   three verdicts are distinct so "cannot answer" never reads as "agreement"
   (§17.2, the #1382 class).
3. **stdout, with ``--json``** — the report's own machine-readable shape
   (``ReconciliationReport.as_dict``) so a dashboard or a cron wrapper can
   graph it instead of grepping a log.

⛔ **What this module is NOT.** It does not remediate (a divergence is observed,
not repaired — §17.2 is an observability requirement, and a repair that runs
before anyone sees the count is the failure mode it exists to prevent), it does
not re-decide residency (§17.3's rule is applied here by calling it, not by
restating it), and it adds no MCP tool and no public ``TortoiseSDK`` method.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .cache_projection import (
    KEY_PROPERTY,
    NODE_TABLES,
    RecordView,
    declared_key,
)
from .reconciliation import (
    SAMPLE_LIMIT,
    ReconciliationReport,
    ReconciliationUnavailable,
    reconcile_views,
)
from .residency import ResidencyDeclaration

__all__ = [
    "CACHE_READ_TIMEOUT_MS",
    "EXIT_CLEAN",
    "EXIT_DIVERGENT",
    "EXIT_UNAVAILABLE",
    "LOG_NAME",
    "RECORD_CONNECT_TIMEOUT_S",
    "RECORD_STATEMENT_TIMEOUT_MS",
    "VERDICT_CLEAN",
    "VERDICT_DIVERGENT",
    "VERDICT_UNANSWERABLE",
    "RecordReconcileResult",
    "exit_code_for",
    "log_result",
    "read_cache_view",
    "reconcile_record_and_cache",
    "record_key_map",
]

#: The trigger's log channel. A separate name from the comparison module so a
#: deployment can route "the observer ran and found nothing" apart from
#: everything else ``tortoise`` logs.
LOG_NAME = "tortoise.record_reconcile"

#: The three verdicts. Deliberately three: ``UNANSWERABLE`` is what keeps an
#: empty or unreadable comparison from being reported as agreement.
VERDICT_CLEAN = "clean"
VERDICT_DIVERGENT = "divergent"
VERDICT_UNANSWERABLE = "unanswerable"

#: Process exit codes. ``2`` is the tree's "a surface could not be queried"
#: convention (``tools/collision_preflight.py``; the #3061 gate's INCOMPLETE),
#: and ``3`` is a real answer rather than an error.
EXIT_CLEAN = 0
EXIT_UNAVAILABLE = 2
EXIT_DIVERGENT = 3

#: The severity each verdict is logged at. Three verdicts, three levels: a
#: run that could not answer must not be quieter than a clean one.
_LOG_LEVEL = {
    VERDICT_CLEAN: logging.INFO,
    VERDICT_DIVERGENT: logging.WARNING,
    VERDICT_UNANSWERABLE: logging.ERROR,
}

#: How long a single cache read may block — the DEFAULT, overridable per run
#: with the CLI's ``--timeout-ms``. The check annotates an operational sweep; a
#: wedged store must not stall it (the bound ``residency.read_declaration``
#: already applies to the sibling read). A read that hits this refuses (exit
#: ``2``) — it never returns a partial one.
CACHE_READ_TIMEOUT_MS = 2000

#: The RECORD side's two bounds, so a wedged Postgres cannot hang a scheduled
#: sweep either (the cache read above is bounded, and a check that blocks
#: forever on the other side is the same outage with half the fix).
#: ``CONNECT_TIMEOUT_S`` bounds the handshake; ``STATEMENT_TIMEOUT_MS`` is set
#: on the session (``-c statement_timeout=…``) so the server aborts a wedged
#: query rather than the client waiting on it.
RECORD_CONNECT_TIMEOUT_S = 5
RECORD_STATEMENT_TIMEOUT_MS = 5000


# ── the two readers ────────────────────────────────────────────────────────


def record_key_map(view: RecordView, *, scope: str = "") -> dict[str, dict]:
    """The RECORD side: ``{declared key: {property: value}}``.

    Keys are :func:`tortoise.cache_projection.declared_key` — ``label:key``,
    scope-prefixed when one graph holds several scopes — because the label is
    part of the coordinate (``Object`` and ``Subject`` both key on ``name``).
    The values are the node's REAL property maps: a reader that returned ``{}``
    per key makes ``field_mismatches`` STRUCTURALLY zero (``reconcile_views``
    compares two per-key mappings field by field, and two empty mappings always
    agree), so the count would prove key-set agreement only — the #8609 review's
    finding 3, guarded by :func:`reconcile_record_and_cache`.
    """
    return {
        declared_key(node.label, node.key, scope=scope): dict(node.props)
        for node in view.nodes
    }


def read_cache_view(g: Any, *, labels: Iterable[str] | None = None,
                    scope: str = "", timeout_ms: int = CACHE_READ_TIMEOUT_MS
                    ) -> dict[str, dict]:
    """The CACHE side: the graph's own property maps, keyed the same way.

    ``labels`` defaults to every label the record places (``NODE_TABLES``). A
    label outside that set is refused rather than interpolated: the label is
    part of a Cypher statement, and the only labels this check may read are the
    ones the projection writes.

    ⚠️ **The read is never TRUNCATED — and it is BOUNDED.** A ``LIMIT`` would
    need truncation detection of its own to stay honest, and a silently
    truncated read UNDERCOUNTS ``extra_in_cache`` — the one number a second
    writer shows up in. What bounds the sweep is a wall-clock ``timeout_ms``
    (the CLI's ``--timeout-ms``, default :data:`CACHE_READ_TIMEOUT_MS`), which
    either returns the whole answer or REFUSES: it can never return a partial
    one, so bounding the cost cannot bias the count.

    ⚠️ **The cache side is the labels the RECORD places** (``NODE_TABLES``). A
    node under any other label is outside this comparison — see the module
    docstring's "NOT observed under this trigger" item (4); ``extra_in_cache``
    counts a second writer's nodes at coordinates the record OWNS, not the
    graph's whole population.

    ⛔ **Every failure is a REFUSAL.** A query that raises, a row that is not a
    property mapping, a node with no contract key, and **two nodes claiming one
    coordinate** all raise :class:`ReconciliationUnavailable`. Silence in any of
    those cases would read as *"the cache agrees with the record"* while the
    comparison had in fact not happened — and a node the reader skipped is
    exactly the ``extra_in_cache`` a second writer produces (the #1382 class
    §17.2 puts first).

    ⛔ **Duplicates are refused, not collapsed.** The writer ``MERGE``s on the
    key and the graph declares no uniqueness constraint, so a second writer's
    ``CREATE`` can leave TWO nodes at one ``(label, key)``. Collapsing them
    (last wins) leaves the two key SETS equal, so the run would report
    ``extra_in_cache == 0`` over a cache holding a node the record does not —
    the same undercount the key-less branch refuses, one step weaker. The
    comparison is therefore UNKNOWN, not clean.
    """
    known = frozenset(NODE_TABLES)
    wanted = tuple(NODE_TABLES) if labels is None else tuple(labels)
    unknown = sorted(set(wanted) - known)
    if unknown:
        raise ValueError(
            f"labels {unknown} are not record node tables — this check may only "
            f"read the labels the projection writes ({sorted(known)})")

    out: dict[str, dict] = {}
    for label in wanted:
        try:
            rows = g.query(
                f"MATCH (n:{label}) RETURN properties(n)",
                timeout=int(timeout_ms),
            ).result_set
        except Exception as e:
            # The driver's own message can carry the connection string (the
            # cache DSN holds a password), so only the TYPE is reported — the
            # same secret-hygiene rule the verb applies to the record side.
            raise ReconciliationUnavailable(
                f"cache is unreadable at label {label!r} "
                f"({type(e).__name__}) — divergence is UNKNOWN, not zero"
            ) from e
        key_property = KEY_PROPERTY[label]
        for row in rows:
            props = row[0] if row else None
            if not isinstance(props, Mapping):
                raise ReconciliationUnavailable(
                    f"cache returned a non-mapping property set at label "
                    f"{label!r} ({type(props).__name__}) — divergence is "
                    f"UNKNOWN, not zero")
            props = dict(props)
            key = props.get(key_property)
            if key is None or key == "":
                raise ReconciliationUnavailable(
                    f"a cache {label!r} node carries no contract key "
                    f"{key_property!r} — it cannot be addressed, and skipping "
                    f"it would undercount `extra_in_cache` (a second writer)")
            declared = declared_key(label, str(key), scope=scope)
            if declared in out:
                raise ReconciliationUnavailable(
                    f"the cache holds more than one {label!r} node at "
                    f"{declared!r} — collapsing them would leave the key sets "
                    f"equal and report `extra_in_cache=0` over a node the "
                    f"record does not have; divergence is UNKNOWN, not zero")
            out[declared] = props
    return out


# ── the one composition ────────────────────────────────────────────────────


@dataclass(frozen=True)
class RecordReconcileResult:
    """One trigger run: the comparison, and the keys it declined to judge.

    ``not_hot`` is separate from every count in ``report`` on purpose. A
    ``partial`` declaration says the cache holds a DECLARED SUBSET, so a record
    key the declaration does not enumerate is a **residency miss** — §17.3's
    ``not-hot``, repaired by fetching from the record — and NOT a projection
    lag. Counting it as ``missing_in_cache`` would conflate the two events
    §17.3 exists to separate, and would make a correctly-bounded projection
    report permanent divergence.
    """

    report: ReconciliationReport
    declaration: ResidencyDeclaration
    not_hot: int = 0

    @property
    def checked(self) -> int:
        return self.report.checked

    @property
    def divergent(self) -> int:
        return self.report.divergent

    def verdict(self) -> str:
        """``clean`` | ``divergent`` | ``unanswerable`` — never a bare boolean.

        ``unanswerable`` when NOTHING was compared (``checked == 0``): an empty
        record and an empty cache agree perfectly, which is the vacuous
        agreement this whole surface exists to refuse. A run that compared
        nothing is not a clean bill of health.
        """
        if self.divergent > 0:
            return VERDICT_DIVERGENT
        if self.checked == 0:
            return VERDICT_UNANSWERABLE
        return VERDICT_CLEAN

    def as_dict(self) -> dict:
        payload = self.report.as_dict()
        payload.update({
            "verdict": self.verdict(),
            "not_hot": self.not_hot,
            "declaration_coverage": self.declaration.coverage,
            "declaration_source": self.declaration.source,
        })
        return payload


def reconcile_record_and_cache(
        record: Mapping[str, Mapping],
        cache: Mapping[str, Mapping], *,
        declaration: ResidencyDeclaration | None = None,
        sample_limit: int = SAMPLE_LIMIT) -> RecordReconcileResult:
    """Compare the two keyed views, PARTITIONING divergence from residency.

    ``record``/``cache`` are ``{declared key: {property: value}}`` — already
    read, so this function is pure and cannot make a second connection to
    either store. ``declaration`` is the known-hot set (``residency``); ``None``
    means "not read", which is treated as COVERAGE-COMPLETE — the §2.0 default
    where the cache is the whole graph — so an absent key really is divergence.

    ⛔ **A vacuous comparison REFUSES.** If either side is non-empty while every
    one of its property maps is empty, the field comparison would be
    structurally zero (the #8609 finding-3 defect) and the count would prove
    key-set agreement only. Raising is the fail-closed polarity: a comparison
    that cannot see values must not report agreement.
    """
    truth = dict(record)
    cached = dict(cache)

    _refuse_vacuous(truth, "record")
    _refuse_vacuous(cached, "cache")

    not_hot = 0
    if declaration is not None and declaration.partial:
        for key in [k for k in truth if k not in cached]:
            if not declaration.covers(key):
                # §17.3: the store never claimed this key. A residency miss,
                # not a projection lag — excluded from the comparison and
                # counted apart, never folded into `missing_in_cache`.
                del truth[key]
                not_hot += 1

    report = reconcile_views(truth, cached, sample_limit=sample_limit)
    return RecordReconcileResult(
        report=report,
        declaration=declaration if declaration is not None
        else ResidencyDeclaration(source=None),
        not_hot=not_hot,
    )


def _refuse_vacuous(view: Mapping[str, Mapping], side: str) -> None:
    if view and not any(view.values()):
        raise ReconciliationUnavailable(
            f"{side} returned EMPTY property maps for all {len(view)} keys — the "
            f"field comparison would be structurally zero, so the count would "
            f"prove key-set agreement only (the #8609 finding-3 defect); "
            f"divergence is UNKNOWN, not zero")


def log_result(result: RecordReconcileResult,
               logger: logging.Logger | None = None) -> str:
    """Land the number: one structured log line, severity following the VERDICT.

    ``ERROR`` for ``unanswerable`` (nothing was compared — a run that produced
    no verdict must not read as a quiet, healthy one), ``WARNING`` for
    ``divergent``, ``INFO`` only for ``clean``. The three verdicts get three
    levels because a scheduler's log is read at a glance, and the module's own
    thesis applies to severity too: a signal nobody can distinguish from
    silence is not observability.

    Returns the rendered line so a caller (the CLI) prints exactly what it
    logged — one spelling of the summary, not two.
    """
    line = result.report.render()
    if result.not_hot:
        line += f" [not_hot={result.not_hot} — residency, not divergence]"
    verdict = result.verdict()
    line = f"{line} verdict={verdict}"
    (logger or logging.getLogger(LOG_NAME)).log(
        _LOG_LEVEL[verdict], "%s", line)
    return line


def exit_code_for(result: RecordReconcileResult) -> int:
    """``0`` clean · ``3`` divergence observed · ``2`` the comparison could not be made."""
    verdict = result.verdict()
    if verdict == VERDICT_DIVERGENT:
        return EXIT_DIVERGENT
    if verdict == VERDICT_CLEAN:
        return EXIT_CLEAN
    return EXIT_UNAVAILABLE

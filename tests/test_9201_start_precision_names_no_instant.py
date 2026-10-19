"""#9201 — a start that NAMES NO INSTANT claims no precision.

The #6140 owner ruling (2026-10-11 — *"A and C (c should be a warning)"*) settled
that a stored ``validFrom`` which names no instant (``""``, ``"TBD"``,
``"not-a-date"``) means **no start was recorded**: the window is OPEN BELOW. The
precision axis was left alone by that change, so the pre-ruling ``is not None``
gate kept reporting ``valid_from_precision = "day"`` on all three read surfaces —
a day-granularity claim about a datum the ruling says does not exist. Measured at
``origin/main`` (``b77d2408``):

    validFrom=""            -> {'valid_from_precision': 'day', 'end_state': 'open'}
    validFrom="TBD"         -> {'valid_from_precision': 'day', 'end_state': 'open'}
    validFrom="not-a-date"  -> {'valid_from_precision': 'day', 'end_state': 'open'}

on ``get_historical_knowledge``, assembly ``window_rows`` and the search wire
alike (and on the embedded fallback decoration).

⚠️ LANDING ORDER — this file pins the PRECISION half of the ruling only. The
COVERAGE half (``sdk._covers`` also requiring orderability, so such a start reads
as OPEN BELOW instead of non-covering) is NOT on ``main`` yet; it arrives with PR
#9205. So the coverage verdict for these shapes is deliberately NOT asserted here
— an assertion of today's ``absent`` would red the day #9205 lands, and an
``xfail`` pinning it would red the same day from the other side.

⛔ SCOPE — the START axis only. The END axis is deliberately untouched:
whether a present-but-unorderable ``validTo`` should report ``END_UNKNOWN`` is
#9202 and awaits its own owner ruling. ``test_the_unparseable_end_is_untouched``
pins that this change did not silently decide it.

Runnable with:
  TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \
      tests/test_9201_start_precision_names_no_instant.py -q
"""
from __future__ import annotations

import datetime

import pytest

from tortoise.sdk import TortoiseSDK

A_DATE = "2024-06-01"

#: Every "names no instant" shape the write boundary accepts and stores
#: verbatim. ``"TBD"`` is the one the issue measured; ``""`` is the shape
#: #2350's suite uses; ``"not-a-date"`` is #5360's.
UNPARSEABLE_STARTS = ["", "TBD", "not-a-date"]


@pytest.fixture
def lane(tmp_path):
    """(sdk, graph) on the embedded lane — the three surfaces need no server."""
    sdk = TortoiseSDK(str(tmp_path / "t.db"),
                      event_log_path=str(tmp_path / "events" / "e.jsonl"))
    yield sdk, sdk._get_proj().g
    sdk.close()


def _stored_start(graph, pid: str):
    """The RAW stored bound — non-vacuous: an unparseable start is PERSISTED."""
    row = graph.query("MATCH (n:Point {id:$id}) RETURN n.validFrom",
                      params={"id": pid}).result_set
    assert row, f"point {pid} missing"
    return row[0][0]


def _surfaces(sdk, graph, pid: str) -> dict[str, dict]:
    """The four read paths that carry the precision block (#2350's contract)."""
    from tortoise.assembly import docker_walker_port

    today = datetime.date.today()
    gk = sdk.get_historical_knowledge(pid, A_DATE)["chain"][0]
    row = next(r for r in docker_walker_port(sdk).window_rows(today, today)
               if r.get("id") == pid)
    hits = sdk.tortoise_fts_query(query=None, kind="statement",
                                  entity_type="point", limit=50,
                                  include_terminal=True)
    wire = next(r for r in hits if r["id"] == pid)
    return {"get_historical_knowledge": gk, "assembly": row, "search_wire": wire}


# ── the one-home resolver (pure) ─────────────────────────────────────────


def test_start_precision_table():
    """The START's gate is "does it NAME AN INSTANT", not presence alone."""
    from tortoise.validity import start_precision

    # a start that names no instant claims NO precision
    assert start_precision("", None) is None
    assert start_precision("TBD", None) is None
    assert start_precision("not-a-date", None) is None
    # ... and an absent start claims none either
    assert start_precision(None, None) is None
    # an orderable start is the legacy ``day``
    assert start_precision(A_DATE, None) == "day"
    assert start_precision("2024-06-01T10:00:00Z", None) == "day"
    # epoch 0 NAMES A REAL INSTANT (1948-…), so it stays a day and does not
    # collapse into the None bucket with the unparseable shapes above.
    assert start_precision(0, None) == "day"
    assert start_precision(0.0, None) == "day"
    # a DECLARED token wins over a start that names an instant ...
    assert start_precision(A_DATE, "year") == "year"
    # ... but NOT over one that does not: a precision describes its bound, and
    # ``validate_validity_precision`` only refuses a declaration when the bound
    # is ABSENT — so ``validFrom=""`` + ``validFromPrecision="year"`` is
    # reachable through the public write API. Reporting ``year`` there would
    # republish the very claim this issue removes.
    assert start_precision("", "year") is None
    assert start_precision("TBD", "year") is None
    assert start_precision(None, "year") is None


def test_window_present_table():
    """The emission gate's one home: "does this point carry a window at all".

    Value-truthiness cannot answer it — ``""`` is a PRESENT bound and an
    unparseable one claims no precision — which is exactly how the block got
    dropped on one surface only (#2350's drift, re-opened by #9201).
    """
    from tortoise.validity import window_present

    assert window_present(None, None, None, None) is False
    assert window_present("", None, None, None) is True
    assert window_present("TBD", None, None, None) is True
    assert window_present(0, None, None, None) is True
    assert window_present(None, "2024-06-01", None, None) is True
    # a declared precision with its bound cleared is refused at the write
    # boundary, but a pre-validator graph can still hold the shape — it is
    # window information, so the gate is true and the reader sees the block.
    assert window_present(None, None, None, "unknown") is True


# ── all three read surfaces agree (the issue's measurement) ───────────────


@pytest.mark.parametrize("raw", UNPARSEABLE_STARTS)
def test_an_unparseable_start_claims_no_precision_on_any_read_path(lane, raw):
    """RED at ``origin/main``: every surface said ``day`` for these shapes."""
    sdk, graph = lane
    pid = sdk.create_point("statement", f"start={raw!r}", validFrom=raw)["id"]
    # non-vacuous: the value is STORED, not refused and not normalized away.
    assert _stored_start(graph, pid) == raw

    for surface, entry in _surfaces(sdk, graph, pid).items():
        claim = entry.get("valid_from_precision")
        assert claim in (None, ""), (
            f"{surface} still claims valid_from_precision={claim!r} for a start "
            f"that names no instant ({raw!r}); the window is OPEN BELOW per the "
            f"#6140 ruling, so there is no start to be day-precise about")
        # the window block itself must survive: an un-datable start is not an
        # undated point, and the still-true end state is what #2350 exists for.
        assert entry.get("end_state") == "open", (surface, entry)


def test_the_embedded_fallback_decoration_agrees_on_an_unparseable_start(lane):
    """The fourth surface (#2350 review P2) must not drift to ``day`` either."""
    from tortoise.sdk import _decorate_fallback_hits

    sdk, graph = lane
    pid = sdk.create_point("statement", "fallback", validFrom="TBD")["id"]
    hit = _decorate_fallback_hits([{"id": pid}], graph)[0]
    assert hit.get("valid_from_precision") in (None, ""), hit
    assert hit["end_state"] == "open", hit


def test_epoch_zero_start_keeps_its_day_precision(lane):
    """The discriminating neighbour: ``0`` names a real instant (1948-…)."""
    sdk, graph = lane
    pid = sdk.create_point("statement", "epoch", validFrom=0)["id"]
    for surface, entry in _surfaces(sdk, graph, pid).items():
        assert entry.get("valid_from_precision") == "day", (surface, entry)


def test_a_dated_start_is_unchanged(lane):
    """The regression half — the fix must not touch an orderable start."""
    sdk, graph = lane
    pid = sdk.create_point("statement", "dated", validFrom=A_DATE)["id"]
    for surface, entry in _surfaces(sdk, graph, pid).items():
        assert entry.get("valid_from_precision") == "day", (surface, entry)
        assert entry.get("end_state") == "open", (surface, entry)
    # the emission fact is INTERNAL: it must never reach the public wire, or a
    # future ``asdict``-style serializer would publish a dataclass field nobody
    # declared (the surface manifest would then be wrong without going red).
    hit = _surfaces(sdk, graph, pid)["search_wire"]
    assert "has_window" not in hit, sorted(hit)


def test_a_declared_start_precision_wins_on_an_orderable_start(lane):
    """An explicit assertion is authoritative FOR A START THAT NAMES AN INSTANT."""
    sdk, graph = lane
    pid = sdk.create_point("statement", "declared", validFrom=A_DATE,
                           validFromPrecision="year")["id"]
    for surface, entry in _surfaces(sdk, graph, pid).items():
        assert entry.get("valid_from_precision") == "year", (surface, entry)


def test_a_declared_precision_cannot_resurrect_an_unparseable_start(lane):
    """#9201 residual: the write boundary only refuses a declaration whose bound
    is ABSENT, so a caller can declare a granularity for a start that names no
    instant. The read must still publish no precision claim for it."""
    sdk, graph = lane
    pid = sdk.create_point("statement", "declared over nothing", validFrom="TBD",
                           validFromPrecision="year")["id"]
    assert _stored_start(graph, pid) == "TBD"
    for surface, entry in _surfaces(sdk, graph, pid).items():
        assert entry.get("valid_from_precision") is None, (surface, entry)
        assert entry.get("end_state") == "open", (surface, entry)


# ── the END axis is NOT this change's (pins #9202 as untouched) ───────────


def test_the_unparseable_end_is_untouched():
    """#9202's question — a present-but-unorderable ``validTo`` — is NOT
    decided here. Its current reading is pinned so a later reader cannot infer
    that #9201's ruling-derived START change settled the END as well."""
    from tortoise.validity import window_read_fields

    out = window_read_fields("", "TBD", None, None)
    assert out["valid_to_precision"] == "day", out
    assert out["end_state"] == "known", out
    # and the start side, in the same call, is the #9201 answer
    assert out["valid_from_precision"] is None, out

    # the undated window keeps its pre-#2350 reading
    undated = window_read_fields(None, None, None, None)
    assert undated == {"valid_from_precision": None,
                       "valid_to_precision": None, "end_state": "open"}

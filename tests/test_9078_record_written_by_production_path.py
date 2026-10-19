"""The record is WRITTEN BY PRODUCTION — the call site, not a probe (#9188 / #9078).

THE DEFECT THIS PINS. At `221673135` (and at the #9078 head it was measured on),
`tortoise/record_projection.py` was complete, proven, and **called by nothing in
production**: its only importer was `supabase/tests/pglite/validate.mjs`. So the
epic's acceptance sentence — *"a user's Points, operators, edges, Objects,
Subjects and the event stream exist in Postgres"* — was a claim about a test
fixture. No unit test could see that, because every unit test drove the fold
itself; the missing thing was a **caller**.

So the proof here is deliberately not "the fold can write". It is:

1. **There is a production caller**, and it is inside the shipped package
   (`tortoise/sdk.py`), which is what `tools/record_wiring_check.py` asserts —
   a `tests/**` or `docs/evidence/**` caller cannot satisfy it, because
   production is *the package that is installed*, not a path allowlist a probe
   can join. (Indicator 3 of #9188: "no `docs/evidence/**` or `tests/**` caller
   is what satisfies the gate".)
2. **The SDK's own public capture path writes rows into all four ontology
   layers** — `event` · `object`/`subject` · `point`/`relation` · procedural —
   with no probe module and no monkeypatched writer. This leg needs a Postgres
   carrying the record migrations (`TEST_RECORD_DB_URI`, the same pattern
   `tests/test_audit_events.py` uses for `TEST_AUDIT_DB_URI`); the schema is
   applied from `supabase/migrations/` on first use, so no manual setup.
3. **An armed write is never a guess**: no tenant ⇒ refused and counted, no
   DSN ⇒ no connection at all (byte-identical capture for an unarmed
   deployment), and the live path never invents a `journal_event` position it
   cannot know (#9190).
4. **A loss is OBSERVABLE.** Two record shapes still cannot be projected because
   the SCHEMA's column type disagrees with what the producer emits (measured
   below). They are pinned by EQUALITY, so this file goes red the moment they
   are fixed and the named-away pair must be re-measured — a permanent
   exclusion could otherwise go stale in silence, which is the class of defect
   this epic exists to remove.

WHAT IS NOT HERE, NAMED SO THE ABSENCE IS VISIBLE.

* The reconciler's production trigger (#9189), the `journal_event.seq` ordinal
  (#9190) and the reconciler's own dirty-run behaviour are other lanes.
* ⚠️ **No CI job arms `TEST_RECORD_DB_URI`** (checked: `.github/` and `config/`
  never set it), so the two Postgres legs skip in CI and the DB-FREE wiring gate
  (`tools/record_wiring_check.py`) is what guards this seam on the PR path. The
  Postgres legs are the operator-run acceptance command from #9078
  ("a throwaway Postgres with only the RECORD migrations"), not a CI check.
  Wiring them into a CI service container is its own change.
* The fold defect that keeps captured Points out of the record today is pinned
  CONDITIONALLY (see `_fold_refuses_captured_points`), so this file neither
  hides it nor reddens the day it is fixed.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tortoise import monitoring, record_projection, record_writer  # noqa: E402
from tortoise.record_writer import use_record_tenant  # noqa: E402
from tortoise.sdk import TortoiseSDK  # noqa: E402

#: The env var a lane points at a throwaway Postgres with the record migrations
#: (the `TEST_AUDIT_DB_URI` convention). Unset ⇒ the DB legs SKIP, never pass.
RECORD_DB_ENV = "TEST_RECORD_DB_URI"

ORG, GRAPH = "org-9188-production-path", "graph-9188-production-path"

#: Journal keys whose emitted VALUE the record's declared column type cannot
#: hold — a schema/producer DISAGREEMENT, not a folding bug (`_jsonb_or_scalar`
#: deliberately refuses to coerce, so the disagreement stays visible instead of
#: being papered over). Derived from the RECORD ITSELF, never hard-coded by
#: record type: the moment the schema or the producer is fixed, the record stops
#: matching and the expectation flips to "must project cleanly" on its own — so
#: nothing here can go stale in silence.
#:
#:   * ``search_keys`` — an EXTRACTED point carries the graph's flattened string
#:     (`_flatten_search_keys_prop`, #8006/#5482), while
#:     `public.point.search_keys` is `jsonb`: ``invalid input syntax for type
#:     json``. Two record types re-project the same snapshot
#:     (``PointAdded``/``PointPromoted``), so the TYPE is not the unit.
#:   * ``capture_redactions`` — the capture receipt's integer COUNT, while
#:     `public.session.capture_redactions` is `jsonb`: ``column
#:     "capture_redactions" is of type jsonb but expression is of type integer``.
#:
#: Measured at #9200's head (`62ec432a8`) + this diff, against a real Postgres.
#: Reported on #9200 (the producer-accuracy class it belongs to): the fix is a
#: schema/placement decision, not a wiring one.
_SCHEMA_DISAGREEMENTS = {
    "search_keys": "invalid input syntax for type json",
    "capture_redactions": "capture_redactions",
}


def _disagreement(record: dict) -> str | None:
    """Which declared-column disagreement this journal record carries, if any."""
    redactions = record.get("capture_redactions")
    if redactions is not None and not isinstance(redactions, (dict, list)):
        return "capture_redactions"
    snapshot = record.get("point") or {}
    keys = snapshot.get("search_keys")
    if isinstance(keys, str) and keys.strip():
        return "search_keys"
    return None


#: The record migrations, read from the fold's own registry (never restated).
RECORD_MIGRATION_FILES = [REPO_ROOT / "supabase" / "migrations" / name
                          for name in record_projection.RECORD_MIGRATIONS]

#: The minimum Supabase-shaped substrate the record migrations need: the three
#: roles they GRANT to, and the two control-plane tables their tenancy FKs
#: point at (`public.organizations`, `public.graphs`). Deliberately minimal —
#: the record is the subject here, not the control plane.
_PREAMBLE = """
DO $$ BEGIN
  CREATE ROLE anon NOLOGIN;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  CREATE ROLE authenticated NOLOGIN;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  CREATE ROLE service_role NOLOGIN BYPASSRLS;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
CREATE TABLE IF NOT EXISTS public.organizations (
    id text PRIMARY KEY,
    name text NOT NULL,
    graph_name text
);
CREATE TABLE IF NOT EXISTS public.graphs (
    id text PRIMARY KEY,
    org_id text NOT NULL REFERENCES public.organizations(id),
    name text NOT NULL,
    namespace text,
    UNIQUE (org_id, id)
);
"""


@pytest.fixture(scope="session")
def wiring():
    """The wiring gate, loaded by path (it is a tool, not an installed module).

    Session-scoped: `check()` imports the SDK (the fold's registry lives behind
    `tortoise/__init__.py`) and the tool memoises it, so the import is paid once.
    """
    path = REPO_ROOT / "tools" / "record_wiring_check.py"
    spec = importlib.util.spec_from_file_location("record_wiring_check", path)
    assert spec and spec.loader, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    code, report = module.check()
    return module, code, report


@pytest.fixture(autouse=True)
def _no_ambient_record_env(monkeypatch):
    """Hermetic against the record's OWN arming surfaces (#9231 review P2).

    A developer lane configured exactly the way this PR's single-tenant
    instructions say — ``TORTOISE_RECORD_DSN`` + the ``_ORG_ID``/``_GRAPH_ID``
    pair — would otherwise arm and attribute every capture here, so the
    ``no_tenant`` refusal test would connect, count ``write_failed`` and fail
    with a message ("the tenant was guessed") that misdiagnoses an
    env-configured machine as a code defect. The sibling
    ``tests/test_record_writer_seam.py`` clears the DSN for the same reason;
    this clears all THREE, because this PR is what introduced the pair.
    """
    for name in (record_writer.RECORD_DSN_ENV, record_writer.RECORD_ORG_ENV,
                 record_writer.RECORD_GRAPH_ENV):
        monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture()
def capture_sdk(tmp_path):
    """A real SDK with its own graph and journal. No record DSN is set here."""
    def _make(*, journal: bool = True):
        path = str(tmp_path / "journal.jsonl")
        sdk = TortoiseSDK(
            str(tmp_path / "graph.db"),
            event_log_path=path if journal else None,
        )
        return sdk, Path(path)
    return _make


@pytest.fixture()
def no_record_dsn(monkeypatch):
    monkeypatch.delenv(record_writer.RECORD_DSN_ENV, raising=False)
    return record_writer.RECORD_DSN_ENV


# ════════════════════════════════════════════════════════════════════════════
# 1. The call site — static, DB-free, and the one that would have caught #9078
# ════════════════════════════════════════════════════════════════════════════


def test_the_write_half_has_a_production_caller(wiring):
    """`tools/record_wiring_check.py` exits 0 only on the real tree.

    Before this change it exits 1 with "no module inside tortoise/ imports AND
    calls the record's write half" — the defect #9078 measured.
    """
    _, code, report = wiring
    assert code == 0, report
    assert report["verdict"] == "WIRED"
    modules = [caller["module"] for caller in report["production_callers"]]
    assert "tortoise/sdk.py" in modules, modules


def test_only_the_shipped_package_can_satisfy_the_wiring_gate(wiring):
    """The gate's caller set is EXACTLY the three shipped doors — no probe can join.

    Production is the installed package, not a path a probe could join, so the
    claim is that the caller list is the expected door set and nothing else.
    Asserting `module.startswith("tortoise/")` alone is true by construction:
    `find_production_callers` builds every path from `PACKAGE = REPO_ROOT/
    "tortoise"`, so it could never fail and proved nothing (#9231 review P2).
    """
    _, _, report = wiring
    modules = {caller["module"] for caller in report["production_callers"]}
    assert modules == {"tortoise/api.py", "tortoise/mining.py", "tortoise/sdk.py"}, (
        "the door set moved — a door was added or removed without this gate "
        "learning the new place: " + repr(sorted(modules))
    )


def test_every_layer_has_a_writer_in_the_one_registry(wiring):
    """The four layers are derived from RECORD_MUTATIONS, never a second list."""
    check, _, report = wiring
    writers = report["layer_writers"]
    for layer, tables in check.LAYERS.items():
        assert any(writers.get(table) for table in tables), (
            f"{layer} has no writer among {tables}: {sorted(writers)}"
        )


# ════════════════════════════════════════════════════════════════════════════
# 2. Unarmed is byte-identical — the deployment that has not armed the record
# ════════════════════════════════════════════════════════════════════════════


def test_an_unarmed_capture_opens_no_record_connection(capture_sdk, no_record_dsn,
                                                       monkeypatch):
    """No DSN ⇒ no connection, no plan, no exception: §2.0's sequence."""
    def _must_not_connect(dsn):                      # pragma: no cover - asserted
        raise AssertionError(f"the record is unarmed but connected to {dsn!r}")

    monkeypatch.setattr(record_writer, "_default_connect", _must_not_connect)
    sdk, _journal = capture_sdk()
    before = dict(monitoring.record_write_failure_counts())
    receipt = sdk.capture_session([
        {"role": "user", "content": "We chose FalkorDB for the hot cache."},
        {"role": "assistant", "content": "Noted."},
    ])
    assert receipt["ok"] is True
    # A DELTA, not process-global equality (#9231 review P2): the counter is a
    # module-level Prometheus Counter, so `== {}` would only hold while no other
    # file in the suite arms the record — an accident, not the claim.
    after = dict(monitoring.record_write_failure_counts())
    assert after == before, (before, after)


# ════════════════════════════════════════════════════════════════════════════
# 3. Armed but unattributed — refused, counted, never guessed (#9192)
# ════════════════════════════════════════════════════════════════════════════


def test_an_armed_capture_without_a_tenant_is_refused_and_counted(
        capture_sdk, monkeypatch):
    """Armed with no tenant ⇒ REFUSED (counted `no_tenant`), never filed blind.

    The DSN is syntactically valid and deliberately unreachable: a refusal that
    had gone looking for a tenant's rows would first TRY to connect, so "no
    connection attempted" is what proves the refusal happened before the guess.
    """
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV,
                       "postgresql://record:record@127.0.0.1:1/record")

    def _must_not_connect(dsn):                      # pragma: no cover - asserted
        raise AssertionError(
            "an armed write with no tenant in scope connected to the record — "
            "the tenant was guessed"
        )

    monkeypatch.setattr(record_writer, "_default_connect", _must_not_connect)
    sdk, _journal = capture_sdk()
    # A DELTA, not a process-global `>=` (#9231 review P2): the counter is a
    # module-level Prometheus Counter, so `>= 1` would pass on some OTHER
    # test's refusal — an accident, not this claim.
    before = dict(monitoring.record_write_failure_counts())
    receipt = sdk.capture_session([
        {"role": "user", "content": "We chose FalkorDB for the hot cache."},
        {"role": "assistant", "content": "Noted."},
    ])
    assert receipt["ok"] is True
    after = dict(monitoring.record_write_failure_counts())
    assert after.get("no_tenant", 0) > before.get("no_tenant", 0), (before, after)
    assert after.get("write_failed", 0) == before.get("write_failed", 0), after


def test_the_single_tenant_environment_pair_resolves_a_tenant(capture_sdk,
                                                              monkeypatch):
    """The single-tenant arming surface (#9078 scope decision 3).

    Without it, arming the record needs a code change at every call site — which
    is how a capability ends up with no production caller again. A request scope
    always wins over the pair: env is process-global, so it is the same single
    value for every tenant the process serves (#9192).
    """
    monkeypatch.delenv(record_writer.RECORD_ORG_ENV, raising=False)
    monkeypatch.delenv(record_writer.RECORD_GRAPH_ENV, raising=False)
    assert record_writer.record_tenant() is None
    monkeypatch.setenv(record_writer.RECORD_ORG_ENV, "org-from-env")
    assert record_writer.record_tenant() is None, "a HALF pair must not be a tenant"
    monkeypatch.setenv(record_writer.RECORD_GRAPH_ENV, "graph-from-env")
    assert record_writer.record_tenant() == ("org-from-env", "graph-from-env")
    with use_record_tenant("org-scoped", "graph-scoped"):
        assert record_writer.record_tenant() == ("org-scoped", "graph-scoped")
    assert record_writer.record_tenant() == ("org-from-env", "graph-from-env")
    # ⛔ `current_record_tenant()` is the SCOPE only — its `None` is NOT the
    # refusal an armed write reads (#9231 review P2). It returned None while
    # `record_tenant()` resolved the env pair, so the docstring that called its
    # None "a REFUSAL for an armed write" was false: a caller asking it "would
    # my armed write be refused?" got the wrong answer while the write
    # proceeded under the env tenant. `record_tenant()` is the one whose None
    # means refusal.
    assert record_writer.current_record_tenant() is None
    assert record_writer.record_tenant() is not None


def test_env_example_declares_the_record_tenant_pair():
    """``.env.example`` IS this repo's env contract (``AGENTS.md``).

    #9231 review P2: the record block documents the DSN across 28 lines and
    named NOTHING about the tenant pair — the surface that decides whether an
    armed write is filed or refused. `git grep` for the pair over `.env.example`,
    `.github/`, `docs/` and `config/` returned nothing, so the only prose
    describing it was the `no_tenant` warning an operator reads AFTER the
    refusal. The PR's own argument for the pair is discoverability ("without it,
    arming the record would require a code change at every call site"), which
    the DSN-only block defeated. Same pin shape as
    ``test_bm25_lexical_8488.py::test_env_example_declares_the_bm25_arm_switch``.
    """
    env = (REPO_ROOT / ".env.example").read_text()
    for name in (record_writer.RECORD_ORG_ENV, record_writer.RECORD_GRAPH_ENV):
        assert name in env, (
            f"{name} (the #9078 single-tenant arming surface) decides whether an "
            "armed record write is filed or refused, and is not declared in "
            ".env.example")
        declared = [line for line in env.splitlines()
                    if line.strip() in (f"# {name}=", f"{name}=")]
        assert declared, (
            f"{name} must be shown as a commented assignment (the example form "
            "an operator copies), not just named in prose")
    # The seam's connect bound is named where the DSN is documented; the block
    # must not go back to claiming the DSN is handed over untouched.
    assert "CONNECT_TIMEOUT_SECONDS" in env, (
        ".env.example must name the seam's own connect bound — the DSN block "
        "otherwise reads as if libpq's wait-forever default still applied")
    assert "sets no\n# timeout of its own" not in env, (
        ".env.example claims the seam sets no timeout of its own; it sets "
        "record_writer.CONNECT_TIMEOUT_SECONDS")


def test_an_armed_capture_without_a_journal_is_counted_not_silent(
        capture_sdk, monkeypatch):
    """A journal-less deployment with an armed record is a NAMED misconfiguration.

    `tortoise/config.py` supports the journal-less lane by design, so an
    operator can arm the record and forget the journal — and then nothing is
    written and NOTHING could heal it later, because the record's sustainable
    source is the journal. Silent would be the #9078 hole wearing a green
    health check.
    """
    monkeypatch.setattr(record_writer, "_default_connect",
                        lambda dsn: pytest.fail("connected with no journal"))
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV,
                       "postgresql://record:record@127.0.0.1:1/record")
    sdk, _journal = capture_sdk(journal=False)
    before = dict(monitoring.record_write_failure_counts())
    receipt = sdk.capture_session([
        {"role": "user", "content": "We chose FalkorDB for the hot cache."},
        {"role": "assistant", "content": "Noted."},
    ])
    assert receipt["ok"] is True
    after = dict(monitoring.record_write_failure_counts())
    assert after.get("no_journal", 0) > before.get("no_journal", 0), after


# ════════════════════════════════════════════════════════════════════════════
# 4. The live path never invents a journal position (#9190)
# ════════════════════════════════════════════════════════════════════════════


def test_the_live_path_never_writes_a_journal_position(wiring):
    """The chokepoint passes `position=False`, and the tool says so statically.

    Two surfaces, because either alone is weak: the AST check (a live call that
    omits the argument fails the gate) and the seam itself (the flag actually
    removes the row from the plan).
    """
    _, _, report = wiring
    assert report["position_problems"] == [], report["position_problems"]
    assert record_writer.POSITION_ROW_DEFERRED_ON == "#9190"

    record = {"type": "PointAdded", "ts": "2026-10-19T00:00:00Z", "id": "p1",
              "point": {"id": "p1", "content": "a claim", "pointKind": "claim"}}
    with_position = record_projection.replay_plan([record], org_id="o", graph_id="g")
    without = record_projection.replay_plan(
        [record], org_id="o", graph_id="g", position=False)
    assert any("journal_event" in s.sql for s in with_position)
    assert not any("journal_event" in s.sql for s in without)
    # The layer rows are unaffected — the position row is bookkeeping, not a layer.
    assert [s for s in without if "journal_event" not in s.sql] == [
        s for s in with_position if "journal_event" not in s.sql
    ]


# ════════════════════════════════════════════════════════════════════════════
# 5. Only a JOURNAL feeds the record — a scratch buffer must never file a row
# ════════════════════════════════════════════════════════════════════════════


def test_the_seam_writes_nothing_from_a_scratch_buffer(monkeypatch):
    """`durable=False` ⇒ no connection, no row, one named count (#9231 review P1).

    The record is `replay(journal)`: a row whose record the journal never
    carried can be neither reproduced nor healed, so the seam must refuse it
    BEFORE opening a connection — that is what `_default_connect` raising
    proves. The count is the signal that a lane is filing unreproducible rows.
    """
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV,
                       "postgresql://record:record@127.0.0.1:1/record")
    monkeypatch.setattr(record_writer, "_default_connect", lambda dsn: pytest.fail(
        "a non-durable record opened a record connection"))
    before = dict(monitoring.record_write_failure_counts())
    with use_record_tenant(ORG, GRAPH):
        assert record_writer.write_live_record(
            {"type": "PointAdded", "id": "p1"}, durable=False) is None
    after = dict(monitoring.record_write_failure_counts())
    assert after.get("no_durable_journal", 0) > before.get("no_durable_journal", 0), after
    assert after.get("write_failed", 0) == before.get("write_failed", 0), after


def test_the_sdk_door_reads_durability_off_its_log(capture_sdk, monkeypatch):
    """A door READS `durable`; it must not assume every `EventLog` is a journal.

    `EventLog` now declares `durable = True` explicitly rather than relying on
    the doors' `getattr(log, "durable", True)` fallback, so a log constructed
    onto a SCRATCH path can say so — which is exactly what the MCP mining
    lane's temp-file journal does (`mcp_server`, `tempfile.mkdtemp`). Before
    this, `sdk.py` and `mining.py` passed no `durable` at all, so such a buffer
    filed rows whose journal was a directory nothing references, and no replay
    could ever heal them (#9231 review P1).
    """
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV,
                       "postgresql://record:record@127.0.0.1:1/record")
    monkeypatch.setattr(record_writer, "_default_connect", lambda dsn: pytest.fail(
        "the SDK door filed a record from a log that declared itself a "
        "scratch buffer"))
    sdk, _journal = capture_sdk()
    log = sdk._get_event_log()
    assert log is not None, "the fixture configures a journal"
    assert log.durable is True, "a real journal is durable by default"
    log.durable = False
    before = dict(monitoring.record_write_failure_counts())
    sdk.capture_session([{"role": "user", "content": "We chose FalkorDB."}])
    after = dict(monitoring.record_write_failure_counts())
    assert after.get("no_durable_journal", 0) > before.get(
        "no_durable_journal", 0), after
    assert after.get("write_failed", 0) == before.get("write_failed", 0), after


def test_the_m2_capture_lane_never_files_a_row_its_journal_never_carried(        capture_sdk, monkeypatch):
    """The M2 extraction lane appends to a SCRATCH buffer, so it never writes.

    `TortoiseSDK._extract_session_llm` builds `EventAPI(_InMemoryEventLog())`
    ("No file I/O, no temp files", by design) and the extractor emits through
    `EventAPI._emit`. Those records are journaled NOWHERE, so with the record
    armed they used to land as rows no replay could ever correct — and the
    in-capture dedup even DELETEs a repeated Point from the graph, leaving the
    row asserting a Point the graph does not hold. Pre-fix this test fails on
    the connect assertion AND on the missing `no_durable_journal` count.

    `TORTOISE_SESSION_LLM_MOCK=1` is the existing, keyless test seam for this
    lane, so the REAL `_extract_session_llm` runs (no stub of its own body).
    """
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV,
                       "postgresql://record:record@127.0.0.1:1/record")
    monkeypatch.setattr(record_writer, "_default_connect", lambda dsn: pytest.fail(
        "the M2 capture lane opened a record connection for a record its "
        "journal never carried"))
    sdk, _journal = capture_sdk()
    before = dict(monitoring.record_write_failure_counts())
    with use_record_tenant(ORG, GRAPH):
        extracted, meta = sdk._extract_session_llm(
            [{"role": "user", "content": "We chose FalkorDB for the hot cache."},
             {"role": "assistant",
              "content": "Noted: FalkorDB holds the hot cache."}],
            "sess-m2-scratch", "2026-10-19T00:00:00+00:00")
    assert extracted, "the M2 lane must really have emitted"
    assert meta.get("mode") != "empty", meta
    after = dict(monitoring.record_write_failure_counts())
    assert after.get("no_durable_journal", 0) > before.get(
        "no_durable_journal", 0), after
    assert after.get("write_failed", 0) == before.get("write_failed", 0), after


def test_a_real_journal_is_still_durable_for_the_door(monkeypatch):
    """The gate is the log's own answer: an `EventLog` keeps writing.

    Stated so the non-durable fix cannot be read as "the API door stopped
    writing": a log with no `durable` marker (every duck-typed double, and the
    SDK's `EventLog`) is treated as a journal.
    """
    from tortoise.api import EventAPI
    from tortoise.sdk import _InMemoryEventLog

    class _Log:
        """A duck-typed log with no `durable` marker (the real `EventLog`
        shape as the door sees it: the attribute is absent, not True)."""

        def __init__(self):
            self.events = []

        def append(self, event):
            self.events.append(event)

    assert _InMemoryEventLog.durable is False
    assert getattr(_Log(), "durable", True) is True
    seen = []
    monkeypatch.setattr(record_writer, "write_live_record",
                        lambda record, **kw: seen.append(kw.get("durable")))
    api = EventAPI(_Log(), initiated_by="extractor")
    api._emit("SourceCreated", id="s1")
    assert seen == [True], seen
    assert len(api.log.events) == 1, "the append still happened"


# ════════════════════════════════════════════════════════════════════════════
# 6. Resilience — the seam's "never raises" is ENFORCED, and bounded
# ════════════════════════════════════════════════════════════════════════════


def test_a_metrics_failure_cannot_escape_the_live_seam(monkeypatch):
    """A counting failure must not raise out of a committed write (#5148).

    `TortoiseSDK._record_write_failure` already suppressed this; the function it
    delegates to for every other reason did not, so a broken metrics registry
    turned a REPORTED loss into an exception thrown at `EventAPI._emit` / the
    miner AFTER the journal append committed. Pre-fix this test raises.
    """
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV,
                       "postgresql://record:record@127.0.0.1:1/record")

    def _boom(reason):
        raise RuntimeError("the metrics registry is broken")

    monkeypatch.setattr(monitoring, "record_record_write_failure", _boom)
    # No tenant in scope ⇒ the seam takes its counting path, which must swallow.
    assert record_writer.write_live_record({"type": "PointAdded"}) is None


def test_the_record_connection_is_bounded(monkeypatch):
    """The live door's connect is capped — libpq's default is "wait forever".

    One connection is opened per journal record, on the caller's thread, AFTER
    the append committed; an armed-but-unreachable record would otherwise stall
    every capture for as long as the kernel takes. A DSN that already names a
    `connect_timeout` keeps ITS number.
    """
    import types

    calls = []

    def _connect(dsn, **kwargs):
        calls.append((dsn, kwargs))
        return object()

    monkeypatch.setitem(sys.modules, "psycopg2",
                        types.SimpleNamespace(connect=_connect))
    record_writer._default_connect("postgresql://u:p@127.0.0.1:1/db")
    # Not `== record_writer.CONNECT_TIMEOUT_SECONDS`: that compares the
    # implementation's constant to itself and cannot fail (#9231 review P2).
    # The bound is the independent claim; the DSN-override below is the other.
    assert "connect_timeout" in calls[0][1], calls[0][1]
    assert isinstance(calls[0][1]["connect_timeout"], int), calls[0][1]
    assert 0 < calls[0][1]["connect_timeout"] <= 30, calls[0][1]
    record_writer._default_connect(
        "postgresql://u:p@127.0.0.1:1/db?connect_timeout=3")
    assert "connect_timeout" not in calls[1][1], (
        "an explicit connect_timeout on the DSN must win over the default")


def test_a_malformed_dsn_on_the_journal_less_lane_is_counted(
        capture_sdk, monkeypatch):
    """A typo'd DSN with no journal produced ZERO signal before this fix.

    On the journal-less lane the seam is never called (there is no append to
    hook), so `write_live_record`'s own `malformed_dsn` count could never fire:
    the bare `except Exception: return` swallowed the misconfiguration. The
    armed-but-malformed shape is the one an operator hits first when arming.
    """
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV, "mysql://not-postgres/nope")
    sdk, _journal = capture_sdk(journal=False)
    before = dict(monitoring.record_write_failure_counts())
    receipt = sdk.capture_session([
        {"role": "user", "content": "We chose FalkorDB for the hot cache."},
        {"role": "assistant", "content": "Noted."},
    ])
    assert receipt["ok"] is True
    after = dict(monitoring.record_write_failure_counts())
    assert after.get("malformed_dsn", 0) > before.get("malformed_dsn", 0), after


# ════════════════════════════════════════════════════════════════════════════
# 7. The four-layer proof — a real Postgres, the PUBLIC capture path
# ════════════════════════════════════════════════════════════════════════════


def _record_db():
    """A connection to a Postgres carrying the record migrations, or skip."""
    dsn = os.environ.get(RECORD_DB_ENV)
    if not dsn:
        pytest.skip(f"{RECORD_DB_ENV} not set — no Postgres for the four-layer proof")
    psycopg2 = pytest.importorskip("psycopg2",
                                   reason="psycopg2-binary is the record's driver")
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    return conn


def _ensure_record_schema(conn) -> None:
    """Apply the record migrations if the database does not carry them yet.

    Idempotent by inspection (`journal_event` present ⇒ done), so it works both
    on a throwaway container and on one a previous run already migrated.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.journal_event')")
        if cur.fetchone()[0] is not None:
            return
        # ONE statement: the preamble's role guards are `DO $$ … $$` blocks, and
        # splitting on ';' would shred them into unterminated dollar quotes.
        cur.execute(_PREAMBLE)
        for path in RECORD_MIGRATION_FILES:
            cur.execute(path.read_text(encoding="utf-8"))


def _seed_tenant(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.organizations (id, name) VALUES (%s, %s) "
            "ON CONFLICT (id) DO NOTHING", (ORG, ORG))
        cur.execute(
            "INSERT INTO public.graphs (id, org_id, name) VALUES (%s, %s, %s) "
            "ON CONFLICT (id) DO NOTHING", (GRAPH, ORG, GRAPH))
        for table in ("event", "object", "subject", "point", "relation",
                      "session", "source", "journal_event"):
            cur.execute(
                f"DELETE FROM public.{table} WHERE org_id=%s AND graph_id=%s",
                (ORG, GRAPH))


def _count(conn, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM public.{table} WHERE org_id=%s "
                    f"AND graph_id=%s", (ORG, GRAPH))
        return int(cur.fetchone()[0])


#: Fold refusals that are PRE-EXISTING defects of the projection — not of this
#: wiring. PR #9200 fixes them; until it lands, a live record can be REFUSED by
#: the fold before a single statement runs. They are tolerated ONLY while the
#: fold still refuses the live Point shape (see `_fold_refuses_captured_points`),
#: so the tolerance DELETE-ITSELF when #9200 lands: no stale pin, no red on main
#: the day it merges, and a NEW refusal (no fragment here) fails loudly.
#:
#:   * "no usable 'id' key for Point" — `_key_value` reads the envelope while the
#:     producer puts the id in `payload["point"]` (#9200 row 1);
#:   * "carries no `point` snapshot" — `EntityBindingRefused` is FLAT
#:     (`subject_binding._journal_refusal`) while `RECORD_MUTATIONS` declares a
#:     `point` snapshot for it; #9200 row 4 turns it into `NoRecordEffect`.
_ENGINE_REFUSAL_FRAGMENTS = {
    "no usable 'id' key for Point": "#9200 row 1 — the id rides the snapshot",
    "carries no `point` snapshot": "#9200 row 4 — EntityBindingRefused is flat",
}

#: The engine dependency the epistemic-layer claim cannot satisfy on its own.
_ENGINE_DEPENDENCY = (
    "the fold cannot project a captured Point yet — its id rides the snapshot "
    "and `record_projection._key_value` reads the envelope (PR #9200 fixes it; "
    "it also turns the flat EntityBindingRefused into NoRecordEffect). Measured "
    "at #9200's head + this diff: with those fixes applied the epistemic leg "
    "passes for real, so this marker self-disables (the condition is the "
    "MEASURED defect) and reddens nothing on the day #9200 merges."
)


def _fold_refuses_captured_points() -> bool:
    """Does the fold still refuse a Point whose id rides the snapshot?

    The exact shape `TortoiseSDK._emit_event` produces for `PointAdded` /
    `PointPromoted` (`event["point"] = clean`, no envelope `id`). Measured, not
    asserted from a date: DB-free and cheap.
    """
    live_shape = {"type": "PointAdded", "ts": "2026-10-19T00:00:00Z",
                  "point": {"id": "p1", "content": "a claim",
                            "pointKind": "claim"}}
    try:
        record_projection.mutations_for(
            live_shape, org_id="probe", graph_id="probe",
            columns=record_projection.table_columns(),
            refused=record_projection.refused_props_names())
    except record_projection.RecordProjectionError:
        return True
    return False


def _engine_refusal(message: str) -> str | None:
    """Is this refusal one of the PRE-EXISTING fold defects (#9200's class)?

    Empty unless the fold still refuses the live Point shape, so the tolerance
    disappears on its own when #9200 lands.
    """
    if not _fold_refuses_captured_points():
        return None
    return next((fragment for fragment in _ENGINE_REFUSAL_FRAGMENTS
                 if fragment in message), None)


def _journal_records(journal_path: Path) -> list[dict]:
    return [json.loads(line) for line in
            journal_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _capture(capture_sdk, monkeypatch) -> tuple[object, Path, int]:
    """Seed, arm, capture through the PUBLIC API, return (sdk, journal, losses)."""
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV, os.environ[RECORD_DB_ENV])
    sdk, journal_path = capture_sdk()
    before = dict(monitoring.record_write_failure_counts())
    with use_record_tenant(ORG, GRAPH):
        receipt = sdk.capture_session([
            {"role": "user", "content": "We chose FalkorDB for the hot cache."},
            {"role": "assistant", "content": "Noted: FalkorDB holds the hot cache."},
        ])
    lost = (monitoring.record_write_failure_counts().get("write_failed", 0)
            - before.get("write_failed", 0))
    assert receipt["ok"] is True, receipt
    return sdk, journal_path, lost


def test_the_public_capture_path_writes_the_record(capture_sdk, monkeypatch):
    """The record, written by the PUBLIC capture path — and the residue NAMED.

    Layer by layer, from the schema's own shape:

    * **event stream** — `event` / `source` / `session` (migration 1's episodic
      tables; `journal_event` is bookkeeping ABOUT the layers and is deferred,
      #9190);
    * **semantic** — `object` / `subject`, asserted against the objects the
      capture actually emitted (counted from the journal, so the claim is
      non-vacuous either way: a capture that emits no Object record must produce
      no Object row);
    * **procedural** — a VIEW, never a table ("the procedural layer owns no
      table", migration 4): `object_current_status` folds the `object` rows.

    Then the RESIDUE, per RECORD and unconditionally: every journal record is
    replayed on its own, and a refusal must name a known cause — either a
    schema/producer disagreement (the emitted value does not fit the declared
    column) or one of the pre-existing fold refusals PR #9200 fixes. Anything
    else is a NEW defect and fails here. Every allowance is derived from what
    the record itself carries or from a MEASURED fold behaviour, so nothing can
    go stale in silence.
    """
    conn = _record_db()
    try:
        _ensure_record_schema(conn)
        _seed_tenant(conn)
        _sdk, journal_path, lost = _capture(capture_sdk, monkeypatch)

        # 1. the event stream, from the production path
        for table in ("event", "source", "session"):
            assert _count(conn, table) >= 1, (
                f"{table} received no row from the public capture path")
        # 2. the position row is NOT invented (#9190)
        assert _count(conn, "journal_event") == 0

        records = _journal_records(journal_path)
        # 3. the semantic + procedural layers, counted against the journal.
        #    `assert objects` FIRST and unconditionally: without it the count
        #    comparison below is satisfiable by `0 >= 0`, i.e. by a capture that
        #    emitted no ObjectRegistered record at all — which is the pre-fix
        #    world (nothing written) reading as a pass. Measured: the public
        #    capture emits ObjectRegistered, so this is a real assertion.
        objects = [r for r in records if r.get("type") == "ObjectRegistered"]
        assert objects, (
            "the capture emitted no ObjectRegistered record, so the semantic- "
            f"layer claim would be vacuous — journal types: "
            f"{sorted({r.get('type') for r in records})}")
        assert _count(conn, "object") >= len(objects), (
            f"the record holds {_count(conn, 'object')} Object row(s) for the "
            f"{len(objects)} ObjectRegistered record(s) the journal carried")
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM public.object_current_status "
                        "WHERE org_id=%s", (ORG,))
            assert int(cur.fetchone()[0]) >= len(objects), (
                "the procedural layer's view returns fewer rows than the "
                "objects the record holds")

        # 4. the residue, per record
        refused = 0
        for record in records:
            try:
                record_projection.replay([record], conn, org_id=ORG,
                                         graph_id=GRAPH, position=False)
                conn.commit()
            except Exception as exc:
                conn.rollback()
                message = str(exc)
                if _engine_refusal(message) is not None:
                    # A pre-existing fold refusal: the record never reached
                    # Postgres, so it cannot be judged on its values yet.
                    refused += 1
                    continue
                disagreement = _disagreement(record)
                assert disagreement is not None, (
                    f"{record.get('type')} cannot be projected and the refusal "
                    f"is not a schema/producer disagreement: {message}. That is "
                    "a NEW defect, not a widened allowance."
                )
                assert _SCHEMA_DISAGREEMENTS[disagreement] in message, (
                    f"{record.get('type')} carries a {disagreement} "
                    f"disagreement but the error does not name it: {message}"
                )
                refused += 1
            else:
                assert _disagreement(record) is None, (
                    f"{record.get('type')} now projects cleanly, so its "
                    f"{_disagreement(record)!r} disagreement is FIXED — delete "
                    "it from _SCHEMA_DISAGREEMENTS. A pin that outlives its "
                    "defect hides the next one."
                )
        # 5. the capture lost exactly those records and nothing else
        assert lost == refused, (
            f"the capture counted {lost} lost record(s); replaying the journal "
            f"shows {refused} refused record(s)")
    finally:
        conn.close()


@pytest.mark.xfail(condition=_fold_refuses_captured_points(), strict=True,
                   reason=_ENGINE_DEPENDENCY)
def test_the_public_capture_path_writes_the_epistemic_layer(capture_sdk,
                                                            monkeypatch):
    """`point` / `relation` receive rows from the production path.

    THE claim the epic makes about a user's data ("a user's Points … exist in
    Postgres"). It is the ONLY assertion gated on the engine, because it is the
    only one the pre-existing fold defect makes unreachable: on a tree without
    #9200 every captured Point is refused, so `point` would be empty.
    """
    conn = _record_db()
    try:
        _ensure_record_schema(conn)
        _seed_tenant(conn)
        _capture(capture_sdk, monkeypatch)
        assert _count(conn, "point") >= 1, (
            "the epistemic layer received no row from the capture")
    finally:
        conn.close()

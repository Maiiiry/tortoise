"""#5045 — the declared cost-dimension registry must be TRUE about the code.

The registry (``tortoise/cost_dimensions.py``) is the substrate's declaration;
this file is the proof that it is a claim about reality rather than a comment.
Four things are pinned:

1. **Completeness.** Every org-attributed prometheus child in
   ``tortoise/monitoring.py`` is declared, and no other tracked module builds a
   prometheus metric. A seventh counter added in a hurry reds this, which is the
   failure #5045 exists to end (six lanes, each adding its own counter).
2. **Truth.** Every declared writer/reader resolves and is callable, every
   declared metric carries EXACTLY its declared labels, every declared column and
   SQL function exists in a tracked migration, and every declared cap/overflow
   attribute resolves.
3. **No dead hook.** A declared writer must be reachable from a tracked NON-TEST
   file, or the declaration must say why it has no schedule (#4493: a metric that
   registered, exported and tested while never being incremented in production).
4. **Declared policy == measured policy.** Each dimension's declared label
   cardinality LIFETIME (``persistent`` / ``per_publish``) is pinned against its
   real behaviour, so the two conventions the #4493 lane found could not drift
   further without a red test — the *choice* between them stays an open decision
   on #5045, recorded rather than silently made.

The mutation tests are the discriminating half: each injects the drift it claims
to catch and asserts the audit REPORTS it. A gate nobody has seen fail is not
evidence.
"""
from __future__ import annotations

import dataclasses
import importlib.util
import inspect
import os
import subprocess
import sys
import textwrap
from contextlib import contextmanager
from pathlib import Path

import pytest
from prometheus_client import Counter

import tortoise.cost_allocation as ca
import tortoise.cost_dimensions as cd
import tortoise.monitoring as monitoring
from tortoise.monitoring import (
    COMPUTE_MAX_ORGS,
    COMPUTE_OVERFLOW,
    EGRESS_MAX_ORGS,
    EGRESS_OVERFLOW,
)

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

_ROOT = Path(__file__).resolve().parent.parent
AUDIT_VERB = _ROOT / "tools" / "cost_coverage_audit.py"

#: The audit tool is loaded from its PATH (``tools/`` is not an import package,
#: and adding it to ``sys.path`` would put its imports after executable code).
_SPEC = importlib.util.spec_from_file_location("cost_coverage_audit", AUDIT_VERB)
audit = importlib.util.module_from_spec(_SPEC)
sys.modules["cost_coverage_audit"] = audit
_SPEC.loader.exec_module(audit)


# ── helpers ──────────────────────────────────────────────────────────────


@contextmanager
def _declaring(*dimensions):
    """Swap the registry for a mutated one, restoring it afterwards.

    The registry is module state (a tuple plus its index), so a mutation test
    has to patch BOTH — exactly as the audit reads it.
    """
    original = cd.DIMENSIONS
    cd.DIMENSIONS = tuple(dimensions)
    cd._BY_NAME = {d.name: d for d in cd.DIMENSIONS}
    try:
        yield
    finally:
        cd.DIMENSIONS = original
        cd._BY_NAME = {d.name: d for d in original}


def _mutated(name: str, **changes) -> cd.Dimension:
    return dataclasses.replace(cd.by_name(name), **changes)


def migration_sql() -> str:
    """Every tracked migration's SQL, as the ledger check reads it."""
    return audit.migration_text(_ROOT)


def _migration_root(root: Path, *migrations: str) -> Path:
    """A throwaway repo root whose ``supabase/migrations`` holds *migrations*.

    Files are numbered so they replay in the order given, which is what an
    APPENDED rename/drop depends on (the corpus is ordered, not a set).
    """
    migdir = root / "supabase" / "migrations"
    migdir.mkdir(parents=True, exist_ok=True)
    for i, sql in enumerate(migrations, start=1):
        (migdir / f"{i:04d}_corpus.sql").write_text(sql, encoding="utf-8")
    return root


# ── 1. completeness ──────────────────────────────────────────────────────


def test_every_org_attributed_metric_is_declared():
    """The substrate's per-org metric surface is exactly the declared one."""
    assert audit.undeclared_metric_findings() == [], (
        "an org-attributed metric exists that no declared dimension names")


def test_no_prometheus_metric_is_built_outside_the_metric_module():
    """A second metric surface is how a substrate grows an unowned counter."""
    assert audit.foreign_metric_findings(_ROOT) == []


def test_a_seventh_counter_cannot_appear_undeclared(monkeypatch):
    """MUTATION: a new org-labelled counter reds the audit.

    Driven through the LIVE enumeration (no injected child list), so the failure
    is the real predicate — a counter added to ``monitoring`` and nothing else.
    """
    monkeypatch.setattr(
        monitoring, "EGRESS_BYTES_V2",
        Counter("tortoise_egress_bytes_v2", "a seventh counter", ["org"]),
        raising=False)
    findings = audit.undeclared_metric_findings()
    assert any("EGRESS_BYTES_V2" in f for f in findings), findings


def test_a_prometheus_metric_outside_monitoring_is_flagged(tmp_path):
    """MUTATION: a metric constructed in another module is drift."""
    module = tmp_path / "tortoise" / "elsewhere.py"
    module.parent.mkdir(parents=True)
    module.write_text(textwrap.dedent("""
        from prometheus_client import Counter
        NEW_DIMENSION = Counter("tortoise_new_dimension_total", "x", ["org"])
    """), encoding="utf-8")
    findings = audit.foreign_metric_findings(tmp_path)
    assert findings, "a metric built outside the declared module was not flagged"
    assert "elsewhere.py" in findings[0]


def test_a_plain_collections_counter_is_not_mistaken_for_a_metric(tmp_path):
    """The gate must not red on ``collections.Counter`` tally code.

    A false positive here would make the check unusable in this repo, which uses
    ``Counter(...)`` for ordinary tallies in several modules. Both arms matter:
    the non-literal tally AND the string-literal tally — the earlier predicate
    keyed on "any identifier as the first argument", so ``Counter("abcd")`` (a
    perfectly ordinary tally of characters) was reported as a foreign prometheus
    metric. Provenance, not argument shape, decides it.
    """
    module = tmp_path / "tortoise" / "tally.py"
    module.parent.mkdir(parents=True)
    module.write_text(textwrap.dedent("""
        from collections import Counter
        def tally(tokens):
            return Counter(tokens)
        def chars():
            return Counter("abcd")
        def named():
            return Counter("tortoise_like_tally")
    """), encoding="utf-8")
    assert audit.foreign_metric_findings(tmp_path) == []


def test_a_keyword_constructed_metric_outside_monitoring_is_flagged(tmp_path):
    """MUTATION: ``Counter(name=…)`` is still a metric (the gate's fail-open).

    ``prometheus_client.Counter`` takes ``name``/``documentation`` as ordinary
    positional-or-keyword parameters, so the keyword form is valid code that the
    positional-only read missed entirely — a metric built outside
    ``monitoring.py`` that NEITHER half of check 4 could see.
    """
    module = tmp_path / "tortoise" / "kwarg_metric.py"
    module.parent.mkdir(parents=True)
    module.write_text(textwrap.dedent("""
        from prometheus_client import Counter
        NEW_DIMENSION = Counter(
            name="tortoise_new_dimension_total",
            documentation="built with keywords",
            labelnames=["org"],
        )
    """), encoding="utf-8")
    findings = audit.foreign_metric_findings(tmp_path)
    assert findings, "a keyword-constructed metric outside the declared module was not flagged"
    assert "kwarg_metric.py" in findings[0]


def test_an_aliased_prometheus_constructor_is_still_caught(tmp_path):
    """MUTATION: `import … as _C` must not be a bypass.

    The check resolves the callee to its IMPORT, so an aliased constructor is a
    second metric surface like any other. A callee-name match (`name in {Counter,
    Gauge, Histogram, Summary}`) could not see this spelling at all.
    """
    module = tmp_path / "tortoise" / "aliased.py"
    module.parent.mkdir(parents=True)
    module.write_text(textwrap.dedent("""
        from prometheus_client import Counter as _C
        NEW_DIMENSION = _C("tortoise_aliased_total", "x", ["org"])
    """), encoding="utf-8")
    findings = audit.foreign_metric_findings(tmp_path)
    assert findings, "an aliased prometheus constructor was not flagged"
    assert "aliased.py" in findings[0]


def test_an_unparseable_module_is_reported_not_skipped(tmp_path):
    """MUTATION: a module the scans cannot parse is a FINDING, never a pass.

    An unparseable file contributes no names and no metric facts, so it silently
    satisfied both corpus gates: it could reference every declared writer and build
    a tenth counter, and the audit reported clean. The module docstring's contract
    is "an unresolvable declaration is reported as drift, never skipped".
    """
    module = tmp_path / "tortoise" / "broken.py"
    module.parent.mkdir(parents=True)
    module.write_text("def oops(:\n", encoding="utf-8")
    assert audit.unparseable_corpus_findings(tmp_path), "an unparseable module was not reported"
    assert any("broken.py" in f for f in audit.foreign_metric_findings(tmp_path))


# ── 2. truth of the declaration ──────────────────────────────────────────


def test_every_declared_binding_resolves():
    """Each declared writer/reader callable, each metric with its exact labels."""
    assert audit.check_bindings() == []


def test_every_declared_column_and_sql_function_exists():
    """The ledger half of each declaration is verified against the DDL."""
    assert audit.check_ledger(_ROOT) == []


def test_a_renamed_column_is_drift():
    """MUTATION: a declared column that no migration creates is reported."""
    with _declaring(_mutated("egress", columns=("no_such_column_xyz",)),
                    *[d for d in cd.DIMENSIONS if d.name != "egress"]):
        findings = audit.check_ledger(_ROOT)
    assert any("no_such_column_xyz" in f for f in findings), findings


def test_a_column_named_only_in_a_sql_comment_is_not_a_definition():
    """MUTATION: prose is not DDL — the false green the docstring promises to avoid.

    The migrations quote column names in their own header comments. Before the
    comment strip + ALTER anchoring, ``_creates_column`` matched
    ``ADD COLUMN <col>`` ANYWHERE, so a header comment satisfied the ledger check
    for a column the DDL never creates (measured on
    ``20260925000002_oauth_redemption_state.sql``, whose comment says "each `ADD
    COLUMN code_id` takes ACCESS EXCLUSIVE").
    """
    prose = textwrap.dedent("""
        -- rollback note: this migration does ADD COLUMN IF NOT EXISTS embed_calls integer
        /* and the block comment says ADD COLUMN embed_chars text too */
        SELECT 1;
    """)
    assert audit._creates_column(prose, "embed_calls") is False
    assert audit._creates_column(prose, "embed_chars") is False
    # ... and the anchoring must not break the real thing:
    real = "ALTER TABLE public.metering_records\n    ADD COLUMN IF NOT EXISTS embed_calls bigint;",
    assert audit._creates_column(real[0], "embed_calls") is True
    assert audit._creates_column(migration_sql(), "embed_calls") is True


def test_an_rpc_named_only_in_a_grant_or_rename_is_not_a_definition():
    """MUTATION: a mention of a function is not its definition.

    ``GRANT``/``REVOKE``/``COMMENT ON``/``ALTER FUNCTION … RENAME TO`` all name a
    function without creating it, so the old ``FUNCTION public.<rpc>`` substring
    matched a RENAMED-away RPC and the declaration went stale green. The twin of
    the renamed-column mutation above.
    """
    sql = textwrap.dedent("""
        ALTER FUNCTION public.metering_increment_embedding(text) RENAME TO old_name;
        REVOKE ALL ON FUNCTION public.metering_increment_embedding(text) FROM anon;
        GRANT EXECUTE ON FUNCTION public.metering_increment_embedding(text) TO s;
        COMMENT ON FUNCTION public.metering_increment_embedding IS 'gone';
    """)
    assert audit._defines_function(sql, "metering_increment_embedding") is False
    created = "CREATE OR REPLACE FUNCTION public.metering_increment_embedding(\n"
    assert audit._defines_function(created, "metering_increment_embedding") is True
    assert audit._defines_function(migration_sql(), "metering_increment_embedding") is True


def test_a_bare_name_type_line_outside_a_create_table_is_not_a_definition():
    """MUTATION: only a real ``CREATE TABLE`` body defines a column.

    The second arm used to be ``^\\s*<name>\\s+<type>`` under ``re.MULTILINE`` over
    the WHOLE concatenated corpus, so ``^`` was any line start with no
    ``CREATE TABLE`` context at all — while three documents promised a definition
    inside a ``CREATE TABLE`` body. Two shapes this repo writes satisfy the old
    arm and create nothing: a plpgsql ``DECLARE`` variable
    (``0014_metering_records.sql``, ``0010_provisioning_rpcs.sql``) and a
    ``RETURNS TABLE (`` output column
    (``20260927000001_metering_unmetered_increments.sql``). Latent rather than live
    only because every declared column is matched by an ``ADD COLUMN`` today.
    """
    declare = textwrap.dedent("""
        DECLARE
          nodes_written integer;
        BEGIN
        END;
    """)
    returns_table = textwrap.dedent("""
        RETURNS TABLE (
          nodes_written bigint
        )
    """)
    assert audit._creates_column(declare, "nodes_written") is False
    assert audit._creates_column(returns_table, "nodes_written") is False
    # ... and the anchoring must not break the real thing, constraints included:
    create = textwrap.dedent("""
        CREATE TABLE IF NOT EXISTS public.metering_nodes (
          org_id uuid NOT NULL,
          nodes_written bigint NOT NULL DEFAULT 0,
          tags jsonb,
          PRIMARY KEY (org_id)
        );
    """)
    assert audit._creates_column(create, "nodes_written") is True
    assert audit._creates_column(create, "org_id") is True
    assert audit._creates_column(create, "PRIMARY") is False


def test_a_column_renamed_by_an_APPENDED_migration_is_drift(tmp_path):
    """MUTATION: the corpus is REPLAYED, so an append-only RENAME is drift.

    This repo expresses a rename as a NEW migration appended after the one that
    created the column (``20260915000001_tenancy_team_to_org.sql``: "Earlier
    migrations keep their historical `team` spelling and are untouched: they run
    first, this migration renames the end state"). A history-only predicate keeps
    matching the earlier file's ``ADD COLUMN`` FOREVER, so the stale declaration
    stayed green on a column the schema no longer has.
    """
    root = _migration_root(
        tmp_path,
        "ALTER TABLE public.metering_records\n    ADD COLUMN embed_calls bigint;",
        "ALTER TABLE public.metering_records\n    RENAME COLUMN embed_calls TO embed_call_count;",
        "ALTER TABLE public.metering_records\n    ADD COLUMN embed_dropped bigint;",
        "ALTER TABLE public.metering_records\n    DROP COLUMN embed_dropped;",
    )
    sql = audit.migration_text(root)
    assert audit._creates_column(sql, "embed_calls") is False
    assert audit._creates_column(sql, "embed_call_count") is True
    assert audit._creates_column(sql, "embed_dropped") is False
    with _declaring(_mutated("egress", columns=("embed_calls", "embed_call_count"))):
        findings = audit.check_ledger(root)
    assert any("'embed_calls'" in f for f in findings), findings
    assert not any("embed_call_count" in f for f in findings), findings


def test_every_command_in_a_multi_command_alter_table_moves_the_end_state():
    """MUTATION: a single ``ALTER TABLE`` may carry several verbs.

    Postgres allows ``DROP COLUMN a, ADD COLUMN b, RENAME COLUMN c TO d`` in ONE
    statement. Applying only the FIRST command left the rest invisible: the added
    column read as absent and the renamed-away one as present — a stale
    declaration stays green in both directions, which is the same fail-open the
    replay closes for append-only migrations, one command further in.
    """
    sql = textwrap.dedent("""
        ALTER TABLE public.metering_records
            ADD COLUMN embed_calls bigint,
            ADD COLUMN embed_tokens bigint,
            ADD COLUMN embed_dropped bigint;
        ALTER TABLE public.metering_records
            DROP COLUMN embed_dropped,
            RENAME COLUMN embed_tokens TO embed_token_count;
    """)
    assert audit._creates_column(sql, "embed_calls") is True
    assert audit._creates_column(sql, "embed_token_count") is True
    assert audit._creates_column(sql, "embed_dropped") is False
    assert audit._creates_column(sql, "embed_tokens") is False


def test_a_dropped_table_takes_its_columns_with_it(tmp_path):
    """MUTATION: ``DROP TABLE`` removes the columns it held.

    The replay tracked columns per table but never dropped a table, so a column
    created only on a table the corpus later drops stayed "present" for good —
    a declaration about a table that no longer exists stayed green. The live
    column on the surviving table must still be found, so this is not a predicate
    that simply says no.
    """
    root = _migration_root(tmp_path, textwrap.dedent("""
        CREATE TABLE public.metering_retired (
          embed_dropped bigint
        );
        CREATE TABLE public.metering_live (
          embed_calls bigint
        );
        DROP TABLE IF EXISTS public.metering_retired;
    """))
    sql = audit.migration_text(root)
    assert audit._creates_column(sql, "embed_dropped") is False
    assert audit._creates_column(sql, "embed_calls") is True
    with _declaring(_mutated("egress", columns=("embed_dropped", "embed_calls"))):
        findings = audit.check_ledger(root)
    assert any("'embed_dropped'" in f for f in findings), findings
    assert not any("'embed_calls'" in f for f in findings), findings


def test_a_multi_target_drop_table_takes_EVERY_table_with_it(tmp_path):
    """MUTATION: ``DROP TABLE a, b`` drops both — not only the first.

    ``DROP TABLE`` takes a comma-separated target list and Postgres drops all of
    them atomically. The replay captured a SINGLE identifier, so ``DROP TABLE IF
    EXISTS a, b`` popped only ``a`` and ``b``'s columns kept reading as present
    for the rest of the replay — the stale-declaration fail-open the replay exists
    to close, one target further in (and the twin of the multi-verb ``ALTER
    TABLE`` arm above). The surviving table's column must still be found, so this
    is not a predicate that simply says no.
    """
    root = _migration_root(tmp_path, textwrap.dedent("""
        CREATE TABLE public.metering_first (embed_first bigint);
        CREATE TABLE public.metering_second (embed_second bigint);
        CREATE TABLE public.metering_kept (embed_kept bigint);
        DROP TABLE IF EXISTS public.metering_first, public.metering_second;
    """))
    sql = audit.migration_text(root)
    assert audit._creates_column(sql, "embed_first") is False
    assert audit._creates_column(sql, "embed_second") is False
    assert audit._creates_column(sql, "embed_kept") is True
    with _declaring(_mutated("egress", columns=("embed_second", "embed_kept"))):
        findings = audit.check_ledger(root)
    assert any("'embed_second'" in f for f in findings), findings
    assert not any("embed_kept" in f for f in findings), findings


def test_a_multi_target_drop_function_drops_EVERY_function_with_it(tmp_path):
    """MUTATION: ``DROP FUNCTION f(a), g(b)`` drops BOTH — not only the first.

    ``DROP FUNCTION`` takes the same comma-separated target list ``DROP TABLE``
    does, and Postgres drops every target in one statement. The replay captured a
    SINGLE identifier, so ``DROP FUNCTION IF EXISTS f(a), g(b)`` discarded only
    ``f`` and ``g`` kept reading as defined for the rest of the replay — the same
    stale-declaration fail-open the sibling ``DROP TABLE`` arm above pins, one
    verb over, and unreported (no ``problems`` entry), so the declaration stayed
    green on a function the end state no longer has. The surviving function must
    still be found, so this is not a predicate that simply says no.
    """
    root = _migration_root(tmp_path, textwrap.dedent("""
        CREATE OR REPLACE FUNCTION public.metering_increment_first(p_org text)
        RETURNS void AS $$ BEGIN END; $$ LANGUAGE plpgsql;
        CREATE OR REPLACE FUNCTION public.metering_increment_second(p_org text)
        RETURNS void AS $$ BEGIN END; $$ LANGUAGE plpgsql;
        CREATE OR REPLACE FUNCTION public.metering_increment_kept(p_org text)
        RETURNS void AS $$ BEGIN END; $$ LANGUAGE plpgsql;
        DROP FUNCTION IF EXISTS public.metering_increment_first(text),
                                public.metering_increment_second(text);
    """))
    sql = audit.migration_text(root)
    assert audit._defines_function(sql, "metering_increment_first") is False
    assert audit._defines_function(sql, "metering_increment_second") is False
    assert audit._defines_function(sql, "metering_increment_kept") is True
    with _declaring(_mutated(
            "retrieval",
            columns=(),
            rpcs=("metering_increment_second", "metering_increment_kept"))):
        findings = audit.check_ledger(root)
    assert any("'metering_increment_second'" in f for f in findings), findings
    assert not any("metering_increment_kept" in f for f in findings), findings


def test_a_drop_function_target_the_replay_cannot_read_is_a_problem():
    """The rule is uniform: an unreadable target is a PROBLEM, never a skip.

    ``_drop_table_targets`` states it for tables ("A target this replay cannot read
    is a PROBLEM, never a silent half-drop"); the function replay applies the same
    rule, so a ``DROP FUNCTION`` shape the name-shaped replay cannot parse is
    reported as drift instead of leaving every function it names green.
    """
    targets, problems = audit._drop_function_targets(
        "public.metering_increment_first(text), "
        "\"public\".\"weird name\"(text)")
    assert targets == ["metering_increment_first"]
    assert len(problems) == 1, problems
    assert "DROP FUNCTION target" in problems[0]


def test_a_multi_target_drop_function_is_applied_by_the_ledger_replay():
    """The discriminating arm, on the live corpus path: end state, not history.

    A drop-then-recreate pair is the repo's live idiom
    (``20260813000002_metering_nodes_written.sql``), so the pair form below is the
    next migration a real ``DROP FUNCTION f(old_sig), g(old_sig)`` would look like:
    every listed name must be gone when nothing recreates it afterwards, and the
    signature's commas must not confuse the target split.
    """
    schema = audit._effective_schema(textwrap.dedent("""
        CREATE FUNCTION public.metering_increment_ask(
          p_org text, p_model text
        ) RETURNS void AS $$ BEGIN END; $$ LANGUAGE plpgsql;
        CREATE FUNCTION public.metering_increment_retrieval(
          p_org text, p_model text
        ) RETURNS void AS $$ BEGIN END; $$ LANGUAGE plpgsql;
        CREATE FUNCTION public.metering_increment_kept(p_org text)
        RETURNS void AS $$ BEGIN END; $$ LANGUAGE plpgsql;
        DROP FUNCTION IF EXISTS public.metering_increment_ask(text, text),
                                public.metering_increment_retrieval(text, text)
                                CASCADE;
    """))
    assert sorted(schema.functions) == ["metering_increment_kept"], schema.functions
    assert schema.problems == ()


def test_a_dash_inside_a_string_literal_does_not_swallow_the_ddl_after_it():
    """MUTATION: the comment strip is QUOTE-AWARE, so literals cannot eat DDL.

    ``_strip_sql_comments`` used ``re.sub(r"--[^\n]*")``, which is quote-unaware:
    a ``--`` inside a single-quoted literal deleted the literal's closing quote
    too, leaving an unterminated quote that made ``_statements`` run to the NEXT
    quote and emit one statement matching no DDL regex. Everything after it —
    ``DROP FUNCTION``, ``RENAME COLUMN`` — went invisible, in BOTH directions:
    a dropped/renamed symbol read as still present, and a symbol that IS created
    read as drift when the literal came first. Each assertion below is that
    inversion.
    """
    dropped = textwrap.dedent("""
        CREATE FUNCTION public.metering_kept(text) RETURNS void AS $$ BEGIN END; $$ LANGUAGE plpgsql;
        COMMENT ON TABLE public.metering_records IS 'a -- b';
        DROP FUNCTION IF EXISTS public.metering_kept(text);
    """)
    assert audit._defines_function(dropped, "metering_kept") is False
    renamed = textwrap.dedent("""
        ALTER TABLE public.metering_records ADD COLUMN embed_calls bigint;
        COMMENT ON TABLE public.metering_records IS 'a -- b';
        ALTER TABLE public.metering_records RENAME COLUMN embed_calls TO embed_call_count;
    """)
    assert audit._creates_column(renamed, "embed_calls") is False
    assert audit._creates_column(renamed, "embed_call_count") is True
    # ... and the mirror failure: a literal BEFORE real DDL must not report the
    # column the DDL really creates as drift (a false red on a correct change).
    created = textwrap.dedent("""
        COMMENT ON TABLE public.metering_records IS 'a -- b';
        ALTER TABLE public.metering_records ADD COLUMN IF NOT EXISTS embed_calls bigint;
    """)
    assert audit._creates_column(created, "embed_calls") is True
    # ... and the real corpus is still read through the same lexer:
    assert audit._creates_column(migration_sql(), "embed_calls") is True


def test_an_unlexable_corpus_is_a_finding_not_a_silent_pass(tmp_path):
    """MUTATION: an input the replay cannot read is drift, never a skip.

    The module's rule is "an unresolvable declaration is reported as drift, never
    skipped with a warning". A corpus containing an unterminated literal leaves
    every following statement unread, and the pre-fix replay returned a clean
    ``[]`` — the gate reporting "verified" about statements it never saw.
    """
    root = _migration_root(tmp_path, textwrap.dedent("""
        -- the literal below is never closed
        COMMENT ON TABLE public.metering_records IS 'a -- b;
        ALTER TABLE public.metering_records ADD COLUMN embed_calls bigint;
    """))
    # The ledger must SAY it could not read the corpus (pre-fix: a clean ``[]``),
    # and the lexer must name the problem rather than truncate silently.
    with _declaring(_mutated("egress", columns=("embed_calls",))):
        findings = audit.check_ledger(root)
    assert any("cannot be replayed" in f for f in findings), findings
    assert audit._lex_sql(audit.migration_text(root)).problems


def test_an_rpc_renamed_or_dropped_by_an_APPENDED_migration_is_drift(tmp_path):
    """MUTATION: the RPC twin — the end state, not the historical ``CREATE``.

    ``DROP FUNCTION IF EXISTS public.metering_increment(text, text, integer)``
    followed by a NEW ``CREATE`` is the repo's live pattern
    (``20260813000002_metering_nodes_written.sql``), and
    ``20260915000001`` drops the pre-org signature outright. A rename is a
    rename whichever verb writes it.
    """
    root = _migration_root(
        tmp_path,
        "CREATE OR REPLACE FUNCTION public.metering_increment_embedding(\n"
        "  p_org text\n) RETURNS void AS $$ BEGIN END; $$ LANGUAGE plpgsql;",
        "CREATE OR REPLACE FUNCTION public.metering_increment_retrieval(\n"
        "  p_org text\n) RETURNS void AS $$ BEGIN END; $$ LANGUAGE plpgsql;",
        "ALTER FUNCTION public.metering_increment_embedding(text)\n"
        "    RENAME TO metering_increment_embedding_v2;",
        "DROP FUNCTION IF EXISTS public.metering_increment_retrieval(text);",
    )
    sql = audit.migration_text(root)
    assert audit._defines_function(sql, "metering_increment_embedding") is False
    assert audit._defines_function(sql, "metering_increment_embedding_v2") is True
    assert audit._defines_function(sql, "metering_increment_retrieval") is False
    with _declaring(_mutated(
            "retrieval",
            columns=(),
            rpcs=("metering_increment_retrieval",
                  "metering_increment_embedding_v2"))):
        findings = audit.check_ledger(root)
    assert any("'metering_increment_retrieval'" in f for f in findings), findings
    assert not any("metering_increment_embedding_v2" in f for f in findings), findings


def test_a_renamed_cap_constant_is_drift():
    """MUTATION: a cap attribute that no longer resolves is reported."""
    broken = _mutated(
        "egress",
        cardinality=cd.Cardinality(
            lifetime=cd.PERSISTENT,
            cap="tortoise.monitoring.EGRESS_MAX_ORGS_RENAMED",
            overflow="tortoise.monitoring.EGRESS_OVERFLOW"))
    with _declaring(broken, *[d for d in cd.DIMENSIONS if d.name != "egress"]):
        findings = audit.check_cardinality()
    assert any("EGRESS_MAX_ORGS_RENAMED" in f for f in findings), findings


def test_both_label_axes_are_declared_and_pinned():
    """The route axis is a live cap convention too — declared, not assumed.

    The registry used to describe THREE label-cap conventions and declare two: the
    route axis carries its own caps (``EGRESS_MAX_PATHS``,
    ``EGRESS_MAX_DERIVED_PATHS``, ``COMPUTE_MAX_PATHS``) and its own
    ``__unrouted__`` sentinel, and ``check_cardinality`` could not see any of them.
    An undeclared live cap convention is the drift class this module exists to end.
    """
    assert audit.check_cardinality() == []
    for name in ("egress", "hosted_compute"):
        card = cd.by_name(name).cardinality
        assert card is not None and card.path_caps, f"{name} declares no path-axis cap"
        assert card.path_overflow, f"{name} declares no path-axis overflow sentinel"
    # Two axes, two sentinels: the path fallback is deliberately NOT `__other__`, so
    # a client cannot pre-occupy the overflow child by requesting its name.
    assert monitoring.EGRESS_UNROUTED != monitoring.EGRESS_OVERFLOW
    broken = _mutated(
        "egress",
        cardinality=cd.Cardinality(
            lifetime=cd.PERSISTENT,
            cap="tortoise.monitoring.EGRESS_MAX_ORGS",
            overflow="tortoise.monitoring.EGRESS_OVERFLOW",
            path_caps=("tortoise.monitoring.EGRESS_MAX_PATHS_RENAMED",),
            path_overflow="tortoise.monitoring.EGRESS_UNROUTED"))
    with _declaring(broken, *[d for d in cd.DIMENSIONS if d.name != "egress"]):
        findings = audit.check_cardinality()
    assert any("EGRESS_MAX_PATHS_RENAMED" in f for f in findings), findings


def test_declared_metric_labels_match_the_live_metric():
    """MUTATION: a declared label set that differs from the live child is drift."""
    dim = cd.by_name("egress")
    wrong = dataclasses.replace(
        next(b for b in dim.metrics() if b.attr == "EGRESS_BYTES"),
        labels=("org",))
    others = tuple(b for b in dim.bindings if b.attr != "EGRESS_BYTES")
    rest = tuple(d for d in cd.DIMENSIONS if d.name != "egress")
    with _declaring(dataclasses.replace(dim, bindings=(wrong, *others)), *rest):
        findings = audit.check_bindings()
    assert any("carries labels" in f for f in findings), findings


def test_a_metric_binding_without_a_declared_family_is_drift():
    """MUTATION: an UNDECLARED family is drift, not a skip.

    ``Binding.family`` defaults to ``""`` and nothing required a ``METRIC`` binding
    to declare one, so the family half of the check was guarded behind ``if
    binding.family and family != binding.family``: a new dimension that named a
    metric without ``family=`` got a clean ``matrix`` with its family UNVERIFIED —
    and a renamed family (every dashboard's selector) read as "no drift". The
    label half was never optional, and neither may this one be.
    """
    dim = cd.by_name("egress")
    no_family = dataclasses.replace(
        next(b for b in dim.metrics() if b.attr == "EGRESS_BYTES"), family="")
    others = tuple(b for b in dim.bindings if b.attr != "EGRESS_BYTES")
    rest = tuple(d for d in cd.DIMENSIONS if d.name != "egress")
    with _declaring(dataclasses.replace(dim, bindings=(no_family, *others)), *rest):
        findings = audit.check_bindings()
    assert any("declares no family" in f for f in findings), findings
    # Every declared metric binding DOES declare its family (the other direction):
    assert audit.check_bindings() == []


def test_a_renamed_metric_family_is_drift():
    """MUTATION: the emitted family name is a series identity, not a detail.

    ``attributed_bindings()``'s docstring used to say the audit "reads the LIVE
    child name ``_name``" while nothing read it, so renaming a family (e.g.
    ``tortoise_egress_bytes`` → ``tortoise_egress_bytes_v2``) changed every series a
    dashboard selects on and reported "no drift". Now the declared family is
    compared against the live ``_name``.
    """
    dim = cd.by_name("egress")
    wrong = dataclasses.replace(
        next(b for b in dim.metrics() if b.attr == "EGRESS_BYTES"),
        family="tortoise_egress_bytes_v2")
    others = tuple(b for b in dim.bindings if b.attr != "EGRESS_BYTES")
    rest = tuple(d for d in cd.DIMENSIONS if d.name != "egress")
    with _declaring(dataclasses.replace(dim, bindings=(wrong, *others)), *rest):
        findings = audit.check_bindings()
    assert any("emits family" in f for f in findings), findings
    # The declared families are the live ones (the OTHER direction, unmutated):
    assert audit.check_bindings() == []


# ── 3. no dead hook ──────────────────────────────────────────────────────


def test_every_dimension_has_a_production_call_site_or_says_why_not():
    assert audit.check_dead_hooks(_ROOT) == []


def test_the_dead_hook_check_is_not_vacuous():
    """The dimensions with no schedule DECLARE it — they are not silent.

    ``measure_postgres_storage``/``read_analytics_row_growth`` have no caller
    outside tests on purpose (the SQL function is the read path). If that were
    silent the check above would be passing for the wrong reason, so the declared
    reason is asserted here.
    """
    for name in ("postgres_storage", "analytics_row_growth"):
        assert cd.by_name(name).unscheduled_reason, (
            f"{name} has no production caller and no declared reason")


def test_a_writer_only_tests_reach_is_a_dead_hook():
    """MUTATION: a writer no production file mentions is reported."""
    ghost = _mutated(
        "egress",
        bindings=(cd.Binding("tortoise.monitoring", "record_egress_ghost",
                             cd.WRITER),),
        unscheduled_reason=None)
    with _declaring(ghost, *[d for d in cd.DIMENSIONS if d.name != "egress"]):
        findings = audit.check_dead_hooks(_ROOT)
    assert any("dead hook" in f for f in findings), findings


def test_a_writer_reached_only_from_a_tool_is_a_dead_hook(tmp_path):
    """MUTATION: a developer INSTRUMENT is not a production call site.

    ``tools/`` used to be scanned as production, so a script that merely mentions
    the writer satisfied the check and a dimension with no production increment
    read as measured — the #4493 defect one directory over ("registered, exported
    and tested while never being incremented in production"). ``battery/`` and
    ``graph-scripts/`` were already excluded for exactly this reason.
    """
    tool = tmp_path / "tools" / "capture_cost_report.py"
    tool.parent.mkdir(parents=True)
    tool.write_text(textwrap.dedent("""
        from tortoise.metering import record_egress
        print(record_egress)
    """), encoding="utf-8")
    sites = audit.production_call_sites(tmp_path)
    assert sites["record_egress"] == [], (
        f"a tools/ script was read as a production call site: {sites['record_egress']}")
    findings = audit.check_dead_hooks(tmp_path)
    assert any("dead hook" in f and "record_egress" in f for f in findings), findings


# ── 4. the coverage query ────────────────────────────────────────────────


def test_no_dimension_is_unmeasured_and_undeclared():
    """#5045's target: 0 unmeasured dimensions, each out-of-scope one named."""
    assert cd.states() == (cd.MEASURED, cd.ALLOCATION, cd.OUT_OF_SCOPE)
    for dim in cd.dimensions():
        assert dim.state in cd.states(), dim.name
        if dim.state != cd.MEASURED:
            assert dim.limits, f"{dim.name} is {dim.state} with no stated reason"
    assert cd.unbacked_dimensions() == (), cd.unbacked_dimensions()
    matrix = cd.render_matrix()
    assert "unmeasured-and-undeclared: 0" in matrix
    for dim in cd.out_of_scope():
        assert dim.name in matrix, dim.name
        assert dim.limits[0][:40] in matrix, dim.name


def test_the_last_matrix_figure_is_DERIVED_not_a_literal():
    """MUTATION: the summary's last number must be able to move.

    It used to be the string literal ``unmeasured-and-undeclared: 0`` inside
    ``render_matrix``'s f-string, so no registry state could change it and the two
    assertions on it were green by construction — while the PR cited the ``0`` as a
    measured result. Now: a MEASURED row with no binding and no declared reason
    counts, an unknown state counts, and the audit's own undeclared-metric findings
    count.
    """
    assert cd.unbacked_dimensions() == ()
    empty = cd.Dimension(
        name="ghost_dimension", kind="local_compute", unit="units",
        attribution="none", state=cd.MEASURED, note="mutation: no primitive")
    with _declaring(empty):
        assert cd.unbacked_dimensions() == ("ghost_dimension",)
        assert "unmeasured-and-undeclared: 1" in cd.render_matrix()
    # The audit's undeclared-metric count feeds the same figure.
    assert "unmeasured-and-undeclared: 2" in cd.render_matrix(undeclared_metrics=2)


def test_an_unknown_state_is_drift():
    """MUTATION: a typo'd state falls into none of the three buckets.

    It is counted in neither ``measured``/``allocations`` nor ``out of scope``, so
    the summary's arithmetic silently stops adding up and ``matrix`` still exited 0
    (its docstring promises "exit 1 on ANY drift").
    """
    typo = _mutated("egress", state="measurd")
    with _declaring(typo, *[d for d in cd.DIMENSIONS if d.name != "egress"]):
        findings = audit.check_states()
        assert any("measurd" in f for f in findings), findings
        assert cd.unbacked_dimensions() == ("egress",)
        assert "unmeasured-and-undeclared: 1" in cd.render_matrix()
        assert audit.audit(_ROOT)["states"], "audit() dropped the state check"


def test_the_coverage_matrix_names_its_allocations():
    """An ALLOCATION must never be readable as a measurement."""
    allocations = cd.allocations()
    assert [d.name for d in allocations] == ["fixed_shared_saas"]
    assert allocations[0].state != cd.MEASURED


def test_query_by_name_answers_and_typos_are_not_answers():
    assert cd.by_name("egress").state == cd.MEASURED
    assert cd.by_name("mitigation_weight_delta").state == cd.OUT_OF_SCOPE
    with pytest.raises(KeyError) as exc:
        cd.by_name("egres")  # a typo must never read as "not measured"
    assert "egress" in str(exc.value)


def test_query_verb_exit_codes_distinguish_the_three_answers():
    """measured -> 0; declared out of scope -> 3; unknown -> 2."""
    def run(name: str) -> int:
        return audit.main(["query", name])

    assert run("egress") == 0
    assert run("fixed_shared_saas") == 0
    assert run("mitigation_weight_delta") == 3
    assert run("egres") == 2


def test_the_matrix_verb_reports_no_drift_on_this_tree(capsys):
    """The operator verb, end to end: exit code 0 and every check reported."""
    assert audit.main(["matrix"]) == 0
    out = capsys.readouterr().out
    assert "no drift" in out
    derived = len(cd.unbacked_dimensions()) + len(audit.undeclared_metric_findings())
    assert f"unmeasured-and-undeclared: {derived}" in out
    assert derived == 0, derived


def test_the_cli_entrypoint_works_in_a_fresh_interpreter():
    """A real ``python tools/cost_coverage_audit.py <verb>`` invocation.

    ``query`` (not ``matrix``) so the subprocess pays an import and nothing else:
    the exit code and the argv plumbing are what this pins, and the corpus scan is
    already covered in-process above.
    """
    proc = subprocess.run(
        [sys.executable, str(AUDIT_VERB), "query", "egress"],
        cwd=_ROOT, capture_output=True, text=True,
        env={**os.environ, "TORTOISE_SECRET_PEPPER": "test-static-pepper"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "egress: measured" in proc.stdout


# ── 5. declared cardinality POLICY == measured policy ────────────────────
#
# The #4493 lane found two live conventions with different SEMANTICS and no
# convergence point. They are declared (module docstring) and pinned here, so the
# divergence is a recorded fact rather than an accident — the choice between them
# is the open #5045 decision.


def test_egress_org_admission_is_persistent_by_arrival_not_by_sorted_order():
    """``persistent``: an admitted label keeps its child; a later one folds.

    The discriminating observation is ORDER: with the cap full, an org that sorts
    BEFORE the admitted ones still folds, because admission is first-come and is
    never revisited. A ``per_publish`` policy (recomputed from the current set, in
    sorted order — the allocation's rule) would instead give the newcomer the
    child and demote the incumbent.
    """
    monitoring._reset_egress()
    original_cap = monitoring.EGRESS_MAX_ORGS
    monitoring.EGRESS_MAX_ORGS = 2
    try:
        monitoring.record_egress("m-org", "/v1/x", 10)
        monitoring.record_egress("z-org", "/v1/x", 20)
        monitoring.record_egress("a-org", "/v1/x", 40)  # sorts first, arrives last
        totals = monitoring.egress_bytes_by_org()
        assert totals["m-org"] == 10 and totals["z-org"] == 20
        assert totals[EGRESS_OVERFLOW] == 40
        assert "a-org" not in totals, "a first-come cap re-admitted a later org"
        # A second cycle keeps the admitted children where they are: no flap.
        monitoring.record_egress("m-org", "/v1/x", 5)
        monitoring.record_egress("a-org", "/v1/x", 1)
        totals = monitoring.egress_bytes_by_org()
        assert totals["m-org"] == 15
        assert totals[EGRESS_OVERFLOW] == 41
    finally:
        monitoring.EGRESS_MAX_ORGS = original_cap
        monitoring._reset_egress()


def test_compute_org_admission_is_persistent_like_egress():
    """The compute twin declares the same lifetime — pinned, not assumed."""
    monitoring._reset_compute()
    original_cap = monitoring.COMPUTE_MAX_ORGS
    monitoring.COMPUTE_MAX_ORGS = 1
    try:
        monitoring.record_compute("m-org", "/v1/x", 0.5)
        monitoring.record_compute("a-org", "/v1/x", 0.25)
        by_org = monitoring.compute_by_org()
        assert by_org["m-org"]["requests"] == 1.0
        assert by_org[COMPUTE_OVERFLOW]["requests"] == 1.0
        assert "a-org" not in by_org
        assert COMPUTE_OVERFLOW == EGRESS_OVERFLOW  # one sentinel spelling
        assert COMPUTE_MAX_ORGS == EGRESS_MAX_ORGS  # one cap value
    finally:
        monitoring.COMPUTE_MAX_ORGS = original_cap
        monitoring._reset_compute()


def test_allocation_org_admission_is_per_publish_and_self_healing():
    """``per_publish``: the admitted set follows the CURRENT orgs, so it prunes.

    Under the persistent policy the folded ``__other__`` child would keep its
    accumulated value forever. Here a re-publish with the org gone prunes the
    child, which is what makes the two conventions different KINDS.
    """
    ca._reset_for_tests()
    original_cap = ca.MAX_ORG_LABELS
    ca.MAX_ORG_LABELS = 2
    try:
        ca.publish(_snapshot_for({"a": 10, "b": 20, "c": 30}))
        first = monitoring.team_cost_cents()
        assert set(first) == {"a", "b", ca.ORG_OVERFLOW}
        assert first[ca.ORG_OVERFLOW] == 30
        # The fleet shrinks: the per-publish rule recomputes admission, so the
        # overflow child is pruned rather than retained (persistent would keep it).
        ca.publish(_snapshot_for({"a": 10, "b": 20}))
        second = monitoring.team_cost_cents()
        assert set(second) == {"a", "b"}, second
    finally:
        ca.MAX_ORG_LABELS = original_cap
        ca._reset_for_tests()


def _snapshot_for(shares: dict[str, int]) -> ca.AllocationSnapshot:
    """One-line MEASURED snapshot carrying *shares* (integer cents)."""
    return ca.AllocationSnapshot(
        generated_at="2026-10-18T00:00:00+00:00",
        window_start="2026-10-01T00:00:00+00:00",
        window_end="2026-11-01T00:00:00+00:00",
        unit="cents",
        state=ca.STATE_MEASURED,
        enumeration_available=True,
        unallocated_cents=0,
        lines=(
            ca.LineAllocation(
                line="shared-saas",
                basis="even",
                total_cents=sum(shares.values()),
                source="test",
                as_of="2026-10-18T00:00:00+00:00",
                is_estimate=False,
                state=ca.STATE_MEASURED,
                shares=tuple(ca.OrgShare(org_label=org, cents=cents)
                             for org, cents in shares.items()),
                residual_cents=0,
                detail="",
            ),
        ),
    )


# ── 6. one read path for the org-attributed dimensions ───────────────────


def test_both_org_readers_share_one_extraction():
    """#5045's convergence: a dimension differs in unit, not in how it is read."""
    egress_src = inspect.getsource(monitoring.egress_bytes_by_org)
    compute_src = inspect.getsource(monitoring._sum_compute_by_org)
    assert "_sum_by_label" in egress_src
    assert "_sum_by_label" in compute_src
    # ... and the shared helper is what actually sums, not a leftover second loop.
    monitoring._reset_egress()
    monitoring.record_egress("o", "/v1/a", 7)
    monitoring.record_egress("o", "/v1/b", 5)
    assert monitoring.egress_bytes_by_org()["o"] == 12
    assert monitoring._sum_by_label(
        monitoring.EGRESS_BYTES, "org", cast=int)["o"] == 12
    monitoring._reset_egress()

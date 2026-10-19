"""#8483 — a session-lifetime MCP server must be able to say its code is stale.

WHY THIS FILE EXISTS
--------------------
Every `pi` session spawns its own `tortoise.mcp_server` child and that process
then holds one code revision for the whole life of the session: no supervisor,
no respawn, no reload. Measured 2026-10-13, five servers started within eight
seconds of each other and one ran 12h53m. A read-path fix (`#8316`'s
`stored_embedding_vector_expr`) landed ~11 hours AFTER they started, so every
one of them served code that predated it and every hybrid-search read died with
`cannot import name 'stored_embedding_vector_expr'`.

The failure was not the problem; the SILENCE was. From inside the session it
reads like an empty graph — the write mode `how-to-use-tortoise` explicitly
warns about — and the health surface said `{"status": "ok"}` the whole time,
because the database really was fine and only the client's code was stale.

#8483's minimum is DETECTABILITY, and that is all this change adds: no reload,
no spawned-per-call, no shared supervised server (all three are the owner's
call, and a shared server changes the multi-tenant story). So these tests pin:

* the staleness verdict itself, over a fixture tree, in both directions —
  a source newer than the load instant is stale, everything older is not;
* a file the process never imported still counts (the module that breaks a
  stale server is usually one that did not exist when it started);
* the `code` block on the health surface, through BOTH routes that serve it
  (`tortoise_health`, and the live consolidator `tortoise_overview(section=
  "health")`) — and that `status` is NOT downgraded by it, because a healthy
  database with stale client code is not an outage;
* the failure-path hint: an ImportError/AttributeError while stale names the
  cause and says to restart, while a FRESH process's error text is unchanged
  (the pre-#8483 wire shape) and an unrelated exception never gets the hint.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from tortoise import code_currency


# ── the verdict ──────────────────────────────────────────────────────────────

def _fixture_tree(tmp_path: Path, offsets: dict[str, float]) -> tuple[Path, float]:
    """Build `<tmp>/tortoise/` with one file per name, mtime = instant + offset.

    Returns `(package_root, instant)`. The instant sits at `now`, so a negative
    offset is "already on disk before this process loaded it" and a positive one
    is "edited after".
    """
    root = tmp_path / "tortoise"
    root.mkdir()
    instant = time.time() - 60  # well clear of the filesystem's mtime granularity
    for name, offset in offsets.items():
        path = root / name
        path.write_text("# placeholder\n", encoding="utf-8")
        stamp = instant + offset
        os.utime(path, (stamp, stamp))
    return root, instant


def test_a_source_newer_than_the_load_instant_is_stale(tmp_path):
    root, instant = _fixture_tree(
        tmp_path, {"fresh.py": -30.0, "edited_after_load.py": 5.0}
    )
    count, files, error = code_currency.changed_sources(root, instant, ttl=0)
    assert error is None
    assert count == 1, "only the file edited after the load instant is stale"
    assert files == ["tortoise/edited_after_load.py"], (
        "the path is reported the way a reader would open it (root-relative)"
    )


def test_nothing_changed_means_not_stale(tmp_path):
    root, instant = _fixture_tree(tmp_path, {"a.py": -30.0, "b.py": -10.0})
    state = code_currency.staleness(root, instant, ttl=0)
    assert state["stale"] is False
    assert state["changed_count"] == 0
    assert state["changed_files"] == []
    assert state["scan_error"] is None


def test_a_file_the_process_never_imported_still_counts(tmp_path):
    """The scan walks the DIRECTORY, not `sys.modules`.

    The module that breaks a stale server is typically one that did not exist
    when the server started, so a `sys.modules`-based scan would miss exactly
    the case that caused #8483 (a function added to an existing file) and the
    case next to it (a brand-new module). A never-imported file must count.
    """
    root, instant = _fixture_tree(tmp_path, {"never_imported.py": 1.0})
    assert "tortoise/never_imported.py" not in list(
        m for m in __import__("sys").modules if "never_imported" in m
    )
    state = code_currency.staleness(root, instant, ttl=0)
    assert state["stale"] is True
    assert state["changed_files"] == ["tortoise/never_imported.py"]


def test_newest_change_is_named_first(tmp_path):
    root, instant = _fixture_tree(
        tmp_path, {"older_edit.py": 1.0, "newer_edit.py": 9.0}
    )
    _, files, _ = code_currency.changed_sources(root, instant, ttl=0)
    assert files == ["tortoise/newer_edit.py", "tortoise/older_edit.py"], (
        "newest first, so a truncated hint names the likeliest culprit"
    )


def test_a_missing_root_degrades_instead_of_raising(tmp_path):
    missing = tmp_path / "not_there"
    count, files, error = code_currency.changed_sources(missing, 0.0, ttl=0)
    assert count == 0 and files == []
    assert error is None or isinstance(error, str)


# ── the report ───────────────────────────────────────────────────────────────

def test_report_describes_this_process_and_the_loaded_checkout():
    block = code_currency.report()
    assert set(block) == {
        "pid",
        "revision",
        "stale",
        "changed_count",
        "changed_files",
        "loaded_at",
        "age_seconds",
        "scan_error",
    }
    assert block["pid"] == os.getpid()
    assert isinstance(block["stale"], bool)
    assert block["age_seconds"] >= 0
    # `revision` is orientation only: a wheel install has no `.git`, so None is a
    # legitimate answer and must never be what decides `stale`.
    assert block["revision"] is None or isinstance(block["revision"], str)


def test_report_of_a_fresh_process_is_not_stale():
    """The real package, this process: nothing has been edited under it."""
    code_currency.reset_cache()
    assert code_currency.report()["stale"] is False, (
        "a test process that started after the checkout must not report itself stale"
    )
    assert code_currency.stale_code_hint() is None


# ── the hint ────────────────────────────────────────────────────────────────

def test_the_hint_names_the_file_and_says_restart(monkeypatch):
    monkeypatch.setattr(
        code_currency,
        "staleness",
        lambda *a, **k: {
            "stale": True,
            "changed_count": 1,
            "changed_files": ["tortoise/vector_write.py"],
            "loaded_at": "2026-10-12T22:01:53+00:00",
            "age_seconds": 46400.0,
            "scan_error": None,
        },
    )
    hint = code_currency.stale_code_hint()
    assert hint is not None
    assert "tortoise/vector_write.py" in hint
    assert "12h53m" in hint, "the age is what tells a reader the server predates the fix"
    assert "Restart the MCP server" in hint
    assert "#8483" in hint, "the hint points at the defect class, not only at the symptom"


def test_the_hint_summarises_a_long_change_list(monkeypatch):
    monkeypatch.setattr(
        code_currency,
        "staleness",
        lambda *a, **k: {
            "stale": True,
            "changed_count": 4,
            "changed_files": [f"tortoise/m{i}.py" for i in range(4)],
            "loaded_at": "2026-10-12T22:01:53+00:00",
            "age_seconds": 120.0,
            "scan_error": None,
        },
    )
    hint = code_currency.stale_code_hint(limit=2)
    assert "4 package source files" in hint
    assert "tortoise/m0.py, tortoise/m1.py" in hint
    assert "+2 more" in hint


# ── the surface: health carries the block ────────────────────────────────────

@pytest.fixture()
def healthy_metrics(monkeypatch):
    """Stand in for the DB probe: this file is about the CLIENT half of health.

    Also opens the transport gate `_safe` applies to every tool (stdio + dev mode
    is the incident's shape, and the default in a test env with no API key).
    """
    from tortoise import mcp_server
    from tortoise.mcp_auth import _transport_mode

    monkeypatch.setattr(mcp_server.monitoring, "metrics", lambda **kwargs: {"status": "ok", "graph_size": 7})
    monkeypatch.setattr(mcp_server, "_get_org_sdk", lambda: object())
    monkeypatch.setattr(mcp_server, "_is_dev_mode", lambda: True)
    token = _transport_mode.set("stdio")
    yield mcp_server
    _transport_mode.reset(token)


def test_health_carries_the_code_block(healthy_metrics):
    report = healthy_metrics.tortoise_health()
    assert report["status"] == "ok", "the DB half is untouched"
    assert report["graph_size"] == 7
    assert report["code"]["pid"] == os.getpid()
    assert "stale" in report["code"]


def test_overview_health_section_carries_the_code_block(healthy_metrics):
    """The LIVE route: `tortoise_health` is a retired NAME, `tortoise_overview` runs."""
    report = healthy_metrics.tortoise_overview(section="health")
    assert report["status"] == "ok"
    assert report["code"]["pid"] == os.getpid()


def test_a_failed_health_report_keeps_its_failure_shape(healthy_metrics, monkeypatch):
    """`_SafeError` is a failure envelope matched by value at the telemetry seam."""
    def boom(**kwargs):
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(healthy_metrics.monitoring, "metrics", boom)
    report = healthy_metrics.tortoise_health()
    assert "probe exploded" in report["error"]
    assert "code" not in report, (
        "the failure envelope must not grow the client block — the #8135 seam "
        "discriminates on this value"
    )


# ── the failure path: a stale load explains itself ───────────────────────────

@pytest.fixture()
def stdio_dev_mode(monkeypatch):
    """The incident's transport: stdio, dev mode (no API key in the test env)."""
    from tortoise import mcp_server
    from tortoise.mcp_auth import _transport_mode

    monkeypatch.setattr(mcp_server, "_is_dev_mode", lambda: True)
    token = _transport_mode.set("stdio")
    yield mcp_server
    _transport_mode.reset(token)


def test_stale_import_error_names_the_staleness(stdio_dev_mode, monkeypatch):
    monkeypatch.setattr(
        code_currency,
        "stale_code_hint",
        lambda *a, **k: "code-stale (#8483): this MCP process is stale (tortoise/x.py)",
    )

    def stale_read():
        raise ImportError(
            "cannot import name 'stored_embedding_vector_expr' from 'tortoise.vector_write'"
        )

    result = stdio_dev_mode._safe(stale_read)
    assert result["error"].startswith(
        "cannot import name 'stored_embedding_vector_expr'"
    ), "the real error stays first — the hint never replaces it"
    assert "code-stale (#8483)" in result["error"]


def test_a_fresh_process_error_text_is_unchanged(stdio_dev_mode, monkeypatch):
    """No stale code -> byte-identical to the pre-#8483 message."""
    monkeypatch.setattr(code_currency, "stale_code_hint", lambda *a, **k: None)

    def boom():
        raise ImportError("cannot import name 'later_fix' from 'tortoise.mod'")

    assert stdio_dev_mode._safe(boom)["error"] == (
        "cannot import name 'later_fix' from 'tortoise.mod'"
    )


def test_an_unrelated_failure_never_gets_the_stale_hint(stdio_dev_mode, monkeypatch):
    """The trigger stays narrow: only the stale-load signatures are annotated."""
    monkeypatch.setattr(
        code_currency, "stale_code_hint", lambda *a, **k: "code-stale (#8483): stale"
    )

    def boom():
        raise ValueError("bad kind")

    result = stdio_dev_mode._safe(boom)
    assert result["error"] == "bad kind"
    assert "code-stale" not in result["error"]


def test_attribute_error_on_a_stale_module_gets_the_hint(stdio_dev_mode, monkeypatch):
    """The other stale-load shape: a missing attribute on an already-loaded module."""
    monkeypatch.setattr(
        code_currency, "stale_code_hint", lambda *a, **k: "code-stale (#8483): stale"
    )

    def boom():
        raise AttributeError("module 'tortoise.vector_write' has no attribute 'expr'")

    assert "code-stale (#8483)" in stdio_dev_mode._safe(boom)["error"]


# ── the record (CONTRIBUTING.md: an added response field is RECORDED) ────────

def test_the_code_field_is_recorded_and_rendered():
    """The carve-out covers the GATE, not the record.

    `check` anchors the entries that exist but cannot see a field nobody wrote
    down (nothing inspects response bodies at runtime), so the record has its own
    pin — the same shape as `test_the_8171_advisory_field_is_recorded_on_both_
    create_doors` in `tests/test_surface_manifest.py`.
    """
    import yaml

    repo = Path(__file__).resolve().parent.parent
    manifest = yaml.safe_load((repo / "config" / "surface-manifest.yml").read_text())
    recorded = {(rf["response"], rf["field"]) for rf in manifest["response_fields"]}
    assert ("tortoise_overview", "code") in recorded, (
        "the `code` block served on the health section is not recorded in "
        "config/surface-manifest.yml::response_fields"
    )
    rendered = (repo / "docs" / "product" / "mcp-sdk-surface.md").read_text(encoding="utf-8")
    assert "### Recorded response fields" in rendered
    assert "`code`" in rendered, "the record is not rendered into the list the owner reviews"

"""#2121 — the Hermes two-way seam (epic #2080 end-state platform).

Hermes (NousResearch/hermes-agent) is the ONE harness in the seam family whose
per-turn surface is a NATIVE PLUGIN rather than a shell command: the injection
contract is ``register(ctx)`` → ``ctx.register_hook("pre_llm_call", cb)`` and
the callback's RETURN value (``{"context": ...}``) is appended to the current
turn's user message (docs ``plugins/index.md`` §"``pre_llm_call`` context
injection"; ``agent/turn_context.py::_collect_pre_llm_call_context``). There is
therefore no registration file whose "command" can point at
``volunteer-turn.sh``, and this seam ships a directory plugin instead.

This file pins both directions of the seam, at the surfaces a regression would
actually move:

1. ``tortoise install hermes`` — the agent-first onboarding writes the shipped
   plugin (``plugin.yaml`` + ``__init__.py``) under
   ``<dir>/.hermes/plugins/tortoise-volunteer/``; it is idempotent, refuses a
   foreign directory of the same name, and ``--uninstall`` deletes only what it
   can prove it wrote.
2. The INSTALLED plugin, executed for real (imported from the installed path
   with a fake ``tortoise`` reflex on PATH): ``register()`` wires
   ``pre_llm_call``, the handler runs the shared reflex, returns
   ``{"context": block}``, passes the harness ``session_id`` through (the
   reflex's cross-turn re-mention suppression), bounds the block below
   Hermes' 10,000-char spill cap, and fails OPEN (no injection) on a missing
   reflex / the kill switch.
3. ``parse_hermes`` — the ingest half, over a REAL SQLite ``state.db`` fixture
   shaped from the store's own DDL (``hermes_state_common.py``): the live
   transcript is ``messages.active = 1`` ordered by ``id`` (``timestamp`` is
   NOT monotonic), generated compaction summaries and non-user/assistant roles
   are skipped, the default selection is the most recently started session,
   and the store is opened read-only (a backfill must never mutate a live
   Hermes store).
"""

from __future__ import annotations

import importlib.util
import os
import shlex
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tortoise.session_import import (
    PARSERS,
    parse_hermes,
    parse_transcript,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PY = sys.executable
_PLUGIN_MARKER = "tortoise-hermes-plugin"

# The subset of the Hermes store DDL this seam reads, kept faithful to
# ``hermes_state_common.py`` (column names/types + the ``active`` /
# ``_compressed_summary`` defaults the parser filters on).
_STATE_DB_DDL = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    started_at REAL NOT NULL,
    message_count INTEGER DEFAULT 0
);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL,
    content TEXT,
    tool_call_id TEXT,
    tool_calls TEXT,
    tool_name TEXT,
    timestamp REAL NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    compacted INTEGER NOT NULL DEFAULT 0,
    _compressed_summary INTEGER NOT NULL DEFAULT 0
);
"""


def _env(**extra) -> dict:
    env = {**os.environ, "TORTOISE_SECRET_PEPPER": "test-static-pepper"}
    env.update(extra)
    return env


def _run(argv, env, cwd: str | None = None):
    return subprocess.run(
        [_PY, "-m", "tortoise", *argv],
        env=env, cwd=cwd, capture_output=True, text=True, timeout=180,
    )


def _make_state_db(path: Path, sessions: dict[str, list[dict]],
                   started: dict[str, float] | None = None) -> Path:
    """Write a real Hermes-shaped ``state.db``.

    ``sessions``: {session_id: [message dicts]}; each message carries ``id``
    (insertion order), ``role``, ``content`` and optional ``active`` /
    ``summary`` flags.
    """
    started = started or {}
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_STATE_DB_DDL)
        for sid, msgs in sessions.items():
            conn.execute("INSERT INTO sessions (id, started_at) VALUES (?, ?)",
                         (sid, started.get(sid, 1_000.0)))
            for m in msgs:
                conn.execute(
                    "INSERT INTO messages (id, session_id, role, content, "
                    "timestamp, active, _compressed_summary) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (m["id"], sid, m["role"], m.get("content"),
                     m.get("ts", float(m["id"])),
                     1 if m.get("active", True) else 0,
                     1 if m.get("summary", False) else 0))
        conn.commit()
    finally:
        conn.close()
    return path


# ── 1. `tortoise install hermes` — the shipped plugin registration ───────


def _plugin_dir(root: Path) -> Path:
    return root / ".hermes" / "plugins" / "tortoise-volunteer"


def test_install_hermes_writes_shipped_plugin(tmp_path):
    r = _run(["install", "hermes", "--dir", str(tmp_path)], _env())
    assert r.returncode == 0, r.stderr
    target = _plugin_dir(tmp_path)
    manifest = (target / "plugin.yaml").read_text()
    init = (target / "__init__.py").read_text()
    # The injection contract Hermes' own doctor validates: the declared hook
    # must be the hook `register(ctx)` actually registers.
    assert "pre_llm_call" in manifest
    assert 'ctx.register_hook("pre_llm_call", on_pre_llm_call)' in init
    # Ownership marker — the thing --uninstall and the overwrite refusal key on.
    assert _PLUGIN_MARKER in init
    assert _PLUGIN_MARKER in manifest
    assert "Installed" in r.stdout


def test_install_hermes_is_idempotent_and_uninstalls(tmp_path):
    _run(["install", "hermes", "--dir", str(tmp_path)], _env())
    target = _plugin_dir(tmp_path)
    marker_text = (target / "__init__.py").read_text()
    # A reinstall UPDATES in place (never a second copy, never a refusal).
    r = _run(["install", "hermes", "--dir", str(tmp_path)], _env())
    assert r.returncode == 0, r.stderr
    assert "Updated" in r.stdout
    assert (target / "__init__.py").read_text() == marker_text
    # --uninstall removes the plugin, leaving the .hermes tree clean.
    r = _run(["install", "hermes", "--dir", str(tmp_path), "--uninstall"],
             _env())
    assert r.returncode == 0, r.stderr
    assert not target.exists()
    # Uninstalling again is a clean no-op, never an inverted reinstall.
    r = _run(["install", "hermes", "--dir", str(tmp_path), "--uninstall"],
             _env())
    assert r.returncode == 0, r.stderr
    assert not target.exists()


def test_install_hermes_dry_run_writes_nothing(tmp_path):
    r = _run(["install", "hermes", "--dir", str(tmp_path), "--dry-run"],
             _env())
    assert r.returncode == 0, r.stderr
    assert "[dry-run]" in r.stdout
    assert not _plugin_dir(tmp_path).exists()


def test_install_hermes_refuses_foreign_plugin_dir(tmp_path):
    """A `tortoise-volunteer` directory this installer did not write is never
    overwritten — and never deleted by --uninstall."""
    target = _plugin_dir(tmp_path)
    target.mkdir(parents=True)
    (target / "__init__.py").write_text("def register(ctx):\n    pass\n")
    user_bytes = (target / "__init__.py").read_text()
    r = _run(["install", "hermes", "--dir", str(tmp_path)], _env())
    assert r.returncode == 1
    assert "refusing to overwrite" in r.stderr.lower()
    assert (target / "__init__.py").read_text() == user_bytes
    r = _run(["install", "hermes", "--dir", str(tmp_path), "--uninstall"],
             _env())
    assert r.returncode == 1
    assert "refusing to delete" in r.stderr.lower()
    assert (target / "__init__.py").read_text() == user_bytes


def test_install_hermes_refuses_file_where_plugin_dir_belongs(tmp_path):
    target = _plugin_dir(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text("not a directory")
    r = _run(["install", "hermes", "--dir", str(tmp_path)], _env())
    assert r.returncode == 1
    assert "not a directory" in r.stderr
    assert target.read_text() == "not a directory"


def test_install_hermes_uninstall_keeps_unrelated_files(tmp_path):
    """Only the two files the installer wrote are removed; a file the USER
    added keeps the directory alive (an uninstall never deletes what it
    cannot prove it created)."""
    _run(["install", "hermes", "--dir", str(tmp_path)], _env())
    target = _plugin_dir(tmp_path)
    (target / "notes.txt").write_text("user data\n")
    r = _run(["install", "hermes", "--dir", str(tmp_path), "--uninstall"],
             _env())
    assert r.returncode == 0, r.stderr
    assert target.is_dir()
    assert (target / "notes.txt").read_text() == "user data\n"
    assert not (target / "__init__.py").exists()


# ── 2. The INSTALLED plugin — the real pre_llm_call seam ─────────────────


def _install_and_load(tmp_path, monkeypatch, *, block: str = "",
                      argv_file: Path | None = None, **env_extra):
    """Install the shipped plugin into a temp root and import THAT copy.

    A fake ``tortoise`` console script stands in for the reflex: it records
    its argv (so the test can prove ``--session-id`` is passed) and prints
    ``block``. The fallback candidates are cleared so nothing on the host can
    masquerade as the reflex.
    """
    r = _run(["install", "hermes", "--dir", str(tmp_path)], _env())
    assert r.returncode == 0, r.stderr
    plugin = _plugin_dir(tmp_path) / "__init__.py"

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "tortoise"
    script = "#!/bin/bash\n"
    if argv_file is not None:
        script += f'printf "%s\\n" "$@" > {shlex.quote(str(argv_file))}\n'
    script += "cat > /dev/null\n"
    if block:
        script += f"printf '%s\\n' {shlex.quote(block)}\n"
    fake.write_text(script)
    fake.chmod(0o755)

    monkeypatch.setenv("PATH", f"{bin_dir}:/bin:/usr/bin")
    monkeypatch.setenv("TORTOISE_SRC_DIR", "")
    monkeypatch.setenv("VIRTUAL_ENV", "")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for key, value in env_extra.items():
        monkeypatch.setenv(key, value)

    spec = importlib.util.spec_from_file_location(
        "tortoise_hermes_plugin_under_test", plugin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_BLOCK = "- **CI deploys on merge** → point/pt_ci123 — GitHub Actions on main"


def test_installed_plugin_registers_pre_llm_call(tmp_path, monkeypatch):
    module = _install_and_load(tmp_path, monkeypatch, block=_BLOCK)
    seen = []

    class _Ctx:
        def register_hook(self, name, cb):
            seen.append((name, cb))

    module.register(_Ctx())
    assert seen == [("pre_llm_call", module.on_pre_llm_call)]


def test_pre_llm_call_returns_context_and_passes_session_id(
        tmp_path, monkeypatch):
    argv_file = tmp_path / "argv.txt"
    module = _install_and_load(tmp_path, monkeypatch, block=_BLOCK,
                               argv_file=argv_file)
    out = module.on_pre_llm_call(
        user_message="How is CI deploy triggered?", session_id="sess-42")
    assert out == {"context": _BLOCK}
    argv = argv_file.read_text().split("\n")
    # The reflex is the SHARED `tortoise volunteer` CLI and carries the
    # harness session id — the reflex's cross-turn re-mention suppression.
    assert argv[0] == "volunteer"
    assert "--session-id" in argv and "sess-42" in argv


def test_pre_llm_call_no_session_id_still_injects(tmp_path, monkeypatch):
    module = _install_and_load(tmp_path, monkeypatch, block=_BLOCK)
    out = module.on_pre_llm_call(user_message="what do we know?")
    assert out == {"context": _BLOCK}


def test_pre_llm_call_bounds_the_block_below_the_hermes_spill_cap(
        tmp_path, monkeypatch):
    """Hermes spills >10,000-char hook context to disk and shows a preview;
    the seam bounds itself at 8,000 so the block is never spilled."""
    module = _install_and_load(tmp_path, monkeypatch, block="x" * 20_000)
    out = module.on_pre_llm_call(user_message="how is CI deployed?")
    assert out is not None
    assert len(out["context"]) == module.MAX_INJECT_CHARS == 8000


def test_pre_llm_call_fails_open_without_a_reflex(tmp_path, monkeypatch):
    """No `tortoise` anywhere → no injection, no raise, the turn proceeds."""
    module = _install_and_load(tmp_path, monkeypatch)  # no block, but a fake CLI
    # Remove the fake CLI: nothing resolvable.
    (tmp_path / "bin" / "tortoise").unlink()
    assert module.on_pre_llm_call(user_message="hi there") is None


def test_pre_llm_call_fails_open_when_the_reflex_fails(tmp_path, monkeypatch):
    module = _install_and_load(tmp_path, monkeypatch)
    fake = tmp_path / "bin" / "tortoise"
    fake.write_text("#!/bin/bash\nexit 1\n")
    fake.chmod(0o755)
    assert module.on_pre_llm_call(user_message="hi there") is None


def test_pre_llm_call_fails_open_on_empty_graph(tmp_path, monkeypatch):
    """A clean reflex run with NOTHING above the gate prints nothing — the
    documented content fail-open (no injection, exit 0)."""
    module = _install_and_load(tmp_path, monkeypatch)  # block="" → prints nothing
    assert module.on_pre_llm_call(user_message="hi there") is None


def test_pre_llm_call_kill_switch_skips_the_reflex(tmp_path, monkeypatch):
    argv_file = tmp_path / "argv.txt"
    module = _install_and_load(tmp_path, monkeypatch, block=_BLOCK,
                               argv_file=argv_file,
                               TORTOISE_VOLUNTEER_DISABLE="1")
    assert module.on_pre_llm_call(user_message="how is CI deployed?") is None
    assert not argv_file.exists()  # the reflex was never invoked


def test_plugin_resolves_the_installer_recorded_module_dir(tmp_path,
                                                           monkeypatch):
    """No `tortoise` on PATH → the plugin falls back to the module dir
    `tortoise install` records (`~/.tortoise/hook-src-dir`), the same chain
    the shipped shell hook walks."""
    module = _install_and_load(tmp_path, monkeypatch, block=_BLOCK)
    (tmp_path / "bin" / "tortoise").unlink()
    home = Path(os.environ["HOME"])
    src = tmp_path / "checkout"
    (src / ".venv" / "bin").mkdir(parents=True)
    console = src / ".venv" / "bin" / "tortoise"
    console.write_text("#!/bin/bash\ncat > /dev/null\nprintf '%s\\n' "
                       f"{shlex.quote(_BLOCK)}\n")
    console.chmod(0o755)
    (home / ".tortoise").mkdir(parents=True)
    (home / ".tortoise" / "hook-src-dir").write_text(str(src))
    assert module.on_pre_llm_call(user_message="how is CI deployed?") == {
        "context": _BLOCK}


# ── 3. `parse_hermes` — the ingest half over a real state.db ─────────────


def test_parse_hermes_live_transcript_in_id_order(tmp_path):
    """`active = 1` rows in insertion (id) order — NOT timestamp order, and
    rewound rows / generated summaries / non-conversation roles are skipped."""
    db = _make_state_db(tmp_path / "state.db", {"s1": [
        {"id": 1, "role": "user", "content": "Let's wire Hermes.",
         "ts": 900.0},
        {"id": 2, "role": "assistant", "content": "Agreed — the plugin first.",
         "ts": 100.0},
        {"id": 3, "role": "tool", "content": "tool noise", "ts": 200.0},
        {"id": 4, "role": "session_meta", "content": "meta", "ts": 300.0},
        {"id": 5, "role": "user", "content": "rewound turn",
         "active": False},
        {"id": 6, "role": "user", "content": "compaction summary",
         "summary": True},
        {"id": 7, "role": "assistant", "content": "   "},
        {"id": 8, "role": "user", "content": "Ship the parser too."},
    ]})
    assert parse_hermes(db) == [
        {"role": "user", "content": "Let's wire Hermes."},
        {"role": "assistant", "content": "Agreed — the plugin first."},
        {"role": "user", "content": "Ship the parser too."},
    ]


def test_parse_hermes_defaults_to_the_most_recent_session(tmp_path):
    db = _make_state_db(
        tmp_path / "state.db",
        {
            "old": [{"id": 1, "role": "user", "content": "old session"}],
            "new": [{"id": 2, "role": "user", "content": "new session"}],
        },
        started={"old": 1_000.0, "new": 2_000.0},
    )
    assert parse_hermes(db) == [
        {"role": "user", "content": "new session"}]


def test_parse_hermes_explicit_session_selector(tmp_path):
    db = _make_state_db(
        tmp_path / "state.db",
        {
            "old": [{"id": 1, "role": "user", "content": "old session"}],
            "new": [{"id": 2, "role": "user", "content": "new session"}],
        },
        started={"old": 1_000.0, "new": 2_000.0},
    )
    # `<state.db>::<session-id>` — the escape hatch for a specific session,
    # reached through the SHARED dispatch contract too.
    assert parse_transcript(f"{db}::old", "hermes") == [
        {"role": "user", "content": "old session"}]
    assert PARSERS["hermes"] is parse_hermes


def test_parse_hermes_reads_the_store_read_only(tmp_path):
    """A backfill must never mutate a Hermes store (never create -wal/-shm
    beside a live state.db)."""
    db = _make_state_db(tmp_path / "state.db", {"s1": [
        {"id": 1, "role": "user", "content": "hello Hermes"}]})
    before = db.read_bytes()
    assert parse_hermes(db)
    assert db.read_bytes() == before
    assert not (tmp_path / "state.db-wal").exists()
    assert not (tmp_path / "state.db-shm").exists()


@pytest.mark.parametrize("build,needle", [
    (lambda p: p / "missing.db", "session store not found"),
    (lambda p: _empty_store(p / "state.db"), "no sessions in Hermes"),
    (lambda p: _plain_file(p / "state.db"), "cannot read Hermes session"),
    (lambda p: _sqlite_without_tables(p / "state.db"), "cannot read Hermes session"),
])
def test_parse_hermes_errors_are_honest_value_errors(tmp_path, build, needle):
    with pytest.raises(ValueError) as exc:
        parse_hermes(build(tmp_path))
    assert needle in str(exc.value)


def test_parse_hermes_unknown_session_is_a_clean_error(tmp_path):
    db = _make_state_db(tmp_path / "state.db", {"s1": [
        {"id": 1, "role": "user", "content": "hi"}]})
    with pytest.raises(ValueError) as exc:
        parse_hermes(f"{db}::nope")
    assert "no Hermes session 'nope'" in str(exc.value)


def test_parse_hermes_accepts_the_hermes_home_directory(tmp_path):
    home = tmp_path / "hermes-home"
    home.mkdir()
    _make_state_db(home / "state.db", {"s1": [
        {"id": 1, "role": "user", "content": "from HERMES_HOME"}]})
    assert parse_hermes(home) == [
        {"role": "user", "content": "from HERMES_HOME"}]


def _empty_store(path: Path) -> Path:
    _make_state_db(path, {})
    return path


def _plain_file(path: Path) -> Path:
    path.write_text("this is not a sqlite database\n")
    return path


def _sqlite_without_tables(path: Path) -> Path:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE unrelated (x INTEGER)")
    conn.commit()
    conn.close()
    return path


# ── 4. CLI wiring — `sessions import --harness hermes` ──────────────────


def test_cli_accepts_the_hermes_harness(tmp_path):
    """The `--harness` choice is wired: a missing file is reported as a
    missing SESSION FILE (not an argparse rejection), which means `hermes`
    parsed and reached the import path."""
    r = _run(["sessions", "import", "--harness", "hermes",
              "--file", str(tmp_path / "missing.db")],
             _env(TORTOISE_CAPTURE="1", HOME=str(tmp_path / "home")))
    assert r.returncode == 1
    assert "Session file not found" in r.stderr
    assert "invalid choice" not in r.stderr


def test_install_list_mentions_the_hermes_seam(tmp_path):
    r = _run(["install", "--list"], _env())
    assert r.returncode == 0, r.stderr
    assert "install hermes" in r.stdout
    assert "pre_llm_call" in r.stdout

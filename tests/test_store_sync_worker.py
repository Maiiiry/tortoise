"""#3515 — the store-sync BACKSTOP, proven at PROCESS level.

The cursor's claims are about processes: a process that exits, and a later
process that resumes. An in-process call cannot produce them, so every row here
drives the real artifact through ``tortoise store-sync`` (a ``__main__.py``
subcommand, §C(a) of the issue) with ``subprocess.run`` and asserts side
effects — cursor bytes on disk and POST bodies against a REAL local HTTP server
(not a stubbed ``fetch``: the transport half of the two-part verification has to
be transport).

The node-level unit rows (cursor math, branch window, backoff schedule) live in
``tortoise/pi-hooks/store-sync.test.mjs`` and are run by
``test_node_suite_passes`` here, so the suite tests the ARTIFACT and never a
Python copy of it (§C(b),(e)).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PI_HOOKS = REPO_ROOT / "tortoise" / "pi-hooks"
WORKER = PI_HOOKS / "store-sync.ts"
CURSOR_STORE = PI_HOOKS / "cursor-store.ts"
NODE_SUITE = PI_HOOKS / "store-sync.test.mjs"
CAPTURE_TS = PI_HOOKS / "tortoise-capture.ts"

RECEIPT_SENTINEL = "RECEIPT_JSON:"


# ── the local capture server (a REAL transport for the verification) ───────


class CaptureServer:
    """A minimal ``/v1/sessions`` + ``/v1/sessions/install-probe`` collector.

    It records every body it RECEIVES, which is what makes the verification
    two-part rather than self-reported: the client's own "I posted" is not
    evidence; the server's stored copy is.
    """

    def __init__(self) -> None:
        self.sessions: list[dict] = []
        self.probes: list[dict] = []
        self.status = 200
        self.calls = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # the BaseHTTPRequestHandler protocol name
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                try:
                    body = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    body = {"__unparsable__": True}
                outer.calls += 1
                if self.path.endswith("/install-probe"):
                    outer.probes.append(body)
                else:
                    outer.sessions.append(body)
                payload = json.dumps({"ok": outer.status < 300}).encode()
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args) -> None:  # silence
                return

        self._httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


@pytest.fixture()
def server():
    srv = CaptureServer()
    try:
        yield srv
    finally:
        srv.close()


def _env(server: CaptureServer) -> dict[str, str]:
    env = dict(os.environ)
    env["TORTOISE_API_KEY"] = "test-key-3515"
    env["TORTOISE_API_URL"] = server.url
    # Never let a real hosted config leak into a test run.
    env.pop("TORTOISE_CAPTURE", None)
    return env


def _run_sync(tmp: Path, *args: str, server: CaptureServer, extra_env=None):
    cmd = [
        sys.executable, "-m", "tortoise", "store-sync",
        "--no-spool",
        "--session-dir", str(tmp / "sessions"),
        "--cursor-dir", str(tmp / "cursor"),
        "--json",
        *args,
    ]
    proc = subprocess.run(
        cmd, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=180,
        env={**_env(server), **(extra_env or {})},
    )
    receipt = None
    for line in reversed((proc.stdout or "").splitlines()):
        if line.startswith(RECEIPT_SENTINEL):
            receipt = json.loads(line[len(RECEIPT_SENTINEL):])
            break
    if receipt is None:
        # `tortoise store-sync` composes the spool half with the worker half and
        # prints ONE document (the worker's `RECEIPT_JSON:` line is consumed by
        # `tortoise/store_sync.py`, and unwrapped into this shape).
        try:
            receipt = json.loads(proc.stdout or "")
        except json.JSONDecodeError:
            receipt = None
    assert receipt is not None, f"no receipt\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr}"
    return proc, receipt


# ── fixtures ──────────────────────────────────────────────────────────────


def _header(session_id: str) -> str:
    return json.dumps({
        "type": "session", "version": 3, "id": session_id,
        "timestamp": "2026-10-23T10:00:00.000Z", "cwd": "/tmp/x",
    })


def _message(entry_id: str, parent: str | None, role: str, text: str, ts: str = "") -> str:
    return json.dumps({
        "type": "message", "id": entry_id, "parentId": parent,
        "timestamp": ts or "2026-10-23T10:00:01.000Z",
        "message": {"role": role, "content": [{"type": "text", "text": text}]},
    })


def _session_file(tmp: Path, name: str, lines: list[str]) -> Path:
    d = tmp / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _cursor(tmp: Path) -> dict:
    path = tmp / "cursor" / "cursor.json"
    if not path.exists():
        return {"files": {}}
    return json.loads(path.read_text(encoding="utf-8"))


# ── §C(e): the artifact guard ─────────────────────────────────────────────


def test_the_worker_artifact_exists_and_is_what_the_suite_drives():
    """Fails if `store-sync.ts` is deleted, so the suite cannot drift into
    testing a Python copy of it (§C(e))."""
    assert WORKER.exists(), f"the store-sync worker is missing: {WORKER}"
    assert CURSOR_STORE.exists(), f"the cursor store is missing: {CURSOR_STORE}"
    source = WORKER.read_text(encoding="utf-8")
    # The pinned rules must live in the ARTIFACT, not only in this docstring.
    for pin in (
        "advance-on-2xx",  # case-insensitive marker of the cursor rule
        "cursor_past_eof",
        "file_replaced",
        "activeBranch",
        "LANE = \"store_sync\"",
    ):
        assert pin.lower() in source.lower(), f"pinned rule missing from the worker: {pin}"


def test_the_worker_does_not_carry_a_second_payload_builder():
    """ONE capture path: the worker must import the hook's payload builder
    rather than re-deriving a payload shape (the 'fourth copy' failure)."""
    source = WORKER.read_text(encoding="utf-8")
    assert "./tortoise-capture.ts" in source
    for imported in ("buildCapturePayload", "extractTurns", "postCapture"):
        assert imported in source, f"the worker must reuse {imported} from the hook"


def test_the_capture_lane_vocabulary_is_mirrored_in_the_ts_leg():
    """The TS mirror must equal the server's closed set, or a lane the client
    emits draws a 422 the client cannot see it caused."""
    from tortoise.hosted_api import (
        _SESSION_CAPTURE_LANE_VALUES,
        _SESSION_CAPTURED_AT_SOURCE_VALUES,
    )

    source = CAPTURE_TS.read_text(encoding="utf-8")
    for lane in _SESSION_CAPTURE_LANE_VALUES:
        assert f'"{lane}"' in source, f"lane {lane} missing from the TS mirror"
    for source_name in _SESSION_CAPTURED_AT_SOURCE_VALUES:
        assert f'"{source_name}"' in source, (
            f"client_captured_at_source {source_name} missing from the TS mirror"
        )
    # #3515 piece 12: the store-sync lane's own clock is a named source.
    assert "entry_timestamp" in _SESSION_CAPTURED_AT_SOURCE_VALUES


# ── the node-level unit rows (§C(b)) ──────────────────────────────────────


def test_node_suite_passes():
    """The node rows, run the way CI runs them: no ambient credential, no
    `~/.pi/agent/tortoise-config.json`, and a Node that can run TypeScript
    without a flag.

    #3721 is the reason for every word of that. `runStoreSync` resolves its
    credential from `TORTOISE_API_KEY` / the Pi config file, so with the
    INHERITED environment the suite is a coin flip on the machine: on a
    developer box it passed while asserting the developer's own config, and on
    a clean runner the empty key short-circuited the POST before the injected
    `fetch` was ever called (`0 !== 1`). The remedy is the sibling's, applied
    here in both places — a scrubbed env for the runner (so a local run
    reproduces CI) and `HERMETIC` at every call site inside the suite.
    """
    from test_pi_capture_hooks import _node_supports_ts, _scrubbed_env

    node = shutil.which("node")
    if not node:
        pytest.skip("node is absent")
    if not _node_supports_ts(node):
        pytest.skip("node < 22.18 does not enable TypeScript type stripping by "
                    "default — the source pins above still ran")
    with tempfile.TemporaryDirectory() as fake_home:
        proc = subprocess.run(
            [node, "--test", str(NODE_SUITE)],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=300,
            env=_scrubbed_env(fake_home),
        )
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-2000:]
    assert "fail 0" in proc.stdout, proc.stdout[-2000:]


# ── the two-part verification, part (i): TRANSPORT ────────────────────────


def test_part_i_install_probe_reaches_the_server_and_is_read_back(server, tmp_path):
    """Part (i) of the two-part verification: the install probe → 2xx + a
    server-side key read-back.

    Driven through the SHIPPED extension (``postInstallProbe``) so the artifact
    that runs on a user's machine is the thing proven, and against a REAL HTTP
    server so the leg includes network, headers, path and payload shape. A
    probe is synthetic: it proves transport and CANNOT prove the hook fires on
    real sessions — part (ii) is the other half, and neither substitutes for
    the other.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node is absent")
    probe_runner = tmp_path / "probe.mjs"
    probe_runner.write_text(
        "import { postInstallProbe } from "
        f"{json.dumps(CAPTURE_TS.as_uri())};\n"
        "const cfg = { apiKey: 'test-key-3515', apiUrl: process.env.PROBE_URL };\n"
        "const res = await postInstallProbe(cfg);\n"
        "console.log('PROBE_RESULT:' + JSON.stringify(res));\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [node, str(probe_runner)],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "PROBE_URL": server.url},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = None
    for line in proc.stdout.splitlines():
        if line.startswith("PROBE_RESULT:"):
            result = json.loads(line[len("PROBE_RESULT:"):])
    assert result is not None, proc.stdout
    assert result["ok"] is True and result["status"] == 200, result

    # The read-back: the SERVER holds a probe for this harness (the client's
    # own success is not the evidence).
    assert server.probes, "the server received no install probe"
    assert server.probes[-1]["harness"] == "pi"


# ── the two-part verification, part (ii): REAL-SESSION CAPTURE ────────────


def test_part_ii_a_real_pi_session_file_is_captured_and_attributed(server, tmp_path):
    """Part (ii): real Pi OUTPUT — the product's own file, not a synthetic
    writer — lands in the store, with its ``session_id`` equal to the header
    ``id`` and its lane the store-sync backstop's.

    The file is COPIED out of ``~/.pi/agent/sessions`` into ``tmp_path`` and
    driven from there. Two reasons, both from review:

    * the row used to read the developer's REAL store and POST real
      conversation content to a local collector — the product's transcript
      leaving a machine to prove a mechanism the pinned fixtures above already
      prove;
    * it picked ``max(mtime)``, i.e. on a working box THE LIVE SESSION, and then
      hard-asserted that its header id equals the filename UUID — a statement
      about Pi's FILENAMING convention, measured on one machine, in a test for a
      module with no opinion about filenames. A resumed/forked session whose
      header id differs from the filename reds it for a reason unrelated to
      store-sync.

    Where no real session exists on the machine the row SKIPS with the reason
    (it is an ops check there, never a fake pass). The copy keeps the property
    that matters: the bytes are what Pi wrote.
    """
    real_root = Path.home() / ".pi" / "agent" / "sessions"
    real_files = sorted(real_root.rglob("*.jsonl")) if real_root.is_dir() else []
    header = None
    source = None
    for candidate in real_files:
        try:
            with candidate.open(encoding="utf-8") as fh:
                first = fh.readline()
            parsed = json.loads(first)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict) and parsed.get("type") == "session" and parsed.get("id"):
            header, source = parsed, candidate
            break  # a REAL header is enough: the shape is what is being proved
    if header is None:
        pytest.skip(f"no usable real Pi session file under {real_root}")

    (tmp_path / "sessions").mkdir(parents=True, exist_ok=True)
    target = tmp_path / "sessions" / "copied.jsonl"
    target.write_bytes(source.read_bytes())

    _proc, receipt = _run_sync(tmp_path, "--file", str(target), server=server)
    log = receipt["log"]
    assert log["errors"] == [], log["errors"]
    if not server.sessions:
        # The copied session may simply hold no shippable turn (the live one was
        # mid-answer): that is not a store-sync defect, so it is reported rather
        # than asserted — the row's claim is about the HEADER/lane, and a file
        # with nothing to ship cannot carry it.
        pytest.skip(f"the copied real session has no shippable turn yet: {log}")
    body = server.sessions[-1]
    assert body["session_id"] == header["id"], (
        "piece 6: session_id must come from the on-disk header id, never a "
        "generated one"
    )
    assert body["capture_lane"] == "store_sync", "piece 8: the lane discriminator"
    assert body["harness"] == "pi"
    assert body["conversation"], "a real session must carry turns"
    assert isinstance(body.get("client_captured_at"), (int, float)), "piece 12"


# ── cursor behaviours, at PROCESS level ──────────────────────────────────


def test_cursor_survives_a_restart_and_does_not_re_emit(server, tmp_path):
    """A restart resumes from the cursor: the second process ships NOTHING."""
    path = _session_file(tmp_path, "a.jsonl", [
        _header("sid-restart"),
        _message("u1", None, "user", "hello"),
        _message("a1", "u1", "assistant", "hi"),
    ])
    _, first = _run_sync(tmp_path, server=server)
    assert first["log"]["shipped"] == 1
    assert len(server.sessions) == 1
    cursor_after_first = _cursor(tmp_path)["files"][str(path)]["offset"]
    assert cursor_after_first == path.stat().st_size

    server.calls = 0
    _, second = _run_sync(tmp_path, server=server)
    assert second["log"]["shipped"] == 0, "a restart must not re-ship"
    assert second["log"]["skipped"] == 1
    assert server.calls == 0, "no POST at all on the resumed run"
    assert _cursor(tmp_path)["files"][str(path)]["offset"] == cursor_after_first


def test_a_half_written_tail_is_neither_consumed_nor_lost(server, tmp_path):
    """The trailing line is incomplete: the cursor must stay BEHIND it, and the
    completed line must ship on the next process."""
    path = _session_file(tmp_path, "a.jsonl", [
        _header("sid-tail"),
        _message("u1", None, "user", "first"),
    ])
    partial = '{"type":"message","id":"a1","parentId":"u1","message":{"role":"assistant","cont'
    with path.open("a", encoding="utf-8") as fh:
        fh.write(partial)

    _, first = _run_sync(tmp_path, server=server)
    assert first["log"]["shipped"] == 1
    assert [t["content"] for t in server.sessions[-1]["conversation"]] == ["first"]
    offset = _cursor(tmp_path)["files"][str(path)]["offset"]
    assert offset < path.stat().st_size, (
        "the cursor advanced past a half-written line — that is the loss the "
        "partial-tail rule exists to forbid"
    )

    with path.open("a", encoding="utf-8") as fh:
        fh.write('ent":[{"type":"text","text":"second"}]}}\n')
    _, second = _run_sync(tmp_path, server=server)
    assert second["log"]["shipped"] == 1
    assert [t["content"] for t in server.sessions[-1]["conversation"]] == ["first", "second"]


def test_a_truncated_file_is_re_anchored_not_silently_skipped(server, tmp_path):
    """``cursor > file_size`` (Pi's ``_rewriteFile`` truncates then rewrites) —
    the normal degenerate case. It must re-read from the header."""
    path = _session_file(tmp_path, "a.jsonl", [
        _header("sid-trunc"),
        _message("u1", None, "user", "one"),
        _message("a1", "u1", "assistant", "two"),
    ])
    _run_sync(tmp_path, server=server)
    before = _cursor(tmp_path)["files"][str(path)]["offset"]
    assert before > 0

    path.write_text(
        "\n".join([_header("sid-trunc"), _message("u1", None, "user", "one")]) + "\n",
        encoding="utf-8",
    )
    assert path.stat().st_size < before

    server.calls = 0
    _, second = _run_sync(tmp_path, server=server)
    assert server.calls >= 1, "a silent skip would have made zero POSTs"
    assert [t["content"] for t in server.sessions[-1]["conversation"]] == ["one"]
    assert second["log"]["files"][0]["action"] == "ship"
    assert _cursor(tmp_path)["files"][str(path)]["offset"] == path.stat().st_size


def test_a_failing_transport_does_not_advance_the_cursor(server, tmp_path):
    """At-least-once: no 2xx, no advance — and a later run delivers it."""
    path = _session_file(tmp_path, "a.jsonl", [
        _header("sid-fail"),
        _message("u1", None, "user", "must survive"),
    ])
    server.status = 503
    _, first = _run_sync(tmp_path, server=server)
    assert first["log"]["shipped"] == 0
    assert first["log"]["files"][0]["action"] == "failed"
    assert _cursor(tmp_path)["files"][str(path)]["offset"] == 0

    server.status = 200
    _, second = _run_sync(tmp_path, server=server)
    assert second["log"]["shipped"] == 1
    assert [t["content"] for t in server.sessions[-1]["conversation"]] == ["must survive"]


def test_a_permanent_4xx_is_dead_lettered_and_counts_the_drop(server, tmp_path):
    """A permanently refused file must not head-of-line-block the next one, and
    the loss must be COUNTED (never a silent drop)."""
    _session_file(tmp_path, "a-bad.jsonl", [_header("sid-bad"), _message("u1", None, "user", "x")])
    _session_file(tmp_path, "b-good.jsonl", [_header("sid-good"), _message("v1", None, "user", "y")])
    server.status = 422
    _, receipt = _run_sync(tmp_path, "--max-files", "1", server=server)
    assert receipt["log"]["deadLettered"] == 1
    assert receipt["log"]["dropped"] >= 1
    letters = receipt["log"]["deadLetters"]
    assert letters and letters[0]["sessionId"] == "sid-bad"

    server.status = 200
    _, second = _run_sync(tmp_path, server=server)
    assert second["log"]["shipped"] == 1, "the second file still ships"
    assert server.sessions[-1]["session_id"] == "sid-good"


def test_shipped_turns_match_the_logs_own_content_and_branch(server, tmp_path):
    """The window is the ACTIVE BRANCH: an abandoned branch is not shipped."""
    _session_file(tmp_path, "a.jsonl", [
        _header("sid-branch"),
        _message("u1", None, "user", "question one"),
        _message("a1", "u1", "assistant", "answer one"),
        _message("u2", "a1", "user", "abandoned question"),
        _message("a2", "u2", "assistant", "abandoned answer"),
        _message("u3", "a1", "user", "live question"),
        _message("a3", "u3", "assistant", "live answer"),
    ])
    _run_sync(tmp_path, server=server)
    contents = [t["content"] for t in server.sessions[-1]["conversation"]]
    assert contents == ["question one", "answer one", "live question", "live answer"]
    assert "abandoned question" not in contents


def test_the_sweep_is_bounded_across_runs(server, tmp_path):
    for i in range(4):
        _session_file(tmp_path, f"s{i}.jsonl", [
            _header(f"sid-{i}"), _message(f"u{i}", None, "user", f"t{i}"),
        ])
    _, first = _run_sync(tmp_path, "--max-files", "2", server=server)
    assert first["log"]["shipped"] == 2
    _, second = _run_sync(tmp_path, "--max-files", "2", server=server)
    assert second["log"]["shipped"] == 2, "the remainder drains on the NEXT run"
    _, third = _run_sync(tmp_path, server=server)
    assert third["log"]["shipped"] == 0


def test_the_cli_never_invents_a_session_id(server, tmp_path):
    _session_file(tmp_path, "a.jsonl", [_message("u1", None, "user", "no header")])
    proc, receipt = _run_sync(tmp_path, server=server)
    assert receipt["log"]["files"][0]["reason"] == "no_header_id"
    assert server.calls == 0, "an invented id would have produced a POST"
    assert proc.returncode == 1, "a structurally unusable file is reported, not swallowed"


# ── the CREDENTIAL is one chain (review: split-brain key/URL) ─────────────


def test_the_cli_resolved_credential_reaches_the_log_half(server, tmp_path):
    """A key written by `tortoise init --api-key` lands in
    ``~/.tortoise/credentials.json`` — a store the WORKER never reads.

    `run_sweep` resolved that key for the spool half and forwarded only the
    URL, so the log half — the half this PR exists for — resolved its OWN chain
    (env → ``~/.pi/agent/tortoise-config.json``), found nothing, and reported
    "no TORTOISE_API_KEY configured" forever. That is the #2369 D1.1 split-brain
    in both directions: nothing shipped, and where a key did exist it was paired
    with a URL from a different chain.

    MUTATION THAT REDS THIS: drop `api_key=api_key` from the `run_worker` call
    in `run_sweep` (or the `env` hand-over in `run_worker`) → the receipt's log
    half carries `no TORTOISE_API_KEY configured` and ships 0.
    """
    home = tmp_path / "home"
    (home / ".tortoise").mkdir(parents=True)
    (home / ".tortoise" / "credentials.json").write_text(
        json.dumps({"api_key": "tt_from_file_store", "api_url": server.url}),
        encoding="utf-8",
    )
    _session_file(tmp_path, "a.jsonl", [
        _header("sid-cred"), _message("u1", None, "user", "from the file store"),
    ])
    # NO env key: the credential exists ONLY in the user-global store.
    _, receipt = _run_sync(tmp_path, server=server, extra_env={
        "HOME": str(home),
        "TORTOISE_API_KEY": "",
        "TORTOISE_API_URL": "",
    })
    log = receipt["log"]
    assert log["errors"] == [], log["errors"]
    assert log["shipped"] == 1, f"the log half never got the key: {log}"
    assert server.sessions[-1]["conversation"][0]["content"] == "from the file store"


def test_the_worker_gets_the_key_in_its_ENVIRONMENT_not_its_argv(monkeypatch):
    """A credential on argv is readable by every process on the box (`ps`)."""
    from tortoise import store_sync

    seen: dict = {}

    class _Proc:
        returncode = 0
        stdout = 'RECEIPT_JSON:{"shipped":0,"skipped":0,"errors":[],"files":[]}'
        stderr = ""

    def _run(argv, **kwargs):
        seen["argv"] = argv
        seen["env"] = kwargs.get("env")
        return _Proc()

    monkeypatch.setattr(store_sync.subprocess, "run", _run)
    receipt = store_sync.run_worker(
        session_dir="/tmp/s", cursor_dir="/tmp/c", api_key="tt_secret",
        api_url="https://api.example", node="/usr/bin/node",
    )
    assert receipt["ok"] is True
    assert "tt_secret" not in " ".join(seen["argv"]), "the key must not be in argv"
    assert seen["env"]["TORTOISE_API_KEY"] == "tt_secret"
    assert seen["env"]["TORTOISE_API_URL"] == "https://api.example", (
        "the URL must come from the SAME chain as the key (co-source, #2369 D1.1)"
    )


# ── the SPOOL half: consent, credential, and the two filters ──────────────


def _spool_fixture(monkeypatch, tmp_path, session_ids=("sess-live", "sess-dead")):
    """A real spool under tmp_path with one entry per session id."""
    from tortoise.capture_spool import Snapshot, write_spool_entry

    root = tmp_path / "spool"
    monkeypatch.setenv("TORTOISE_CAPTURE_SPOOL_DIR", str(root))
    for sid in session_ids:
        write_spool_entry(root, Snapshot(
            session_id=sid,
            turns=[{"role": "user", "content": f"hello from {sid}"},
                   {"role": "assistant", "content": "and back"}],
            source=f"{sid}.pi.jsonl",
            machine_id="m" * 64,
            harness="pi",
        ))
    return root


def test_the_spool_half_files_when_consented(monkeypatch, tmp_path):
    """The spool leg really runs — the headline claim ("one command, both
    substrates"), which had NO row at all: every process row passes
    `--no-spool`, so a regression there was green."""
    from tortoise.store_sync import run_sweep

    posted: list[dict] = []

    def post(payload):
        from tortoise.capture_spool import PostOutcome
        posted.append(payload)
        return PostOutcome(ok=True, status=200, body={"session_id": payload["session_id"]})

    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    _spool_fixture(monkeypatch, tmp_path, session_ids=("sess-dead",))
    receipt = run_sweep(spool=True, post=post, node="/nonexistent/node")
    assert receipt["spool"]["filed"] == 1, receipt["spool"]
    assert [p["session_id"] for p in posted] == ["sess-dead"]
    # And the summary keys are read off a REAL FlushSummary — not a stub.
    for key in ("filed", "deferred", "skipped", "held_back", "held_by_backoff",
                "terminal", "discarded"):
        assert key in receipt["spool"], f"{key} missing from the spool summary"


def test_the_spool_half_is_CONSENT_GATED(monkeypatch, tmp_path):
    """`tortoise store-sync` is the FOURTH upload path.

    Every other transmitting primitive (`session capture`, `sessions import`,
    `session drain`) refuses without consent (#3615); this one POSTed the spool
    anyway. The spool write itself is deliberately ungated (consent authorizes
    transmission, not durability), so a host that never consented has a POPULATED
    spool — and this command shipped it.

    MUTATION THAT REDS THIS: delete the `capture_consent_enabled()` branch in
    `run_sweep` → the entry below is posted.
    """
    from tortoise.store_sync import run_sweep

    posted: list[dict] = []

    def post(payload):
        from tortoise.capture_spool import PostOutcome
        posted.append(payload)
        return PostOutcome(ok=True, status=200, body={})

    monkeypatch.delenv("TORTOISE_CAPTURE", raising=False)
    _spool_fixture(monkeypatch, tmp_path, session_ids=("sess-unconsented",))
    receipt = run_sweep(spool=True, post=post, node="/nonexistent/node")
    assert posted == [], "a host that never consented had its spool uploaded"
    assert receipt["spool"]["status"] == "declined", receipt["spool"]
    assert "consent" in receipt["spool"]["detail"]


def test_the_spool_half_reports_no_credential_without_posting(monkeypatch, tmp_path):
    from tortoise.store_sync import run_sweep

    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    _spool_fixture(monkeypatch, tmp_path, session_ids=("sess-nocred",))
    receipt = run_sweep(spool=True, api_key=None, node="/nonexistent/node")
    assert receipt["spool"]["status"] == "no_credential", receipt["spool"]
    assert receipt["ok"] is False, "a substrate that was NOT drained is not a success"


def test_a_dry_run_drains_NO_spool_entry_and_stamps_NOTHING(monkeypatch, tmp_path):
    """`--dry-run` is "Plan only — POST nothing, move no cursor" (`--help`).

    `dry_run` reached `run_worker` and nothing else, so the DEFAULT (spool on)
    mode uploaded every spooled transcript — a real `flush_spool` transport, not
    a stub — and then stamped the entry's outcome (`filed_key`, backoff, or a
    terminal marker). The plan could not even be reproduced afterwards: the
    entries it reported on had changed.

    The spool is snapshotted byte-for-byte, so this reds on ANY mutation (a POST
    is only the loudest one) — and the follow-up real sweep proves the entry is
    still filable, i.e. nothing was consumed.

    MUTATION THAT REDS THIS: drop the `spool and dry_run` branch in `run_sweep`.
    """
    from tortoise.store_sync import run_sweep

    posted: list[dict] = []

    def post(payload):
        from tortoise.capture_spool import PostOutcome
        posted.append(payload)
        return PostOutcome(ok=True, status=200, body={})

    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    root = _spool_fixture(monkeypatch, tmp_path, session_ids=("sess-dry",))

    def snapshot() -> dict[str, bytes]:
        return {
            str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*"))
            if p.is_file()
        }

    before = snapshot()
    receipt = run_sweep(spool=True, dry_run=True, post=post, node="/nonexistent/node")
    assert posted == [], "a plan-only run POSTed spooled transcript turns"
    assert receipt["spool"]["status"] == "dry_run", receipt["spool"]
    assert snapshot() == before, "a plan-only run mutated the spool it was reporting on"

    # And the entry is untouched, not merely quiet: a REAL sweep still files it.
    real = run_sweep(spool=True, post=post, node="/nonexistent/node")
    assert [p["session_id"] for p in posted] == ["sess-dry"], (
        "the dry run consumed the entry it was supposed to report on"
    )
    assert real["spool"]["filed"] == 1


def test_both_halves_honour_exclude_session_id_and_file(monkeypatch, tmp_path):
    """`--exclude-session-id` reaches the SPOOL leg too.

    It was forwarded only to the log half, so `tortoise store-sync
    --exclude-session-id <live>` filed the LIVE session from the spool anyway —
    after which the server skips extraction for every later turn of it. The
    command's own help documents exactly that consequence. `--file` had the
    same shape (forwarded only to the worker).

    MUTATION THAT REDS THIS: drop `exclude_session_id=` from either `flush_spool`
    call in `run_sweep` → the live session is posted.
    """
    from tortoise.store_sync import run_sweep

    posted: list[dict] = []

    def post(payload):
        from tortoise.capture_spool import PostOutcome
        posted.append(payload)
        return PostOutcome(ok=True, status=200, body={})

    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    _spool_fixture(monkeypatch, tmp_path, session_ids=("sess-live", "sess-dead"))
    run_sweep(spool=True, post=post, exclude_session_id="sess-live",
              node="/nonexistent/node")
    assert [p["session_id"] for p in posted] == ["sess-dead"], (
        "the LIVE session was filed from the spool"
    )

    # `--file` names one session for the LOG half; the spool half must narrow to
    # that same session rather than draining everything.
    posted.clear()
    run_sweep(spool=True, post=post,
              files=[str(tmp_path / "2026-10-23T10-00-00Z_sess-live.jsonl")],
              node="/nonexistent/node")
    assert [p["session_id"] for p in posted] == ["sess-live"], posted


# ── the worker's skip path, and the cursor it must not touch ──────────────


def test_dry_run_moves_no_cursor_at_process_level(server, tmp_path):
    """The CLI advertises "Plan only — POST nothing, move no cursor".

    The no-turns branch advanced the cursor unconditionally, so a `--dry-run`
    over a session with no shippable turn yet wrote `cursor.json` and moved an
    offset — a durable side effect from a command documented as read-only.

    MUTATION THAT REDS THIS: remove the `if (dryRun)` guard above the
    `!turns.length` branch in `store-sync.ts`, or the `if (!dryRun)` around the
    final `saveCursor`.
    """
    _session_file(tmp_path, "a.jsonl", [
        _header("sid-dryrun"),
        json.dumps({"type": "label", "id": "l1", "parentId": None,
                    "timestamp": "2026-10-23T10:00:01.000Z", "label": "cp"}),
    ])
    _proc, receipt = _run_sync(tmp_path, "--dry-run", server=server)
    assert server.calls == 0, "a dry run POSTed"
    assert receipt["log"]["noTurns"] == 1, receipt["log"]
    assert not (tmp_path / "cursor" / "cursor.json").exists(), (
        "a report-only run created the cursor store"
    )


def test_an_up_to_date_file_is_skipped_without_a_second_full_read():
    """Source pin: the sweep reads the HEADER LINE before the whole file.

    A `session_start` sweep runs over the user's entire session history, so the
    full `readFileSync` + per-line `JSON.parse` in `scanSessionFile` must sit
    BEHIND the skip decision — otherwise every start re-reads the whole tree (a
    fleet box: gigabytes, per start, times concurrent sessions). This is a
    STRUCTURAL pin: a behavioural one would need a byte counter inside the
    worker, and the worker deliberately takes no test seam for its reads. It
    fails if the ordering is reversed or the cheap path is deleted.
    """
    source = WORKER.read_text(encoding="utf-8")
    loop = source[source.index("for (const path of candidates)"):]
    head_at = loop.index("scanHeaderLine(")
    plan_at = loop.index("planFile(")
    full_at = loop.index("scanSessionFile(")
    assert head_at < plan_at < full_at, (
        "the header-only read and the skip decision must come BEFORE the full "
        "scan: a sweep must not read a file it is going to skip"
    )
    cheap = loop[:full_at]
    assert 'action === "skip"' in cheap and "continue;" in cheap, (
        "the cheap skip branch (no full read) is missing"
    )

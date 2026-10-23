/**
 * #3515 — the store-sync backstop's own suite (`node --test`).
 *
 * These are the rows that make each pinned behaviour DISCRIMINATING: every
 * test here fails if the rule it names is removed. Run by
 * `tests/test_store_sync_worker.py`, which is CI-selected for a change under
 * `tortoise/pi-hooks/`.
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { chmodSync, mkdtempSync, readFileSync, statSync, writeFileSync, appendFileSync, mkdirSync, renameSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  activeBranch,
  classifyStatus,
  cutAtCompaction,
  planFile,
  readCompleteLines,
  runStoreSync,
  scanSessionFile,
  windowCapturedAt,
  windowTurns,
  BACKOFF_MS,
  LANE,
} from "./store-sync.ts";
import {
  backupPath,
  cursorPath,
  loadCursor,
  saveCursor,
  emptyCursor,
} from "./cursor-store.ts";

// ── helpers ───────────────────────────────────────────────────────────────

const HEADER = (id) => JSON.stringify({ type: "session", version: 3, id, timestamp: "2026-10-23T10:00:00.000Z", cwd: "/tmp/x" });

function entry(id, parentId, role, text, extra = {}) {
  return JSON.stringify({
    type: "message",
    id,
    parentId,
    timestamp: extra.timestamp ?? "2026-10-23T10:00:01.000Z",
    message: { role, content: [{ type: "text", text }] },
  });
}

function sessionFixture(dir, name, lines) {
  const path = join(dir, name);
  mkdirSync(dir, { recursive: true });
  writeFileSync(path, `${lines.join("\n")}\n`);
  return path;
}

function tmp(prefix) {
  return mkdtempSync(join(tmpdir(), prefix));
}

/** A fetch stub: records every body, answers from a script, optionally throws. */
function stubFetch(script) {
  const calls = [];
  let index = 0;
  const fn = async (url, init) => {
    const body = JSON.parse(init.body);
    calls.push({ url, body });
    const step = script[Math.min(index, script.length - 1)];
    index += 1;
    if (step === "throw") throw new Error("socket hang up");
    return {
      ok: step >= 200 && step < 300,
      status: step,
      json: async () => ({ detail: step < 300 ? undefined : `refused ${step}` }),
    };
  };
  fn.calls = calls;
  return fn;
}

const FAST = { sleep: async () => {}, log: () => {} };

// ── piece 2/§E: partial trailing line ─────────────────────────────────────

test("a partial trailing line is NOT consumed and NOT lost", () => {
  // Real Pi writes with appendFileSync; a kill can land between two writes, so
  // the last line is a prefix of a JSON object.
  const complete = `${HEADER("sid-1")}\n${entry("a", null, "user", "hi")}\n`;
  const partial = '{"type":"message","id":"b","parentId":"a","message":{"role":"assistant","con';
  const buf = Buffer.from(complete + partial, "utf8");

  const first = readCompleteLines(buf, 0);
  assert.equal(first.lines.length, 2, "only the two complete lines are parsed");
  assert.equal(first.partial, true, "the tail is reported as partial");
  // The cursor stops BEFORE the partial line: exactly the end of the last
  // complete line. Advancing past it is the loss this test exists to forbid.
  assert.equal(first.nextOffset, Buffer.byteLength(complete, "utf8"));
  assert.ok(
    first.nextOffset < buf.length,
    "the cursor must stay behind the partial tail, never past it",
  );

  // Second pass: the line finished (Pi appended the rest).
  const finished = Buffer.from(complete + '{"type":"message","id":"b","parentId":"a","message":{"role":"assistant","content":[{"type":"text","text":"done"}]}}\n', "utf8");
  const second = readCompleteLines(finished, first.nextOffset);
  assert.equal(second.lines.length, 1, "the completed line ships on the next pass");
  assert.equal(second.partial, false);
  const parsed = JSON.parse(second.lines[0]);
  assert.equal(parsed.message.content[0].text, "done", "the turn is whole, not truncated");
});

test("no newline at all leaves the cursor exactly where it was", () => {
  const buf = Buffer.from('{"type":"session","id":"x"}', "utf8"); // no \n
  const r = readCompleteLines(buf, 0);
  assert.deepEqual(r.lines, []);
  assert.equal(r.nextOffset, 0, "a file with no complete line contributes no offset");
  assert.equal(r.partial, true);
});

test("an offset past EOF reads nothing and never moves the cursor", () => {
  const buf = Buffer.from("a\nb\n", "utf8");
  const r = readCompleteLines(buf, 4096);
  assert.deepEqual(r.lines, []);
  assert.equal(r.nextOffset, buf.length, "clamped to EOF — a stall, never a skip");
});

// ── piece 9: cursor past EOF / file replaced ──────────────────────────────

test("planFile re-anchors to the header on cursor > file_size (truncation)", () => {
  const cursor = { offset: 900, sessionId: "sid-1", dev: 1, ino: 2, headerSha: "h" };
  const plan = planFile(cursor, { size: 120, dev: 1, ino: 2 }, { headerId: "sid-1", headerSha: "h" });
  assert.equal(plan.action, "reanchor");
  assert.equal(plan.offset, 0);
  assert.equal(plan.reason, "cursor_past_eof");
});

test("planFile re-anchors when the file is REPLACED under the same path", () => {
  const cursor = { offset: 90, sessionId: "sid-1", dev: 1, ino: 2, headerSha: "h" };
  // Same id, same size, different inode: a rewrite (Pi's `_rewriteFile`
  // truncates then rewrites) or a copy over the path.
  const plan = planFile(cursor, { size: 400, dev: 1, ino: 3 }, { headerId: "sid-1", headerSha: "h" });
  assert.equal(plan.action, "reanchor");
  assert.equal(plan.reason, "file_replaced");
});

test("planFile re-anchors when the header changes under the same path", () => {
  const cursor = { offset: 90, sessionId: "sid-1", dev: 1, ino: 2, headerSha: "old" };
  const plan = planFile(cursor, { size: 400, dev: 1, ino: 2 }, { headerId: "sid-1", headerSha: "new" });
  assert.equal(plan.action, "reanchor");
  assert.equal(plan.reason, "header_changed");
});

test("planFile re-anchors when a DIFFERENT session id appears in the same path", () => {
  const cursor = { offset: 90, sessionId: "sid-1", dev: 1, ino: 2, headerSha: "h" };
  const plan = planFile(cursor, { size: 400, dev: 1, ino: 2 }, { headerId: "sid-2", headerSha: "h" });
  assert.equal(plan.action, "reanchor");
  assert.equal(plan.reason, "session_id_changed");
});

test("planFile skips only when the cursor is AT the size, else ships", () => {
  const cursor = { offset: 100, dev: 1, ino: 2, headerSha: "h" };
  assert.equal(planFile(cursor, { size: 100, dev: 1, ino: 2 }, {}).action, "skip");
  assert.equal(planFile(cursor, { size: 140, dev: 1, ino: 2 }, {}).action, "ship");
  assert.equal(planFile(undefined, { size: 140, dev: 1, ino: 2 }, {}).action, "ship");
});

// ── piece 13: the shared branch contract ──────────────────────────────────

test("activeBranch ships the LIVE chain only — an abandoned branch is excluded", () => {
  // user a -> assistant b -> user c -> assistant d   (abandoned at c)
  //      \-> user c2 -> assistant d2                (the active leaf)
  const entries = [
    JSON.parse(HEADER("sid-1")),
    JSON.parse(entry("a", null, "user", "Q1")),
    JSON.parse(entry("b", "a", "assistant", "A1")),
    JSON.parse(entry("c", "b", "user", "abandoned question")),
    JSON.parse(entry("d", "c", "assistant", "abandoned answer")),
    JSON.parse(entry("c2", "b", "user", "live question")),
    JSON.parse(entry("d2", "c2", "assistant", "live answer")),
  ];
  const turns = windowTurns(activeBranch(entries));
  assert.deepEqual(
    turns.map((t) => t.content),
    ["Q1", "A1", "live question", "live answer"],
    "the abandoned branch's two turns must not be shipped as live",
  );
});

test("a compaction entry cuts the window at firstKeptEntryId", () => {
  const entries = [
    JSON.parse(HEADER("sid-1")),
    JSON.parse(entry("a", null, "user", "old question")),
    JSON.parse(entry("b", "a", "assistant", "old answer")),
    JSON.parse(entry("k", "b", "user", "kept question")),
    JSON.parse(entry("j", "k", "assistant", "kept answer")),
    JSON.parse(JSON.stringify({ type: "compaction", id: "z", parentId: "j", timestamp: "2026-10-23T11:00:00.000Z", summary: "S", firstKeptEntryId: "k" })),
  ];
  const window = activeBranch(entries);
  assert.deepEqual(windowTurns(window).map((t) => t.content), ["kept question", "kept answer"]);
  // And cutAtCompaction is the exported rule (so both lanes can call it).
  assert.equal(cutAtCompaction(window).length, window.length);
});

test("an entry with no id is never given a positional id", () => {
  const entries = [
    JSON.parse(HEADER("sid-1")),
    { type: "message", parentId: null, message: { role: "user", content: [{ type: "text", text: "ghost" }] } },
    JSON.parse(entry("a", null, "user", "real")),
  ];
  const window = activeBranch(entries);
  assert.deepEqual(window.map((w) => w.id), ["a"], "no synthesized id, no ghost turn");
});

// ── piece 12: the clock source ────────────────────────────────────────────

test("windowCapturedAt reads the entry's own timestamp, in seconds", () => {
  const entries = [
    JSON.parse(HEADER("sid-1")),
    JSON.parse(entry("a", null, "user", "x", { timestamp: "2026-10-23T10:00:05.000Z" })),
    JSON.parse(entry("b", "a", "assistant", "y", { timestamp: "2026-10-23T10:00:42.500Z" })),
  ];
  assert.equal(
    windowCapturedAt(activeBranch(entries)),
    Math.floor(Date.parse("2026-10-23T10:00:42.500Z") / 1000),
    "unix SECONDS, floored — the wire field is a seconds instant",
  );
});

// ── piece 11: status classification ──────────────────────────────────────

test("only a 2xx advances; permanent 4xx is dead-lettered; 402/409/429/5xx retained", () => {
  assert.equal(classifyStatus(200), "advance");
  assert.equal(classifyStatus(204), "advance");
  assert.equal(classifyStatus(422), "permanent");
  assert.equal(classifyStatus(401), "permanent");
  assert.equal(classifyStatus(413), "permanent");
  assert.equal(classifyStatus(402), "retain"); // quota: the moment, not the bytes
  assert.equal(classifyStatus(409), "retain"); // "already in flight" is benign
  assert.equal(classifyStatus(429), "retain");
  assert.equal(classifyStatus(503), "retain");
  assert.equal(classifyStatus(undefined), "retain"); // transport
});

test("the backoff schedule is bounded and monotone", () => {
  assert.ok(BACKOFF_MS.length > 0);
  for (let i = 1; i < BACKOFF_MS.length; i += 1) assert.ok(BACKOFF_MS[i] > BACKOFF_MS[i - 1]);
  assert.ok(BACKOFF_MS[BACKOFF_MS.length - 1] <= 60_000, "a bounded wait, not an unbounded loop");
});

// ── cursor store: atomic write, restart, corrupt fail-closed ──────────────

test("the cursor SURVIVES a restart and neither re-emits nor skips", () => {
  const dir = tmp("cursor-");
  const state = emptyCursor();
  state.files["/s/one.jsonl"] = { offset: 512, sessionId: "sid-1", dev: 1, ino: 2, delivered: 3 };
  const saved = saveCursor(dir, state);
  assert.equal(saved.ok, true);

  const reloaded = loadCursor(dir);
  assert.equal(reloaded.recovered, false);
  assert.equal(reloaded.state.files["/s/one.jsonl"].offset, 512);
  assert.equal(reloaded.state.files["/s/one.jsonl"].sessionId, "sid-1");
  assert.equal(reloaded.state.files["/s/one.jsonl"].delivered, 3);
});

test("a CORRUPT cursor fails closed to the last good offset (recorded, not silent)", () => {
  const dir = tmp("cursor-corrupt-");
  const state = emptyCursor();
  state.files["/s/one.jsonl"] = { offset: 111, sessionId: "sid-1" };
  saveCursor(dir, state);
  // Second save creates the .bak of the first good copy.
  state.files["/s/one.jsonl"].offset = 222;
  saveCursor(dir, state);

  writeFileSync(cursorPath(dir), '{"version":1,"files":{"\/s\/one.jsonl":{"offset":'); // torn
  const loaded = loadCursor(dir);
  assert.equal(loaded.recovered, true);
  assert.match(String(loaded.error), /failed closed/);
  assert.equal(
    loaded.state.files["/s/one.jsonl"].offset,
    111,
    "the last GOOD offset (the backup), not 0 and not EOF",
  );
  assert.ok(statSync(backupPath(dir)).size > 0);
});

test("a negative or non-numeric offset in the cursor is dropped, never trusted", () => {
  const dir = tmp("cursor-invalid-");
  mkdirSync(dir, { recursive: true });
  writeFileSync(
    cursorPath(dir),
    JSON.stringify({ version: 1, files: { "/s/a.jsonl": { offset: -5 }, "/s/b.jsonl": { offset: 7 } } }),
  );
  const loaded = loadCursor(dir);
  assert.equal(loaded.state.files["/s/a.jsonl"], undefined, "an invalid offset cannot skip content");
  assert.equal(loaded.state.files["/s/b.jsonl"].offset, 7);
});

test("a cursor from a future version is not silently interpreted", () => {
  const dir = tmp("cursor-version-");
  mkdirSync(dir, { recursive: true });
  writeFileSync(cursorPath(dir), JSON.stringify({ version: 99, files: { "/s/a.jsonl": { offset: 5 } } }));
  const loaded = loadCursor(dir);
  assert.equal(loaded.recovered, true);
  assert.deepEqual(loaded.state.files, {});
});

// ── the worker end-to-end (real files, stubbed server) ───────────────────

test("the worker ships a session file, stamps lane=store_sync, and advances on 2xx", async () => {
  const root = tmp("sessions-");
  const cursorDir = tmp("cursor-run-");
  const path = sessionFixture(root, "s.jsonl", [
    HEADER("sid-e2e"),
    entry("a", null, "user", "hello"),
    entry("b", "a", "assistant", "world"),
  ]);
  const fetchImpl = stubFetch([200]);
  const receipt = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl, ...FAST });

  assert.equal(receipt.shipped, 1);
  assert.equal(receipt.deadLettered, 0);
  assert.equal(fetchImpl.calls.length, 1);
  const body = fetchImpl.calls[0].body;
  assert.equal(body.session_id, "sid-e2e", "piece 6: from the header, not a generated id");
  assert.equal(body.harness, "pi");
  assert.equal(body.capture_lane, LANE, "piece 8: the lane is the discriminator");
  assert.deepEqual(body.conversation.map((t) => t.content), ["hello", "world"]);
  assert.equal(typeof body.client_captured_at, "number", "piece 12: the log's own clock");

  const state = loadCursor(cursorDir).state;
  assert.equal(state.files[path].offset, statSync(path).size, "cursor advanced to the file size");
});

test("a failing POST does NOT advance the cursor — the batch survives the restart", async () => {
  const root = tmp("sessions-fail-");
  const cursorDir = tmp("cursor-fail-");
  const path = sessionFixture(root, "s.jsonl", [HEADER("sid-f"), entry("a", null, "user", "keep me")]);
  const failing = stubFetch([503]);
  const first = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: failing, ...FAST });
  assert.equal(first.shipped, 0);
  assert.equal(first.files[0].action, "failed");
  assert.equal(loadCursor(cursorDir).state.files[path].offset, 0, "cursor did NOT move");

  // Restart: the SAME content is retried and now lands. At-least-once.
  const good = stubFetch([200]);
  const second = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: good, ...FAST });
  assert.equal(second.shipped, 1);
  assert.equal(good.calls[0].body.conversation[0].content, "keep me");
});

test("restarts do not MULTIPLY retries: the attempt count is persisted", async () => {
  const root = tmp("sessions-mult-");
  const cursorDir = tmp("cursor-mult-");
  const path = sessionFixture(root, "s.jsonl", [HEADER("sid-m"), entry("a", null, "user", "x")]);
  const first = stubFetch([503]);
  await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: first, ...FAST });
  const afterFirst = loadCursor(cursorDir).state.files[path];
  const second = stubFetch([503]);
  await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: second, ...FAST });
  const afterSecond = loadCursor(cursorDir).state.files[path];
  assert.equal(
    afterSecond.failures,
    (afterFirst.failures ?? 0) + 1,
    "a restart increments ONE in-flight retry record, it does not reset to zero",
  );
  // DISCRIMINATION: the schedule RESUMES where the previous run stopped, so a
  // restart makes FEWER attempts than a fresh file would. Without reading the
  // persisted counter both runs would make the full BACKOFF_MS.length attempts.
  assert.ok(
    second.calls.length < first.calls.length,
    `a restart must not multiply retries (first=${first.calls.length}, second=${second.calls.length})`,
  );
  assert.equal(afterSecond.offset, 0, "still not advanced");
});

test("a PERMANENT 4xx is dead-lettered and does NOT block a later file", async () => {
  const root = tmp("sessions-dl-");
  const cursorDir = tmp("cursor-dl-");
  sessionFixture(root, "a-bad.jsonl", [HEADER("sid-bad"), entry("a", null, "user", "poison")]);
  sessionFixture(root, "b-good.jsonl", [HEADER("sid-good"), entry("b", null, "user", "fine")]);
  // The staleness of the first answer is what makes head-of-line blocking
  // visible: a naive worker stops at the first permanent refusal.
  const fetchImpl = async (url, init) => {
    const body = JSON.parse(init.body);
    const status = body.session_id === "sid-bad" ? 422 : 200;
    return { ok: status === 200, status, json: async () => ({ detail: `refused ${status}` }) };
  };
  const receipt = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl, ...FAST });
  assert.equal(receipt.deadLettered, 1);
  assert.equal(receipt.shipped, 1, "the later file still ships — no head-of-line block");
  assert.equal(receipt.dropped, 1, "the drop is COUNTED, never silent");
  assert.equal(receipt.deadLetters.length, 1);
  assert.match(receipt.deadLetters[0].reason, /422/);
});

test("a half-written tail neither ships garbage nor blocks the next run", async () => {
  const root = tmp("sessions-tail-");
  const cursorDir = tmp("cursor-tail-");
  const path = join(root, "s.jsonl");
  writeFileSync(
    path,
    `${HEADER("sid-tail")}\n${entry("a", null, "user", "first")}\n{"type":"message","id":"b","parentId":"a","mess`,
  );
  const fetchImpl = stubFetch([200]);
  const first = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl, ...FAST });
  assert.equal(fetchImpl.calls.length, 1, "the complete part ships");
  assert.deepEqual(fetchImpl.calls[0].body.conversation.map((t) => t.content), ["first"]);
  const cursorAfter = loadCursor(cursorDir).state.files[path].offset;
  assert.ok(cursorAfter < statSync(path).size, "cursor is behind the partial tail");

  // Pi finishes the line; the second run picks it up from that offset.
  appendFileSync(path, 'age":{"role":"assistant","content":[{"type":"text","text":"second"}]}}\n');
  const second = stubFetch([200]);
  await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: second, ...FAST });
  assert.equal(second.calls.length, 1);
  assert.deepEqual(
    second.calls[0].body.conversation.map((t) => t.content),
    ["first", "second"],
    "the completed turn is present and whole",
  );
});

test("a TRUNCATED file is re-read from the header, not skipped forever", async () => {
  const root = tmp("sessions-trunc-");
  const cursorDir = tmp("cursor-trunc-");
  const path = sessionFixture(root, "s.jsonl", [
    HEADER("sid-t"),
    entry("a", null, "user", "one"),
    entry("b", "a", "assistant", "two"),
  ]);
  const first = stubFetch([200]);
  await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: first, ...FAST });
  const before = loadCursor(cursorDir).state.files[path].offset;
  assert.ok(before > 0);

  // Pi's `_rewriteFile` truncates then rewrites: the file is now SHORTER.
  writeFileSync(path, `${HEADER("sid-t")}\n${entry("a", null, "user", "one")}\n`);
  assert.ok(statSync(path).size < before, "the fixture must actually shrink");

  const second = stubFetch([200]);
  const receipt = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: second, ...FAST });
  assert.equal(second.calls.length, 1, "a silent skip would have made zero calls");
  assert.deepEqual(second.calls[0].body.conversation.map((t) => t.content), ["one"]);
  assert.equal(receipt.files[0].action, "ship");
});

test("a file REPLACED under the same path re-ships from the header", async () => {
  const root = tmp("sessions-replace-");
  const cursorDir = tmp("cursor-replace-");
  const path = sessionFixture(root, "s.jsonl", [HEADER("sid-r"), entry("a", null, "user", "old")]);
  await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: stubFetch([200]), ...FAST });
  assert.ok(loadCursor(cursorDir).state.files[path].offset > 0);

  // Replace atomically with a DIFFERENT session (fork/clone shape) of the same
  // length: only the inode (and the header hash) reveal it.
  const replacement = join(root, "tmp-replacement.jsonl");
  writeFileSync(replacement, `${HEADER("sid-r2")}\n${entry("z", null, "user", "new")}\n`);
  renameSync(replacement, path);

  const fetchImpl = stubFetch([200]);
  const receipt = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl, ...FAST });
  assert.equal(fetchImpl.calls.length, 1);
  assert.equal(fetchImpl.calls[0].body.session_id, "sid-r2", "the NEW header id, not the stale one");
  assert.equal(receipt.files[0].sessionId, "sid-r2");
});

test("a file with no session header id is REFUSED — an id is never invented", async () => {
  const root = tmp("sessions-nohdr-");
  const cursorDir = tmp("cursor-nohdr-");
  sessionFixture(root, "s.jsonl", [entry("a", null, "user", "no header here")]);
  const fetchImpl = stubFetch([200]);
  const receipt = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl, ...FAST });
  assert.equal(fetchImpl.calls.length, 0);
  assert.equal(receipt.files[0].action, "failed");
  assert.equal(receipt.files[0].reason, "no_header_id");
  assert.equal(receipt.errors.length, 1);
});

test("the run is BOUNDED: maxFiles caps one pass and the rest drains later", async () => {
  const root = tmp("sessions-bound-");
  const cursorDir = tmp("cursor-bound-");
  for (let i = 0; i < 5; i += 1) {
    sessionFixture(root, `s${i}.jsonl`, [HEADER(`sid-${i}`), entry(`a${i}`, null, "user", `t${i}`)]);
  }
  const fetchImpl = stubFetch([200]);
  const receipt = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl, maxFiles: 2, ...FAST });
  assert.equal(fetchImpl.calls.length, 2, "at most maxFiles POSTs in one pass");
  const second = stubFetch([200]);
  await runStoreSync({ sessionDir: root, cursorDir, fetchImpl: second, maxFiles: 2, ...FAST });
  assert.equal(second.calls.length, 2, "and the REMAINDER drains on the next run");
});

test("no credential is a NAMED error and does not burn the retry schedule", async () => {
  const root = tmp("sessions-nocred-");
  const cursorDir = tmp("cursor-nocred-");
  sessionFixture(root, "s.jsonl", [HEADER("sid-nocred"), entry("a", null, "user", "x")]);
  const fetchImpl = stubFetch([200]);
  const receipt = await runStoreSync({
    sessionDir: root, cursorDir, fetchImpl,
    // `resolveConfig` is bypassed by supplying both: an empty key models an
    // unconfigured box without depending on the machine's own config.
    apiKey: "", apiUrl: "http://127.0.0.1:1",
    ...FAST,
  });
  assert.equal(fetchImpl.calls.length, 0, "no POST is attempted without a key");
  assert.equal(receipt.shipped, 0);
  assert.equal(receipt.errors.length, 1);
  assert.match(receipt.errors[0], /no TORTOISE_API_KEY/);
});

test("dry-run POSTs nothing and still reports the plan", async () => {
  const root = tmp("sessions-dry-");
  const cursorDir = tmp("cursor-dry-");
  sessionFixture(root, "s.jsonl", [HEADER("sid-d", ), entry("a", null, "user", "x")]);
  const fetchImpl = stubFetch([200]);
  const receipt = await runStoreSync({ sessionDir: root, cursorDir, fetchImpl, dryRun: true, ...FAST });
  assert.equal(fetchImpl.calls.length, 0);
  assert.equal(receipt.dryRun, true);
  assert.equal(receipt.files[0].reason, "dry_run");
});

test("scanSessionFile keeps the header sha stable across re-reads", () => {
  const root = tmp("scan-");
  const path = sessionFixture(root, "s.jsonl", [HEADER("sid-s"), entry("a", null, "user", "x")]);
  const a = scanSessionFile(path);
  const b = scanSessionFile(path);
  assert.equal(a.headerId, "sid-s");
  assert.equal(a.headerSha, b.headerSha);
  assert.equal(a.entries.length, 2);
});

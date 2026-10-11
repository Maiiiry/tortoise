# Platform Seams Matrix — per-turn volunteering-memory injection

Epic #2080 end-state delivery (issues #2119–#2126). Each seam = a thin
registration over the ONE shared per-turn reflex:

```
UserPromptSubmit hook  →  volunteer-turn.sh <harness>  →  `tortoise volunteer`
                                                          (hosted POST /v1/context
                                                           or local SDK volunteer_context
                                                           — one pipeline, tortoise/volunteer.py)
```

For a harness whose per-turn surface is a native PLUGIN rather than a shell
command (Hermes, #2121), the callback invokes the same `tortoise volunteer`
CLI directly — the arrow on the left is the only part that differs.

Install (agent-first onboarding, #2123/#2124 mandate): `tortoise install
<harness>` for codex / claude / cline / hermes. Everything else in this
matrix is the documented manual registration.

## Contracts (verified against harness primary docs)

| # | Harness | Hook event | Registration target | Output contract | Wave-1 status |
|---|---------|-----------|--------------------|-----------------|---------------|
| #2126 | Claude Code / Desktop | `UserPromptSubmit` | `.claude/settings.json` `hooks` | `hookSpecificOutput.additionalContext` | shipped (`install claude`); MCP priming in `mcp_server.py` |
| #2123 | Codex | `UserPromptSubmit` | `<repo>/.codex/hooks.json` (also `~/.codex/hooks.json`) | `hookSpecificOutput.additionalContext` (stdin JSON has `prompt`) | shipped (`install codex`) |
| #2124* | Cline | `UserPromptSubmit` | `.cline/hooks/UserPromptSubmit` (project) or `~/.cline/hooks` (global; enable Hooks in settings) | `contextModification.context` | shipped (`install cline`) |
| #2124* | Devin | `UserPromptSubmit` | `.devin/hooks.v1.json` | `hookSpecificOutput.additionalContext` (Claude-hooks-shaped) | shared script supports `devin`; registration pending primary-doc re-fetch |
| #2119 | Pi (pi-coding-agent) | session lifecycle only (no per-turn prompt hook in the extension API today) | `~/.pi/agent/extensions/` TS extension | n/a — per-turn injection not reachable via extension API | MCP pull + session-start digest (existing) + documented limitation |
| #2122 | Cursor | `UserPromptSubmit` (Claude Code-compatible) | `.cursor/hooks.json` (Claude Code-compatible file) | `hookSpecificOutput.additionalContext` | shared script `claude` arg compatible; registration file documented |
| #2120 | OpenClaw | per-turn hook surface = AgentRequest hook | `~/.openclaw/*.claw` AgentRequest handler → `volunteer-turn.sh openclaw` | `handle_AgentRequest` → context dict | hook output mapping documented; harness-store parser scaffolded |
| #2121 | Hermes | `pre_llm_call` (directory plugin: `register(ctx)` + `ctx.register_hook`) | `<dir>/.hermes/plugins/tortoise-volunteer/` (`plugin.yaml` + `__init__.py`, written by `tortoise install hermes`) | the callback RETURNS `{"context": "<block>"}`, injected into the current turn's user message | shipped (`install hermes`) + ingestion: `tortoise sessions import --harness hermes <state.db>` (reads `$HERMES_HOME/state.db` read-only) |
| #2124* | Gemini CLI / OpenCode / other Claude-hooks-compatibles | `UserPromptSubmit`/equivalent | harness-specific | harness-specific | shared script's default emits the bare block (harmless where unsupported) |
| #2125 | Frameworks (SDK-level middleware: Vercel AI SDK / LangChain / Mastra / OpenAI Agents SDK) | per-request middleware | project code import `tortoise/framework.py` | tool-role message with the block | **not wave-1** — demand-gated per the issue; wave-1 shared script's bare-block default is the interim |

\* #2124 (harness adapters) is demand-gated per the issue — shared script
coverage lands first; per-harness registration files follow demand.

### #2121 Hermes — corrected contract (2026-10-11, primary source)

The wave-1 row above previously read "`pre_llm_call` (agents-hub) /
`.agents.yml` actions / tool-result text = the block / hook output mapping
documented; **parser entry added**". Two claims in it were wrong and are
corrected here:

1. **No parser entry existed.** A fleet-wide search for a Hermes parser
   returned nothing (`git log --all -S"parse_hermes"` is empty; no
   `hermes` reference in `tortoise/` or `tests/` outside unrelated
   fixtures), so the status was aspirational, not shipped. The parser
   (`tortoise/session_import/parsers.py::parse_hermes`) and the
   `--harness hermes` CLI choice land with #2121.
2. **`.agents.yml` actions is not a Hermes surface.** Code search across
   `NousResearch/hermes-agent` returns 0 hits for `agents.yml` /
   `agents-hub`; the injection surface is the `pre_llm_call` PLUGIN hook,
   which is REGISTERED FROM PYTHON, not from a YAML action, and whose
   RETURN VALUE (not a tool result) carries the block:
   * `website/docs/developer-guide/plugins/index.md` §"`pre_llm_call`
     context injection" — "Per-hook context is capped at `10,000`
     characters by default. Anything above the cap is written to
     `$HERMES_HOME/hook_outputs/<session_id>/<uuid>.txt`";
   * `plugins/AGENTS.md` — general plugins live in `plugins/<name>/`,
     `~/.hermes/plugins/`, `./.hermes/plugins/`; `register(ctx)` registers
     `pre_llm_call`;
   * `agent/turn_context.py::_collect_pre_llm_call_context` — the callback's
     `{"context": ...}` (or string) is appended to the CURRENT turn's user
     message; a raising callback is caught and logged (fail-open);
   * `hermes_state.py` — `DEFAULT_DB_PATH = get_hermes_home() / "state.db"`,
     and `hermes_state_common.py` carries the `sessions` / `messages` DDL
     the parser reads (live transcript = `messages.active = 1`, ordered by
     `id`, which the store itself uses — `timestamp` is not monotonic).

Because Hermes' read seam is a Python plugin rather than a shell command,
the seam does NOT add a `hermes` arm to `volunteer-turn.sh` (that would be
dead code) — the plugin invokes the SAME `tortoise volunteer` CLI the shell
hook does, with the harness `session_id` passed through for the reflex's
cross-turn re-mention suppression. Ingestion rides `tortoise sessions import`
as the wave-1 note on #2121 prescribes (no hook-driven live capture).

## Fail-open / fail-closed posture (all seams)

- **Content fail-open**: any reflex failure (unreachable, misconfig, empty
  graph, nothing above the confidence gate) → the hook emits NOTHING and
  exits 0. The agent turn proceeds untouched.
- **Auth fail-closed**: hosted capture/auth channels keep their own deny
  rules. The hook process carries no credentials of its own, but the reflex
  it invokes transmits the Bearer identity resolved from ONE source chain
  (#2369 D1, hardened): a file-sourced key transmits only to the SAME
  file's `api_url` or the built-in default — the env `TORTOISE_API_URL`
  override applies only to env-sourced keys; the transmitting identity
  resolves from the user-global `~/.tortoise/credentials.json` only (a
  repo/cwd `.tortoise` can never supply it — repo config feeds only local,
  non-sending mode); and a hosted-against-file run prints a stderr
  endpoint-mode note (`tortoise: hosted-mode note:`) that the shipped hook
  relays to the harness log, so a future redirect is loud at run time.
  Env alone (a bare `TORTOISE_API_KEY`) still never flips the hook to
  hosted mode (#1708 D1b).
- W4 user-exposure opt-in: per-turn injection ships behind the `install`
  command — nothing auto-registers.

## Verification

W3 harness seam grade (tests/eval/harness or tests/test_*): executes the
SHIPPED `volunteer-turn.sh` against a seeded embedded graph and asserts the
per-harness output contract carries the reflex block (see the wave-1 seam
test in tests/). The Hermes seam grades the same way at its own surface:
`tests/test_hermes_seam.py` installs the shipped plugin into a temp root,
imports it, fakes the reflex CLI, and asserts the `register(ctx)` →
`pre_llm_call` → `{"context": ...}` path (plus the cap, fail-open, and the
`parse_hermes` ingest leg over a real SQLite `state.db`).

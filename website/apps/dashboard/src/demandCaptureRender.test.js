// demandCaptureRender.test.js — #3514 (epic #1714 J4): the WIRING of the
// coming-soon click in the real dashboard source.
//
// WHY A SOURCE PIN AND NOT ONLY THE STATE-MACHINE TESTS. `demandCapture.test.js`
// proves the policy (body, key, states, copy) in isolation, and the dashboard
// e2e spec (`tests/e2e/test_dashboard_onboarding.py`, the `dashboard-e2e` job)
// drives the built bundle. This file is the always-run backstop for the seam
// BETWEEN them: the states exist and are correct, the dialog renders them, and
// the click actually posts through the session-proxying seam with its
// idempotency header. A refactor that keeps the module and drops the call site
// leaves the unit tests green and the feature recording nothing — the exact
// silent-drop shape this pins against.
//
// Reads main.jsx as TEXT (the integrationsGridRender.test.js pattern): no React
// runtime, no browser, and it runs in the always-on node suite.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  DEMAND_DISMISS_LABEL, DEMAND_FAILED, DEMAND_PRIMARY_LABEL, DEMAND_RECORDED,
  DEMAND_RETRY_LABEL, DEMAND_SENDING, DEMAND_SURFACE_SETTINGS,
  DEMAND_SURFACE_WIZARD,
} from './demandCapture.js'

const here = dirname(fileURLToPath(import.meta.url))
const src = readFileSync(join(here, 'main.jsx'), 'utf8')

/** The coming-soon dialog subtree, bounded at the next top-level component. */
function dialogBlock() {
  const start = src.indexOf('{comingSoonTile && (')
  assert.ok(start > -1, 'the coming-soon dialog must render')
  const end = src.indexOf('// #1728 (Task 16/17)', start)
  assert.ok(end > start, 'refusing an unbounded slice to EOF — it would pass on any copy')
  return src.slice(start, end)
}

/** The MemorySources component body, bounded at the next top-level component. */
function memorySourcesBlock() {
  const start = src.indexOf('function MemorySources(props) {')
  assert.ok(start > -1, 'MemorySources must exist')
  const end = src.indexOf('// #1728 (Task 16/17)', start)
  assert.ok(end > start, 'refusing an unbounded slice to EOF')
  return src.slice(start, end)
}

test('#3514: the dialog carries a real primary action, never a dead control', () => {
  const block = dialogBlock()
  assert.match(block, /className="btn-primary"[\s\S]*?onClick=\{recordComingSoonClick\}/,
    'the primary must post the click')
  assert.ok(block.includes('DEMAND_PRIMARY_LABEL'),
    'the action label comes from the module, not a second literal')
  assert.ok(block.includes('{DEMAND_RETRY_LABEL}'), 'the failure state retries')
  assert.ok(block.includes('{DEMAND_DISMISS_LABEL}'), 'the recorded state dismisses')
})

test('#3514: the primary is disabled while the POST is in flight', () => {
  const block = dialogBlock()
  assert.match(block, /disabled=\{demandBusy\(comingSoonDemand\)\}/,
    'the loading state must disable the primary (a double submit may not race the record)')
  assert.ok(block.includes('DEMAND_SENDING_LABEL'),
    'the disabled primary says what it is doing')
})

test('#3514: the status region is aria-live=polite and never upgrades itself', () => {
  const block = dialogBlock()
  assert.match(block, /aria-live="polite"/)
  assert.doesNotMatch(block, /role="alert"/,
    'role=alert would make the pinned polite region assertive — the contract says polite')
})

test('#3514: all four states render, driven by the module\'s constants', () => {
  const block = dialogBlock()
  for (const name of [DEMAND_SENDING, DEMAND_RECORDED, DEMAND_FAILED]) {
    assert.ok(block.includes(`comingSoonDemand.status === ${name}`)
      || block.includes(`comingSoonDemand.status === DEMAND_${name.toUpperCase()}`),
    `the ${name} state must be rendered`)
  }
  assert.ok(block.includes('{DEMAND_FAILED_COPY}'), 'the failure names itself')
  assert.ok(block.includes('{DEMAND_RECORDED_COPY}'), 'the recorded state is shown inline')
  assert.ok(block.includes('demandRecordedDetail(comingSoonDemand)'))
})

test('#3514: focus is never dropped to <body> as the states swap the controls', () => {
  const block = dialogBlock()
  assert.match(block, /className="ghost"\s*\n?\s*autoFocus/,
    'the idle state keeps the pinned initial focus on Close')
  assert.match(block, /className="btn-primary" autoFocus onClick=\{closeComingSoon\}\)?[\s\S]{0,40}\{DEMAND_DISMISS_LABEL\}/,
    'Dismiss takes focus when it mounts in place of the primary')
  assert.match(block, /className="btn-primary" autoFocus onClick=\{recordComingSoonClick\}/,
    'Retry takes focus in the failure state')
})

test('#3514: the click posts through the session seam with its idempotency key', () => {
  const sources = [src, readFileSync(join(here, 'demandCapture.js'), 'utf8')].join('\n')
  assert.match(src, /import \{[\s\S]*?recordDemand[\s\S]*?\} from '\.\/demandCapture\.js'/,
    'the policy half is the module, not a second copy in main.jsx')
  assert.match(src, /request: api\s*\}\)/,
    'the POST rides the same-origin BFF proxy seam (`api`), never a client-built credential')
  assert.doesNotMatch(src, /fetch\(\s*[`'"]\/api\/v1\/data-sources\/demand/,
    'no raw fetch for the demand path — the seam owns the session cookie')
  assert.match(sources, /'X-Idempotency-Key': idempotencyKey/)
  assert.match(sources, /demandKeyFor\(demandKeysRef\.current, key\)/,
    'the key is minted once per (mount, tile) so a second click REPLAYS')
})

test('#3514: both live mounts tell the record WHICH surface it came from', () => {
  assert.ok(src.includes('surface: DEMAND_SURFACE_SETTINGS'),
    'the Settings mount (via the shared prop bundle) records surface=settings')
  assert.ok(src.includes('surface={DEMAND_SURFACE_WIZARD}'),
    'the wizard mount overrides it — the two mounts differ, and `surface` is read')
  assert.ok(src.includes('surface = DEMAND_SURFACE_SETTINGS'),
    'the component reads the prop it is handed')
})

test('#3514: the recorded state is per-tile and survives a second open', () => {
  const body = memorySourcesBlock()
  assert.match(body, /demandStates\[comingSoonKey\]/,
    'opening an already-recorded tile shows its recorded state, not a fresh primary')
  assert.match(body, /setDemandStates\(/)
  assert.match(body, /demandRecorded\(s\[key\]/, 'a success writes the recorded state')
  assert.match(body, /demandFailed\(s\[key\]/, 'a failure writes the failure state')
})

test('#3514: the dialog contract #3513 pinned is intact', () => {
  const block = dialogBlock()
  assert.match(block, /role="dialog"/)
  assert.match(block, /aria-modal="true"/)
  assert.match(block, /aria-labelledby="integration-modal-title"/)
  assert.match(block, /if \(e\.key === 'Escape'\) closeComingSoon\(\)/)
})

// dialogContainmentRemainderTripwire.test.js — #8670 static tripwire
// (`node --test src/*.test.js`, zero-dep, same convention as
// dialogContainmentTripwire.test.js).
//
// WHY THIS FILE EXISTS ALONGSIDE #2577's. `dialogFocus.test.js` proves the
// containment helpers are CORRECT and `dialogContainmentTripwire.test.js` pins
// the FOUR surfaces #2577/#2728 wired (create-team, reveal-key, ReauthDialog,
// Configured-packs). Neither can see the rest of the family: after #2577 landed,
// six more dialogs went on declaring `aria-modal="true"` with no Tab wrap and no
// inert background — the card said "modal" while the page behind it stayed
// reachable to Tab AND to a screen reader's virtual cursor. That is the exact
// defect #2577 closes, so this file pins the remainder:
//
//   captured-session delete   main.jsx      aria-label="Delete captured session"
//   key-create                main.jsx      aria-label="Create API key"
//   graph delete              main.jsx      aria-label="Delete graph"
//   integration coming-soon   main.jsx      aria-labelledby="integration-modal-title"
//   GitHub disconnect         githubDisconnect.js   'aria-label': 'Disconnect GitHub'
//   account deletion          accountDeletion.js    'aria-label': 'Delete account'
//
// Shape is pinned here; behaviour is pinned where it can be EXECUTED. The two
// extracted modules are `createElement` modules, so the last two tests drive
// their real `onKeyDown` through the real `containTabFocus` and assert on
// `.focus()` + `preventDefault()`, not on source text. main.jsx has no React
// runtime harness — its half is the source tripwire (the repo's established
// lane for that file, and the layer the issue's Verification Checklist names).
//
// ONE STRUCTURAL PIN IS LOAD-BEARING: the inert effect must be keyed on a flag
// that changes when the card UNMOUNTS, not merely when the id clears. The
// session-delete and graph-delete dialogs resolve their subject from the live
// list and render null once it is gone while the id stays set — an effect keyed
// on the id would leave the whole page `inert` with no dialog left to release
// it. That is a page-wide keyboard lockout, so it is pinned explicitly below.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { GithubDisconnectControl } from './githubDisconnect.js'
import { DeleteAccountSection } from './accountDeletion.js'

const HERE = dirname(fileURLToPath(import.meta.url))
const read = (f) => readFileSync(join(HERE, f), 'utf8')
const mainJsx = read('main.jsx')
const profileJsx = read('profile.jsx')
const githubJs = read('githubDisconnect.js')
const accountJs = read('accountDeletion.js')

/** The source window starting at `needle` (asserted present). */
function windowFrom(src, needle, after) {
  const i = src.indexOf(needle)
  assert.ok(i >= 0, `expected source to contain ${JSON.stringify(needle)}`)
  return src.slice(i, i + after)
}

/** Number of non-overlapping occurrences of `needle`. */
function count(src, needle) {
  return src.split(needle).length - 1
}

// Both spellings the dashboard uses: JSX `aria-modal="true"` and the
// createElement modules' `'aria-modal': 'true'`. Deliberately NOT the bare word —
// `dialogFocus.js` and this change's own comments discuss aria-modal in prose.
// Both spellings the dashboard uses: JSX `aria-modal="true"` and the
// createElement modules' `'aria-modal': 'true'`. Deliberately NOT the bare word —
// `dialogFocus.js` and this change's own comments discuss aria-modal in prose
// (one even writes `aria-modal=true` unquoted). A FRESH regex per use: a shared
// /g regex carries `lastIndex` between `test()` calls, which would make the
// census pass or fail depending on what ran before it.
const ARIA_MODAL_ATTR_SRC = 'aria-modal="true"|\'aria-modal\': \'true\''
const ariaModalRe = (flags = '') => new RegExp(ARIA_MODAL_ATTR_SRC, flags)

function ariaModalSurfaces() {
  const out = []
  for (const f of readdirSync(HERE)) {
    if (!/\.jsx?$/.test(f) || /\.test\.jsx?$/.test(f)) continue
    const src = read(f)
    for (const m of src.matchAll(ariaModalRe('g'))) out.push({ file: f, index: m.index, src })
  }
  return out
}

test('#8670: the tripwire extractors are not a no-op', () => {
  assert.equal(windowFrom('aa START middle END zz', 'START', 12), 'START middle')
  assert.equal(count('a a a', 'a'), 3)
  assert.equal(count('a', 'ZZ'), 0)
  assert.throws(() => windowFrom('nope', 'START', 5), /expected source to contain/)
  assert.match('role="dialog" aria-modal="true"', ariaModalRe())
  assert.match("h('div', { 'aria-modal': 'true' })", ariaModalRe())
  assert.doesNotMatch('the aria-modal family owes containment', ariaModalRe())
  assert.doesNotMatch('the dialogs declare aria-modal=true', ariaModalRe())
})

test('#8670: no new aria-modal surface escapes containment (family census)', () => {
  const surfaces = ariaModalSurfaces()
  // #2577's four (3 in main.jsx + ReauthDialog in profile.jsx) plus #8670's six.
  // A new dialog that declares modality lands here and must be wired + pinned.
  assert.equal(surfaces.length, 10,
    `expected 10 aria-modal surfaces (4 from #2577/#2728 + 6 from #8670), found ${surfaces.length}: `
    + surfaces.map((s) => `${s.file}@${s.index}`).join(', '))
  const files = [...new Set(surfaces.map((s) => s.file))].sort()
  assert.deepEqual(files, ['accountDeletion.js', 'githubDisconnect.js', 'main.jsx', 'profile.jsx'],
    'aria-modal must only be declared in the four files whose dialogs are pinned')
  // Every one of them must wrap Tab shortly after its own declaration — the
  // window is generous because the handler sits in the same opening tag, and the
  // per-dialog pins below assert the exact association.
  for (const { file, index, src } of surfaces) {
    const win = src.slice(index, index + 1600)
    assert.match(win, /containTabFocus\(e\.currentTarget/,
      `${file}@${index}: this aria-modal dialog does not wrap Tab — it announces modality without containing the keyboard`)
  }
})

test('#8670: each of the six remainder dialogs wraps Tab/Shift+Tab via containTabFocus', () => {
  // Anchored on each dialog's own identity, never on attribute adjacency.
  assert.match(windowFrom(mainJsx, 'aria-label="Delete captured session"', 700),
    /containTabFocus\(e\.currentTarget, \{ shiftKey: e\.shiftKey \}\)/,
    'captured-session delete: Tab containment')
  assert.match(windowFrom(mainJsx, 'aria-label="Create API key"', 1200),
    /containTabFocus\(e\.currentTarget, \{ shiftKey: e\.shiftKey \}\)/,
    'key-create: Tab containment')
  assert.match(windowFrom(mainJsx, 'aria-label="Delete graph"', 700),
    /containTabFocus\(e\.currentTarget, \{ shiftKey: e\.shiftKey \}\)/,
    'graph delete: Tab containment')
  assert.match(windowFrom(mainJsx, 'aria-labelledby="integration-modal-title"', 900),
    /containTabFocus\(e\.currentTarget, \{ shiftKey: e\.shiftKey \}\)/,
    'integration coming-soon: Tab containment')
  assert.match(windowFrom(githubJs, "'aria-label': 'Disconnect GitHub'", 1400),
    /containTabFocus\(e\.currentTarget, \{ shiftKey: e\.shiftKey \}\)/,
    'GitHub disconnect: Tab containment')
  assert.match(windowFrom(accountJs, "'aria-label': 'Delete account'", 1400),
    /containTabFocus\(e\.currentTarget, \{ shiftKey: e\.shiftKey \}\)/,
    'account deletion: Tab containment')
})

test('#8670: each of the six dialogs makes the background inert while open', () => {
  assert.match(mainJsx, /import \{[^}]*containTabFocus[^}]*applyBackgroundInert[^}]*\} from '\.\/dialogFocus\.js'/)
  // The four inline dialogs: one ref'd container + one effect each, keyed on a
  // value that flips when the card opens/closes.
  const inline = [
    ['captured-session delete', 'sessionDeleteDialogRef', 'applyBackgroundInert(el, document)', 'sessionDeleteArmed'],
    ['key-create', 'keyModalDialogRef', 'applyBackgroundInert(el, document)', 'keyModalOpen'],
    ['graph delete', 'graphDeleteDialogRef', 'applyBackgroundInert(el, document)', 'graphDeleteArmed'],
    ['integration coming-soon', 'comingSoonDialogRef', 'applyBackgroundInert(el, document)', 'comingSoonKey'],
  ]
  for (const [name, ref, call, dep] of inline) {
    assert.ok(mainJsx.includes(`ref={${ref}}`), `${name}: the dialog needs its own ref`)
    assert.match(windowFrom(mainJsx, `ref={${ref}}`, 80), /tabIndex=\{-1\}/,
      `${name}: the container must be focusable for the all-controls-disabled state`)
    const effect = windowFrom(mainJsx, `if (!${dep} || typeof document === 'undefined')`, 500)
    assert.match(effect, new RegExp(call.replace(/[().]/g, '\\$&')), `${name}: inert effect`)
    // …applied to THAT dialog's node. Without this, an effect that computed its
    // node from anywhere else (or a dead expression) still satisfies the shape.
    assert.match(effect, new RegExp(`const el = ${ref}\\.current`),
      `${name}: the effect must inert the dialog's own node (${ref})`)
    assert.match(effect, /return \(\) => \{[^}]*release\(\) \}/, `${name}: cleanup returned`)
    assert.ok(mainJsx.includes(`}, [${dep}])`), `${name}: effect keyed on ${dep}`)
  }
  // The two extracted modules stay hook-free (their guards call them as plain
  // functions — a hook there throws "Invalid hook call"), so the parent owns the
  // ref + effect and hands the node down.
  for (const [name, src, state, ref] of [
    ['GitHub disconnect', githubJs, 'githubDisconnect.open', 'githubDisconnectDialogRef'],
    ['account deletion', accountJs, 'deleteAccountOpen', 'deleteAccountDialogRef'],
  ]) {
    assert.match(src, /ref: dialogRef/, `${name}: the module must accept the parent's dialog ref`)
    const effect = windowFrom(mainJsx, `if (!${state} || typeof document === 'undefined')`, 500)
    assert.match(effect, /applyBackgroundInert\(el, document\)/, `${name}: inert effect`)
    assert.match(effect, new RegExp(`const el = ${ref}\\.current`),
      `${name}: the effect must inert the dialog's own node (${ref})`)
    assert.match(effect, /return \(\) => \{[^}]*release\(\) \}/, `${name}: cleanup returned`)
    assert.ok(mainJsx.includes(`dialogRef={${ref}}`), `${name}: the parent must pass its ref down`)
    assert.ok(mainJsx.includes(`}, [${state}])`), `${name}: effect keyed on ${state}`)
  }
})

test('#8670: the inert effect cannot outlive its dialog (page-lockout guard)', () => {
  // The session-delete and graph-delete cards resolve their subject from the LIVE
  // list and return null once it is gone, while the id state stays set — so an
  // effect keyed on the id would never clean up and the page would stay `inert`
  // with no dialog to release it. The flag must be DERIVED from the live list.
  assert.match(mainJsx,
    /const sessionDeleteArmed = !!confirmSessionId && sessions\.some\(\(x\) => String\(x\.id\) === confirmSessionId\)/,
    'the session-delete effect must be keyed on the rendered-dialog flag')
  assert.match(mainJsx,
    /const graphDeleteArmed = !!confirmDeleteId && graphs\.some\(\(g\) => g\.graph_id === confirmDeleteId\)/,
    'the graph-delete effect must be keyed on the rendered-dialog flag')
  assert.doesNotMatch(mainJsx, /if \(!confirmSessionId \|\| typeof document === 'undefined'\)/,
    'keying the inert effect on the ID would leak `inert` when the row leaves the list')
  assert.doesNotMatch(mainJsx, /if \(!confirmDeleteId \|\| typeof document === 'undefined'\)/,
    'keying the inert effect on the ID would leak `inert` when the graph leaves the list')
  // the modal really does return null on a vanished subject (the reason above)
  assert.ok(mainJsx.includes('if (!ds) return null'), 'session-delete renders null once its session is gone')
  assert.ok(mainJsx.includes('if (!dg) return null'), 'graph-delete renders null once its graph is gone')
})

test('#8670: every close path releases the inert BEFORE it restores focus', () => {
  // An element inside an inert subtree is not focusable, so a restore that runs
  // first silently no-ops (#2577) - the failure mode is invisible in jsdom, which
  // does not model `inert`, so the ORDER is pinned in source. Each window starts
  // at its own close helper and reaches its own restore (`InertRef.current()` is
  // matched inside the named ref).
  for (const [name, re] of [
    ['captured-session delete',
      /const sessionDeleteClose = \(\) => \{[\s\S]{0,400}?[Ii]nertRef\.current\(\)[\s\S]{0,300}?restoreFocus\(sessionDeleteRestoreRef\)/],
    ['key-create',
      /function dismissKeyModal\(\) \{[\s\S]{0,900}?[Ii]nertRef\.current\(\)[\s\S]{0,300}?restoreFocus\(keyModalRestoreRef\)/],
    ['graph delete',
      /const graphDeleteClose = \(\) => \{[\s\S]{0,400}?[Ii]nertRef\.current\(\)[\s\S]{0,300}?restoreFocus\(graphDeleteRestoreRef\)/],
    ['GitHub disconnect',
      /function closeGithubDisconnect\(\) \{[\s\S]{0,1600}?[Ii]nertRef\.current\(\)[\s\S]{0,300}?restoreFocus\(githubDisconnectRestoreRef\)/],
  ]) {
    assert.match(mainJsx, re,
      `${name}: the inert background must be released before the focus restore`)
  }
  // The three whose close helpers are arrow functions (not declarations).
  assert.match(mainJsx,
    /const closeComingSoon = \(\) => \{[\s\S]{0,600}?comingSoonInertRef\.current\(\)[\s\S]{0,300}?requestAnimationFrame\(\(\) => restoreFocus\(comingSoonRestoreRef\)\)/,
    'integration coming-soon: release before the deferred restore')
  assert.match(mainJsx,
    /onCancel=\{\(\) => \{[\s\S]{0,700}?deleteAccountInertRef\.current\(\)[\s\S]{0,200}?restoreFocus\(deleteAccountRestoreRef\)/,
    'account deletion: release before the restore')
  // and the restore half actually exists on each (#2392 minimal set): the three
  // inline dialogs had none, so the containment would otherwise drop focus on
  // <body> when the card unmounts.
  for (const [name, needle] of [
    ['captured-session delete', 'rememberFocusedTrigger(sessionDeleteRestoreRef)'],
    ['key-create', 'rememberFocusedTrigger(keyModalRestoreRef)'],
    ['graph delete', 'rememberFocusedTrigger(graphDeleteRestoreRef)'],
  ]) {
    assert.ok(mainJsx.includes(needle), `${name}: the opening gesture must capture the restore target`)
  }
})

test('#8670: existing Escape / busy wiring is preserved on every wired dialog', () => {
  // The Escape guards that already existed must survive the containment edit —
  // a busy surface must never be dismissable mid-flight.
  assert.match(windowFrom(mainJsx, 'aria-label="Delete captured session"', 2600),
    /e\.key === 'Escape' && !sessionBusy/, 'captured-session delete: Escape stays busy-guarded')
  assert.match(windowFrom(mainJsx, 'aria-label="Delete graph"', 2600),
    /e\.key === 'Escape' && !graphBusy/, 'graph delete: Escape stays busy-guarded')
  assert.match(windowFrom(mainJsx, 'aria-label="Create API key"', 1200),
    /e\.key === 'Escape' && !keyModalBusy/, 'key-create: Escape is busy-guarded')
  assert.match(windowFrom(githubJs, "'aria-label': 'Disconnect GitHub'", 1400),
    /e\.key === 'Escape' && !busy/, 'GitHub disconnect: Escape stays busy-guarded')
  assert.match(windowFrom(accountJs, "'aria-label': 'Delete account'", 1400),
    /e\.key === 'Escape' && !busy/, 'account deletion: Escape stays busy-guarded')
  assert.match(windowFrom(mainJsx, 'aria-labelledby="integration-modal-title"', 900),
    /e\.key === 'Escape'/, 'integration coming-soon: Escape handler preserved')
  // …and the modals still announce themselves as modal: an inert-trapped dialog
  // that stopped claiming modality would be a silent a11y regression.
  assert.equal(count(mainJsx, 'aria-modal="true"'), 7)
  assert.equal(count(profileJsx, 'aria-modal="true"'), 1)
})

// ── EXECUTED behaviour for the two extracted dialogs ──────────────────────
// Their guards render them as plain functions (no jsdom), but containment is pure
// enough to drive: `containTabFocus` only reads `querySelectorAll`, `focus()`,
// `hasAttribute`/`getAttribute` and `parentElement`, so fakes exercise the REAL
// wiring — including that the handler consumes the key (preventDefault), which is
// the difference between "wraps" and "moves focus and also lets the browser move
// it out again".
function fakeControl(name) {
  return {
    name,
    focused: false,
    disabled: false,
    parentElement: null,
    focus() { this.focused = true },
    hasAttribute: () => false,
    getAttribute: () => null,
  }
}

function driveTab(componentFn, props, { cycle = false } = {}) {
  const tree = componentFn(props)
  const dialog = findDialog(tree)
  assert.ok(dialog, 'the dialog element must render while open')
  assert.equal(dialog.props.tabIndex, -1, 'the container must be focusable (tabIndex -1)')
  const first = fakeControl('first')
  const last = fakeControl('last')
  const container = {
    querySelectorAll: () => (cycle ? [first, last] : []),
    ownerDocument: { activeElement: cycle ? last : null },
    focus() { this.focused = true },
  }
  let prevented = 0
  dialog.props.onKeyDown({
    key: 'Tab', shiftKey: false, currentTarget: container,
    preventDefault: () => { prevented += 1 },
  })
  return { prevented, first, last, container, dialog }
}

function findDialog(node, acc) {
  if (node === null || node === undefined || typeof node !== 'object') return acc || null
  if (Array.isArray(node)) {
    for (const child of node) {
      const hit = findDialog(child)
      if (hit) return hit
    }
    return null
  }
  if (!('props' in node)) return null
  if (node.type === 'div' && node.props && node.props.role === 'dialog') return node
  return findDialog(node.props && node.props.children)
}

test('#8670: the GitHub disconnect dialog keeps Tab inside (executed)', () => {
  const props = {
    connected: true, open: true, busy: false, error: '', result: null,
    onOpen() {}, onClose() {}, onConfirm() {},
  }
  // last control focused → the wrap must move focus to the FIRST and consume the key
  const cycled = driveTab(GithubDisconnectControl, props, { cycle: true })
  assert.equal(cycled.first.focused, true, 'Tab off the last control must land on the first')
  assert.equal(cycled.prevented, 1, 'the handler must preventDefault so the browser does not also move out')
  // no focusable control left (all disabled while busy) → keep focus on the container
  const parked = driveTab(GithubDisconnectControl, { ...props, busy: true })
  assert.equal(parked.container.focused, true, 'with no focusable control the container holds focus')
  assert.equal(parked.prevented, 1, 'the container case must consume the key too')
})

test('#8670: the account-deletion dialog keeps Tab inside (executed)', () => {
  const props = {
    open: true, busy: false, error: '', result: null,
    onOpen() {}, onCancel() {}, onConfirm() {}, onDone() {},
  }
  const cycled = driveTab(DeleteAccountSection, props, { cycle: true })
  assert.equal(cycled.first.focused, true, 'Tab off the last control must land on the first')
  assert.equal(cycled.prevented, 1, 'the handler must preventDefault so the browser does not also move out')
  const parked = driveTab(DeleteAccountSection, { ...props, busy: true })
  assert.equal(parked.container.focused, true, 'with no focusable control the container holds focus')
  assert.equal(parked.prevented, 1, 'the container case must consume the key too')
  // a non-Tab key must NOT be swallowed (Escape has its own busy-guarded branch)
  let prevented = 0
  cycled.dialog.props.onKeyDown({
    key: 'Enter', shiftKey: false, currentTarget: cycled.container,
    preventDefault: () => { prevented += 1 },
  })
  assert.equal(prevented, 0, 'only Tab is consumed by the containment branch')
})

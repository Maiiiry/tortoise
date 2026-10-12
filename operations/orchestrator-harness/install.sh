#!/usr/bin/env bash
# install.sh — render + install the launchd agent for the orchestrator harness.
#
# Deliberately does NOT start the loop. It writes the plist and prints the load command.
# Starting an autonomous merge loop is an explicit act, not a side effect of installing.
#
#   ./install.sh                 # render + install (idempotent)
#   ./install.sh --uninstall     # unload + remove the plist (leaves state/logs alone)
#
# Config comes from the environment; defaults are the values this harness was built with
# (see README §4). Nothing is written outside ~/Library/LaunchAgents and $O.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

O="${O:-$HOME/orchestrator}"
REPO="${REPO:-/Users/home/eldato/tortoise}"
OR="${OR:-daniel-ospina/tortoise}"

MAX_LOAD="${MAX_LOAD:-16}"
MAX_LANES="${MAX_LANES:-4}"
MAX_REVIEW="${MAX_REVIEW:-4}"
MAX_FIX="${MAX_FIX:-3}"
MAX_FIX_TRIES="${MAX_FIX_TRIES:-3}"
MIN_SWAP_FREE="${MIN_SWAP_FREE:-600}"
BATCH="${BATCH:-2}"
CYC="${CYC:-240}"

LABEL="com.eldato.orchestrator-loop"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
TEMPLATE="$HERE/launchd.plist.template"

die() { echo "❌ $*" >&2; exit 1; }
ok()  { echo "  ✓ $*"; }
warn(){ echo "  ⚠️  $*"; }

# ── uninstall ────────────────────────────────────────────────────────────────────
if [ "${1:-}" = "--uninstall" ]; then
  launchctl unload -w "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "✅ Removed $LABEL."
  echo "   State and logs left intact: $O/{logs,state,wip}"
  exit 0
fi

# ── platform ─────────────────────────────────────────────────────────────────────
echo "==> preflight"
[ "$(uname)" = "Darwin" ] || die "this harness uses launchd + tmux; only macOS is supported"
[ -f "$TEMPLATE" ] || die "missing template: $TEMPLATE"

# ── dependencies ─────────────────────────────────────────────────────────────────
for bin in tmux gh pi; do
  command -v "$bin" >/dev/null 2>&1 && ok "$bin" || die "$bin not on PATH"
done
command -v uv >/dev/null 2>&1 && ok "uv" || warn "uv not found — lane.sh needs it to sync each worktree"
gh auth status >/dev/null 2>&1 && ok "gh authenticated" || die "gh is not authenticated"

# ── the merge authority ──────────────────────────────────────────────────────────
# The harness can review and record, but it cannot MERGE without the gate key.
if [ -n "${AI_REVIEW_GATE_KEY:-}" ]; then
  ok "AI_REVIEW_GATE_KEY present in env"
elif [ -f "$HOME/.pi/agent/.ai-review-gate-key" ]; then
  ok "gate key found at ~/.pi/agent/.ai-review-gate-key"
else
  warn "no AI_REVIEW_GATE_KEY (env or ~/.pi/agent/.ai-review-gate-key)"
  warn "  → reviews will be recorded UNSIGNED and ai-review-gate will fail: this box cannot merge."
  warn "  → the loop degrades to review/record only. Fine for evaluation; not for a merge role."
fi
[ -f "$HOME/.pi/agent/scripts/record-review.sh" ] \
  && ok "record-review.sh" \
  || warn "~/.pi/agent/scripts/record-review.sh missing — record step will fail (shipped by agent-infra)"

# ── target repo ──────────────────────────────────────────────────────────────────
git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1 || die "not a git checkout: $REPO"
git -C "$REPO" check-ignore -q .worktrees \
  && ok "$REPO/.worktrees is gitignored" \
  || die "$REPO/.worktrees is NOT gitignored — worktrees would be committed. Fix .gitignore first."
[ -f "$REPO/tools/collision_preflight.py" ] \
  && ok "collision pre-flight present" \
  || warn "tools/collision_preflight.py missing — refresh-candidates.sh will add nothing to the backlog"

# ── shipped scripts ──────────────────────────────────────────────────────────────
for f in "$HERE"/*.sh; do
  bash -n "$f" || die "syntax error in $(basename "$f")"
done
ok "all shipped scripts parse"

# ── state dir ────────────────────────────────────────────────────────────────────
mkdir -p "$O/logs" "$O/state" "$O/wip"
touch "$O/candidates.txt" "$O/dispatched.txt" "$O/checked.txt"
ok "state dir $O"

# ── render plist ─────────────────────────────────────────────────────────────────
echo "==> rendering $PLIST"
sed \
  -e "s|__O__|$O|g" \
  -e "s|__REPO__|$REPO|g" \
  -e "s|__OR__|$OR|g" \
  -e "s|__HOME__|$HOME|g" \
  -e "s|__MAX_LOAD__|$MAX_LOAD|g" \
  -e "s|__MAX_LANES__|$MAX_LANES|g" \
  -e "s|__MAX_REVIEW__|$MAX_REVIEW|g" \
  -e "s|__MAX_FIX__|$MAX_FIX|g" \
  -e "s|__MAX_FIX_TRIES__|$MAX_FIX_TRIES|g" \
  -e "s|__MIN_SWAP_FREE__|$MIN_SWAP_FREE|g" \
  -e "s|__BATCH__|$BATCH|g" \
  -e "s|__CYC__|$CYC|g" \
  "$TEMPLATE" > "$PLIST"
plutil -lint "$PLIST" >/dev/null || die "rendered plist is invalid"
ok "plist valid"

# launchd needs the harness at $O; make sure the running copy is the one installed here.
if ! diff -q "$HERE/orch-loop.sh" "$O/orch-loop.sh" >/dev/null 2>&1; then
  warn "$O/orch-loop.sh differs from the copy in this directory"
  warn "  the daemon runs \$O/orch-loop.sh — copy the shipped scripts into \$O first:"
  warn "  cp '$HERE'/*.sh '$O'/"
fi

cat <<EOF

✅ Installed $LABEL

  harness   $O
  repo      $REPO   ($OR)
  limits    lanes=$MAX_LANES review=$MAX_REVIEW fix=$MAX_FIX fix_tries=$MAX_FIX_TRIES
            max_load=$MAX_LOAD min_swap_free=${MIN_SWAP_FREE}M batch=$BATCH cycle=${CYC}s

The loop is NOT running. To start it:

    launchctl load -w "$PLIST"

Watch it (the first line of the decision log each cycle):

    tail -f "$O/logs/orch-loop.log"

Liveness — a stale heartbeat means the controller is wedged:

    echo \$(( \$(date +%s) - \$(cat "$O/heartbeat") ))   # seconds since last cycle

Stop it before handing the machine to a human, and capture dirty work (README §6):

    launchctl unload -w "$PLIST"; pkill -f orch-loop.sh; rm -f "$O/.orch.lock"
EOF

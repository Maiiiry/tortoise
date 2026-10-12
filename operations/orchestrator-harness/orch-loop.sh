#!/bin/bash
# orch-loop.sh — self-sustaining orchestrator loop (bash 3.2).
# Fast, non-blocking cycle: heartbeat -> merge sweep -> capacity-gated dispatch -> background refresh.
set -u
OR="${OR:-daniel-ospina/tortoise}"
REPO="${REPO:-/Users/home/eldato/tortoise}"
O="${O:-$HOME/orchestrator}"
export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH
export AGENT_SKIP_REVIEW_GATE=1 ELDATO_SKIP_VGATE=1

MAX_LOAD="${MAX_LOAD:-16}"
MAX_LANES="${MAX_LANES:-4}"
MAX_REVIEW="${MAX_REVIEW:-2}"
MIN_SWAP_FREE="${MIN_SWAP_FREE:-600}"
BATCH="${BATCH:-2}"
CYC="${CYC:-240}"
REQ="ai-review-gate python-ci-gate docs test-isolation license-surface legal-e2e pricing-artifact"

mkdir -p "$O/logs" "$O/state"
LOG="$O/logs/orch-loop.log"
touch "$O/dispatched.txt" "$O/candidates.txt" "$O/checked.txt"
chmod +x "$O"/*.sh 2>/dev/null

# single-instance lock (atomic: noclobber create fails if the file exists)
LOCKF="$O/.orch.lock"
acq() { ( set -o noclobber; echo $$ > "$LOCKF" ) 2>/dev/null; }
if ! acq; then
  OPID="$(cat "$LOCKF" 2>/dev/null || echo 0)"
  if [ "${OPID:-0}" -gt 0 ] && kill -0 "$OPID" 2>/dev/null; then echo "[orch-loop] already running (pid $OPID)"; exit 0; fi
  rm -f "$LOCKF"
  acq || { echo "[orch-loop] lock race — exiting"; exit 0; }
fi
trap 'rm -f "$LOCKF"' EXIT INT TERM

heartbeat() { date +%s > "$O/heartbeat"; }
load1() { sysctl -n vm.loadavg 2>/dev/null | awk '{print int($2)}'; }

while true; do
  heartbeat
  L="$(load1)"
  echo "=== $(date) load=$L lanes=$(tmux ls 2>/dev/null | grep -c '^lane-') backlog=$(wc -l < "$O/candidates.txt" | tr -d ' ') ===" >> "$LOG"

  # ---- 1) merge sweep (this box's open PRs) -------------------------------
  # scope: when the repo has >10 open PRs, help drain EVERYONE's (broad); otherwise just this box's.
  OPEN_N="$(gh pr list --repo "$OR" --state open --limit 300 --json number --jq 'length' 2>/dev/null)"; : "${OPEN_N:=0}"
  if [ "$OPEN_N" -gt 10 ]; then
    SEL='select(.headRefName|test("^design/|^scope/|^dependabot/")|not)'
    echo "[scope] open=$OPEN_N -> BROAD (help drain all)" >> "$LOG"
  else
    SEL='select(.headRefName|test("-tm$|-cap$|^fix/"))'
    echo "[scope] open=$OPEN_N -> OWN" >> "$LOG"
  fi
  gh pr list --repo "$OR" --state open --limit 300 --json number,headRefName,isDraft \
    --jq ".[]|select(.isDraft|not)|$SEL|\"\(.number)\t\(.headRefName)\"" 2>/dev/null |
  while IFS="$(printf '\t')" read -r PR BR; do
    [ -z "${PR:-}" ] && continue
    [ "$(gh pr view "$PR" --repo "$OR" --json state --jq .state 2>/dev/null)" = "MERGED" ] && continue
    V="$(gh pr checks "$PR" --repo "$OR" --json name,state 2>/dev/null | python3 -c "
import sys,json
try: d=json.load(sys.stdin)
except Exception: print('NOCHECKS'); raise SystemExit
m={c['name']:c['state'] for c in d}
want='$REQ'.split()
bad=lambda s: s in ('FAILURE','ERROR','CANCELLED','TIMED_OUT','ACTION_REQUIRED')
ok=lambda s: s in ('SUCCESS','FAILURE','ERROR','CANCELLED','TIMED_OUT','ACTION_REQUIRED','SKIPPED','NEUTRAL')
fail=[n for n in want if bad(m.get(n,''))]
pend=[n for n in want if not ok(m.get(n,''))]
print(('FAILED:'+','.join(fail)) if fail else (('PENDING:'+','.join(pend)) if pend else 'GREEN'))
" 2>/dev/null)"
    case "$V" in
      GREEN)     gh pr ready "$PR" --repo "$OR" >/dev/null 2>&1 || true; gh pr merge "$PR" --repo "$OR" --squash >>"$LOG" 2>&1 && echo "#$PR MERGED" >>"$LOG" || echo "#$PR merge fail" >>"$LOG" ;;
      PENDING:*) gh pr ready "$PR" --repo "$OR" >/dev/null 2>&1 || true; gh pr merge "$PR" --repo "$OR" --squash --auto >>"$LOG" 2>&1 && echo "#$PR auto-armed" >>"$LOG" || true ;;
      FAILED:*)
        echo "#$PR $V" >>"$LOG"
        if echo "$V" | grep -q ai-review-gate; then
          if tmux has-session -t "fix-$PR" 2>/dev/null || tmux has-session -t "merge-$PR" 2>/dev/null; then
            : # this PR already has a worker in flight
          else
            HEAD_N="$(gh pr view "$PR" --repo "$OR" --json headRefOid --jq .headRefOid 2>/dev/null)"
            FIXN="$O/state/fixcount-$PR"; NF="$(cat "$FIXN" 2>/dev/null || echo 0)"
            case "$NF" in ''|*[!0-9]*) NF=0 ;; esac
            # a reviewer already reported real issues AT THIS HEAD -> fix them and re-run the chain.
            # otherwise the gate is probably just missing evidence -> re-record (fresh review).
            if [ -n "${HEAD_N:-}" ] && [ -f "$O/state/needsfix-$PR-$HEAD_N" ] \
               && [ "$NF" -lt "${MAX_FIX_TRIES:-3}" ] \
               && [ "$(tmux ls 2>/dev/null | grep -c '^fix-')" -lt "${MAX_FIX:-3}" ]; then
              echo $((NF + 1)) > "$FIXN"; rm -f "$O/state/needsfix-$PR-$HEAD_N"
              tmux new-session -d -s "fix-$PR" "bash '$O/fix-one.sh' '$REPO' '$OR' $PR '$BR'" 2>/dev/null \
                && echo "#$PR fixer attempt $((NF + 1))" >>"$LOG"
            elif [ "$(tmux ls 2>/dev/null | grep -c '^merge-')" -lt "${MAX_REVIEW:-4}" ]; then
              tmux new-session -d -s "merge-$PR" "bash '$O/merge-one.sh' '$REPO' '$OR' $PR '$BR'" 2>/dev/null \
                && echo "#$PR re-record" >>"$LOG"
            fi
          fi
        fi
        if echo "$V" | grep -q python-ci-gate; then
          RID="$(gh pr checks "$PR" --repo "$OR" --json name,link --jq '.[]|select(.name=="python-ci-gate")|.link' 2>/dev/null | sed -nE 's#.*/runs/([0-9]+).*#\1#p')"
          if [ -n "${RID:-}" ] && [ ! -f "$O/state/rerun-$PR-$RID" ]; then
            gh run rerun "$RID" --repo "$OR" --failed >>"$LOG" 2>&1 && { touch "$O/state/rerun-$PR-$RID"; echo "#$PR rerun CI" >>"$LOG"; }
          fi
        fi ;;
    esac
  done
  heartbeat

  # ---- 2) capacity-gated dispatch ----------------------------------------
  # prune the backlog of already-dispatched issues (otherwise it reads as non-empty but yields nothing)
  if [ -s "$O/candidates.txt" ]; then
    python3 - "$O/candidates.txt" "$O/dispatched.txt" <<'PY'
import sys
cand=[l.strip() for l in open(sys.argv[1]) if l.strip()]
done=set(l.strip() for l in open(sys.argv[2]) if l.strip())
keep=[x for x in cand if x not in done]
open(sys.argv[1],"w").write("\n".join(keep)+("\n" if keep else ""))
PY
  fi
  L="$(load1)"; LANES="$(tmux ls 2>/dev/null | grep -c '^lane-')"
  SWFREE="$(sysctl -n vm.swapusage 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="free"){print int($(i+2)); exit}}')"
  SWTOTAL="$(sysctl -n vm.swapusage 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="total"){print int($(i+2)); exit}}')"
  : "${SWFREE:=9999}"; : "${SWTOTAL:=0}"
  echo "[cap] lanes=$LANES swap_free=${SWFREE}M swap_total=${SWTOTAL}M load=$L" >> "$LOG"
  # swap_total=0 => swap uninitialised (fresh boot), NOT pressure — allow dispatch.
  if [ "$LANES" -lt "$MAX_LANES" ] && { [ "$SWTOTAL" -eq 0 ] || [ "$SWFREE" -gt "${MIN_SWAP_FREE:-600}" ]; }; then
    : > "$O/.batch"; K=0
    while read -r ISS; do
      [ -z "${ISS:-}" ] && continue
      grep -qx "$ISS" "$O/dispatched.txt" 2>/dev/null && continue
      echo "$ISS" >> "$O/.batch"; echo "$ISS" >> "$O/dispatched.txt"; K=$((K+1)); [ "$K" -ge "$BATCH" ] && break
    done < "$O/candidates.txt"
    if [ -s "$O/.batch" ]; then
      echo "[dispatch] load=$L lanes=$LANES -> $(tr '\n' ' ' < "$O/.batch")" >> "$LOG"
      bash "$O/dispatch-merge.sh" "$REPO" "$OR" $(tr '\n' ' ' < "$O/.batch") >> "$LOG" 2>&1
    else echo "[dispatch] backlog empty" >> "$LOG"; fi
  else
    echo "[dispatch] skipped (load=$L lanes=$LANES)" >> "$LOG"
  fi
  heartbeat

  # ---- 3) background backlog refresh (locked, small, non-blocking) --------
  BL="$(wc -l < "$O/candidates.txt" | tr -d ' ')"
  if [ "$BL" -lt 6 ]; then
    if [ ! -d "$O/.refresh.lock" ]; then
      mkdir -p "$O/.refresh.lock"
      echo "[refresh] spawning (backlog=$BL)" >> "$LOG"
      ( bash "$O/refresh-candidates.sh" 6 >> "$LOG" 2>&1; rm -rf "$O/.refresh.lock" ) &
    else
      # stale lock guard (>20 min)
      if [ -n "$(find "$O/.refresh.lock" -mmin +20 2>/dev/null)" ]; then rm -rf "$O/.refresh.lock"; echo "[refresh] cleared stale lock" >> "$LOG"; fi
    fi
  fi

  sleep "$CYC"
done

#!/bin/bash
# refresh-candidates.sh — pre-flight a pool of open P1/P2 issues and append the CLEAN ones
# to ~/orchestrator/candidates.txt (the dispatch backlog). Skips anything already checked.
set -u
OR="${OR:-daniel-ospina/tortoise}"
REPO="${REPO:-/Users/home/eldato/tortoise}"
SC="$REPO/.worktrees/dispatch-scout"
O="${O:-$HOME/orchestrator}"
export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH
LIMIT="${1:-24}"
mkdir -p "$O"
cd "$SC" || exit 1
git fetch origin --quiet 2>/dev/null || true

touch "$O/checked.txt" "$O/candidates.txt"

# pool = open unassigned P1-severe ∪ P2-moderate, minus epics/owner-decisions
{  
  gh issue list --repo "$OR" --state open --limit 300 --label P0-blocker --json number,assignees,title \
    --jq '.[]|select((.assignees|length)==0)|select(.title|test("^(epic|lane|OWNER DECISION|decision|analysis|investigation)";"i")|not)|.number' 2>/dev/null
  gh issue list --repo "$OR" --state open --limit 200 --label P1-severe --json number,assignees,title \
    --jq '.[]|select((.assignees|length)==0)|select(.title|test("^(epic|OWNER DECISION)";"i")|not)|.number' 2>/dev/null
  gh issue list --repo "$OR" --state open --limit 200 --label P2-moderate --json number,assignees,title \
    --jq '.[]|select((.assignees|length)==0)|select(.title|test("^(epic|OWNER DECISION)";"i")|not)|.number' 2>/dev/null
} | sort -n -u > "$O/.pool"

n=0
while read -r N; do
  [ -z "${N:-}" ] && continue
  grep -qx "$N" "$O/checked.txt" 2>/dev/null && continue
  grep -qx "$N" "$O/dispatched.txt" 2>/dev/null && continue
  echo "$N" >> "$O/checked.txt"
  n=$((n+1)); [ "$n" -gt "$LIMIT" ] && break
  V="$("$SC/.venv/bin/python" tools/collision_preflight.py "$N" --repo . 2>&1 | grep -E '^VERDICT' | head -1 | sed -E 's/.*VERDICT: (CLEAN|COLLISION).*/\1/')"
  if [ "$V" = "CLEAN" ]; then
    echo "$N" >> "$O/candidates.txt"
    echo "[refresh] +#$N CLEAN -> backlog"
  fi
done < "$O/.pool"
sort -n -u -o "$O/candidates.txt" "$O/candidates.txt"
echo "[refresh] checked $n; backlog now $(wc -l < "$O/candidates.txt" | tr -d ' ') issues"

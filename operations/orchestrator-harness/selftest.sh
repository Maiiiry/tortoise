#!/usr/bin/env bash
# selftest.sh — offline checks for the shipped harness. No network, no tmux, no git writes.
#
# Every check here corresponds to a defect found in review (see the PR body / README §7): each one
# fails on the pre-fix copy and passes on the shipped one. Run this before installing on a new box.
#
#   ./selftest.sh          # run all checks
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
pass=0; fail=0
ck() { # ck <name> <0|1 result>
  if [ "$2" -eq 0 ]; then printf '  \033[32mPASS\033[0m  %s\n' "$1"; pass=$((pass+1));
  else printf '  \033[31mFAIL\033[0m  %s\n' "$1"; fail=$((fail+1)); fi
}
echo "==> offline self-test ($HERE)"

# ── 1) every script parses ───────────────────────────────────────────────────────
rc=0
for f in "$HERE"/*.sh "$HERE"/retired/*.sh; do
  [ "$(basename "$f")" = "selftest.sh" ] && continue
  bash -n "$f" || { echo "      syntax error: $f"; rc=1; }
done
ck "all shell scripts parse (bash -n)" $rc

# ── 2) exact tmux session matching ───────────────────────────────────────────────
# `tmux has-session -t fix-82` prefix-matches `fix-820`, so a guard built on it reports
# "in flight" for PR #82 whenever #820 is running and starves #82 of attention.
fake=$'fix-820\nmerge-821\nlane-5\n'
printf '%s' "$fake" | grep -qx "fix-82" && rc=1 || rc=0
ck "exact session match: fix-82 does NOT match fix-820" $rc
printf '%s' "$fake" | grep -qx "fix-820" && rc=0 || rc=1
ck "exact session match: fix-820 matches its own name" $rc

# ── 3) merge authority is fail-closed ────────────────────────────────────────────
# SKIPPED/NEUTRAL on ai-review-gate means no signed review exists; reading it as satisfied
# merges without evidence. Feed the classifier both states and require FAILED.
classify() {
  printf '%s' "$1" | python3 -c "
import sys, json
d=json.load(sys.stdin)
m={c['name']:c['state'] for c in d}
want='ai-review-gate docs'.split()
bad=lambda s: s in ('FAILURE','ERROR','CANCELLED','TIMED_OUT','ACTION_REQUIRED')
ok=lambda s: s in ('SUCCESS','FAILURE','ERROR','CANCELLED','TIMED_OUT','ACTION_REQUIRED','SKIPPED','NEUTRAL')
gate=m.get('ai-review-gate','')
if gate != 'SUCCESS' and not bad(gate):
    print('FAILED:ai-review-gate'); raise SystemExit
fail=[n for n in want if bad(m.get(n,''))]
pend=[n for n in want if not ok(m.get(n,''))]
print(('FAILED:'+','.join(fail)) if fail else (('PENDING:'+','.join(pend)) if pend else 'GREEN'))
"
}
got="$(classify '[{"name":"ai-review-gate","state":"SKIPPED"},{"name":"docs","state":"SUCCESS"}]')"
[ "$got" = "FAILED:ai-review-gate" ] && rc=0 || { rc=1; echo "      got '$got'"; }
ck "ai-review-gate SKIPPED is not treated as satisfied" $rc
got="$(classify '[{"name":"ai-review-gate","state":"SUCCESS"},{"name":"docs","state":"SKIPPED"}]')"
[ "$got" = "GREEN" ] && rc=0 || { rc=1; echo "      got '$got'"; }
ck "ai-review-gate SUCCESS + path-filtered docs SKIPPED = GREEN" $rc
got="$(classify '[{"name":"docs","state":"SUCCESS"}]')"
[ "$got" = "FAILED:ai-review-gate" ] && rc=0 || { rc=1; echo "      got '$got'"; }
ck "a missing ai-review-gate row is not treated as satisfied" $rc

# ── 4) the dispatch gate consults load, and names the binding gate ───────────────
# MAX_LOAD was rendered into the plist and documented, but never compared — so a lanes-bound
# stall logged itself as a load stall.
grep -qE '\$L" -ge "\$MAX_LOAD' "$HERE/orch-loop.sh" && rc=0 || rc=1
ck "dispatch gate compares load against MAX_LOAD" $rc
grep -q 'GATE_WHY=' "$HERE/orch-loop.sh" && rc=0 || rc=1
ck "dispatch skip message names the binding gate" $rc

# ── 5) the merge claim is atomic ─────────────────────────────────────────────────
# Two workers must not both review the same PR. The claim idiom is noclobber-create.
tmp="$(mktemp -d)"; CL="$tmp/claim"
( set -o noclobber; echo 1 > "$CL" ) 2>/dev/null; first=$?
( set -o noclobber; echo 2 > "$CL" ) 2>/dev/null; second=$?
[ "$first" -eq 0 ] && [ "$second" -ne 0 ] && rc=0 || rc=1
ck "claim lock: second concurrent claim is refused" $rc
rm -rf "$tmp"
grep -q 'mergeclaim-\$PR.lock' "$HERE/merge-one.sh" && rc=0 || rc=1
ck "merge-one.sh claims per PR before reviewing" $rc
grep -q 'mergeclaim-\$PR.lock' "$HERE/orch-loop.sh" && rc=0 || rc=1
ck "controller honours the claim written by the chain path" $rc

# ── 6) fixer attempts are only consumed by a worker that actually started ────────
grep -q 'fixer spawn FAILED (attempt not consumed' "$HERE/orch-loop.sh" && rc=0 || rc=1
ck "failed fixer spawn does not consume an attempt" $rc
grep -q 'PARKED after' "$HERE/orch-loop.sh" && rc=0 || rc=1
ck "reaching MAX_FIX_TRIES parks the PR instead of re-reviewing forever" $rc

# ── 7) the plist renderer escapes sed metacharacters ────────────────────────────
# A bare & in a path expanded to the matched text, producing a valid plist with wrong paths.
esc() { printf '%s' "$1" | sed -e 's/[\\&|]/\\&/g'; }
rendered="$(printf '%s\n' '__O__' | sed -e "s|__O__|$(esc '/tmp/a&b')|g")"
[ "$rendered" = "/tmp/a&b" ] && rc=0 || { rc=1; echo "      got '$rendered'"; }
ck "renderer escapes & so a path containing it survives" $rc

# ── 8) the plist template and the renderer agree on the placeholder set ─────────
tpl="$(grep -oE '__[A-Z_]+__' "$HERE/launchd.plist.template" | sort -u)"
sedlist="$(sed -n '/^sed \\/,/launchd.plist.template/p' "$HERE/install.sh" | grep -oE '__[A-Z_]+__' | sort -u)"
[ -n "$tpl" ] && [ "$tpl" = "$sedlist" ] && rc=0 || { rc=1; echo "      template-only: $(comm -23 <(printf '%s\n' "$tpl") <(printf '%s\n' "$sedlist") | tr '\n' ' ')"; }
ck "every template placeholder is substituted by install.sh" $rc

# ── 9) no credential material shipped ───────────────────────────────────────────
rc=0
if grep -rqE 'github_pat_[A-Za-z0-9_]{20,}|ghp_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|BEGIN [A-Z ]*PRIVATE KEY' "$HERE" 2>/dev/null; then rc=1; fi
ck "no credential-shaped strings in the shipped tree" $rc

echo
echo "==> $pass passed, $fail failed"
[ "$fail" -eq 0 ]

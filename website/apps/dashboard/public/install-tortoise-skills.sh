#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Premise Labs
#
# ── Licence: MIT (in-band, not a served sidecar) ───────────────────────────
# This installer is part of the Tortoise CONSUMER surface and is licensed under
# MIT — it does NOT inherit the repository's BSL, which governs the engine.
#
# The notice is reproduced INSIDE this file on purpose. A `curl … | bash` user
# receives this script's bytes and nothing else: a licence file served next to
# the script (or next to the skills) never travels with a piped download, so
# MIT's "included in all copies" condition can only be met for a single-file
# script by carrying the notice in the file itself. (Contrast #526's client
# dist, where client/LICENSE ships INSIDE the wheel — the notice is packaged
# with the artifact.) The same MIT text is served beside the skills at
# https://app.premiselabs.co/skills/LICENSE.
#
# MIT License
#
# Copyright (c) 2026 Premise Labs
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
# ───────────────────────────────────────────────────────────────────────────
# install-tortoise-skills.sh — install the official Tortoise agent skills.
#
# The skills are downloaded from the Tortoise product site
# (https://app.premiselabs.co/skills/<name>/SKILL.md) — no git clone, no
# third-party repo.
#
# ── WHICH REPO AUTHORS WHAT (#4643) ────────────────────────────────────────
# Two repos hold Tortoise consumer artifacts, and editing the wrong one is a
# drift rather than a fork. This header is the statement a contributor reads:
#
#   * The three reusable skills this script installs are AUTHORED in the public
#     MIT repo https://github.com/daniel-ospina/tortoise-skills-and-integrations.
#     The copies served here are a DERIVED MIRROR: they are pinned in
#     `config/served-skills-upstream.yml` and gated fail-closed by
#     `tools/served_skills_parity.py`: the offline served↔pin check runs per-PR,
#     and the upstream (honesty/currency/scope) arm runs before the dashboard
#     deploy and daily on a schedule. `--sync` re-derives. To change one of those
#     skills, edit it UPSTREAM and re-derive — never edit a served copy here.
#   * THIS script and the onboarding instructions are authored HERE, in
#     https://github.com/daniel-ospina/tortoise — the script at
#     `website/apps/dashboard/public/install-tortoise-skills.sh` (its source of
#     truth was moved out of the MIT repo by #3324 because that copy went
#     stale) and the onboarding document at `tortoise/onboarding/SKILL.md`
#     (served, but not installed by this script since #4365).
#
# The ownership rationale and the artifacts NOT covered by the pin:
# `docs/license-notes.md` §7.
#
# Project-scoped for Claude Code / Codex / Cursor (installs into the current
# project's skills dir — version-controllable, non-destructive to the
# machine); personal for Pi (~/.pi/agent/skills — the only supported path).
#
# Usage:
#   curl -fsSL https://app.premiselabs.co/install-tortoise-skills.sh | bash -s -- --harness claude
#   (or codex | cursor | pi)
#
# Idempotent: re-running updates the skills in place and refreshes the version
# stamp. A destination that already holds a file we did not write is backed up
# once and then MERGED — our payload becomes the file, foreign frontmatter keys
# and the foreign banner/trailer blocks are preserved, never silently dropped
# (#4327). Prints the verify step.
set -euo pipefail

SKILLS_VERSION="v3"   # bump when the skill set changes
SKILLS_BASE="https://app.premiselabs.co/skills"
# v3 (#4365): -tortoise-onboarding. Onboarding is a one-time SETUP FLOW, not a
# reusable capability, so it is NOT installed into a harness's skills
# namespace — it is DELIVERED as instructions (the served document at
# $SKILLS_BASE/tortoise-onboarding/SKILL.md, also printed by `tortoise init`)
# plus the per-harness MCP config the dashboard's universal command writes.
# This installer ships the three reusable capabilities only. v2 (#1998 W2) had
# added the 4th; it is also the one basename with no upstream counterpart, so
# it widened a flat, shared namespace this installer only MERGES into (#4327).
SKILLS=(how-to-use-tortoise tortoise-decide tortoise-file-finding)

usage() {
  cat <<'HELP'
Usage: install-tortoise-skills.sh --harness claude|codex|cursor|pi

Installs the official Tortoise skills into the harness's skills directory:
  claude -> .claude/skills    (project)
  codex  -> .agents/skills    (project)  — Codex's documented skill root (#2329);
                                       (Codex also scans ~/.agents/skills — this
                                       installer writes the project root only)
  cursor -> .cursor/skills   (project)
  pi     -> ~/.pi/agent/skills (personal)

Run: curl -fsSL https://app.premiselabs.co/install-tortoise-skills.sh | bash -s -- --harness <harness>
HELP
  exit 0
}

HARNESS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --harness) [ $# -ge 2 ] || { echo "Missing value for --harness" >&2; exit 2; }
               HARNESS="${2}"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [ -z "$HARNESS" ]; then
  echo "Usage: install-tortoise-skills.sh --harness claude|codex|cursor|pi" >&2
  exit 2
fi

case "$HARNESS" in
  claude) DEST=".claude/skills" ;;
  codex)  DEST=".agents/skills" ;;
  cursor) DEST=".cursor/skills" ;;
  pi)     DEST="$HOME/.pi/agent/skills" ;;
  *) echo "Unknown harness: $HARNESS (expected claude|codex|cursor|pi)" >&2; exit 2 ;;
esac

echo "Installing Tortoise skills (${SKILLS_VERSION}) into: $DEST"
mkdir -p "$DEST"

# Version stamp (#3): record the installed version in a SIDECAR, never in the
# skill bodies — the served SKILL.md files must stay byte-identical to their
# originals (the `^name:` payload check below and the canonical<->mirror parity
# test both depend on that). Read any previous stamp first so a re-run can
# report an upgrade and flag an on-disk copy that drifted.
STAMP="$DEST/.tortoise-skills-version"
prev_version=""
if [ -f "$STAMP" ]; then
  # `awk ... exit` (not `sed | head`) — reading an advisory file must never
  # abort the install under `set -euo pipefail`, and an unreadable stamp is
  # not fatal.
  prev_version="$(awk -F= '/^skills_version=/{print $2; exit}' "$STAMP" 2>/dev/null || true)"
fi

# Cross-platform digest (macOS ships shasum, GNU userland ships sha256sum).
# Empty when neither exists — the stamp then records the version only, rather
# than advertising content identity it cannot compute.
if command -v sha256sum >/dev/null 2>&1; then SHA_TOOL="sha256sum"
elif command -v shasum >/dev/null 2>&1; then SHA_TOOL="shasum"
else SHA_TOOL=""; fi

sha256_of() {
  case "$SHA_TOOL" in
    sha256sum) sha256sum "$1" | cut -d' ' -f1 ;;
    shasum)    shasum -a 256 "$1" | cut -d' ' -f1 ;;
    *)         return 1 ;;
  esac
}

# ── Foreign-content preservation (#4327) ──────────────────────────────────
# $DEST is a SHARED, flat namespace. On the measured machine a different
# owner's tool (agent-infra) had already written three of these basenames,
# and the 2026-09-20 install replaced them verbatim: no merge, no backup, no
# message, silently stripping two machine-wide conventions carried by 95-96
# of 122 installed skills — a `subjects.team:` frontmatter key and the
# `⛔ … MUST be read in full — not skimmed.` banner + its trailing line.
#
# Chosen behaviour (research + precedent in the PR body): a `curl | bash`
# install must stay non-interactive, so a hard refusal is out; a preserving
# write is the safe default. Concretely — BACK UP a pre-existing copy once,
# then MERGE: the payload becomes the file, but every foreign frontmatter KEY
# (one the payload does not define) and every foreign seam BLOCK (the leading
# blockquote run after the frontmatter, and a trailing blockquote run after a
# `---` rule) is kept. The seams are positional on purpose: they are the only
# places an external tool can annotate a foreign file without its text being
# read as the skill's own instructions, so preserving them cannot union two
# whole documents. The positional rule is deliberately CONSERVATIVE: a banner
# is kept only when it is the blockquote run that OPENS the body (one placed
# after a `# Title` is not detected), and a trailer only when it is a
# blockquote run directly after a `---` rule (a plain-paragraph trailer is not
# detected). Widening either seam would start reading ordinary body content as
# a foreign annotation; the `.bak` still holds anything the rule misses.
#
# The merge is ADDITIVE-PRESERVING: it can retain content that is merely
# stale (the `.bak` and the printed report make that visible), whereas
# replacing deletes unrecoverable foreign content. That is the correct
# direction for a shared namespace.
#
# merge_skill_file <incoming-payload> <existing-file> <report-file>
#   → merged content on stdout; one `preserved-*` line per kept item in the
#     report file.
merge_skill_file() {
  awk -v rpt="$3" '
    function keyof(s) {
      if (s ~ /^[A-Za-z0-9_.-]+:/) return substr(s, 1, index(s, ":") - 1)
      return ""
    }
    FNR == 1 { f++ }
    { if (f == 1) p[++pn] = $0; else e[++en] = $0 }
    END {
      printf "" > rpt

      # frontmatter bounds: opening `---` … next `---`
      if (pn >= 1 && p[1] ~ /^---[ \t]*$/)
        for (i = 2; i <= pn; i++) if (p[i] ~ /^---[ \t]*$/) { p_end = i; break }
      if (en >= 1 && e[1] ~ /^---[ \t]*$/)
        for (i = 2; i <= en; i++) if (e[i] ~ /^---[ \t]*$/) { e_end = i; break }

      # payload + existing frontmatter entries (a key owns its indented
      # continuation lines, so block lists survive intact)
      for (i = 2; i < p_end; i++) {
        k = keyof(p[i])
        if (k != "") { pfn++; pk[pfn] = k; ptxt[pfn] = p[i]; pset[k] = 1 }
        else if (pfn > 0) ptxt[pfn] = ptxt[pfn] "\n" p[i]
      }
      for (i = 2; i < e_end; i++) {
        k = keyof(e[i])
        if (k != "") { efn++; ek[efn] = k; etxt[efn] = e[i] }
        else if (efn > 0) etxt[efn] = etxt[efn] "\n" e[i]
      }

      # every payload body line — the "is this already ours?" test
      pbs = p_end + 1
      for (i = pbs; i <= pn; i++) pbody[p[i]] = 1
      ebs = e_end + 1

      # ── frontmatter: payload keys, then foreign keys appended ─────
      if (p_end > 0) {
        print p[1]
        for (i = 1; i <= pfn; i++) print ptxt[i]
        for (i = 1; i <= efn; i++)
          if (!(ek[i] in pset)) {
            print etxt[i]
            printf "preserved-frontmatter: %s\n", ek[i] > rpt
          }
        print p[p_end]
      }

      # ── foreign banner: leading blockquote run of the existing body ──
      j = ebs
      while (j <= en && e[j] ~ /^[ \t]*$/) j++
      runn = 0
      while (j <= en && (e[j] ~ /^[ \t]*>/ || e[j] ~ /^[ \t]*$/)) {
        runn++; run[runn] = e[j]; j++
      }
      # keep the blockquote lines not already ours, plus the blank lines that
      # separated them (a two-paragraph banner stays two paragraphs)
      lastkept = 0
      for (i = 1; i <= runn; i++)
        if (run[i] ~ /^[ \t]*>/ && !(run[i] in pbody)) {
          if (lastkept > 0) {
            gap = 0
            for (jj = lastkept + 1; jj < i; jj++)
              if (run[jj] ~ /^[ \t]*$/) gap = 1
            if (gap) { fpre++; fpreline[fpre] = "" }
          }
          fpre++; fpreline[fpre] = run[i]
          lastkept = i
        }
      if (fpre > 0) {
        for (i = 1; i <= fpre; i++) print fpreline[i]
        print ""
        printf "preserved-banner: %d line(s)\n", fpre > rpt
        # the banner now occupies the payload own leading blank, so do not
        # emit it twice (keeps the merged shape identical across re-runs)
        while (pbs <= pn && p[pbs] ~ /^[ \t]*$/) pbs++
      }

      # ── payload body ─────────────────────────────────────────────
      for (i = pbs; i <= pn; i++) print p[i]

      # ── foreign trailer: trailing blockquote run after a `---` rule ──
      k = en
      while (k >= ebs && e[k] ~ /^[ \t]*$/) k--
      if (k >= ebs && e[k] ~ /^[ \t]*>/) {
        r2 = 0
        while (k >= ebs && e[k] ~ /^[ \t]*>/) { r2++; tmp[r2] = e[k]; k-- }
        kk = k
        while (kk >= ebs && e[kk] ~ /^[ \t]*$/) kk--
        if (kk >= ebs && e[kk] ~ /^---[ \t]*$/)
          for (i = r2; i >= 1; i--)
            if (!(tmp[i] in pbody)) {
              fepi++; fepiline[fepi] = tmp[i]
            }
      }
      if (fepi > 0) {
        print "---"
        for (i = 1; i <= fepi; i++) print fepiline[i]
        printf "preserved-trailer: %d line(s)\n", fepi > rpt
      }
    }
  ' "$1" "$2"
}

for s in "${SKILLS[@]}"; do
  mkdir -p "$DEST/$s"
  # mktemp (unpredictable name) so a planted `SKILL.md.tmp` symlink/hardlink in
  # an untrusted project clone cannot make curl clobber an arbitrary file.
  tmp="$(mktemp "$DEST/$s/SKILL.md.XXXXXX" 2>/dev/null || true)"
  if [ -n "$tmp" ] && curl -fsSL --max-time 20 "$SKILLS_BASE/$s/SKILL.md" -o "$tmp" \
      && [ ! -L "$tmp" ] && grep -q "^name: $s$" "$tmp"; then
    # mktemp creates 0600; keep the payload readable like the pre-mktemp
    # `curl -o` temp was (same reason the stamp temp gets chmod 0644 below).
    # Hoisted above BOTH write paths below: a fresh destination is `mv`d from
    # this file, so a first install would otherwise land 0600.
    chmod 0644 "$tmp" 2>/dev/null || true
    dest="$DEST/$s/SKILL.md"
    if [ ! -f "$dest" ]; then
      mv "$tmp" "$dest"
      echo "  ✓ $s"
      continue
    fi

    # Drift detection (#3): compare the on-disk copy against the digest the
    # last install recorded — content, not version, so a local edit is caught
    # even across a version bump. With no recorded digest (an older or manual
    # install) fall back to comparing against the incoming file.
    if [ -f "$DEST/$s/SKILL.md" ]; then
      cmp_rc=0
      cmp -s "$DEST/$s/SKILL.md" "$tmp" 2>/dev/null || cmp_rc=$?
      if [ "$cmp_rc" -eq 0 ]; then
        : # already identical to the incoming file — nothing can be lost
      else
        recorded=""
        if [ -f "$STAMP" ]; then
          recorded="$(awk -F= -v k="sha256.$s" '$1 == k {print $2; exit}' "$STAMP" 2>/dev/null || true)"
        fi
        disk_hash="$(sha256_of "$DEST/$s/SKILL.md" 2>/dev/null || true)"
        if [ -n "$recorded" ] && [ -n "$disk_hash" ] && [ "$disk_hash" != "$recorded" ]; then
          # `merging over it`, not `overwriting`: the write path below MERGES
          # preserving foreign content and keeps a first-backup-wins `.bak`.
          echo "  ⚠ $s — installed copy was edited locally; merging over it" >&2
        elif [ -z "$recorded" ]; then
          echo "  ⚠ $s — merging over a differing on-disk copy from an older/manual install" >&2
        fi
      fi
    fi

    # Destination exists. Merge our payload with whatever is there, keeping
    # foreign content (above), and back the pre-existing copy up ONCE —
    # first-backup-wins leaves the pre-Tortoise revision recoverable across
    # re-runs instead of letting a re-run overwrite the only copy of it.
    merged="$DEST/$s/SKILL.md.merged.$$"
    report="$DEST/$s/SKILL.md.preserved.$$"
    if ! merge_skill_file "$tmp" "$dest" "$report" > "$merged"; then
      rm -f "$tmp" "$merged" "$report"
      echo "  ✗ $s — could not merge into the existing $dest" >&2
      exit 1
    fi

    if cmp -s "$dest" "$merged"; then
      # Already current — our content plus the preserved foreign content.
      rm -f "$tmp" "$merged" "$report"
      echo "  ✓ $s (already current)"
      continue
    fi

    backed_up=""
    if [ ! -e "$dest.bak" ]; then
      if ! cp -p "$dest" "$dest.bak"; then
        rm -f "$tmp" "$merged" "$report"
        echo "  ✗ $s — could not back up $dest to $dest.bak" >&2
        exit 1
      fi
      backed_up="$dest.bak"
    fi
    if ! mv "$merged" "$dest"; then
      rm -f "$tmp" "$merged" "$report"
      echo "  ✗ $s — could not write the merged skill to $dest" >&2
      exit 1
    fi
    rm -f "$tmp"
    echo "  ✓ $s (merged)"
    if [ -n "$backed_up" ]; then
      echo "      · backed up existing copy → $backed_up"
    else
      echo "      · existing backup kept → $dest.bak"
    fi
    # Report the foreign content explicitly surfaced by the merge (the same
    # logic that preserved it), so a destructive-looking install is visibly
    # not destructive.
    if [ -s "$report" ]; then
      while IFS= read -r line; do echo "      · $line"; done < "$report"
    fi
    rm -f "$report"
  else
    [ -n "$tmp" ] && rm -f "$tmp"
    echo "  ✗ $s — download failed or payload was not the skill file ($SKILLS_BASE/$s/SKILL.md)" >&2
    exit 1
  fi
done

# #2329/#2330 (codex): Codex reads repo-root AGENTS.md as its standing
# instructions (it auto-loads per session; there is NO skills-declaration
# syntax in AGENTS.md). Emit a Tortoise block so every session knows when and
# how to use the graph — idempotent (marker-delimited replace).
#
# #3429: the block is VERSIONED. The markers make a re-run idempotent, but
# nothing made a STALE block DETECTABLE — and a repo provisioned before the
# text changed keeps re-reading the old block forever, because the file is
# someone else's and the installer is the only thing that rewrites it. The
# version line is inside the marker window, so the replace below refreshes it
# too. BUMP `AGENTS_BLOCK_VERSION` whenever the block's text changes; the
# upgrade path needs nothing else.
AGENTS_BLOCK_VERSION="2"

emit_codex_agents_block() {
  local file="AGENTS.md"
  local ms="<!-- tortoise:start -->"
  local me="<!-- tortoise:end -->"
  local tmpdir block pre post target
  tmpdir="$(mktemp -d)" || return 1
  block="$tmpdir/block"; pre="$tmpdir/pre"; post="$tmpdir/post"
  # ── PRE-STATE: read BEFORE anything is written (after the write the file
  # always looks current). `state` is decided with the SAME line-anchored test
  # the replace uses: an unanchored `grep -qF` matched a marker mid-line or in
  # prose, chose the replace branch, and the anchored awk split then found no
  # pair — duplicating the block while reporting success.
  local state="none" prev_version="" same_bytes=0
  local ms_re="^${ms}\r?$" me_re="^${me}\r?$"
  # Resolve a symlinked AGENTS.md (monorepos point AGENTS.md at a shared
  # file) — refresh the TARGET, never clobber the link.
  target="$file"
  [ -L "$file" ] && target="$(readlink "$file")"
  printf '%s\n' \
"${ms}
<!-- tortoise:block v${AGENTS_BLOCK_VERSION} -->
# Tortoise — agent memory (this project is connected to Tortoise Cloud)
- If the Tortoise MCP tools are available in this session (search/query/health +
  graph-write tools) and these skills (how-to-use-tortoise, tortoise-decide,
  tortoise-file-finding) are installed, use the graph without
  being asked to: after a decision or a research finding, file it; search the graph
  before answering from memory; say plainly when nothing relevant is stored.
- When the user says \"Set up Tortoise\", follow the onboarding instructions at
  $SKILLS_BASE/tortoise-onboarding/SKILL.md. Write the MCP config FIRST, then
  restart the client so the new session reads it — the server is loaded at
  session start, so the rest of THIS session cannot see it. Only in that NEW
  session run the verify with tortoise_health, and report done from there.
  Onboarding is delivered as INSTRUCTIONS, not as an installed skill.
- First-time MCP calls may prompt for approval — tortoise_health and the read tools
  are safe to allow.
${me}" > "$block"

  if [ -f "$target" ]; then
    state="$(awk -v msre="$ms_re" -v mere="$me_re" '
      $0 ~ msre { starts++; if (!startline) startline = NR }
      $0 ~ mere { ends++;   if (!endline)   endline   = NR }
      END {
        if (!starts && !ends)         { print "none";      exit }
        if (starts != 1 || ends != 1) { print "malformed"; exit }
        if (startline > endline)      { print "malformed"; exit }
        print "paired"
      }' "$target")"
  fi

  if [ "$state" = "paired" ]; then
    # The version is read INSIDE the window (a token in prose elsewhere must not
    # read as "this block is current"), and the bytes are compared with what we
    # would write: a version line is evidence of ORDER, never proof that the
    # text matches, so "already current" is decided by the text itself.
    prev_version="$(awk -v msre="$ms_re" -v mere="$me_re" '
      $0 ~ msre { inb = 1; next }
      $0 ~ mere { inb = 0; next }
      inb && match($0, /tortoise:block v[0-9]+ -->/) {
        v = substr($0, RSTART, RLENGTH); gsub(/[^0-9]/, "", v); print v; exit
      }' "$target")"
    : > "$tmpdir/window"
    awk -v msre="$ms_re" -v mere="$me_re" '
      $0 ~ msre { inb = 1; print; next }
      $0 ~ mere { print; inb = 0; next }
      inb { print }' "$target" > "$tmpdir/window"
    if cmp -s "$tmpdir/window" "$block"; then same_bytes=1; fi
  fi

  if [ "$state" = "malformed" ]; then
    # Markers present but not ONE clean start→end pair: a stray start swallows
    # everything to EOF, a stray end makes the split drop the tail, and either
    # way appending leaves the stale block in place. Refuse and say what to fix
    # — a truthful failure beats a success report over a duplicated block.
    echo "  ✗ AGENTS.md — the Tortoise block markers are MALFORMED" >&2
    echo "      found $(grep -cE "$ms_re" "$target" || true) start / $(grep -cE "$me_re" "$target" || true) end marker line(s)" >&2
    echo "      expected exactly one ${ms} before exactly one ${me}, each on its own line." >&2
    echo "      NOT rewritten: appending would leave the stale block in place." >&2
    echo "      Fix the markers by hand (or delete the whole block), then re-run." >&2
    rm -rf "$tmpdir"
    return 1
  fi

  if [ "$state" = "paired" ] && [ "$same_bytes" -eq 0 ]; then
    # In-place replace between the markers — never touches content outside them.
    : > "$pre"; : > "$post"
    awk -v msre="$ms_re" -v mere="$me_re" -v pre="$pre" -v post="$post" '
      BEGIN { inmarker = 0; ended = 0 }
      $0 ~ msre { inmarker = 1; next }
      inmarker && $0 ~ mere { inmarker = 0; ended = 1; next }
      { if (ended) print > post; else if (!inmarker) print > pre }
    ' "$target"
    # cp -p first preserves the file's mode (a 0600 AGENTS.md stays 0600) and
    # ownership; cat then overwrites content in place.
    cp -p "$target" "$target.tmp"
    cat "$pre" "$block" "$post" > "$target.tmp"
    mv "$target.tmp" "$target"
  elif [ "$state" = "none" ]; then
    if [ -f "$target" ]; then
      printf '\n\n' >> "$target"
      cat "$block" >> "$target"
    else
      cat "$block" > "$target"
    fi
  fi
  rm -rf "$tmpdir"

  # ── REPORT: derived from what was WRITTEN and what was there before, never
  # from the version token alone (that reported "already current" while it was
  # rewriting the body whenever the text changed without a bump).
  v="$AGENTS_BLOCK_VERSION"
  if [ "$state" = "paired" ] && [ "$same_bytes" -eq 1 ]; then
    echo "  ✓ AGENTS.md — Tortoise block v$v (already current)"
  elif [ "$state" = "none" ]; then
    echo "  ✓ AGENTS.md — Tortoise standing instructions created (block v$v)"
  elif [ -z "$prev_version" ]; then
    echo "  ✓ AGENTS.md — Tortoise block upgraded (unversioned → v$v)"
  elif [ "$prev_version" -lt "$v" ]; then
    echo "  ✓ AGENTS.md — Tortoise block upgraded v$prev_version → v$v"
  elif [ "$prev_version" -gt "$v" ]; then
    # A pinned/cached/rolled-back installer must not silently DOWNGRADE a
    # project's standing instructions, still less call it an upgrade.
    echo "  ✓ AGENTS.md — Tortoise block written at v$v (the file had v$prev_version: this installer is OLDER than the block it replaced)"
  else
    echo "  ✓ AGENTS.md — Tortoise block refreshed (v$v — same version, updated text)"
  fi
}

# Verify the target dir — we KNOW where we wrote, so this is a local check.
missing=()
for s in "${SKILLS[@]}"; do
  [ -f "$DEST/$s/SKILL.md" ] || missing+=("$s")
done

if [ ${#missing[@]} -eq 0 ]; then
  # Version stamp (#3): sidecar manifest, written only after every skill
  # verified — a failed install never claims a version it did not place.
  # Written to a mktemp-created temp file then `mv`d into place: `mv` REPLACES
  # a symlink instead of writing through it, the write is atomic (an
  # interrupted run cannot leave a truncated stamp), and the unpredictable
  # mktemp name also defeats a pre-planted `$STAMP.tmp` symlink/hardlink.
  stamp_tmp=""
  stamp_ok=0
  if [ ! -d "$STAMP" ]; then
    stamp_tmp="$(mktemp "${STAMP}.XXXXXX" 2>/dev/null || true)"
  fi
  if [ -n "$stamp_tmp" ] && {
    printf '%s\n' \
      "# Tortoise skills install manifest — written by install-tortoise-skills.sh." \
      "# Do not edit by hand; re-run the installer to refresh this stamp." \
      "skills_version=$SKILLS_VERSION" \
      "harness=$HARNESS" \
      "source=$SKILLS_BASE" \
      "skills=${SKILLS[*]}"
    if [ -n "$SHA_TOOL" ]; then
      for s in "${SKILLS[@]}"; do
        printf 'sha256.%s=%s\n' "$s" "$(sha256_of "$DEST/$s/SKILL.md")"
      done
    fi
  } > "$stamp_tmp" 2>/dev/null; then
    # mktemp creates 0600; keep the stamp world-readable like a normal
    # redirection would, so a shared/CI checkout can still read it.
    chmod 0644 "$stamp_tmp" 2>/dev/null || true
    if mv -f "$stamp_tmp" "$STAMP" 2>/dev/null \
        && [ -f "$STAMP" ] && [ ! -L "$STAMP" ]; then
      stamp_tmp=""
      stamp_ok=1
    fi
  fi
  [ -n "$stamp_tmp" ] && rm -f "$stamp_tmp" || true
  echo ""
  echo "✅ Tortoise skills installed to $DEST"
  echo "   ${SKILLS[*]} (${SKILLS_VERSION})"
  if [ "$stamp_ok" -eq 1 ]; then
    if [ -n "$prev_version" ] && [ "$prev_version" != "$SKILLS_VERSION" ]; then
      echo "   ⬆  version stamp updated: $prev_version -> $SKILLS_VERSION"
    fi
    echo "   version stamp: $STAMP (cat it to see what is installed)"
  else
    echo "   ⚠ could not write the version stamp to $STAMP" >&2
  fi
  echo ""
  # #4365: onboarding is NOT one of the installed skills — say where it lives,
  # unconditionally (before only the codex harness learned it, via AGENTS.md).
  echo "Onboarding is NOT a skill — it is the instructions your agent reads:"
  echo "   $SKILLS_BASE/tortoise-onboarding/SKILL.md"
  echo ""
  # #4365: an earlier v2 installer wrote tortoise-onboarding into this same
  # namespace. #4327 forbids deleting content we did not write, so it stays —
  # and saying nothing would leave the user with TWO live onboarding artifacts,
  # which is the thing #4365 exists to end.
  if [ -f "$DEST/tortoise-onboarding/SKILL.md" ]; then
    echo "⚠️  A superseded copy is still at $DEST/tortoise-onboarding/SKILL.md"
    echo "    Onboarding is no longer an installed skill; this copy is stale."
    echo "    Remove it:  rm -rf \"$DEST/tortoise-onboarding\""
    echo ""
  fi
  echo "Next: restart your agent, then confirm the skills are listed:"
  case "$HARNESS" in
    claude) echo "   claude — the skills appear under /skills" ;;
    codex)  echo "   codex — open the project in Codex and check /skills, or ask the agent \"Set up Tortoise\"" ;;
    cursor) echo "   cursor — skills load from .cursor/skills" ;;
    pi)
      echo "   pi — skills load from ~/.pi/agent/skills when the session starts."
      echo "   pi — verify the MCP CONNECTION too (installed skills are not proof it connected):"
      echo "        ask the agent to call tortoise_health — \"ok\" is a graph-status reply, not a tool error."
      echo "        If tortoise_health is missing, the first connect to the cold hosted machine"
      echo "        failed: have the agent run  mcp_load tortoise  and call tortoise_health again."
      ;;
  esac

  # #2329/#2330: Codex standing instructions — only for the codex harness
  # (other harnesses have their own memory mechanisms; a stray AGENTS.md
  # would change every agent's behavior in this project).
  if [ "$HARNESS" = "codex" ]; then
    # #3429: a failure here is a REAL failure (malformed markers, nothing
    # written) — surface it as such instead of letting set -e's abort happen
    # with no explanation of what remains broken in the project.
    emit_codex_agents_block || exit 1
  fi
else
  echo ""
  echo "⚠️  Some skills did not verify in $DEST: ${missing[*]}" >&2
  echo "   Check the directory + permissions, then re-run the installer." >&2
  exit 1
fi

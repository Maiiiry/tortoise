## Summary

<!-- What this PR does and why. Reference the tracked issue. -->

<!-- The closing keyword must be on its OWN BARE LINE, exactly like the one
     above: no backticks, no code fence, no blockquote. GitHub ignores a closing
     keyword inside an inline code span or a fenced block, so a body ending in
     `` `Closes #123`. `` does NOT close #123 — the issue stays OPEN after your
     fix merges. That is what happened on PR #2949 (#2892 stayed open for a
     month with its fix already in `main`). `pr-close-keyword.yml` fails this PR
     if the only closing keyword in the body is inert. A PR that should not close
     its issue says `Refs #123` instead. -->

Closes #<issue-number>

## Contribution license

By submitting this PR, I donate this contribution to the Tortoise project under
the **BSD 3-Clause License** (or public domain — see CONTRIBUTING.md):

- [ ] I donate this contribution under the BSD 3-Clause License (this project is
      BSL 1.1 → MPL 2.0; see CONTRIBUTING.md → Contribution license note)

## Checklist

- [ ] Change is linked to a tracked issue
- [ ] Tests pass locally (`uv run pytest tests/ -v` where applicable)
- [ ] CI is green
- [ ] **If this PR changes the MCP tool or SDK method surface** (`tortoise/tool_registry.py`,
      `tortoise/sdk.py`, `config/surface-manifest.yml`): Daniel's approval is **recorded**
      before the re-cut — you may not add or remove a tool or method without it. The surface is
      the contract every agent and integration is built on, so a change to it materially affects
      customer outcomes. See
      `CONTRIBUTING.md` → "The MCP tool surface and public SDK methods cannot grow by accident".
      (`surface-guard` is a **drift** control, not an approval gate — a green run is not consent.)

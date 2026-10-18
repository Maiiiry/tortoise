---
title: "Reading why an org is flagged — the abuse state read (#4874)"
type: operations
domain: platform
doc_status: live
subjects.team: epistemic-team
aboutSubjects: tortoise-hosted-api, abuse-engine
aboutObjects: tortoise, supabase/migrations/0015_abuse_events.sql
created: 2026-10-18
updated: 2026-10-18
---

# Reading why an org is flagged — the abuse state read (#4874)

An org reads `"status": "flagged"` on `GET /v1/team`. That status is **derived
from one column** — `flagged_at IS NOT NULL` — and carries no reason. The reason
(the rule that fired, the count that breached it, the threshold and the staging
window) IS recorded: `AbuseEngine.flag_org` writes it into the `abuse_events`
row (`details = {rule, count, threshold, window_s}`). Until #4874 the only way
to read it back was a hand-written query against `abuse_events`.

## The read

```bash
curl -sS "$API/v1/internal/abuse/state?org_id=<org-id>" \
  -H "Authorization: Bearer $FASTAPI_INTERNAL_KEY" \
  --max-time 10 | jq
```

```json
{
  "org_id": "org-…",
  "status": "flagged",
  "flagged_at": "2026-09-23T08:30:35+00:00",
  "suspended": false,
  "flags": [
    {"rule": "point_create", "count": 612, "threshold": 500,
     "window_s": 3600, "since": "2026-09-23T08:30:35+00:00"}
  ],
  "org_state_error": null,
  "flags_error": null
}
```

* `status` mirrors `GET /v1/team`'s derivation exactly (`flagged_at IS NOT
  NULL`), so the two cannot disagree about the chip they describe.
* `flags` lists the **active** episodes, newest first. "Active" is the
  enforcement path's own rule (`latest_flag_at`): the newest `flag` row for a
  rule, unless a `flag_clear` lands at or after it. A test pins the two
  definitions together.
* `count` / `threshold` / `window_s` are the values **recorded at flag time**.
  A flag row written before those fields existed reports `null` — the window it
  originally breached is not recoverable later, and a recomputed number would
  be worse than none.
* `suspended` is the store's own durable read. In registry/selfhost mode the
  **episode rows are in-process**, so after a restart `flags` can be empty while
  the durable `flagged_at` prop still says flagged. The durable store is
  authoritative.

## Why this is operator-only

`point_create` / `key_create` are our internal vocabulary and are deliberately
absent from every **customer** surface. `GET /v1/team/alerts` returns a
customer-facing message with no rule token for exactly this reason (see
`abuse._alert_dict`) — a human operator needs the rule; the customer is told
what happened. That is why #4874 added this read here rather than widening
`/v1/team`.

Auth is `_check_internal` (`FASTAPI_INTERNAL_KEY`) — an org-scoped `tt_` key
cannot satisfy it, and an unconfigured key answers **503**, never open.

## Reading a failure correctly

A failed read is never rendered as "nothing is flagged":

* store error → `flags: null` **and** `flags_error` set;
* org-state error → `status: null` and `org_state_error` set.

No active episode is `flags: []` with no error. Keep those two apart: an
operator who reads "no reason" where the truth is "could not read" is the exact
hour this endpoint exists to save.

## What a flag does and does not do

`flagged_at` enforces **nothing**. Refusal keys on `suspended_at` only (403 /
MCP `-32006`). A flag is: the stage-1 clock the stage-2 review alert anchors
against, the `status` chip, and one operator alert. It is cleared by a clean
window (`flag_clear`) or an un-suspend — nothing else.

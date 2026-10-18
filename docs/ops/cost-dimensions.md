# Cost dimensions — the declared coverage matrix (#5045)

**What this is.** The one place that answers *"does the product measure cost dimension X?"* —
as a query, not a `git grep`. The declaration lives in code
(`tortoise/cost_dimensions.py`); the audit that verifies it against the tree lives in
`tools/cost_coverage_audit.py`; `tests/test_cost_dimensions.py` fails on any drift.

```bash
uv run python tools/cost_coverage_audit.py matrix            # the matrix + every check
uv run python tools/cost_coverage_audit.py query egress      # is this dimension measured?
uv run python tools/cost_coverage_audit.py matrix --json     # machine-readable
```

Exit codes for `query`: **0** measured / allocation · **3** declared out of scope (an
answer, with its reason) · **2** no such dimension (so a typo can never read as "not
measured"). `matrix` exits **1** and names each finding on any drift.

## Why it exists

Six lanes closed the B7 cost-coverage gap one dimension at a time (#4488 embedding encode,
#4489 retrieval, #4490 hosted-API compute, #4491 egress, #4492 Postgres storage + row
growth, #4493 the fixed/shared allocation). Each landed its own writer, unit, attribution
key — and, by the time they were all in, **three** per-org label-cap conventions. That is
six counters in one process, not a substrate: nothing could answer "what does the meter
measure?" without re-deriving it from source, and a metric could be added with no
declaration noticing.

## What a row declares

`name` · cost `kind` · `unit` (the customer-visible unit: storage in MB/GB, extraction
usage in tokens) · `attribution` key (plus the version identity where a figure is only
comparable within one — the encoder's model id + revision) · whether it is a
**measurement** or an **allocation** · the code bindings that must exist (writer, reader,
metric with its exact label set) · the ledger columns and SQL functions that must exist ·
the label-cardinality policy · and **every known hole**.

A dimension with no primitive is not omitted: it is declared `out_of_scope` *with the
reason*, so the matrix names it instead of showing a blank.

## Measurement vs allocation

An **allocation** is a policy applied to a stated total (`fixed_shared_saas`: even /
proportional to measured write ops, recomputed each refresh). It must never be read as "we
measured this org's share". Postgres **bytes** have no per-org split for the same reason —
a per-org byte figure would be an allocation wearing a measurement's name, so the row
declares the limit instead of inventing one.

## What the audit enforces

1. **bindings** — every declared writer/reader resolves and is callable; every declared
   metric carries *exactly* its declared labels (a label gained or lost is a series change).
2. **ledger** — every declared column is created by a tracked migration and every declared
   SQL function is defined by one.
3. **no dead hook** — a dimension's writer must be referenced from a tracked **non-test**
   file, or the declaration must state why it has no schedule. #4493's defect was a metric
   that registered, exported and tested while never being incremented in production:
   "wired and tested" is not "measured".
4. **no undeclared dimension** — every org-attributed prometheus child in
   `tortoise/monitoring.py` is declared, and no other tracked module builds a prometheus
   metric.
5. **cardinality** — each declared cap/overflow attribute resolves to a live int/str.

## Open decision (recorded, not made here)

Two per-org label policies are live and they differ in **kind**:

| | `persistent` (`egress`, `compute`) | `per_publish` (`fixed_shared_saas`) |
|---|---|---|
| admission | first-come, never evicted | recomputed from the current org set each publish (sorted) |
| a demoted org | keeps its own child | folds into `__other__`; its child is pruned |
| self-healing | no | yes |
| cost of the choice | the admitted set never shrinks (eviction would drop accumulated cost, keeping it grows the child set) | a label can flap between its own child and `__other__` as the fleet changes |

Both are declared and pinned against their measured behaviour so the divergence cannot
drift further unnoticed. **Choosing one policy for the whole substrate is an open decision
on #5045** — the answer applies to every future dimension, which is why it is not settled in
a per-dimension lane.

## Not in this file

No rate, tier boundary, quota, entitlement, overage or retention decision is declared
anywhere in the registry, and `product/pricing.json` is never imported. Pricing lives on
#5331 / #5313; retention stays a product/legal decision (`docs/retention-and-deletion.md`).
The registry states units because a payable dimension must be measurable in the customer's
unit — stating a unit is not setting a price.

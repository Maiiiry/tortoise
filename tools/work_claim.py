#!/usr/bin/env python3
"""work_claim — the CLAIM on a unit of work, stored in the Tortoise graph (#5052, #7871).

WHY THIS EXISTS (measured 2026-10-21, all three cells)
-----------------------------------------------------
Two boxes were handed the SAME fix and produced two PRs (#2247, #2250) and nothing
noticed. The coordination state that should have prevented it does not exist in any
store a peer can read: claims live in `refs/claims/*` on a git origin (delete-on-release,
so no history), lane/capacity state in `~/.pi/agent/state/fleet-state.json` and
`/tmp/cell-home/orchestrator/state/*` (one box's disk), and the worklist in the
orchestrator's own memory. `tools/who_is_on.py` can only enumerate what a *remote* ref
happens to name, which is the class #4256 recorded.

This tool moves exactly ONE slice into the graph: **who holds a unit of work, right now**.

WHAT IT STORES
--------------
One `:Point` per held unit, id = a DETERMINISTIC digest of `(repo, key)`, so the graph's
own uniqueness is the compare-and-swap:

    work-claim:<sha256(repo + key)>  pointKind=work-claim
      props: repo, key, owner, session, branch, head, lease_until, epoch,
             purpose, claimed_at, host, ncpu, lpc, lanes
      content: the same fields in ONE canonical line (the CAS compares CONTENT)

`create_point` with an id that already exists does NOT overwrite — verified against a
live FalkorDB: the second creator is handed the FIRST point back (its content, not the
caller's). That is the same property `claims.sh` gets from `git push` to a new ref, and
it is why the read-back below is the authority rather than the write's exit code.

⚠️ THE ID MUST NOT CONTAIN THE BOX. `claims.sh` learned this the hard way and says so:
"a KEY CONTAINING THE BOX ID IS NOT A CAS. Two boxes mint two DISTINCT refs and BOTH
pushes succeed". Two boxes must compute the SAME id for the same unit of work, or this
is a ledger and not a lock.

THE FIVE PROPERTIES (each one is a copy of something `claims.sh` already proved)
--------------------------------------------------------------------------------
1. **Content-addressed key** — `issue-<N>` / `pr-<N>`, never a box, never a random id.
2. **Create = CAS** — the loser gets the winner's point back and REFUSES (rc 1).
3. **Read back, never trust the write** — the write's return value is re-read by id and
   the content compared. A store that answers with something else is a lost race.
4. **The lease is DATA, not liveness** (#1709 rec 3) — a remote pid cannot be probed, so
   `lease_until` in the record is what frees a claim, and an expired claim is reclaimable.
5. **Fencing** (#1709 rec 6) — a reclaim bumps `epoch`, and the bump is only trusted
   through a store that can express the precondition (`replace_if_epoch`, a real CAS). A
   blind replace cannot: A.replace → A.read → B.replace → B.read lets BOTH boxes observe the
   same expired epoch and both report `acquired`. So a store without the fence (the shipped
   `SDKClaimStore`: `update_point` has no conditional form) REFUSES to reclaim an expired
   claim (rc 2) and the operator frees it explicitly (`release --force --reason`). Refusing a
   capability the store does not have is the same discipline as refusing an unset store.

⛔ FAIL-CLOSED, AND THAT IS A DELIBERATE DEPARTURE — SEE `OVERRIDES:` BELOW
-------------------------------------------------------------------------
`claims.sh`'s merge path is **fail-open on purpose** (recorded 2026-10-14 on
agent-infra#1709): "duplicate execution is the assumption … every side effect on this
path is idempotent per head, and failing closed would stall the whole fleet on one
transient network error". That reasoning holds for a review/merge claim, whose duplicate
cost is one wasted review. It does NOT hold at DISPATCH, which is where #2247/#2250 were
produced: a dispatch that proceeds while the claim store is unreadable is the exact
failure this tool exists to prevent, and its duplicate cost is a whole lane.

    OVERRIDES: the fleet claim registry is fail-OPEN when its store is unreachable
    (agent-infra#1709, 2026-10-14 — chosen because review/merge duplicates are
    idempotent per head) — this dispatch-time claim is fail-CLOSED instead, because
    the measured cost of proceeding blind at dispatch is a duplicated lane and PR
    (#2247/#2250), not a repeated idempotent side effect.

So `rc 2` is a REFUSAL, never a warning: no claim, no dispatch. And rc 2 is returned for
every shape of "I cannot answer", including the one #7871 measured as silently wrong —
an UNSET store URI. The SDK falls back to a default/embedded target when `TORTOISE_DB_URI`
is unset, and an empty read from the wrong target is indistinguishable from "nobody holds
this". An unset store is therefore refused before any query is attempted.

CALLER CONTRACT (drop-in vocabulary with `claims.sh`, so a caller needs no new branch)
------------------------------------------------------------------------------------
    rc 0  acquired (or reclaimed an EXPIRED claim, or a release/status that succeeded)
    rc 1  held by a LIVE claim (or a release refused: not the caller's / epoch moved)
    rc 2  the store could not answer — REFUSE the dispatch, do not proceed blind
    rc 3  the INVOCATION was malformed (no number where one is required, `--force` without
          `--reason`). Deliberately distinct from rc 2: a controller retries an unanswerable
          store, and a retry of a malformed command can never succeed.

`--json` emits one object with `status`, `owner`, `lease_until`, `epoch`, `reason` so a
controller can log the measured reason instead of guessing.

WHY THE CAPACITY FIELDS ARE HERE (and why this is NOT a second probe)
--------------------------------------------------------------------
The in-flight `capacity.sh` (`refs/boxes/<host>/<record>`) already MEASURES each box —
`host`, `ncpu`, `load1`, `lpc`, `lanes`, disk, memory. This tool never measures anything:
it accepts those fields on the command line and persists them WITH the claim, so the
fleet's capacity map and its claim map are the same record and a reader can answer "who
holds this, and on how loaded a box" from one store. Passing them is optional; omitting
them stores the claim without any capacity claim rather than a zero.

USAGE
-----
    tools/work_claim.py acquire --repo daniel-ospina/tortoise --issue 5052 \
        --session cell-t1 --branch feat/x --purpose "claim registry pilot"
    tools/work_claim.py status --repo daniel-ospina/tortoise [--json]     # repo-wide listing
    tools/work_claim.py status --repo daniel-ospina/tortoise --issue 5052 --json
    tools/work_claim.py release --repo daniel-ospina/tortoise --issue 5052 --session cell-t1
    tools/work_claim.py release --repo daniel-ospina/tortoise --issue 5052 \
        --session cell-t9 --force --reason "holder crashed, lease expired 2h ago"
    tools/work_claim.py check            # is the store reachable? prints the target; rc 2 = no

STORE
-----
`TORTOISE_DB_URI` — the shared graph (docker://… or bolt://…) — and NOTHING ELSE. The SDK
reads only that variable; `TORTOISE_API_URL` names a hosted HTTP surface this tool has no
client for, so accepting it as proof of a store disarmed the guard and let the SDK fall back
to this box's EMBEDDED file. Measured on this branch: `check` printed `ok: claim store
reachable` and exited 0 while the claim lived in a box-local file that every peer reads as
free — the duplicate-dispatch failure, reported as a success. There is deliberately no
default: see the fail-closed note.
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/work_claim.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/work_claim.py`"
    )

import argparse
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

# The kind is registered at import so both the writer and every reader agree on it. The
# registry is per-process (`domain_loader.register_kind` mutates an in-memory set), which is
# why registration lives in the module rather than in a caller: a reader that forgot to
# register would get a warn-not-block unknown-kind warning on every query and could sort the
# claim out of a kind-filtered read. Promoting it into `packs/agent-ops/manifest.yaml` is the
# follow-up once the pilot has a second consumer — that is a pack/ontology change and this
# module does not make it.
CLAIM_KIND = "work-claim"

#: Seconds a claim is held before a peer may reclaim it. Mirrors `claims.sh`'s issue-claim
#: default (3 h) and is clamped by the caller, never extended implicitly: a lease that grows
#: on its own is a claim nobody can expire.
DEFAULT_TTL = 3 * 60 * 60

#: A key longer than this is a caller bug (a branch or a session id pasted into the key),
#: and the length is part of the identity — refuse rather than truncate, because truncating
#: would make two different units collide on one claim.
MAX_KEY_LEN = 64

EXIT_OK = 0
EXIT_HELD = 1
EXIT_UNANSWERABLE = 2
# A distinct code for a malformed invocation (review of #9487 P2). `status --repo` with no
# number used to reach `normalise_key(0)` → rc 2, which a controller reads as "the store is
# unanswerable" and retries forever; the invocation was wrong and nothing was unreachable.
EXIT_USAGE = 3


class StoreUnanswerable(RuntimeError):
    """The store could not answer. Always a REFUSAL (rc 2), never a default answer."""


@dataclass
class ClaimRecord:
    """The claim as it is stored — one canonical line of content plus flat props.

    `content` is the CAS payload: the whole record in a fixed field order, so two boxes
    computing the same claim produce byte-identical content and a read-back comparison is
    an equality test rather than a semantic one.
    """

    repo: str
    key: str
    owner: str
    session: str = ""
    branch: str = ""
    head: str = ""
    lease_until: int = 0
    epoch: int = 1
    purpose: str = ""
    claimed_at: int = 0
    host: str = ""
    ncpu: int = 0
    lpc: int = 0
    lanes: int = 0

    def content(self) -> str:
        # Field order is part of the contract: a reordering would make every historical
        # claim unreadable by identity. Values are quoted where they may contain spaces.
        def q(v: Any) -> str:
            # ALWAYS quoted (review of #9487): the conditional form left a value containing a
            # quote but no space unbalanced, and `_parse` then swallowed every field after it
            # (purpose absorbed claimed_at/host/ncpu/lpc/lanes). Round-trip stability is the
            # contract, so the escape is unconditional.
            s = str(v)
            escaped = s.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{escaped}"'

        return " ".join(
            [
                f"work-claim repo={q(self.repo)}",
                f"key={q(self.key)}",
                f"owner={q(self.owner)}",
                f"session={q(self.session)}",
                f"branch={q(self.branch)}",
                f"head={q(self.head)}",
                f"lease_until={int(self.lease_until)}",
                f"epoch={int(self.epoch)}",
                f"purpose={q(self.purpose)}",
                f"claimed_at={int(self.claimed_at)}",
                f"host={q(self.host)}",
                f"ncpu={int(self.ncpu)}",
                f"lpc={int(self.lpc)}",
                f"lanes={int(self.lanes)}",
            ]
        )

    def props(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "claimKey": self.key,
            # ⚠️ NOT `owner`: the SDK reserves `owner` for tenant ownership and SILENTLY
            # ignores a client-supplied value ("ignoring client-supplied 'owner' on tenant
            # props", verified on a live write). A claim whose owner prop is dropped is a
            # claim nobody can query by holder, so the prop is namespaced like `claim_epoch`.
            "claim_owner": self.owner,
            "session": self.session,
            "branch": self.branch,
            "head": self.head,
            "lease_until": int(self.lease_until),
            "claim_epoch": int(self.epoch),
            "purpose": self.purpose,
            "claimed_at": int(self.claimed_at),
            "host": self.host,
            "ncpu": int(self.ncpu),
            "lpc": int(self.lpc),
            "lanes": int(self.lanes),
        }


class ClaimStore(Protocol):
    """The three calls the CAS needs, plus two OPTIONAL capabilities.

    `replace` is a blind write and is therefore used ONLY for a holder's own record (release);
    it is never the reclaim path. Two capabilities are optional because a store may not be able
    to express them, and their ABSENCE is an answer rather than a default:

    - `replace_if_epoch(cid, record, expected_epoch) -> str | None` — the fenced reclaim. `None`
      means the precondition failed (a peer moved the epoch) and the caller lost the race. A
      store without it cannot reclaim: two boxes would both report `acquired`.
    - `query_repo(repo) -> list[dict]` — the repo-wide listing. A store without it must make
      `status --repo` REFUSE (rc 2), never answer `0 claim records`.
    """

    def read(self, claim_id: str) -> str | None: ...

    def create(self, claim_id: str, record: ClaimRecord) -> str: ...

    def replace(self, claim_id: str, record: ClaimRecord) -> str: ...


def claim_id(repo: str, key: str) -> str:
    """The deterministic point id. Canonical `<hex>-<12 hex>` shape so the SDK does not warn
    on a non-ULID id, and derived ONLY from (repo, key) so two boxes compute the same one —
    the property that makes this a CAS instead of a ledger."""
    digest = hashlib.sha256(f"{repo}\x00{key}".encode()).hexdigest()
    return f"{digest[:20]}-{digest[20:32]}"


def normalise_key(kind: str, number: int) -> str:
    """`issue-<N>` / `pr-<N>`. Disjoint by prefix, like `claims.sh`'s two keyings, so an
    issue claim never blocks a PR claim for the same number."""
    if kind not in ("issue", "pr"):
        raise ValueError(f"unknown claim kind {kind!r} (expected 'issue' or 'pr')")
    if number <= 0:
        raise ValueError(f"claim number must be positive, got {number!r}")
    key = f"{kind}-{number}"
    if len(key) > MAX_KEY_LEN:
        raise ValueError(f"claim key {key!r} is longer than {MAX_KEY_LEN} characters")
    return key


def _require_store_configured(environ: dict[str, str] | None = None) -> str:
    """Refuse an UNSET store before touching it.

    This is the one branch that must not be reachable by accident: with no URI the SDK
    answers from a default target, and "0 rows" from the wrong graph is indistinguishable
    from "nobody holds this" (#7871 gap 1 measured this as the real read defect — "an
    unset URI returning 0 rows silently"). A caller that genuinely wants the embedded
    target must name it.
    """
    env = os.environ if environ is None else environ
    uri = (env.get("TORTOISE_DB_URI") or "").strip()
    api = (env.get("TORTOISE_API_URL") or "").strip()
    if not uri:
        detail = ""
        if api:
            # Measured (review of #9487): accepting the API URL as proof of a store armed the
            # guard and then let `TortoiseSDK()` ignore it — the SDK reads ONLY
            # TORTOISE_DB_URI and falls back to this box's embedded file. The claim landed
            # locally, the read-back confirmed the local write, and the tool reported
            # `acquired` while every peer read `free` — the duplicate-dispatch failure this
            # tool exists to prevent, reported as a win. Naming the API URL again must not
            # re-open that, so the message says why instead of just what.
            detail = (
                " TORTOISE_API_URL is set, but this tool has no hosted client: the SDK consumes "
                "ONLY TORTOISE_DB_URI and would silently fall back to the BOX-LOCAL embedded "
                "database, where a claim reads as acquired here and FREE on every peer. Point "
                "TORTOISE_DB_URI at the shared graph instead."
            )
        raise StoreUnanswerable(
            "no claim store configured: set TORTOISE_DB_URI (the shared graph). An unset "
            "store is refused rather than answered from a default target — an empty read "
            "from the wrong graph looks exactly like 'nobody holds this' (#7871)." + detail
        )
    return uri


class SDKClaimStore:
    """The shipped store: Tortoise points through the SDK.

    `create` never overwrites (the graph's id uniqueness is the CAS); `replace` is the
    fenced reclaim path and is only reachable after an observed expired lease + epoch.
    """

    def __init__(self, sdk: Any = None) -> None:
        if sdk is None:
            from tortoise.domain_loader import register_kind
            from tortoise.sdk import TortoiseSDK

            register_kind(CLAIM_KIND)
            sdk = TortoiseSDK()
        else:  # injected client (tests) still needs the kind registered
            from tortoise.domain_loader import register_kind

            register_kind(CLAIM_KIND)
        # Belt and braces over `_require_store_configured` (review of #9487 P0), and it applies
        # to an INJECTED client too: if a caller reaches this class directly with a target that
        # resolved to the box-local file, the write would be confirmed by its own read-back and
        # reported as an acquisition while every peer reads FREE — the duplicate-dispatch
        # failure in its purest form. An embedded target is not a shared claim store.
        if getattr(sdk, "_db_uri", None) is None:
            raise StoreUnanswerable(
                "the SDK resolved to this box's EMBEDDED database, not a shared graph — "
                "a claim written there reads as acquired here and free on every peer. "
                "Set TORTOISE_DB_URI to the shared store (docker://… or bolt://…)."
            )
        self._sdk = sdk

    def read(self, cid: str) -> str | None:
        try:
            point = self._sdk.get_point(cid)
        except Exception as exc:
            # A missing point is not an error the SDK reports uniformly across deployments
            # (a hosted 404 and a self-hosted empty match differ), so an exception is
            # classified by asking the SDK for a definite answer a second way: by id list.
            return self._read_via_query(cid, exc)
        if not point:
            return None
        return point.get("content") or ""

    def _read_via_query(self, cid: str, exc: Exception) -> str | None:
        try:
            rows = self._sdk.query(kind=CLAIM_KIND)
        except Exception:
            raise StoreUnanswerable(
                f"the graph could not be read ({type(exc).__name__}: {str(exc)[:200]})"
            ) from exc
        for row in rows or []:
            if row.get("id") == cid:
                return row.get("content") or ""
        return None

    def create(self, cid: str, record: ClaimRecord) -> str:
        try:
            point = self._sdk.create_point(CLAIM_KIND, record.content(), id=cid, **record.props())
        except Exception as exc:
            raise StoreUnanswerable(
                f"the claim could not be written ({type(exc).__name__}: {str(exc)[:200]})"
            ) from exc
        return (point or {}).get("content") or ""

    def replace(self, cid: str, record: ClaimRecord) -> str:
        try:
            point = self._sdk.update_point(cid, **record.props(), content=record.content())
        except Exception as exc:
            raise StoreUnanswerable(
                f"the claim could not be reclaimed ({type(exc).__name__}: {str(exc)[:200]})"
            ) from exc
        return (point or {}).get("content") or ""

    # ⚠️ There is deliberately NO `replace_if_epoch` here (review of #9487 P1). The SDK's
    # `update_point` is a blind write: it cannot express "set this ONLY if the stored epoch is
    # still E", and the interleaving (A.replace → A.read → B.replace → B.read) lets TWO boxes
    # both observe the old epoch, both write, and both report `acquired` — one holder in the
    # graph, two lanes believing they own the unit. `acquire` therefore REFUSES to reclaim an
    # expired claim through this store rather than pretending the bump fences (rc 2). An
    # expired claim is freed explicitly (`release --force`) and then acquired fresh.

    def target(self) -> str:
        """Which store answered — printed by `check` so a human sees it rather than trusting"""
        uri = getattr(self._sdk, "_db_uri", None)
        return str(uri) if uri else "embedded (box-local — NOT a shared claim store)"

    def query_repo(self, repo: str) -> list[dict]:
        """Every claim record for one repo (review of #9487 P1).

        `status --repo` used to answer `0 claim record(s)` for every store, because the caller
        guarded a missing method with `hasattr` and turned "this store cannot list" into "there
        is nothing to list". A fleet reader would conclude nobody holds anything. A store that
        cannot list must refuse (rc 2), never fabricate an empty success.
        """
        try:
            rows = self._sdk.query(kind=CLAIM_KIND, repo=repo)
        except Exception as exc:
            raise StoreUnanswerable(
                f"the claim store could not be listed ({type(exc).__name__}: {str(exc)[:200]})"
            ) from exc
        out: list[dict] = []
        for row in rows or []:
            props = row.get("properties") if isinstance(row.get("properties"), dict) else row
            if not isinstance(props, dict):
                continue
            got_repo = props.get("repo", row.get("repo", ""))
            if got_repo != repo:
                continue
            out.append(
                {
                    "key": props.get("claimKey") or props.get("key") or "",
                    "owner": props.get("claim_owner") or props.get("owner") or "",
                    "lease_until": int(props.get("lease_until") or 0),
                    "epoch": int(props.get("claim_epoch") or props.get("epoch") or 0),
                    "session": props.get("session") or "",
                }
            )
        return out


@dataclass
class Outcome:
    """What the caller is told. `status` is the vocabulary `claims.sh` already uses."""

    rc: int
    status: str
    reason: str
    owner: str = ""
    lease_until: int = 0
    epoch: int = 0
    claim_id: str = ""
    record: ClaimRecord | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "rc": self.rc,
            "reason": self.reason,
            "owner": self.owner,
            "lease_until": self.lease_until,
            "epoch": self.epoch,
            "claim_id": self.claim_id,
            "record": self.record.content() if self.record else "",
            **({"extra": self.extra} if self.extra else {}),
        }


def _now() -> int:
    return int(time.time())


def acquire(
    store: ClaimStore,
    record: ClaimRecord,
    *,
    now: int | None = None,
    hostname: str | None = None,
) -> Outcome:
    """Take the claim, or refuse. Never blocks, never retries blind.

    Three shapes only: created (0), held by a live claim (1), unanswerable (2). The read-back
    is the authority in all three — `create`'s return value is compared to what we meant to
    write, so a store that silently hands back someone else's point is a lost race rather
    than an acquisition.
    """
    now = _now() if now is None else now
    cid = claim_id(record.repo, record.key)
    record.claimed_at = record.claimed_at or now
    record.lease_until = record.lease_until or (now + DEFAULT_TTL)
    record.host = record.host or (hostname or os.environ.get("HOSTNAME") or "")
    mine = record.content()

    # Read BEFORE the create (review of #9487): `create_point` hands back the EXISTING content
    # for an id that already exists, so a retry whose payload happened to be byte-identical to
    # the stored claim compared equal and was reported `acquired` by a box that wrote nothing.
    # The read also makes re-entrancy decidable without inferring identity from content.
    try:
        existing = store.read(cid) or ""
    except StoreUnanswerable as exc:
        return Outcome(
            rc=EXIT_UNANSWERABLE,
            status="REFUSED",
            reason=f"the claim store could not be read — dispatch refused: {exc}",
            claim_id=cid,
            record=record,
        )

    if existing:
        got = None  # nothing was created; the tail decides held / reclaim
    else:
        try:
            got = store.create(cid, record)
        except StoreUnanswerable as exc:
            return Outcome(
                rc=EXIT_UNANSWERABLE,
                status="REFUSED",
                reason=f"claim store unanswerable — dispatch refused: {exc}",
                claim_id=cid,
                record=record,
            )

        if got == mine:
            # Confirm by an independent read: a create that echoed our own payload without
            # persisting it would otherwise read as a win (the class of defect where the
            # instrument reports success and the state never landed).
            try:
                back = store.read(cid)
            except StoreUnanswerable as exc:
                return Outcome(
                    rc=EXIT_UNANSWERABLE,
                    status="REFUSED",
                    reason=f"claim written but the read-back failed — dispatch refused: {exc}",
                    claim_id=cid,
                    record=record,
                )
            if back == mine:
                return Outcome(
                    rc=EXIT_OK,
                    status="acquired",
                    reason=f"acquired {record.key} until {record.lease_until}",
                    owner=record.owner,
                    lease_until=record.lease_until,
                    epoch=record.epoch,
                    claim_id=cid,
                    record=record,
                )
            return Outcome(
                rc=EXIT_HELD,
                status="held",
                reason=(
                    "lost the race: the store returned our payload but the read-back differs "
                    "(another box wrote first)"
                ),
                owner=_field(back or "", "owner"),
                lease_until=_int_field(back or "", "lease_until"),
                epoch=_int_field(back or "", "epoch"),
                claim_id=cid,
            )

        # Somebody else's record came back, or nothing at all.
        existing = got or ""

    holder = _field(existing, "owner")
    until = _int_field(existing, "lease_until")
    epoch = _int_field(existing, "epoch")

    if not existing:
        return Outcome(
            rc=EXIT_UNANSWERABLE,
            status="REFUSED",
            reason=(
                "the store neither created nor returned a claim — dispatch refused rather "
                "than proceeding without a decision"
            ),
            claim_id=cid,
        )

    if until > now:
        # Re-entrancy (review of #9487 P2): a retry by the SAME caller must not be told the
        # unit is held by a peer. `claims.sh` never expressed "already mine", so a resumed
        # lane used to refuse work it owned — and for a CLI caller the reported holder was a
        # different `<host>:<pid>`, so even a human could not recognise their own claim.
        if holder and holder == record.owner:
            return Outcome(
                rc=EXIT_OK,
                status="already-held",
                reason=(
                    f"{record.key} is already held by this caller ({holder}) until {until} "
                    f"({until - now}s left, epoch {epoch}) — no new claim taken"
                ),
                owner=holder,
                lease_until=until,
                epoch=epoch,
                claim_id=cid,
            )
        return Outcome(
            rc=EXIT_HELD,
            status="held",
            reason=(
                f"{record.key} is held by {holder or 'an unnamed owner'} until {until} "
                f"({until - now}s left, epoch {epoch})"
            ),
            owner=holder,
            lease_until=until,
            epoch=epoch,
            claim_id=cid,
        )

    # Expired: reclaim with a fencing bump — but ONLY through a store that can express the
    # precondition (review of #9487 P1). A blind `replace` here let two boxes observe the same
    # expired epoch, both write, and both return `acquired`: one holder in the graph, two lanes
    # believing they own the unit. Absence of the capability is a REFUSAL, not a fallback.
    reclaim = ClaimRecord(**{**record.__dict__})
    reclaim.epoch = epoch + 1
    reclaim.claimed_at = now
    reclaim.lease_until = now + DEFAULT_TTL
    mine = reclaim.content()
    fenced = getattr(store, "replace_if_epoch", None)
    if fenced is None:
        return Outcome(
            rc=EXIT_UNANSWERABLE,
            status="REFUSED",
            reason=(
                f"{record.key} expired at {until} (holder {holder or 'unknown'}), but this "
                "store cannot express the reclaim precondition (an epoch-CAS), so a reclaim "
                "could double-grant. Dispatch refused; the claim must be released explicitly "
                "(`release --force`) or reclaimed by a store that supports the fence."
            ),
            owner=holder,
            lease_until=until,
            epoch=epoch,
            claim_id=cid,
        )
    try:
        got = fenced(cid, reclaim, expected_epoch=epoch)
        if got is None:
            # A peer moved the epoch between our read and our write: we lost, and we say so
            # rather than retrying into a claim we do not hold.
            winner = store.read(cid) or ""
            return Outcome(
                rc=EXIT_HELD,
                status="held",
                reason=(
                    "lost the reclaim race: the record moved to "
                    f"{_field(winner, 'owner') or 'another box'} at epoch "
                    f"{_int_field(winner, 'epoch')}"
                ),
                owner=_field(winner, "owner"),
                lease_until=_int_field(winner, "lease_until"),
                epoch=_int_field(winner, "epoch"),
                claim_id=cid,
            )
        back = store.read(cid)
    except StoreUnanswerable as exc:
        return Outcome(
            rc=EXIT_UNANSWERABLE,
            status="REFUSED",
            reason=f"reclaim of an expired claim could not be confirmed — dispatch refused: {exc}",
            claim_id=cid,
            record=reclaim,
        )
    if got == mine and back == mine:
        return Outcome(
            rc=EXIT_OK,
            status="acquired",
            reason=(
                f"reclaimed {record.key} (previous holder {holder or 'unknown'} expired at "
                f"{until}); epoch {epoch} -> {reclaim.epoch}"
            ),
            owner=reclaim.owner,
            lease_until=reclaim.lease_until,
            epoch=reclaim.epoch,
            claim_id=cid,
            record=reclaim,
        )
    winner = back or got or ""
    return Outcome(
        rc=EXIT_HELD,
        status="held",
        reason=(
            "lost the reclaim race: the record moved to "
            f"{_field(winner, 'owner') or 'another box'} at epoch {_int_field(winner, 'epoch')}"
        ),
        owner=_field(winner, "owner"),
        lease_until=_int_field(winner, "lease_until"),
        epoch=_int_field(winner, "epoch"),
        claim_id=cid,
    )


def release(
    store: ClaimStore,
    repo: str,
    key: str,
    *,
    owner: str,
    now: int | None = None,
    session: str = "",
    force: bool = False,
    reason: str = "",
) -> Outcome:
    """Release a claim this owner holds. A release by a non-holder is REFUSED (rc 1), never
    a silent no-op: releasing someone else's claim is how a peer's lane gets duplicated.

    `force=True` is the audited operator route for a crashed holder: the lease is expired and
    `reason` is recorded IN the claim, so the override is legible to the next reader. It never
    bypasses the identity check silently — the tombstone carries the reason and the releasing
    identity, and the refusal message (absent force) names the real holder.
    """
    now = _now() if now is None else now
    cid = claim_id(repo, key)
    try:
        existing = store.read(cid) or ""
    except StoreUnanswerable as exc:
        return Outcome(
            rc=EXIT_UNANSWERABLE,
            status="REFUSED",
            reason=f"claim store unanswerable — release not attempted: {exc}",
            claim_id=cid,
        )
    if not existing:
        return Outcome(
            rc=EXIT_OK,
            status="released",
            reason=f"{key} was not claimed — nothing to release",
            claim_id=cid,
        )
    holder = _field(existing, "owner")
    if holder != owner and not force:
        return Outcome(
            rc=EXIT_HELD,
            status="refused",
            reason=f"{key} is held by {holder or 'an unnamed owner'}, not by {owner} — refused",
            owner=holder,
            lease_until=_int_field(existing, "lease_until"),
            epoch=_int_field(existing, "epoch"),
            claim_id=cid,
        )
    # Release = expire it in place, not delete it. A deleted claim has no history and no
    # fencing epoch, which is exactly the defect that makes `refs/claims/*` unable to answer
    # "who held this yesterday" (the refs are deleted on release).
    tomb = _parse(existing)
    tomb.lease_until = now
    if force:
        actor = f" (forced by {owner}" + (f" from session {session}" if session else "") + ")"
        tomb.purpose = (tomb.purpose + " [released:" + reason.replace("]", ")") + "]" + actor).strip()
    else:
        tomb.purpose = (tomb.purpose + " [released]").strip()
    # Narrow the overwrite window (review of #9487 P1): re-read IMMEDIATELY before the blind
    # write and refuse if the record already moved. Without a store-side CAS this cannot be
    # atomic — the residual window is documented in the module docstring — but a release must
    # not overwrite a peer's freshly reclaimed claim when the move is observable at all.
    try:
        pre = store.read(cid) or ""
    except StoreUnanswerable as exc:
        return Outcome(
            rc=EXIT_UNANSWERABLE,
            status="REFUSED",
            reason=f"release could not be confirmed — dispatch state unknown: {exc}",
            claim_id=cid,
        )
    if pre != existing:
        return Outcome(
            rc=EXIT_HELD,
            status="refused",
            reason="release lost a race: the claim moved while being released",
            owner=_field(pre, "owner"),
            epoch=_int_field(pre, "epoch"),
            claim_id=cid,
        )
    try:
        store.replace(cid, tomb)
        back = store.read(cid)
    except StoreUnanswerable as exc:
        return Outcome(
            rc=EXIT_UNANSWERABLE,
            status="REFUSED",
            reason=f"release could not be confirmed — dispatch state unknown: {exc}",
            claim_id=cid,
        )
    if back != tomb.content():
        return Outcome(
            rc=EXIT_HELD,
            status="refused",
            reason="release lost a race: the claim moved while being released",
            owner=_field(back or "", "owner"),
            claim_id=cid,
        )
    return Outcome(
        rc=EXIT_OK,
        status="released",
        reason=f"released {key} (held by {owner})",
        owner=owner,
        claim_id=cid,
        record=tomb,
    )


def status(store: ClaimStore, repo: str, key: str | None, *, now: int | None = None) -> Outcome:
    """Who holds this right now. A reachable store with no claim is a real answer (rc 0 with
    `status=free`), and is never confused with an unanswerable one (rc 2)."""
    now = _now() if now is None else now
    if key:
        cid = claim_id(repo, key)
        try:
            existing = store.read(cid) or ""
        except StoreUnanswerable as exc:
            return Outcome(
                rc=EXIT_UNANSWERABLE,
                status="REFUSED",
                reason=f"claim store unanswerable — state unknown: {exc}",
                claim_id=cid,
            )
        if not existing:
            return Outcome(rc=EXIT_OK, status="free", reason=f"{key} is unclaimed", claim_id=cid)
        until = _int_field(existing, "lease_until")
        live = until > now
        return Outcome(
            rc=EXIT_OK,
            status="held" if live else "expired",
            reason=(
                f"{key} is held by {_field(existing, 'owner')} until {until} "
                f"({until - now}s {'left' if live else 'ago'})"
            ),
            owner=_field(existing, "owner"),
            lease_until=until,
            epoch=_int_field(existing, "epoch"),
            claim_id=cid,
            record=_parse(existing),
        )
    # Repo-wide listing (review of #9487 P1): a store that cannot list must REFUSE. The old
    # `hasattr(store, "query_repo")` fallback turned "this store cannot list" into "there is
    # nothing to list" and answered rc 0 `0 claim record(s)` while the store held claims —
    # the exact default-answer-without-a-read that this tool exists to remove.
    lister = getattr(store, "query_repo", None)
    if lister is None:
        return Outcome(
            rc=EXIT_UNANSWERABLE,
            status="REFUSED",
            reason=(
                "this store cannot list claims for a repo — refusing rather than reporting an "
                "empty ledger (an empty answer from an unread store looks exactly like 'nobody "
                "holds anything')"
            ),
        )
    try:
        rows = lister(repo)
    except StoreUnanswerable as exc:
        return Outcome(rc=EXIT_UNANSWERABLE, status="REFUSED", reason=f"claim store unanswerable: {exc}")
    return Outcome(
        rc=EXIT_OK,
        status="listed",
        reason=f"{len(rows)} claim record(s) for {repo}",
        extra={"claims": rows},
    )


def _field(content: str, name: str) -> str:
    """Read a field out of the canonical content line, tolerating a quoted value."""
    marker = f"{name}="
    for token in _tokens(content):
        if token.startswith(marker):
            return token[len(marker) :]
    return ""


def _int_field(content: str, name: str) -> int:
    raw = _field(content, name)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _tokens(content: str) -> list[str]:
    """Split the canonical line into `k=v` tokens, keeping quoted values whole."""
    out: list[str] = []
    buf = ""
    in_quotes = False
    escape = False
    for ch in content:
        if escape:
            buf += ch
            escape = False
        elif ch == "\\" and in_quotes:
            escape = True
        elif ch == '"':
            in_quotes = not in_quotes
        elif ch == " " and not in_quotes:
            if buf:
                out.append(buf)
            buf = ""
        else:
            buf += ch
    if buf:
        out.append(buf)
    return out


def _parse(content: str) -> ClaimRecord:
    """Rebuild a record from content. Unknown/absent fields become empty, never a guess."""
    return ClaimRecord(
        repo=_field(content, "repo"),
        key=_field(content, "key"),
        owner=_field(content, "owner"),
        session=_field(content, "session"),
        branch=_field(content, "branch"),
        head=_field(content, "head"),
        lease_until=_int_field(content, "lease_until"),
        epoch=_int_field(content, "epoch") or 1,
        purpose=_field(content, "purpose"),
        claimed_at=_int_field(content, "claimed_at"),
        host=_field(content, "host"),
        ncpu=_int_field(content, "ncpu"),
        lpc=_int_field(content, "lpc"),
        lanes=_int_field(content, "lanes"),
    )


def _owner_token(session: str = "", pid: int | None = None) -> str:
    """The caller's identity, STABLE across processes.

    `claims.sh` writes `box:<pid>`; this tool keeps that shape only when no session is named.
    A pid is not an identity for a claim (review of #9487 P1): `acquire` and `release` are
    different processes, so a pid-derived owner made every claim unreleasable — the release
    was always refused as "held by <a different pid>". A session id is what the caller already
    has and what survives a retry, so it is preferred whenever it is given.

    The lease (not `kill -0`) is still what expires a claim: a session id is a name, not a
    liveness proof.
    """
    host = os.environ.get("HOSTNAME") or "box"
    if session:
        return f"{host}:{session}"
    pid = os.getpid() if pid is None else pid
    return f"{host}:{pid}"


def main(argv: list[str] | None = None, store: ClaimStore | None = None) -> int:
    # `--json` must work on BOTH sides of the subcommand: a controller writes
    # `work_claim acquire … --json` (the natural order) and a human often writes
    # `work_claim --json status …`. The subcommand's copy uses SUPPRESS so it cannot
    # overwrite a value already given before the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="machine-readable outcome on stdout")

    parser = argparse.ArgumentParser(
        prog="work_claim",
        description="The claim on a unit of work, in the Tortoise graph. rc 2 = REFUSED.",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable outcome on stdout")
    sub = parser.add_subparsers(dest="cmd", required=True)

    acq = sub.add_parser("acquire", parents=[common], help="claim a unit of work (CAS; refuses if held)")
    acq.add_argument("--repo", required=True, help="owner/name whose issue/PR number is claimed")
    acq.add_argument("--kind", default="issue", choices=("issue", "pr"))
    acq.add_argument("--issue", type=int, default=0, help="number (issue or PR per --kind)")
    acq.add_argument("--pr", type=int, default=0, help="alias for --kind pr --issue N")
    acq.add_argument("--session", default="", help="the lane/session id taking the claim")
    acq.add_argument("--branch", default="")
    acq.add_argument("--head", default="", help="head sha, for a content-addressed key")
    acq.add_argument("--purpose", default="")
    acq.add_argument("--ttl", type=int, default=DEFAULT_TTL)
    # Capacity fields: MEASURED BY capacity.sh, never by this tool (see the module docstring).
    acq.add_argument("--host", default="")
    acq.add_argument("--ncpu", type=int, default=0)
    acq.add_argument("--lpc", type=int, default=0, help="load-per-cpu x100, as capacity.sh emits it")
    acq.add_argument("--lanes", type=int, default=0)

    rel = sub.add_parser("release", parents=[common], help="release a claim this owner holds")
    rel.add_argument("--repo", required=True)
    rel.add_argument("--kind", default="issue", choices=("issue", "pr"))
    rel.add_argument("--issue", type=int, default=0)
    rel.add_argument("--pr", type=int, default=0)
    rel.add_argument("--owner", default="", help="defaults to <host>:<session> when --session is given, else <host>:<pid>")
    rel.add_argument("--session", default="", help="the lane/session id that took the claim")
    rel.add_argument(
        "--force",
        action="store_true",
        help="expire a claim this caller does NOT hold (requires --reason); the audited route "
        "for a crashed holder when the store cannot reclaim an expired claim itself",
    )
    rel.add_argument("--reason", default="", help="why --force is justified (recorded with the claim)")

    st = sub.add_parser("status", parents=[common], help="who holds it right now")
    st.add_argument("--repo", required=True)
    st.add_argument("--kind", default="issue", choices=("issue", "pr"))
    st.add_argument("--issue", type=int, default=0)
    st.add_argument("--pr", type=int, default=0)

    sub.add_parser("check", parents=[common], help="is the store reachable? rc 2 = no")

    args = parser.parse_args(argv)
    args.json = getattr(args, "json", False)

    if args.cmd == "check":
        try:
            _require_store_configured()
            if store is None:
                store = SDKClaimStore()
            # A definite read, not a write: `check` must be safe to run on any box at any
            # time, and an empty answer still proves the store answered.
            store.read(claim_id("probe", "probe"))
        except StoreUnanswerable as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return EXIT_UNANSWERABLE
        except Exception as exc:
            print(f"REFUSED: claim store unanswerable ({type(exc).__name__}: {exc})", file=sys.stderr)
            return EXIT_UNANSWERABLE
        print(f"ok: claim store reachable ({store.target()})")
        return EXIT_OK

    # `status --repo` with NO number is the repo-wide listing, not a malformed single-key
    # query (review of #9487 P2). Any other command still needs a number, and a bad
    # invocation is EXIT_USAGE — never EXIT_UNANSWERABLE, which means "the store could not
    # answer" and is retried by a controller.
    try:
        number = int(getattr(args, "pr", 0) or 0) or int(getattr(args, "issue", 0) or 0)
        kind = "pr" if int(getattr(args, "pr", 0) or 0) else args.kind
        key = None if args.cmd == "status" and number == 0 else normalise_key(kind, number)
    except (ValueError, TypeError) as exc:
        print(f"usage: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if store is None:
        try:
            _require_store_configured()
            store = SDKClaimStore()
        except StoreUnanswerable as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return EXIT_UNANSWERABLE
        except Exception as exc:
            print(f"REFUSED: claim store could not be opened ({type(exc).__name__}: {exc})", file=sys.stderr)
            return EXIT_UNANSWERABLE

    if args.cmd == "acquire":
        owner = _owner_token(args.session)
        record = ClaimRecord(
            repo=args.repo,
            key=key,
            owner=owner,
            session=args.session,
            branch=args.branch,
            head=args.head,
            lease_until=_now() + args.ttl,
            purpose=args.purpose,
            host=args.host,
            ncpu=args.ncpu,
            lpc=args.lpc,
            lanes=args.lanes,
        )
        outcome = acquire(store, record)
    elif args.cmd == "release":
        # Identity must be reproducible across processes (review of #9487 P1): `release` runs
        # in a DIFFERENT process from `acquire`, so a pid-derived default made every claim
        # unreleasable. `--session` names the same lane the acquire named.
        if getattr(args, "force", False) and not args.reason.strip():
            print("usage: --force requires --reason (an audited override, not a quiet one)", file=sys.stderr)
            return EXIT_USAGE
        owner = args.owner or _owner_token(args.session)
        outcome = release(
            store,
            args.repo,
            key,
            owner=owner,
            session=args.session,
            force=getattr(args, "force", False),
            reason=args.reason.strip(),
        )
    else:
        outcome = status(store, args.repo, key) if key is not None else status(store, args.repo, None)

    if args.json:
        print(json.dumps(outcome.as_dict(), sort_keys=True))
    else:
        print(f"{outcome.status}: {outcome.reason}")
    if outcome.rc == EXIT_UNANSWERABLE:
        # The reason is already on stdout/stderr; rc is the contract the caller branches on.
        print(
            "REFUSED: no claim decision was reached, so the dispatch must not proceed blind",
            file=sys.stderr,
        )
    return outcome.rc


if __name__ == "__main__":
    sys.exit(main())

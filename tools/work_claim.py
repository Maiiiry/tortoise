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
5. **Fencing** (#1709 rec 6) — a reclaim bumps `epoch`. A holder that lost ownership
   cannot overwrite the new holder's record: the reclaim re-reads and compares the epoch
   it observed, and a mismatch is reported as a lost race, not retried.

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
    tools/work_claim.py status --repo daniel-ospina/tortoise [--json]
    tools/work_claim.py status --repo daniel-ospina/tortoise --issue 5052 --json
    tools/work_claim.py release --repo daniel-ospina/tortoise --issue 5052 --session cell-t1
    tools/work_claim.py check            # is the store reachable? rc 2 = no

STORE
-----
`TORTOISE_DB_URI` (self-hosted docker/bolt) or a hosted deployment's URI + key, exactly
as the SDK reads them. There is deliberately no default: see the fail-closed note.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
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
            s = str(v)
            escaped = s.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{escaped}"' if (" " in s or not s) else s

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
    """The three calls the CAS needs. The SDK-backed store is the only shipped one; tests
    inject an in-memory double so the CAS and the refusal paths are exercised without a
    graph (the live path is covered by the URI-guarded test)."""

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
    uri = (env.get("TORTOISE_DB_URI") or env.get("TORTOISE_API_URL") or "").strip()
    if not uri:
        raise StoreUnanswerable(
            "no claim store configured: set TORTOISE_DB_URI (self-hosted) or "
            "TORTOISE_API_URL + TORTOISE_API_KEY (hosted). An unset store is refused "
            "rather than answered from a default target — an empty read from the wrong "
            "graph looks exactly like 'nobody holds this' (#7871)."
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

    # Expired: reclaim with a fencing bump. The reclaim re-reads and compares the epoch it
    # OBSERVED — a peer that reclaimed in the same instant moved it, and the loser is told
    # it lost rather than retrying into a claim it does not hold.
    reclaim = ClaimRecord(**{**record.__dict__})
    reclaim.epoch = epoch + 1
    reclaim.claimed_at = now
    reclaim.lease_until = now + DEFAULT_TTL
    mine = reclaim.content()
    try:
        got = store.replace(cid, reclaim)
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
) -> Outcome:
    """Release a claim this owner holds. A release by a non-holder is REFUSED (rc 1), never
    a silent no-op: releasing someone else's claim is how a peer's lane gets duplicated."""
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
    if holder != owner:
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
    tomb.purpose = (tomb.purpose + " [released]").strip()
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
    try:
        rows = store.query_repo(repo) if hasattr(store, "query_repo") else []
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


def _owner_token(session: str, pid: int | None = None) -> str:
    """`<host>:<pid>` — the SAME encoding `claims.sh` writes (`box:<pid>`), so a fleet reader
    that already parses claim owners needs no second parser. The pid is local to the box by
    construction, which is why the lease (not `kill -0`) is what expires a claim."""
    host = os.environ.get("HOSTNAME") or "box"
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
    rel.add_argument("--owner", default="", help="defaults to this process's <host>:<pid>")

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
        print("ok: claim store reachable")
        return EXIT_OK

    try:
        number = int(getattr(args, "pr", 0) or 0) or int(getattr(args, "issue", 0) or 0)
        kind = "pr" if int(getattr(args, "pr", 0) or 0) else args.kind
        key = normalise_key(kind, number)
    except (ValueError, TypeError) as exc:
        print(f"usage: {exc}", file=sys.stderr)
        return EXIT_UNANSWERABLE

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
        owner = args.owner or _owner_token("")
        outcome = release(store, args.repo, key, owner=owner)
    else:
        outcome = status(store, args.repo, key)

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

#!/usr/bin/env python3
"""Detect and repair the un-writable `tortoise` graph condition.

THE FAILURE THIS GUARDS AGAINST (measured, reproducible)
--------------------------------------------------------
FalkorDB 6.0.1, `falkordb-16379`, the shared `tortoise` graph:

* READS succeed -- every query, all nodes, consistently.
* EVERY WRITE is refused with:
      ResponseError: graph was deleted or replaced while the query was running, aborting
  from the SDK, from raw Cypher, and from `redis-cli` inside the container -- so it is
  not a client artefact.
* It survives a container restart, so the fault is in what the RDB reload produces. It is
  NOT a transient during load: measured still-refused 155 s after `loading:0` with the
  container stable.
* It is SPECIFIC TO THIS GRAPH. Measured 2026-10-13 across one restart: `tortoise`,
  `org_tortoise`, `registry_tortoise`, `test_test_9ac6a1580cd5` and `org_team_s1_aed9f79d`
  were ALL writable before it, and after it the other four were writable and ONLY
  `tortoise` was broken.
* Not eviction (`maxmemory=0`, `noeviction`, 386 MB used of no limit) and not a concurrent
  deleter (a `MONITOR` capture taken during a refused write showed NO `GRAPH.DELETE` of
  `tortoise`; the only deletes were unrelated test-graph sweeps).

Every container restart therefore takes the whole fleet's graph writes down, and the lanes
that would notice are the lanes that are blocked.

THE REPAIR: COPY -> VERIFY -> RENAME. NEVER DELETE.
---------------------------------------------------
A freshly `GRAPH.COPY`-ed graph IS complete and writable (verified: 20300 nodes, matching
point-kind counts, spot-checked ids present, first write accepted).

The swap is a single atomic `RENAME`, which OVERWRITES in one step. That is the fix for
the failure that destroyed this graph once already: `GRAPH.DELETE` + copy-back leaves a
window in which a Cypher read on the missing graph AUTO-CREATES an empty one (so the
copy-back then fails with `destination key already exists`), and the graph is left EMPTY.
`RENAME` has no such window, so this module contains no `GRAPH.DELETE` of the live graph at
all. The repo's own primitives are used for the pieces that do delete
(`graph_delete_guard.graph_delete_lock`, `safe_graph_delete`), and presence is always
established with `graph_exists` -- never by querying a graph whose existence is assumed.

REPAIR ONLY ON THE SPECIFIC DEFECT
----------------------------------
The probe classifies the failure, and only the exact defect authorises a repair. This is
not defensiveness for its own sake: an earlier version caught bare `Exception`, so a
`BusyLoadingError` (the server still loading its RDB) was classified as "the graph is
broken" and it ran the destructive swap against a graph that was merely not ready. That is
the incident this file exists to make unrepeatable. `BusyLoadingError` and connection
failures are UNKNOWN -- exit 2 -- never "broken".

KNOWN RESIDUALS (declared, not hidden)
--------------------------------------
* **The probe mutates.** Verifying that a graph accepts writes requires attempting a write,
  so `--check` writes one node and removes it. An earlier revision claimed "never mutate"
  and did; the claim is dropped rather than the behaviour dressed up.
* **The defect is not fixed upstream.** It recurs on every container restart. This buys the
  fleet working writes and names the bug; it does not prevent it.
* **The RDB-load cause is not pinned.** `GRAPH.COPY` producing a good graph localises it to
  the reload path, but not further.

Usage:
    uv run python tools/graph_write_repair.py            # check, repair if broken
    uv run python tools/graph_write_repair.py --check    # report only; never repairs
    uv run python tools/graph_write_repair.py --graph X  # a different graph

Exit codes: 0 healthy or repaired | 2 cannot connect or state UNKNOWN | 3 broken and NOT repaired
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid

DEFAULT_GRAPH = "tortoise"
PROBE_LABEL = "GraphWriteRepairProbe"

# The ONE error text that authorises a repair. Everything else is UNKNOWN.
_DEFECT = "graph was deleted or replaced while the query was running"
# Errors that mean "cannot determine", never "broken": the server is not ready, or the
# transport failed. Repairing on any of these is how the graph was destroyed.
_UNKNOWN_MARKERS = (
    "is loading the dataset in memory",
    "BusyLoadingError",
    "ConnectionError",
    "TimeoutError",
    "No connection",
)


class Unreachable(RuntimeError):
    """Cannot determine the graph's state -- never a licence to repair."""


class Defect(RuntimeError):
    """The specific, measured reload defect -- the only condition that repairs."""


def connect(port: int):
    import falkordb  # noqa: PLC0415

    # Pin 127.0.0.1, never `localhost` (#6666).
    return falkordb.FalkorDB(host="127.0.0.1", port=port)


def node_count(graph) -> int:
    return int(graph.query("MATCH (n) RETURN count(n)").result_set[0][0])


def probe(db, name: str, attempts: int = 2, delay: float = 2.0) -> tuple[str, str]:
    """Return ("ok"|"defect"|"unknown", detail). Never raises.

    Establishes PRESENCE FIRST with the key catalogue, because a Cypher read on a missing
    graph auto-creates an empty one and would then report a healthy zero-node graph.
    """
    from tortoise.graph_delete_guard import graph_exists  # noqa: PLC0415

    if not graph_exists(db, name):
        return "absent", f"graph {name!r} does not exist"

    graph = db.select_graph(name)
    last = ""
    for i in range(attempts):
        try:
            graph.query(f"CREATE (t:{PROBE_LABEL} {{id:'probe'}})")
        except Exception as exc:  # noqa: BLE001 - classifying is the whole job
            last = f"{type(exc).__name__}: {exc}"
            if _DEFECT in last:
                if i + 1 < attempts:
                    time.sleep(delay)
                    continue
                return "defect", last
            if any(m in last for m in _UNKNOWN_MARKERS):
                if i + 1 < attempts:
                    time.sleep(delay)
                    continue
                return "unknown", last
            return "unknown", f"unclassified failure (refusing to repair): {last}"
        # The write was accepted. Clean up; a failed cleanup is NOT a graph failure.
        try:
            graph.query(f"MATCH (t:{PROBE_LABEL}) DELETE t")
        except Exception as exc:  # noqa: BLE001
            return "unknown", (f"the probe write was ACCEPTED but cleanup failed ({exc}) -- "
                               f"the graph writes; leaving a {PROBE_LABEL} node behind")
        return "ok", "writes accepted"
    return "unknown", last


def repair(db, name: str, verbose: bool = True) -> tuple[bool, str]:
    """COPY -> verify clone -> atomic RENAME. No GRAPH.DELETE of the live graph, ever."""
    from tortoise.graph_delete_guard import (  # noqa: PLC0415
        graph_delete_lock,
        graph_exists,
        safe_graph_delete,
    )

    def say(msg: str) -> None:
        if verbose:
            print(msg, flush=True)

    # A per-run scratch name: a FIXED name lets two runs (the timer and a hand-run) delete
    # each other's clone.
    scratch = f"__gwr_{uuid.uuid4().hex[:12]}"

    with graph_delete_lock(db) as acquired:
        if not acquired:
            return False, "could not take the graph-delete lock (another repair may be running)"

        before = node_count(db.select_graph(name))
        say(f"  node count before repair: {before}")
        say(f"  [1/4] GRAPH.COPY {name} -> {scratch}   (the clone is also the fallback)")
        try:
            db.connection.execute_command("GRAPH.COPY", name, scratch)
        except Exception as exc:  # noqa: BLE001
            return False, f"GRAPH.COPY failed: {exc}"

        clone_nodes = node_count(db.select_graph(scratch))
        say(f"  [2/4] clone node count: {clone_nodes}")
        if clone_nodes != before:
            safe_graph_delete(db, scratch)
            return False, f"clone has {clone_nodes} nodes, source has {before} - refusing to swap"

        kind, detail = probe(db, scratch, attempts=1)
        if kind != "ok":
            # RETAIN the clone: it may be the only copy. Never clean up a failed repair.
            return False, (f"the clone is not usable ({kind}: {detail}) - refusing to swap; "
                           f"clone {scratch} retained")
        say("  [3/4] clone is complete and writable")

        # ATOMIC. `RENAME` overwrites the destination in one step, so there is never a window
        # in which `name` is absent -- the window is what auto-created an empty graph and
        # destroyed this graph on 2026-10-13.
        say(f"  [4/4] RENAME {scratch} -> {name}   (atomic overwrite; no delete window)")
        try:
            db.connection.execute_command("RENAME", scratch, name)
        except Exception as exc:  # noqa: BLE001
            present = graph_exists(db, name)
            return False, (f"RENAME failed: {exc}; source graph present={present}, "
                           f"clone {scratch} retained as the fallback")

    after = node_count(db.select_graph(name))
    say(f"  node count after repair: {after}")
    # Only a LOSS is a failure. A gain is a concurrent fleet write landing on a now-healthy
    # graph, which is the outcome we wanted.
    if after < before:
        return False, f"post-repair count {after} < pre-repair {before} - DATA LOSS, investigate"

    kind, detail = probe(db, name, attempts=2)
    if kind != "ok":
        return False, f"still not writable after repair ({kind}: {detail})"
    if not graph_exists(db, scratch):
        pass  # RENAME removed the source; expected.
    return True, f"repaired: {after} nodes, writes restored"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--graph", default=DEFAULT_GRAPH)
    ap.add_argument("--port", type=int, default=int(os.environ.get("FALKORDB_PORT", "16379")))
    ap.add_argument("--check", action="store_true", help="report only; never repairs")
    ap.add_argument("--quiet", action="store_true",
                    help="suppress step narration (the verdict and every count still print)")
    args = ap.parse_args()

    def say(msg: str) -> None:
        if not args.quiet:
            print(msg, flush=True)

    try:
        db = connect(args.port)
        from tortoise.graph_delete_guard import graph_exists  # noqa: PLC0415
        if not graph_exists(db, args.graph):
            print(f"⛔ graph '{args.graph}' is ABSENT on 127.0.0.1:{args.port} -- "
                  f"this is not a repair case (the defect leaves the key present and "
                  f"readable). Do not create it here: investigate why it is gone.",
                  file=sys.stderr)
            return 3
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: cannot reach FalkorDB on 127.0.0.1:{args.port}: {exc}", file=sys.stderr)
        return 2

    say(f"graph '{args.graph}': probing the write path...")
    kind, detail = probe(db, args.graph)

    if kind == "ok":
        # Counts print even under --quiet: a log that cannot answer "did the repair
        # preserve the data?" is not a read artifact.
        print(f"graph '{args.graph}': HEALTHY - {detail}")
        return 0

    if kind in ("unknown", "absent"):
        print(f"⚠  graph '{args.graph}': state UNKNOWN - {detail}\n"
              f"   Not repairing. Only the specific reload defect authorises a repair; "
              f"anything else is an unestablished read.", file=sys.stderr)
        return 2

    print(f"⚠  graph '{args.graph}' is UNWRITABLE: {detail}\n"
          f"   Known FalkorDB 6.0.1 reload defect (see the docstring). READS work; the "
          f"fleet cannot write.", file=sys.stderr)
    if args.check:
        print("   --check given: not repairing.", file=sys.stderr)
        return 3

    say(f"graph '{args.graph}': repairing...")
    ok, msg = repair(db, args.graph, verbose=not args.quiet)
    if ok:
        print(f"graph '{args.graph}': REPAIRED - {msg}")
        print("note: this recurs on the next container restart (upstream, RDB load path).")
        return 0

    print(f"⛔ REPAIR FAILED for graph '{args.graph}': {msg}", file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())

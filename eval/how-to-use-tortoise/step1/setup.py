#!/usr/bin/env python3
"""Seed the pre-existing graph for the INCREMENTAL Step-1 inputs (#9675).

Four of the ten inputs are not standalone: the interesting write failures only
happen *against existing knowledge* —

* ``correction_existing_claim`` — supersede vs edit (the old claim must survive),
* ``duplicate_entity``         — re-mention vs a second node,
* ``contradicting_claim``      — NAND against a Point vs dampening an operator,
* ``product_spec_object``      — v1 replaced by v2 as a lifecycle fold.

Each fixture writes the "before" graph a real memory would already hold. Nothing
here is guidance for the eval agent: it is state, and the ids are printed so the
run record can be read back against them.

Usage:
    uv run python eval/how-to-use-tortoise/step1/setup.py \
        --id duplicate_entity --db /tmp/ax-eval/duplicate_entity.db
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path


def _open(db: str):
    if db.startswith(("docker://", "redis://", "rediss://")):
        os.environ["TORTOISE_DB_URI"] = db
        os.environ.pop("TORTOISE_DB_PATH", None)
    else:
        os.environ.pop("TORTOISE_DB_URI", None)
        os.environ["TORTOISE_DB_PATH"] = db
    from tortoise.sdk import TortoiseSDK

    return TortoiseSDK()


def seed(input_id: str, db: str) -> dict:
    """Create the before-graph for ``input_id`` and return the ids minted."""
    sdk = _open(db)
    minted: dict[str, str] = {}
    try:
        if input_id == "correction_existing_claim":
            p1 = sdk.create_point(
                "statement",
                "Our Q3 churn was 4.1%.",
                when="2026-10-01",
            )
            minted["P1_churn_4_1"] = p1["id"]
            src = sdk.create_source(
                "https://dash.internal.example/churn/q3",
                "document",
                tier="T1",
                title="Internal churn dashboard, Q3 export",
            )
            minted["source"] = src["url"]
            # NOTE: deliberately NO provenance edge here. `extractedFrom` is in
            # §3.3 but NOT in §3.9's `create_edge` vocabulary, so a hand write
            # has no validated route to attach a claim to its source (it is
            # produced by the ingest paths only). That gap is itself a Step-1
            # measurement, so the fixture must not paper over it.

        elif input_id == "duplicate_entity":
            o1 = sdk.create_object("Northwind Logistics", objectKind="other")
            minted["O1_northwind"] = o1["id"]

        elif input_id == "contradicting_claim":
            p2 = sdk.create_point(
                "statement",
                "Solstice Robotics passed its safety audit on 2026-08-28.",
            )
            p3 = sdk.create_point("statement", "Acme released tranche 2.")
            op = sdk.create_operator("IMPL", p2["id"], [p3["id"]])
            minted["P2_audit_passed"] = p2["id"]
            minted["P3_tranche_released"] = p3["id"]
            minted["op_impl"] = op.get("id", "") if isinstance(op, dict) else str(op)

        elif input_id == "product_spec_object":
            o1 = sdk.create_object(
                "Beta memory API plan v1",
                objectKind="plan",
                goalState="active",
                status="live",
            )
            minted["O1_plan_v1"] = o1["id"]

        else:
            raise SystemExit(f"{input_id!r} is not an incremental input — no setup needed")
    finally:
        sdk.close()
    return minted


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--id", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--fresh", action="store_true", help="delete the db first")
    args = ap.parse_args()

    if not args.db.startswith(("docker://", "redis://", "rediss://")):
        p = Path(args.db)
        if args.fresh and p.exists():
            shutil.rmtree(p) if p.is_dir() else p.unlink()
        p.parent.mkdir(parents=True, exist_ok=True)
    minted = seed(args.id, args.db)
    print(json.dumps({"input": args.id, "db": args.db, "minted": minted}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

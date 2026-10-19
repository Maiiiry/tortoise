"""Probe: can a post-write EP-selector call report participation for exactly the written operators?"""
import os, tempfile, json
from tortoise.sdk import TortoiseSDK

d = tempfile.mkdtemp(prefix="m8530c_")
sdk = TortoiseSDK(os.path.join(d, "t.db"))
bundle = {"points": [
    {"ref": "p1", "kind": "statement", "content": "Alpha claim one."},
    {"ref": "p2", "kind": "statement", "content": "Beta claim two."},
    {"ref": "p3", "kind": "statement", "content": "Gamma claim three."},
], "connections": [
    {"from": "p1", "to": "p2", "operator": "IMPL"},
    {"from": "p1", "to": "p3", "operator": "IMPL"},
]}
res = sdk.ingest(bundle, promotion_policy="auto")
pt_ids = res["ids"]["points"]
op_ids = [c for c in res["ids"]["connections"] if isinstance(c, str)]
print("written ops:", op_ids)
# make p3 live explicitly to see a participating operator
sdk.update_point(pt_ids[2], status="live")
skipped = []
ep = sdk._get_ep()
factors = ep._affected_factors(set(pt_ids), include_draft=False, skipped=skipped)
print("factors:", [(f[0], f[1], f[2]) for f in factors])
print("skipped:", json.dumps(skipped, indent=1))
sdk.close()

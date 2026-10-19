"""Measurement: under promotion_policy='auto', are written operators EP-inert? (#8530 finding 2)"""
import os, tempfile, json
from tortoise.sdk import TortoiseSDK

d = tempfile.mkdtemp(prefix="m8530b_")
sdk = TortoiseSDK(os.path.join(d, "t.db"))
bundle = {"points": [
    {"ref": "p1", "kind": "statement", "content": "Alpha claim one."},
    {"ref": "p2", "kind": "statement", "content": "Beta claim two."},
], "connections": [{"from": "p1", "to": "p2", "operator": "IMPL"}]}
res = sdk.ingest(bundle, promotion_policy="auto")
print("RESPONSE:", json.dumps({k: res[k] for k in ("created", "deduped", "warnings")}, indent=1))
print("ids:", res["ids"]["points"], "op:", res["ids"]["connections"])
for pid in res["ids"]["points"]:
    p = sdk.get_point(pid)
    print("point", pid, "status=", repr(p.get("status")), "embedded=", p.get("embedding") is not None)
for oid in res["ids"]["connections"]:
    print("operator", oid, "status=", repr(sdk.get_point(oid).get("status")))
ep = sdk._get_ep() if hasattr(sdk, "_get_ep") else None
print("has ep?", ep is not None)
# run EP and inspect skipped factors
try:
    sdk.compute_confidence()
except Exception as e:
    print("compute_confidence raised:", type(e).__name__, e)
print("skipped_operators / diag:")
for attr in ("skipped_factors", "_last_skipped_factors"):
    fn = getattr(sdk, attr, None) or getattr(getattr(sdk, "ep", None), attr, None)
    print(" ", attr, fn if not callable(fn) else fn())
sdk.close()

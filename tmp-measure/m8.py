import os, tempfile, json
from tortoise.sdk import TortoiseSDK
d = tempfile.mkdtemp(prefix="m8530_ep2_")
sdk = TortoiseSDK(os.path.join(d, "t.db"))
res = sdk.ingest({"points": [{"ref": "a", "kind": "statement", "content": "EP2 A claim."},
                              {"ref": "b", "kind": "statement", "content": "EP2 B claim."}],
                  "connections": [{"from": "a", "to": "b", "operator": "IMPL"}]},
                 promotion_policy="auto")
for pid in res["ids"]["points"]:
    sdk.set_point_baseline(pid, 3.0, 1.0)
op = [c for c in res["ids"]["connections"] if isinstance(c, str)][0]
try:
    out = sdk.compute_confidence(factors=[op])
    print("diag:", out.get("diagnostic"), "skipped:", json.dumps(out.get("skipped_factors")))
except Exception as e:
    print("ERR", type(e).__name__, e)
print("coverage:", json.dumps(res["coverage"]["ep"]["operators"]))

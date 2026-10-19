import os, tempfile, json
from tortoise.sdk import TortoiseSDK
d = tempfile.mkdtemp(prefix="m8530_ep_")
sdk = TortoiseSDK(os.path.join(d, "t.db"))
res = sdk.ingest({"points": [{"ref": "a", "kind": "statement", "content": "EP A claim."},
                              {"ref": "b", "kind": "statement", "content": "EP B claim."}],
                  "connections": [{"from": "a", "to": "b", "operator": "IMPL"}]},
                 promotion_policy="auto")
print("coverage ops:", json.dumps(res["coverage"]["ep"]["operators"]))
for pid in res["ids"]["points"]:
    sdk.set_point_baseline(pid, 3.0, 1.0)
out = sdk.compute_confidence()
print("diagnostic:", out.get("diagnostic"))
print("skipped_factors:", json.dumps(out.get("skipped_factors"), indent=1)[:600])
print("converged:", out.get("converged"))
sdk.close()

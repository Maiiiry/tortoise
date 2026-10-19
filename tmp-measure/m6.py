import os, tempfile, json
from tortoise.sdk import TortoiseSDK
d = tempfile.mkdtemp(prefix="m8530_g_")
sdk = TortoiseSDK(os.path.join(d, "t.db"))
res = sdk.ingest({"points": [{"ref": "a", "kind": "statement", "content": "Gated A."},
                              {"ref": "b", "kind": "statement", "content": "Gated B."}],
                  "connections": [{"from": "a", "to": "b", "operator": "NAND"}]})
print(json.dumps(res["coverage"], indent=1))
sdk.close()

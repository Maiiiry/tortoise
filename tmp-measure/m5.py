"""Probe the coverage report: gated, auto, auto+promoted, embedded-healthy."""
import os, tempfile, json
from tortoise.sdk import TortoiseSDK
import tortoise.embeddings as E

def show(res):
    print("created:", res["created"], "warnings:", len(res["warnings"]))
    print("coverage:", json.dumps(res["coverage"], indent=1))

for policy in ("gated", "auto"):
    d = tempfile.mkdtemp(prefix=f"m8530_{policy}_")
    sdk = TortoiseSDK(os.path.join(d, "t.db"))
    bundle = {"points": [
        {"ref": "p1", "kind": "statement", "content": f"Alpha {policy} one."},
        {"ref": "p2", "kind": "statement", "content": f"Beta {policy} two."},
    ], "connections": [{"from": "p1", "to": "p2", "operator": "IMPL"}]}
    print(f"=== policy={policy}")
    res = sdk.ingest(bundle, promotion_policy=policy)
    show(res)
    # promote the draft target, re-ingest the same bundle -> participation should appear
    for pid in res["coverage"]["ep"]["points"]["not_live_ids"]:
        sdk.update_point(pid, status="live")
    print("--- after promoting non-live ids, re-ingest")
    res2 = sdk.ingest(bundle, promotion_policy="auto")
    show(res2)
    sdk.close()

# healthy embedder stub
d = tempfile.mkdtemp(prefix="m8530_emb_")
sdk = TortoiseSDK(os.path.join(d, "t.db"))
width = sdk._get_proj().required_embedding_dim or 384
E.compute_embedding = lambda text, *a, **k: [0.01] * width
E.EmbeddingModel._instance = None
E.EmbeddingModel._last_failed_at = None
class _M:  # fake loaded model so status() reports ready
    _model = object()
E.EmbeddingModel._instance = _M()
print("=== healthy embedder")
res = sdk.ingest({"points": [{"ref": "q1", "kind": "statement", "content": "Embed me."}]})
show(res)
sdk.close()

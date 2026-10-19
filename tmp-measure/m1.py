"""Measurement: does ingest declare embedding coverage? (#8530 finding 1)"""
import os, tempfile, json
from tortoise.sdk import TortoiseSDK
from tortoise import embeddings

# degrade the embedder the way a real load failure does
embeddings.EmbeddingModel._instance = None
import tortoise.embeddings as E
_orig_get = E.EmbeddingModel.get
E.EmbeddingModel.get = classmethod(lambda cls: None)
# also make sure compute_embedding path used by create_point sees None
E.compute_embedding = lambda text, *a, **k: None

d = tempfile.mkdtemp(prefix="m8530a_")
sdk = TortoiseSDK(os.path.join(d, "t.db"))
bundle = {"points": [
    {"ref": "p1", "kind": "claim", "content": "Alpha claim one."},
    {"ref": "p2", "kind": "claim", "content": "Beta claim two."},
    {"ref": "p3", "kind": "claim", "content": "Gamma claim three."},
], "connections": [{"from": "p1", "to": "p2", "operator": "IMPL"}]}
res = sdk.ingest(bundle)
print("RESPONSE:", json.dumps(res, indent=1)[:1200])
ids = res["ids"]["points"]
rows = sdk._get_proj().g.query(
    "MATCH (p:Point) WHERE p.id IN $ids RETURN p.id, p.embedding IS NULL",
    params={"ids": ids}).result_set
print("MISSING EMBEDDINGS:", sum(1 for r in rows if r[1]), "of", len(rows))
sdk.close()

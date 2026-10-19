"""Does a mitigation Point (NULL status) participate in EP? (#8530 finding 2, mitigate_operator half)"""
import os, tempfile, json
from tortoise.sdk import TortoiseSDK
from tortoise.weights import compute_operator_weight

d = tempfile.mkdtemp(prefix="m8530d_")
sdk = TortoiseSDK(os.path.join(d, "t.db"))
a = sdk.create_point("statement", "A: pilot proof of pain precedes capital.", status="live")
b = sdk.create_point("statement", "B: capital follows.", status="live")
op = sdk.create_operator("IMPL", a["id"], [b["id"]], promote_source=False)
sdk.update_point(op["id"], status="live")
mit = sdk.mitigate_operator(op["id"], "one pilot is not proof", strength=0.5)
print("mitigation status:", repr(mit.get("status")), "id", mit["id"])
skipped = []
ep = sdk._get_ep()
factors = ep._affected_factors({a["id"], b["id"], op["id"]}, include_draft=False, skipped=skipped)
print("factors:", [(f[0], f[1], f[2], f[3]) for f in factors])
print("skipped:", skipped)
print("weight:", compute_operator_weight(sdk._get_proj(), op["id"]))
# strict allow-list readers: does the mitigation point survive?
from tortoise.live import _live_only
print("live_only(mit):", _live_only("n.status"))
rows = sdk._get_proj().g.query(
    "MATCH (op:Point {id:$o})-[:mitigated_by]->(m:Point) RETURN m.id, m.status",
    params={"o": op["id"]}).result_set
print("mitigated_by rows:", rows)
sdk.close()

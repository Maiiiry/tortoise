"""Throwaway probe for #9063 — measure the calibration gate's universe vs EP's
participant set. Run: .venv/bin/python probe_9063.py"""
import os
import shutil
import tempfile

from tortoise.sdk import TortoiseSDK
from tortoise.exceptions import CalibrationError

TMP = tempfile.mkdtemp(prefix="probe9063_")
DB = os.path.join(TMP, "t.db")
sdk = TortoiseSDK(DB)


def gate_raises(**kw):
    try:
        r = sdk.compute_confidence(require_calibration=True, **kw)
        return False, r
    except CalibrationError as e:
        return True, str(e)


print("=" * 70)
print("CASE 1: :Subject as an operator input (issue body measurement)")
s = sdk.create_subject("subject B")
stmt = sdk.create_point("statement", "the statement", credibility="gold")
op = sdk.create_operator("IMPL", stmt["id"], [s["id"]])
raised, res = gate_raises(factors=[op["id"]])
print("  subject id:", s["id"], "label:", s.get("label") or s.get("pointKind"))
print("  gate_raised:", raised, "|", (res if raised else ""))
if not raised:
    row = sdk._get_proj().g.query(
        "MATCH (n:Subject {id:$id}) RETURN n.confidence, n.ep_alpha, "
        "n.baseline_set", params={"id": s["id"]}).result_set
    print("  subject after run (conf, ep_alpha, baseline_set):", row)

print("=" * 70)
print("CASE 2: set_point_baseline on a :Subject — is there a write that closes it?")
before = sdk._get_proj().g.query(
    "MATCH (n:Subject {id:$id}) RETURN n.ep_alpha, n.baseline_set",
    params={"id": s["id"]}).result_set
ret = sdk.set_point_baseline(s["id"], 5.0, 2.0)
after = sdk._get_proj().g.query(
    "MATCH (n:Subject {id:$id}) RETURN n.ep_alpha, n.baseline_set",
    params={"id": s["id"]}).result_set
print("  returned:", ret)
print("  graph before:", before, "after:", after)

print("=" * 70)
print("CASE 3: :Event as an operator input")
e = sdk.create_event("event B", "generic")
stmt2 = sdk.create_point("statement", "stmt for event", credibility="gold")
op2 = sdk.create_operator("IMPL", stmt2["id"], [e["id"]])
raised, res = gate_raises(factors=[op2["id"]])
print("  gate_raised:", raised, "|", (res if raised else ""))
if not raised:
    print("  event after:", sdk._get_proj().g.query(
        "MATCH (n:Event) WHERE n.eventId IS NOT NULL OR n.id = $id "
        "RETURN n.id, n.confidence, n.ep_alpha", params={"id": e["id"]}).result_set)

print("=" * 70)
print("CASE 4: pointKind='assessment' as an operator input")
a = sdk.create_point("assessment", "assessment B")
stmt3 = sdk.create_point("statement", "stmt for assessment", credibility="gold")
op3 = sdk.create_operator("IMPL", stmt3["id"], [a["id"]])
raised, res = gate_raises(factors=[op3["id"]])
print("  gate_raised:", raised, "|", (res if raised else ""))
print("  in calibrate_summary:",
      [x["id"] for x in sdk.calibrate_summary() if x["id"] == a["id"]])

print("=" * 70)
print("CASE 5: legacy operator without is_operator property")
legacy = sdk.create_point("statement", "legacy shape", credibility="gold")
op4 = sdk.create_operator("IMPL", legacy["id"], [a["id"]])
sdk._get_proj().g.query(
    "MATCH (n:Point {id:$id}) REMOVE n.is_operator SET n.op_type = 'IMPL'",
    params={"id": op4})
print("  legacy op nodes:", sdk._get_proj().g.query(
    "MATCH (n:Point {id:$id}) RETURN n.is_operator, n.op_type, n.baseline_set",
    params={"id": op4}).result_set)

sdk.close()
shutil.rmtree(TMP, ignore_errors=True)

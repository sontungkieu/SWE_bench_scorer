"""Read-only collection of pilot reports and app-specific Modal billing."""
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


def reports(root, pattern):
    found = {}
    for path in root.glob(pattern):
        try:
            found.update(json.loads(path.read_text()))
        except json.JSONDecodeError:
            pass
    return found


def test_seconds(root, pattern):
    times = {}
    for path in root.glob(pattern):
        matches = re.findall(r"Test runtime: ([0-9_.]+) seconds", path.read_text())
        if matches:
            times[path.parent.name] = float(matches[-1].replace("_", ""))
    return times


def main():
    out = Path(sys.argv[1]).resolve()
    selection = json.loads((out/"selection.json").read_text())
    native = reports(out/"native", "logs/**/report.json")
    vm = reports(out/"vm", "output/**/report.json")
    receipt = json.loads((out/"vm/receipt.json").read_text())
    native_summary = (json.loads((out/"native/summary.json").read_text())
                      if (out/"native/summary.json").exists() else {})
    native_ids = sorted(set(re.findall(r"\bap-[A-Za-z0-9]+",(out/"native/console.log").read_text())))
    ids = set(native_ids + [receipt["app_id"]])
    billed = subprocess.run([sys.executable,"-m","modal","billing","report","--for","this month",
                              "--show-resources","--json"],check=True,capture_output=True,text=True,timeout=90)
    rows = [r for r in json.loads(billed.stdout) if r.get("object_id") in ids]
    cost = {name:sum((Decimal(str(r["cost"])) for r in rows if r["object_id"] in app_ids),Decimal(0))
            for name,app_ids in [("native",set(native_ids)),("vm",{receipt["app_id"]})]}
    timings_native = test_seconds(out/"native", "logs/**/run_instance.log")
    timings_vm = test_seconds(out/"vm", "output/**/run_instance.log")
    images = {}
    for path in (out/"vm").glob("output/batch-*/images.json"):
        for record in json.loads(path.read_text()):
            images[record["instance_id"]] = record
    items = []
    for row in selection["selected"]:
        iid=row["instance_id"]
        a=native.get(iid); b=vm.get(iid)
        items.append(dict(row,native_report_present=a is not None,vm_report_present=b is not None,
                          native_resolved=a.get("resolved") if a else None,
                          vm_resolved=b.get("resolved") if b else None,
                          complete_report_equal=(a==b) if a and b else None,
                          native_test_seconds=timings_native.get(iid),
                          vm_test_seconds=timings_vm.get(iid),
                          vm_image_load_seconds=images.get(iid,{}).get("seconds")))
    summary={"profile":"phamvanvuhoan","selection":selection,"instances":items,
             "native_reports":len(native),"vm_reports":len(vm),
             "measurement_finished":all((out/name/"cleanup.json").exists() for name in ["native","vm"]),
             "wall_seconds":{"native":native_summary.get("wall_seconds"),"vm":receipt.get("wall_seconds")},
             "native_timed_out":native_summary.get("timed_out"),
             "harness_exit_codes":{"native":native_summary.get("exit_code"),"vm":receipt.get("exit_code")},
             "native_resolved":sum(r.get("resolved") is True for r in native.values()),
             "vm_resolved":sum(r.get("resolved") is True for r in vm.values()),
             "matched_complete_reports":sum(r["complete_report_equal"] is True for r in items),
             "app_ids":{"native":native_ids,"vm":[receipt["app_id"]]},
             "reported_cost_usd":{k:str(v) for k,v in cost.items()},
             "vm_cache_sources":dict(Counter(r["source"] for r in images.values())),
             "vm_total_image_load_seconds":sum(r["seconds"] for r in images.values()),
             "vm_total_cache_added_bytes":sum(r["cache_added_bytes"] for r in images.values()),
             "checked_at":datetime.now(timezone.utc).isoformat(),"billing_may_lag":True,
             "before_credits":True,"full_500_evaluated":False,"gold_controls_not_model_score":True}
    (out/"comparison.json").write_bytes((json.dumps(summary,indent=2)+"\n").encode())
    (out/"billing.json").write_bytes((json.dumps({"rows":rows,**{k:summary[k] for k in ["reported_cost_usd","checked_at","before_credits","billing_may_lag"]}},indent=2)+"\n").encode())
    print(json.dumps({k:summary[k] for k in ["native_reports","vm_reports","native_resolved","vm_resolved","matched_complete_reports","reported_cost_usd"]}))


if __name__=="__main__":
    main()

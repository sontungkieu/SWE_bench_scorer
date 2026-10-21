"""Collect matched snapshot results and provider billing without running compute."""
import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
import json
from pathlib import Path
import subprocess
import sys


def dump(path, value):
    path.write_bytes((json.dumps(value, indent=2) + "\n").encode())


def reports(root):
    found = {}
    for path in root.glob("batch-*/logs/**/report.json"):
        found.update(json.loads(path.read_text()))
    return found


def compare(expected, baseline, restored):
    return {"selected_count": len(expected), "baseline_reports": len(baseline),
            "snapshot_reports": len(restored),
            "snapshot_resolved": sum(row.get("resolved") is True for row in restored.values()),
            "missing_ids": sorted(set(expected) - restored.keys()),
            "extra_ids": sorted(restored.keys() - set(expected)),
            "different_report_ids": [iid for iid in expected if iid in restored and baseline.get(iid) != restored[iid]],
            "complete_report_matches": sum(iid in restored and baseline.get(iid) == restored[iid] for iid in expected)}


def cost_summary(costs):
    baseline = Decimal(costs["baseline_vm"])
    scoring = Decimal(costs["snapshot_score"])
    setup = Decimal(costs["canary"]) + Decimal(costs["snapshot_prepare"])
    saving = baseline - scoring
    return {"baseline_vm_usd": str(baseline), "snapshot_recurring_usd": str(scoring),
            "one_time_setup_including_canary_usd": str(setup),
            "first_use_total_usd": str(setup + scoring), "recurring_saving_usd": str(saving),
            "recurring_saving_percent": str(saving / baseline * 100) if baseline else None,
            "first_use_saving_percent": str((baseline - setup - scoring) / baseline * 100) if baseline else None,
            "break_even_runs": max(1, int((setup / saving).to_integral_value(rounding=ROUND_CEILING))) if saving > 0 else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--canary", required=True, type=Path)
    parser.add_argument("--billing-name", default="billing.json")
    args = parser.parse_args()
    out, baseline = args.out.resolve(), args.baseline.resolve()
    selected = json.loads((out / "selection.json").read_text())
    expected = [row["instance_id"] for row in selected["selected"]]
    if len(expected) != 24 or len(set(expected)) != 24:
        raise ValueError("Only the exact 24-instance pilot may be summarized")
    baseline_receipt = json.loads((baseline / "vm/receipt.json").read_text())
    canary = json.loads(args.canary.read_text())
    prepare = json.loads((out / "prepare-receipt.json").read_text())
    score = json.loads((out / "score-receipt.json").read_text())
    before = reports(baseline / "vm/output")
    after = reports(out / "score/output")
    result = compare(expected, before, after)
    result.update(frozen_pilot_sha256=selected["pilot_sha256"], frozen_predictions_sha256=selected["predictions_sha256"],
                  snapshot_image_id=prepare["snapshot_image_id"], snapshot_ttl_seconds=prepare["snapshot_ttl_seconds"],
                  canary_passed=canary.get("passed"), prepare_passed=prepare.get("passed"), score_passed=score.get("passed"),
                  all_sandboxes_terminated=all([canary.get("all_sandboxes_terminated"), prepare.get("sandbox_terminated"), score.get("sandbox_terminated")]),
                  baseline_vm_wall_seconds=baseline_receipt["wall_seconds"],
                  snapshot_scoring_wall_seconds=score["wall_seconds"],
                  snapshot_restore_docker_ready_seconds=score.get("restore_docker_ready_seconds"),
                  one_time_prepare_wall_seconds=prepare["wall_seconds"],
                  filesystem_snapshot_seconds=prepare.get("snapshot_seconds"),
                  full_500_evaluated=False, gold_controls_not_model_score=True,
                  image_cache_volume_attached=False, registry_secrets_injected=False)
    image_rows = [row for path in (out / "score/output").glob("batch-*/images.json") for row in json.loads(path.read_text())]
    result.update(image_sources=dict(Counter(row["source"] for row in image_rows)),
                  total_image_ensure_seconds=sum(row["seconds"] for row in image_rows),
                  total_image_cache_added_bytes=sum(row["cache_added_bytes"] for row in image_rows))
    roles = {"baseline_vm": baseline_receipt["app_id"], "canary": canary["app_id"],
             "snapshot_prepare": prepare["app_id"], "snapshot_score": score["app_id"]}
    command = [sys.executable, "-m", "modal", "billing", "report", "--start", "2026-10-21", "--end", "2026-10-23", "--show-resources", "--json"]
    response = subprocess.run(command, capture_output=True, text=True, check=True, timeout=90)
    rows = [row for row in json.loads(response.stdout) if row.get("object_id") in roles.values()]
    costs = {role: str(sum((Decimal(str(row["cost"])) for row in rows if row["object_id"] == app_id), Decimal(0)))
             for role, app_id in roles.items()}
    dump(out / args.billing_name, {"checked_at": datetime.now(timezone.utc).isoformat(), "app_ids": roles,
          "cost_by_role_usd": costs, "rows": rows, "before_credits": True, "not_final_invoice": True,
          "shared_storage_not_in_app_sum": True})
    result["billing_roles_with_no_rows"] = [role for role, app_id in roles.items() if not any(row["object_id"] == app_id for row in rows)]
    result["costs"] = cost_summary(costs)
    result["qualified"] = (result["snapshot_reports"] == result["snapshot_resolved"] == result["complete_report_matches"] == 24
                           and not result["missing_ids"] and not result["extra_ids"]
                           and result["all_sandboxes_terminated"] and score.get("passed")
                           and result["image_sources"] == {"local": 24})
    dump(out / "snapshot-reports.json", after)
    dump(out / "comparison.json", result)
    print(json.dumps(result))


if __name__ == "__main__":
    main()

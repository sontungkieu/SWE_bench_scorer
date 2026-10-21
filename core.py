"""Offline input validation and honest score accounting (stdlib only)."""
import json
import re
from pathlib import Path

DATASET = "SWE-bench/SWE-bench_Verified"
REVISION = "78f471bf655a3137b2e8a75af1501690ec009ec3"
HARNESS = "4.1.0"
CLIENT = "1.6.2"
MOCK_ID = "django__django-11099"


def load_predictions(path):
    rows = []
    ids = set()
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            raise ValueError(f"Blank line {number}")
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"Line {number}: expected JSON object")
        for key in ("instance_id", "model_name_or_path", "model_patch"):
            if not isinstance(row.get(key), str):
                raise ValueError(f"Line {number}: {key} must be a string")
        iid = row["instance_id"]
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+__[a-zA-Z0-9_.-]+-\d+", iid):
            raise ValueError(f"Invalid instance_id: {iid}")
        if iid in ids:
            raise ValueError(f"Duplicate instance_id: {iid}")
        if not row["model_name_or_path"].strip():
            raise ValueError("model_name_or_path cannot be empty")
        ids.add(iid)
        rows.append({key: row[key] for key in ("instance_id", "model_name_or_path", "model_patch")})
    if not rows:
        raise ValueError("Empty predictions file")
    if len({r["model_name_or_path"] for r in rows}) != 1:
        raise ValueError("Use one model_name_or_path per predictions file")
    return rows


def summarize(rows, reports, selected_ids, full=False):
    selected = set(selected_ids)
    by_id = {r["instance_id"]: r for r in rows}
    if not selected or selected - by_id.keys():
        raise ValueError("Selected IDs must have predictions")
    if full and len(selected) != 500:
        raise ValueError("Verified full evaluation requires all 500 IDs")
    resolved, unresolved, empty, errors = [], [], [], []
    for iid in sorted(selected):
        if not by_id[iid]["model_patch"].strip():
            empty.append(iid)
            unresolved.append(iid)
        elif iid not in reports:
            errors.append(iid)
        elif reports[iid].get("resolved") is True:
            resolved.append(iid)
        else:
            unresolved.append(iid)
    complete = not errors
    return {
        "scope": "full_verified" if full else "smoke_subset",
        "selected_count": len(selected),
        "resolved_count": len(resolved),
        "unresolved_count": len(unresolved),
        "empty_patch_count": len(empty),
        "error_or_missing_report_count": len(errors),
        "complete": complete,
        "resolved_ids": resolved,
        "unresolved_ids": unresolved,
        "error_or_missing_report_ids": errors,
        "subset_resolved_rate": len(resolved) / len(selected) if complete else None,
        "verified_score": len(resolved) / 500 if full and complete else None,
    }

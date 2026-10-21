"""Offline input validation and honest score accounting (stdlib only)."""
import json
import re
import csv
import io
from pathlib import Path

SCORER_VERSION = "0.3.0"
DATASET = "SWE-bench/SWE-bench_Verified"
REVISION = "78f471bf655a3137b2e8a75af1501690ec009ec3"
HARNESS = "4.1.0"
CLIENT = "1.6.2"
MOCK_ID = "django__django-11099"


def parse_predictions(text, suffix=".jsonl"):
    """Normalize JSONL, JSON list/ID mapping, or CSV into official predictions."""
    text = text.removeprefix("\ufeff")
    suffix = suffix.lower()
    if suffix == ".jsonl":
        records = []
        for number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                raise ValueError(f"Blank line {number}")
            records.append(json.loads(line))
    elif suffix == ".json":
        value = json.loads(text)
        if isinstance(value, dict):
            records = []
            for iid, row in value.items():
                if not isinstance(row, dict) or row.get("instance_id") != iid:
                    raise ValueError("JSON mapping keys must match each row's instance_id")
                records.append(row)
        elif isinstance(value, list):
            records = value
        else:
            raise ValueError("JSON must be a list of predictions or an instance_id mapping")
    elif suffix == ".csv":
        csv.field_size_limit(20 * 1024 * 1024)
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError("CSV requires unique column names")
        required = {"instance_id", "model_name_or_path", "model_patch"}
        if not required.issubset(reader.fieldnames):
            raise ValueError("CSV needs instance_id, model_name_or_path, model_patch columns")
        records = list(reader)
        if any(None in row for row in records):
            raise ValueError("CSV has extra cells; quote patches containing commas/newlines")
    else:
        raise ValueError("Supported file types: .json, .jsonl, .csv")
    return validate_predictions(records)


def validate_predictions(records):
    rows = []
    ids = set()
    for number, row in enumerate(records, 1):
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
        model = row["model_name_or_path"]
        if len(model) > 200 or any(ord(c) < 32 for c in model) or "\\" in model or ".." in model.split("/"):
            raise ValueError("Unsafe model_name_or_path")
        ids.add(iid)
        rows.append({key: row[key] for key in ("instance_id", "model_name_or_path", "model_patch")})
    if not rows:
        raise ValueError("Empty predictions file")
    if len(rows) > 500:
        raise ValueError("Verified accepts at most 500 predictions")
    if len({r["model_name_or_path"] for r in rows}) != 1:
        raise ValueError("Use one model_name_or_path per predictions file")
    return rows


def load_predictions(path):
    path = Path(path)
    return parse_predictions(path.read_text(encoding="utf-8-sig"), path.suffix)


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

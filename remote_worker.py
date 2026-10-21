"""Runs exclusively inside the Modal VM. No cloud credentials are injected."""
import argparse
import hashlib
import importlib.metadata
import json
import subprocess
import sys
import traceback
import time
from pathlib import Path

from core import DATASET, REVISION, HARNESS, MOCK_ID, SCORER_VERSION, load_predictions, summarize
from image_cache import ensure_image, sync_cache
from registry_cache import RegistryCache

ROOT = Path("/work")
OUT = ROOT / "output"


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def execute(command, log, cwd=ROOT, timeout=None):
    print("COMMAND", json.dumps(command), flush=True)
    with log.open("w") as stream:
        result = subprocess.run(command, cwd=cwd, stdout=stream,
                                stderr=subprocess.STDOUT, timeout=timeout)
    print("FINISHED", log.name, "exit_code", result.returncode, flush=True)
    return result.returncode


def evaluate(rows, selected, dataset_path, label, args):
    from swebench.harness.test_spec.test_spec import make_test_spec

    case = OUT / label
    case.mkdir(parents=True)
    pred_path = case / "preds.jsonl"
    pred_path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    # Pull once, retain content digests in the receipt, and let official harness
    # reuse the exact locally cached image for all controls in this VM.
    image_records = []
    dataset = {r["instance_id"]: r for r in json.loads(dataset_path.read_text())}
    # Bound disk use instead of preloading hundreds of multi-GB images.
    active = [iid for iid in selected if next(r for r in rows if r["instance_id"] == iid)["model_patch"].strip()]
    batches = ([active[i:i + args.workers] for i in range(0, len(active), args.workers)]
               if args.mode == "full" else ([active] if active else []))
    rc = 0
    for index, batch in enumerate(batches):
        batch_images = []
        for iid in batch:
            spec = make_test_spec(dataset[iid], namespace="swebench")
            image = spec.instance_image_key
            record = ensure_image(image, args.cache, case / f"image-{iid}.log",
                                  require_cached=args.require_cached)
            image_records.append(dict(record, instance_id=iid))
            print("SWE_IMAGE_JSON=" + json.dumps(image_records[-1]), flush=True)
            batch_images.append(image)
        write_json(case / "images.json", image_records)
        command = [sys.executable, "-m", "swebench.harness.run_evaluation",
                   "--dataset_name", str(dataset_path), "--split", "test",
                   "--predictions_path", str(pred_path), "--instance_ids", *batch,
                   "--max_workers", str(args.workers), "--run_id", label,
                   "--timeout", str(args.test_timeout), "--modal", "false",
                   "--cache_level", "instance", "--clean", "false"]
        log_name = f"harness-batch-{index:03d}.log" if args.mode == "full" else "harness.log"
        tested_at = time.monotonic()
        batch_rc = execute(command, case / log_name, cwd=case)
        print("SWE_TEST_SECONDS=" + str(time.monotonic() - tested_at), flush=True)
        rc = rc or batch_rc
        if args.mode == "full":
            execute(["docker", "image", "rm", *batch_images], case / f"cleanup-{index:03d}.log")
    reports = {}
    for path in case.glob("logs/**/report.json"):
        value = json.loads(path.read_text())
        reports.update(value)
    summary = summarize(rows, reports, selected, full=args.mode == "full")
    summary.update(harness_exit_code=rc, label=label)
    write_json(case / "summary.json", summary)
    print("SWE_SUMMARY_JSON=" + json.dumps(summary), flush=True)
    return summary


def prewarm(data, args):
    from swebench.harness.test_spec.test_spec import make_test_spec
    selected = data if args.all_instances else [r for r in data if r["instance_id"] in args.instance_ids]
    if not selected or (not args.all_instances and len(selected) != len(set(args.instance_ids))):
        raise ValueError("Unknown or missing Verified instance IDs")
    records = []
    failures = []
    started = time.monotonic()
    for row in sorted(selected, key=lambda r: r["instance_id"]):
        if time.monotonic() - started > args.work_seconds - 650:
            break
        iid = row["instance_id"]
        reference = make_test_spec(row, namespace="swebench").instance_image_key
        try:
            record = args.cache.prefetch(reference)
            record["instance_id"] = iid
            records.append(record)
            print("SWE_CACHE_PROGRESS=" + json.dumps(dict(record, completed=len(records), requested=len(selected))), flush=True)
        except Exception as exc:
            failures.append({"instance_id": iid, "error": f"{type(exc).__name__}: {exc}"})
            (OUT / "cache-error.txt").write_text(traceback.format_exc())
            # Preserve completed images and stop; no paid retry loop on rate limits.
            break
        finally:
            write_json(OUT / "cache-summary.json", {"requested": len(selected), "cached": len(records),
                       "complete": len(records) == len(selected), "records": records, "failures": failures,
                       "seconds": time.monotonic() - started, "storage_bytes": args.cache.used})
    sync_cache(args.cache.root)
    write_json(OUT / "cache-summary.json", {"requested": len(selected), "cached": len(records),
               "complete": len(records) == len(selected), "records": records, "failures": failures,
               "seconds": time.monotonic() - started, "storage_bytes": args.cache.used})
    return 0 if len(records) == len(selected) else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["mock", "smoke", "full", "cache"], required=True)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--test-timeout", type=int, default=600)
    parser.add_argument("--instance-ids", nargs="+", default=[MOCK_ID])
    parser.add_argument("--cache-root", default="")
    parser.add_argument("--cache-limit-gib", type=int, default=256)
    parser.add_argument("--require-cached", action="store_true")
    parser.add_argument("--all-instances", action="store_true")
    parser.add_argument("--work-seconds", type=int, default=1620)
    args = parser.parse_args()
    args.cache = RegistryCache(args.cache_root, args.cache_limit_gib) if args.cache_root else None
    OUT.mkdir(parents=True)
    write_json(OUT / "environment.json", {"scorer_version": SCORER_VERSION, "python": sys.version,
               "harness": importlib.metadata.version("swebench"), "dataset": DATASET,
               "dataset_revision": REVISION, "mode": args.mode})
    execute([sys.executable, "-m", "pip", "freeze"], OUT / "pip-freeze.txt")
    execute([sys.executable, "-m", "swebench.harness.run_evaluation", "--help"], OUT / "harness-help.txt")
    if importlib.metadata.version("swebench") != HARNESS:
        raise RuntimeError("Unexpected harness version")
    from datasets import load_dataset
    data = list(load_dataset(DATASET, revision=REVISION, split="test"))
    if len(data) != 500 or len({r["instance_id"] for r in data}) != 500:
        raise RuntimeError("Pinned Verified dataset must contain 500 unique instances")
    dataset_path = ROOT / "verified.json"
    write_json(dataset_path, data)
    ids = {row["instance_id"] for row in data}
    write_json(OUT / "dataset-manifest.json", {"dataset": DATASET, "revision": REVISION,
               "count": len(data), "sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest()})
    if args.mode == "cache":
        if args.cache is None:
            raise ValueError("Cache action needs persistent cache root")
        return prewarm(data, args)
    if args.mode == "mock":
        instance = next(r for r in data if r["instance_id"] == MOCK_ID)
        gold = instance["patch"]
        # Valid unified diff with a harmless new file: applies, but does not fix
        # the bug. This negative control exercises actual failing tests.
        wrong = "diff --git a/swebench_mock_negative.txt b/swebench_mock_negative.txt\nnew file mode 100644\n--- /dev/null\n+++ b/swebench_mock_negative.txt\n@@ -0,0 +1 @@\n+This mock intentionally leaves the original bug unfixed.\n"
        controls = [("gold", gold, True), ("wrong", wrong, False), ("empty", "", False)]
        checks = []
        for label, patch, expected in controls:
            rows = [{"instance_id": MOCK_ID, "model_name_or_path": "MOCK-CONTROL-" + label,
                     "model_patch": patch}]
            summary = evaluate(rows, [MOCK_ID], dataset_path, label, args)
            passed = (summary["complete"] and summary["harness_exit_code"] == 0
                      and summary["resolved_count"] == int(expected))
            checks.append({"control": label, "expected_resolved": expected, "passed": passed})
        write_json(OUT / "mock-checks.json", {"mock_only": True, "checks": checks,
                   "passed": all(c["passed"] for c in checks)})
        return 0 if all(c["passed"] for c in checks) else 1
    rows = load_predictions(ROOT / "preds.jsonl")
    by_id = {r["instance_id"]: r for r in rows}
    if by_id.keys() - ids:
        raise ValueError("Predictions contain IDs outside Verified")
    if args.mode == "full" and by_id.keys() != ids:
        raise ValueError("Full mode requires exactly all 500 Verified predictions (including empty patches)")
    selected = sorted(ids) if args.mode == "full" else args.instance_ids
    if set(selected) - by_id.keys():
        raise ValueError("Selected IDs missing from predictions")
    summary = evaluate(rows, selected, dataset_path, "evaluation", args)
    return 0 if summary["complete"] and summary["harness_exit_code"] == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        OUT.mkdir(exist_ok=True, parents=True)
        (OUT / "error.txt").write_text(traceback.format_exc())
        raise

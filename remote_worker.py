"""Runs exclusively inside the Modal VM. No cloud credentials are injected."""
import argparse
import hashlib
import importlib.metadata
import json
import subprocess
import sys
import traceback
from pathlib import Path

from core import DATASET, REVISION, HARNESS, MOCK_ID, SCORER_VERSION, load_predictions, summarize

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
    batches = ([selected[i:i + args.workers] for i in range(0, len(selected), args.workers)]
               if args.mode == "full" else [selected])
    rc = 0
    for index, batch in enumerate(batches):
        batch_images = []
        for iid in batch:
            spec = make_test_spec(dataset[iid], namespace="swebench")
            image = spec.instance_image_key
            cached = subprocess.run(["docker", "image", "inspect", image],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
            if not cached:
                pull_rc = execute(["docker", "pull", image], case / f"pull-{iid}.log", timeout=600)
                if pull_rc:
                    raise RuntimeError(f"Image pull failed: {image}; see pull log")
            inspected = json.loads(subprocess.check_output(["docker", "image", "inspect", image]))[0]
            image_records.append({"instance_id": iid, "reference": image,
                                  "id": inspected["Id"], "repo_digests": inspected.get("RepoDigests", [])})
            batch_images.append(image)
        write_json(case / "images.json", image_records)
        command = [sys.executable, "-m", "swebench.harness.run_evaluation",
                   "--dataset_name", str(dataset_path), "--split", "test",
                   "--predictions_path", str(pred_path), "--instance_ids", *batch,
                   "--max_workers", str(args.workers), "--run_id", label,
                   "--timeout", str(args.test_timeout), "--modal", "false",
                   "--cache_level", "instance", "--clean", "false"]
        log_name = f"harness-batch-{index:03d}.log" if args.mode == "full" else "harness.log"
        batch_rc = execute(command, case / log_name, cwd=case)
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["mock", "smoke", "full"], required=True)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--test-timeout", type=int, default=600)
    parser.add_argument("--instance-ids", nargs="+", default=[MOCK_ID])
    args = parser.parse_args()
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

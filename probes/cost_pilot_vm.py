"""Remote-only pilot: reuse the production Docker evaluator in batches of eight."""
import argparse
import importlib.metadata
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import time

sys.path.insert(0, "/work")
import remote_worker as worker
from core import summarize
from registry_cache import RegistryCache


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    data = json.loads(Path("/work/pilot.json").read_text())
    rows = [json.loads(x) for x in Path("/work/preds.jsonl").read_text().splitlines()]
    if not 20 <= len(data) <= 30 or len(rows) != len(data):
        raise ValueError("Pilot must have 20..30 matched instances, never full 500")
    assert importlib.metadata.version("swebench") == "4.1.0"
    worker.OUT.mkdir()
    worker.write_json(worker.OUT / "environment.json", {
        "harness": importlib.metadata.version("swebench"), "python": sys.version,
        "workers": args.workers, "dataset_count": len(data), "mock_gold_only": True})
    config = SimpleNamespace(workers=args.workers, test_timeout=600, mode="smoke",
                             cache=RegistryCache("/image-cache", 256), require_cached=True)
    reports = {}
    batches = []
    for start in range(0, len(data), args.workers):
        batch = rows[start:start + args.workers]
        ids = [r["instance_id"] for r in batch]
        label = f"batch-{start // args.workers:02d}"
        started = time.monotonic()
        try:
            result = worker.evaluate(batch, ids, Path("/work/pilot.json"), label, config)
            batches.append({"label": label, "seconds": time.monotonic()-started, "summary": result})
        except Exception as exc:
            batches.append({"label": label, "error": type(exc).__name__+": "+str(exc)})
            worker.write_json(worker.OUT / "batch-errors.json", batches)
        for path in (worker.OUT / label).glob("logs/**/report.json"):
            reports.update(json.loads(path.read_text()))
        # Same per-batch image eviction policy as the production full scorer.
        paths = worker.OUT / label / "images.json"
        if paths.exists():
            images = [r["reference"] for r in json.loads(paths.read_text())]
            worker.execute(["docker", "image", "rm", *images], worker.OUT / f"cleanup-{label}.log")
        worker.write_json(worker.OUT / "batches.json", batches)
        worker.write_json(worker.OUT / "summary.json", summarize(rows, reports, [r["instance_id"] for r in rows]))
    return 0 if len(reports) == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())

"""Runs inside the snapshot VM; loads only the frozen 24 baseline images."""
import argparse
import importlib.metadata
import json
import re
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, "/work")


def check_plan(plan):
    if len(plan) != 24 or len({row["instance_id"] for row in plan}) != 24:
        raise ValueError("Expected exactly 24 distinct baseline images")
    for row in plan:
        if not row["reference"].startswith("swebench/sweb.eval.x86_64."):
            raise ValueError("Unexpected benchmark registry reference")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", row["id"]):
            raise ValueError("Expected pinned Docker image ID")


def verify_local_images(plan, inspect):
    check_plan(plan)
    checked = []
    for row in plan:
        actual = inspect(row["reference"])
        if actual is None or actual["Id"] != row["id"]:
            raise RuntimeError("Restored Docker image missing or differs from baseline")
        checked.append({"instance_id": row["instance_id"], "reference": row["reference"], "id": actual["Id"]})
    return checked


def preload():
    from image_cache import ensure_image, inspect_image
    from registry_cache import RegistryCache
    plan = json.loads(Path("/work/image-plan.json").read_text())
    check_plan(plan)
    if subprocess.check_output(["docker", "ps", "-aq"]).strip():
        raise RuntimeError("Snapshot builder must contain no test containers")
    cache = RegistryCache("/image-cache", 256)
    out = Path("/work/preload")
    out.mkdir()
    started = time.monotonic()
    loaded = []
    for row in plan:
        if shutil.disk_usage("/var/lib/docker").free < 20 * 1024 ** 3:
            raise RuntimeError("Insufficient VM disk headroom")
        saved = cache.get(row["reference"])
        if saved is None or saved["id"] != row["id"]:
            raise RuntimeError("Cache image does not match baseline image ID")
        record = ensure_image(row["reference"], cache, out / (row["instance_id"] + ".log"),
                              require_cached=True)
        if record["id"] != row["id"] or record["cache_added_bytes"] != 0:
            raise RuntimeError("Unexpected image mutation or registry access")
        loaded.append(dict(record, instance_id=row["instance_id"]))
        print("SNAPSHOT_PRELOAD=" + json.dumps({"completed": len(loaded), "requested": 24,
              "instance_id": row["instance_id"], "seconds": record["seconds"],
              "elapsed_seconds": time.monotonic() - started}), flush=True)
    if subprocess.check_output(["docker", "ps", "-aq"]).strip():
        raise RuntimeError("Snapshot builder unexpectedly contains test containers")
    manifest = {"schema": 1, "images": plan, "records": loaded,
                "no_test_containers": True, "predictions_present": Path("/work/preds.jsonl").exists(),
                "tests_run": False, "preload_seconds": time.monotonic() - started,
                "harness": importlib.metadata.version("swebench"),
                "docker_root": "/var/lib/docker", "disk_used_bytes": shutil.disk_usage("/").used}
    if manifest["predictions_present"]:
        raise RuntimeError("Predictions must not enter clean snapshot builder")
    Path("/snapshot-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return 0


def evaluate():
    import remote_worker as worker
    from core import summarize
    from image_cache import inspect_image
    if importlib.metadata.version("swebench") != "4.1.0":
        raise RuntimeError("Unexpected harness version")
    manifest = json.loads(Path("/snapshot-manifest.json").read_text())
    plan = manifest["images"]
    check_plan(plan)
    if manifest.get("tests_run") or manifest.get("predictions_present"):
        raise RuntimeError("Snapshot is not a clean pre-test environment")
    rows = [json.loads(line) for line in Path("/work/preds.jsonl").read_text().splitlines()]
    data = json.loads(Path("/work/pilot.json").read_text())
    expected = {row["instance_id"] for row in plan}
    if (len(rows) != 24 or len(data) != 24 or {row["instance_id"] for row in rows} != expected
            or {row["instance_id"] for row in data} != expected):
        raise ValueError("Restored VM may evaluate only the frozen 24 instances")
    if subprocess.check_output(["docker", "ps", "-aq"]).strip():
        raise RuntimeError("Restored snapshot contains old test containers")
    checked = verify_local_images(plan, inspect_image)
    worker.OUT.mkdir()
    worker.write_json(worker.OUT / "restored-images.json", checked)
    worker.write_json(worker.OUT / "environment.json", {
        "harness": importlib.metadata.version("swebench"), "python": sys.version,
        "workers": 8, "dataset_count": 24, "mock_gold_only": True,
        "image_cache_volume_attached": False, "registry_secrets_injected": False,
        "no_preexisting_test_containers": True})
    # cache=None + require_cached=True forbids fallback to load or registry pull.
    config = SimpleNamespace(workers=8, test_timeout=600, mode="smoke", cache=None, require_cached=True)
    reports = {}
    batches = []
    for start in range(0, len(rows), 8):
        batch = rows[start:start + 8]
        ids = [row["instance_id"] for row in batch]
        label = f"batch-{start // 8:02d}"
        started = time.monotonic()
        result = worker.evaluate(batch, ids, Path("/work/pilot.json"), label, config)
        batches.append({"label": label, "seconds": time.monotonic() - started, "summary": result})
        for path in (worker.OUT / label).glob("logs/**/report.json"):
            reports.update(json.loads(path.read_text()))
        images = [row["reference"] for row in json.loads((worker.OUT / label / "images.json").read_text())]
        worker.execute(["docker", "image", "rm", *images], worker.OUT / f"cleanup-{label}.log")
        worker.write_json(worker.OUT / "batches.json", batches)
        worker.write_json(worker.OUT / "summary.json", summarize(rows, reports, [r["instance_id"] for r in rows]))
    return 0 if len(reports) == 24 and all(r.get("resolved") for r in reports.values()) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["preload", "evaluate"])
    args = parser.parse_args()
    raise SystemExit(preload() if args.action == "preload" else evaluate())

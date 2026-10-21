"""Runs inside the snapshot VM; loads only the frozen 24 baseline images."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

sys.path.insert(0, "/work")


def check_plan(plan):
    if len(plan) != 24 or len({row["instance_id"] for row in plan}) != 24:
        raise ValueError("Expected exactly 24 distinct baseline images")
    for row in plan:
        if not row["reference"].startswith("swebench/sweb.eval.x86_64."):
            raise ValueError("Unexpected benchmark registry reference")


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


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["preload"])
    args = parser.parse_args()
    raise SystemExit(preload())

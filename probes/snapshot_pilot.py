"""Remote-only, bounded filesystem-snapshot qualification for the frozen pilot.

No production deployment, registry secrets, or full-500 grading is performed.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


RUNTIME = "im-xE1XGZwwpKZrzcYW0wqjEI"
RATE = 2 * .00003942 + 8 * .00000667


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, indent=2) + "\n").encode())


def guard(source, label, estimate, status=None, app_id=None):
    command = [sys.executable, str(source / "web_budget.py"),
               "guard" if status is None else "record-run", "--profile", "phamvanvuhoan"]
    if status is None:
        command += ["--estimate", str(estimate)]
    else:
        command += ["--run-id", label, "--status", status, "--estimated-cost", str(estimate)]
        if app_id:
            command += ["--app-id", app_id]
    subprocess.run(command, check=True)


def execute(sandbox, *command, timeout=120):
    process = sandbox.exec(*command, timeout=timeout)
    stdout = process.stdout.read()
    stderr = process.stderr.read()
    process.wait()
    if process.returncode:
        raise RuntimeError(f"Remote command {command[0]} failed: " + (stderr or stdout)[-2000:])
    return stdout


def canary(out, source):
    import modal
    estimate = .10
    label = out.name + "-canary"
    guard(source, label, estimate)
    guard(source, label, estimate, "planned")
    app = modal.App("swebench-snapshot-canary")
    sandboxes = []
    receipt = {"started_at": datetime.now(timezone.utc).isoformat(), "profile": "phamvanvuhoan",
               "runtime_image_id": RUNTIME, "runtime": "vm", "cpu": 2, "memory_gib": 8,
               "lifetime_per_vm_seconds": 300, "max_vms": 2,
               "allocation_bound_usd": RATE * 300 * 2, "snapshot_ttl_seconds": 3600,
               "no_tests_run": True, "full_500_evaluated": False}
    started = time.monotonic()
    try:
        with modal.enable_output(), app.run():
            try:
                guard(source, label, estimate, "running", app.app_id)
                parent = modal.Sandbox.create("sleep", "infinity", app=app,
                    image=modal.Image.from_id(RUNTIME), runtime="vm", cpu=(2, 2),
                    memory=(8192, 8192), timeout=300)
                sandboxes.append(parent)
                receipt.update(app_id=app.app_id, parent_sandbox=parent.object_id)
                dump(out / "canary.json", receipt)
                execute(parent, "sh", "-c", "printf snapshot-probe > /snapshot-canary.txt; sync")
                snapshot_started = time.monotonic()
                image = parent.snapshot_filesystem(timeout=120, ttl=3600)
                receipt.update(snapshot_image_id=image.object_id,
                               snapshot_seconds=time.monotonic() - snapshot_started)
                dump(out / "canary.json", receipt)
                parent.terminate(wait=True)
                child = modal.Sandbox.create("sleep", "infinity", app=app, image=image,
                    runtime="vm", cpu=(2, 2), memory=(8192, 8192), timeout=300)
                sandboxes.append(child)
                receipt["child_sandbox"] = child.object_id
                value = execute(child, "cat", "/snapshot-canary.txt")
                if value != "snapshot-probe":
                    raise RuntimeError("Snapshot sentinel did not survive new VM")
                receipt["passed"] = True
            finally:
                for sandbox in sandboxes:
                    sandbox.terminate(wait=True)
                receipt["all_sandboxes_terminated"] = True
    except Exception as exc:
        receipt.update(passed=False, error=type(exc).__name__ + ": " + str(exc))
    finally:
        receipt.update(wall_seconds=time.monotonic() - started,
                       finished_at=datetime.now(timezone.utc).isoformat())
        dump(out / "canary.json", receipt)
        guard(source, label, estimate, "completed" if receipt.get("passed") else "failed")
    return 0 if receipt.get("passed") else 1


def validate_inputs(baseline):
    selected = json.loads((baseline / "selection.json").read_text())
    if selected.get("count") != 24 or not selected.get("gold_control_only"):
        raise ValueError("Only the frozen 24 gold controls are authorized")
    for name, key in [("pilot.json", "pilot_sha256"), ("preds.jsonl", "predictions_sha256")]:
        if hashlib.sha256((baseline / name).read_bytes()).hexdigest() != selected[key]:
            raise ValueError("Frozen pilot inputs changed: " + name)
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["canary"])
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    args = parser.parse_args()
    if os.environ.get("MODAL_PROFILE") != "phamvanvuhoan":
        raise ValueError("Only authorized profile phamvanvuhoan")
    import modal
    if modal.__version__ != "1.6.2":
        raise ValueError("Requires existing Modal SDK 1.6.2")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    return canary(out, args.source.resolve())


if __name__ == "__main__":
    raise SystemExit(main())

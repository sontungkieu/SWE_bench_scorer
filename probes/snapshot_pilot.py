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
import shutil
import sys
import time


RUNTIME = "im-xE1XGZwwpKZrzcYW0wqjEI"
RATE = 2 * .00003942 + 8 * .00000667
PILOT_SHA = "47b545950e6fc204b2d028573298f4ee65a9497c035012bfe9ffe709c91ddbbf"
PREDS_SHA = "aa914e991bbc328e9d1f8b8521e664cbe0c061b34fac60d94bfbdbd715fbc42f"


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
    if selected.get("pilot_sha256") != PILOT_SHA or selected.get("predictions_sha256") != PREDS_SHA:
        raise ValueError("Only the exact previous pilot inputs may be reused")
    for name, key in [("pilot.json", "pilot_sha256"), ("preds.jsonl", "predictions_sha256")]:
        if hashlib.sha256((baseline / name).read_bytes()).hexdigest() != selected[key]:
            raise ValueError("Frozen pilot inputs changed: " + name)
    return selected


def stream(sandbox, command, log, timeout):
    import modal
    process = sandbox.exec(*command, stderr=modal.stream_type.StreamType.STDOUT, timeout=timeout)
    with log.open("w") as output:
        for line in process.stdout:
            output.write(line)
            output.flush()
            print(line, end="", flush=True)
    process.wait()
    if process.returncode:
        raise RuntimeError(f"Snapshot worker exited {process.returncode}; see {log.name}")


def start_docker(sandbox):
    execute(sandbox, "sh", "-c", "nohup dockerd --host=unix:///var/run/docker.sock "
            "--storage-driver=overlay2 --pidfile=/run/snapshot-dockerd.pid "
            ">/tmp/snapshot-dockerd.log 2>&1 </dev/null &")
    execute(sandbox, "sh", "-c", "for i in $(seq 1 90); do docker info >/dev/null 2>&1 && exit 0; sleep 1; done; exit 1")


def prepare(out, source, baseline, canary_path):
    import fcntl
    import modal
    validate_inputs(baseline)
    canary_receipt = json.loads(canary_path.read_text())
    if not canary_receipt.get("passed") or not canary_receipt.get("all_sandboxes_terminated"):
        raise ValueError("VM filesystem snapshot canary must pass first")
    plan_by_id = {}
    for path in (baseline / "vm/output").glob("batch-*/images.json"):
        for row in json.loads(path.read_text()):
            plan_by_id[row["instance_id"]] = {key: row[key] for key in ("instance_id", "reference", "id", "repo_digests")}
    selected = json.loads((baseline / "selection.json").read_text())
    plan = [plan_by_id[row["instance_id"]] for row in selected["selected"]]
    if len(plan) != 24 or len(plan_by_id) != 24:
        raise ValueError("Baseline must have exactly 24 pinned image records")
    dump(out / "image-plan.json", plan)
    for name in ("selection.json", "pilot.json", "preds.jsonl"):
        shutil.copyfile(baseline / name, out / name)
    dump(out / "source-provenance.json", {"baseline": str(baseline), "runtime_image_id": RUNTIME,
         "source_sha256": {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
                           for name in ("core.py", "remote_worker.py", "image_cache.py", "registry_cache.py")},
         "probe_sha256": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                          for name in ("snapshot_pilot.py", "snapshot_worker.py")}})
    label = out.name + "-prepare"
    estimate = .30
    guard(source, label, estimate)
    guard(source, label, estimate, "planned")
    lock = (Path(os.environ["SWE_WEB_STATE"]) / "image-cache.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX)
    app = modal.App("swebench-snapshot-prepare")
    sandbox = None
    started = time.monotonic()
    receipt = {"started_at": datetime.now(timezone.utc).isoformat(), "runtime_image_id": RUNTIME,
               "cpu": 2, "memory_gib": 8, "lifetime_seconds": 1800,
               "allocation_bound_usd": RATE * 1800, "snapshot_ttl_seconds": 30 * 86400,
               "frozen_pilot_sha256": PILOT_SHA, "frozen_predictions_sha256": PREDS_SHA,
               "full_500_evaluated": False, "test_containers_created": False}
    try:
        with modal.enable_output(), app.run():
            try:
                guard(source, label, estimate, "running", app.app_id)
                sandbox = modal.Sandbox.create("sleep", "infinity", app=app,
                    image=modal.Image.from_id(RUNTIME), runtime="vm", cpu=(2, 2),
                    memory=(8192, 8192), timeout=1800,
                    volumes={"/image-cache": modal.Volume.from_name("swebench-verified-images-v1", version=2)})
                receipt.update(app_id=app.app_id, sandbox_id=sandbox.object_id)
                dump(out / "prepare-receipt.json", receipt)
                execute(sandbox, "mkdir", "-p", "/work")
                for name in ("core.py", "image_cache.py", "registry_cache.py"):
                    sandbox.filesystem.copy_from_local(str(source / name), "/work/" + name)
                sandbox.filesystem.copy_from_local(str(Path(__file__).with_name("snapshot_worker.py")), "/work/snapshot_worker.py")
                sandbox.filesystem.copy_from_local(str(out / "image-plan.json"), "/work/image-plan.json")
                start_docker(sandbox)
                stream(sandbox, ["python", "-u", "/work/snapshot_worker.py", "preload"], out / "preload.log", 1250)
                sandbox.filesystem.copy_to_local("/snapshot-manifest.json", str(out / "preload-manifest.json"))
                # Quiesce Docker before snapshot; PID 1 remains sleep, so the VM stays alive.
                execute(sandbox, "python", "-c", "import pathlib,os,signal,time; "
                        "p=int(pathlib.Path('/run/snapshot-dockerd.pid').read_text()); "
                        "assert p>1 and b'dockerd' in pathlib.Path(f'/proc/{p}/cmdline').read_bytes(); "
                        "os.kill(p,signal.SIGTERM); "
                        "\nfor _ in range(600):\n q=pathlib.Path(f'/proc/{p}/stat'); "
                        "\n if not q.exists() or q.read_text().split()[2]=='Z':break\n time.sleep(.1)"
                        "\nelse:raise RuntimeError('Docker did not stop cleanly')", timeout=75)
                execute(sandbox, "sh", "-c", "! docker info >/dev/null 2>&1 && sync")
                snapshot_started = time.monotonic()
                image = sandbox.snapshot_filesystem(timeout=450, ttl=30 * 86400)
                receipt.update(snapshot_image_id=image.object_id,
                               snapshot_seconds=time.monotonic() - snapshot_started, passed=True)
                dump(out / "prepare-receipt.json", receipt)
            finally:
                if sandbox is not None:
                    sandbox.terminate(wait=True)
                    receipt["sandbox_terminated"] = True
    except Exception as exc:
        receipt.update(passed=False, error=type(exc).__name__ + ": " + str(exc))
    finally:
        receipt.update(wall_seconds=time.monotonic() - started,
                       finished_at=datetime.now(timezone.utc).isoformat())
        dump(out / "prepare-receipt.json", receipt)
        guard(source, label, estimate, "completed" if receipt.get("passed") else "failed")
        lock.close()
    return 0 if receipt.get("passed") else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["canary", "prepare"])
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--canary", type=Path)
    args = parser.parse_args()
    if os.environ.get("MODAL_PROFILE") != "phamvanvuhoan":
        raise ValueError("Only authorized profile phamvanvuhoan")
    import modal
    if modal.__version__ != "1.6.2":
        raise ValueError("Requires existing Modal SDK 1.6.2")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    if args.action == "canary":
        return canary(out, args.source.resolve())
    if not args.baseline or not args.canary:
        parser.error("prepare requires --baseline and --canary")
    return prepare(out, args.source.resolve(), args.baseline.resolve(), args.canary.resolve())


if __name__ == "__main__":
    raise SystemExit(main())

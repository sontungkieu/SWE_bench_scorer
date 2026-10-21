"""Thin local Modal client. Installs/builds/runs Docker only on Modal."""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

from core import CLIENT, HARNESS, MOCK_ID, SCORER_VERSION, load_predictions
from image_cache import DEFAULT_VOLUME, MOUNT

HERE = Path(__file__).resolve().parent


def host_path(value):
    if os.name != "nt" and re.match(r"^[A-Za-z]:[\\/]", value):
        return Path("/mnt/" + value[0].lower() + "/" + value[3:].replace("\\", "/"))
    return Path(value).expanduser().resolve()


def extract_checked(archive, directory):
    with tarfile.open(archive, "r:gz") as stream:
        target = directory.resolve()
        for member in stream.getmembers():
            if not (target / member.name).resolve().is_relative_to(target):
                raise ValueError("Unsafe archive path")
            if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                raise ValueError("Unsupported archive entry")
        stream.extractall(directory, filter="data")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["validate", "mock", "smoke", "full", "cache"])
    parser.add_argument("--profile")
    parser.add_argument("--predictions")
    parser.add_argument("--instance-ids", nargs="+", default=[MOCK_ID])
    parser.add_argument("--run-id", default=datetime.now(timezone.utc).strftime("swe-%Y%m%dT%H%M%SZ"))
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--test-timeout", type=int, default=600)
    parser.add_argument("--lifetime", type=int, default=1800)
    parser.add_argument("--estimate-usd", type=float, default=0.50)
    parser.add_argument("--output-root", help="Persistent run directory (defaults to ./runs beside launcher)")
    parser.add_argument("--cache-volume", default=DEFAULT_VOLUME, help="Modal v2 Volume; 'none' disables cache")
    parser.add_argument("--cache-limit-gib", type=int, default=256)
    parser.add_argument("--require-cached", action="store_true", help="Fail on cache miss instead of pulling")
    parser.add_argument("--all-instances", action="store_true", help="Prewarm all 500 images (cache action only)")
    parser.add_argument("--registry-secret", help="Named Modal registry secret; cache action only")
    parser.add_argument("--guard-script", default="/mnt/c/Users/Tung/.codex/skills/modal-gpu-ops/scripts/modal_billing_guard.py")
    args = parser.parse_args()
    if args.action == "mock" and args.predictions:
        parser.error("Mock creates its own controls; do not supply --predictions")
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", args.run_id):
        parser.error("run-id must contain only letters, digits, _ and -")
    if args.action not in {"mock", "cache"}:
        if not args.predictions:
            parser.error("--predictions required")
        rows = load_predictions(host_path(args.predictions))
        print(json.dumps({"predictions": len(rows), "empty_patches": sum(not r["model_patch"].strip() for r in rows)}))
        if args.action == "validate":
            return 0
    if not args.profile:
        parser.error("--profile required for cloud runs")
    if not 1 <= args.workers <= 8 or not 60 <= args.test_timeout <= 3600 or not 300 <= args.lifetime <= 86400:
        parser.error("workers: 1..8; test-timeout: 60..3600; lifetime: 300..86400")
    if args.estimate_usd <= 0:
        parser.error("--estimate-usd must be positive")
    if not 1 <= args.cache_limit_gib <= 512:
        parser.error("cache-limit-gib: 1..512")
    if args.all_instances and args.action != "cache":
        parser.error("--all-instances requires cache action")
    if args.registry_secret and args.action != "cache":
        parser.error("Registry credentials may only be injected into cache-only VMs")
    if args.action == "cache" and (args.predictions or args.workers != 1 or args.cache_volume == "none"):
        parser.error("Cache action requires one worker, a Volume, and no predictions")
    # Conservative resource allocation bound at Modal's 2026-10-21 Sandbox rates.
    # Finite VM lifetime bounds CPU/RAM usage even if the client disconnects.
    resource_bound = args.lifetime * (2 * args.workers * 0.00003942 +
                                     (2 if args.action == "cache" else 8 * args.workers) * 0.00000667)
    if args.action == "cache" and resource_bound > args.estimate_usd:
        parser.error(f"Cache lifetime CPU/RAM bound ${resource_bound:.4f} exceeds estimate; shorten --lifetime")
    if args.action == "mock" and (args.workers != 1 or args.lifetime > 1800):
        parser.error("Mock is bounded to one worker and at most 1800 seconds")
    cache_lock = None
    if os.environ.get("SWE_WEB_STATE") and args.cache_volume != "none":
        import fcntl
        cache_lock = (Path(os.environ["SWE_WEB_STATE"]) / "image-cache.lock").open("a")
        print("Waiting for exclusive image-cache access (no VM billed while waiting)", flush=True)
        fcntl.flock(cache_lock, fcntl.LOCK_EX)
    guard = host_path(args.guard_script)
    if not guard.is_file():
        raise RuntimeError("Billing guard not found; supply --guard-script. No cloud job started.")
    # Use the explicitly selected profile in subprocesses; never activate another
    # profile globally or inject credentials into the evaluation sandbox.
    os.environ["MODAL_PROFILE"] = args.profile
    os.environ["UV_OFFLINE"] = "1"
    os.environ["PATH"] = str(Path.home() / ".local" / "bin") + os.pathsep + os.environ.get("PATH", "")
    command = [sys.executable, str(guard), "guard", "--profile", args.profile,
               "--estimate", str(args.estimate_usd), "--reserve", "0"]
    checked = subprocess.run(command)
    if checked.returncode:
        raise RuntimeError("Billing guard blocked launch; refresh billing or select an authorized profile")
    import modal
    if modal.__version__ != CLIENT:
        raise RuntimeError(f"Use cached Modal {CLIENT}; got {modal.__version__}")
    output = (host_path(args.output_root) if args.output_root else HERE / "runs") / args.run_id
    output.mkdir(parents=True, exist_ok=False)
    source = {name: hashlib.sha256((HERE / name).read_bytes()).hexdigest()
              for name in ("core.py", "remote_worker.py", "launch.py", "image_cache.py", "registry_cache.py")}
    receipt = {"scorer_version": SCORER_VERSION, "run_id": args.run_id, "mode": args.action, "profile": args.profile,
               "modal_client": CLIENT, "harness": HARNESS, "source_sha256": source,
               "lifetime_seconds": args.lifetime, "estimate_usd": args.estimate_usd,
               "estimate_is_hard_billing_cap": False,
               "started_at": datetime.now(timezone.utc).isoformat()}
    receipt.update(cache_volume=args.cache_volume, cache_limit_gib=args.cache_limit_gib,
                   require_cached=args.require_cached, cpu_memory_allocation_bound_usd=resource_bound,
                   allocation_bound_price_date="2026-10-21", registry_secret=args.registry_secret,
                   registry_auth="secret" if args.registry_secret else "anonymous")
    ledger = [sys.executable, str(guard), "record-run", "--profile", args.profile,
              "--run-id", args.run_id, "--label", "swebench-" + args.action,
              "--estimated-cost", str(args.estimate_usd), "--run-dir", str(output)]
    subprocess.run(ledger + ["--status", "planned"], check=True)
    image = (modal.Image.debian_slim(python_version="3.11")
             .apt_install("docker.io", "git")
             .pip_install(f"swebench=={HARNESS}", f"modal=={CLIENT}", "ghapi<2"))
    volumes = {} if args.cache_volume == "none" else {
        MOUNT: modal.Volume.from_name(args.cache_volume, create_if_missing=True, version=2)}
    memory_mib = 2048 if args.action == "cache" else 8192 * args.workers
    app = modal.App("swebench-modal-grader")
    sandbox = None
    exit_code = 1
    error = None
    try:
        with modal.enable_output(), app.run():
            try:
                entrypoint = (["sleep", "infinity"] if args.action == "cache" else
                              ["dockerd", "--host=unix:///var/run/docker.sock", "--storage-driver=overlay2"])
                probe = (["python", "--version"] if args.action == "cache" else ["docker", "info"])
                sandbox = modal.Sandbox.create(
                    *entrypoint, app=app, image=image,
                    runtime="vm", cpu=(2 * args.workers, 2 * args.workers),
                    memory=(memory_mib, memory_mib), volumes=volumes,
                    secrets=[modal.Secret.from_name(args.registry_secret)] if args.registry_secret else [],
                    timeout=args.lifetime,
                    readiness_probe=modal.Probe.with_exec(*probe, interval_ms=1000))
                receipt.update(app_id=app.app_id, sandbox_id=sandbox.object_id,
                               modal_image_id=image.object_id)
                (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
                subprocess.run(ledger + ["--status", "running", "--app-id", app.app_id], check=True)
                print("SWE_LAUNCH_JSON=" + json.dumps(receipt), flush=True)
                sandbox.wait_until_ready(timeout=120)
                process = sandbox.exec("mkdir", "-p", "/work")
                process.wait()
                if process.returncode:
                    raise RuntimeError("Cannot initialize /work")
                for name in ("core.py", "remote_worker.py", "image_cache.py", "registry_cache.py"):
                    sandbox.filesystem.copy_from_local(str(HERE / name), "/work/" + name)
                if args.predictions:
                    canonical = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
                    sandbox.filesystem.write_text(canonical, "/work/preds.jsonl")
                worker = ["python", "-u", "/work/remote_worker.py", "--mode", args.action,
                          "--workers", str(args.workers), "--test-timeout", str(args.test_timeout),
                          "--cache-root", "" if args.cache_volume == "none" else MOUNT,
                          "--cache-limit-gib", str(args.cache_limit_gib),
                          "--work-seconds", str(max(60, args.lifetime - 180)),
                          "--instance-ids", *args.instance_ids]
                if args.require_cached:
                    worker += ["--require-cached"]
                if args.all_instances:
                    worker += ["--all-instances"]
                process = sandbox.exec(*worker, stderr=modal.stream_type.StreamType.STDOUT,
                                       timeout=args.lifetime)
                with (output / "worker.log").open("w", encoding="utf-8") as log:
                    for line in process.stdout:
                        print(line, end="", flush=True)
                        log.write(line)
                        log.flush()
                process.wait()
                exit_code = process.returncode
            except BaseException as exc:
                error = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                if sandbox is not None:
                    try:
                        package = sandbox.exec("tar", "-czf", "/tmp/results.tar.gz", "-C", "/work", "output")
                        package.wait()
                        if package.returncode == 0:
                            archive = output / "results.tar.gz"
                            sandbox.filesystem.copy_to_local("/tmp/results.tar.gz", str(archive))
                            receipt["archive_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
                            extract_checked(archive, output)
                        else:
                            receipt["artifact_error"] = package.stderr.read()
                    except Exception as exc:
                        receipt["artifact_error"] = f"{type(exc).__name__}: {exc}"
                    finally:
                        sandbox.terminate()
                        receipt["sandbox_terminated"] = True
    finally:
        receipt.update(exit_code=exit_code, error=error,
                       finished_at=datetime.now(timezone.utc).isoformat())
        (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        subprocess.run(ledger + ["--status", "completed" if exit_code == 0 and error is None else "failed"], check=False)
        print("LOCAL_RESULTS=" + str(output), flush=True)
    # Missing artifacts must never be reported as a passing evaluation.
    if "archive_sha256" not in receipt or receipt.get("artifact_error"):
        return 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

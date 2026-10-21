"""Finite GCP prewarm job with quota cooldown outside Modal.

Resumes committed images, stops on non-quota failures, and reserves CPU/RAM
allocation bounds before any new VM. No registry PAT is read by this process.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RATE = 2 * 0.00003942 + 2 * 0.00000667  # USD/second, 2026-10-21


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def decision(summary):
    if summary.get("complete") and summary.get("cached") == 500:
        return "complete"
    failures = summary.get("failures", [])
    if failures and all("HTTP Error 429" in row.get("error", "") for row in failures):
        return "cooldown"
    # The prewarm worker exits cleanly at its finite work boundary, writes a
    # partial summary with no failures, and commits completed images.
    if not failures and summary.get("requested") == 500 and 0 < summary.get("cached", 0) < 500:
        return "resume"
    return "stop"


def progress(run):
    path = run / "worker.log"
    if not path.exists():
        return {}
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - 32768))
        tail = stream.read().decode("utf-8", errors="replace")
    for line in reversed(tail.splitlines()):
        if line.startswith("SWE_CACHE_PROGRESS="):
            try:
                row = json.loads(line.split("=", 1)[1])
                return {"cached_count": row["completed"], "requested": row["requested"],
                        "storage_bytes": row["cache_total_bytes"]}
            except (ValueError, KeyError):
                continue
    return {}


def acquire_lock(root):
    import fcntl
    handle = (root / "prewarm-queue.lock").open("a")
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return handle


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--registry-secret", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--adopt-run", help="Already launched run; wait for its VM to terminate")
    parser.add_argument("--run-prefix", required=True)
    parser.add_argument("--budget-usd", type=float, default=2)
    parser.add_argument("--prior-spend-usd", type=float, default=0.11)
    parser.add_argument("--egress-reserve-usd", type=float, default=0.05)
    parser.add_argument("--lifetime", type=int, default=1800)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--cooldown-seconds", type=int, default=21660,
                        help="Default 6h+60s; may use a currently verified provider window + margin")
    args = parser.parse_args(argv)
    if not 900 <= args.lifetime <= 3600 or not 1 <= args.max_attempts <= 10 or args.cooldown_seconds < 0:
        parser.error("Invalid finite run bounds")
    if args.budget_usd <= 0 or args.prior_spend_usd < 0 or args.egress_reserve_usd < 0:
        parser.error("Invalid budget")
    root = Path(args.state).resolve()
    root.mkdir(parents=True, exist_ok=True)
    coordinator_lock = acquire_lock(root)
    here = Path(__file__).resolve().parent
    state = {"status": "starting", "cached_count": 0, "requested": 500,
             "profile": args.profile, "budget_usd": args.budget_usd,
             "prior_spend_usd": args.prior_spend_usd, "allocation_reserved_usd": 0,
             "egress_reserve_usd": args.egress_reserve_usd, "attempts": 0,
             "cooldown_seconds": args.cooldown_seconds, "runs": []}

    def save(**update):
        state.update(update, updated_at=timestamp())
        target = root / "image-cache-status.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2) + "\n")
        os.replace(temporary, target)

    def monitor(run_id, process=None):
        run = root / "runs" / run_id
        deadline = time.monotonic() + args.lifetime + 300
        while time.monotonic() < deadline:
            save(status="warming", run_id=run_id, **progress(run))
            receipt_path = run / "receipt.json"
            if receipt_path.exists():
                try:
                    receipt = json.loads(receipt_path.read_text())
                except json.JSONDecodeError:
                    time.sleep(1)
                    continue
                if receipt.get("finished_at"):
                    if not receipt.get("sandbox_terminated"):
                        raise RuntimeError("Termination not confirmed; refusing another VM")
                    summary_path = run / "output/cache-summary.json"
                    if not summary_path.exists():
                        raise RuntimeError("No cache summary; refusing paid retry")
                    return json.loads(summary_path.read_text())
            if process is not None and process.poll() is not None:
                raise RuntimeError("Launcher ended without final receipt")
            time.sleep(5)
        raise RuntimeError("Final receipt deadline exceeded; refusing another VM")

    def after_run(summary, can_retry=True):
        save(cached_count=summary["cached"], storage_bytes=summary["storage_bytes"])
        action = decision(summary)
        if action == "complete":
            save(status="complete", next_retry_at=None)
            return True
        if action not in ("cooldown", "resume"):
            save(status="stopped", reason="Non-quota failure or invalid summary; no paid retry", next_retry_at=None)
            return True
        if not can_retry:
            save(status="attempt_limit", next_retry_at=None)
            return True
        if args.prior_spend_usd + args.egress_reserve_usd + state["allocation_reserved_usd"] + RATE * args.lifetime > args.budget_usd:
            save(status="budget_exhausted", next_retry_at=None)
            return True
        if action == "resume":
            save(status="resuming", next_retry_at=None, reason="Finite work boundary; completed images committed")
            return False
        retry_at = time.time() + args.cooldown_seconds
        save(status="cooldown", next_retry_at=datetime.fromtimestamp(retry_at, timezone.utc).isoformat(),
             reason="Docker Hub quota; no Modal VM held during cooldown")
        while time.time() < retry_at:
            time.sleep(min(30, retry_at - time.time()))
        return False

    try:
        save()
        if args.adopt_run:
            state["allocation_reserved_usd"] += RATE * args.lifetime
            state["runs"].append(args.adopt_run)
            if after_run(monitor(args.adopt_run)):
                return 0 if state["status"] == "complete" else 1
        for number in range(1, args.max_attempts + 1):
            bound = RATE * args.lifetime
            projected = args.prior_spend_usd + args.egress_reserve_usd + state["allocation_reserved_usd"] + bound
            if projected > args.budget_usd:
                save(status="budget_exhausted", next_retry_at=None)
                return 1
            run_id = args.run_prefix + "-" + str(number)
            if (root / "runs" / run_id).exists():
                raise RuntimeError("Run ID already exists; refusing duplicate paid launch")
            state["allocation_reserved_usd"] += bound
            state["runs"].append(run_id)
            save(attempts=number)
            command = [sys.executable, str(here / "launch.py"), "cache", "--all-instances",
                       "--profile", args.profile, "--registry-secret", args.registry_secret,
                       "--run-id", run_id, "--lifetime", str(args.lifetime), "--estimate-usd", str(round(bound + 0.03, 6)),
                       "--guard-script", str(here / "web_budget.py"), "--output-root", str(root / "runs")]
            logs = root / "logs"
            logs.mkdir(exist_ok=True)
            with (logs / (run_id + ".log")).open("w") as log:
                process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, cwd=here)
                summary = monitor(run_id, process)
                process.wait(timeout=60)
            if after_run(summary, can_retry=number < args.max_attempts):
                return 0 if state["status"] == "complete" else 1
        save(status="attempt_limit", next_retry_at=None)
        return 1
    except Exception as exc:
        save(status="stopped", reason=type(exc).__name__ + ": " + str(exc), next_retry_at=None)
        return 1


if __name__ == "__main__":
    sys.exit(main())

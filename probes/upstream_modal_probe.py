"""Probe the SWE-bench Modal CLI from an isolated remote environment.

Credentials stay in the coordinator's existing scoped Modal configuration.
Optional compatibility changes apply only to a copied package. The installed
harness is never modified and this probe never starts local Docker.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def control_passed(label, expected, instance_id, exit_code, timed_out, report, aggregate):
    if exit_code != 0 or timed_out or aggregate.get("error_instances") != 0:
        return False
    if label == "empty":
        return (instance_id in aggregate.get("empty_patch_ids", [])
                and aggregate.get("resolved_instances") == 0
                and aggregate.get("completed_instances") == 0)
    return (aggregate.get("completed_instances") == 1 and report is not None
            and report.get("resolved") is expected)


def account_skipped_empty(rows, console):
    """Account validated empty inputs when upstream returns before reporting.

    This is probe accounting, never presented as an official harness report.
    A zero exit code alone is insufficient to enter this path.
    """
    if (not rows or any(row["model_patch"].strip() for row in rows)
            or "No instances to run." not in console.splitlines()
            or "Traceback" in console or "Error:" in console):
        return {}
    return {"error_instances": 0, "completed_instances": 0, "resolved_instances": 0,
            "empty_patch_instances": len(rows), "empty_patch_ids": [row["instance_id"] for row in rows],
            "accounting_origin": "probe accounting for official CLI empty-patch early return",
            "official_report_written": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--compat", action="store_true", help="Use audited SDK compatibility overlay")
    args = parser.parse_args()
    if os.environ.get("MODAL_PROFILE") != "phamvanvuhoan":
        raise RuntimeError("Probe requires explicitly selected phamvanvuhoan profile")
    if importlib.metadata.version("swebench") != "4.1.0":
        raise RuntimeError("Probe requires swebench==4.1.0")
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    summary = {"profile": "phamvanvuhoan", "backend": "official --modal true",
               "started_at": datetime.now(timezone.utc).isoformat(),
               "harness_modified": args.compat, "installed_harness_modified": False,
               "controls": [], "full_500_evaluated": False}
    write_json(out / "summary.json", summary)
    write_json(out / "packages.json", dict(sorted(
        (dist.metadata["Name"], dist.version) for dist in importlib.metadata.distributions())))
    manifest = json.loads((args.fixtures / "manifest.json").read_text())
    for control in manifest["controls"]:
        content = (args.fixtures / control["predictions"]).read_bytes()
        if hashlib.sha256(content).hexdigest() != control["sha256"]:
            raise RuntimeError("Fixture checksum mismatch")
    # Pin the same dataset revision as the existing Docker-backed scorer.
    from datasets import load_dataset
    data = list(load_dataset(manifest["dataset"], revision=manifest["dataset_revision"], split="test"))
    instance = next(row for row in data if row["instance_id"] == manifest["instance_id"])
    gold = json.loads((args.fixtures / "gold/preds.jsonl").read_text())
    if instance["patch"] != gold["model_patch"] or len(data) != 500:
        raise RuntimeError("Pinned dataset and fixture do not match")
    dataset = out / "verified.json"
    write_json(dataset, data)
    summary["dataset"] = {"name": manifest["dataset"], "revision": manifest["dataset_revision"],
                          "count": len(data), "sha256": hashlib.sha256(dataset.read_bytes()).hexdigest()}
    import swebench.harness.modal_eval.run_evaluation_modal as backend
    import swebench.harness.test_spec.test_spec as spec
    for module in (backend, spec):
        source = Path(module.__file__)
        (out / source.name).write_bytes(source.read_bytes())
    overlay = None
    if args.compat:
        from modal_sdk_compat import prepare_overlay
        import swebench
        overlay = prepare_overlay(Path(swebench.__file__).parent, out)
        summary["backend"] = "official --modal true with audited SDK compatibility overlay"
        summary["compatibility"] = json.loads((out / "compat-manifest.json").read_text())
    summary["coordinator_python"] = sys.version
    for control in manifest["controls"]:
        label = control["control"]
        case = out / label
        case.mkdir()
        predictions = case / "preds.jsonl"
        predictions.write_bytes((args.fixtures / control["predictions"]).read_bytes())
        command = [sys.executable, "-u", "-m", "swebench.harness.run_evaluation",
                   "--dataset_name", str(dataset), "--split", "test",
                   "--predictions_path", str(predictions),
                   "--instance_ids", manifest["instance_id"], "--max_workers", "1",
                   "--run_id", out.name + "-" + label, "--timeout", "600", "--modal", "true"]
        write_json(case / "command.json", command)
        started = time.monotonic()
        timed_out = False
        print("PROBE_START=" + json.dumps({"control": label, "command": command}), flush=True)
        # Upstream 4.1.0 checks ~/.modal.toml even when the SDK uses
        # MODAL_CONFIG_PATH. Use a private process HOME with a temporary link
        # to the existing scoped config; do not copy or rewrite credentials.
        config = Path(os.environ["MODAL_CONFIG_PATH"]).resolve(strict=True)
        with tempfile.TemporaryDirectory(prefix="coordinator-home-", dir=case) as home:
            (Path(home) / ".modal.toml").symlink_to(config)
            env = dict(os.environ, HOME=home)
            if overlay is not None:
                env["PYTHONPATH"] = str(overlay) + os.pathsep + env.get("PYTHONPATH", "")
            with (case / "console.log").open("w", encoding="utf-8") as stream:
                process = subprocess.Popen(command, cwd=case, stdout=stream, env=env,
                                           stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    process.wait(timeout=900)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    os.killpg(process.pid, signal.SIGINT)
                    try:
                        process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
        console = (case / "console.log").read_text()
        app_ids = sorted(set(re.findall(r"\bap-[A-Za-z0-9]+", console)))
        result = {"control": label, "expected_resolved": control["expected_resolved"],
                  "exit_code": process.returncode, "timed_out": timed_out,
                  "credential_path_compatibility": "temporary private HOME symlink to scoped config",
                  "wall_seconds": time.monotonic() - started, "app_ids": app_ids}
        # Explicitly stop every app created by this case, including failed builds.
        cleanup = []
        for app_id in app_ids:
            stopped = subprocess.run([sys.executable, "-m", "modal", "app", "stop", app_id],
                                     capture_output=True, text=True, timeout=60)
            (case / (app_id + "-stop.log")).write_text(stopped.stdout + stopped.stderr)
            cleanup.append({"app_id": app_id, "stop_exit_code": stopped.returncode})
            import modal
            remaining = list(modal.Sandbox.list(app_id=app_id))
            for sandbox in remaining:
                sandbox.terminate(wait=True)
            cleanup[-1]["remaining_sandboxes_after_cleanup"] = [
                sandbox.object_id for sandbox in modal.Sandbox.list(app_id=app_id)]
        result["cleanup"] = cleanup
        reports = {}
        for path in case.glob("logs/**/report.json"):
            try:
                reports.update(json.loads(path.read_text()))
            except json.JSONDecodeError:
                pass
        result["report"] = reports.get(manifest["instance_id"])
        aggregate = {}
        for path in case.glob("*.json"):
            value = json.loads(path.read_text())
            if isinstance(value, dict) and "error_instances" in value:
                aggregate = value
                break
        result["aggregate_origin"] = "official harness" if aggregate else None
        if not aggregate and label == "empty" and process.returncode == 0 and not timed_out:
            aggregate = account_skipped_empty([json.loads(line) for line in predictions.read_text().splitlines()], console)
            if aggregate:
                result["aggregate_origin"] = "probe empty-patch accounting"
                write_json(case / "empty-accounting.json", aggregate)
        result["aggregate"] = aggregate
        result["passed"] = control_passed(label, control["expected_resolved"],
                                          manifest["instance_id"], process.returncode,
                                          timed_out, result["report"], aggregate)
        result["passed"] = result["passed"] and all(
            not item["remaining_sandboxes_after_cleanup"] for item in cleanup)
        summary["controls"].append(result)
        summary["finished_at"] = datetime.now(timezone.utc).isoformat()
        summary["passed"] = len(summary["controls"]) == 3 and all(c["passed"] for c in summary["controls"])
        write_json(out / "summary.json", summary)
        print("PROBE_RESULT=" + json.dumps(result), flush=True)
        if not result["passed"]:
            summary["stopped_after_failure"] = label
            write_json(out / "summary.json", summary)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Bounded multi-repository comparison of native Modal and production Docker VM.

Run only on the existing remote coordinator with scoped credentials. Gold
patches are infrastructure controls, never model predictions or a full score.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tarfile
import tempfile
import time

REVISION = "78f471bf655a3137b2e8a75af1501690ec009ec3"
MODEL = "PILOT-GOLD-NOT-MODEL"


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, indent=2)+"\n").encode())


def select_instances(data, count=24, seed="20261022"):
    groups = {}
    for row in data:
        groups.setdefault(row["repo"], []).append(row)
    for repo in groups:
        groups[repo].sort(key=lambda row: hashlib.sha256((seed+":"+row["instance_id"]).encode()).hexdigest())
    selected = [row for repo in sorted(groups) for row in groups[repo][:2]]
    for repo in sorted(groups, key=lambda repo: (-len(groups[repo]), repo)):
        for row in groups[repo][2:]:
            if len(selected) >= count:
                break
            selected.append(row)
        if len(selected) >= count:
            break
    if len(selected) != count or not 20 <= count <= 30:
        raise ValueError("Pilot size must be 20..30 and cover each available repository")
    return selected


def prepare(out, dataset):
    data = json.loads(dataset.read_text())
    if len(data) != 500:
        raise ValueError("Expected pinned 500-instance source dataset")
    selected = select_instances(data)
    out.mkdir(parents=True, exist_ok=False)
    dump(out/"pilot.json", selected)
    predictions = [{"instance_id": row["instance_id"], "model_name_or_path": MODEL,
                    "model_patch": row["patch"]} for row in selected]
    (out/"preds.jsonl").write_bytes("".join(json.dumps(row)+"\n" for row in predictions).encode())
    dump(out/"selection.json", {"count": len(selected), "dataset_revision": REVISION,
         "full_500_evaluated": False, "gold_control_only": True, "seed": "20261022",
         "selection_rule": "two SHA256-ranked instances per repo; remaining slot from largest repo",
         "population_counts": dict(Counter(row["repo"] for row in data)),
         "sample_counts": dict(Counter(row["repo"] for row in selected)),
         "selected": [{"instance_id":r["instance_id"], "repo":r["repo"]} for r in selected],
         "source_dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
         "pilot_sha256": hashlib.sha256((out/"pilot.json").read_bytes()).hexdigest(),
         "predictions_sha256": hashlib.sha256((out/"preds.jsonl").read_bytes()).hexdigest()})


def budget(source, action, run_id, estimate, app_id=None, status=None):
    command = [sys.executable, str(source/"web_budget.py"), action,
               "--profile", "phamvanvuhoan"]
    if action == "guard":
        command += ["--estimate", str(estimate)]
    else:
        command += ["--run-id", run_id, "--status", status, "--estimated-cost", str(estimate)]
        if app_id:
            command += ["--app-id", app_id]
    subprocess.run(command, check=True)


def cleanup_apps(ids, out):
    import modal
    result = []
    for app_id in sorted(set(ids)):
        stopped = subprocess.run([sys.executable, "-m", "modal", "app", "stop", app_id],
                                 capture_output=True, text=True, timeout=45)
        sandboxes = list(modal.Sandbox.list(app_id=app_id))
        for sandbox in sandboxes:
            sandbox.terminate(wait=True)
        result.append({"app_id":app_id, "stop_code":stopped.returncode,
                       "remaining_sandboxes":[s.object_id for s in modal.Sandbox.list(app_id=app_id)]})
    dump(out/"cleanup.json", result)


def native(out, source):
    import swebench
    from modal_sdk_compat import prepare_overlay
    case = out/"native"
    case.mkdir()
    budget(source, "guard", out.name+"-native", 3)
    budget(source, "record-run", out.name+"-native", 3, status="planned")
    overlay = prepare_overlay(Path(swebench.__file__).parent, case)
    backend = overlay/"swebench/harness/modal_eval/run_evaluation_modal.py"
    text = backend.read_text()
    before_policy = text
    changes = [
        ("include_source=True,", "include_source=True,\n    max_containers=4,"),
        ("stop_after_attempt(7)", "stop_after_attempt(1)"),
        ("timeout=120\n    * 60,", "timeout=1200,"),
        ("            cpu=4,", "            cpu=(4, 4),\n            memory=(1024, 8192),"),
        ("        self.verbose = verbose", '        print("PILOT_SANDBOX=" + json.dumps({"instance_id":test_spec.instance_id,"sandbox_id":self.sandbox.object_id,"created_at":time.time()}), flush=True)\n        self.verbose = verbose'),
        ("        runner.sandbox.terminate(wait=True)", '        runner.sandbox.terminate(wait=True)\n        print("PILOT_SANDBOX_STOP=" + json.dumps({"instance_id":test_spec.instance_id,"stopped_at":time.time()}), flush=True)'),
    ]
    for old,new in changes:
        if text.count(old) != 1:
            raise RuntimeError("Unexpected source while applying pilot resource policy")
        text = text.replace(old,new,1)
    backend.write_bytes(text.encode())
    (case/"pilot-policy.patch").write_text("".join(difflib.unified_diff(
        before_policy.splitlines(keepends=True),text.splitlines(keepends=True),
        fromfile="compat/backend.py",tofile="pilot/backend.py")))
    dump(case/"pilot-policy.json", {"max_containers":4,"cpu_request_limit":[4,4],
         "memory_request_limit_mib":[1024,8192],"function_timeout_seconds":1200,
         "sandbox_timeout_seconds":600,"coordinator_timeout_seconds":1800,"sandbox_create_attempts":1,
         "backend_sha256":hashlib.sha256(backend.read_bytes()).hexdigest()})
    ids = [row["instance_id"] for row in json.loads((out/"pilot.json").read_text())]
    command = [sys.executable,"-u","-m","swebench.harness.run_evaluation",
               "--dataset_name",str(out/"pilot.json"),"--split","test",
               "--predictions_path",str(out/"preds.jsonl"),"--instance_ids",*ids,
               "--max_workers","4","--run_id",out.name+"-native","--timeout","600","--modal","true"]
    dump(case/"command.json",command)
    start = time.monotonic()
    timed_out = False
    app_ids = []
    try:
        with tempfile.TemporaryDirectory(prefix="coordinator-home-",dir=case) as home:
            (Path(home)/".modal.toml").symlink_to(Path(os.environ["MODAL_CONFIG_PATH"]).resolve())
            env = dict(os.environ, HOME=home, PYTHONPATH=str(overlay))
            with (case/"console.log").open("w") as log:
                process = subprocess.Popen(command,cwd=case,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                try:
                    process.wait(timeout=1800)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    os.killpg(process.pid,signal.SIGINT)
                    try:
                        process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid,signal.SIGKILL)
                        process.wait()
        app_ids = re.findall(r"\bap-[A-Za-z0-9]+",(case/"console.log").read_text())
        reports = {}
        for path in case.glob("logs/**/report.json"):
            try:
                reports.update(json.loads(path.read_text()))
            except json.JSONDecodeError:
                pass
        dump(case/"summary.json", {"wall_seconds":time.monotonic()-start,"exit_code":process.returncode,
             "timed_out":timed_out,"app_ids":sorted(set(app_ids)),"selected_count":len(ids),
             "reports":len(reports),"resolved_count":sum(r.get("resolved") is True for r in reports.values()),
             "missing_ids":sorted(set(ids)-reports.keys()),"full_500_evaluated":False})
    finally:
        if (case/"console.log").exists():
            app_ids += re.findall(r"\bap-[A-Za-z0-9]+",(case/"console.log").read_text())
        cleanup_apps(app_ids,case)
        budget(source,"record-run",out.name+"-native",3,status="completed")


def vm(out, source):
    import modal
    case=out/"vm"
    case.mkdir()
    budget(source,"guard",out.name+"-vm",2)
    budget(source,"record-run",out.name+"-vm",2,status="planned")
    import fcntl
    lock=(Path(os.environ["SWE_WEB_STATE"])/"image-cache.lock").open("a")
    fcntl.flock(lock,fcntl.LOCK_EX)
    image=(modal.Image.debian_slim(python_version="3.11").apt_install("docker.io","git")
           .pip_install("swebench==4.1.0","modal==1.6.2","ghapi<2"))
    app=modal.App("swebench-cost-pilot-vm")
    sandbox=None
    receipt={"backend":"production Docker evaluator in VM, pilot batch policy","workers":8,
             "cpu":16,"memory_gib":64,"lifetime_seconds":1800,"require_cached":True,
             "full_500_evaluated":False,"started_at":datetime.now(timezone.utc).isoformat()}
    started=time.monotonic()
    try:
        with modal.enable_output(),app.run():
            try:
                sandbox=modal.Sandbox.create("dockerd","--host=unix:///var/run/docker.sock","--storage-driver=overlay2",
                    app=app,image=image,runtime="vm",cpu=(16,16),memory=(65536,65536),timeout=1800,
                    volumes={"/image-cache":modal.Volume.from_name("swebench-verified-images-v1",version=2)},
                    readiness_probe=modal.Probe.with_exec("docker","info",interval_ms=1000))
                receipt.update(app_id=app.app_id,sandbox_id=sandbox.object_id,image_id=image.object_id)
                dump(case/"receipt.json",receipt)
                sandbox.wait_until_ready(timeout=120)
                p=sandbox.exec("mkdir","-p","/work");p.wait()
                for name in ["core.py","remote_worker.py","image_cache.py","registry_cache.py"]:
                    sandbox.filesystem.copy_from_local(str(source/name),"/work/"+name)
                sandbox.filesystem.copy_from_local(str(Path(__file__).with_name("cost_pilot_vm.py")),"/work/pilot_vm.py")
                for name in ["pilot.json","preds.jsonl"]:
                    sandbox.filesystem.copy_from_local(str(out/name),"/work/"+name)
                p=sandbox.exec("python","-u","/work/pilot_vm.py",stderr=modal.stream_type.StreamType.STDOUT,timeout=1700)
                with (case/"console.log").open("w") as log:
                    for line in p.stdout:
                        log.write(line);log.flush()
                p.wait();receipt["exit_code"]=p.returncode
            finally:
                if sandbox is not None:
                    try:
                        package=sandbox.exec("tar","-czf","/tmp/pilot.tar.gz","-C","/work","output")
                        package.wait()
                        if package.returncode==0:
                            sandbox.filesystem.copy_to_local("/tmp/pilot.tar.gz",str(case/"artifacts.tar.gz"))
                            with tarfile.open(case/"artifacts.tar.gz") as archive:
                                archive.extractall(case,filter="data")
                    finally:
                        sandbox.terminate(wait=True);receipt["sandbox_terminated"]=True
    except Exception as exc:
        receipt["error"]=type(exc).__name__+": "+str(exc)
    finally:
        receipt["wall_seconds"]=time.monotonic()-started
        dump(case/"receipt.json",receipt)
        cleanup_apps([app.app_id] if app.app_id else [],case)
        budget(source,"record-run",out.name+"-vm",2,status="completed")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=["prepare","native","vm"])
    parser.add_argument("--out",type=Path,required=True)
    parser.add_argument("--source",type=Path)
    parser.add_argument("--dataset",type=Path)
    args=parser.parse_args()
    if os.environ.get("MODAL_PROFILE")!="phamvanvuhoan":
        raise ValueError("Only the authorized phamvanvuhoan profile may run this pilot")
    out=args.out.resolve()
    if args.action=="prepare":
        prepare(out,args.dataset)
    else:
        selected=json.loads((out/"selection.json").read_text())
        if selected["count"]!=24 or not selected["gold_control_only"]:
            raise ValueError("Only the frozen 24-instance gold pilot is authorized")
        if hashlib.sha256((out/"preds.jsonl").read_bytes()).hexdigest()!=selected["predictions_sha256"]:
            raise ValueError("Predictions changed after pilot freeze")
        if hashlib.sha256((out/"pilot.json").read_bytes()).hexdigest()!=selected["pilot_sha256"]:
            raise ValueError("Pilot dataset changed after selection freeze")
        globals()[args.action](out,args.source.resolve())


if __name__=="__main__":
    main()

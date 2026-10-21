"""Bounded WSL-only migration of an existing Modal Docker Hub secret.

The source PAT never enters logs or result storage as plaintext: the source
Sandbox returns only RSA-OAEP ciphertext for an ephemeral client key. The
client decrypts in memory and writes directly to the destination Modal Secret.
An expired original credential file is not used or modified.
"""
import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REMOTE_PROBE = r'''
import base64,json,os,subprocess,urllib.request,urllib.parse
result = {"authenticated": False}
try:
    username=os.environ["REGISTRY_USERNAME"]
    password=os.environ["REGISTRY_PASSWORD"]
    repository="swebench/sweb.eval.x86_64.django_1776_django-13158"
    query=urllib.parse.urlencode({"service":"registry.docker.io","scope":f"repository:{repository}:pull"})
    basic=base64.b64encode(f"{username}:{password}".encode()).decode()
    request=urllib.request.Request("https://auth.docker.io/token?"+query,headers={"Authorization":"Basic "+basic})
    with urllib.request.urlopen(request,timeout=12) as r: token=json.load(r)["token"]
    claims=json.loads(base64.urlsafe_b64decode(token.split(".")[1]+"==="))
    if claims.get("sub")!=username: raise ValueError("Unexpected registry subject")
    request=urllib.request.Request(f"https://registry-1.docker.io/v2/{repository}/manifests/latest",method="HEAD",headers={"Authorization":"Bearer "+token,"Accept":"application/vnd.oci.image.index.v1+json"})
    with urllib.request.urlopen(request,timeout=12) as r:
        result["rate_limit_headers"]={k.lower():v for k,v in r.headers.items() if "ratelimit" in k.lower() and "source" not in k.lower()}
    result["authenticated"]=True
    payload=json.dumps({"REGISTRY_USERNAME":username,"REGISTRY_PASSWORD":password}).encode()
    if len(payload)>318: raise ValueError("Credential payload too large for RSA-3072 OAEP")
    encrypted=subprocess.run(["openssl","pkeyutl","-encrypt","-pubin","-inkey","/tmp/export-public.pem","-pkeyopt","rsa_padding_mode:oaep","-pkeyopt","rsa_oaep_md:sha256","-pkeyopt","rsa_mgf1_md:sha256"],input=payload,capture_output=True,timeout=5)
    if encrypted.returncode: raise RuntimeError("Encryption failed")
    result["sealed"]=base64.b64encode(encrypted.stdout).decode()
except Exception as exc:
    result["error_type"]=type(exc).__name__
    result["http_status"]=getattr(exc,"code",None)
print(json.dumps(result))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-profile", required=True)
    parser.add_argument("--source-secret", required=True)
    parser.add_argument("--target-profile", required=True)
    parser.add_argument("--target-secret", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--guard-script", required=True)
    parser.add_argument("--allow-over-budget-probe", action="store_true",
                        help="Only after explicit user authorization for this <=60s, $0.01 probe")
    args = parser.parse_args()
    if os.name == "nt" or not shutil.which("openssl"):
        raise RuntimeError("Use the existing WSL Modal runtime with OpenSSL")
    guard = [sys.executable, str(Path(args.guard_script).resolve())]
    checked = subprocess.run(guard + ["guard", "--profile", args.source_profile,
                                     "--estimate", "0.01", "--reserve", "0", "--refresh"], capture_output=True, text=True)
    status = json.loads(checked.stdout)
    if status["status"] == "UNKNOWN" or (checked.returncode and not
            (status["status"] == "BLOCK" and args.allow_over_budget_probe)):
        print(json.dumps({"status": status["status"], "probe_started": False}))
        return 2
    import modal
    from modal.config import Config
    config = Config()
    def client(profile):
        return modal.Client.from_credentials(config.get("token_id", profile=profile, use_env=False),
                                             config.get("token_secret", profile=profile, use_env=False))
    source_client, target_client = client(args.source_profile), client(args.target_profile)
    if any(s.name == args.target_secret for s in modal.Secret.objects.list(client=target_client)):
        raise RuntimeError("Target secret already exists; refusing overwrite")
    output = Path(__file__).resolve().parent / "runs" / args.run_id
    output.mkdir(parents=True, exist_ok=False)
    ledger = guard + ["record-run", "--profile", args.source_profile, "--run-id", args.run_id,
                      "--label", "swebench-registry-secret-probe", "--estimated-cost", "0.01", "--run-dir", str(output)]
    subprocess.run(ledger + ["--status", "planned"], check=True, stdout=subprocess.DEVNULL)
    app = modal.App("swebench-registry-secret-probe")
    sandbox = None
    receipt = {"source_profile": args.source_profile, "source_secret": args.source_secret,
               "target_profile": args.target_profile, "target_secret": args.target_secret,
               "estimate_usd": 0.01, "lifetime_seconds": 60,
               "over_budget_probe_explicitly_authorized": args.allow_over_budget_probe,
               "secret_created": False}
    try:
        with tempfile.TemporaryDirectory(prefix="swe-registry-seal-") as folder:
            private = Path(folder) / "private.pem"
            subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072",
                            "-out", str(private)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            public = subprocess.check_output(["openssl", "pkey", "-in", str(private), "-pubout"])
            with app.run(client=source_client):
                receipt["app_id"] = app.app_id
                sandbox = modal.Sandbox.create("sleep", "60", app=app,
                    image=modal.Image.debian_slim(python_version="3.11"),
                    secrets=[modal.Secret.from_name(args.source_secret, client=source_client)],
                    cpu=(0.125, 0.125), memory=(256, 256), timeout=60, client=source_client)
                receipt.update(app_id=app.app_id, sandbox_id=sandbox.object_id)
                subprocess.run(ledger + ["--status", "running", "--app-id", app.app_id], check=True, stdout=subprocess.DEVNULL)
                sandbox.filesystem.write_text(public.decode(), "/tmp/export-public.pem")
                process = sandbox.exec("python", "-c", REMOTE_PROBE, timeout=45)
                result = json.loads(process.stdout.read())
                process.wait()
                sealed = result.pop("sealed", None)
                receipt.update(result)
                if process.returncode or not result.get("authenticated") or not sealed:
                    return 1
                decrypted = subprocess.run(["openssl", "pkeyutl", "-decrypt", "-inkey", str(private),
                    "-pkeyopt", "rsa_padding_mode:oaep", "-pkeyopt", "rsa_oaep_md:sha256",
                    "-pkeyopt", "rsa_mgf1_md:sha256"], input=base64.b64decode(sealed), capture_output=True, check=True)
                credential = json.loads(decrypted.stdout)
                if set(credential) != {"REGISTRY_USERNAME", "REGISTRY_PASSWORD"} or not all(credential.values()):
                    raise ValueError("Invalid sealed credential")
                modal.Secret.objects.create(args.target_secret, credential, client=target_client, allow_existing=False)
                receipt["secret_created"] = True
                sandbox.terminate()
                receipt["sandbox_terminated"] = True
        return 0
    except Exception as exc:
        receipt["error_type"] = type(exc).__name__
        if sandbox is None and type(exc).__name__ == "ResourceExhaustedError":
            receipt["allocation_error"] = str(exc)
        raise
    finally:
        if sandbox is not None and not receipt.get("sandbox_terminated"):
            sandbox.terminate()
            receipt["sandbox_terminated"] = True
        (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        subprocess.run(ledger + ["--status", "completed" if receipt["secret_created"] else "failed"], check=False, stdout=subprocess.DEVNULL)
        print(json.dumps(receipt))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(json.dumps({"migration_failed": True, "error_type": type(exc).__name__}))
        sys.exit(1)

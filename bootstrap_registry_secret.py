"""Validate a WSL username/PAT file and store directly in a Modal Secret.

Run in the already installed Modal client runtime. No token is printed,
written to another file, or passed on a command line.
"""
import argparse
import base64
import json
import os
from pathlib import Path

from registry_cache import registry_token, urlopen
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--credential-file", required=True)
    parser.add_argument("--secret-name", required=True)
    args = parser.parse_args()
    lines = [line.strip() for line in Path(args.credential_file).read_text().splitlines() if line.strip()]
    if len(lines) != 2:
        raise ValueError("Credential file must contain username then PAT")
    credential = {"REGISTRY_USERNAME": lines[0], "REGISTRY_PASSWORD": lines[1]}
    previous = {key: os.environ.get(key) for key in credential}
    try:
        os.environ.update(credential)
        repository = "swebench/sweb.eval.x86_64.django_1776_django-13158"
        token = registry_token(repository)
        claims = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "==="))
        if claims.get("sub") != credential["REGISTRY_USERNAME"]:
            raise ValueError("Auth response did not identify the expected account")
        request = urllib.request.Request(f"https://registry-1.docker.io/v2/{repository}/manifests/latest",
            method="HEAD", headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.oci.image.index.v1+json"})
        with urlopen(request, timeout=30) as response:
            limits = {key.lower(): value for key, value in response.headers.items()
                      if "ratelimit" in key.lower() and "source" not in key.lower()}
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    os.environ["MODAL_PROFILE"] = args.profile
    import modal
    modal.Secret.objects.create(args.secret_name, credential, allow_existing=False)
    print(json.dumps({"authenticated": True, "profile": args.profile,
                      "secret_created": args.secret_name, "rate_limit_headers": limits}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"authenticated": False, "secret_created": False, "error_type": type(exc).__name__}))
        raise SystemExit(1)

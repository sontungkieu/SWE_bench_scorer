"""Version-checked infrastructure patch for SWE-bench 4.1.0 / Modal 1.6.2.

Only a copied package is modified. Tests, test specifications and grading stay
byte-identical to the installed upstream package.
"""
import ast
import difflib
import hashlib
import json
from pathlib import Path
import shutil

UPSTREAM_SHA256 = "5632a6f4f5e2c042de127fea9b988e7da358fbf1b5ed569ae7d1d84ac0a177f7"


def patch_backend(source):
    changes = [
        ('swebench_image = modal.Image.debian_slim().pip_install("swebench", "tenacity")',
         'swebench_image = modal.Image.debian_slim().pip_install("swebench==4.1.0", "modal==1.6.2", "tenacity==9.2.1", "ghapi==1.1.1")'),
        ('        # Hack for pylint\n        self.write_file("/sys/fs/cgroup/cpu/cpu.shares", "2048")',
         '        # CPU allocation is requested through Sandbox.create(cpu=4).'),
        ('        self.sandbox.open(file_path, "w").write(content)',
         '        self.sandbox.filesystem.write_text(content, file_path)'),
        ('        p = self.sandbox.exec("python", "-m", SANDBOX_ENTRYPOINT, command)',
         '        p = self.sandbox.exec("python", REMOTE_SANDBOX_ENTRYPOINT_PATH, command)'),
        ('\n\ndef run_instances_modal(',
         '\n    finally:\n        runner.sandbox.terminate(wait=True)\n\n\ndef run_instances_modal('),
    ]
    for old, new in changes:
        if source.count(old) != 1:
            raise RuntimeError("Unexpected upstream source; refusing compatibility patch")
        source = source.replace(old, new, 1)
    ast.parse(source)
    return source


def prepare_overlay(package, directory, expected_sha=UPSTREAM_SHA256):
    package = Path(package)
    relative = Path("harness/modal_eval/run_evaluation_modal.py")
    original = (package / relative).read_bytes()
    actual = hashlib.sha256(original).hexdigest()
    if actual != expected_sha:
        raise RuntimeError("Installed backend differs from the audited upstream 4.1.0 source")
    overlay = directory / "package-overlay"
    copied = overlay / "swebench"
    shutil.copytree(package, copied, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    patched = patch_backend(original.decode()).encode()
    (copied / relative).write_bytes(patched)
    differences = []
    for path in copied.rglob("*"):
        if path.is_file() and path.read_bytes() != (package / path.relative_to(copied)).read_bytes():
            differences.append(path.relative_to(copied).as_posix())
    if differences != [relative.as_posix()]:
        raise RuntimeError("Compatibility overlay changed files outside the backend")
    protected = ["harness/grading.py", "harness/test_spec/test_spec.py",
                 "harness/test_spec/create_scripts.py", "harness/log_parsers/python.py"]
    hashes = {name: hashlib.sha256((copied / name).read_bytes()).hexdigest()
              for name in protected}
    manifest = {"upstream_backend_sha256": actual,
                "patched_backend_sha256": hashlib.sha256(patched).hexdigest(),
                "changed_package_files": differences, "unchanged_protected_sha256": hashes,
                "changes": ["pin worker dependencies", "use filesystem.write_text",
                            "use Sandbox CPU request instead of cgroup write",
                            "invoke unchanged entrypoint by absolute path",
                            "terminate Sandbox in finally"]}
    (directory / "compat-manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
    (directory / "compat.patch").write_text("".join(difflib.unified_diff(
        original.decode().splitlines(keepends=True), patched.decode().splitlines(keepends=True),
        fromfile="upstream/"+relative.as_posix(), tofile="compat/"+relative.as_posix())))
    return overlay

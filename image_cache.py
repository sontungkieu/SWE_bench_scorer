"""Deduplicated Docker-save cache. Only archives live on a Modal v2 Volume.

Docker's overlay2 data root stays on local VM disk. Manifests are published
only after every compressed blob is durable; cached images never follow tags.
Use one cache writer at a time (the web queue already serializes jobs).
"""
import gzip
import hashlib
import json
import os
import re
import subprocess
import tarfile
import time
import uuid
from pathlib import Path, PurePosixPath

FORMAT = 1
DEFAULT_VOLUME = "swebench-verified-images-v1"
MOUNT = "/image-cache"
HEX = re.compile(r"[0-9a-f]{64}")


class ImageCache:
    def __init__(self, root, limit_gib=256):
        self.root = Path(root)
        self.limit = int(limit_gib * 1024 ** 3)
        self.blobs = self.root / "blobs"
        self.manifests = self.root / "manifests"
        self.blobs.mkdir(parents=True, exist_ok=True)
        self.manifests.mkdir(parents=True, exist_ok=True)
        self.used = sum(p.stat().st_size for p in self.blobs.glob("*/*") if p.is_file())
        self.known = {}
        for path in self.manifests.glob("*.json"):
            value = json.loads(path.read_text())
            self.remember(value)

    def remember(self, value):
        for part in value["parts"]:
            # These filenames are content identities in Docker save archives.
            if re.fullmatch(r"(?:[0-9a-f]{64}/layer.tar|[0-9a-f]{64}\.json|blobs/sha256/[0-9a-f]{64})", part["name"]):
                self.known[(part["name"], part["size"])] = part

    def path(self, reference):
        key = hashlib.sha256(reference.encode()).hexdigest()
        return self.manifests / (key + ".json")

    def blob_path(self, digest):
        if not isinstance(digest, str) or not HEX.fullmatch(digest):
            raise ValueError("Invalid cache blob digest")
        return self.blobs / digest[:2] / (digest + ".gz")

    def get(self, reference):
        path = self.path(reference)
        if not path.exists():
            return None
        value = json.loads(path.read_text())
        if (value.get("format") != FORMAT or value.get("reference") != reference
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", value.get("id", ""))):
            raise ValueError("Invalid cache manifest")
        for part in value["parts"]:
            name = PurePosixPath(part["name"])
            if name.is_absolute() or ".." in name.parts or part["size"] < 0:
                raise ValueError("Unsafe Docker archive member")
            if not self.blob_path(part["sha256"]).is_file():
                raise ValueError("Cache manifest references missing blob")
        return value

    def store(self, reference, inspected, stream):
        """Consume Docker's tar stdout without buffering the image in memory."""
        parts = []
        added = 0
        with tarfile.open(fileobj=stream, mode="r|") as archive:
            for member in archive:
                if member.isdir():
                    continue
                name = PurePosixPath(member.name)
                if not member.isfile() or name.is_absolute() or ".." in name.parts:
                    raise ValueError("Unsupported Docker archive entry")
                known = self.known.get((member.name, member.size))
                if known and self.blob_path(known["sha256"]).is_file():
                    digest = hashlib.sha256()
                    with archive.extractfile(member) as source:
                        while chunk := source.read(1024 * 1024):
                            digest.update(chunk)
                    if digest.hexdigest() != known["sha256"]:
                        raise ValueError("Docker archive content identity changed")
                    parts.append(known)
                    continue
                temp = self.root / (".blob-" + uuid.uuid4().hex)
                digest = hashlib.sha256()
                try:
                    with archive.extractfile(member) as source, temp.open("wb") as raw:
                        with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=1, mtime=0) as target:
                            while chunk := source.read(1024 * 1024):
                                digest.update(chunk)
                                target.write(chunk)
                                # Include temporary bytes in the storage bound.
                                if self.used + raw.tell() > self.limit:
                                    raise RuntimeError("Image cache storage limit reached")
                    key = digest.hexdigest()
                    destination = self.blob_path(key)
                    if not destination.exists():
                        size = temp.stat().st_size
                        if self.used + size > self.limit:
                            raise RuntimeError("Image cache storage limit reached")
                        destination.parent.mkdir(exist_ok=True)
                        os.replace(temp, destination)
                        self.used += size
                        added += size
                    parts.append({"name": member.name, "size": member.size, "sha256": key})
                finally:
                    temp.unlink(missing_ok=True)
        if not parts or not any(p["name"] == "manifest.json" for p in parts):
            raise ValueError("Incomplete Docker save archive")
        value = {"format": FORMAT, "reference": reference, "id": inspected["Id"],
                 "repo_digests": inspected.get("RepoDigests", []), "parts": parts,
                 "uncompressed_bytes": sum(p["size"] for p in parts)}
        return value, added

    def publish(self, value):
        temp = self.root / (".manifest-" + uuid.uuid4().hex)
        try:
            temp.write_text(json.dumps(value, indent=2) + "\n")
            os.replace(temp, self.path(value["reference"]))
            self.remember(value)
        finally:
            temp.unlink(missing_ok=True)

    def restore(self, value, target):
        # Hash while streaming once. A mismatch aborts docker load; no tests run.
        with tarfile.open(fileobj=target, mode="w|") as archive:
            for part in value["parts"]:
                member = tarfile.TarInfo(part["name"])
                member.size = part["size"]
                member.mode = 0o644
                with gzip.open(self.blob_path(part["sha256"]), "rb") as source:
                    checked = HashReader(source)
                    archive.addfile(member, checked)
                    if checked.digest.hexdigest() != part["sha256"] or source.read(1):
                        raise ValueError("Corrupt image cache blob")


class HashReader:
    def __init__(self, source):
        self.source = source
        self.digest = hashlib.sha256()

    def read(self, size):
        data = self.source.read(size)
        self.digest.update(data)
        return data


def inspect_image(reference):
    result = subprocess.run(["docker", "image", "inspect", reference], capture_output=True)
    return json.loads(result.stdout)[0] if result.returncode == 0 else None


def sync_cache(root):
    # Modal Volumes v2 supports explicit commits using sync on the mountpoint.
    subprocess.run(["sync", str(root)], check=True, timeout=120)


def ensure_image(reference, cache, log, *, require_cached=False):
    started = time.monotonic()
    saved = cache.get(reference) if cache else None
    inspected = inspect_image(reference)
    added = 0
    fetched = False
    if cache and not saved and hasattr(cache, "prefetch"):
        if require_cached:
            raise RuntimeError(f"Required image is not cached: {reference}")
        cache_record = cache.prefetch(reference)
        added = cache_record["cache_added_bytes"]
        saved = cache.get(reference)
        fetched = True
    source = "local"
    with Path(log).open("wb") as output:
        if inspected is None:
            if saved:
                source = "registry+volume" if fetched else "volume"
                process = subprocess.Popen(["docker", "load"], stdin=subprocess.PIPE,
                                           stdout=output, stderr=subprocess.STDOUT)
                try:
                    cache.restore(saved, process.stdin)
                    process.stdin.close()
                    if process.wait(timeout=600):
                        raise RuntimeError("docker load failed; see cache log")
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait()
            else:
                if require_cached:
                    raise RuntimeError(f"Required image is not cached: {reference}")
                source = "registry"
                subprocess.run(["docker", "pull", reference], stdout=output,
                               stderr=subprocess.STDOUT, check=True, timeout=600)
            inspected = inspect_image(reference)
        if inspected is None:
            raise RuntimeError("Image missing after load/pull")
        if saved and inspected["Id"] != saved["id"]:
            raise ValueError("Docker image differs from pinned cache manifest")
        if cache and not saved:
            process = subprocess.Popen(["docker", "save", reference], stdout=subprocess.PIPE, stderr=output)
            try:
                saved, added = cache.store(reference, inspected, process.stdout)
                while process.stdout.read(1024 * 1024):
                    pass
                process.stdout.close()
                if process.wait(timeout=600):
                    raise RuntimeError("docker save failed")
                sync_cache(cache.root)  # blobs durable before manifest publication
                cache.publish(saved)
                sync_cache(cache.root)
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()
    return {"reference": reference, "id": inspected["Id"],
            "repo_digests": saved["repo_digests"] if saved else inspected.get("RepoDigests", []),
            "source": source, "seconds": time.monotonic() - started,
            "cache_added_bytes": added, "cache_total_bytes": cache.used if cache else None}

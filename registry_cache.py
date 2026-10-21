"""Cache official OCI blobs directly; never unpack/recompress during prewarm.

The anonymous registry bearer token is held in memory only. No Docker or
registry credentials are needed. Compressed and uncompressed hashes are checked.
Legacy Docker-save manifests remain readable, avoiding a second download.
"""
import gzip
import hashlib
import io
import json
import os
import re
import tarfile
import time
import urllib.parse
import urllib.request
import uuid
import zlib

from image_cache import HashReader, ImageCache, sync_cache

ACCEPT = ", ".join(["application/vnd.docker.distribution.manifest.v2+json",
                    "application/vnd.docker.distribution.manifest.list.v2+json",
                    "application/vnd.oci.image.manifest.v1+json",
                    "application/vnd.oci.image.index.v1+json"])


class RegistryRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        redirected = super().redirect_request(request, fp, code, message, headers, newurl)
        if urllib.parse.urlsplit(newurl).netloc != urllib.parse.urlsplit(request.full_url).netloc:
            redirected.remove_header("Authorization")
        return redirected


def urlopen(request, timeout=60):
    return urllib.request.build_opener(RegistryRedirect()).open(request, timeout=timeout)


def digest_key(digest):
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("Unsupported registry digest")
    return digest.split(":")[1]


class RegistryCache:
    def __init__(self, root, limit_gib=256):
        self.legacy = ImageCache(root, limit_gib)
        self.root = self.legacy.root
        self.limit = self.legacy.limit
        self.base = self.root / "oci"
        self.blobs = self.base / "blobs"
        self.manifests = self.base / "manifests"
        self.blobs.mkdir(parents=True, exist_ok=True)
        self.manifests.mkdir(parents=True, exist_ok=True)
        self.used = self.legacy.used + sum(p.stat().st_size for p in self.blobs.iterdir()
                                          if p.is_file() and re.fullmatch(r"[0-9a-f]{64}", p.name))

    def path(self, reference):
        return self.manifests / (hashlib.sha256(reference.encode()).hexdigest() + ".json")

    def blob_path(self, descriptor):
        return self.blobs / digest_key(descriptor["digest"])

    def get(self, reference):
        path = self.path(reference)
        if not path.exists():
            return self.legacy.get(reference)
        value = json.loads(path.read_text())
        if value["format"] != 2 or value["reference"] != reference:
            raise ValueError("Invalid OCI cache manifest")
        digest_key(value["id"])
        for blob in [value["config"], *value["layers"]]:
            path = self.blob_path(blob)
            if not path.is_file() or path.stat().st_size != blob["size"]:
                raise ValueError("Incomplete OCI cache blob")
        return value

    def fetch_json(self, repository, name, token):
        request = urllib.request.Request(f"https://registry-1.docker.io/v2/{repository}/manifests/{name}",
                                         headers={"Accept": ACCEPT, "Authorization": "Bearer " + token})
        with urlopen(request, timeout=60) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("Registry manifest too large")
        actual = "sha256:" + hashlib.sha256(raw).hexdigest()
        if name.startswith("sha256:") and name != actual:
            raise ValueError("Registry manifest digest mismatch")
        return json.loads(raw), actual

    def download(self, repository, descriptor, token, *, layer=False):
        path = self.blob_path(descriptor)
        metadata = path.with_suffix(".json")
        if path.exists() and metadata.exists():
            value = json.loads(metadata.read_text())
            if path.stat().st_size != descriptor["size"]:
                raise ValueError("Corrupt cached OCI blob size")
            return value
        if path.exists():
            with path.open("rb") as source, open(os.devnull, "wb") as target:
                value = copy_blob(source, target, descriptor, layer=layer)
            metadata.write_text(json.dumps(value) + "\n")
            return value
        if descriptor["size"] < 0 or self.used + descriptor["size"] > self.limit:
            raise RuntimeError("Image cache storage limit reached")
        request = urllib.request.Request(f"https://registry-1.docker.io/v2/{repository}/blobs/{descriptor['digest']}",
                                         headers={"Authorization": "Bearer " + token})
        temp = self.base / (".blob-" + uuid.uuid4().hex)
        try:
            with urlopen(request, timeout=60) as source, temp.open("wb") as target:
                value = copy_blob(source, target, descriptor, layer=layer)
            os.replace(temp, path)
            self.used += path.stat().st_size
            metadata.write_text(json.dumps(value) + "\n")
            return value
        finally:
            temp.unlink(missing_ok=True)

    def prefetch(self, reference):
        start = time.monotonic()
        old = self.get(reference) if self.path(reference).exists() else None
        if old:
            return self.record(old, "already_cached", 0, 0)
        match = re.fullmatch(r"(swebench/[a-z0-9_.-]+):([a-zA-Z0-9_.-]+)", reference)
        if not match:
            raise ValueError("Cache only accepts official swebench Docker Hub references")
        repository, tag = match.groups()
        query = urllib.parse.urlencode({"service": "registry.docker.io", "scope": f"repository:{repository}:pull"})
        with urlopen("https://auth.docker.io/token?" + query, timeout=60) as response:
            token = json.load(response)["token"]
        manifest, digest = self.fetch_json(repository, tag, token)
        if "manifests" in manifest:
            platforms = [r for r in manifest["manifests"] if r.get("platform", {}).get("os") == "linux"
                         and r.get("platform", {}).get("architecture") == "amd64"]
            if len(platforms) != 1:
                raise ValueError("Expected exactly one linux/amd64 image")
            manifest, digest = self.fetch_json(repository, platforms[0]["digest"], token)
        before = self.used
        config = manifest["config"]
        if config["size"] > 4 * 1024 * 1024:
            raise ValueError("Image config too large")
        self.download(repository, config, token)
        configuration = json.loads(self.blob_path(config).read_bytes())
        if configuration.get("os") != "linux" or configuration.get("architecture") != "amd64":
            raise ValueError("Wrong image platform")
        diff_ids = configuration["rootfs"]["diff_ids"]
        if len(diff_ids) != len(manifest["layers"]):
            raise ValueError("Image layer/config mismatch")
        layers = []
        for blob, diff_id in zip(manifest["layers"], diff_ids):
            metadata = self.download(repository, blob, token, layer=True)
            if metadata["diff_id"] != diff_id:
                raise ValueError("Uncompressed layer digest mismatch")
            layers.append(dict(blob, **metadata))
        value = {"format": 2, "reference": reference, "id": config["digest"],
                 "repo_digests": [repository + "@" + digest], "config": config, "layers": layers}
        sync_cache(self.root)
        temporary = self.base / (".manifest-" + uuid.uuid4().hex)
        try:
            temporary.write_text(json.dumps(value, indent=2) + "\n")
            os.replace(temporary, self.path(reference))
        finally:
            temporary.unlink(missing_ok=True)
        sync_cache(self.root)
        return self.record(value, "registry", time.monotonic() - start, self.used - before)

    def record(self, value, source, seconds, added):
        return {"reference": value["reference"], "id": value["id"], "repo_digests": value["repo_digests"],
                "source": source, "seconds": seconds, "cache_added_bytes": added, "cache_total_bytes": self.used}

    def restore(self, value, target):
        if value["format"] == 1:
            return self.legacy.restore(value, target)
        config = self.blob_path(value["config"]).read_bytes()
        if "sha256:" + hashlib.sha256(config).hexdigest() != value["id"]:
            raise ValueError("Corrupt OCI config")
        config_name = digest_key(value["id"]) + ".json"
        names = [digest_key(blob["diff_id"]) + "/layer.tar" for blob in value["layers"]]
        archive_manifest = json.dumps([{"Config": config_name, "RepoTags": [value["reference"]], "Layers": names}]).encode()
        with tarfile.open(fileobj=target, mode="w|") as archive:
            add_bytes(archive, config_name, config)
            for name, blob in zip(names, value["layers"]):
                member = tarfile.TarInfo(name)
                member.size = blob["uncompressed_size"]
                member.mode = 0o644
                with self.blob_path(blob).open("rb") as raw:
                    compressed = HashReader(raw)
                    with gzip.GzipFile(fileobj=compressed, mode="rb") as source:
                        checked = HashReader(source)
                        archive.addfile(member, checked)
                        if checked.digest.hexdigest() != digest_key(blob["diff_id"]) or source.read(1):
                            raise ValueError("Corrupt OCI layer")
                    if compressed.digest.hexdigest() != digest_key(blob["digest"]):
                        raise ValueError("Corrupt compressed OCI layer")
            add_bytes(archive, "manifest.json", archive_manifest)


def add_bytes(archive, name, data):
    member = tarfile.TarInfo(name)
    member.size = len(data)
    member.mode = 0o644
    archive.addfile(member, io.BytesIO(data))


def copy_blob(source, target, descriptor, *, layer):
    if layer and not descriptor.get("mediaType", "").endswith((".gzip", "+gzip")):
        raise ValueError("Unsupported layer compression (expected gzip)")
    compressed = hashlib.sha256()
    uncompressed = hashlib.sha256()
    inflater = zlib.decompressobj(31) if layer else None
    size = expanded = 0
    while chunk := source.read(1024 * 1024):
        size += len(chunk)
        if size > descriptor["size"]:
            raise ValueError("Registry blob exceeds descriptor size")
        compressed.update(chunk)
        target.write(chunk)
        if inflater:
            pending = chunk
            while pending:
                data = inflater.decompress(pending, 1024 * 1024)
                pending = inflater.unconsumed_tail
                uncompressed.update(data)
                expanded += len(data)
    if size != descriptor["size"] or compressed.hexdigest() != digest_key(descriptor["digest"]):
        raise ValueError("Registry blob digest/size mismatch")
    if inflater and (not inflater.eof or inflater.unused_data):
        raise ValueError("Incomplete or concatenated gzip layer")
    return {"diff_id": "sha256:" + uncompressed.hexdigest(), "uncompressed_size": expanded} if layer else {}

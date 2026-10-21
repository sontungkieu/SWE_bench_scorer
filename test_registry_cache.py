import gzip
import hashlib
import io
import json
import tarfile
import tempfile
import unittest
import subprocess
import sys
from pathlib import Path
import urllib.request
import urllib.error
from unittest.mock import patch

from registry_cache import RegistryCache, RegistryRedirect, copy_blob, registry_token


class RegistryCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = RegistryCache(self.tmp.name)
        self.layer = b"layer content" * 10000
        self.compressed = gzip.compress(self.layer)
        self.descriptor = {"digest": "sha256:" + hashlib.sha256(self.compressed).hexdigest(),
                           "size": len(self.compressed), "mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip"}

    def test_stream_checks_both_hashes_and_uncompressed_size(self):
        output = io.BytesIO()
        value = copy_blob(io.BytesIO(self.compressed), output, self.descriptor, layer=True)
        self.assertEqual(output.getvalue(), self.compressed)
        self.assertEqual(value["diff_id"], "sha256:" + hashlib.sha256(self.layer).hexdigest())
        self.assertEqual(value["uncompressed_size"], len(self.layer))

    def test_invalid_digest_and_truncated_gzip_fail(self):
        with self.assertRaisesRegex(ValueError, "mismatch"):
            copy_blob(io.BytesIO(self.compressed), io.BytesIO(), dict(self.descriptor, digest="sha256:" + "0" * 64), layer=True)
        truncated = self.compressed[:-8]
        descriptor = dict(self.descriptor, digest="sha256:" + hashlib.sha256(truncated).hexdigest(), size=len(truncated))
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            copy_blob(io.BytesIO(truncated), io.BytesIO(), descriptor, layer=True)

    def test_restore_reconstructs_docker_archive_and_verifies_content(self):
        blob = self.cache.blob_path(self.descriptor)
        blob.write_bytes(self.compressed)
        metadata = copy_blob(io.BytesIO(self.compressed), io.BytesIO(), self.descriptor, layer=True)
        config = b'{"architecture":"amd64","os":"linux"}'
        config_descriptor = {"digest": "sha256:" + hashlib.sha256(config).hexdigest(), "size": len(config)}
        self.cache.blob_path(config_descriptor).write_bytes(config)
        value = {"format": 2, "reference": "swebench/test:latest", "id": config_descriptor["digest"],
                 "config": config_descriptor, "layers": [dict(self.descriptor, **metadata)]}
        result = io.BytesIO()
        self.cache.restore(value, result)
        result.seek(0)
        with tarfile.open(fileobj=result) as archive:
            manifest = json.load(archive.extractfile("manifest.json"))[0]
            self.assertEqual(manifest["RepoTags"], ["swebench/test:latest"])
            self.assertEqual(archive.extractfile(manifest["Layers"][0]).read(), self.layer)
        blob.write_bytes(b"corrupt")
        with self.assertRaises((ValueError, OSError, EOFError)):
            self.cache.restore(value, io.BytesIO())

    def test_digest_paths_are_validated(self):
        with self.assertRaises(ValueError):
            self.cache.blob_path({"digest": "../../outside"})

    def test_registry_token_is_not_forwarded_to_blob_cdn(self):
        request = urllib.request.Request("https://registry-1.docker.io/v2/test/blobs/sha256:123",
                                         headers={"Authorization": "Bearer test-only"})
        redirected = RegistryRedirect().redirect_request(request, None, 302, "Found", {}, "https://cdn.example/blob")
        self.assertIsNone(redirected.get_header("Authorization"))

    def test_high_compression_ratio_layer_is_streamed_correctly(self):
        original = b"x" * (10 * 1024 * 1024)
        compressed = gzip.compress(original)
        descriptor = dict(self.descriptor, digest="sha256:" + hashlib.sha256(compressed).hexdigest(), size=len(compressed))
        result = copy_blob(io.BytesIO(compressed), io.BytesIO(), descriptor, layer=True)
        self.assertEqual(result["uncompressed_size"], len(original))
        self.assertEqual(result["diff_id"], "sha256:" + hashlib.sha256(original).hexdigest())

    def test_shared_blob_skips_network(self):
        blob = self.cache.blob_path(self.descriptor)
        blob.write_bytes(self.compressed)
        with patch("urllib.request.urlopen") as network:
            first = self.cache.download("swebench/test", self.descriptor, "unused", layer=True)
            second = self.cache.download("swebench/test", self.descriptor, "unused", layer=True)
        network.assert_not_called()
        self.assertEqual(first, second)

    def test_authenticated_request_uses_only_trusted_token_endpoint(self):
        with patch.dict("os.environ", {"REGISTRY_USERNAME": "test-user", "REGISTRY_PASSWORD": "test-password"}), \
                patch("registry_cache.urlopen") as request:
            request.return_value.__enter__.return_value = io.BytesIO(b'{"token":"test-bearer"}')
            self.assertEqual(registry_token("swebench/test"), "test-bearer")
        sent = request.call_args.args[0]
        self.assertTrue(sent.full_url.startswith("https://auth.docker.io/token?"))
        self.assertNotIn("test-password", sent.full_url)
        self.assertTrue(sent.get_header("Authorization").startswith("Basic "))

    def test_invalid_credentials_never_fall_back_to_anonymous(self):
        with patch.dict("os.environ", {"REGISTRY_USERNAME": "test-user", "REGISTRY_PASSWORD": "test-password"}), \
                patch("registry_cache.urlopen", side_effect=urllib.error.HTTPError("https://auth.docker.io", 401, "Unauthorized", {}, None)) as request:
            with self.assertRaisesRegex(RuntimeError, "HTTP 401.*no anonymous fallback"):
                registry_token("swebench/test")
        self.assertEqual(request.call_count, 1)

    def test_partial_credentials_fail_before_network(self):
        with patch.dict("os.environ", {"REGISTRY_USERNAME": "test-user", "REGISTRY_PASSWORD": ""}), \
                patch("registry_cache.urlopen") as request:
            with self.assertRaisesRegex(RuntimeError, "Both"):
                registry_token("swebench/test")
        request.assert_not_called()

    def test_anonymous_request_remains_supported(self):
        with patch.dict("os.environ", {"REGISTRY_USERNAME": "", "REGISTRY_PASSWORD": ""}), \
                patch("registry_cache.urlopen") as request:
            request.return_value.__enter__.return_value = io.BytesIO(b'{"token":"anonymous-test"}')
            self.assertEqual(registry_token("swebench/test"), "anonymous-test")
        self.assertIsNone(request.call_args.args[0].get_header("Authorization"))

    def test_registry_secret_cannot_be_injected_into_evaluation(self):
        result = subprocess.run([sys.executable, str(Path(__file__).with_name("launch.py")),
            "mock", "--profile", "offline-test", "--registry-secret", "test-only"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("only be injected into cache-only VMs", result.stderr)


if __name__ == "__main__":
    unittest.main()

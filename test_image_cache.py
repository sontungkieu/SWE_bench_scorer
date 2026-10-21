import gzip
import io
import json
import tarfile
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from image_cache import ImageCache
import remote_worker


def docker_archive(layer=b"shared layer", config=b"{}"):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, value in [("layer/layer.tar", layer), ("config.json", config),
                            ("manifest.json", b'[{"Config":"config.json","RepoTags":["test:latest"],"Layers":["layer/layer.tar"]}]')]:
            member = tarfile.TarInfo(name)
            member.size = len(value)
            archive.addfile(member, io.BytesIO(value))
    stream.seek(0)
    return stream


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = ImageCache(self.tmp.name)
        self.inspected = {"Id": "sha256:" + "a" * 64, "RepoDigests": ["test@sha256:" + "b" * 64]}

    def save(self, reference="test:latest", **kwargs):
        value, added = self.cache.store(reference, self.inspected, docker_archive(**kwargs))
        self.cache.publish(value)
        return value, added

    def test_roundtrip_and_layer_deduplication(self):
        first, added = self.save()
        self.assertGreater(added, 0)
        second, second_added = self.save("test:other", config=b'{"different": true}')
        self.assertEqual(first["parts"][0]["sha256"], second["parts"][0]["sha256"])
        self.assertLess(second_added, added)
        output = io.BytesIO()
        self.cache.restore(self.cache.get("test:latest"), output)
        output.seek(0)
        with tarfile.open(fileobj=output) as archive:
            self.assertEqual(archive.extractfile("layer/layer.tar").read(), b"shared layer")
        reopened = ImageCache(self.tmp.name)
        self.assertEqual(reopened.used, self.cache.used)
        self.assertEqual(reopened.get("test:latest")["id"], self.inspected["Id"])

    def test_partial_archive_never_publishes_manifest(self):
        with self.assertRaises((ValueError, tarfile.ReadError)):
            self.cache.store("test:bad", self.inspected, io.BytesIO(b"incomplete tar"))
        self.assertIsNone(self.cache.get("test:bad"))

    def test_storage_bound_and_temp_cleanup(self):
        self.cache.limit = 1
        with self.assertRaisesRegex(RuntimeError, "storage limit"):
            self.save()
        self.assertIsNone(self.cache.get("test:latest"))
        self.assertEqual(list(Path(self.tmp.name).glob(".blob-*")), [])

    def test_corruption_aborts_restore(self):
        value, _ = self.save()
        with gzip.open(self.cache.blob_path(value["parts"][0]["sha256"]), "wb") as stream:
            stream.write(b"wrong content")
        with self.assertRaises((ValueError, OSError)):
            self.cache.restore(value, io.BytesIO())

    def test_missing_blob_fails_closed(self):
        value, _ = self.save()
        self.cache.blob_path(value["parts"][0]["sha256"]).unlink()
        with self.assertRaisesRegex(ValueError, "missing blob"):
            self.cache.get("test:latest")

    def test_manifest_path_traversal_rejected(self):
        value, _ = self.save()
        value["parts"][0]["sha256"] = "../../outside"
        self.cache.path("test:latest").write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "digest"):
            self.cache.get("test:latest")

    def test_empty_predictions_do_not_load_images_or_run_tests(self):
        data = [{"instance_id": "django__django-11099"}]
        dataset = Path(self.tmp.name) / "dataset.json"
        dataset.write_text(json.dumps(data))
        rows = [dict(data[0], model_name_or_path="test", model_patch="")]
        args = SimpleNamespace(mode="smoke", workers=1, test_timeout=60, cache=None, require_cached=True)
        with patch.dict("sys.modules", {"swebench.harness.test_spec.test_spec": SimpleNamespace(make_test_spec=None)}), \
                patch.object(remote_worker, "OUT", Path(self.tmp.name)), \
                patch.object(remote_worker, "ensure_image") as ensure, \
                patch.object(remote_worker, "execute") as execute:
            summary = remote_worker.evaluate(rows, [data[0]["instance_id"]], dataset, "empty", args)
        ensure.assert_not_called()
        execute.assert_not_called()
        self.assertTrue(summary["complete"])
        self.assertEqual(summary["empty_patch_count"], 1)


if __name__ == "__main__":
    unittest.main()

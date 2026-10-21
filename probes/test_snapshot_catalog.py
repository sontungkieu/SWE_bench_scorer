import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from snapshot_catalog import make_plan, BUILD_BOUND, VERIFY_BOUND, ESTIMATE
from snapshot_catalog_worker import TTL, batches_of, identity, validate_rows, write_archive


def digest(data):
    return 'sha256:' + hashlib.sha256(data).hexdigest()


class FakeCache:
    def __init__(self, root):
        self.root = root
        self.rows = []

    def blob_path(self, blob):
        return self.root / blob['digest'].split(':')[1]

    def get(self, ref):
        return next(r for r in self.rows if r['reference'] == ref)

    def add(self, number, raw=b'layer tar fixture'):
        compressed = gzip.compress(raw, mtime=0)
        layer = {'digest':digest(compressed),'diff_id':digest(raw),'size':len(compressed),
                 'uncompressed_size':len(raw)}
        self.blob_path(layer).write_bytes(compressed)
        config_bytes=json.dumps({'rootfs':{'diff_ids':[layer['diff_id']]},'fixture':number}).encode()
        config={'digest':digest(config_bytes),'size':len(config_bytes)}
        self.blob_path(config).write_bytes(config_bytes)
        row={'format':2,'reference':f'swebench/sweb.eval.x86_64.django_1776_django-{number}:latest',
             'id':config['digest'],'config':config,'layers':[layer]}
        self.rows.append(row)
        return row


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache=FakeCache(Path(self.temp.name))
        self.cache.add(1)
        self.cache.add(2)

    def test_standard_multi_image_archive_deduplicates_shared_layer(self):
        output=io.BytesIO()
        stats=write_archive(self.cache,self.cache.rows,output)
        self.assertEqual(stats['unique_layers'],1)
        with tarfile.open(fileobj=io.BytesIO(output.getvalue())) as archive:
            names=archive.getnames()
            self.assertEqual(len([n for n in names if n.endswith('/layer.tar')]),1)
            manifest=json.load(archive.extractfile('manifest.json'))
            self.assertEqual(len(manifest),2)
            self.assertEqual(manifest[0]['Layers'],manifest[1]['Layers'])
            for row,item in zip(self.cache.rows,manifest):
                self.assertEqual(digest(archive.extractfile(item['Config']).read()),row['id'])

    def test_corrupt_config_aborts_before_publication(self):
        self.cache.blob_path(self.cache.rows[0]['config']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'Corrupt OCI config'):
            write_archive(self.cache,self.cache.rows,io.BytesIO())

    def test_changed_compressed_bytes_are_rejected_even_if_expanded_bytes_match(self):
        blob=self.cache.rows[0]['layers'][0]
        self.cache.blob_path(blob).write_bytes(gzip.compress(b'layer tar fixture',mtime=123))
        with self.assertRaisesRegex(ValueError,'compressed OCI layer'):
            write_archive(self.cache,self.cache.rows,io.BytesIO())

    def test_wrong_expanded_digest_is_rejected(self):
        blob=self.cache.rows[0]['layers'][0]
        changed=gzip.compress(b'different fixture',mtime=0)
        self.cache.blob_path(blob).write_bytes(changed)
        with self.assertRaisesRegex(ValueError,'expanded OCI layer'):
            write_archive(self.cache,self.cache.rows,io.BytesIO())

    def test_changed_cache_manifest_is_rejected(self):
        planned=json.loads(json.dumps(self.cache.rows))
        self.cache.rows[0]['id']=digest(b'changed')
        with self.assertRaisesRegex(ValueError,'changed after planning'):
            write_archive(self.cache,planned,io.BytesIO())

    def test_batches_bound_unique_bytes_and_count(self):
        self.assertEqual(len(batches_of(self.cache.rows,max_images=1)),2)
        size=self.cache.rows[0]['layers'][0]['uncompressed_size']
        self.assertEqual(len(batches_of(self.cache.rows,max_bytes=size)),1)
        with self.assertRaisesRegex(ValueError,'Single image'):
            batches_of(self.cache.rows,max_bytes=size-1)

    def test_partial_or_duplicate_catalog_is_rejected(self):
        with self.assertRaises(ValueError):validate_rows(self.cache.rows)
        with self.assertRaises(ValueError):validate_rows([self.cache.rows[0]]*2,expected=2)
        validate_rows(self.cache.rows,expected=2)

    def test_90_day_ttl_and_finite_allocation_bound(self):
        self.assertEqual(TTL,7776000)
        self.assertLess(BUILD_BOUND+VERIFY_BOUND,ESTIMATE)
        self.assertLess(ESTIMATE,2)
        self.assertEqual(identity(self.cache.rows[0]['reference']),'django__django-1')


if __name__=='__main__':
    unittest.main()

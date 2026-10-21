"""Adversarial checks for the independent evidence audit, without cloud calls."""
import copy
import json
from pathlib import Path
import tarfile
import unittest
from unittest.mock import patch

from audit_snapshot_evidence import billing_cost, decode, image_map, validate_shard_binding
from snapshot_catalog_worker import verify_images
from snapshot_shards import verify_catalog


class EvidenceAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1] / 'evidence/2026-10-22-verified-snapshots-90d'
        with tarfile.open(root / 'raw-shards-metadata.tar.gz') as archive:
            prefix = 'snapshot-shards-90d-20261022-01/'
            cls.plan = decode(archive.extractfile(prefix + 'shard-plan.json').read())
            cls.shards = decode((root / 'shard-receipt.json').read_bytes())['shards']
            shard = prefix + 'shard-00/'
            raw = archive.extractfile(shard + 'catalog-plan.json').read()
            cls.good = [cls.plan['groups'][0], cls.shards[0], decode(raw),
                        decode(archive.extractfile(shard + 'manifest.json').read()),
                        decode(archive.extractfile(shard + 'verification.json').read()), raw]

    def test_retained_group_is_bound_to_its_own_verified_plan(self):
        validate_shard_binding(*self.good)
        self.assertTrue(verify_catalog(self.plan, self.shards))

    def test_duplicate_json_keys_and_nonfinite_numbers_are_rejected(self):
        for raw in ['{"resolved":false,"resolved":true}', '{"cost":NaN}', '{"cost":Infinity}']:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                decode(raw)

    def test_missing_negative_nonfinite_billing_is_rejected(self):
        for rows in [[], [{'cost': '-0.1'}], [{'cost': 'NaN'}], [{'cost': 'Infinity'}]]:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                billing_cost(rows)

    def test_global_union_alone_does_not_establish_per_shard_mapping(self):
        # The executed collector accepted this; both validators must reject it now.
        swapped = copy.deepcopy(self.shards)
        swapped[0]['images'], swapped[1]['images'] = swapped[1]['images'], swapped[0]['images']
        with self.assertRaisesRegex(ValueError, 'verified shard plan'):
            verify_catalog(self.plan, swapped)
        bad = copy.deepcopy(self.good)
        bad[1] = swapped[0]
        with self.assertRaisesRegex(ValueError, 'per-shard coverage'):
            validate_shard_binding(*bad)

    def test_wrong_count_and_receipt_hash_are_rejected(self):
        for field, value in [('images_verified', 99), ('plan_sha256', '0' * 64)]:
            bad = copy.deepcopy(self.good)
            bad[4][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_shard_binding(*bad)

    def test_duplicate_image_rows_are_rejected(self):
        row = self.good[0]['images'][0]
        with self.assertRaises(ValueError):
            image_map([row, row])

    def test_docker_inspection_rejects_wrong_id_tag_or_layer_order(self):
        row = self.good[0]['images'][0]
        good = {'Id': row['id'], 'RepoTags': [row['reference']],
                'RootFS': {'Layers': [blob['diff_id'] for blob in row['layers']]}}
        variants = [dict(good, Id='sha256:' + 'f' * 64), dict(good, RepoTags=[]),
                    dict(good, RootFS={'Layers': list(reversed(good['RootFS']['Layers']))})]
        for image in variants:
            with self.subTest(image=image), patch('snapshot_catalog_worker.subprocess.check_output',
                                                  return_value=json.dumps([image]).encode()):
                with self.assertRaises(ValueError):
                    verify_images([row])

    def test_old_test_container_prevents_clean_snapshot_verification(self):
        row = self.good[0]['images'][0]
        image = {'Id': row['id'], 'RepoTags': [row['reference']],
                 'RootFS': {'Layers': [blob['diff_id'] for blob in row['layers']]}}
        with patch('snapshot_catalog_worker.subprocess.check_output',
                   side_effect=[json.dumps([image]).encode(), b'old-container\n']):
            with self.assertRaisesRegex(ValueError, 'clean Docker store'):
                verify_images([row])


if __name__ == '__main__':
    unittest.main()

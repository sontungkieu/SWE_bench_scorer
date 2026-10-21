import hashlib
import unittest

from snapshot_shards import DEADLINE_SECONDS, ESTIMATE, RATE, shard_plan, verify_catalog
from snapshot_catalog_worker import TTL, GIB, validate_rows


def rows(unique=False):
    result=[]
    for i in range(500):
        key='sha256:'+hashlib.sha256(str(i).encode()).hexdigest()
        layer=key if unique else 'sha256:'+'a'*64
        result.append({'format':2,'reference':f'swebench/sweb.eval.x86_64.django_1776_django-{i}:latest',
             'id':key,'config':{'digest':key,'size':1},
             'layers':[{'digest':layer,'diff_id':layer,'size':1,'uncompressed_size':GIB}]})
    return result


class ShardTests(unittest.TestCase):
    def plan(self,unique=False):
        return shard_plan({'images':rows(unique),'image_count':500,'snapshot_ttl_seconds':TTL,'dataset':{}})

    def receipts(self,plan):
        return [dict(index=g['index'],image_count=g['image_count'],images=g['images'],
                     snapshot_image_id=f"im-fixture-{g['index']}",
                     verified=True,all_vms_terminated=True) for g in plan['groups']]

    def test_partition_covers_every_image_once_with_size_and_count_bounds(self):
        plan=self.plan(unique=True)
        self.assertEqual(sum(g['image_count'] for g in plan['groups']),500)
        self.assertTrue(all(g['image_count']<=100 and g['layer_tar_bytes']<=35*GIB for g in plan['groups']))
        verify_catalog(plan,self.receipts(plan))

    def test_shared_layers_allow_many_images_without_multiplying_storage(self):
        plan=self.plan()
        self.assertEqual(plan['shard_count'],5)
        self.assertEqual(plan['groups'][0]['layer_tar_bytes'],GIB)

    def test_missing_or_unverified_shard_never_publishes_complete_catalog(self):
        plan=self.plan()
        with self.assertRaises(ValueError):verify_catalog(plan,self.receipts(plan)[:-1])
        shards=self.receipts(plan);shards[-1]['verified']=False
        with self.assertRaises(ValueError):verify_catalog(plan,shards)

    def test_duplicate_and_mutated_image_coverage_are_rejected(self):
        plan=self.plan();shards=self.receipts(plan)
        shards[-1]['images']=[shards[0]['images'][0],*shards[-1]['images'][1:]]
        with self.assertRaises(ValueError):verify_catalog(plan,shards)
        plan=self.plan();shards=self.receipts(plan)
        shards[-1]['images']=[dict(r,id='sha256:'+'f'*64) for r in shards[-1]['images']]
        with self.assertRaises(ValueError):verify_catalog(plan,shards)

    def test_resource_deadline_fits_remaining_original_budget(self):
        self.assertLess(RATE*DEADLINE_SECONDS,ESTIMATE)
        prior=.42199984+.27996446+.53845001+.0126624
        self.assertLess(prior+ESTIMATE,2)

    def test_worker_rejects_empty_or_unbounded_shards(self):
        with self.assertRaises(ValueError):validate_rows([],expected=0)
        with self.assertRaises(ValueError):validate_rows(rows(),expected=501)

    def test_swapped_groups_cannot_publish_wrong_instance_to_snapshot_mapping(self):
        plan=self.plan();shards=self.receipts(plan)
        shards[0]['images'],shards[1]['images']=shards[1]['images'],shards[0]['images']
        with self.assertRaisesRegex(ValueError,'verified shard plan'):verify_catalog(plan,shards)

    def test_duplicate_index_snapshot_or_count_mismatch_is_rejected(self):
        for key,value in [('index',0),('snapshot_image_id','im-fixture-0'),('image_count',99)]:
            plan=self.plan();shards=self.receipts(plan);shards[1][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):verify_catalog(plan,shards)


if __name__=='__main__':unittest.main()

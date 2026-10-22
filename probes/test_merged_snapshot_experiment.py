import copy
from datetime import datetime, timedelta, timezone
import tempfile
from pathlib import Path
import unittest

from merged_pilot_worker import select_queue
from merged_snapshot_experiment import BOUND, ESTIMATE, lifetime_for, selected_groups, prepare
from snapshot_pilot import validate_inputs

ROOT=Path(__file__).resolve().parents[1]


class ExperimentTests(unittest.TestCase):
    def inputs(self):
        data=[{'instance_id':f'id-{i}','patch':f'gold-{i}'} for i in range(24)]
        preds=[{'instance_id':r['instance_id'],'model_patch':r['patch']} for r in data]
        return data,preds

    def test_nine_controls_are_one_queue_without_dropping_last_control(self):
        data,preds=self.inputs()
        assignment={'instance_ids':[r['instance_id'] for r in data[:9]]}
        selected=select_queue(data,preds,assignment)
        self.assertEqual(selected,preds[:9])
        self.assertEqual(lifetime_for(8),780)
        self.assertEqual(lifetime_for(9),1380)
        self.assertLess(BOUND,ESTIMATE)
        self.assertAlmostEqual(BOUND,4.4531424)

    def test_queue_does_not_weaken_frozen_gold_validation(self):
        data,preds=self.inputs()
        for ids in [[],['id-1','id-1'],['unknown']]:
            with self.assertRaises(ValueError):select_queue(data,preds,{'instance_ids':ids})
        changed=copy.deepcopy(preds);changed[-1]['model_patch']='not-gold'
        with self.assertRaises(ValueError): select_queue(data,changed,{'instance_ids':['id-1']})

    def test_catalog_mapping_preserves_old_snapshot_index_after_reindexing(self):
        images=[{'instance_id':f'id-{i}','reference':f'ref-{i}','id':str(i)} for i in range(500)]
        counts=[4,0,2,9,4,3,2]
        groups=[];offset=0
        # First distribute the 24 selected controls, then distribute other images.
        for index,count in enumerate(counts):
            group={'index':index,'snapshot_image_id':f'im-{index}',
                   'snapshot_shard_index':8 if index==4 else index,'snapshot_plan_sha256':'a'*64,
                   'verified':True,'all_vms_terminated':True,
                   'use_before':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),
                   'images':images[offset:offset+count]}
            offset+=count;groups.append(group)
        groups[1]['images']=images[24:]
        catalog={'complete':True,'image_count':500,'shards':groups}
        selection={'selected':[{'instance_id':r['instance_id']} for r in images[:24]]}
        result=selected_groups(catalog,selection,images[:24])
        self.assertEqual(sum(len(g['instance_ids']) for g in result),24)
        self.assertEqual(len(result[0]['instance_ids']),9)
        self.assertEqual(next(g['snapshot_shard_index'] for g in result if g['index']==4),8)
        groups[0]['images'][0]=dict(images[0],id='wrong')
        with self.assertRaisesRegex(ValueError,'identity'):selected_groups(catalog,selection,images[:24])

    def test_staging_uses_pinned_inputs_without_modal_or_new_runtime(self):
        reference=ROOT/'runs/sharded-pilot-24-20261022-01-retrieved/sharded-pilot-24-20261022-01'
        if not reference.exists():self.skipTest('local retained baseline not installed; run integration on coordinator')
        with tempfile.TemporaryDirectory() as temporary:
            out=Path(temporary)/'staged'
            prepare(out,ROOT,reference,'fixture-source-commit')
            self.assertEqual(validate_inputs(out)['count'],24)
            self.assertTrue((out/'optimizeN7merge/merge-plan.json').exists())
            self.assertTrue((out/'optimizeN7weighted/merge-plan.json').exists())
            with self.assertRaisesRegex(ValueError,'overwrite'):prepare(out,ROOT,reference,'fixture')


if __name__=='__main__':unittest.main()

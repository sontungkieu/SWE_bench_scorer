import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tarfile
import unittest

from sharded_pilot import DEADLINE, ESTIMATE, RATE, make_plan
from sharded_pilot_worker import select_inputs


class ShardedPilotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        evidence = Path(__file__).resolve().parents[1] / 'evidence'
        cls.catalog = json.loads((evidence/'2026-10-22-verified-snapshots-90d/usable-shards-catalog.json').read_text())
        cls.selection = json.loads((evidence/'2026-10-22-cost-pilot/selection.json').read_text())
        cls.images = json.loads((evidence/'2026-10-22-vm-snapshot/image-plan.json').read_text())
        cls.now = datetime(2026,10,22,tzinfo=timezone.utc)
        with tarfile.open(evidence/'2026-10-22-scientific-audit/pilot-inputs.tar.gz') as archive:
            cls.data = json.load(archive.extractfile('pilot.json'))
            cls.preds = [json.loads(line) for line in archive.extractfile('preds.jsonl')]

    def plan(self):
        return make_plan(self.catalog,self.selection,self.images,self.now)

    def test_exact_24_route_to_eight_existing_snapshots(self):
        plan=self.plan()
        self.assertEqual([g['index'] for g in plan['groups']],[0,2,4,6,7,8,9,10])
        self.assertEqual([len(g['instance_ids']) for g in plan['groups']],[4,2,1,6,3,4,3,1])
        found=[iid for g in plan['groups'] for iid in g['instance_ids']]
        self.assertEqual(len(found),24)
        self.assertEqual(set(found),{r['instance_id'] for r in self.preds})

    def test_full_or_duplicate_selection_is_rejected(self):
        for selected in [self.selection['selected']*21,self.selection['selected'][:23]+self.selection['selected'][:1]]:
            bad=dict(self.selection,selected=selected)
            with self.assertRaises(ValueError):make_plan(self.catalog,bad,self.images,self.now)

    def test_changed_image_id_is_rejected_before_allocation(self):
        bad=copy.deepcopy(self.images);bad[0]['id']='sha256:'+'f'*64
        with self.assertRaises(ValueError):make_plan(self.catalog,self.selection,bad,self.now)

    def test_duplicate_catalog_instance_is_rejected(self):
        bad=copy.deepcopy(self.catalog);bad['shards'][0]['images'].append(bad['shards'][0]['images'][0])
        with self.assertRaises(ValueError):make_plan(bad,self.selection,self.images,self.now)

    def test_expired_snapshot_is_rejected_before_allocation(self):
        bad=copy.deepcopy(self.catalog);bad['shards'][0]['use_before']=(self.now+timedelta(minutes=2)).isoformat()
        with self.assertRaises(ValueError):make_plan(bad,self.selection,self.images,self.now)

    def test_worker_runs_only_assigned_original_gold_rows(self):
        group=self.plan()['groups'][0]
        found=select_inputs(self.data,self.preds,group)
        self.assertEqual([r['instance_id'] for r in found],group['instance_ids'])
        bad=copy.deepcopy(self.preds);bad[0]['model_patch']='modified'
        with self.assertRaises(ValueError):select_inputs(self.data,bad,group)

    def test_worker_rejects_unknown_duplicate_or_oversized_assignment(self):
        for selected in [['other__repo-1'],[self.preds[0]['instance_id']]*2,[r['instance_id'] for r in self.preds][:9]]:
            with self.assertRaises(ValueError):select_inputs(self.data,self.preds,{'instance_ids':selected})

    def test_bounded_cpu_memory_estimate(self):
        self.assertEqual(DEADLINE,1800)
        self.assertAlmostEqual(RATE*DEADLINE,1.90368)
        self.assertLess(RATE*DEADLINE,ESTIMATE)


if __name__=='__main__':unittest.main()

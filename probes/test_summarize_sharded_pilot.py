import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import tarfile
import unittest

from sharded_pilot import make_plan
from summarize_sharded_pilot import summarize


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.out=Path(self.temp.name)
        self.evidence=Path(__file__).resolve().parents[1]/'evidence'
        read=lambda name:json.loads((self.evidence/name).read_text())
        selection=read('2026-10-22-cost-pilot/selection.json')
        catalog=read('2026-10-22-verified-snapshots-90d/usable-shards-catalog.json')
        images=read('2026-10-22-vm-snapshot/image-plan.json')
        plan=make_plan(catalog,selection,images,datetime(2026,10,22,tzinfo=timezone.utc))
        with tarfile.open(self.evidence/'2026-10-22-scientific-audit/pilot-inputs.tar.gz') as archive:
            for name in ['pilot.json','preds.jsonl']:(self.out/name).write_bytes(archive.extractfile(name).read())
        self.reports=read('2026-10-22-cost-pilot/vm-reports.json')
        sources=read('2026-10-22-cost-pilot/source-provenance.json')['vm_sources_read_from_running_sandbox_sha256']
        receipt={'plan':plan,'complete':True,'all_vms_terminated':True,'groups':[],
                 'source_sha256':sources,'helper_sha256':{},
                 'source_context':{'git_commit':'synthetic-unit-fixture'},
                 'terminated_vm_ids':[],'app_id':'ap-unit-fixture','wall_seconds':200}
        for group in plan['groups']:
            index=group['index'];case=self.out/f'shard-{index:02d}'
            batch=case/'output'/f'shard-{index:02d}';batch.mkdir(parents=True)
            self.save(case/'source-readback.json',receipt['source_sha256'])
            self.save(case/'assignment.json',group)
            self.save(case/'output/reports.json',{iid:self.reports[iid] for iid in group['instance_ids']})
            self.save(case/'output/summary.json',{'complete':True,'harness_exit_code':0,'test_and_cleanup_seconds':2})
            self.save(case/'output/environment.json',{'selected_count':len(group['instance_ids'])})
            self.save(batch/'images.json',[dict(row,source='local',cache_added_bytes=0,seconds=.01) for row in group['images']])
            vm=f'sb-unit-{index}';receipt['terminated_vm_ids'].append(vm)
            receipt['groups'].append(dict(group,worker_passed=True,terminated=True,vm_id=vm,docker_ready_seconds=1,wall_seconds=3))
        self.save(self.out/'receipt.json',receipt);self.save(self.out/'reports.json',self.reports)
        for name,minute in [('billing-second.json',1),('billing-final.json',3)]:
            self.save(self.out/name,{'checked_at':f'2026-10-22T00:0{minute}:00+00:00',
                'app_id':'ap-unit-fixture','remaining_vms':[],'compute_egress_usd':'0.2',
                'rows':[{'object_id':'ap-unit-fixture','resource':'CPU','cost':'0.2'}]})

    def save(self,path,value):
        path.write_bytes((json.dumps(value)+'\n').encode())

    def collect(self):
        return summarize(self.out,self.evidence,'billing-final.json','billing-second.json')

    def test_complete_reports_and_separated_billing_reads_qualify(self):
        result=self.collect()
        self.assertTrue(result['qualified'])
        self.assertEqual(result['complete_report_matches_baseline'],24)
        self.assertTrue(result['batching_changed_from_original_3x8'])

    def test_equal_resolved_count_does_not_hide_changed_test_report(self):
        reports=copy.deepcopy(self.reports);iid='astropy__astropy-13398'
        reports[iid]['tests_status']['FAIL_TO_PASS']['success'].append('synthetic-changed-test')
        self.save(self.out/'reports.json',reports)
        path=self.out/'shard-00/output/reports.json';part=json.loads(path.read_text());part[iid]=reports[iid];self.save(path,part)
        result=self.collect()
        self.assertFalse(result['qualified']);self.assertEqual(result['different_baseline_ids'],[iid])

    def test_missing_report_and_changed_source_are_rejected(self):
        path=self.out/'shard-00/output/reports.json';part=json.loads(path.read_text());part.pop(next(iter(part)));self.save(path,part)
        with self.assertRaisesRegex(ValueError,'coverage'):self.collect()
        self.setUp()
        self.save(self.out/'shard-00/source-readback.json',{'fixture.py':'changed'})
        with self.assertRaisesRegex(ValueError,'source differs'):self.collect()

    def test_unstable_cost_or_unseparated_reads_are_rejected(self):
        path=self.out/'billing-final.json';data=json.loads(path.read_text())
        data['compute_egress_usd']='0.3';data['rows'][0]['cost']='0.3';self.save(path,data)
        with self.assertRaisesRegex(ValueError,'stabilized'):self.collect()
        data['compute_egress_usd']='0.2';data['rows'][0]['cost']='0.2'
        data['checked_at']='2026-10-22T00:01:01+00:00';self.save(path,data)
        with self.assertRaisesRegex(ValueError,'stabilized'):self.collect()


if __name__=='__main__':unittest.main()

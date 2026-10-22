import json
import unittest
from parallel_sharded_pilot import Reservations
from summarize_parallel_pilot import parallel_summary
import test_summarize_sharded_pilot as serial_tests


class ParallelCollectionTests(unittest.TestCase):
    save=serial_tests.CollectionTests.save

    def setUp(self):
        serial_tests.CollectionTests.setUp(self)
        r=json.loads((self.out/'receipt.json').read_text())
        old=json.loads((self.evidence/'2026-10-22-sharded-snapshot-pilot/receipt.json').read_text())
        r['helper_sha256']=old['helper_sha256']
        for group in r['groups']:
            self.save(self.out/f"shard-{group['index']:02d}/source-readback.json",dict(r['source_sha256'],**r['helper_sha256']))
        r['plan'].update(mode='optimizeN11max',max_simultaneous_vms=2,cumulative_allocation_seconds=1800)
        b=Reservations()
        for pair in range(4):
            for j in range(2):b.admit(r['groups'][pair*2+j]['index'])
            for j in range(2):
                g=r['groups'][pair*2+j]
                g.update(start_offset_seconds=pair*4+1,end_offset_seconds=pair*4+4,lifetime_seconds=780)
                b.finish(g['index'],3,True)
        r.update(reservation_history=b.history,completed_upper_bound_vm_seconds=b.spent)
        self.save(self.out/'receipt.json',r)

    def run_summary(self):return parallel_summary(self.out,self.evidence)

    def test_parallel_parity_and_budget_qualify(self):
        r=self.run_summary()
        self.assertTrue(r['qualified'])
        self.assertEqual(r['observed_peak_vm_intervals'],2)
        self.assertEqual(r['overlapping_vm_interval_seconds'],12)
        self.assertEqual(r['complete_report_matches_serial_N11'],24)

    def test_changed_cpu_or_timeout_is_rejected(self):
        path=self.out/'receipt.json';r=json.loads(path.read_text())
        r['plan']['cpu']=4;self.save(path,r)
        with self.assertRaises(ValueError):self.run_summary()

    def test_fake_parallelism_is_rejected(self):
        path=self.out/'receipt.json';r=json.loads(path.read_text())
        for i,g in enumerate(r['groups']):g.update(start_offset_seconds=i*4+1,end_offset_seconds=i*4+4)
        self.save(path,r)
        with self.assertRaises(ValueError):self.run_summary()

    def test_reservation_overrun_is_rejected(self):
        path=self.out/'receipt.json';r=json.loads(path.read_text())
        r['reservation_history'][0]['reserved_total_seconds']=1801;self.save(path,r)
        with self.assertRaises(ValueError):self.run_summary()

    def test_completed_mismatched_gold_report_is_retained_but_not_qualified(self):
        iid=next(iter(self.reports))
        self.reports[iid]['resolved']=False
        self.save(self.out/'reports.json',self.reports)
        receipt=json.loads((self.out/'receipt.json').read_text())
        group=next(g for g in receipt['groups'] if iid in g['instance_ids'])
        path=self.out/f"shard-{group['index']:02d}/output/reports.json"
        partial=json.loads(path.read_text());partial[iid]=self.reports[iid];self.save(path,partial)
        result=self.run_summary()
        self.assertFalse(result['qualified'])
        self.assertEqual(result['complete_report_matches_serial_N11'],23)
        self.assertEqual(result['different_serial_N11_ids'],[iid])


if __name__=='__main__':unittest.main()

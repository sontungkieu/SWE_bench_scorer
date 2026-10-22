from pathlib import Path
import unittest
from analyze_snapshot_costs import cost_partition, layers, load_observations, queue_seconds


class AnalysisTests(unittest.TestCase):
    def test_queue_is_parallel_wall_not_sum_task_times(self):
        self.assertEqual(queue_seconds([5,4,3],2),7)
        self.assertEqual(queue_seconds([],8),0)
        self.assertEqual(queue_seconds([8]*24,8),24)

    def test_partition_conserves_images_and_deduplicates_within_capacity(self):
        rows=[{'reference':str(i),'layers':[{'diff_id':'shared','uncompressed_size':3},
                {'diff_id':str(i),'uncompressed_size':2}]} for i in range(12)]
        result=cost_partition(rows,{str(i):i+1 for i in range(12)},3,cap=15)
        self.assertIsNotNone(result)
        self.assertEqual(sorted(r['reference'] for g in result for r in g['images']),sorted(str(i) for i in range(12)))
        for group in result:
            self.assertLessEqual(sum({k:v for r in group['images'] for k,v in layers(r).items()}.values()),15)

    def test_capacity_failure_returns_no_fake_solution(self):
        row={'reference':'x','layers':[{'diff_id':'x','uncompressed_size':10}]}
        self.assertIsNone(cost_partition([row],{'x':1},1,cap=9))

    def test_observations_are_only_24_historical_gold_controls(self):
        evidence=Path(__file__).resolve().parents[1]/'evidence'
        values=load_observations(evidence/'2026-10-22-sharded-snapshot-pilot/raw-evidence.tar.gz')
        self.assertEqual(len(values),24)
        for row in values.values():
            self.assertGreater(row['eval_script_seconds'],0)
            self.assertGreaterEqual(row['container_cleanup_logged_seconds'],0)
            self.assertGreater(row['task_service_logged_seconds'],row['eval_script_seconds'])


if __name__=='__main__':unittest.main()

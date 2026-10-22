import unittest
import gzip
import json
from pathlib import Path

from score_phase_analysis import framework_times,parse_log,from_archive


class PhaseTests(unittest.TestCase):
    def test_public_baseline_archive_reproduces_all_24_historical_phase_rows(self):
        folder=Path(__file__).resolve().parents[1]/'evidence/2026-10-22-n7-merged-qualification'
        rows=from_archive(folder/'baseline24-phase-logs.tar.gz')
        before=json.loads(gzip.decompress((folder/'historical-phases.json.gz').read_bytes()))
        self.assertEqual(rows,before['runs']['snapshot24']['per_case'])
        self.assertEqual(len(rows),24)
        self.assertTrue(all(r['framework_printed_timings'] for r in rows))

    def test_extracts_framework_summary_without_treating_setup_as_pytest(self):
        text='pip install took 70s\n===== 24 passed in 2.31s =====\nRan 34 tests in 0.016s\n== tests finished: 98 passed, in 1.81 seconds ===\n==== 88 passed, 2 warnings in 13.18 seconds ===='
        rows=framework_times(text)
        self.assertEqual([r['framework'] for r in rows],['pytest','unittest','sympy','pytest'])
        self.assertAlmostEqual(sum(r['seconds'] for r in rows),17.316)

    def test_decomposes_real_timestamp_phases_and_keeps_stop_delay(self):
        log='\n'.join(f'2026-10-22 00:00:{stamp} - INFO - {message}' for stamp,message in [
            ('00,000','Creating container for fixture...'),('00,100','Container for fixture created: abc'),
            ('00,200','Container for fixture started: abc'),('01,000','Eval script for fixture written'),
            ('11,000','Test runtime: 9.90 seconds'),('11,100','Attempting to stop container fixture...'),
            ('26,200','Attempting to remove container fixture...'),('26,210','Container fixture removed.')])
        row=parse_log('fixture','group',log,'=== 8 passed in 2.00s ===',b'unchanged script')
        self.assertAlmostEqual(row['container_stop_seconds'],15.1)
        self.assertAlmostEqual(row['container_remove_seconds'],.01)
        self.assertAlmostEqual(row['eval_outside_framework_printed_seconds'],7.9)
        self.assertAlmostEqual(row['task_service_seconds'],26.21)
        with self.assertRaises(ValueError):parse_log('fixture','group',log.replace('Test runtime:','missing:'))


if __name__=='__main__':unittest.main()

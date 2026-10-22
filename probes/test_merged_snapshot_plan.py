import copy
from functools import lru_cache
import gzip
import itertools
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from merged_snapshot_plan import merge_groups, plan_mode, union_bytes, weighted_merges
from analyze_snapshot_costs import queue_seconds

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    groups = [{'index': i, 'images': [{'reference': str(i), 'id': str(i), 'layers': [
        {'diff_id': 'shared', 'uncompressed_size': 3},
        {'diff_id': str(i), 'uncompressed_size': 2}]}]} for i in range(5)]
    weights = {str(i): float(i+1) for i in range(5)}
    return groups, weights


@lru_cache(None)
def real_plan(mode):
    return plan_mode(mode)


class MergePlanTests(unittest.TestCase):
    def test_shared_layer_union_and_unchanged_image_identity(self):
        groups, weights = fixture()
        merged = merge_groups(groups, [(0, 1), (2, 3, 4)], weights, 9)
        self.assertEqual([g['layer_tar_bytes'] for g in merged], [7, 9])
        self.assertEqual([r for g in merged for r in g['images']], [g['images'][0] for g in groups])
        self.assertEqual(sum(g['image_count'] for g in merged), 5)

    def test_missing_duplicate_unknown_shards_and_empty_groups_rejected(self):
        groups, weights = fixture()
        for assignments in [[(0, 1), (2, 3)], [(0, 1), (1, 2, 3, 4)],
                            [(0, 1, 2, 3, 4), ()], [(0, 1, 2, 3, 4, 8)]]:
            with self.subTest(assignments=assignments), self.assertRaises(ValueError):
                merge_groups(groups, assignments, weights, 99)

    def test_bad_weights_capacity_and_conflicting_shared_layer_sizes_rejected(self):
        groups, weights = fixture()
        assignments = [(0, 1), (2, 3, 4)]
        with self.assertRaises(ValueError): merge_groups(groups, assignments, weights, 8)
        for value in [float('nan'), float('inf'), 0, -1]:
            with self.subTest(weight=value), self.assertRaises(ValueError):
                merge_groups(groups, assignments, dict(weights, **{'0': value}), 99)
        changed = copy.deepcopy(groups)
        changed[-1]['images'][0]['layers'][0]['uncompressed_size'] = 4
        with self.assertRaisesRegex(ValueError, 'shared layer'):
            merge_groups(changed, assignments, weights, 99)

    def test_duplicate_image_and_over_100_image_merge_rejected(self):
        groups, weights = fixture()
        changed = copy.deepcopy(groups)
        changed[-1]['images'] = changed[0]['images']
        with self.assertRaisesRegex(ValueError, 'Duplicate source image'):
            merge_groups(changed, [(0, 1), (2, 3, 4)], weights, 99)
        large = [{'index': i, 'images': [{'reference': f'{i}-{j}', 'layers': []} for j in range(51)]}
                 for i in range(2)]
        weights = {r['reference']: 1. for g in large for r in g['images']}
        with self.assertRaisesRegex(ValueError, 'envelope'):
            merge_groups(large, [(0, 1)], weights, 99)

    def test_weighted_search_matches_independent_exhaustive_small_partition(self):
        groups, weights = fixture()
        actual = weighted_merges(groups, weights, n=2, cap=9)
        result = merge_groups(groups, actual, weights, 9)
        actual_score = sum(g['proxy_8_worker_lpt_seconds'] for g in result)
        # Exhaust all two-label assignments instead of using the search's DP.
        candidates = []
        for labels in itertools.product(range(2), repeat=5):
            if set(labels) != {0, 1}: continue
            rows = [[g['images'][0] for i, g in enumerate(groups) if labels[i] == label] for label in range(2)]
            if all(union_bytes(r) <= 9 for r in rows):
                candidates.append(sum(queue_seconds([weights[r['reference']] for r in group]) for group in rows))
        self.assertEqual(actual_score, min(candidates))
        with self.assertRaisesRegex(ValueError, 'No whole-shard'):
            weighted_merges(groups, weights, n=1, cap=9)

    def test_pinned_500_plans_preserve_all_image_ids_and_scientific_settings(self):
        from merged_snapshot_plan import load_inputs
        original, _, _, _, _, _ = load_inputs(ROOT)
        expected = {r['reference']: r for r in original['images']}
        for mode in ['optimizeN7merge', 'optimizeN7weighted']:
            plan = real_plan(mode)
            rows = [r for g in plan['groups'] for r in g['images']]
            self.assertEqual(len(rows), 500)
            self.assertEqual({r['reference']: r for r in rows}, expected)
            self.assertEqual(plan['shard_count'], 7)
            self.assertEqual(plan['source_provenance']['measured_tasks'], 24)
            self.assertEqual(plan['source_provenance']['imputed_tasks'], 476)
            self.assertEqual(plan['scientific_config'], {'cpu': 16, 'memory_gib': 64, 'workers': 8,
                             'test_timeout': 600, 'evaluator_or_tests_changed': False})
            self.assertFalse(plan['paid_actions_performed'])
            self.assertFalse(plan['new_snapshots_built'])
            self.assertFalse(plan['scientific_parity_qualified'])
            self.assertEqual(sum(plan['summary']['selected_24_control_group_counts']), 24)
            self.assertTrue(all(g['unique_layer_tar_gib'] <= plan['preparation_envelope']['max_layer_tar_gib'] for g in plan['groups']))

    def test_requested_merge_needs_two_new_snapshots_and_keeps_nine_control_queue(self):
        plan = real_plan('optimizeN7merge')
        self.assertEqual(plan['summary']['merged_counts'], [100, 100, 59, 48, 43, 100, 50])
        self.assertEqual(plan['summary']['new_snapshots_required'], 2)
        self.assertEqual(plan['summary']['old_snapshots_reusable_after_live_checks'], 5)
        self.assertEqual(plan['summary']['selected_24_control_queues_exceeding_8'], [3])
        for g in plan['groups']:
            self.assertEqual('reuse_candidate_snapshot_image_id' in g, not g['requires_new_snapshot'])
            self.assertNotIn('snapshot_image_id', g)  # No fake usable merged catalog.

    def test_unqualified_mode_cannot_run_collect_or_clone_old_catalog(self):
        for action in ['run', 'collect', 'prepare']:
            process = subprocess.run([sys.executable, str(ROOT/'eval_modes.py'), action,
                                      '--mode', 'optimizeN7merge'], capture_output=True, text=True)
            self.assertEqual(process.returncode, 2)
            self.assertIn('offline_plan_only', process.stderr)
            self.assertNotIn('Traceback', process.stderr)

    def test_plan_cli_is_offline_reproducible_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)/'new-plan'
            command = [sys.executable, str(ROOT/'eval_modes.py'), 'plan', '--mode',
                       'optimizeN7merge', '--out', str(out)]
            process = subprocess.run(command, capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(process.stdout)['status'], 'offline_plan_only')
            artifact = out/'merge-plan.json.gz'
            original = artifact.read_bytes()
            self.assertEqual(json.loads(gzip.decompress(original)), real_plan('optimizeN7merge'))
            process = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(process.returncode, 0)
            self.assertIn('never overwrite', process.stderr)
            self.assertEqual(artifact.read_bytes(), original)

    def test_saved_plan_matches_recomputation_and_old_modes_still_listed(self):
        for folder, mode in [('user', 'optimizeN7merge'), ('weighted', 'optimizeN7weighted')]:
            saved = json.loads(gzip.decompress((ROOT/'evidence/2026-10-22-merged-snapshot-plans'/folder/'merge-plan.json.gz').read_bytes()))
            self.assertEqual(saved, real_plan(mode))
        process = subprocess.run([sys.executable, str(ROOT/'eval_modes.py'), 'list'],
                                  capture_output=True, text=True, check=True)
        modes = json.loads(process.stdout)
        self.assertEqual(modes['optimizeN11']['catalog_shards'], 11)
        self.assertEqual(modes['optimizeN11max']['max_active_vms'], 2)
        self.assertFalse(modes['optimizeN7merge']['execution_enabled'])


if __name__ == '__main__': unittest.main()

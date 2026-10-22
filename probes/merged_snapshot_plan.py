"""Offline, content-pinned merge plans; never create or evaluate a VM.

Merge whole original N11 shards. Existing snapshots can be reused only for
singleton groups; a merged group needs a newly built and restored snapshot.
"""
from collections import defaultdict
from functools import lru_cache
import gzip
import hashlib
import json
import math
from pathlib import Path
from statistics import median

from analyze_snapshot_costs import load_observations, queue_seconds
from snapshot_catalog_worker import GIB, identity, layers_of, validate_rows
from snapshot_shards import FROZEN_CATALOG_SHA, shard_plan, verify_catalog
from sharded_pilot import CATALOG_SHA


USER_MERGES = ((0,), (1,), (2,), (6, 7), (8,), (9,), (10, 3, 4, 5))
MODES = {
    'optimizeN7merge': {'assignments': USER_MERGES, 'layer_cap_gib': 96},
    'optimizeN7weighted': {'assignments': None, 'layer_cap_gib': 64},
}


def union_bytes(rows):
    return sum(layer['uncompressed_size'] for layer in layers_of(rows).values())


def merge_groups(groups, assignments, weights, cap):
    """Validate exact coverage, capacity, layer consistency and weight inputs."""
    indexed = {g['index']: g for g in groups}
    flat = [i for assignment in assignments for i in assignment]
    if (len(indexed) != len(groups) or not assignments or any(not a for a in assignments)
            or len(flat) != len(set(flat)) or set(flat) != set(indexed)):
        raise ValueError('Every source shard must occur exactly once')
    originals = [r for g in groups for r in g['images']]
    refs = [r['reference'] for r in originals]
    if len(refs) != len(set(refs)):
        raise ValueError('Duplicate source image coverage')
    # Check shared-layer sizes across ALL groups, not only inside each merge.
    layers_of(originals)
    if set(weights) != set(refs) or any(not math.isfinite(w) or w <= 0 for w in weights.values()):
        raise ValueError('Every image needs one finite positive runtime weight')
    result = []
    for index, assignment in enumerate(assignments):
        rows = [r for i in assignment for r in indexed[i]['images']]
        size = union_bytes(rows)
        if len(rows) > 100 or size > cap:
            raise ValueError('Merged shard exceeds explicit image/layer envelope')
        result.append({'index': index, 'source_shard_indices': list(assignment),
                       'image_count': len(rows), 'images': rows,
                       'layer_tar_bytes': size, 'unique_layer_tar_gib': size / GIB,
                       'requires_new_snapshot': len(assignment) > 1,
                       'proxy_8_worker_lpt_seconds': queue_seconds([weights[r['reference']] for r in rows])})
    return result


def weighted_merges(groups, weights, n=7, cap=64*GIB):
    """Exact partition search for a WEAK proxy, restricted to whole old shards.

    Minimize sum of 8-worker LPT queue proxies at fixed N. Ties favor fewer new
    builds, less layer replication, then smaller peak size. This is neither a
    measured cost optimum nor an instruction to change harness dispatch order.
    """
    if len(groups) > 11 or n < 1 or n > len(groups):
        raise ValueError('Bounded whole-shard search requires 1 <= N <= shards <= 11')
    singletons = [(g['index'],) for g in groups]
    # Validate inputs independently of whether a feasible partition is found.
    merge_groups(groups, singletons, weights, cap=float('inf'))
    valid = {}
    for mask in range(1, 1 << len(groups)):
        members = [g for i, g in enumerate(groups) if mask & (1 << i)]
        rows = [r for g in members for r in g['images']]
        if len(rows) > 100:
            continue
        size = union_bytes(rows)
        if size <= cap:
            valid[mask] = (queue_seconds([weights[r['reference']] for r in rows]),
                           int(len(members) > 1), size, size)

    @lru_cache(None)
    def solve(mask, count):
        if not mask:
            return ((0., 0, 0, 0), ()) if count == 0 else None
        if count <= 0 or mask.bit_count() < count:
            return None
        anchor = mask & -mask
        subset = mask
        best = None
        while subset:
            if subset & anchor and subset in valid:
                tail = solve(mask ^ subset, count - 1)
                if tail is not None:
                    own, rest = valid[subset], tail[0]
                    score = (own[0] + rest[0], own[1] + rest[1],
                             own[2] + rest[2], max(own[3], rest[3]))
                    candidate = (score, (subset,) + tail[1])
                    if best is None or candidate < best:
                        best = candidate
            subset = (subset - 1) & mask
        return best

    answer = solve((1 << len(groups)) - 1, n)
    if answer is None:
        raise ValueError('No whole-shard partition fits the requested N/envelope')
    return [tuple(g['index'] for i, g in enumerate(groups) if mask & (1 << i))
            for mask in answer[1]]


def load_inputs(root):
    evidence = root / 'evidence'
    source = evidence / '2026-10-22-verified-snapshots-90d'
    original_raw = gzip.decompress((source / 'catalog-plan.json.gz').read_bytes())
    catalog_raw = (source / 'usable-shards-catalog.json').read_bytes()
    if hashlib.sha256(original_raw).hexdigest() != FROZEN_CATALOG_SHA:
        raise ValueError('Changed frozen OCI manifest')
    if hashlib.sha256(catalog_raw).hexdigest() != CATALOG_SHA:
        raise ValueError('Changed frozen N11 snapshot catalog')
    original, catalog = json.loads(original_raw), json.loads(catalog_raw)
    validate_rows(original['images'])
    source_plan = shard_plan(original)
    verify_catalog(source_plan, catalog['shards'])
    if len(source_plan['groups']) != 11:
        raise ValueError('Expected original N11 layout')
    features_path = evidence / '2026-10-22-optimizeN11max/task-features.json'
    observations_path = evidence / '2026-10-22-sharded-snapshot-pilot/raw-evidence.tar.gz'
    features = json.loads(features_path.read_text())
    tasks = {r['instance_id']: r for r in features['tasks']}
    refs = {identity(r['reference']): r['reference'] for r in original['images']}
    if (features['dataset_sha256'] != original['dataset']['sha256']
            or len(tasks) != len(features['tasks']) or set(tasks) != set(refs)):
        raise ValueError('Dataset feature inventory differs from pinned images')
    observations = load_observations(observations_path)
    if not set(observations) <= set(tasks):
        raise ValueError('Timing observations reference unknown tasks')
    samples = defaultdict(list)
    for iid, row in observations.items():
        value = row['task_service_logged_seconds']
        if not math.isfinite(value) or value <= 0:
            raise ValueError('Invalid observed runtime')
        samples[tasks[iid]['repo']].append(value)
    if set(samples) != {r['repo'] for r in tasks.values()}:
        raise ValueError('Missing repo runtime prior')
    weights = {refs[iid]: (observations[iid]['task_service_logged_seconds'] if iid in observations
                           else median(samples[task['repo']])) for iid, task in tasks.items()}
    provenance = {'original_catalog_sha256': FROZEN_CATALOG_SHA,
                  'source_N11_catalog_sha256': CATALOG_SHA,
                  'features_sha256': hashlib.sha256(features_path.read_bytes()).hexdigest(),
                  'observations_archive_sha256': hashlib.sha256(observations_path.read_bytes()).hexdigest(),
                  'measured_tasks': len(observations), 'imputed_tasks': 500-len(observations),
                  'imputation': 'per-repo median historical task-service wall; gold patches only',
                  'repo_measured_sample_counts': {k: len(v) for k, v in sorted(samples.items())}}
    return original, catalog, source_plan['groups'], weights, observations, provenance


def plan_mode(mode, root=None):
    root = Path(root) if root else Path(__file__).resolve().parents[1]
    config = MODES[mode]
    original, catalog, groups, weights, observations, provenance = load_inputs(root)
    assignments = config['assignments']
    if assignments is None:
        assignments = weighted_merges(groups, weights, cap=config['layer_cap_gib'] * GIB)
    merged = merge_groups(groups, assignments, weights, config['layer_cap_gib'] * GIB)
    old = {s['index']: s for s in catalog['shards']}
    for group in merged:
        group['measured_task_count'] = sum(identity(r['reference']) in observations for r in group['images'])
        group['frozen_24_control_count'] = group['measured_task_count']
        if not group['requires_new_snapshot']:
            shard = old[group['source_shard_indices'][0]]
            group['reuse_candidate_snapshot_image_id'] = shard['snapshot_image_id']
            group['reuse_before'] = shard['use_before']
            # Eligibility still requires live expiry/read-back checks at execution.
            group['reuse_requires_live_validation'] = True
    old_queue = sum(queue_seconds([weights[r['reference']] for r in g['images']]) for g in groups)
    new_queue = sum(g['proxy_8_worker_lpt_seconds'] for g in merged)
    return {'schema': 1, 'mode': mode, 'status': 'offline_plan_only',
            'runtime': 'vm', 'image_count': 500, 'shard_count': len(merged),
            'dataset': original['dataset'], 'runtime_image_id': original['runtime_image_id'],
            'snapshot_ttl_seconds': original['snapshot_ttl_seconds'],
            'source_provenance': provenance,
            'preparation_envelope': {'max_images': 100, 'max_layer_tar_gib': config['layer_cap_gib'],
                'not_ram_or_provider_limit_or_billable_storage': True},
            'scientific_config': {'cpu': 16, 'memory_gib': 64, 'workers': 8, 'test_timeout': 600,
                                  'evaluator_or_tests_changed': False},
            'objective': 'reduce sum allocated VM-seconds; not equalize VM finish times',
            'weighted_search_scope': 'whole N11 shards; fixed N; LPT time proxy, then build-count/bytes ties',
            'groups': merged,
            'summary': {'source_counts': [len(g['images']) for g in groups],
                'merged_counts': [g['image_count'] for g in merged],
                'new_snapshots_required': sum(g['requires_new_snapshot'] for g in merged),
                'old_snapshots_reusable_after_live_checks': sum(not g['requires_new_snapshot'] for g in merged),
                'selected_24_control_group_counts': [g['frozen_24_control_count'] for g in merged],
                'selected_24_control_queues_exceeding_8': [g['index'] for g in merged if g['frozen_24_control_count'] > 8],
                'layer_tar_gib_sum_N11': sum(union_bytes(g['images']) for g in groups) / GIB,
                'layer_tar_gib_sum_merged': sum(g['layer_tar_bytes'] for g in merged) / GIB,
                'proxy_sum_test_vm_seconds_N11': old_queue,
                'proxy_sum_test_vm_seconds_merged': new_queue,
                'proxy_vm_seconds_at_startup_scenarios': {
                    str(s): {'N11': old_queue + 11*s, 'merged': new_queue + len(merged)*s}
                    for s in (5, 15, 30)}},
            'limitations': ['24 observed gold tasks; 476 imputed durations',
                'LPT proxy is not actual harness dispatch or measured full500 time',
                'No cold-read I/O, restore-time, creation-time or billing prediction',
                'Unchanged snapshot IDs require live retention and integrity validation',
                'Merged Docker stores require a new build from pinned OCI cache, not copying store directories'],
            'new_snapshots_built': False, 'scientific_parity_qualified': False,
            'full_500_evaluated': False, 'paid_actions_performed': False,
            'next_required_steps': ['authorize bounded snapshot build/qualification budget',
                'build merged groups remotely; verify every image ID and layer chain after restore',
                'create a new immutable qualified catalog and per-snapshot manifest hashes',
                'wire the new catalog into a bounded 24-control runner; preserve N11 catalog pin',
                'support queues larger than eight controls without dropping tasks or increasing worker concurrency',
                'compare all official reports and provider billing; no test or score exceptions']}

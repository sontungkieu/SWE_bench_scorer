"""Grade only an assigned subset of the original frozen 24 gold controls."""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, '/work')
PILOT_SHA = '47b545950e6fc204b2d028573298f4ee65a9497c035012bfe9ffe709c91ddbbf'
PREDS_SHA = 'aa914e991bbc328e9d1f8b8521e664cbe0c061b34fac60d94bfbdbd715fbc42f'


def select_inputs(data, predictions, assignment):
    data_ids = {r['instance_id'] for r in data}
    pred_ids = {r['instance_id'] for r in predictions}
    selected = assignment['instance_ids']
    if (len(data) != 24 or len(predictions) != 24 or len(data_ids) != 24
            or data_ids != pred_ids or not 1 <= len(selected) <= 8
            or len(set(selected)) != len(selected) or set(selected) - data_ids):
        raise ValueError('Only a unique assigned subset of the frozen 24 is allowed')
    by_id = {r['instance_id']: r for r in data}
    if any(p['model_patch'] != by_id[p['instance_id']]['patch'] for p in predictions):
        raise ValueError('Not the original gold controls')
    return [p for p in predictions if p['instance_id'] in set(selected)]


def main():
    import remote_worker as worker
    from core import summarize
    from snapshot_catalog_worker import verify_images
    if importlib.metadata.version('swebench') != '4.1.0':
        raise ValueError('Unexpected harness')
    for name, expected in [('pilot.json', PILOT_SHA), ('preds.jsonl', PREDS_SHA)]:
        if hashlib.sha256((Path('/work') / name).read_bytes()).hexdigest() != expected:
            raise ValueError('Frozen input bytes changed')
    if any(name in os.environ for name in ['REGISTRY_USERNAME', 'REGISTRY_PASSWORD', 'DOCKERHUB_TOKEN']):
        raise ValueError('Registry credentials must not enter a scoring VM')
    data = json.loads(Path('/work/pilot.json').read_text())
    predictions = [json.loads(line) for line in Path('/work/preds.jsonl').read_text().splitlines()]
    assignment = json.loads(Path('/work/assignment.json').read_text())
    rows = select_inputs(data, predictions, assignment)
    snapshot_plan = json.loads(Path('/work/catalog-plan.json').read_text())
    manifest = json.loads(Path('/snapshot-catalog-manifest.json').read_text())
    plan_sha = hashlib.sha256(Path('/work/catalog-plan.json').read_bytes()).hexdigest()
    if (plan_sha != assignment['snapshot_plan_sha256'] or plan_sha != manifest['plan_sha256']
            or snapshot_plan['shard_index'] != assignment['index']
            or manifest['tests_run'] or manifest['predictions_present']):
        raise ValueError('Restored shard provenance differs')
    checked = verify_images(snapshot_plan['images'])
    indexed = {r['instance_id']: r for r in checked}
    for expected in assignment['images']:
        if indexed.get(expected['instance_id']) != expected:
            raise ValueError('Assigned instance image differs from baseline')
    worker.OUT.mkdir(exist_ok=False)
    worker.write_json(worker.OUT / 'restored-images.json', checked)
    worker.write_json(worker.OUT / 'environment.json', {
        'harness': importlib.metadata.version('swebench'), 'python': sys.version,
        'workers': 8, 'test_timeout': 600, 'selected_count': len(rows),
        'frozen_pilot_count': 24, 'mock_gold_only': True, 'full_500_evaluated': False,
        'image_cache_volume_attached': False, 'registry_secrets_injected': False,
        'no_preexisting_test_containers': True, 'shard_index': assignment['index']})
    config = SimpleNamespace(workers=8, test_timeout=600, mode='smoke', cache=None, require_cached=True)
    started = time.monotonic()
    label = f"shard-{assignment['index']:02d}"
    result = worker.evaluate(rows, assignment['instance_ids'], Path('/work/pilot.json'), label, config)
    reports = {}
    for path in (worker.OUT / label).glob('logs/**/report.json'):
        value = json.loads(path.read_text())
        if set(value) & reports.keys():
            raise ValueError('Duplicate official report')
        reports.update(value)
    if set(reports) != set(assignment['instance_ids']):
        raise ValueError('Missing or extra report; do not classify infrastructure errors as model failures')
    images = json.loads((worker.OUT / label / 'images.json').read_text())
    if any(row['source'] != 'local' or row['cache_added_bytes'] != 0 for row in images):
        raise ValueError('Image loading fallback is forbidden')
    worker.execute(['docker', 'image', 'rm', *[r['reference'] for r in images]], worker.OUT / 'cleanup.log')
    if subprocess.check_output(['docker', 'ps', '-aq']).strip():
        raise ValueError('Harness left a test container')
    worker.write_json(worker.OUT / 'reports.json', reports)
    summary = summarize(rows, reports, assignment['instance_ids'])
    summary.update(harness_exit_code=result['harness_exit_code'], test_and_cleanup_seconds=time.monotonic()-started)
    worker.write_json(worker.OUT / 'summary.json', summary)
    return 0 if summary['complete'] and result['harness_exit_code'] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())

"""N7-only frozen-control queue with passive Docker/host timing evidence.

Official evaluator and tests are unchanged. Unlike the old N11 worker, this
worker admits a queue of up to 24 controls, still with exactly eight workers.
"""
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

from sharded_pilot_worker import PILOT_SHA, PREDS_SHA, select_inputs


def select_queue(data, predictions, assignment):
    selected = assignment['instance_ids']
    if not 1 <= len(selected) <= 24 or len(set(selected)) != len(selected):
        raise ValueError('N7 queue must contain 1..24 unique frozen controls')
    # Reuse the strict gold/input validator for each validation chunk only.
    # Execution is ONE official harness queue, not these validation chunks.
    for offset in range(0, len(selected), 8):
        select_inputs(data, predictions, dict(assignment, instance_ids=selected[offset:offset+8]))
    return [row for row in predictions if row['instance_id'] in set(selected)]


def host_stats():
    paths = ['/proc/stat', '/proc/vmstat', '/proc/diskstats', '/proc/pressure/cpu',
             '/proc/pressure/io', '/proc/pressure/memory', '/sys/fs/cgroup/cpu.stat']
    values = {p: Path(p).read_text() for p in paths if Path(p).is_file()}
    cpu = Path('/proc/cpuinfo').read_text()
    return {'monotonic': time.monotonic(), 'epoch': time.time(), 'cpu_count': os.cpu_count(),
            'cpu_models': sorted({line.split(':', 1)[1].strip() for line in cpu.splitlines()
                                  if line.startswith('model name')}), 'counters': values,
            'interpretation': 'guest counters; not provider physical I/O or CPU billing'}


def main():
    import remote_worker as worker
    from core import summarize
    from snapshot_catalog_worker import verify_images
    if importlib.metadata.version('swebench') != '4.1.0':
        raise ValueError('Unexpected harness')
    for name, expected in [('pilot.json', PILOT_SHA), ('preds.jsonl', PREDS_SHA)]:
        if hashlib.sha256((Path('/work')/name).read_bytes()).hexdigest() != expected:
            raise ValueError('Frozen input bytes changed')
    if any(k in os.environ for k in ['REGISTRY_USERNAME', 'REGISTRY_PASSWORD', 'DOCKERHUB_TOKEN']):
        raise ValueError('Registry credentials must not enter grading VM')
    data = json.loads(Path('/work/pilot.json').read_text())
    predictions = [json.loads(s) for s in Path('/work/preds.jsonl').read_text().splitlines()]
    assignment = json.loads(Path('/work/assignment.json').read_text())
    rows = select_queue(data, predictions, assignment)
    snapshot_plan = json.loads(Path('/work/catalog-plan.json').read_text())
    manifest = json.loads(Path('/snapshot-catalog-manifest.json').read_text())
    plan_sha = hashlib.sha256(Path('/work/catalog-plan.json').read_bytes()).hexdigest()
    if (plan_sha != assignment['snapshot_plan_sha256'] or plan_sha != manifest['plan_sha256']
            or snapshot_plan['shard_index'] != assignment['snapshot_shard_index']
            or manifest['tests_run'] or manifest['predictions_present']):
        raise ValueError('Snapshot provenance differs')
    worker.OUT.mkdir(exist_ok=False)
    verification_started = time.monotonic()
    checked = verify_images(snapshot_plan['images'])
    verification_seconds = time.monotonic()-verification_started
    indexed = {r['instance_id']: r for r in checked}
    for row in assignment['images']:
        if indexed.get(row['instance_id']) != row:
            raise ValueError('Selected image differs from frozen baseline')
    worker.write_json(worker.OUT/'restored-images.json', checked)
    worker.write_json(worker.OUT/'environment.json', {
        'harness': importlib.metadata.version('swebench'), 'python': sys.version,
        'workers': 8, 'cpu_request': 16, 'memory_gib': 64, 'test_timeout': 600,
        'selected_count': len(rows), 'queue_waves_upper_bound': math.ceil(len(rows)/8),
        'full_500_evaluated': False, 'mock_gold_only': True,
        'image_cache_volume_attached': False, 'registry_secrets_injected': False,
        'metadata_verification_seconds': verification_seconds,
        'passive_events_only': True, 'benchmark_tests_or_clock_modified': False})
    before = host_stats()
    worker.write_json(worker.OUT/'host-before.json', before)
    event_file = (worker.OUT/'docker-events.jsonl').open('w')
    error_file = (worker.OUT/'docker-events-stderr.txt').open('w')
    events = subprocess.Popen(['docker', 'events', '--since', str(int(before['epoch'])),
                              '--format', '{{json .}}'], stdout=event_file, stderr=error_file)
    config = SimpleNamespace(workers=8, test_timeout=600, mode='smoke', cache=None, require_cached=True)
    started = time.monotonic()
    label = f"n7-shard-{assignment['index']:02d}"
    try:
        result = worker.evaluate(rows, assignment['instance_ids'], Path('/work/pilot.json'), label, config)
        harness_wrapper_seconds = time.monotonic()-started
        reports = {}
        for path in (worker.OUT/label).glob('logs/**/report.json'):
            value = json.loads(path.read_text())
            if set(value) & reports.keys(): raise ValueError('Duplicate report')
            reports.update(value)
        if set(reports) != set(assignment['instance_ids']):
            raise ValueError('Missing/extra reports are infrastructure errors')
        images = json.loads((worker.OUT/label/'images.json').read_text())
        if any(r['source'] != 'local' or r['cache_added_bytes'] != 0 for r in images):
            raise ValueError('Image loading fallback forbidden')
        cleanup_started = time.monotonic()
        worker.execute(['docker', 'image', 'rm', *[r['reference'] for r in images]], worker.OUT/'cleanup.log')
        cleanup_seconds = time.monotonic()-cleanup_started
        if subprocess.check_output(['docker', 'ps', '-aq']).strip():
            raise ValueError('Remaining test container')
        worker.write_json(worker.OUT/'reports.json', reports)
        summary = summarize(rows, reports, assignment['instance_ids'])
        summary.update(harness_exit_code=result['harness_exit_code'],
                       harness_wrapper_seconds=harness_wrapper_seconds,
                       image_cleanup_seconds=cleanup_seconds,
                       test_and_cleanup_seconds=time.monotonic()-started)
        worker.write_json(worker.OUT/'summary.json', summary)
        return 0 if summary['complete'] and result['harness_exit_code'] == 0 else 1
    finally:
        worker.write_json(worker.OUT/'host-after.json', host_stats())
        events.terminate()
        try: events.wait(timeout=5)
        except subprocess.TimeoutExpired:
            events.kill(); events.wait()
        event_file.close(); error_file.close()


if __name__ == '__main__': raise SystemExit(main())

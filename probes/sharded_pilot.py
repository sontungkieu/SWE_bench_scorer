"""Bounded 24-case evaluation from the existing 90-day VM snapshot catalog."""
import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time

from snapshot_pilot import PILOT_SHA, PREDS_SHA, dump, execute, guard, stream, validate_inputs
from summarize_snapshot_pilot import compare

CATALOG_SHA = '6d6bb668130943a891fa0d9add90272398344d6588e3a1c6e0eb55c61cdadddb'
RATE = 16 * .00003942 + 64 * .00000667
DEADLINE = 1800
ESTIMATE = 2


def make_plan(catalog, selection, image_plan, now=None):
    now = now or datetime.now(timezone.utc)
    selected = [r['instance_id'] for r in selection['selected']]
    if (len(selected) != 24 or len(set(selected)) != 24 or not selection['gold_control_only']
            or selection['pilot_sha256'] != PILOT_SHA or selection['predictions_sha256'] != PREDS_SHA):
        raise ValueError('Only the frozen 24 gold controls may be scheduled')
    baseline = {r['instance_id']: r for r in image_plan}
    if len(image_plan) != 24 or set(baseline) != set(selected):
        raise ValueError('Baseline image coverage differs')
    mapping = {}
    for shard in catalog['shards']:
        if not shard['verified'] or not shard['all_vms_terminated']:
            raise ValueError('Unverified catalog shard')
        if datetime.fromisoformat(shard['use_before']) <= now + timedelta(seconds=DEADLINE):
            raise ValueError('Snapshot expires before this bounded evaluation finishes')
        for row in shard['images']:
            iid = row['instance_id']
            if iid in mapping:
                raise ValueError('Duplicate catalog instance')
            mapping[iid] = (shard, row)
    if len(mapping) != 500 or catalog['image_count'] != 500 or not catalog['complete']:
        raise ValueError('Incomplete frozen catalog')
    groups = {}
    for iid in selected:
        shard, row = mapping[iid]
        expected = baseline[iid]
        if row['reference'] != expected['reference'] or row['id'] != expected['id']:
            raise ValueError('Catalog image differs from frozen baseline')
        group = groups.setdefault(shard['index'], {'index': shard['index'],
            'snapshot_image_id': shard['snapshot_image_id'], 'instance_ids': [], 'images': []})
        group['instance_ids'].append(iid)
        group['images'].append({k: expected[k] for k in ['instance_id', 'reference', 'id']})
    if any(len(g['instance_ids']) > 8 for g in groups.values()):
        raise ValueError('Group exceeds original 8-worker concurrency')
    return {'schema': 1, 'selected_count': 24, 'full_500_evaluated': False,
            'pilot_sha256': PILOT_SHA, 'predictions_sha256': PREDS_SHA,
            'catalog_sha256': CATALOG_SHA, 'groups': [groups[i] for i in sorted(groups)],
            'cpu': 16, 'memory_gib': 64, 'workers': 8, 'test_timeout': 600,
            'overall_deadline_seconds': DEADLINE, 'allocation_bound_usd': RATE * DEADLINE,
            'estimate_usd': ESTIMATE, 'max_simultaneous_vms': 1,
            'batching_changed_from_original_3x8': True}


def run(out, source, baseline):
    import fcntl
    import modal
    if os.environ.get('MODAL_PROFILE') != 'phamvanvuhoan' or modal.__version__ != '1.6.2':
        raise ValueError('Requires authorized profile and qualified SDK')
    if (out / 'receipt.json').exists():
        raise ValueError('Run already started; no automatic paid retry')
    selection = validate_inputs(out)
    validate_inputs(baseline)
    raw_catalog = (out / 'usable-shards-catalog.json').read_bytes()
    if hashlib.sha256(raw_catalog).hexdigest() != CATALOG_SHA:
        raise ValueError('Only the qualified frozen catalog is allowed')
    catalog = json.loads(raw_catalog)
    image_plan = json.loads((out / 'image-plan.json').read_text())
    plan = make_plan(catalog, selection, image_plan)
    # Original selected IDs must also be bound to the checksum-verified input bytes.
    if {r['instance_id'] for r in json.loads((out / 'pilot.json').read_text())} != {
            r['instance_id'] for r in selection['selected']}:
        raise ValueError('Selection detached from frozen input')
    dump(out / 'plan.json', plan)
    here = Path(__file__).resolve().parent
    expected = json.loads((baseline / 'source-provenance.json').read_text())['vm_sources_read_from_running_sandbox_sha256']
    if {name: hashlib.sha256((source/name).read_bytes()).hexdigest() for name in expected} != expected:
        raise ValueError('Evaluator source differs from baseline')
    helpers = ['sharded_pilot_worker.py', 'snapshot_catalog_worker.py']
    helper_hashes = {n: hashlib.sha256((here/n).read_bytes()).hexdigest() for n in helpers}
    lock = (Path(os.environ['SWE_WEB_STATE'])/'image-cache.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    guard(source, out.name, ESTIMATE)
    guard(source, out.name, ESTIMATE, 'planned')
    record = {'started_at': datetime.now(timezone.utc).isoformat(), 'profile': 'phamvanvuhoan',
              'plan': plan, 'source_sha256': expected, 'helper_sha256': helper_hashes,
              'source_context': json.loads((out / 'source-context.json').read_text()),
              'image_cache_volume_attached': False, 'registry_secrets_injected': False,
              'full_500_evaluated': False, 'groups': [], 'terminated_vm_ids': []}
    started = time.monotonic(); deadline = started + DEADLINE
    active = None; all_reports = {}
    app = modal.App('swebench-sharded-snapshot-24-score')
    def persist():
        dump(out / 'receipt.json', record)
    try:
        with modal.enable_output(), app.run():
            record['app_id'] = app.app_id
            guard(source, out.name, ESTIMATE, 'running', app.app_id)
            persist()
            try:
                for group in plan['groups']:
                    left = math.floor(deadline - time.monotonic()) - 30
                    if left < 720:
                        raise RuntimeError('Remaining allocation cannot accommodate unchanged 600-second test timeout')
                    lifetime = min(780, left)
                    case = out / f"shard-{group['index']:02d}"
                    case.mkdir()
                    assignment = dict(group)
                    # Hash came from this shard's retained build manifest, before restoring.
                    assignment['snapshot_plan_sha256'] = json.loads((out/'snapshot-plan-hashes.json').read_text())[str(group['index'])]
                    dump(case/'assignment.json', assignment)
                    t = time.monotonic()
                    active = modal.Sandbox.create('dockerd', '--host=unix:///var/run/docker.sock', '--storage-driver=overlay2',
                        app=app, image=modal.Image.from_id(group['snapshot_image_id']), runtime='vm',
                        cpu=(16,16), memory=(65536,65536), timeout=lifetime,
                        readiness_probe=modal.Probe.with_exec('docker','info',interval_ms=1000))
                    row = dict(group, vm_id=active.object_id, lifetime_seconds=lifetime,
                               started_at=datetime.now(timezone.utc).isoformat())
                    record['groups'].append(row); persist()
                    try:
                        active.wait_until_ready(timeout=90)
                        row['docker_ready_seconds'] = time.monotonic()-t
                        for name in expected:
                            active.filesystem.copy_from_local(str(source/name), '/work/'+name)
                        for name in helpers:
                            active.filesystem.copy_from_local(str(here/name), '/work/'+name)
                        for name in ['pilot.json','preds.jsonl']:
                            active.filesystem.copy_from_local(str(out/name), '/work/'+name)
                        active.filesystem.copy_from_local(str(case/'assignment.json'), '/work/assignment.json')
                        names = list(expected) + helpers
                        actual = json.loads(execute(active, 'python', '-c',
                            'import hashlib,json,pathlib; names='+repr(names)+'; '
                            'print(json.dumps({n:hashlib.sha256((pathlib.Path("/work")/n).read_bytes()).hexdigest() for n in names}))'))
                        if actual != dict(expected, **helper_hashes):
                            raise ValueError('Source readback differs on restored VM')
                        dump(case/'source-readback.json', actual)
                        budget = min(650, lifetime-math.ceil(time.monotonic()-t)-35)
                        if budget < 610:
                            raise RuntimeError('Startup consumed timeout reserve; no shortened test protocol')
                        stream(active, ['python','-u','/work/sharded_pilot_worker.py'], case/'console.log', budget)
                        row['worker_passed'] = True
                    finally:
                        try:
                            execute(active, 'tar','-czf','/tmp/sharded-score-output.tar.gz','-C','/work','output',timeout=30)
                            active.filesystem.copy_to_local('/tmp/sharded-score-output.tar.gz',str(case/'artifacts.tar.gz'))
                            with tarfile.open(case/'artifacts.tar.gz') as archive:
                                archive.extractall(case, filter='data')
                        except Exception as exc:
                            row['artifact_error'] = type(exc).__name__+': '+str(exc)
                        finally:
                            active.terminate(wait=True)
                            row['terminated'] = True
                            record['terminated_vm_ids'].append(active.object_id)
                            active = None
                            row['wall_seconds'] = time.monotonic()-t
                            persist()
                    if row.get('artifact_error'):
                        raise RuntimeError('Output collection failed')
                    found = json.loads((case/'output/reports.json').read_text())
                    if set(found) != set(group['instance_ids']) or set(found) & all_reports.keys():
                        raise ValueError('Wrong or duplicated group report coverage')
                    all_reports.update(found)
                    dump(out/'reports.json', all_reports)
                    print('SHARDED_PILOT_PROGRESS='+json.dumps({'completed':len(all_reports),'requested':24,
                          'groups_completed':len(record['groups']),'groups_requested':len(plan['groups'])}),flush=True)
                before = json.loads((out/'baseline-reports.json').read_text())
                comparison = compare([r['instance_id'] for r in selection['selected']], before, all_reports)
                record['comparison'] = comparison
                record['complete'] = len(all_reports)==24 and all(g.get('worker_passed') for g in record['groups'])
                record['report_parity'] = comparison['complete_report_matches']==24 and not comparison['different_report_ids']
            finally:
                if active is not None:
                    active.terminate(wait=True)
                    record['terminated_vm_ids'].append(active.object_id)
                    active = None
                record['all_vms_terminated'] = active is None and all(g.get('terminated') for g in record['groups'])
    except Exception as exc:
        record.update(complete=False, error=type(exc).__name__+': '+str(exc))
    finally:
        record.update(finished_at=datetime.now(timezone.utc).isoformat(), wall_seconds=time.monotonic()-started)
        persist()
        guard(source, out.name, ESTIMATE, 'completed' if record.get('complete') else 'failed')
        lock.close()
    return 0 if record.get('complete') and record.get('report_parity') else 1


def collect(out, name):
    import modal
    record = json.loads((out/'receipt.json').read_text())
    remaining = [v.object_id for v in modal.Sandbox.list(app_id=record['app_id'])]
    if remaining or not record.get('all_vms_terminated'):
        raise ValueError('VM cleanup not confirmed')
    now = datetime.now(timezone.utc)
    response = subprocess.check_output([sys.executable,'-m','modal','billing','report',
        '--start',record['started_at'][:10],'--end',(now+timedelta(days=1)).strftime('%Y-%m-%d'),
        '--show-resources','--json'],text=True,timeout=90)
    rows = [r for r in json.loads(response) if r.get('object_id')==record['app_id']]
    if not rows: raise ValueError('Billing rows not available yet')
    total = sum((Decimal(str(r['cost'])) for r in rows),Decimal(0))
    result = {'checked_at':now.isoformat(),'app_id':record['app_id'],'rows':rows,
              'compute_egress_usd':str(total),'before_credits':True,'not_final_invoice':True,
              'snapshot_storage_not_in_app_sum':True,'remaining_vms':remaining}
    dump(out/name,result)
    print(json.dumps({k:v for k,v in result.items() if k!='rows'}))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['run','collect'])
    parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--source',type=Path)
    parser.add_argument('--baseline',type=Path)
    parser.add_argument('--billing-name',default='billing.json')
    args=parser.parse_args()
    if args.action=='run':
        if not args.source or not args.baseline: parser.error('run requires --source and --baseline')
        raise SystemExit(run(args.out.resolve(),args.source.resolve(),args.baseline.resolve()))
    collect(args.out.resolve(),args.billing_name)

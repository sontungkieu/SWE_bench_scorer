"""One-shard, at-most-three real predictions; unchanged official tests/evaluator.

Separate from the frozen-gold N7 qualification runner. No snapshots are built,
no automatic retry, no observer, no full-500 evaluation, no registry secrets.
"""
import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time
from types import SimpleNamespace

from snapshot_pilot import dump, execute, guard, stream

# Direct CLI execution starts with probes/ on sys.path; the VM copies this
# script beside core.py, while the coordinator keeps the repository layout.
if (Path(__file__).resolve().parent.parent/'core.py').is_file():
    sys.path.insert(0,str(Path(__file__).resolve().parent.parent))

LIFETIME = 900
ESTIMATE = 1.05
CATALOG = 'evidence/2026-10-22-n7-merged-qualification/optimizeN7merge-catalog.json'
CATALOG_SHA = '39aa9ca5302ccc42cd5ff6adabadc48976451527d8c47af11380ab378328f2c5'
DATASET_SHA = '6a6fb72484bfc40f35cecbae1ae4615e9460905f729c57659df5f4ef9d7dec6e'
EVALUATORS = ['core.py', 'remote_worker.py', 'image_cache.py', 'registry_cache.py']


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(out, source, predictions, dataset, expected_sha):
    from core import load_predictions, DATASET, REVISION
    if out.exists(): raise ValueError('Use a new output directory; no paid retry')
    if digest(predictions) != expected_sha: raise ValueError('Input changed after authorization')
    rows = load_predictions(predictions)
    if not 1 <= len(rows) <= 3: raise ValueError('Only 1..3 real predictions authorized')
    data = json.loads(dataset.read_text())
    if digest(dataset) != DATASET_SHA or len(data) != 500: raise ValueError('Wrong pinned Verified dataset')
    if digest(source/CATALOG) != CATALOG_SHA: raise ValueError('Wrong N7merge catalog')
    catalog = json.loads((source/CATALOG).read_text())
    if not catalog['complete'] or len(catalog['shards']) != 7 or catalog['image_count'] != 500:
        raise ValueError('Incomplete catalog')
    ids = [r['instance_id'] for r in rows]
    selected = [r for r in data if r['instance_id'] in ids]
    if len(selected) != len(ids): raise ValueError('Prediction ID outside Verified')
    groups = [g for g in catalog['shards'] if any(r['instance_id'] in ids for r in g['images'])]
    if len(groups) != 1: raise ValueError('This bounded runner supports one shard only')
    group = groups[0]
    if (not group['verified'] or not group['all_vms_terminated'] or
            datetime.fromisoformat(group['use_before']) <= datetime.now(timezone.utc)+timedelta(seconds=LIFETIME)):
        raise ValueError('Unverified/expired snapshot')
    images = [r for r in group['images'] if r['instance_id'] in ids]
    if len(images) != len(ids): raise ValueError('Incomplete image assignment')
    active = [r['instance_id'] for r in rows if r['model_patch'].strip()]
    if not active: raise ValueError('All patches empty: no VM necessary')
    out.mkdir(parents=True)
    (out/'preds.jsonl').write_bytes(predictions.read_bytes())
    dump(out/'dataset.json', selected)
    assignment = {k: group[k] for k in ['index','snapshot_image_id','snapshot_plan_sha256','snapshot_shard_index']}
    assignment.update(instance_ids=ids, active_ids=active, images=images,
        predictions_sha256=expected_sha, selected_dataset_sha256=digest(out/'dataset.json'),
        dataset=DATASET, dataset_revision=REVISION, full_dataset_sha256=DATASET_SHA,
        mode='optimizeN7merge', catalog_sha256=CATALOG_SHA,
        scientific_parity_qualified=False, user_authorized_real_trial=True,
        workers=8, test_timeout=600, cpu=16, memory_gib=64, lifetime=LIFETIME,
        estimate_usd=ESTIMATE, tests_or_clock_modified=False, full_500_evaluated=False)
    dump(out/'assignment.json', assignment)
    files = {n:source/n for n in EVALUATORS}
    files.update({n:Path(__file__).parent/n for n in ['n7_predictions.py','snapshot_pilot.py','snapshot_catalog_worker.py']})
    dump(out/'source-hashes.json', {n:digest(p) for n,p in files.items()})


def validate_reports(rows, reports):
    active = {r['instance_id'] for r in rows if r['model_patch'].strip()}
    if set(reports) != active:
        raise ValueError('Missing/extra report for nonempty patches: infrastructure error')
    if any(type(row.get('resolved')) is not bool for row in reports.values()):
        raise ValueError('Malformed official report')


def worker():
    from core import load_predictions, summarize, HARNESS
    import remote_worker as base
    from snapshot_catalog_worker import verify_images
    root = Path('/work'); assignment = json.loads((root/'assignment.json').read_text())
    if importlib.metadata.version('swebench') != HARNESS: raise ValueError('Wrong harness')
    for name,key in [('preds.jsonl','predictions_sha256'),('dataset.json','selected_dataset_sha256')]:
        if digest(root/name) != assignment[key]: raise ValueError('VM input hash mismatch')
    if any(k in os.environ for k in ['REGISTRY_USERNAME','REGISTRY_PASSWORD','DOCKERHUB_TOKEN']):
        raise ValueError('Registry credentials forbidden in grading VM')
    plan = json.loads((root/'catalog-plan.json').read_text())
    manifest = json.loads(Path('/snapshot-catalog-manifest.json').read_text())
    if (digest(root/'catalog-plan.json') != assignment['snapshot_plan_sha256'] or
            manifest['plan_sha256'] != assignment['snapshot_plan_sha256'] or
            plan['shard_index'] != assignment['snapshot_shard_index'] or
            manifest['tests_run'] or manifest['predictions_present']):
        raise ValueError('Wrong or contaminated snapshot')
    checked = {r['instance_id']:r for r in verify_images(plan['images'])}
    if any(checked.get(r['instance_id']) != r for r in assignment['images']):
        raise ValueError('Restored image identity differs')
    rows = load_predictions(root/'preds.jsonl')
    if len(rows)>3 or [r['instance_id'] for r in rows] != assignment['instance_ids']:
        raise ValueError('Prediction scope changed')
    base.OUT.mkdir(exist_ok=False)
    dump(base.OUT/'environment.json', dict(assignment, harness=HARNESS,
         cpu_models=sorted({s.split(':',1)[1].strip() for s in Path('/proc/cpuinfo').read_text().splitlines()
                            if s.startswith('model name')}), registry_secrets_injected=False))
    dump(base.OUT/'restored-images.json', list(checked.values()))
    started = time.monotonic()
    config = SimpleNamespace(workers=8, test_timeout=600, mode='smoke', cache=None, require_cached=True)
    result = base.evaluate(rows, assignment['instance_ids'], root/'dataset.json', 'real-predictions', config)
    reports = {}
    for path in (base.OUT/'real-predictions').glob('logs/**/report.json'):
        found = json.loads(path.read_text())
        if set(found)&reports.keys(): raise ValueError('Duplicate report')
        reports.update(found)
    validate_reports(rows, reports)
    images = json.loads((base.OUT/'real-predictions/images.json').read_text())
    if any(r['source'] != 'local' or r['cache_added_bytes'] != 0 for r in images):
        raise ValueError('Registry/image-load fallback forbidden')
    dump(base.OUT/'reports.json', reports)
    summary = summarize(rows, reports, assignment['instance_ids'])
    summary.update(harness_exit_code=result['harness_exit_code'],
        harness_wrapper_seconds=time.monotonic()-started, scientific_parity_qualified=False,
        official_tests_modified=False, real_model_predictions=True,
        empty_patch_handling='unresolved; no synthetic official report; retained in denominator')
    dump(base.OUT/'summary.json', summary)
    if subprocess.check_output(['docker','ps','-aq']).strip(): raise ValueError('Remaining test container')
    return 0 if summary['complete'] and result['harness_exit_code']==0 else 1


def run(out, source):
    import modal
    if os.environ.get('MODAL_PROFILE') != 'phamvanvuhoan' or modal.__version__ != '1.6.2':
        raise ValueError('Unauthorized profile/SDK')
    if (out/'receipt.json').exists(): raise ValueError('No automatic paid retry')
    assignment = json.loads((out/'assignment.json').read_text())
    if (assignment['mode'] != 'optimizeN7merge' or len(assignment['instance_ids'])>3 or
            assignment['lifetime'] != LIFETIME or assignment['estimate_usd'] != ESTIMATE):
        raise ValueError('Allocation contract changed')
    if digest(out/'preds.jsonl') != assignment['predictions_sha256']: raise ValueError('Predictions changed')
    files = {n:source/n for n in EVALUATORS}
    files.update({n:Path(__file__).parent/n for n in ['n7_predictions.py','snapshot_pilot.py','snapshot_catalog_worker.py']})
    expected = json.loads((out/'source-hashes.json').read_text())
    if {n:digest(p) for n,p in files.items()} != expected: raise ValueError('Source changed after prepare')
    guard(source,out.name,ESTIMATE);guard(source,out.name,ESTIMATE,'planned')
    app=modal.App('swebench-n7-real-preds-dry3');active=None;started=time.monotonic()
    receipt={'started_at':datetime.now(timezone.utc).isoformat(),'assignment':assignment,
             'source_sha256':expected,'profile':'phamvanvuhoan','max_vms':1,'no_paid_retry':True}
    try:
        with modal.enable_output(),app.run():
            receipt['app_id']=app.app_id;dump(out/'receipt.json',receipt)
            guard(source,out.name,ESTIMATE,'running',app.app_id)
            try:
                active=modal.Sandbox.create('dockerd','--host=unix:///var/run/docker.sock','--storage-driver=overlay2',
                    app=app,image=modal.Image.from_id(assignment['snapshot_image_id']),runtime='vm',
                    cpu=(16,16),memory=(65536,65536),timeout=LIFETIME,
                    readiness_probe=modal.Probe.with_exec('docker','info',interval_ms=1000))
                receipt['vm_id']=active.object_id;dump(out/'receipt.json',receipt)
                active.wait_until_ready(timeout=90)
                files.update({n:out/n for n in ['preds.jsonl','dataset.json','assignment.json']})
                expected={n:digest(p) for n,p in files.items()}
                for n,p in files.items(): active.filesystem.copy_from_local(str(p),'/work/'+n)
                actual=json.loads(execute(active,'python','-c','import hashlib,pathlib,json; names='+repr(list(files))+'; '
                    'print(json.dumps({n:hashlib.sha256((pathlib.Path("/work")/n).read_bytes()).hexdigest() for n in names}))'))
                if actual != expected: raise ValueError('Source/input readback mismatch')
                dump(out/'source-readback.json',actual)
                stream(active,['python','-u','/work/n7_predictions.py','worker'],out/'worker.log',720)
                receipt['worker_completed']=True
            finally:
                if active is not None:
                    try:
                        execute(active,'tar','-czf','/tmp/real-output.tar.gz','-C','/work','output',timeout=30)
                        active.filesystem.copy_to_local('/tmp/real-output.tar.gz',str(out/'artifacts.tar.gz'))
                        with tarfile.open(out/'artifacts.tar.gz') as archive: archive.extractall(out,filter='data')
                    except Exception as exc: receipt['artifact_error']=type(exc).__name__+': '+str(exc)
                    finally:
                        active.terminate(wait=True);receipt['all_vms_terminated']=True;active=None
    except Exception as exc: receipt['error']=type(exc).__name__+': '+str(exc)
    finally:
        receipt.update(finished_at=datetime.now(timezone.utc).isoformat(),wall_seconds=time.monotonic()-started)
        dump(out/'receipt.json',receipt)
        guard(source,out.name,ESTIMATE,'completed' if receipt.get('worker_completed') else 'failed')
    return 0 if receipt.get('worker_completed') and not receipt.get('artifact_error') else 1


def collect(out, name):
    import modal
    receipt=json.loads((out/'receipt.json').read_text());now=datetime.now(timezone.utc)
    remaining=[v.object_id for v in modal.Sandbox.list(app_id=receipt['app_id'])]
    if remaining or not receipt.get('all_vms_terminated'): raise ValueError('Cleanup not confirmed')
    response=json.loads(subprocess.check_output([sys.executable,'-m','modal','billing','report',
        '--start',receipt['started_at'][:10],'--end',(now+timedelta(days=1)).strftime('%Y-%m-%d'),
        '--show-resources','--json'],text=True,timeout=90))
    rows=[r for r in response if r.get('object_id')==receipt['app_id']]
    if not rows: raise ValueError('Billing not available yet')
    total=sum((Decimal(str(r['cost'])) for r in rows),Decimal(0))
    result={'checked_at':now.isoformat(),'app_id':receipt['app_id'],'rows':rows,
        'compute_egress_usd':str(total),'remaining_vms':remaining,'before_credits':True,
        'not_final_invoice':True,'snapshot_storage_not_in_app_sum':True}
    dump(out/name,result);print(json.dumps({k:v for k,v in result.items() if k!='rows'}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','run','worker','collect'])
    for n in ['out','source','predictions','dataset']:p.add_argument('--'+n,type=Path)
    p.add_argument('--expected-sha256');p.add_argument('--billing-name',default='billing.json');a=p.parse_args()
    if a.action=='worker':raise SystemExit(worker())
    if not a.out:p.error('--out required')
    if a.action=='collect':collect(a.out,a.billing_name)
    elif a.action=='prepare':
        if not all([a.source,a.predictions,a.dataset,a.expected_sha256]):p.error('prepare requires source/predictions/dataset/expected-sha256')
        prepare(a.out,a.source,a.predictions,a.dataset,a.expected_sha256)
    else:
        if not a.source:p.error('--source required')
        raise SystemExit(run(a.out,a.source))

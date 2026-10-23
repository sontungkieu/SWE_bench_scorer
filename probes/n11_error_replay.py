"""Optional, explicitly approved single replay of a missing official timeout report.

Never replaces a first-attempt report or retries an unresolved test result.
Original evidence remains immutable; combined accounting is saved separately.
"""
import argparse
from datetime import datetime,timezone
from decimal import Decimal
import json
import math
import os
from pathlib import Path
import sys
import tarfile
import time

from n11_predictions import EVALUATORS,HELPERS,PRIOR_COST,RATE,digest,collect
from snapshot_pilot import dump,execute,guard,stream

LIFETIME=960
ESTIMATE=Decimal('1.05')
TARGET='psf__requests-2317'


def settled_cost(first,second,app_id):
    values=[]
    for data in [first,second]:
        if data['app_id']!=app_id or data['remaining_vms'] or not data['rows']:
            raise ValueError('Wrong billing identity, missing rows or active VM')
        if any(r['object_id']!=app_id for r in data['rows']):raise ValueError('Mixed billing apps')
        costs=[Decimal(str(r['cost'])) for r in data['rows']]
        if any(not x.is_finite() or x<0 for x in costs):raise ValueError('Invalid cost')
        total=sum(costs,Decimal(0))
        if total!=Decimal(data['compute_egress_usd']):raise ValueError('Billing sum mismatch')
        values.append(total)
    gap=(datetime.fromisoformat(second['checked_at'])-datetime.fromisoformat(first['checked_at'])).total_seconds()
    if gap<60 or values[0]!=values[1]:raise ValueError('Billing not settled across separated reads')
    if PRIOR_COST+values[1]+ESTIMATE>8:raise ValueError('Replay does not fit cumulative $8 cap')
    return values[1]


def validate_missing_target(rows,reports,receipt,timeout_text):
    active={r['instance_id'] for r in rows if r['model_patch'].strip()}
    if not receipt.get('execution_complete') or not receipt.get('all_vms_terminated'):
        raise ValueError('Finish and terminate the original run first')
    if active-set(reports)!={TARGET} or set(reports)-active:
        raise ValueError('Exactly the single approved missing case required; no unresolved-result replay')
    if not timeout_text.rstrip().endswith('Timeout error: 600 seconds exceeded.'):
        raise ValueError('Missing official timeout receipt')


def prepare(out,original,source,first,second,commit):
    from core import load_predictions
    if out.exists():raise ValueError('New replay directory required; no second replay')
    read=lambda p:json.loads(p.read_text())
    receipt=read(original/'receipt.json');plan=read(original/'plan.json')
    rows=load_predictions(original/'preds.jsonl');reports=read(original/'reports.json')
    paths=list(original.glob('shard-*/output/wave-*/official/logs/**/'+TARGET+'/test_output.txt'))
    if len(paths)!=1:raise ValueError('Ambiguous first-attempt evidence')
    validate_missing_target(rows,reports,receipt,paths[0].read_text())
    cost=settled_cost(read(first),read(second),receipt['app_id'])
    files={n:source/n for n in EVALUATORS}|{n:Path(__file__).parent/n for n in HELPERS}
    if {n:digest(p) for n,p in files.items()}!=receipt['source_sha256']:
        raise ValueError('Original evaluator/worker changed')
    if digest(original/'preds.jsonl')!=plan['predictions_sha256']:raise ValueError('Original predictions changed')
    group=next(g for g in plan['groups'] if TARGET in g['instance_ids'])
    out.mkdir(parents=True)
    for n in ['preds.jsonl','dataset.json']:(out/n).write_bytes((original/n).read_bytes())
    assignment={k:group[k] for k in ['index','snapshot_image_id','snapshot_plan_sha256']}
    assignment.update(instance_ids=[TARGET],images=[r for r in group['images'] if r['instance_id']==TARGET],
                      predictions_sha256=plan['predictions_sha256'])
    dump(out/'assignment.json',assignment)
    dump(out/'contract.json',dict(original_run=str(original),original_app_id=receipt['app_id'],
        original_cost_usd=str(cost),original_reports_sha256=digest(original/'reports.json'),
        source_sha256=receipt['source_sha256'],replay_source_commit=commit,
        replay_coordinator_sha256=digest(Path(__file__)),lifetime=LIFETIME,estimate_usd=str(ESTIMATE),
        target=TARGET,explicit_run_approval_required=True,original_error_retained=True,
        protocol='first attempt plus one authorized missing-timeout-report replay',
        prior_task_cost_usd=str(PRIOR_COST),cumulative_authorized_usd='8'))


def run(out,source,approved):
    import modal
    if not approved:raise ValueError('Explicit user authorization for this one replay is required')
    if os.environ.get('MODAL_PROFILE')!='phamvanvuhoan' or modal.__version__!='1.6.2':raise ValueError('Wrong profile/SDK')
    if (out/'receipt.json').exists():raise ValueError('One replay only; no retry')
    contract=json.loads((out/'contract.json').read_text());original=Path(contract['original_run'])
    if digest(original/'reports.json')!=contract['original_reports_sha256']:raise ValueError('First attempt changed')
    if digest(Path(__file__))!=contract['replay_coordinator_sha256']:raise ValueError('Replay source changed')
    if PRIOR_COST+Decimal(contract['original_cost_usd'])+ESTIMATE>8:raise ValueError('Insufficient cumulative cap')
    assignment=json.loads((out/'assignment.json').read_text())
    if assignment['instance_ids']!=[TARGET]:raise ValueError('Wrong replay scope')
    files={n:source/n for n in EVALUATORS}|{n:Path(__file__).parent/n for n in HELPERS}
    if {n:digest(p) for n,p in files.items()}!=contract['source_sha256']:raise ValueError('Worker changed')
    guard(source,out.name,float(ESTIMATE));guard(source,out.name,float(ESTIMATE),'planned')
    app=modal.App('swebench-n11-single-missing-timeout-replay');active=None;started=time.monotonic()
    record=dict(started_at=datetime.now(timezone.utc).isoformat(),contract=contract,explicitly_authorized=True)
    try:
        with modal.enable_output(),app.run():
            record['app_id']=app.app_id;dump(out/'receipt.json',record)
            guard(source,out.name,float(ESTIMATE),'running',app.app_id)
            try:
                active=modal.Sandbox.create('dockerd','--host=unix:///var/run/docker.sock','--storage-driver=overlay2',
                    app=app,image=modal.Image.from_id(assignment['snapshot_image_id']),runtime='vm',cpu=(16,16),
                    memory=(65536,65536),timeout=LIFETIME,
                    readiness_probe=modal.Probe.with_exec('docker','info',interval_ms=1000))
                record['vm_id']=active.object_id;dump(out/'receipt.json',record)
                active.wait_until_ready(timeout=90)
                assignment['work_seconds']=math.floor(LIFETIME-(time.monotonic()-started)-75)
                dump(out/'assignment.json',assignment)
                copies=dict(files,**{n:out/n for n in ['preds.jsonl','dataset.json','assignment.json']})
                for n,p in copies.items():active.filesystem.copy_from_local(str(p),'/work/'+n)
                actual=json.loads(execute(active,'python','-c','import json,hashlib,pathlib; names='+repr(list(copies))+'; '
                    'print(json.dumps({n:hashlib.sha256((pathlib.Path("/work")/n).read_bytes()).hexdigest() for n in names}))'))
                if actual!={n:digest(p) for n,p in copies.items()}:raise ValueError('Readback failed')
                dump(out/'source-readback.json',actual)
                if assignment['work_seconds']<780:raise ValueError('Insufficient full timeout reserve')
                stream(active,['python','-u','/work/n11_predictions.py','worker'],out/'worker.log',assignment['work_seconds']+10)
                record['worker_completed']=True
            finally:
                if active is not None:
                    try:
                        execute(active,'tar','-czf','/tmp/replay-output.tar.gz','-C','/work','output',timeout=40)
                        active.filesystem.copy_to_local('/tmp/replay-output.tar.gz',str(out/'artifacts.tar.gz'))
                        with tarfile.open(out/'artifacts.tar.gz') as archive:archive.extractall(out,filter='data')
                    finally:
                        active.terminate(wait=True);record['all_vms_terminated']=True;active=None
    except Exception as exc:record['error']=type(exc).__name__+': '+str(exc)
    finally:
        record.update(finished_at=datetime.now(timezone.utc).isoformat(),wall_seconds=time.monotonic()-started)
        dump(out/'receipt.json',record)
        guard(source,out.name,float(ESTIMATE),'completed' if record.get('worker_completed') else 'failed')
    return 0 if record.get('worker_completed') and not record.get('error') else 1


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','run','collect'])
    for k in ['out','original','source','billing-first','billing-second']:p.add_argument('--'+k,type=Path)
    p.add_argument('--commit');p.add_argument('--approved-one-replay',action='store_true')
    p.add_argument('--billing-name',default='billing.json');a=p.parse_args()
    if a.action=='prepare':prepare(a.out,a.original,a.source,a.billing_first,a.billing_second,a.commit)
    elif a.action=='collect':collect(a.out,a.billing_name)
    else:raise SystemExit(run(a.out,a.source,a.approved_one_replay))

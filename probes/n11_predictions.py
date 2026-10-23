"""Official full-Verified input accounting on the existing N11 VM snapshots.

One VM at a time, up to eight tests per wave, unchanged 600s test timeout.
Finite VM lifetime bounds compute; admit a wave only with its full reserve.
No retry, new snapshot, registry fallback, test modification or instrumentation.
"""
import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time
from types import SimpleNamespace

from snapshot_pilot import dump, execute, guard, stream

if (Path(__file__).resolve().parent.parent/'core.py').is_file():
    sys.path.insert(0,str(Path(__file__).resolve().parent.parent))

CATALOG='evidence/2026-10-22-verified-snapshots-90d/usable-shards-catalog.json'
METADATA='evidence/2026-10-22-verified-snapshots-90d/raw-shards-metadata.tar.gz'
CATALOG_SHA='6d6bb668130943a891fa0d9add90272398344d6588e3a1c6e0eb55c61cdadddb'
DATASET_SHA='6a6fb72484bfc40f35cecbae1ae4615e9460905f729c57659df5f4ef9d7dec6e'
EVALUATORS=['core.py','remote_worker.py','image_cache.py','registry_cache.py']
HELPERS=['n11_predictions.py','snapshot_pilot.py','snapshot_catalog_worker.py']
RATE=Decimal('0.0010576')  # 16 requested cores + 64 GiB, Sandbox list rates.
PRIOR_COST=Decimal('1.67845737')
BUDGET=Decimal('6.30')
EGRESS_RESERVE=Decimal('0.10')
WAVE_RESERVE=780
VM_OVERHEAD=180


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_budget(budget,prior_cost,cap=Decimal('8')):
    if (not budget.is_finite() or not prior_cost.is_finite() or not cap.is_finite() or
        budget<=EGRESS_RESERVE or prior_cost<0 or cap<=0 or prior_cost+budget>cap):
        raise ValueError('Invalid budget or authorized cumulative cap exceeded')


def lifetime_for(active_count, used_seconds, budget=BUDGET):
    if not 1<=active_count<=100 or not math.isfinite(used_seconds) or used_seconds<0:
        raise ValueError('Invalid allocation accounting')
    validate_budget(budget,Decimal(0),budget)
    available=(budget-EGRESS_RESERVE)/RATE-Decimal(str(used_seconds))
    maximum=math.floor(available)-10  # Client/rounding margin beyond metered VM wall.
    if maximum<WAVE_RESERVE+VM_OVERHEAD: return None
    return min(math.ceil(active_count/8)*WAVE_RESERVE+VM_OVERHEAD,maximum)


def make_groups(rows,catalog,hashes,now=None):
    now=now or datetime.now(timezone.utc)
    if len(rows)!=500 or len({r['instance_id'] for r in rows})!=500:
        raise ValueError('Exactly all 500 predictions, including empty patches, required')
    by={r['instance_id']:r for r in rows};mapped={};groups=[]
    if not catalog['complete'] or catalog['image_count']!=500 or len(catalog['shards'])!=11:
        raise ValueError('Incomplete N11 catalog')
    for shard in sorted(catalog['shards'],key=lambda s:s['index']):
        if not shard['verified'] or not shard['all_vms_terminated']:
            raise ValueError('Unverified snapshot')
        if datetime.fromisoformat(shard['use_before'])<=now+timedelta(hours=24):
            raise ValueError('Snapshot retention insufficient')
        selected=[];images=[]
        for image in shard['images']:
            iid=image['instance_id']
            if iid in mapped or iid not in by: raise ValueError('Duplicate/outside catalog instance')
            mapped[iid]=image
            if by[iid]['model_patch'].strip():selected.append(iid);images.append(image)
        if selected:
            groups.append(dict(index=shard['index'],snapshot_image_id=shard['snapshot_image_id'],
                snapshot_plan_sha256=hashes[str(shard['index'])],instance_ids=selected,images=images))
    if set(mapped)!=set(by): raise ValueError('Prediction/catalog coverage differs')
    return groups


def compute_assignment_rows(rows,data,selected_ids=None):
    by={r['instance_id']:r for r in rows};data_ids={r['instance_id'] for r in data}
    selected=list(by) if selected_ids is None else list(selected_ids)
    if (len(data_ids)!=500 or set(by)-data_ids or not selected or
        len(selected)!=len(set(selected)) or set(selected)-set(by) or
        (selected_ids is None and set(by)!=data_ids)):
        raise ValueError('Wrong Verified source coverage or selected scope')
    # This is only an assignment mask. The prediction file is copied byte-for-byte.
    selected_set=set(selected)
    mask=[dict(instance_id=i,model_patch=by[i]['model_patch'] if i in selected_set else '') for i in data_ids]
    return selected,mask


def prepare(out,source,predictions,dataset,expected_sha,expected_nonempty,commit,budget=BUDGET,prior_cost=PRIOR_COST,cap=Decimal('8'),selected_ids=None):
    from core import load_predictions,DATASET,REVISION
    if out.exists(): raise ValueError('New output directory required; never overwrite a run')
    if digest(predictions)!=expected_sha: raise ValueError('Authorized input hash changed')
    rows=load_predictions(predictions)
    if sum(bool(r['model_patch'].strip()) for r in rows)!=expected_nonempty:
        raise ValueError('Nonempty prediction count differs')
    if digest(dataset)!=DATASET_SHA or digest(source/CATALOG)!=CATALOG_SHA:
        raise ValueError('Pinned dataset/catalog differs')
    data=json.loads(dataset.read_text());catalog=json.loads((source/CATALOG).read_text())
    selected,assignment_rows=compute_assignment_rows(rows,data,selected_ids)
    hashes={}
    with tarfile.open(source/METADATA) as archive:
        for member in archive.getmembers():
            if member.isfile() and member.name.endswith('/manifest.json'):
                index=int(Path(member.name).parent.name.split('-')[-1])
                manifest=json.load(archive.extractfile(member))
                shard=next(s for s in catalog['shards'] if s['index']==index)
                if (str(index) in hashes or manifest['tests_run'] or manifest['predictions_present'] or
                    manifest['images']!=shard['images'] or not manifest['no_test_containers']):
                    raise ValueError('Original manifest differs')
                hashes[str(index)]=manifest['plan_sha256']
    if len(hashes)!=11: raise ValueError('Missing original shard manifests')
    groups=make_groups(assignment_rows,catalog,hashes)
    active_count=sum(len(g['instance_ids']) for g in groups)
    validate_budget(budget,prior_cost,cap)
    out.mkdir(parents=True)
    (out/'preds.jsonl').write_bytes(predictions.read_bytes())
    (out/'dataset.json').write_bytes(dataset.read_bytes())
    dump(out/'plan.json',dict(mode='optimizeN11',dataset=DATASET,dataset_revision=REVISION,
        predictions_sha256=expected_sha,dataset_sha256=DATASET_SHA,catalog_sha256=CATALOG_SHA,
        selected_count=len(selected),active_count=active_count,empty_patch_count=len(selected)-active_count,
        selected_instance_ids=selected,source_prediction_count=len(rows),source_nonempty_count=expected_nonempty,
        groups=groups,cpu=16,memory_gib=64,workers=8,test_timeout=600,max_active_vms=1,
        wave_reserve_seconds=WAVE_RESERVE,prior_task_cost_usd=str(prior_cost),
        new_run_compute_egress_budget_usd=str(budget),egress_reserve_usd=str(EGRESS_RESERVE),
        cumulative_authorized_usd=str(cap),official_tests_modified=False,scientific_stability_qualified=False,
        full_verified_accounting=selected_ids is None,source_commit=commit))
    files={n:source/n for n in EVALUATORS}|{n:Path(__file__).parent/n for n in HELPERS}
    dump(out/'source-hashes.json',{n:digest(p) for n,p in files.items()})


def validate_wave_reports(reports,selected):
    if set(reports)-set(selected): raise ValueError('Extra report outside assigned wave')
    if any(type(row.get('resolved')) is not bool for row in reports.values()):
        raise ValueError('Malformed official report')
    return sorted(set(selected)-set(reports))


def validate_selection(plan,rows):
    by={r['instance_id']:r for r in rows}
    selected=plan.get('selected_instance_ids',list(by))
    if (len(selected)!=len(set(selected)) or set(selected)-set(by) or
        len(selected)!=plan['selected_count'] or
        (plan.get('full_verified_accounting',True) and len(selected)!=500)):
        raise ValueError('Invalid selected instance scope')
    active={i for i in selected if by[i]['model_patch'].strip()}
    assigned=[i for g in plan['groups'] for i in g['instance_ids']]
    if (len(assigned)!=len(set(assigned)) or set(assigned)!=active or
        plan['active_count']!=len(active) or plan['empty_patch_count']!=len(selected)-len(active)):
        raise ValueError('Assigned compute differs from selected nonempty predictions')
    return selected


def merge_progress(found,progress,selected,reports,attempted):
    ids=progress['attempted_ids']
    if len(ids)!=len(set(ids)) or set(ids)-set(selected):raise ValueError('Invalid attempted coverage')
    if set(found)&reports.keys() or set(ids)&set(attempted):raise ValueError('Repeated instance')
    validate_wave_reports(found,ids)
    reports.update(found);attempted.extend(ids)


def worker():
    import remote_worker as base
    from core import HARNESS,load_predictions
    from snapshot_catalog_worker import verify_images
    root=Path('/work');out=root/'output';out.mkdir()
    assignment=json.loads((root/'assignment.json').read_text())
    if importlib.metadata.version('swebench')!=HARNESS: raise ValueError('Unexpected harness')
    if digest(root/'preds.jsonl')!=assignment['predictions_sha256'] or digest(root/'dataset.json')!=DATASET_SHA:
        raise ValueError('Input bytes changed')
    if any(n in os.environ for n in ['REGISTRY_USERNAME','REGISTRY_PASSWORD','DOCKERHUB_TOKEN']):
        raise ValueError('Registry secret forbidden in grader')
    plan=json.loads((root/'catalog-plan.json').read_text());manifest=json.loads(Path('/snapshot-catalog-manifest.json').read_text())
    if (digest(root/'catalog-plan.json')!=assignment['snapshot_plan_sha256'] or
        manifest['plan_sha256']!=assignment['snapshot_plan_sha256'] or plan['shard_index']!=assignment['index'] or
        manifest['tests_run'] or manifest['predictions_present']): raise ValueError('Snapshot provenance differs')
    checked={r['instance_id']:r for r in verify_images(plan['images'])}
    if any(checked.get(r['instance_id'])!=r for r in assignment['images']): raise ValueError('Restored image identity differs')
    rows={r['instance_id']:r for r in load_predictions(root/'preds.jsonl')}
    if any(not rows[i]['model_patch'].strip() for i in assignment['instance_ids']): raise ValueError('Empty patch assigned compute')
    dump(out/'environment.json',dict(assignment,harness=HARNESS,workers=8,test_timeout=600,
        cpu_models=sorted({s.split(':',1)[1].strip() for s in Path('/proc/cpuinfo').read_text().splitlines()
                           if s.startswith('model name')}),official_tests_modified=False))
    deadline=time.monotonic()+assignment['work_seconds'];reports={};attempted=[];waves=[]
    def persist():
        dump(out/'reports.json',reports)
        dump(out/'progress.json',dict(attempted_ids=attempted,
             unattempted_ids=[i for i in assignment['instance_ids'] if i not in attempted],
             missing_report_ids=sorted(set(attempted)-reports.keys()),waves=waves,
             complete=len(attempted)==len(assignment['instance_ids'])))
    persist()
    for offset in range(0,len(assignment['instance_ids']),8):
        if deadline-time.monotonic()<WAVE_RESERVE:
            dump(out/'stop.json',{'reason':'insufficient full-wave reserve; no test timeout shortened'})
            break
        ids=assignment['instance_ids'][offset:offset+8]
        base.OUT=out/f'wave-{offset//8:03d}';base.OUT.mkdir()
        config=SimpleNamespace(workers=8,test_timeout=600,mode='smoke',cache=None,require_cached=True)
        started=time.monotonic()
        result=base.evaluate([rows[i] for i in ids],ids,root/'dataset.json','official',config)
        found={}
        for p in base.OUT.glob('official/logs/**/report.json'):
            value=json.loads(p.read_text())
            if set(value)&found.keys(): raise ValueError('Duplicate report in wave')
            found.update(value)
        missing=validate_wave_reports(found,ids)
        if set(found)&reports.keys(): raise ValueError('Instance was attempted twice')
        reports.update(found);attempted.extend(ids)
        images=json.loads((base.OUT/'official/images.json').read_text())
        if any(r['source']!='local' or r['cache_added_bytes']!=0 for r in images):
            raise ValueError('Image fallback forbidden')
        waves.append(dict(index=offset//8,instance_ids=ids,seconds=time.monotonic()-started,
                          harness_exit_code=result['harness_exit_code'],missing_report_ids=missing))
        persist()
        print('N11_REAL_PROGRESS='+json.dumps({'shard':assignment['index'],'attempted':len(attempted),
              'requested':len(assignment['instance_ids']),'resolved':sum(r['resolved'] for r in reports.values()),
              'missing_reports':len(set(attempted)-reports.keys())}),flush=True)
        if subprocess.check_output(['docker','ps','-aq']).strip(): raise ValueError('Remaining test containers')
    persist()


def run(out,source):
    import fcntl,modal
    from core import load_predictions,summarize
    if os.environ.get('MODAL_PROFILE')!='phamvanvuhoan' or modal.__version__!='1.6.2':
        raise ValueError('Wrong profile/SDK')
    if (out/'receipt.json').exists(): raise ValueError('No automatic paid retry')
    plan=json.loads((out/'plan.json').read_text());rows=load_predictions(out/'preds.jsonl')
    selected=validate_selection(plan,rows)
    budget=Decimal(plan['new_run_compute_egress_budget_usd']);prior_cost=Decimal(plan['prior_task_cost_usd'])
    validate_budget(budget,prior_cost,Decimal(plan['cumulative_authorized_usd']))
    if digest(out/'preds.jsonl')!=plan['predictions_sha256'] or digest(out/'dataset.json')!=DATASET_SHA:
        raise ValueError('Inputs changed after prepare')
    files={n:source/n for n in EVALUATORS}|{n:Path(__file__).parent/n for n in HELPERS}
    expected=json.loads((out/'source-hashes.json').read_text())
    if {n:digest(p) for n,p in files.items()}!=expected: raise ValueError('Source changed after prepare')
    lock=(Path(os.environ['SWE_WEB_STATE'])/'image-cache.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    guard(source,out.name,float(budget));guard(source,out.name,float(budget),'planned')
    record=dict(started_at=datetime.now(timezone.utc).isoformat(),source_sha256=expected,source_commit=plan['source_commit'],
        profile='phamvanvuhoan',mode='optimizeN11',groups=[],terminated_vm_ids=[],used_allocation_seconds=0.,
        max_active_vms=1,budget_usd=str(budget),prior_task_cost_usd=str(prior_cost),
        selected_count=len(selected),active_count=plan['active_count'],official_tests_modified=False)
    app=modal.App('swebench-n11-verified-'+str(plan['active_count']));active=None;reports={};attempted=[];started=time.monotonic()
    def persist():
        dump(out/'receipt.json',record);dump(out/'reports.json',reports)
        summary=summarize(rows,reports,selected,full=plan.get('full_verified_accounting',True))
        summary.update(attempted_nonempty_count=len(attempted),pending_nonempty_count=plan['active_count']-len(attempted),
            missing_attempted_report_ids=sorted(set(attempted)-reports.keys()),
            execution_complete=len(attempted)==plan['active_count'],scientific_stability_qualified=False)
        dump(out/'summary.json',summary)
    try:
        with modal.enable_output(),app.run():
            record['app_id']=app.app_id;persist();guard(source,out.name,float(budget),'running',app.app_id)
            for group in plan['groups']:
                lifetime=lifetime_for(len(group['instance_ids']),record['used_allocation_seconds'],budget)
                if lifetime is None:
                    record['budget_stopped']=True;persist();break
                case=out/f"shard-{group['index']:02d}";case.mkdir()
                row=dict(index=group['index'],lifetime_seconds=lifetime,instance_ids=group['instance_ids'])
                record['groups'].append(row);persist();t=time.monotonic();progress=None
                try:
                    active=modal.Sandbox.create('dockerd','--host=unix:///var/run/docker.sock','--storage-driver=overlay2',
                        app=app,image=modal.Image.from_id(group['snapshot_image_id']),runtime='vm',cpu=(16,16),
                        memory=(65536,65536),timeout=lifetime,
                        readiness_probe=modal.Probe.with_exec('docker','info',interval_ms=1000))
                    row['vm_id']=active.object_id;persist();active.wait_until_ready(timeout=90)
                    row['docker_ready_seconds']=time.monotonic()-t
                    work_seconds=math.floor(lifetime-(time.monotonic()-t)-75)
                    assignment=dict(group,predictions_sha256=plan['predictions_sha256'],work_seconds=work_seconds)
                    dump(case/'assignment.json',assignment)
                    copies=dict(files,**{n:out/n for n in ['preds.jsonl','dataset.json']})
                    copies['assignment.json']=case/'assignment.json'
                    for n,p in copies.items():active.filesystem.copy_from_local(str(p),'/work/'+n)
                    readback=json.loads(execute(active,'python','-c','import json,hashlib,pathlib; names='+repr(list(copies))+'; '
                        'print(json.dumps({n:hashlib.sha256((pathlib.Path("/work")/n).read_bytes()).hexdigest() for n in names}))'))
                    if readback!={n:digest(p) for n,p in copies.items()}:raise ValueError('VM readback mismatch')
                    dump(case/'source-readback.json',readback)
                    if work_seconds<WAVE_RESERVE:raise ValueError('Startup consumed full test reserve')
                    stream(active,['python','-u','/work/n11_predictions.py','worker'],case/'worker.log',work_seconds+10)
                    row['worker_completed']=True
                finally:
                    if active is not None:
                        try:
                            execute(active,'tar','-czf','/tmp/n11-real-output.tar.gz','-C','/work','output',timeout=45)
                            active.filesystem.copy_to_local('/tmp/n11-real-output.tar.gz',str(case/'artifacts.tar.gz'))
                            with tarfile.open(case/'artifacts.tar.gz') as archive:archive.extractall(case,filter='data')
                        except Exception as exc:row['artifact_error']=type(exc).__name__+': '+str(exc)
                        finally:
                            active.terminate(wait=True);record['terminated_vm_ids'].append(active.object_id)
                            row['terminated']=True;active=None
                    row['wall_seconds']=time.monotonic()-t
                    record['used_allocation_seconds']+=row['wall_seconds']
                    # A later worker/stream error must not discard earlier,
                    # checksum-retained waves from this same VM.
                    if (case/'output/progress.json').is_file() and (case/'output/reports.json').is_file():
                        progress=json.loads((case/'output/progress.json').read_text())
                        found=json.loads((case/'output/reports.json').read_text())
                        merge_progress(found,progress,group['instance_ids'],reports,attempted)
                    persist()
                if row.get('artifact_error'):raise RuntimeError('Output collection failed')
                if progress is None:raise RuntimeError('Worker progress missing')
                print('N11_SHARD_COMPLETE='+json.dumps({'shard':group['index'],'attempted_total':len(attempted),
                      'active_requested':plan['active_count'],'resolved':sum(r['resolved'] for r in reports.values())}),flush=True)
                if not progress['complete']:
                    record['budget_stopped']=True;persist();break
            record['execution_complete']=len(attempted)==plan['active_count']
            record['reports_complete']=len(reports)==plan['active_count']
    except Exception as exc:record['error']=type(exc).__name__+': '+str(exc)
    finally:
        if active is not None:
            active.terminate(wait=True);record['terminated_vm_ids'].append(active.object_id)
        record.update(all_vms_terminated=all(g.get('terminated') for g in record['groups']),
            finished_at=datetime.now(timezone.utc).isoformat(),wall_seconds=time.monotonic()-started)
        persist();guard(source,out.name,float(budget),'completed' if record.get('execution_complete') else 'failed');lock.close()
    return 0 if record.get('execution_complete') and record.get('reports_complete') else 1


def collect(out,name):
    import modal
    r=json.loads((out/'receipt.json').read_text());now=datetime.now(timezone.utc)
    remaining=[v.object_id for v in modal.Sandbox.list(app_id=r['app_id'])]
    if remaining or not r['all_vms_terminated']:raise ValueError('VM cleanup not confirmed')
    all_rows=json.loads(subprocess.check_output([sys.executable,'-m','modal','billing','report','--start',r['started_at'][:10],
        '--end',(now+timedelta(days=1)).strftime('%Y-%m-%d'),'--show-resources','--json'],text=True,timeout=90))
    rows=[x for x in all_rows if x.get('object_id')==r['app_id']]
    if not rows:raise ValueError('Billing missing')
    result=dict(checked_at=now.isoformat(),app_id=r['app_id'],rows=rows,
        compute_egress_usd=str(sum((Decimal(str(x['cost'])) for x in rows),Decimal(0))),
        remaining_vms=remaining,before_credits=True,not_final_invoice=True,snapshot_storage_not_in_app_sum=True)
    dump(out/name,result);print(json.dumps({k:v for k,v in result.items() if k!='rows'}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','run','worker','collect'])
    for key in ['out','source','predictions','dataset']:p.add_argument('--'+key,type=Path)
    p.add_argument('--expected-sha256');p.add_argument('--expected-nonempty',type=int);p.add_argument('--commit')
    p.add_argument('--budget-usd',type=Decimal,default=BUDGET)
    p.add_argument('--prior-cost-usd',type=Decimal,default=PRIOR_COST)
    p.add_argument('--cumulative-cap-usd',type=Decimal,default=Decimal('8'))
    p.add_argument('--selected-ids',type=Path,help='JSON ID list for an explicit reference subset; preserves source prediction bytes')
    p.add_argument('--billing-name',default='billing.json');a=p.parse_args()
    if a.action=='worker':worker()
    elif a.action=='collect':collect(a.out,a.billing_name)
    elif a.action=='prepare':prepare(a.out,a.source,a.predictions,a.dataset,a.expected_sha256,a.expected_nonempty,a.commit,a.budget_usd,a.prior_cost_usd,a.cumulative_cap_usd,json.loads(a.selected_ids.read_text()) if a.selected_ids else None)
    else:raise SystemExit(run(a.out,a.source))

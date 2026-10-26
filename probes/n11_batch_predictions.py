"""Multi-file N11 VM batch: shared image cache, fresh official containers per patch.

No changes to legacy runners, tests, parser or grader. Eight workers TOTAL, one
VM at a time. Each file has independent 500-ID accounting and raw reports.
"""
import argparse
from datetime import datetime,timezone
from decimal import Decimal
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tarfile
import time
from types import SimpleNamespace

if (Path(__file__).resolve().parent.parent/'core.py').is_file():
    sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from batch_plan import (MODE,WORKERS,MAX_FILES,WAVE_RESERVE,VM_OVERHEAD,EGRESS_RESERVE,
                        assignments,job_id,lifetime_for,merge_output,split_wave,summarize_file,
                        validate_budget,validate_file_id,waves)
from n11_checked_predictions import CATALOG,METADATA,CATALOG_SHA,DATASET_SHA,make_groups
from snapshot_pilot import dump,execute,guard,stream

EVALUATORS=['core.py','remote_worker.py','image_cache.py','registry_cache.py','checked_patch.py','checked_pipeline.py','batch_plan.py','batch_patch.py']
HELPERS=['n11_batch_predictions.py','n11_checked_predictions.py','snapshot_pilot.py','snapshot_catalog_worker.py']
MAX_OUTPUT_BYTES=1024**3


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))


def source_files(source):
    files={n:source/n for n in EVALUATORS}|{n:source/'probes'/n for n in HELPERS}
    if digest(Path(__file__))!=digest(files['n11_batch_predictions.py']):
        raise ValueError('Execute the batch runner from its frozen source directory')
    return files


def input_errors_for_rows(rows,selected):
    from batch_patch import sections
    # A broken Git executable/environment is an infrastructure failure, not
    # evidence that every supplied prediction is malformed. This reads only.
    sections(b'--- /dev/null\n+++ b/__swebench_format_probe__.txt\n@@ -0,0 +1 @@\n+probe\n')
    errors={}
    for row in rows:
        if row['instance_id'] not in selected or not row['model_patch'].strip():continue
        try:sections(row['model_patch'].encode())
        except ValueError as exc:
            errors[row['instance_id']]=dict(kind='input_format_error',error=type(exc).__name__+': '+str(exc),
                official_report_present=False,compute_allocated=False)
    return errors


def catalog_groups(source,mask):
    if digest(source/CATALOG)!=CATALOG_SHA:raise ValueError('Pinned N11 catalog changed')
    catalog=read(source/CATALOG);hashes={}
    with tarfile.open(source/METADATA) as archive:
        for m in archive.getmembers():
            if m.isfile() and m.name.endswith('/manifest.json'):
                index=int(Path(m.name).parent.name.split('-')[-1]);value=json.load(archive.extractfile(m))
                shard=next(s for s in catalog['shards'] if s['index']==index)
                if (str(index) in hashes or value['tests_run'] or value['predictions_present'] or
                    value['images']!=shard['images'] or not value['no_test_containers']):
                    raise ValueError('Original clean snapshot manifest differs')
                hashes[str(index)]=value['plan_sha256']
    if len(hashes)!=11:raise ValueError('Missing original shard manifests')
    return make_groups(mask,catalog,hashes)


def prepare(out,source,manifest,dataset,commit,budget,prior,cap,selected_ids=None):
    from core import load_predictions,DATASET,REVISION
    if out.exists():raise ValueError('New batch directory required; never overwrite a run')
    validate_budget(budget,prior,cap)
    if not re.fullmatch('[0-9a-f]{40}',commit):raise ValueError('Immutable source commit required')
    if digest(dataset)!=DATASET_SHA:raise ValueError('Pinned dataset changed')
    spec=read(manifest)
    if set(spec)!= {'files'} or not isinstance(spec['files'],list) or not 1<=len(spec['files'])<=MAX_FILES:
        raise ValueError('Manifest requires 1..32 explicitly hashed input files')
    files=[];ids=set()
    for value in spec['files']:
        if set(value)!= {'id','path','sha256'}:raise ValueError('Each input requires id, path and sha256')
        validate_file_id(value['id'])
        if value['id'] in ids:raise ValueError('Duplicate file ID')
        ids.add(value['id']);path=(manifest.parent/Path(value['path'])).resolve()
        if path.suffix.lower() not in ['.jsonl','.json','.csv']:raise ValueError('Unsupported prediction format')
        if not re.fullmatch('[0-9a-f]{64}',value['sha256']) or digest(path)!=value['sha256']:
            raise ValueError('Authorized prediction bytes changed')
        files.append(dict(value,input_path=path,rows=load_predictions(path),input_name=value['id']+path.suffix.lower()))
    data=read(dataset);data_ids=[r['instance_id'] for r in data]
    selected=data_ids if selected_ids is None else selected_ids
    for f in files:f['input_errors']=input_errors_for_rows(f['rows'],set(selected))
    active={r['instance_id'] for f in files for r in f['rows'] if r['model_patch'].strip() and r['instance_id'] in selected and r['instance_id'] not in f['input_errors']}
    mask=[dict(instance_id=i,model_patch='assigned' if i in active else '') for i in data_ids]
    groups=catalog_groups(source,mask)
    selected,groups=assignments(files,data,groups,selected_ids)
    sources=source_files(source);hashes={n:digest(p) for n,p in sources.items()}
    out.mkdir(parents=True);(out/'inputs').mkdir()
    frozen=[]
    for f in files:
        target=out/'inputs'/f['input_name'];target.write_bytes(f['input_path'].read_bytes())
        if digest(target)!=f['sha256']:raise ValueError('Input changed during freeze')
        frozen.append(dict(id=f['id'],input_name=f['input_name'],sha256=f['sha256'],
            original_name=f['input_path'].name,source_prediction_count=len(f['rows']),
            model_name_or_path=f['rows'][0]['model_name_or_path'],input_errors=f['input_errors']))
    (out/'dataset.json').write_bytes(dataset.read_bytes())
    dump(out/'input-manifest.json',spec);dump(out/'source-hashes.json',hashes)
    jobs=sum(len(g['jobs']) for g in groups)
    plan=dict(mode=MODE,files=frozen,groups=groups,active_job_count=jobs,unique_active_instances=len(active),
        selected_instance_ids=selected,selected_count=len(selected),full_verified_accounting=selected_ids is None,
        dataset=DATASET,dataset_revision=REVISION,dataset_sha256=DATASET_SHA,catalog_sha256=CATALOG_SHA,
        source_commit=commit,workers_total=WORKERS,cpu=16,memory_gib=64,test_timeout=600,max_active_vms=1,
        budget_usd=str(budget),prior_cost_usd=str(prior),cumulative_cap_usd=str(cap),
        egress_reserve_usd=str(EGRESS_RESERVE),max_artifact_transfer_bytes=MAX_OUTPUT_BYTES,
        official_tests_modified=False,scientific_stability_qualified=False,patch_format_adapter='batch-unified-v1',
        estimated_vm_starts=len(groups),separate_file_vm_starts=sum(len({g['index'] for g in groups if any(t['file_id']==f['id'] for t in g['jobs'])}) for f in files),
        planned_waves=sum(len(waves(g['jobs'])) for g in groups))
    plan['input_error_count']=sum(len(f['input_errors']) for f in frozen)
    plan['nonempty_prediction_count']=jobs+plan['input_error_count']
    dump(out/'plan.json',plan);(out/'plan.sha256').write_text(digest(out/'plan.json')+'\n',encoding='utf-8')
    return plan


def validate_inputs(root,plan):
    from core import load_predictions
    if (plan['mode']!=MODE or plan['workers_total']!=WORKERS or plan['cpu']!=16 or plan['memory_gib']!=64
            or plan['test_timeout']!=600 or plan['max_active_vms']!=1 or plan['dataset_sha256']!=DATASET_SHA
            or plan['max_artifact_transfer_bytes']!=MAX_OUTPUT_BYTES or plan['patch_format_adapter']!='batch-unified-v1'):raise ValueError('Batch configuration changed')
    if digest(root/'dataset.json')!=DATASET_SHA:raise ValueError('Dataset bytes changed')
    files=[];rows={}
    for f in plan['files']:
        validate_file_id(f['id'])
        if Path(f['input_name']).name!=f['input_name'] or f['input_name']!=f['id']+Path(f['input_name']).suffix:
            raise ValueError('Unsafe frozen input path')
        path=root/'inputs'/f['input_name']
        if digest(path)!=f['sha256']:raise ValueError('Frozen prediction changed')
        value=load_predictions(path);rows[f['id']]=value
        files.append(dict(id=f['id'],rows=value,input_errors=f['input_errors']))
    selected,expected=assignments(files,read(root/'dataset.json'),plan['groups'],plan['selected_instance_ids'])
    if (expected!=plan['groups'] or sum(len(g['jobs']) for g in expected)!=plan['active_job_count']
            or len(selected)!=plan['selected_count'] or (plan['full_verified_accounting'] and len(selected)!=500)):
        raise ValueError('Prepared batch assignment changed')
    return rows


def evaluate_group(root,group_path):
    import remote_worker as base
    from core import load_predictions
    group=read(group_path);plan=read(root/'plan.json')
    f=next(f for f in plan['files'] if f['id']==group['file_id'])
    if digest(root/'inputs'/f['input_name'])!=f['sha256']:raise ValueError('Group input hash changed')
    rows={r['instance_id']:r for r in load_predictions(root/'inputs'/f['input_name'])}
    ids=group['instance_ids']
    if not 1<=len(ids)<=WORKERS or len(ids)!=len(set(ids)):raise ValueError('Group worker bound exceeded')
    base.OUT=group_path.parent
    args=SimpleNamespace(workers=len(ids),test_timeout=600,mode='smoke',cache=None,require_cached=True)
    result=base.evaluate([rows[i] for i in ids],ids,root/'dataset.json',group['run_id'],args)
    dump(group_path.parent/'group-summary.json',result)


def terminate_child(process):
    def send(sig):
        if os.name=='posix':os.killpg(process.pid,sig)
        elif sig==signal.SIGTERM:process.terminate()
        else:process.kill()
    try:send(signal.SIGTERM)
    except ProcessLookupError:return
    try:process.wait(timeout=10)
    except subprocess.TimeoutExpired:send(signal.SIGKILL);process.wait(timeout=10)


def process_wave(root,out,index,tasks,deadline):
    """Independent child processes avoid shared OUT/run-ID state across files."""
    commands=[]
    for file_id,ids in split_wave(tasks).items():
        directory=out/'waves'/f'{index:05d}'/file_id;directory.mkdir(parents=True)
        group=dict(file_id=file_id,instance_ids=ids,run_id=f'b{index:05d}-{file_id}')
        dump(directory/'group.json',group)
        commands.append(([sys.executable,str(Path(__file__).resolve()),'evaluate','--root',str(root),
                          '--group',str(directory/'group.json')],directory))
    children=[]
    try:
        for command,directory in commands:
            handle=(directory/'process.log').open('w',encoding='utf-8')
            try:p=subprocess.Popen(command,stdout=handle,stderr=subprocess.STDOUT,env=os.environ.copy(),start_new_session=True)
            except BaseException:handle.close();raise
            children.append((p,handle,directory))
        for p,_,directory in children:
            p.wait(timeout=max(1,deadline-time.monotonic()))
            if p.returncode:raise RuntimeError('Official group process failed: '+str(directory))
    finally:
        for p,handle,_ in children:
            if p.poll() is None:
                terminate_child(p)
            handle.close()


def gather_wave(out,index,tasks,dataset,predictions):
    from checked_pipeline import audit_case
    from swebench.harness.grading import get_eval_report
    from swebench.harness.test_spec.test_spec import make_test_spec
    reports={};validity={};errors={};fatal=[]
    for file_id,ids in split_wave(tasks).items():
        directory=out/'waves'/f'{index:05d}'/file_id
        found={}
        for path in directory.glob('**/report.json'):
            raw=read(path)
            if len(raw)!=1:raise ValueError('Malformed official report')
            iid=next(iter(raw))
            if iid in found or iid not in ids:raise ValueError('Duplicate/outside official report')
            prediction=predictions[file_id][iid]
            spec=make_test_spec(dataset[iid],namespace='swebench')
            if ((path.parent/'patch.diff').read_bytes()!=prediction['model_patch'].encode()
                    or (path.parent/'eval.sh').read_bytes()!=spec.eval_script.encode()
                    or get_eval_report(spec,prediction,path.parent/'test_output.txt',True)!=raw):
                raise ValueError('Per-file prediction, official script or native report verification failed')
            dump(path.parent/'validation-checks.json',dict(prediction_bytes_match=True,
                official_eval_script_matches=True,native_report_matches=True,file_id=file_id))
            found[iid]=path;reports[job_id(file_id,iid)]=raw[iid]
            try:validity[job_id(file_id,iid)]=audit_case(spec,path.parent)
            except Exception as exc:
                validity[job_id(file_id,iid)]=dict(execution_valid=False,qualified_resolved=None,
                    issues=['supplemental_audit_failed'],error=type(exc).__name__+': '+str(exc))
                fatal.append(job_id(file_id,iid))
        images=directory/f'b{index:05d}-{file_id}'/'images.json'
        if images.exists() and any(r['source']!='local' or r['cache_added_bytes']!=0 for r in read(images)):
            raise ValueError('Image fallback is forbidden')
        for iid in set(ids)-set(found):
            logs=list(directory.glob('**/'+iid+'/run_instance.log'))
            if len(logs)==1 and (logs[0].parent/'patch.diff').exists() and (logs[0].parent/'patch.diff').read_bytes()!=predictions[file_id][iid]['model_patch'].encode():
                raise ValueError('Missing-report case has different prediction bytes')
            text=logs[0].read_text(errors='replace') if len(logs)==1 else ''
            key=job_id(file_id,iid)
            if 'Checked forward patch failed:' in text:kind='prediction_application_error'
            elif re.search('Timeout error:|Test timed out after',text):kind='official_timeout'
            else:kind='unknown_missing_report';fatal.append(key)
            errors[key]=dict(kind=kind,official_report_present=False,log_path=str(logs[0].relative_to(out)) if logs else None)
    return reports,validity,errors,fatal


def validate_assignment(plan,assignment):
    matches=[g for g in plan['groups'] if g['index']==assignment['index']]
    if len(matches)!=1 or any(assignment.get(k)!=v for k,v in matches[0].items()):
        raise ValueError('Worker assignment differs from frozen batch plan')
    if not isinstance(assignment['work_seconds'],int) or assignment['work_seconds']<WAVE_RESERVE:
        raise ValueError('Incomplete full-wave allocation')


def worker(root):
    from core import HARNESS
    from checked_pipeline import prepare_overlay
    from snapshot_catalog_worker import verify_images
    import swebench
    plan=read(root/'plan.json');source_rows=validate_inputs(root,plan)
    predictions={fid:{r['instance_id']:r for r in rows} for fid,rows in source_rows.items()}
    assignment=read(root/'assignment.json');validate_assignment(plan,assignment)
    out=root/'output';out.mkdir()
    if importlib.metadata.version('swebench')!=HARNESS:raise ValueError('Wrong official harness')
    if any(n in os.environ for n in ['REGISTRY_USERNAME','REGISTRY_PASSWORD','DOCKERHUB_TOKEN']):raise ValueError('Registry secret forbidden')
    catalog=read(root/'catalog-plan.json');manifest=read(Path('/snapshot-catalog-manifest.json'))
    if (digest(root/'catalog-plan.json')!=assignment['snapshot_plan_sha256'] or manifest['plan_sha256']!=assignment['snapshot_plan_sha256']
            or catalog['shard_index']!=assignment['index'] or manifest['tests_run'] or manifest['predictions_present']):
        raise ValueError('Snapshot provenance changed')
    restored={r['instance_id']:r for r in verify_images(catalog['images'])}
    if any(restored.get(r['instance_id'])!=r for r in assignment['images']):raise ValueError('Restored image identity differs')
    overlay=root/'checked-overlay';dump(out/'overlay-manifest.json',prepare_overlay(Path(swebench.__file__).parent,overlay))
    os.environ.update(PYTHONPATH=str(overlay)+os.pathsep+str(root),SWE_CHECKED_DATASET=str(root/'dataset.json'),
        SWE_CHECKED_DATASET_SHA256=DATASET_SHA,SWE_CHECKED_PATCH_ADAPTER='batch-unified-v1')
    dataset={r['instance_id']:r for r in read(root/'dataset.json')}
    payload=dict(attempted_job_ids=[],reports={},validity={},errors={},waves=[],complete=False)
    def persist():dump(out/'batch-output.json',payload)
    persist();deadline=time.monotonic()+assignment['work_seconds']
    for index,tasks in enumerate(waves(assignment['jobs'])):
        if deadline-time.monotonic()<WAVE_RESERVE:
            payload['stop_reason']='insufficient full-wave reserve; official timeout unchanged';persist();break
        payload['attempted_job_ids'].extend(t['job_id'] for t in tasks);persist();started=time.monotonic()
        process_error=None
        try:process_wave(root,out,index,tasks,deadline)
        except Exception as exc:process_error=type(exc).__name__+': '+str(exc)
        try:found,validity,errors,fatal=gather_wave(out,index,tasks,dataset,predictions)
        except Exception as exc:
            payload['stop_reason']='wave evidence verification failed: '+type(exc).__name__+': '+str(exc);persist();raise
        if set(found)&payload['reports'].keys():raise ValueError('Repeated batch result')
        payload['reports'].update(found);payload['validity'].update(validity);payload['errors'].update(errors)
        payload['waves'].append(dict(index=index,jobs=tasks,seconds=time.monotonic()-started,process_error=process_error))
        persist()
        print('SWE_BATCH_PROGRESS='+json.dumps(dict(shard=assignment['index'],attempted=len(payload['attempted_job_ids']),reports=len(payload['reports']),requested=len(assignment['jobs']))),flush=True)
        if process_error or fatal or subprocess.check_output(['docker','ps','-aq']).strip():
            payload['stop_reason']=process_error or 'infrastructure validation failed or remaining containers';persist();break
    payload['complete']=len(payload['attempted_job_ids'])==len(assignment['jobs']) and 'stop_reason' not in payload
    persist()


def write_results(out,plan,rows,reports,validity,attempted,errors):
    import csv
    summaries={}
    for f in plan['files']:
        fid=f['id'];keys={job_id(fid,i):i for i in plan['selected_instance_ids']}
        result=summarize_file(rows[fid],plan['selected_instance_ids'],
            {keys[k]:v for k,v in reports.items() if k in keys},
            {keys[k]:v for k,v in validity.items() if k in keys},
            [keys[k] for k in attempted if k in keys],plan['full_verified_accounting'],f.get('input_errors',{}))
        directory=out/'results'/fid;directory.mkdir(parents=True,exist_ok=True)
        for label,values in [('reports',reports),('validity',validity),('errors',errors)]:
            dump(directory/(label+'.json'),{keys[k]:v for k,v in values.items() if k in keys})
        cases=result.pop('cases')
        for case in cases:
            case['error_kind']=errors.get(job_id(fid,case['instance_id']),{}).get('kind')
        dump(directory/'cases.json',cases);dump(directory/'summary.json',dict(result,file_id=fid,input_sha256=f['sha256']))
        with (directory/'cases.csv').open('w',encoding='utf-8',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(cases[0]));writer.writeheader();writer.writerows(cases)
        summaries[fid]=result
    dump(out/'summary.json',dict(mode=MODE,files=summaries,active_job_count=plan['active_job_count'],
        attempted_jobs=len(attempted),official_reports=len(reports),
        execution_complete=len(attempted)==plan['active_job_count'],official_tests_modified=False,
        scientific_stability_qualified=False,cost_scope='shared VM batch; no exact per-file billing attribution'))


def run(out,source):
    import fcntl,modal
    if os.environ.get('MODAL_PROFILE')!='phamvanvuhoan' or modal.__version__!='1.6.2':raise ValueError('Wrong profile/SDK')
    if (out/'receipt.json').exists():raise ValueError('Existing batch: no automatic paid retry')
    if digest(out/'plan.json')!=(out/'plan.sha256').read_text().strip():raise ValueError('Plan changed after prepare')
    plan=read(out/'plan.json');rows=validate_inputs(out,plan)
    budget=Decimal(plan['budget_usd']);validate_budget(budget,Decimal(plan['prior_cost_usd']),Decimal(plan['cumulative_cap_usd']))
    files=source_files(source)
    if {n:digest(p) for n,p in files.items()}!=read(out/'source-hashes.json'):raise ValueError('Frozen source changed')
    lock=(Path(os.environ['SWE_WEB_STATE'])/'image-cache.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    record=dict(mode=MODE,started_at=datetime.now(timezone.utc).isoformat(),profile='phamvanvuhoan',
        source_commit=plan['source_commit'],groups=[],used_allocation_seconds=0.,artifact_transfer_bytes=0,
        budget_usd=str(budget),prior_cost_usd=plan['prior_cost_usd'],cumulative_cap_usd=plan['cumulative_cap_usd'],
        max_active_vms=1,workers_total=WORKERS,all_vms_terminated=True)
    reports={};validity={};attempted=[]
    errors={job_id(f['id'],iid):value for f in plan['files'] for iid,value in f['input_errors'].items()}
    active=None;started=time.monotonic()
    def persist():
        dump(out/'receipt.json',record);write_results(out,plan,rows,reports,validity,attempted,errors)
    persist()
    try:
        if not plan['active_job_count']:
            record.update(execution_complete=True,reports_complete=True);return 0
        guard(source,out.name,float(budget));guard(source,out.name,float(budget),'planned')
        app=modal.App('swebench-n11-batch-'+str(len(plan['files'])))
        with modal.enable_output(),app.run():
            record['app_id']=app.app_id;persist();guard(source,out.name,float(budget),'running',app.app_id)
            for group in plan['groups']:
                lifetime=lifetime_for(len(group['jobs']),record['used_allocation_seconds'],budget)
                if lifetime is None:record['stop_reason']='insufficient next-VM reserve';persist();break
                directory=out/f"shard-{group['index']:02d}";directory.mkdir()
                row=dict(index=group['index'],lifetime_seconds=lifetime,job_count=len(group['jobs']),terminated=True)
                record['groups'].append(row);persist();tick=time.monotonic()
                try:
                    active=modal.Sandbox.create('dockerd','--host=unix:///var/run/docker.sock','--storage-driver=overlay2',
                        app=app,image=modal.Image.from_id(group['snapshot_image_id']),runtime='vm',cpu=(16,16),memory=(65536,65536),
                        timeout=lifetime,readiness_probe=modal.Probe.with_exec('docker','info',interval_ms=1000))
                    row.update(vm_id=active.object_id,terminated=False);record['all_vms_terminated']=False;persist()
                    active.wait_until_ready(timeout=90);row['docker_ready_seconds']=time.monotonic()-tick
                    execute(active,'mkdir','-p','/work/inputs')
                    for n,p in files.items():active.filesystem.copy_from_local(str(p),'/work/'+n)
                    copies={'dataset.json':out/'dataset.json','plan.json':out/'plan.json'}
                    copies.update({'inputs/'+f['input_name']:out/'inputs'/f['input_name'] for f in plan['files']})
                    for n,p in copies.items():active.filesystem.copy_from_local(str(p),'/work/'+n)
                    work_seconds=math.floor(lifetime-(time.monotonic()-tick)-75)
                    if work_seconds<WAVE_RESERVE:raise ValueError('Startup consumed full test reserve')
                    assignment=dict(group,work_seconds=work_seconds);dump(directory/'assignment.json',assignment)
                    active.filesystem.copy_from_local(str(directory/'assignment.json'),'/work/assignment.json')
                    expected={n:digest(p) for n,p in dict(files,**copies).items()}
                    expected['assignment.json']=digest(directory/'assignment.json')
                    actual=json.loads(execute(active,'python','-c','import json,pathlib,hashlib; names='+repr(list(expected))+'; print(json.dumps({n:hashlib.sha256((pathlib.Path("/work")/n).read_bytes()).hexdigest() for n in names}))'))
                    if actual!=expected:raise ValueError('VM source/input readback failed')
                    dump(directory/'source-readback.json',actual)
                    stream(active,['python','-u','/work/n11_batch_predictions.py','worker','--root','/work'],directory/'worker.log',work_seconds+10)
                finally:
                    if active is not None:
                        try:
                            execute(active,'tar','-czf','/tmp/batch-output.tar.gz','-C','/work','output',timeout=45)
                            size=int(execute(active,'stat','-c','%s','/tmp/batch-output.tar.gz').strip())
                            if record['artifact_transfer_bytes']+size>MAX_OUTPUT_BYTES:raise ValueError('Artifact egress bound exceeded')
                            active.filesystem.copy_to_local('/tmp/batch-output.tar.gz',str(directory/'artifacts.tar.gz'))
                            record['artifact_transfer_bytes']+=size
                            with tarfile.open(directory/'artifacts.tar.gz') as archive:archive.extractall(directory,filter='data')
                        except Exception as exc:row['artifact_error']=type(exc).__name__+': '+str(exc)
                        finally:
                            active.terminate(wait=True);row['terminated']=True;active=None
                    row['wall_seconds']=time.monotonic()-tick;record['used_allocation_seconds']+=row['wall_seconds']
                    payload_path=directory/'output/batch-output.json'
                    if payload_path.exists():
                        payload=read(payload_path);merge_output(group['jobs'],payload,reports,validity,attempted)
                        errors.update(payload['errors']);row['worker_complete']=payload['complete']
                        if payload.get('stop_reason'):row['stop_reason']=payload['stop_reason']
                    record['all_vms_terminated']=all(g['terminated'] for g in record['groups']);persist()
                print('SWE_BATCH_SHARD_COMPLETE='+json.dumps(dict(shard=group['index'],attempted=len(attempted),reports=len(reports),requested=plan['active_job_count'])),flush=True)
                if row.get('artifact_error') or not row.get('worker_complete'):
                    record['stop_reason']=row.get('artifact_error') or row.get('stop_reason','worker did not finish');break
            record.update(execution_complete=len(attempted)==plan['active_job_count'] and all(g.get('worker_complete') for g in record['groups']),reports_complete=len(reports)==plan['active_job_count'])
    except Exception as exc:record['error']=type(exc).__name__+': '+str(exc)
    finally:
        if active is not None:
            try:active.terminate(wait=True);record['groups'][-1]['terminated']=True
            except Exception as exc:record['cleanup_error']=type(exc).__name__+': '+str(exc)
        record.update(all_vms_terminated=all(g['terminated'] for g in record['groups']),
            finished_at=datetime.now(timezone.utc).isoformat(),wall_seconds=time.monotonic()-started)
        if record.get('app_id'):
            # Also discover an allocation whose create response was lost. A
            # boolean set before create is not provider evidence of cleanup.
            try:
                for vm in modal.Sandbox.list(app_id=record['app_id']):vm.terminate(wait=True)
                remaining=[vm.object_id for vm in modal.Sandbox.list(app_id=record['app_id'])]
                record['remaining_vm_ids']=remaining;record['all_vms_terminated']=not remaining
            except Exception as exc:
                record['all_vms_terminated']=False;record['cleanup_error']=type(exc).__name__+': '+str(exc)
        persist()
        if record.get('app_id'):guard(source,out.name,float(budget),'completed' if record.get('execution_complete') else 'failed')
        lock.close()
    return 0 if record.get('execution_complete') and record.get('reports_complete') and record['all_vms_terminated'] and 'error' not in record and 'stop_reason' not in record else 1


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','run','worker','evaluate','collect'])
    for name in ['out','source','manifest','dataset','selected-ids','group']:p.add_argument('--'+name,type=Path)
    p.add_argument('--root',type=Path,default=Path('/work'));p.add_argument('--commit')
    for name in ['budget-usd','prior-cost-usd','cumulative-cap-usd']:p.add_argument('--'+name,type=Decimal)
    p.add_argument('--billing-name',default='billing.json');a=p.parse_args()
    if a.action=='worker':worker(a.root);return 0
    if a.action=='evaluate':evaluate_group(a.root,a.group);return 0
    if a.action=='collect':
        receipt=read(a.out/'receipt.json')
        if not receipt.get('app_id'):
            if read(a.out/'plan.json')['active_job_count'] or not receipt.get('finished_at'):
                raise ValueError('Missing provider app for a nonempty batch')
            dump(a.out/a.billing_name,dict(compute_egress_usd='0',remaining_vms=[],no_compute_allocated=True));return 0
        from n11_checked_predictions import collect
        collect(a.out,a.billing_name);return 0
    if a.action=='prepare':
        if any(v is None for v in [a.out,a.source,a.manifest,a.dataset,a.commit,a.budget_usd,a.prior_cost_usd,a.cumulative_cap_usd]):
            p.error('prepare requires output/source/manifest/dataset/commit and all three explicit budget fields')
        plan=prepare(a.out,a.source,a.manifest,a.dataset,a.commit,a.budget_usd,a.prior_cost_usd,a.cumulative_cap_usd,read(a.selected_ids) if a.selected_ids else None)
        print(json.dumps({k:plan[k] for k in ['mode','nonempty_prediction_count','input_error_count','active_job_count','unique_active_instances','estimated_vm_starts','separate_file_vm_starts','planned_waves']}));return 0
    if a.out is None or a.source is None:p.error('run requires --out and --source')
    return run(a.out,a.source)


if __name__=='__main__':raise SystemExit(main())

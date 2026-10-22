"""One-VM diagnosis of a timing-sensitive gold-control mismatch; no score changes."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import subprocess
import sys
import tarfile
import time
from types import SimpleNamespace

from snapshot_pilot import dump,execute,guard,stream,validate_inputs

IID='matplotlib__matplotlib-20859'
ESTIMATE=.85
LIFETIME=780
OLD_COST=.45891859
RATE=16*.00003942+64*.00000667


def profiled_script(text):
    lines=text.splitlines(keepends=True)
    targets=[i for i,line in enumerate(lines) if line.startswith('pytest ')]
    if len(targets)!=1:raise ValueError('Expected one original pytest command')
    i=targets[0]
    lines[i]='PYTHONPATH=/tmp/timing-observer MPL_TIMING_OUTPUT=/tmp/timing-observer.json '+lines[i].rstrip('\n')+' -p matplotlib_clock_observer\n'
    return ''.join(lines)


def worker():
    import docker
    import remote_worker as base
    from snapshot_catalog_worker import verify_images
    from swebench.harness.docker_build import build_container
    from swebench.harness.docker_utils import copy_to_container,cleanup_container,exec_run_with_timeout
    from swebench.harness.test_spec.test_spec import make_test_spec
    from swebench.harness.utils import setup_logger
    root=Path('/work');out=root/'timing-output';out.mkdir(exist_ok=False)
    assignment=json.loads((root/'assignment.json').read_text())
    data=json.loads((root/'pilot.json').read_text())
    predictions=[json.loads(x) for x in (root/'preds.jsonl').read_text().splitlines()]
    from sharded_pilot_worker import select_inputs
    selected=select_inputs(data,predictions,assignment)
    expected={r['instance_id']:r for r in assignment['images']}
    checked={r['instance_id']:r for r in verify_images(json.loads((root/'catalog-plan.json').read_text())['images'])}
    if any(checked.get(iid)!=row for iid,row in expected.items()):raise ValueError('Restored image identity differs')
    # Retain only selected CPU identifiers, never environment or credentials.
    cpuinfo=Path('/proc/cpuinfo').read_text()
    dump(out/'vm-hardware.json',{'cpu_model_lines':sorted(set(x for x in cpuinfo.splitlines() if x.startswith(('model name','vendor_id')))),
          'logical_cpus':os.cpu_count(),'kernel':os.uname().release})
    base.OUT=out/'official';base.OUT.mkdir()
    config=SimpleNamespace(workers=8,test_timeout=600,mode='smoke',cache=None,require_cached=True)
    official=[]
    for repetition in range(2):
        label=f'fixed-official-{repetition}'
        result=base.evaluate(selected,assignment['instance_ids'],root/'pilot.json',label,config)
        reports={}
        for p in (base.OUT/label).glob('logs/**/report.json'):reports.update(json.loads(p.read_text()))
        if set(reports)!=set(assignment['instance_ids']):raise ValueError('Official repetition missing report')
        official.append({'repetition':repetition,'reports':reports,'summary':result})
        dump(out/'official-repetitions.json',official)
    row=next(r for r in data if r['instance_id']==IID)
    pred=next(r for r in predictions if r['instance_id']==IID)
    spec=make_test_spec(row,namespace='swebench')
    reference=(root/'reference-eval.sh').read_text()
    if spec.eval_script!=reference:raise ValueError('Generated original eval script differs from retained original')
    dump(out/'protocol.json',{'official_repetitions':2,'profiled_repetitions':2,
         'official_workers':8,'official_test_timeout':600,'group_ids':assignment['instance_ids'],
         'original_eval_script_sha256':hashlib.sha256(reference.encode()).hexdigest(),
         'profiled_script_changes':'add diagnostic pytest plugin and its output/PYTHONPATH; test/source/clock unchanged',
         'diagnostic_reports_do_not_replace_original_24_case_reports':True})
    client=docker.from_env();observations=[]
    for repetition in range(2):
        case=out/f'profile-{repetition}';case.mkdir()
        logger=setup_logger(f'mpl-profile-{repetition}',case/'container.log')
        container=None
        try:
            client.images.get(spec.instance_image_key)  # Fail before build_container could pull on miss.
            container=build_container(spec,client,f'mpl-timing-{repetition}',logger,False,False)
            container.start()
            (case/'gold.patch').write_text(pred['model_patch'])
            copy_to_container(container,case/'gold.patch',PurePosixPath('/tmp/patch.diff'))
            applied=container.exec_run('git apply --verbose /tmp/patch.diff',workdir='/testbed',user='root')
            (case/'gold-apply.log').write_bytes(applied.output)
            if applied.exit_code:raise ValueError('Gold patch did not apply cleanly')
            container.exec_run('mkdir -p /tmp/timing-observer')
            copy_to_container(container,root/'matplotlib_clock_observer.py',PurePosixPath('/tmp/timing-observer/matplotlib_clock_observer.py'))
            (case/'eval-original.sh').write_text(reference)
            (case/'eval-profile.sh').write_text(profiled_script(reference))
            copy_to_container(container,case/'eval-profile.sh',PurePosixPath('/eval.sh'))
            text,timed_out,elapsed=exec_run_with_timeout(container,'/bin/bash /eval.sh',600)
            (case/'test_output.txt').write_text(text)
            if timed_out:raise RuntimeError('Diagnostic test timed out')
            result=container.exec_run('cat /tmp/timing-observer.json')
            if result.exit_code:raise RuntimeError('Observer did not produce evidence')
            observed=json.loads(result.output)
            if observed['file_hashes_before']!=observed['file_hashes_after']:raise ValueError('Test or legend file changed during observation')
            observed.update(repetition=repetition,eval_script_seconds=elapsed)
            dump(case/'observer.json',observed)
            observations.append(observed);dump(out/'observations.json',observations)
        finally:
            cleanup_container(client,container,logger)
    remaining=[c.id for c in client.containers.list(all=True)]
    if remaining:raise ValueError('Test containers remain')
    dump(out/'summary.json',{'completed':True,'official_repetitions':len(official),'profiled_repetitions':len(observations),
         'test_or_clock_modified':False,'original_failed_result_preserved':True,'remaining_test_containers':remaining})
    return 0


def run(out,source,reference):
    import modal
    if os.environ.get('MODAL_PROFILE')!='phamvanvuhoan' or modal.__version__!='1.6.2':raise ValueError('Unauthorized profile/SDK')
    if (out/'receipt.json').exists():raise ValueError('No paid retry')
    if OLD_COST+RATE*LIFETIME+.02>2:raise ValueError('Combined prior run + full diagnostic lifetime exceeds $2')
    validate_inputs(reference)
    original=json.loads((reference/'receipt.json').read_text())
    if original['app_id']!='ap-ieIjh0VdOg7IiggbOW7dh5':raise ValueError('Wrong original run')
    group=next(g for g in original['plan']['groups'] if g['index']==2)
    if IID not in group['instance_ids'] or len(group['instance_ids'])!=2:raise ValueError('Wrong diagnostic group')
    out.mkdir(parents=True,exist_ok=True)
    dump(out/'assignment.json',group)
    expected=original['source_sha256'];here=Path(__file__).resolve().parent
    for n,sha in expected.items():
        if hashlib.sha256((source/n).read_bytes()).hexdigest()!=sha:raise ValueError('Evaluator changed')
    guard(source,out.name,ESTIMATE);guard(source,out.name,ESTIMATE,'planned')
    record={'started_at':datetime.now(timezone.utc).isoformat(),'profile':'phamvanvuhoan','cpu':16,'memory_gib':64,
            'lifetime_seconds':LIFETIME,'estimate_usd':ESTIMATE,'prior_run_cost_usd':OLD_COST,
            'snapshot_image_id':group['snapshot_image_id'],'source_sha256':expected,
            'original_results_not_modified':True,'full_500_evaluated':False,'source_git_commit':os.environ['MPL_DIAG_COMMIT']}
    active=None;started=time.monotonic();app=modal.App('swebench-matplotlib-clock-diagnosis')
    try:
        with modal.enable_output(),app.run():
            record['app_id']=app.app_id;guard(source,out.name,ESTIMATE,'running',app.app_id)
            dump(out/'receipt.json',record)
            try:
                active=modal.Sandbox.create('dockerd','--host=unix:///var/run/docker.sock','--storage-driver=overlay2',app=app,
                    image=modal.Image.from_id(group['snapshot_image_id']),runtime='vm',cpu=(16,16),memory=(65536,65536),
                    timeout=LIFETIME,readiness_probe=modal.Probe.with_exec('docker','info',interval_ms=1000))
                record['vm_id']=active.object_id;active.wait_until_ready(timeout=90)
                files={n:source/n for n in expected}
                for n in ['snapshot_catalog_worker.py','sharded_pilot_worker.py']:
                    files[n]=here/n
                    if hashlib.sha256(files[n].read_bytes()).hexdigest()!=original['helper_sha256'][n]:raise ValueError('Qualified helper changed')
                files.update({n:here/n for n in ['matplotlib_timing_probe.py','matplotlib_clock_observer.py','snapshot_pilot.py']})
                files.update({n:reference/n for n in ['pilot.json','preds.jsonl']})
                files['assignment.json']=out/'assignment.json'
                logs=reference/'shard-02/output/shard-02/logs/run_evaluation/shard-02/PILOT-GOLD-NOT-MODEL'/IID
                files['reference-eval.sh']=logs/'eval.sh'
                record['copied_files_sha256']={n:hashlib.sha256(p.read_bytes()).hexdigest() for n,p in files.items()}
                for name,path in files.items():active.filesystem.copy_from_local(str(path),'/work/'+name)
                actual=json.loads(execute(active,'python','-c','import pathlib,hashlib,json; names='+repr(list(files))+'; print(json.dumps({n:hashlib.sha256((pathlib.Path("/work")/n).read_bytes()).hexdigest() for n in names}))'))
                if actual!=record['copied_files_sha256']:raise ValueError('VM source/input readback differs')
                dump(out/'source-readback.json',actual);dump(out/'receipt.json',record)
                stream(active,['python','-u','/work/matplotlib_timing_probe.py','worker'],out/'worker.log',680)
                record['worker_completed']=True
            finally:
                if active is not None:
                    try:
                        execute(active,'tar','-czf','/tmp/timing-output.tar.gz','-C','/work','timing-output',timeout=30)
                        active.filesystem.copy_to_local('/tmp/timing-output.tar.gz',str(out/'artifacts.tar.gz'))
                        with tarfile.open(out/'artifacts.tar.gz') as archive:archive.extractall(out,filter='data')
                    finally:
                        active.terminate(wait=True);record['all_vms_terminated']=True
    except Exception as exc:record['error']=type(exc).__name__+': '+str(exc)
    finally:
        record.update(finished_at=datetime.now(timezone.utc).isoformat(),wall_seconds=time.monotonic()-started)
        dump(out/'receipt.json',record)
        guard(source,out.name,ESTIMATE,'completed' if record.get('worker_completed') else 'failed')
    return 0 if record.get('worker_completed') and record.get('all_vms_terminated') else 1


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['run','worker'])
    for arg in ['out','source','reference']:p.add_argument('--'+arg,type=Path)
    a=p.parse_args()
    if a.action=='worker':raise SystemExit(worker())
    if not all([a.out,a.source,a.reference]):p.error('run requires --out --source --reference')
    raise SystemExit(run(a.out.resolve(),a.source.resolve(),a.reference.resolve()))

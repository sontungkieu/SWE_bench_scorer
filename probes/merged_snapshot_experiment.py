"""Finite $5 combined N7 build + frozen-24 experiment, existing GCP coordinator.

One VM at a time, no retries, six new snapshot groups maximum. The old N11
catalog and runners remain untouched. Never evaluate all 500 tasks here.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time

from merged_snapshot_plan import MODES, plan_mode, load_inputs, union_bytes
from snapshot_catalog_worker import TTL
from snapshot_pilot import RUNTIME, dump, execute, guard, start_docker, stream, validate_inputs
from snapshot_catalog import stop_docker
from snapshot_shards import verify_catalog
from sharded_pilot import CATALOG_SHA, RATE
from summarize_snapshot_pilot import compare

ESTIMATE = 5
BUILD_LIFETIME = 900
VERIFY_LIFETIME = 120
SCORE_DEADLINE = 1800
BUILD_RATE = 2*.00003942 + 4*.00000667
BOUND = 6*(BUILD_LIFETIME+VERIFY_LIFETIME)*BUILD_RATE + 2*SCORE_DEADLINE*RATE
EVALUATORS = ['core.py', 'remote_worker.py', 'image_cache.py', 'registry_cache.py']
HELPERS = ['merged_pilot_worker.py', 'sharded_pilot_worker.py', 'snapshot_catalog_worker.py']
BUILD_HELPER = 'merged_catalog_worker.py'


def lifetime_for(count):
    if not 1 <= count <= 24: raise ValueError('Only 1..24 controls')
    return math.ceil(count/8)*600+180


def selected_groups(catalog, selection, image_plan):
    selected = [r['instance_id'] for r in selection['selected']]
    baseline = {r['instance_id']: {k:r[k] for k in ['instance_id','reference','id']} for r in image_plan}
    if len(selected) != 24 or len(set(selected)) != 24 or set(selected) != set(baseline):
        raise ValueError('Wrong frozen control set')
    if not catalog['complete'] or catalog['image_count'] != 500 or len(catalog['shards']) != 7:
        raise ValueError('Incomplete N7 catalog')
    mapping = {}
    for group in catalog['shards']:
        if not group['verified'] or not group['all_vms_terminated']:
            raise ValueError('Unverified snapshot')
        if datetime.fromisoformat(group['use_before']) <= datetime.now(timezone.utc)+timedelta(seconds=SCORE_DEADLINE):
            raise ValueError('Insufficient snapshot retention')
        for row in group['images']:
            if row['instance_id'] in mapping: raise ValueError('Duplicate catalog coverage')
            mapping[row['instance_id']] = (group, row)
    if len(mapping) != 500: raise ValueError('Missing catalog coverage')
    result = {}
    for iid in selected:
        group, row = mapping[iid]
        if row != baseline[iid]: raise ValueError('Image identity differs from baseline')
        assignment = result.setdefault(group['index'], {k:group[k] for k in
            ['index','snapshot_image_id','snapshot_plan_sha256','snapshot_shard_index']})
        assignment.setdefault('instance_ids', []).append(iid)
        assignment.setdefault('images', []).append(row)
    # Reserve two timeout waves for the nine-control group before shorter groups.
    return sorted(result.values(), key=lambda g:(-lifetime_for(len(g['instance_ids'])),g['index']))


def build_contract(reuse_bases=False,prior_reservation=0.):
    if not 0 <= prior_reservation <= .19: raise ValueError('Prior-attempt reservation outside recovery contract')
    cpu=4 if reuse_bases else 2
    lifetime=780 if reuse_bases else BUILD_LIFETIME
    bound=6*lifetime*(cpu*.00003942+4*.00000667)+6*VERIFY_LIFETIME*BUILD_RATE+2*SCORE_DEADLINE*RATE
    if bound+prior_reservation > 4.94: raise ValueError('Insufficient $5 envelope / egress reserve')
    return {'reuse_bases':reuse_bases,'build_cpu':cpu,'build_memory_gib':4,'build_lifetime':lifetime,
            'prior_attempt_reservation_usd':prior_reservation,'new_run_cpu_ram_bound_usd':bound,
            'combined_cpu_ram_bound_usd':bound+prior_reservation,'approved_total_compute_egress_usd':5}


def prepare(out, source, reference, commit, reuse_bases=False, prior_reservation=0.):
    validate_inputs(reference)
    if out.exists(): raise ValueError('Never overwrite an experiment')
    raw = (reference/'usable-shards-catalog.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != CATALOG_SHA: raise ValueError('Wrong frozen N11 catalog')
    receipt = json.loads((reference/'receipt.json').read_text())
    if not receipt['complete'] or not receipt['report_parity'] or not receipt['all_vms_terminated']:
        raise ValueError('Reference must have complete parity and cleanup')
    expected = receipt['source_sha256']
    actual = {n:hashlib.sha256((source/n).read_bytes()).hexdigest() for n in EVALUATORS}
    if actual != expected: raise ValueError('Evaluator bytes differ from N11 baseline')
    plans = {mode:plan_mode(mode) for mode in MODES}
    out.mkdir(parents=True)
    for name in ['pilot.json','preds.jsonl','selection.json','image-plan.json',
                 'snapshot-plan-hashes.json','usable-shards-catalog.json','baseline-reports.json']:
        shutil.copyfile(reference/name, out/name)
    dump(out/'evaluator-sha256.json', expected)
    for mode, plan in plans.items():
        dump(out/mode/'merge-plan.json', plan)
    dump(out/'source-context.json', {'git_commit':commit,'reference_run':str(reference),
         'evaluator_sha256':actual,'helper_sha256':{n:hashlib.sha256((Path(__file__).parent/n).read_bytes()).hexdigest() for n in HELPERS+[BUILD_HELPER]},
         'coordinator_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    contract=build_contract(reuse_bases,prior_reservation)
    dump(out/'contract.json', dict(contract,profile='phamvanvuhoan',runtime='vm',
         new_snapshot_groups=6,verify_lifetime=VERIFY_LIFETIME,
         score_cpu=16,score_memory_gib=64,workers=8,test_timeout=600,
         score_deadline_per_mode=SCORE_DEADLINE,max_active_vms=1,ttl_seconds=TTL,
         full_500_evaluated=False,no_automatic_paid_retry=True,
         storage_not_in_one_time_compute_egress_envelope=True))
    if reuse_bases:
        _,_,groups,_,_,_=load_inputs(source)
        originals={g['index']:g for g in groups}
        originals_catalog={s['index']:s for s in json.loads(raw)['shards']}
        hashes=json.loads((reference/'snapshot-plan-hashes.json').read_text())
        strategies={}
        for mode,plan in plans.items():
            strategies[mode]={}
            for group in plan['groups']:
                if not group['requires_new_snapshot']:continue
                base=max(group['source_shard_indices'],key=lambda i:(union_bytes(originals[i]['images']),-i))
                strategies[mode][str(group['index'])]={'base_snapshot_image_id':originals_catalog[base]['snapshot_image_id'],
                    'base_snapshot_plan_sha256':hashes[str(base)],'base_source_shard_index':base,
                    'base_images':originals[base]['images']}
        dump(out/'build-strategies.json',strategies)
        context=json.loads((out/'source-context.json').read_text())
        context['build_strategies_sha256']=hashlib.sha256((out/'build-strategies.json').read_bytes()).hexdigest()
        dump(out/'source-context.json',context)


def run(out, source):
    import fcntl
    import modal
    if os.environ.get('MODAL_PROFILE') != 'phamvanvuhoan' or modal.__version__ != '1.6.2':
        raise ValueError('Requires authorized profile / qualified SDK')
    if (out/'receipt.json').exists(): raise ValueError('No automatic paid retry')
    validate_inputs(out)
    context = json.loads((out/'source-context.json').read_text())
    actual = {n:hashlib.sha256((source/n).read_bytes()).hexdigest() for n in EVALUATORS}
    if actual != context['evaluator_sha256'] or context['coordinator_sha256'] != hashlib.sha256(Path(__file__).read_bytes()).hexdigest():
        raise ValueError('Source changed after staging')
    here = Path(__file__).parent
    helper_hashes = {n:hashlib.sha256((here/n).read_bytes()).hexdigest() for n in HELPERS+[BUILD_HELPER]}
    if helper_hashes != context['helper_sha256']: raise ValueError('Helpers changed after staging')
    plans = {mode:json.loads((out/mode/'merge-plan.json').read_text()) for mode in MODES}
    if plans != {mode:plan_mode(mode) for mode in MODES}: raise ValueError('Merge plan changed')
    contract=json.loads((out/'contract.json').read_text())
    bounded=build_contract(contract['reuse_bases'],contract['prior_attempt_reservation_usd'])
    if any(contract.get(k)!=v for k,v in bounded.items()): raise ValueError('Build contract changed')
    if sum(p['summary']['new_snapshots_required'] for p in plans.values()) != 6:
        raise ValueError('Allocation contract exceeded')
    strategies=json.loads((out/'build-strategies.json').read_text()) if contract['reuse_bases'] else {}
    if contract['reuse_bases'] and hashlib.sha256((out/'build-strategies.json').read_bytes()).hexdigest()!=context.get('build_strategies_sha256'):
        raise ValueError('Build strategies changed after staging')
    original = json.loads((out/'usable-shards-catalog.json').read_text())
    if hashlib.sha256((out/'usable-shards-catalog.json').read_bytes()).hexdigest() != CATALOG_SHA:
        raise ValueError('Original catalog changed')
    old = {s['index']:s for s in original['shards']}
    old_hashes = json.loads((out/'snapshot-plan-hashes.json').read_text())
    expected = context['evaluator_sha256']
    lock = (Path(os.environ['SWE_WEB_STATE'])/'image-cache.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
    guard(source,out.name,ESTIMATE); guard(source,out.name,ESTIMATE,'planned')
    record = {'started_at':datetime.now(timezone.utc).isoformat(),'source_context':context,
              'profile':'phamvanvuhoan','budget_usd':5,'contract':contract,
              'runtime':'vm','max_active_vms':1,'full_500_evaluated':False,
              'modes':{},'terminated_vm_ids':[],'vm_attempts':[]}
    active = None
    started = time.monotonic()
    app = modal.App('swebench-n7-dual-build-and-24')

    def persist(): dump(out/'receipt.json',record)

    def create(**kwargs):
        nonlocal active
        record['vm_attempts'].append({'phase':record['phase'],'timeout':kwargs['timeout'],
                                     'created_id':None})
        persist()
        command=kwargs.pop('command',['sleep','infinity'])
        active = modal.Sandbox.create(*command,app=app,runtime='vm',**kwargs)
        record['vm_attempts'][-1]['created_id'] = active.object_id
        persist()
        return active

    def terminate():
        nonlocal active
        if active is not None:
            active.terminate(wait=True)
            record['terminated_vm_ids'].append(active.object_id)
            active = None
            persist()

    def copy_sources(sb, names):
        for name in names:
            sb.filesystem.copy_from_local(str(source/name),'/work/'+name)

    def assert_readback(sb, names):
        hashes = json.loads(execute(sb,'python','-c',
            'import hashlib,json,pathlib; names='+repr(names)+'; '
            'print(json.dumps({n:hashlib.sha256((pathlib.Path("/work")/n).read_bytes()).hexdigest() for n in names}))'))
        if hashes != {n:(expected|helper_hashes)[n] for n in names}:
            raise ValueError('Remote source read-back differs')
        return hashes

    try:
        with modal.enable_output(),ExitStack() as contexts:
            contexts.enter_context(app.run())
            record['app_id']=app.app_id;record['app_ids']={'build':app.app_id}
            guard(source,out.name,ESTIMATE,'running',app.app_id);persist()
            for mode, plan in plans.items():
                mode_out = out/mode
                status = record['modes'].setdefault(mode,{'built_groups':[],'score_groups':[]})
                record['phase']=mode+'-build';persist()
                shards = []
                try:
                    for group in plan['groups']:
                        index=group['index']
                        if not group['requires_new_snapshot']:
                            old_index=group['source_shard_indices'][0]
                            shards.append(dict(old[old_index],index=index,
                                snapshot_shard_index=old_index,snapshot_plan_sha256=old_hashes[str(old_index)],
                                reused_original_shard=old_index))
                            continue
                        case=mode_out/f'build-{index:02d}';case.mkdir()
                        selected={'schema':1,'image_count':group['image_count'],'images':group['images'],
                                  'runtime_image_id':RUNTIME,'dataset':plan['dataset'],'shard_index':index,
                                  'catalog_plan_sha256':hashlib.sha256((mode_out/'merge-plan.json').read_bytes()).hexdigest()}
                        dump(case/'catalog-plan.json',selected)
                        strategy=strategies.get(mode,{}).get(str(index))
                        base_image=strategy['base_snapshot_image_id'] if strategy else RUNTIME
                        build_cpu=contract['build_cpu'];build_lifetime=contract['build_lifetime']
                        sb=create(image=modal.Image.from_id(base_image),cpu=(build_cpu,build_cpu),memory=(4096,4096),
                                  timeout=build_lifetime,volumes={'/image-cache':modal.Volume.from_name('swebench-verified-images-v1',version=2)})
                        row={'index':index,'image_count':group['image_count'],'builder_vm':sb.object_id}
                        status['built_groups'].append(row);persist()
                        try:
                            if strategy:
                                base_sha=execute(sb,'python','-c','import pathlib,hashlib; print(hashlib.sha256(pathlib.Path("/work/catalog-plan.json").read_bytes()).hexdigest())').strip()
                                if base_sha!=strategy['base_snapshot_plan_sha256']:raise ValueError('Base read-back differs')
                                row['base_source_shard_index']=strategy['base_source_shard_index']
                            execute(sb,'mkdir','-p','/work');copy_sources(sb,EVALUATORS)
                            sb.filesystem.copy_from_local(str(here/'snapshot_catalog_worker.py'),'/work/snapshot_catalog_worker.py')
                            sb.filesystem.copy_from_local(str(case/'catalog-plan.json'),'/work/catalog-plan.json')
                            assert_readback(sb,EVALUATORS+['snapshot_catalog_worker.py'])
                            if strategy:
                                dump(case/'build-strategy.json',strategy)
                                sb.filesystem.copy_from_local(str(case/'build-strategy.json'),'/work/build-strategy.json')
                                sb.filesystem.copy_from_local(str(here/BUILD_HELPER),'/work/'+BUILD_HELPER)
                                assert_readback(sb,[BUILD_HELPER])
                            start_docker(sb)
                            command=['python','-u','/work/'+BUILD_HELPER] if strategy else ['python','-u','/work/snapshot_catalog_worker.py','preload']
                            stream(sb,command,case/'preload.log',min(650,build_lifetime-180))
                            sb.filesystem.copy_to_local('/snapshot-catalog-manifest.json',str(case/'manifest.json'))
                            stop_docker(sb)
                            captured=datetime.now(timezone.utc);t=time.monotonic()
                            image=sb.snapshot_filesystem(timeout=120,ttl=TTL)
                            row.update(snapshot_image_id=image.object_id,snapshot_seconds=time.monotonic()-t,
                                       use_before=(captured+timedelta(seconds=TTL-60)).isoformat())
                            persist()
                        finally: terminate()
                        t=time.monotonic()
                        sb=create(image=image,cpu=(2,2),memory=(4096,4096),
                            timeout=VERIFY_LIFETIME,command=['dockerd','--host=unix:///var/run/docker.sock','--storage-driver=overlay2'],
                            readiness_probe=modal.Probe.with_exec('docker','info',interval_ms=1000))
                        try:
                            sb.wait_until_ready(timeout=75)
                            row['verify_vm']=sb.object_id;row['restore_docker_ready_seconds']=time.monotonic()-t;persist()
                            stream(sb,['python','-u','/work/snapshot_catalog_worker.py','verify'],case/'verify.log',
                                   max(10,VERIFY_LIFETIME-math.ceil(time.monotonic()-t)-10))
                            sb.filesystem.copy_to_local('/catalog-verification.json',str(case/'verification.json'))
                            verification=json.loads((case/'verification.json').read_text())
                            if not verification['passed'] or verification['images_verified'] != group['image_count']:
                                raise ValueError('Incomplete image verification')
                        finally: terminate()
                        manifest=json.loads((case/'manifest.json').read_text())
                        row.update(verified=True,all_vms_terminated=True,images=manifest['images'],
                                   snapshot_shard_index=index,snapshot_plan_sha256=manifest['plan_sha256'])
                        shards.append(dict(row));persist()
                        print('N7_BUILD_PROGRESS='+json.dumps({'mode':mode,'new_groups_verified':len(status['built_groups'])}),flush=True)
                    verify_catalog({'groups':plan['groups'],'shard_count':7},shards)
                    catalog={'schema':1,'complete':True,'runtime':'vm','image_count':500,'shards':shards,
                             'mode':mode,'dataset':plan['dataset'],'snapshot_ttl_seconds':TTL}
                    dump(mode_out/'usable-shards-catalog.json',catalog)
                    status['catalog_complete']=True;persist()
                except Exception as exc:
                    status.update(build_error=type(exc).__name__+': '+str(exc));persist()
                    if active is not None or any(v['created_id'] is None for v in record['vm_attempts']): raise
                    continue

            for mode, plan in plans.items():
                status=record['modes'][mode];mode_out=out/mode
                if not status.get('catalog_complete'): continue
                app=modal.App('swebench-'+mode.lower()+'-24')
                contexts.enter_context(app.run());record['app_ids'][mode]=app.app_id
                record['phase']=mode+'-score';persist()
                deadline=time.monotonic()+SCORE_DEADLINE
                reports={}
                try:
                    catalog=json.loads((mode_out/'usable-shards-catalog.json').read_text())
                    assignments=selected_groups(catalog,validate_inputs(out),json.loads((out/'image-plan.json').read_text()))
                    status['catalog_sha256']=hashlib.sha256((mode_out/'usable-shards-catalog.json').read_bytes()).hexdigest()
                    for assignment in assignments:
                        count=len(assignment['instance_ids']);lifetime=lifetime_for(count)
                        if deadline-time.monotonic() < lifetime+10:
                            raise RuntimeError('Insufficient remaining reservation for full unchanged test-timeout waves')
                        case=mode_out/f"score-{assignment['index']:02d}";case.mkdir()
                        dump(case/'assignment.json',assignment)
                        t=time.monotonic()
                        sb=create(image=modal.Image.from_id(assignment['snapshot_image_id']),cpu=(16,16),memory=(65536,65536),
                            timeout=lifetime,command=['dockerd','--host=unix:///var/run/docker.sock','--storage-driver=overlay2'],
                            readiness_probe=modal.Probe.with_exec('docker','info',interval_ms=1000))
                        row=dict(assignment,vm_id=sb.object_id,lifetime_seconds=lifetime)
                        status['score_groups'].append(row);persist()
                        try:
                            sb.wait_until_ready(timeout=90);row['docker_ready_seconds']=time.monotonic()-t
                            copy_sources(sb,EVALUATORS)
                            for name in HELPERS: sb.filesystem.copy_from_local(str(here/name),'/work/'+name)
                            for name in ['pilot.json','preds.jsonl']: sb.filesystem.copy_from_local(str(out/name),'/work/'+name)
                            sb.filesystem.copy_from_local(str(case/'assignment.json'),'/work/assignment.json')
                            dump(case/'source-readback.json',assert_readback(sb,EVALUATORS+HELPERS))
                            budget=min(math.ceil(count/8)*600+50,lifetime-math.ceil(time.monotonic()-t)-35)
                            if budget < math.ceil(count/8)*600+10: raise RuntimeError('Startup exhausted full-timeout reserve')
                            stream(sb,['python','-u','/work/merged_pilot_worker.py'],case/'console.log',budget)
                            row['worker_passed']=True
                        finally:
                            try:
                                execute(sb,'tar','-czf','/tmp/n7-score.tar.gz','-C','/work','output',timeout=30)
                                sb.filesystem.copy_to_local('/tmp/n7-score.tar.gz',str(case/'artifacts.tar.gz'))
                                with tarfile.open(case/'artifacts.tar.gz') as archive: archive.extractall(case,filter='data')
                            except Exception as exc: row['artifact_error']=type(exc).__name__+': '+str(exc)
                            finally:
                                terminate();row['terminated']=True;row['wall_seconds']=time.monotonic()-t;persist()
                        if row.get('artifact_error'): raise RuntimeError('Artifact collection failed')
                        found=json.loads((case/'output/reports.json').read_text())
                        if set(found) != set(assignment['instance_ids']) or set(found)&reports.keys(): raise ValueError('Report coverage differs')
                        reports.update(found);dump(mode_out/'reports.json',reports)
                        print('N7_SCORE_PROGRESS='+json.dumps({'mode':mode,'completed':len(reports),'requested':24}),flush=True)
                    comparison=compare([r['instance_id'] for r in validate_inputs(out)['selected']],
                                       json.loads((out/'baseline-reports.json').read_text()),reports)
                    status.update(complete=len(reports)==24,comparison=comparison,
                                  report_parity=comparison['complete_report_matches']==24 and not comparison['different_report_ids'])
                except Exception as exc:
                    status.update(complete=False,score_error=type(exc).__name__+': '+str(exc))
                status['score_wall_seconds']=SCORE_DEADLINE-(deadline-time.monotonic());persist()
            record['complete']=all(s.get('complete') for s in record['modes'].values())
            record['report_parity']=all(s.get('report_parity') for s in record['modes'].values())
    except Exception as exc:
        record.update(complete=False,error=type(exc).__name__+': '+str(exc))
    finally:
        terminate()
        record.update(all_vms_terminated=all(v['created_id'] in record['terminated_vm_ids'] for v in record['vm_attempts']),
                      finished_at=datetime.now(timezone.utc).isoformat(),wall_seconds=time.monotonic()-started)
        persist();guard(source,out.name,ESTIMATE,'completed' if record.get('complete') else 'failed');lock.close()
    return 0 if record.get('complete') and record.get('report_parity') else 1


def collect(out,name):
    import modal
    record=json.loads((out/'receipt.json').read_text())
    remaining={role:[s.object_id for s in modal.Sandbox.list(app_id=app_id)]
               for role,app_id in record['app_ids'].items()}
    if any(remaining.values()) or not record['all_vms_terminated']:
        raise ValueError('VM cleanup not confirmed')
    now=datetime.now(timezone.utc)
    rows=json.loads(subprocess.check_output([sys.executable,'-m','modal','billing','report',
        '--start',record['started_at'][:10],'--end',(now+timedelta(days=1)).strftime('%Y-%m-%d'),
        '--show-resources','--json'],text=True,timeout=90))
    rows=[r for r in rows if r.get('object_id') in record['app_ids'].values()]
    missing=[role for role,app_id in record['app_ids'].items() if not any(r['object_id']==app_id for r in rows)]
    result={'checked_at':now.isoformat(),'app_ids':record['app_ids'],'rows':rows,
            'cost_by_role_usd':{role:str(sum((Decimal(str(r['cost'])) for r in rows if r['object_id']==app_id),Decimal(0)))
                                for role,app_id in record['app_ids'].items()},
            'missing_billing_roles':missing,'before_credits':True,'not_final_invoice':True,
            'snapshot_storage_not_in_app_sum':True,'remaining_vms':remaining}
    result['compute_egress_usd']=str(sum((Decimal(c) for c in result['cost_by_role_usd'].values()),Decimal(0)))
    dump(out/name,result)
    print(json.dumps({k:v for k,v in result.items() if k!='rows'},indent=2))


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['prepare','run','collect']);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--source',type=Path);p.add_argument('--reference',type=Path);p.add_argument('--commit')
    p.add_argument('--billing-name',default='billing.json')
    p.add_argument('--reuse-bases',action='store_true')
    p.add_argument('--prior-reservation',type=float,default=0.)
    a=p.parse_args()
    if a.action=='collect': collect(a.out.resolve(),a.billing_name)
    elif a.action=='prepare':
        if not a.source or not a.reference or not a.commit: p.error('prepare needs source, reference and commit')
        prepare(a.out.resolve(),a.source.resolve(),a.reference.resolve(),a.commit,a.reuse_bases,a.prior_reservation)
    else:
        if not a.source: p.error('run needs source')
        raise SystemExit(run(a.out.resolve(),a.source.resolve()))

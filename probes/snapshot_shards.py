"""Save/restore each small shard before advancing; one finite VM at a time."""
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
import time

from snapshot_pilot import RUNTIME, dump, execute, guard, start_docker, stream
from snapshot_catalog import stop_docker
from snapshot_catalog_worker import TTL, GIB, batches_of, layers_of, validate_rows

RATE = 2*.00003942 + 4*.00000667
DEADLINE_SECONDS = 5700
ESTIMATE = .65
FROZEN_CATALOG_SHA = '2a3831d16e29024f85e732f53200ea863917d68acaf207bcdbdb859c02a59dc3'


def shard_plan(catalog):
    rows=catalog['images']
    validate_rows(rows)
    if catalog['image_count'] != 500 or catalog['snapshot_ttl_seconds'] != TTL:
        raise ValueError('Expected frozen 500-image/90-day source plan')
    groups=batches_of(rows,max_images=100,max_bytes=35*GIB)
    return {'schema':1,'runtime_image_id':RUNTIME,'profile':'phamvanvuhoan',
            'dataset':catalog['dataset'],'snapshot_ttl_seconds':TTL,'image_count':500,
            'shard_count':len(groups),'cpu':2,'memory_gib':4,
            'overall_deadline_seconds':DEADLINE_SECONDS,
            'compute_rate_usd_per_second':RATE,
            'sequential_allocation_bound_usd':RATE*DEADLINE_SECONDS,
            'estimate_including_margin_usd':ESTIMATE,
            'groups':[dict(index=i,image_count=len(g),images=g,
                       layer_tar_bytes=sum(b['uncompressed_size'] for b in layers_of(g).values()))
                      for i,g in enumerate(groups)]}


def verify_catalog(plan,shards):
    expected={r['reference']:r['id'] for g in plan['groups'] for r in g['images']}
    groups={g['index']:g for g in plan['groups']}
    found={}; seen_groups=set(); snapshots=set()
    if len(shards) != plan['shard_count']:
        raise ValueError('Incomplete shard count')
    for shard in shards:
        if shard.get('verified') is not True or shard.get('all_vms_terminated') is not True:
            raise ValueError('Unverified shard or missing termination confirmation')
        index=shard.get('index')
        if index not in groups or index in seen_groups:
            raise ValueError('Unknown or duplicate shard index')
        seen_groups.add(index)
        group=groups[index]
        rows=shard['images']
        actual={r['reference']:r['id'] for r in rows}
        required={r['reference']:r['id'] for r in group['images']}
        if (shard.get('image_count') != group['image_count'] or len(rows) != group['image_count']
                or len(actual) != len(rows) or actual != required):
            raise ValueError('Image coverage differs from its verified shard plan')
        snapshot=shard.get('snapshot_image_id')
        if not isinstance(snapshot,str) or not snapshot.startswith('im-') or snapshot in snapshots:
            raise ValueError('Missing or reused snapshot image ID')
        snapshots.add(snapshot)
        for row in shard['images']:
            if row['reference'] in found:
                raise ValueError('Duplicate coverage across shards')
            found[row['reference']]=row['id']
    if found != expected or len(found) != 500:
        raise ValueError('Missing or changed images across shards')
    return True


def build(out,source):
    import fcntl
    import modal
    if os.environ.get('MODAL_PROFILE')!='phamvanvuhoan' or modal.__version__!='1.6.2':
        raise ValueError('Requires scoped, qualified profile/SDK')
    original=json.loads((out/'catalog-plan.json').read_text())
    if hashlib.sha256((out/'catalog-plan.json').read_bytes()).hexdigest()!=FROZEN_CATALOG_SHA:
        raise ValueError('Only the previously frozen, independently verified 500-image plan is authorized')
    plan=shard_plan(original)
    dump(out/'shard-plan.json',plan)
    if (out/'shard-receipt.json').exists():
        raise ValueError('Run already started; no automatic paid retry')
    canary=json.loads((out/'retention-probe.json').read_text())
    if not canary.get('passed') or canary.get('ttl_seconds')!=TTL or not canary.get('sandbox_terminated'):
        raise ValueError('90-day TTL probe must pass and terminate before loading data')
    lock=(Path(os.environ['SWE_WEB_STATE'])/'image-cache.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    label=out.name
    guard(source,label,ESTIMATE)
    guard(source,label,ESTIMATE,'planned')
    here=Path(__file__).resolve().parent
    record={'started_at':datetime.now(timezone.utc).isoformat(),'profile':'phamvanvuhoan',
            'original_catalog_plan_sha256':FROZEN_CATALOG_SHA,
            'plan_sha256':hashlib.sha256((out/'shard-plan.json').read_bytes()).hexdigest(),
            'overall_deadline_seconds':DEADLINE_SECONDS,'max_simultaneous_vms':1,
            'allocation_bound_usd':RATE*DEADLINE_SECONDS,'source_context':json.loads((out/'source-context.json').read_text()),
            'source_sha256':{n:hashlib.sha256((source/n).read_bytes()).hexdigest()
                             for n in ['core.py','image_cache.py','registry_cache.py']},
            'helper_sha256':{n:hashlib.sha256((here/n).read_bytes()).hexdigest()
                              for n in ['snapshot_shards.py','snapshot_catalog_worker.py','snapshot_catalog.py','snapshot_pilot.py']},
            'shards':[],'registry_secrets_injected':False,'full_500_evaluated':False}
    started=time.monotonic()
    deadline=started+DEADLINE_SECONDS
    app=modal.App('swebench-verified-500-shards-90d')
    active=None

    def persist():
        record['updated_at']=datetime.now(timezone.utc).isoformat()
        dump(out/'shard-receipt.json',record)

    def remaining(maximum,minimum):
        seconds=min(maximum,math.floor(deadline-time.monotonic())-30)
        if seconds<minimum:
            raise RuntimeError('Overall bounded deadline reached; retaining completed shards without paid retry')
        return seconds

    def terminate_active():
        nonlocal active
        if active is not None:
            active.terminate(wait=True)
            record.setdefault('terminated_sandbox_ids',[]).append(active.object_id)
            active=None

    try:
        with modal.enable_output(),app.run():
            record['app_id']=app.app_id
            guard(source,label,ESTIMATE,'running',app.app_id)
            persist()
            try:
                for group in plan['groups']:
                    case=out/f"shard-{group['index']:02d}"
                    case.mkdir()
                    selected={'schema':1,'image_count':group['image_count'],'images':group['images'],
                              'runtime_image_id':RUNTIME,'dataset':plan['dataset'],
                              'catalog_plan_sha256':record['plan_sha256'],'shard_index':group['index']}
                    dump(case/'catalog-plan.json',selected)
                    lifetime=remaining(900,180)
                    active=modal.Sandbox.create('sleep','infinity',app=app,image=modal.Image.from_id(RUNTIME),
                          runtime='vm',cpu=(2,2),memory=(4096,4096),timeout=lifetime,
                          volumes={'/image-cache':modal.Volume.from_name('swebench-verified-images-v1',version=2)})
                    shard={'index':group['index'],'image_count':group['image_count'],
                           'started_at':datetime.now(timezone.utc).isoformat(),'builder_vm':active.object_id,
                           'builder_lifetime_seconds':lifetime,'snapshot_ttl_seconds':TTL}
                    record['current_shard']=shard
                    persist()
                    execute(active,'mkdir','-p','/work')
                    for name in record['source_sha256']:
                        active.filesystem.copy_from_local(str(source/name),'/work/'+name)
                    active.filesystem.copy_from_local(str(here/'snapshot_catalog_worker.py'),'/work/snapshot_catalog_worker.py')
                    active.filesystem.copy_from_local(str(case/'catalog-plan.json'),'/work/catalog-plan.json')
                    start_docker(active)
                    stream(active,['python','-u','/work/snapshot_catalog_worker.py','preload'],case/'preload.log',max(60,lifetime-180))
                    active.filesystem.copy_to_local('/snapshot-catalog-manifest.json',str(case/'manifest.json'))
                    stop_docker(active)
                    captured=datetime.now(timezone.utc)
                    t=time.monotonic()
                    image=active.snapshot_filesystem(timeout=120,ttl=TTL)
                    shard.update(snapshot_image_id=image.object_id,snapshot_seconds=time.monotonic()-t,
                                 use_before=(captured+timedelta(seconds=TTL-60)).isoformat())
                    persist()
                    terminate_active()
                    # No Volume, no credentials, and no image-loading fallback on restoration.
                    lifetime=remaining(120,60)
                    t=time.monotonic()
                    active=modal.Sandbox.create('dockerd','--host=unix:///var/run/docker.sock','--storage-driver=overlay2',
                         app=app,image=image,runtime='vm',cpu=(2,2),memory=(4096,4096),timeout=lifetime,
                         readiness_probe=modal.Probe.with_exec('docker','info',interval_ms=1000))
                    shard['verify_vm']=active.object_id
                    persist()
                    active.wait_until_ready(timeout=min(75,lifetime-30))
                    shard['restore_docker_ready_seconds']=time.monotonic()-t
                    verification_seconds=min(90,lifetime-math.ceil(time.monotonic()-t)-10)
                    if verification_seconds<10:raise RuntimeError('Insufficient remaining verification lifetime')
                    stream(active,['python','-u','/work/snapshot_catalog_worker.py','verify'],case/'verify.log',verification_seconds)
                    active.filesystem.copy_to_local('/catalog-verification.json',str(case/'verification.json'))
                    verified=json.loads((case/'verification.json').read_text())
                    if not verified['passed'] or verified['images_verified']!=group['image_count']:
                        raise ValueError('Shard restoration did not verify every image')
                    terminate_active()
                    shard.update(verified=True,all_vms_terminated=True,
                                 images=json.loads((case/'manifest.json').read_text())['images'])
                    record['shards'].append(shard)
                    record.pop('current_shard',None)
                    persist()
                    print('SNAPSHOT_SHARDS_PROGRESS='+json.dumps({'saved_shards':len(record['shards']),
                         'requested_shards':len(plan['groups']),'verified_images':sum(s['image_count'] for s in record['shards']),
                         'requested_images':500}),flush=True)
                verify_catalog(plan,record['shards'])
                record['passed']=True
                dump(out/'usable-shards-catalog.json',{'schema':1,'complete':True,'runtime':'vm',
                      'dataset':plan['dataset'],'image_count':500,'snapshot_ttl_seconds':TTL,
                      'source_context':record['source_context'],'shards':record['shards']})
            finally:
                terminate_active()
                record['all_vms_terminated']=active is None
    except Exception as exc:
        record.update(passed=False,error=type(exc).__name__+': '+str(exc))
    finally:
        record.update(finished_at=datetime.now(timezone.utc).isoformat(),wall_seconds=time.monotonic()-started)
        persist()
        guard(source,label,ESTIMATE,'completed' if record.get('passed') else 'failed')
        lock.close()
    return 0 if record.get('passed') else 1


def collect(out,name):
    import modal
    receipt=json.loads((out/'shard-receipt.json').read_text())
    plan=json.loads((out/'shard-plan.json').read_text())
    if not receipt.get('passed') or not receipt.get('all_vms_terminated'):
        raise ValueError('Complete verified catalog and confirmed termination required')
    verify_catalog(plan,receipt['shards'])
    remaining=[s.object_id for s in modal.Sandbox.list(app_id=receipt['app_id'])]
    if remaining:raise ValueError('Remaining live VM')
    now=datetime.now(timezone.utc)
    rows=json.loads(subprocess.check_output([sys.executable,'-m','modal','billing','report',
          '--start',receipt['started_at'][:10],'--end',(now+timedelta(days=1)).strftime('%Y-%m-%d'),
          '--show-resources','--json'],text=True,timeout=90))
    rows=[r for r in rows if r.get('object_id')==receipt['app_id']]
    if not rows:raise ValueError('Provider billing not available yet')
    result={'checked_at':now.isoformat(),'app_id':receipt['app_id'],'rows':rows,
            'compute_egress_usd':str(sum((Decimal(str(r['cost'])) for r in rows),Decimal(0))),
            'before_credits':True,'not_final_invoice':True,'snapshot_storage_not_in_app_sum':True,
            'remaining_sandboxes':remaining,'image_count':500,'shard_count':len(receipt['shards'])}
    dump(out/name,result)
    print(json.dumps({k:v for k,v in result.items() if k!='rows'},indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['build','collect'])
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--source',type=Path)
    parser.add_argument('--billing-name',default='billing.json')
    args=parser.parse_args()
    if args.action=='build':
        if not args.source:parser.error('build requires --source')
        raise SystemExit(build(args.out.resolve(),args.source.resolve()))
    collect(args.out.resolve(),args.billing_name)

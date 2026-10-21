"""Build a bounded, 90-day filesystem snapshot of all 500 cached images.

Reuses the qualified runtime; no registry credentials, image builds or grading.
Run only on the existing GCP host with its scoped Modal service environment.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from snapshot_pilot import RUNTIME, dump, execute, guard, start_docker, stream
from snapshot_catalog_worker import TTL, GIB, batches_of, layers_of, validate_rows

BUILD_LIFETIME = 7200
VERIFY_LIFETIME = 600
BUILD_BOUND = BUILD_LIFETIME * (2 * .00003942 + 8 * .00000667)
VERIFY_BOUND = VERIFY_LIFETIME * (2 * .00003942 + 4 * .00000667)
ESTIMATE = 1.10


def make_plan(rows, dataset):
    validate_rows(rows)
    if (dataset.get('count') != 500 or dataset.get('dataset') != 'SWE-bench/SWE-bench_Verified'
            or dataset.get('revision') != '78f471bf655a3137b2e8a75af1501690ec009ec3'
            or dataset.get('sha256') != '6a6fb72484bfc40f35cecbae1ae4615e9460905f729c57659df5f4ef9d7dec6e'):
        raise ValueError('Expected pinned 500-instance dataset provenance')
    unique = sum(b['uncompressed_size'] for b in layers_of(rows).values())
    if unique > 350 * GIB:
        raise ValueError('Image store exceeds conservative 512-GiB root-disk bound')
    batches = batches_of(rows)
    return {'schema': 1, 'runtime_image_id': RUNTIME, 'profile': 'phamvanvuhoan',
            'dataset': dataset, 'image_count': 500, 'snapshot_ttl_seconds': TTL,
            'unique_layer_tar_bytes': unique, 'batch_count': len(batches),
            'total_streamed_layer_bytes': sum(sum(b['uncompressed_size'] for b in layers_of(x).values()) for x in batches),
            'build_lifetime_seconds': BUILD_LIFETIME, 'verify_lifetime_seconds': VERIFY_LIFETIME,
            'compute_allocation_bound_usd': BUILD_BOUND + VERIFY_BOUND,
            'estimate_including_egress_reserve_usd': ESTIMATE,
            'full_500_evaluated': False, 'images': sorted(rows, key=lambda r:r['reference'])}


def stop_docker(sb):
    execute(sb, 'python', '-c', "import pathlib,os,signal,time; "
            "p=int(pathlib.Path('/run/snapshot-dockerd.pid').read_text()); "
            "assert p>1 and b'dockerd' in pathlib.Path(f'/proc/{p}/cmdline').read_bytes(); "
            "os.kill(p,signal.SIGTERM); "
            "\nfor _ in range(600):\n q=pathlib.Path(f'/proc/{p}/stat'); "
            "\n if not q.exists() or q.read_text().split()[2]=='Z':break\n time.sleep(.1)"
            "\nelse:raise RuntimeError('Docker did not stop cleanly')", timeout=75)
    execute(sb, 'sh', '-c', '! docker info >/dev/null 2>&1 && sync')


def build(out, source):
    import fcntl
    import modal
    if os.environ.get('MODAL_PROFILE') != 'phamvanvuhoan' or modal.__version__ != '1.6.2':
        raise ValueError('Use scoped phamvanvuhoan environment and qualified SDK 1.6.2')
    plan_path = out / 'catalog-plan.json'
    plan = json.loads(plan_path.read_text())
    if plan != make_plan(plan['images'], plan['dataset']):
        raise ValueError('Plan differs from bounded 500-image/90-day contract')
    if (out / 'catalog-receipt.json').exists():
        raise ValueError('Run already started; no automatic paid retry')
    lock = (Path(os.environ['SWE_WEB_STATE'])/'image-cache.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    label = out.name
    guard(source, label, ESTIMATE)
    guard(source, label, ESTIMATE, 'planned')
    here = Path(__file__).resolve().parent
    sources = {name: hashlib.sha256((source/name).read_bytes()).hexdigest()
               for name in ['core.py','image_cache.py','registry_cache.py']}
    helpers = {name:hashlib.sha256((here/name).read_bytes()).hexdigest()
               for name in ['snapshot_catalog.py','snapshot_catalog_worker.py','snapshot_pilot.py']}
    receipt = {'started_at':datetime.now(timezone.utc).isoformat(), 'profile':'phamvanvuhoan',
               'plan_sha256':hashlib.sha256(plan_path.read_bytes()).hexdigest(),
               'source_sha256':sources, 'helper_sha256':helpers, 'snapshot_ttl_seconds':TTL,
               'compute_allocation_bound_usd':BUILD_BOUND+VERIFY_BOUND,
               'full_500_evaluated':False, 'registry_secrets_injected':False,
               'build_cpu':2, 'build_memory_gib':8, 'verify_cpu':2, 'verify_memory_gib':4}
    app = modal.App('swebench-verified-500-snapshot-90d')
    sandboxes = []
    started = time.monotonic()
    try:
        with modal.enable_output(), app.run():
            receipt['app_id'] = app.app_id
            guard(source, label, ESTIMATE, 'running', app.app_id)
            dump(out/'catalog-receipt.json', receipt)
            try:
                sb = modal.Sandbox.create('sleep','infinity',app=app,image=modal.Image.from_id(RUNTIME),
                     runtime='vm',cpu=(2,2),memory=(8192,8192),timeout=BUILD_LIFETIME,
                     volumes={'/image-cache':modal.Volume.from_name('swebench-verified-images-v1',version=2)})
                sandboxes.append(sb)
                receipt['build_sandbox_id'] = sb.object_id
                dump(out/'catalog-receipt.json',receipt)
                execute(sb,'mkdir','-p','/work')
                for name in sources:
                    sb.filesystem.copy_from_local(str(source/name),'/work/'+name)
                sb.filesystem.copy_from_local(str(here/'snapshot_catalog_worker.py'),'/work/snapshot_catalog_worker.py')
                sb.filesystem.copy_from_local(str(plan_path),'/work/catalog-plan.json')
                start_docker(sb)
                stream(sb,['python','-u','/work/snapshot_catalog_worker.py','preload'],out/'catalog-preload.log',6300)
                sb.filesystem.copy_to_local('/snapshot-catalog-manifest.json',str(out/'catalog-manifest.json'))
                stop_docker(sb)
                t = time.monotonic()
                snapshot_requested_at = datetime.now(timezone.utc)
                image = sb.snapshot_filesystem(timeout=450,ttl=TTL)
                captured = datetime.now(timezone.utc)
                receipt.update(snapshot_image_id=image.object_id,snapshot_seconds=time.monotonic()-t,
                     captured_at=captured.isoformat(),expires_at_latest=(captured+timedelta(seconds=TTL)).isoformat())
                # Conservative usable deadline measured before the snapshot call.
                receipt['use_before'] = (snapshot_requested_at+timedelta(seconds=TTL-60)).isoformat()
                dump(out/'catalog-receipt.json',receipt)
                sb.terminate(wait=True)
                receipt['build_sandbox_terminated'] = True
                # This new VM has no Volume, no registry secret, and no download path.
                t = time.monotonic()
                restored = modal.Sandbox.create('dockerd','--host=unix:///var/run/docker.sock','--storage-driver=overlay2',
                      app=app,image=image,runtime='vm',cpu=(2,2),memory=(4096,4096),timeout=VERIFY_LIFETIME,
                      readiness_probe=modal.Probe.with_exec('docker','info',interval_ms=1000))
                sandboxes.append(restored)
                receipt['verify_sandbox_id'] = restored.object_id
                dump(out/'catalog-receipt.json',receipt)
                restored.wait_until_ready(timeout=180)
                receipt['restore_docker_ready_seconds'] = time.monotonic()-t
                stream(restored,['python','-u','/work/snapshot_catalog_worker.py','verify'],out/'catalog-verify.log',300)
                restored.filesystem.copy_to_local('/catalog-verification.json',str(out/'catalog-verification.json'))
                verification=json.loads((out/'catalog-verification.json').read_text())
                if not verification['passed'] or verification['images_verified'] != 500:
                    raise ValueError('Restored catalog verification incomplete')
                receipt['passed'] = True
            finally:
                terminated = []
                for vm in sandboxes:
                    if not receipt.get('build_sandbox_terminated') and vm.object_id == receipt.get('build_sandbox_id'):
                        for remote,local in [('/work/catalog-progress.json','failed-progress.json'),
                                             ('/tmp/snapshot-dockerd.log','failed-dockerd.log')]:
                            try:
                                vm.filesystem.copy_to_local(remote,str(out/local))
                            except Exception:
                                pass
                    vm.terminate(wait=True)
                    terminated.append(vm.object_id)
                receipt['terminated_sandbox_ids'] = terminated
                receipt['all_sandboxes_terminated'] = len(terminated)==len(sandboxes)
    except Exception as exc:
        receipt.update(passed=False,error=type(exc).__name__+': '+str(exc))
    finally:
        receipt.update(finished_at=datetime.now(timezone.utc).isoformat(),wall_seconds=time.monotonic()-started)
        dump(out/'catalog-receipt.json',receipt)
        guard(source,label,ESTIMATE,'completed' if receipt.get('passed') else 'failed')
        lock.close()
    return 0 if receipt.get('passed') else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['plan','build'])
    parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--source',type=Path)
    parser.add_argument('--manifests',type=Path)
    parser.add_argument('--dataset-manifest',type=Path)
    args=parser.parse_args()
    if args.action=='plan':
        args.out.mkdir(exist_ok=True,parents=True)
        plan=make_plan(json.loads(args.manifests.read_text()),json.loads(args.dataset_manifest.read_text()))
        dump(args.out/'catalog-plan.json',plan)
        print(json.dumps({k:v for k,v in plan.items() if k!='images'},indent=2))
        return 0
    if not args.source:
        parser.error('build requires --source')
    return build(args.out.resolve(),args.source.resolve())


if __name__=='__main__':
    raise SystemExit(main())

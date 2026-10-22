"""Fixed two-VM diagnosis, within the prior cumulative $8 compute envelope.

Per issue: two original full-suite evaluations and two observed full suites,
all in fresh containers. Diagnostics never overwrite original scientific reports.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys
import tarfile
import time
from types import SimpleNamespace

from snapshot_pilot import dump, execute, guard, stream, validate_inputs

IDS = ('psf__requests-1921', 'matplotlib__matplotlib-20859')
LIFETIME = 2900  # Four complete 600s test windows plus setup/readiness/collection.
PRIOR_COST = 1.30621108
RATE = 16 * .00003942 + 64 * .00000667
ESTIMATE = 2 * LIFETIME * RATE + .08


def observed_script(text):
    lines = text.splitlines(keepends=True)
    selected = [i for i, s in enumerate(lines) if s.startswith('pytest ')]
    if len(selected) != 1:
        raise ValueError('Expected exactly one pytest invocation')
    i = selected[0]
    lines[i] = ('PYTHONPATH=/tmp/stability-observer STABILITY_OBSERVER_OUTPUT=/tmp/stability.json '
                + lines[i].rstrip('\n') + ' -p stability_observer\n')
    return ''.join(lines)


def worker(iid):
    import docker
    import remote_worker as base
    from swebench.harness.docker_build import build_container, setup_logger
    from swebench.harness.docker_utils import copy_to_container, cleanup_container, exec_run_with_timeout
    from swebench.harness.test_spec.test_spec import make_test_spec
    root = Path('/work'); out = root/'stability-output'; out.mkdir()
    data = json.loads((root/'pilot.json').read_text())
    predictions = [json.loads(s) for s in (root/'preds.jsonl').read_text().splitlines()]
    row = next(r for r in data if r['instance_id'] == iid)
    pred = next(r for r in predictions if r['instance_id'] == iid)
    spec = make_test_spec(row, namespace='swebench')
    reference = (root/'reference-eval.sh').read_text()
    if spec.eval_script != reference:
        raise ValueError('Original eval script differs')
    client = docker.from_env()
    expected = json.loads((root/'assignment.json').read_text())['image']
    if client.images.get(spec.instance_image_key).id != expected['id']:
        raise ValueError('Wrong cached image')
    dump(out/'hardware.json', {'cpu_models': sorted({s.split(':', 1)[1].strip()
         for s in Path('/proc/cpuinfo').read_text().splitlines() if s.startswith('model name')}),
         'cpu_count': os.cpu_count(), 'epoch': time.time()})
    base.OUT = out/'official'; base.OUT.mkdir()
    config = SimpleNamespace(workers=8, test_timeout=600, mode='smoke', cache=None, require_cached=True)
    for n in range(2):
        base.evaluate([pred], [iid], root/'pilot.json', f'official-{n}', config)
    for n in range(2):
        case = out/f'observed-{n}'; case.mkdir()
        logger = setup_logger(f'stability-{n}', case/'container.log')
        container = None
        try:
            container = build_container(spec, client, f'stability-{n}', logger, False, False)
            container.start()
            # Minimal diagnostic inventory; never dump environment or credentials.
            inventory = container.exec_run(['bash', '-lc',
                'cd /testbed; for f in conftest.py pytest.ini setup.cfg; do '
                'if test -f "$f"; then echo "FILE:$f"; cat "$f"; fi; done; '
                'find /testbed -maxdepth 2 -name conftest.py; '
                '/opt/miniconda3/envs/testbed/bin/python -m pip show pytest pytest-httpbin httpbin requests'])
            (case/'inventory.txt').write_bytes(inventory.output)
            (case/'gold.patch').write_text(pred['model_patch'])
            copy_to_container(container, case/'gold.patch', PurePosixPath('/tmp/gold.patch'))
            applied = container.exec_run('git apply --verbose /tmp/gold.patch', workdir='/testbed', user='root')
            (case/'apply.log').write_bytes(applied.output)
            if applied.exit_code:
                raise ValueError('Gold patch did not apply')
            container.exec_run('mkdir -p /tmp/stability-observer')
            copy_to_container(container, root/'stability_observer.py',
                              PurePosixPath('/tmp/stability-observer/stability_observer.py'))
            (case/'eval-original.sh').write_text(reference)
            (case/'eval-observed.sh').write_text(observed_script(reference))
            copy_to_container(container, case/'eval-observed.sh', PurePosixPath('/eval.sh'))
            text, timed_out, elapsed = exec_run_with_timeout(container, '/bin/bash /eval.sh', 600)
            (case/'test_output.txt').write_text(text)
            dump(case/'timing.json', {'timed_out': timed_out, 'elapsed': elapsed})
            observed = container.exec_run('cat /tmp/stability.json')
            if observed.exit_code or timed_out:
                raise RuntimeError('Observer failed or test timed out')
            result = json.loads(observed.output)
            if result['file_hashes_before'] != result['file_hashes_after']:
                raise ValueError('Test/implementation files changed')
            dump(case/'observer.json', result)
        finally:
            cleanup_container(client, container, logger)
    remaining = [c.id for c in client.containers.list(all=True)]
    if remaining:
        raise ValueError('Remaining test containers')
    dump(out/'summary.json', {'complete': True, 'iid': iid, 'original_full_suites': 2,
         'observed_full_suites': 2, 'diagnostic_only': True, 'original_reports_preserved': True})


def run(out, source, reference):
    import modal
    if os.environ.get('MODAL_PROFILE') != 'phamvanvuhoan' or modal.__version__ != '1.6.2':
        raise ValueError('Unauthorized profile/SDK')
    if PRIOR_COST + ESTIMATE > 8 or out.exists():
        raise ValueError('Budget exceeded or output already exists; no retry')
    validate_inputs(reference)
    out.mkdir()
    here = Path(__file__).resolve().parent
    catalog = json.loads((source/'evidence/2026-10-22-n7-merged-qualification/optimizeN7merge-catalog.json').read_text())
    expected = json.loads((reference/'source-context.json').read_text())['evaluator_sha256']
    for name, sha in expected.items():
        if hashlib.sha256((source/name).read_bytes()).hexdigest() != sha:
            raise ValueError('Evaluator changed')
    guard(source, out.name, ESTIMATE); guard(source, out.name, ESTIMATE, 'planned')
    receipt = {'started_at': datetime.now(timezone.utc).isoformat(), 'estimate_usd': ESTIMATE,
               'prior_cost_usd': PRIOR_COST, 'combined_bound_usd': PRIOR_COST+ESTIMATE,
               'profile': 'phamvanvuhoan', 'max_vms': 2, 'lifetime_each': LIFETIME,
               'cpu': 16, 'memory_gib': 64, 'workers': 8, 'test_timeout': 600,
               'full_500_evaluated': False, 'attempts': [], 'terminated': []}
    app = modal.App('swebench-two-case-stability-diagnosis'); active = None
    try:
        with modal.enable_output(), app.run():
            receipt['app_id'] = app.app_id
            guard(source, out.name, ESTIMATE, 'running', app.app_id)
            for iid in IDS:
                case = out/iid; case.mkdir()
                group = next(g for g in catalog['shards'] if any(r['instance_id'] == iid for r in g['images']))
                image = next(r for r in group['images'] if r['instance_id'] == iid)
                dump(case/'assignment.json', {'image': image, 'snapshot': group['snapshot_image_id']})
                receipt['attempts'].append({'iid': iid, 'snapshot': group['snapshot_image_id']})
                dump(out/'receipt.json', receipt)
                try:
                    active = modal.Sandbox.create('dockerd', '--host=unix:///var/run/docker.sock', '--storage-driver=overlay2',
                        app=app, image=modal.Image.from_id(group['snapshot_image_id']), runtime='vm',
                        cpu=(16,16), memory=(65536,65536), timeout=LIFETIME,
                        readiness_probe=modal.Probe.with_exec('docker', 'info', interval_ms=1000))
                    receipt['attempts'][-1]['vm_id'] = active.object_id
                    dump(out/'receipt.json', receipt)
                    active.wait_until_ready(timeout=90)
                    files = {n: source/n for n in expected}
                    files.update({n: here/n for n in ['stability_diagnosis.py','stability_observer.py','snapshot_pilot.py']})
                    files.update({n: reference/n for n in ['pilot.json','preds.jsonl']})
                    files['assignment.json'] = case/'assignment.json'
                    originals = list(reference.glob('optimizeN7merge/score-*/output/**/'+iid+'/eval.sh'))
                    if len(originals) != 1:
                        raise ValueError('Ambiguous original script')
                    files['reference-eval.sh'] = originals[0]
                    hashes = {n: hashlib.sha256(p.read_bytes()).hexdigest() for n,p in files.items()}
                    dump(case/'source-input-hashes.json', hashes)
                    for n,p in files.items(): active.filesystem.copy_from_local(str(p), '/work/'+n)
                    actual = json.loads(execute(active, 'python', '-c',
                        'import hashlib,pathlib,json; names='+repr(list(files))+'; '
                        'print(json.dumps({n:hashlib.sha256((pathlib.Path("/work")/n).read_bytes()).hexdigest() for n in names}))'))
                    if actual != hashes: raise ValueError('Source/input readback failed')
                    dump(case/'source-readback.json', actual)
                    stream(active, ['python','-u','/work/stability_diagnosis.py','worker','--iid',iid], case/'worker.log', 2700)
                finally:
                    if active is not None:
                        try:
                            execute(active, 'tar','-czf','/tmp/stability-output.tar.gz','-C','/work','stability-output',timeout=30)
                            active.filesystem.copy_to_local('/tmp/stability-output.tar.gz',str(case/'artifacts.tar.gz'))
                            with tarfile.open(case/'artifacts.tar.gz') as archive: archive.extractall(case,filter='data')
                        finally:
                            active.terminate(wait=True)
                            receipt['terminated'].append(active.object_id); active=None
                            dump(out/'receipt.json',receipt)
            receipt['complete'] = True
    except Exception as exc:
        receipt['error'] = type(exc).__name__+': '+str(exc)
    finally:
        receipt['finished_at'] = datetime.now(timezone.utc).isoformat()
        dump(out/'receipt.json',receipt)
        guard(source,out.name,ESTIMATE,'completed' if receipt.get('complete') else 'failed')
    return 0 if receipt.get('complete') else 1


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['run','worker'])
    p.add_argument('--iid',choices=IDS)
    for key in ['out','source','reference']: p.add_argument('--'+key,type=Path)
    a=p.parse_args()
    if a.action=='worker': worker(a.iid)
    else: raise SystemExit(run(a.out.resolve(),a.source.resolve(),a.reference.resolve()))

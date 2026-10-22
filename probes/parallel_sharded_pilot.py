"""Scheduling-only N11 experiment: frozen 24 controls, at most two VMs, $2 cap."""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import tarfile
import threading
import time

from sharded_pilot import CATALOG_SHA, RATE, make_plan
from snapshot_pilot import dump, execute, guard, stream, validate_inputs
from summarize_snapshot_pilot import compare

LIFETIME = 780
TOTAL_SECONDS = 1800
MAX_ACTIVE = 2
ESTIMATE = 2


class Reservations:
    """Reserve full VM lifetime before creation; never release uncertain outcomes."""
    def __init__(self):
        self.spent = 0.
        self.active = {}
        self.uncertain = False
        self.history = []

    def admit(self, index):
        if (self.uncertain or index in self.active or len(self.active) >= MAX_ACTIVE
                or self.spent + sum(self.active.values()) + LIFETIME > TOTAL_SECONDS):
            return False
        self.active[index] = LIFETIME
        self.snapshot('reserve', index)
        return True

    def finish(self, index, elapsed, confirmed):
        if not confirmed:
            self.uncertain = True
        else:
            self.spent += min(max(elapsed, 0.), self.active.pop(index))
        self.snapshot('finish' if confirmed else 'uncertain', index)

    def snapshot(self, event, index):
        self.history.append({'event': event, 'index': index, 'completed_upper_bound_vm_seconds': self.spent,
                             'active_lifetime_reservations': dict(self.active),
                             'reserved_total_seconds': self.spent + sum(self.active.values())})


def run(out, source, baseline):
    import fcntl
    import modal
    if os.environ.get('MODAL_PROFILE') != 'phamvanvuhoan' or modal.__version__ != '1.6.2':
        raise ValueError('Requires authorized profile and qualified SDK')
    if (out/'receipt.json').exists():
        raise ValueError('No automatic paid retry')
    selection = validate_inputs(out)
    validate_inputs(baseline)
    raw = (out/'usable-shards-catalog.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != CATALOG_SHA:
        raise ValueError('Qualified catalog required')
    plan = make_plan(json.loads(raw), selection, json.loads((out/'image-plan.json').read_text()))
    if {r['instance_id'] for r in json.loads((out/'pilot.json').read_text())} != {
            r['instance_id'] for r in selection['selected']}:
        raise ValueError('Selection detached from frozen input')
    plan.update(mode='optimizeN11max', max_simultaneous_vms=MAX_ACTIVE,
                overall_deadline_seconds=None, cumulative_allocation_seconds=TOTAL_SECONDS,
                lifetime_per_vm_seconds=LIFETIME, allocation_bound_usd=RATE*TOTAL_SECONDS)
    dump(out/'plan.json', plan)
    here = Path(__file__).resolve().parent
    expected = json.loads((baseline/'source-provenance.json').read_text())['vm_sources_read_from_running_sandbox_sha256']
    if {n: hashlib.sha256((source/n).read_bytes()).hexdigest() for n in expected} != expected:
        raise ValueError('Evaluator source differs from baseline')
    helpers = ['sharded_pilot_worker.py', 'snapshot_catalog_worker.py']
    helper_hashes = {n: hashlib.sha256((here/n).read_bytes()).hexdigest() for n in helpers}
    hashes = json.loads((out/'snapshot-plan-hashes.json').read_text())
    lock = (Path(os.environ['SWE_WEB_STATE'])/'image-cache.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    guard(source, out.name, ESTIMATE)
    guard(source, out.name, ESTIMATE, 'planned')
    budget = Reservations()
    record = dict(started_at=datetime.now(timezone.utc).isoformat(), profile='phamvanvuhoan',
                  plan=plan, source_sha256=expected, helper_sha256=helper_hashes,
                  source_context=json.loads((out/'source-context.json').read_text()),
                  image_cache_volume_attached=False, registry_secrets_injected=False,
                  full_500_evaluated=False, groups=[], terminated_vm_ids=[])
    mutex = threading.RLock()
    started = time.monotonic()
    all_reports = {}
    def persist():
        with mutex:
            record['reservation_history'] = budget.history
            # Readers never see a partially written receipt.
            dump(out/'receipt.tmp.json', record)
            os.replace(out/'receipt.tmp.json', out/'receipt.json')

    app = modal.App('swebench-optimizeN11max-cap2-24')
    def one(group):
        case = out/f"shard-{group['index']:02d}"
        case.mkdir()
        dump(case/'assignment.json', dict(group, snapshot_plan_sha256=hashes[str(group['index'])]))
        row = dict(group, lifetime_seconds=LIFETIME, started_at=datetime.now(timezone.utc).isoformat(),
                   start_offset_seconds=time.monotonic()-started)
        with mutex:
            record['groups'].append(row)
            record['groups'].sort(key=lambda r: r['index'])
            persist()
        active = None
        t = time.monotonic()
        try:
            active = modal.Sandbox.create('dockerd', '--host=unix:///var/run/docker.sock', '--storage-driver=overlay2',
                app=app, image=modal.Image.from_id(group['snapshot_image_id']), runtime='vm',
                cpu=(16,16), memory=(65536,65536), timeout=LIFETIME,
                readiness_probe=modal.Probe.with_exec('docker','info',interval_ms=1000))
            with mutex:
                row['vm_id'] = active.object_id
                persist()
            active.wait_until_ready(timeout=90)
            row['docker_ready_seconds'] = time.monotonic()-t
            phase = time.monotonic()
            for name in expected:
                active.filesystem.copy_from_local(str(source/name), '/work/'+name)
            for name in helpers:
                active.filesystem.copy_from_local(str(here/name), '/work/'+name)
            for name in ['pilot.json','preds.jsonl']:
                active.filesystem.copy_from_local(str(out/name), '/work/'+name)
            active.filesystem.copy_from_local(str(case/'assignment.json'), '/work/assignment.json')
            names = list(expected)+helpers
            actual = json.loads(execute(active, 'python', '-c',
                'import hashlib,json,pathlib; names='+repr(names)+'; '
                'print(json.dumps({n:hashlib.sha256((pathlib.Path("/work")/n).read_bytes()).hexdigest() for n in names}))'))
            if actual != dict(expected, **helper_hashes):
                raise ValueError('Source readback differs')
            dump(case/'source-readback.json', actual)
            row['source_transfer_and_readback_seconds'] = time.monotonic()-phase
            worker_budget = min(650, LIFETIME-math.ceil(time.monotonic()-t)-35)
            if worker_budget < 610:
                raise RuntimeError('Startup consumed timeout reserve; no shortened test protocol')
            phase = time.monotonic()
            stream(active, ['python','-u','/work/sharded_pilot_worker.py'], case/'console.log', worker_budget)
            row['worker_seconds'] = time.monotonic()-phase
            row['worker_passed'] = True
        except Exception as exc:
            row['error'] = type(exc).__name__+': '+str(exc)
        finally:
            if active is not None:
                phase = time.monotonic()
                try:
                    execute(active,'tar','-czf','/tmp/sharded-score-output.tar.gz','-C','/work','output',timeout=30)
                    active.filesystem.copy_to_local('/tmp/sharded-score-output.tar.gz',str(case/'artifacts.tar.gz'))
                    with tarfile.open(case/'artifacts.tar.gz') as archive:
                        archive.extractall(case,filter='data')
                except Exception as exc:
                    row['artifact_error'] = type(exc).__name__+': '+str(exc)
                finally:
                    row['artifact_capture_seconds'] = time.monotonic()-phase
                    phase = time.monotonic()
                    try:
                        active.terminate(wait=True)
                        row['terminated'] = True
                        with mutex:
                            record['terminated_vm_ids'].append(active.object_id)
                    except Exception as exc:
                        row['termination_error'] = type(exc).__name__+': '+str(exc)
                    row['termination_seconds'] = time.monotonic()-phase
            row['wall_seconds'] = time.monotonic()-t
            row['end_offset_seconds'] = time.monotonic()-started
            with mutex:
                persist()
        return row

    try:
        with modal.enable_output(), app.run():
            record['app_id'] = app.app_id
            guard(source, out.name, ESTIMATE, 'running', app.app_id)
            persist()
            pending = iter(plan['groups'])
            next_group = next(pending, None)
            failed = False
            with ThreadPoolExecutor(max_workers=MAX_ACTIVE) as pool:
                running = {}
                while next_group is not None or running:
                    while not failed and next_group is not None and budget.admit(next_group['index']):
                        group = next_group
                        running[pool.submit(one, group)] = group
                        persist()
                        next_group = next(pending, None)
                    if not running:
                        if next_group is not None:
                            raise RuntimeError('Remaining lifetime reservation cannot fit next VM; no timeout or resource reduction')
                        break
                    done, _ = wait(running, return_when=FIRST_COMPLETED)
                    for future in done:
                        group = running.pop(future)
                        try:
                            row = future.result()
                        except Exception:
                            budget.finish(group['index'], 0, False)
                            raise
                        with mutex:
                            budget.finish(group['index'], row['wall_seconds'], row.get('terminated') is True)
                            persist()
                        if not row.get('worker_passed') or row.get('artifact_error') or not row.get('terminated'):
                            failed = True
                            continue
                        found = json.loads((out/f"shard-{group['index']:02d}/output/reports.json").read_text())
                        if set(found) != set(group['instance_ids']) or set(found) & all_reports.keys():
                            raise ValueError('Wrong or duplicated report coverage')
                        all_reports.update(found)
                        dump(out/'reports.json', all_reports)
                        print('N11MAX_PROGRESS='+json.dumps({'completed':len(all_reports), 'requested':24,
                              'upper_bound_vm_seconds':budget.spent, 'active':len(running)}),flush=True)
                    if failed and not running:
                        raise RuntimeError('A VM failed; no new allocations or paid retries')
            before = json.loads((out/'baseline-reports.json').read_text())
            comparison = compare([r['instance_id'] for r in selection['selected']], before, all_reports)
            record['comparison'] = comparison
            record['complete'] = len(all_reports)==24 and all(g.get('worker_passed') for g in record['groups'])
            record['report_parity'] = comparison['complete_report_matches']==24 and not comparison['different_report_ids']
    except Exception as exc:
        record.update(complete=False, error=type(exc).__name__+': '+str(exc))
    finally:
        record.update(finished_at=datetime.now(timezone.utc).isoformat(), wall_seconds=time.monotonic()-started,
                      all_vms_terminated=bool(record['groups']) and all(g.get('terminated') for g in record['groups']),
                      completed_upper_bound_vm_seconds=budget.spent)
        persist()
        try:
            guard(source,out.name,ESTIMATE,'completed' if record.get('complete') else 'failed')
        finally:
            lock.close()
    return 0 if record.get('complete') and record.get('report_parity') else 1

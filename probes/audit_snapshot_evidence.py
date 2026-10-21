"""Read-only, stdlib audit of saved evidence; never submits cloud compute.

Do not import the builders or their validators: rederive coverage, provenance,
report equality and costs directly from retained bytes. SHA checks establish
internal consistency, not an independent provider attestation.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, "Duplicate JSON key: " + key)
        value[key] = item
    return value


def decode(data):
    return json.loads(data, object_pairs_hook=unique_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def image_map(rows):
    result = {}
    for row in rows:
        ref = row['reference']
        require(ref not in result, 'Duplicate image reference')
        require(re.fullmatch(r'sha256:[0-9a-f]{64}', row['id']), 'Invalid image ID')
        result[ref] = row['id']
    return result


def validate_shard_binding(group, shard, selected, manifest, restored, selected_bytes):
    """Tie each restored receipt to its own group, not just the global union."""
    expected = image_map(group['images'])
    require(group['index'] == shard['index'] == selected['shard_index'], 'Shard index mismatch')
    require(group['image_count'] == shard['image_count'] == selected['image_count']
            == manifest['completed'] == manifest['requested'] == restored['images_verified']
            == len(expected), 'Shard count mismatch')
    for rows in [shard['images'], selected['images'], manifest['images']]:
        require(image_map(rows) == expected, 'Wrong per-shard coverage')
    require(selected['images'] == group['images'], 'Full layer/config plan differs')
    require(manifest['plan_sha256'] == restored['plan_sha256'] == sha(selected_bytes),
            'Shard plan/manifest/restore digest mismatch')
    require(shard['verified'] is True and shard['all_vms_terminated'] is True
            and restored['passed'] is True and restored['no_remaining_containers'] is True,
            'Shard unverified or not terminated')
    require(manifest['no_test_containers'] is True and manifest['tests_run'] is False
            and manifest['predictions_present'] is False and manifest['registry_download_bytes'] == 0
            and restored['tests_run'] is False and restored['volume_attached'] is False
            and restored['registry_secrets_injected'] is False, 'Unexpected test/data/credential state')
    require(shard['snapshot_ttl_seconds'] == 7776000, 'Unexpected retention')
    require(shard['snapshot_image_id'].startswith('im-'), 'Missing snapshot ID')
    require(shard['builder_vm'] != shard['verify_vm'], 'Restore reused builder VM')
    require(1 <= group['image_count'] <= 100 and group['layer_tar_bytes'] <= 35 * 1024**3,
            'Shard resource plan exceeds declared bounds')


def billing_cost(rows, app_id=None):
    selected = [r for r in rows if app_id is None or r['object_id'] == app_id]
    require(bool(selected), 'Missing billing rows')
    costs = [Decimal(str(row['cost'])) for row in selected]
    require(all(c.is_finite() and c >= 0 for c in costs), 'Invalid billing amount')
    return sum(costs, Decimal(0))


def audit(root, source_ref):
    evidence = root / 'evidence'
    full = evidence / '2026-10-22-verified-snapshots-90d'
    pilot = evidence / '2026-10-22-vm-snapshot'
    baseline = evidence / '2026-10-22-cost-pilot'
    read = lambda path: decode(path.read_bytes())
    checksums = 0
    for folder in [full, pilot, baseline]:
        for line in (folder / 'SHA256SUMS.txt').read_text().splitlines():
            expected, name = line.split(maxsplit=1)
            name = name.lstrip('*')
            target = (folder / name).resolve()
            require(target.is_relative_to(folder.resolve()), 'Checksum path escapes evidence')
            require(sha(target.read_bytes()) == expected, 'Evidence checksum mismatch: ' + name)
            checksums += 1
    original_bytes = gzip.decompress((full / 'catalog-plan.json.gz').read_bytes())
    original = decode(original_bytes)
    plan_bytes = gzip.decompress((full / 'shard-plan.json.gz').read_bytes())
    plan = decode(plan_bytes)
    receipt = read(full / 'shard-receipt.json')
    catalog = read(full / 'usable-shards-catalog.json')
    summary = read(full / 'summary.json')
    require(sha(original_bytes) == receipt['original_catalog_plan_sha256']
            == '2a3831d16e29024f85e732f53200ea863917d68acaf207bcdbdb859c02a59dc3', 'Frozen plan changed')
    require(sha(plan_bytes) == receipt['plan_sha256'], 'Global shard plan changed')
    require(catalog['complete'] is True and catalog['runtime'] == 'vm'
            and receipt['passed'] is True and receipt['all_vms_terminated'] is True, 'Incomplete catalog')
    require(plan['image_count'] == catalog['image_count'] == 500 and
            plan['shard_count'] == len(plan['groups']) == len(receipt['shards']) == 11, 'Wrong catalog size')
    require(catalog['shards'] == receipt['shards'], 'Published catalog differs from receipts')
    expected = image_map(original['images'])
    require(len(expected) == 500, 'Frozen plan is not 500 unique images')
    expected_ids = sorted(ref.removeprefix('swebench/sweb.eval.x86_64.').removesuffix(':latest')
                          .replace('_1776_', '__') for ref in expected)
    require(expected_ids == sorted(read(full / 'dataset-coverage.json')['ids']), 'Pinned dataset IDs differ')
    require(original['dataset'] == plan['dataset'] == catalog['dataset'], 'Dataset pin mismatch')
    require(original['dataset']['revision'] == '78f471bf655a3137b2e8a75af1501690ec009ec3', 'Wrong revision')
    found, vm_ids, image_ids, inventory = {}, [], [], []
    with tarfile.open(full / 'raw-shards-metadata.tar.gz') as archive:
        names = archive.getnames()
        require(len(names) == len(set(names)), 'Duplicate archive member')
        prefix = 'snapshot-shards-90d-20261022-01/'
        require(archive.extractfile(prefix + 'catalog-plan.json').read() == original_bytes, 'Archived plan changed')
        require(archive.extractfile(prefix + 'shard-plan.json').read() == plan_bytes, 'Archived shard plan changed')
        for group, shard in zip(plan['groups'], receipt['shards']):
            name = prefix + f"shard-{group['index']:02d}/"
            selected_bytes = archive.extractfile(name + 'catalog-plan.json').read()
            selected = decode(selected_bytes)
            manifest = decode(archive.extractfile(name + 'manifest.json').read())
            restored = decode(archive.extractfile(name + 'verification.json').read())
            validate_shard_binding(group, shard, selected, manifest, restored, selected_bytes)
            require(selected['catalog_plan_sha256'] == sha(plan_bytes), 'Shard detached from global plan')
            for ref, image_id in image_map(shard['images']).items():
                require(ref not in found, 'Coverage overlap across shards')
                found[ref] = image_id
            vm_ids.extend([shard['builder_vm'], shard['verify_vm']])
            image_ids.append(shard['snapshot_image_id'])
            inventory.append({'index': group['index'], 'count': len(shard['images']),
                'root_used_bytes': manifest['disk_used_bytes'], 'preload_seconds': manifest['elapsed_seconds'],
                'snapshot_seconds': shard['snapshot_seconds'], 'restore_seconds': shard['restore_docker_ready_seconds'],
                'startup_checks': restored['representative_containers_passed'],
                'snapshot_image_id': shard['snapshot_image_id'], 'use_before': shard['use_before']})
    require(found == expected, 'Incomplete or changed catalog union')
    require(len(set(vm_ids)) == 22 and set(vm_ids) == set(receipt['terminated_sandbox_ids']), 'VM cleanup coverage differs')
    require(len(set(image_ids)) == 11, 'Snapshot ID reused between groups')
    require(sum(r['root_used_bytes'] for r in inventory) == summary['sum_root_filesystem_used_bytes'], 'Storage summary differs')
    require(sum(r['startup_checks'] for r in inventory) == 22, 'Startup count differs')
    earliest = min(datetime.fromisoformat(r['use_before']) for r in inventory)
    require(earliest.isoformat() == summary['earliest_use_before_utc'], 'Retention summary differs')
    require(all(not app['remaining_sandboxes'] for app in
                read(full / 'cleanup-all-apps.json')['apps'].values()), 'Historical VM remains')
    context = read(full / 'source-context.json')
    require(sha(gzip.decompress((full / 'executed-source-context.patch.gz').read_bytes()))
            == context['staged_diff_sha256'], 'Executed context diff changed')
    for name, expected_sha in context['file_sha256'].items():
        spec = ':' + name if source_ref == 'INDEX' else source_ref + ':' + name
        actual = subprocess.check_output(['git', 'show', spec], cwd=root)
        require(sha(actual) == expected_sha, 'Executed source differs from Git: ' + name)
    readback = read(full / 'source-readback.json')
    require(readback['actual_sha256'] == readback['expected_sha256'], 'VM source readback differs')
    for name, value in readback['actual_sha256'].items():
        key = 'probes/' + name if name == 'snapshot_catalog_worker.py' else name
        require(context['file_sha256'][key] == value, 'VM source detached from launch context')
    before, after = read(baseline / 'vm-reports.json'), read(pilot / 'snapshot-reports.json')
    selection = read(baseline / 'selection.json')
    ids = {r['instance_id'] for r in selection['selected']}
    require(len(ids) == 24 and set(before) == set(after) == ids and before == after, '24 report parity differs')
    audit_dir = evidence / '2026-10-22-scientific-audit'
    local_tests = read(audit_dir / 'offline-test-results.json')
    local_count = 0
    for run in local_tests['results']:
        match = re.search(r'Ran (\d+) tests? in', run['stderr'])
        require(run['exit_code'] == 0 and match is not None and '\nOK\n' in run['stderr'],
                'Offline unit tests failed or incomplete')
        local_count += int(match[1])
    require(local_count == local_tests['total_tests_passed'], 'Offline test total differs')
    for name, expected_sha in local_tests['source_sha256'].items():
        require(sha((root / name).read_bytes()) == expected_sha, 'Offline-tested source changed: ' + name)
    with tarfile.open(audit_dir / 'pilot-inputs.tar.gz') as archive:
        inputs = {name: archive.extractfile(name).read() for name in ['pilot.json', 'preds.jsonl']}
    for name, key in [('pilot.json', 'pilot_sha256'), ('preds.jsonl', 'predictions_sha256')]:
        require(sha(inputs[name]) == selection[key], 'Frozen pilot input checksum differs')
    data = decode(inputs['pilot.json'])
    predictions = [decode(line) for line in inputs['preds.jsonl'].splitlines()]
    by_id = {r['instance_id']: r for r in data}
    require(len(data) == len(by_id) == len(predictions) == 24 and set(by_id) == ids
            == {r['instance_id'] for r in predictions}, 'Frozen pilot input IDs differ')
    require(all(p['model_patch'] == by_id[p['instance_id']]['patch'] for p in predictions),
            'Pilot predictions are not the exact gold patches')
    input_receipt = read(audit_dir / 'input-and-unit-verification.json')
    require(input_receipt['baseline_and_snapshot_input_bytes_equal'] is True
            and input_receipt['input_sha256'] == {n: sha(b) for n, b in inputs.items()},
            'Remote input readback differs')
    for name, expected_sha in input_receipt['source_sha256'].items():
        require(sha((root / name).read_bytes()) == expected_sha, 'Remote unit-test source differs: ' + name)
    statuses = Counter()
    for report in after.values():
        require(report['resolved'] is True and report['patch_successfully_applied'] is True, 'Gold report not resolved')
        for role in ['FAIL_TO_PASS', 'PASS_TO_PASS']:
            require(not report['tests_status'][role]['failure'], 'Required tests failed')
            statuses[role] += len(report['tests_status'][role]['success'])
    for iid, row in by_id.items():
        for role in ['FAIL_TO_PASS', 'PASS_TO_PASS']:
            gold = decode(row[role]) if isinstance(row[role], str) else row[role]
            require(sorted(gold) == sorted(after[iid]['tests_status'][role]['success']),
                    'Required test set omitted or changed: ' + iid)
    pilot_ids = image_map(read(pilot / 'image-plan.json'))
    require(len(pilot_ids) == 24 and all(expected.get(r) == i for r, i in pilot_ids.items()), '24 baseline IDs changed in 500 plan')
    before_sources = read(baseline / 'source-provenance.json')['vm_sources_read_from_running_sandbox_sha256']
    require(before_sources == read(pilot / 'score-source-provenance.json'), 'Evaluator source changed across pilot')
    billing = read(pilot / 'billing-final.json')
    costs = {role: billing_cost(billing['rows'], app) for role, app in billing['app_ids'].items()}
    require(all(str(cost) == billing['cost_by_role_usd'][role] for role, cost in costs.items()), 'Pilot billing summary differs')
    recurring = costs['snapshot_score']; base_cost = costs['baseline_vm']
    setup = costs['canary'] + costs['snapshot_prepare']
    pilot_comparison = read(pilot / 'comparison.json')
    require(str(recurring) == pilot_comparison['costs']['snapshot_recurring_usd'] and
            str(setup + recurring) == pilot_comparison['costs']['first_use_total_usd'], 'Pilot cost result differs')
    last, previous = read(full / 'billing-shards-third.json'), read(full / 'billing-shards-second.json')
    sharded = billing_cost(last['rows'], receipt['app_id'])
    require(sharded == billing_cost(previous['rows'], receipt['app_id']), 'Final shard billing reads differ')
    failed = billing_cost(read(full / 'billing-failed-settled.json')['rows'])
    retention = billing_cost(read(full / 'billing-retention-probe.json')['rows'])
    prior = billing_cost(read(full / 'billing-prior-gcp-setup.json')['rows'])
    require(sharded + failed + retention == Decimal('1.09802332'), '500 build cost arithmetic differs')
    require(prior + setup + recurring + sharded + failed + retention == Decimal('1.81734598'), 'Tracked preparation subtotal differs')
    live = read(audit_dir / 'live-readback.json')
    require(not any(live['live_vms_by_app'].values()) and live['new_compute_started'] is False,
            'Live cleanup audit failed')
    require(billing_cost(live['billing_rows'], receipt['app_id']) == sharded
            and billing_cost(live['billing_rows'], read(full / 'billing-failed-settled.json')['app_id']) == failed,
            'Later provider billing differs')
    base_wall = read(baseline / 'vm-receipt.json')['wall_seconds']
    score_wall = read(pilot / 'score-receipt.json')['wall_seconds']
    first_wall = score_wall + read(pilot / 'prepare-receipt.json')['wall_seconds'] + read(pilot / 'canary.json')['wall_seconds']
    native = read(baseline / 'native-reports.json')
    return {'passed': True, 'checked_at': datetime.now(timezone.utc).isoformat(),
        'source_ref_checked': source_ref, 'evidence_checksums_checked': checksums,
        'source_files_checked': len(context['file_sha256']), 'restored_shards_checked': len(inventory),
        'local_offline_tests_passed': local_count,
        'image_ref_id_pairs_checked': len(found), 'startup_checks_recorded': 22,
        'full_report_matches': len(ids), 'required_test_success_entries': dict(statuses),
        'frozen_gold_patch_input_pairs_checked': len(predictions),
        'all_required_test_sets_match_frozen_dataset_rows': True,
        'live_readback_checked_at': live['checked_at'],
        'gcp_host_unit_tests_passed': input_receipt['remote_host_unit_tests_passed'],
        'native_reports': len(native), 'native_resolved': sum(r['resolved'] is True for r in native.values()),
        'dataset_ids_basis': 'retained live pinned-dataset readback; no new dataset download',
        'source_readback_scope': '4 files on one restored shard VM; 10 launch-context Git blobs',
        'speedup_ratio': base_wall / score_wall, 'baseline_wall_seconds': base_wall,
        'snapshot_score_wall_seconds': score_wall, 'first_use_wall_seconds_including_canary': first_wall,
        'recurring_cost_saving_percent': str((base_cost - recurring) / base_cost * 100),
        'first_use_cost_saving_percent': str((base_cost - setup - recurring) / base_cost * 100),
        'costs_usd': {k: str(v) for k, v in dict(baseline=base_cost, recurring=recurring, pilot_setup=setup,
            sharded_build=sharded, failed_single=failed, ttl_probe=retention,
            build_500_including_failure=sharded+failed+retention,
            listed_preparation_subtotal=prior+setup+recurring+sharded+failed+retention).items()},
        'inventory': inventory, 'new_compute_started': False, 'full_500_evaluated': False,
        'new_shards_24_case_scoring_repeated': False, 'snapshot_storage_fee_verified': False,
        'scientific_scope': 'environment qualification and one 24-gold-control matched pilot'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-ref', default='e0ffeaea19e51c7b2e0f5d3ccc44493b6aface22',
                        help='Git checkpoint containing executed source, or INDEX')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    result = audit(Path(__file__).resolve().parents[1], args.source_ref)
    payload = json.dumps(result, indent=2) + '\n'
    if args.out:
        args.out.write_text(payload, encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'inventory'}, indent=2))

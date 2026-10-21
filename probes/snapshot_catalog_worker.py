"""Remote-only clean Docker-store preparation; never grades predictions."""
import argparse
import gzip
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import time

GIB = 1024 ** 3
TTL = 90 * 86400
PREFIX = "swebench/sweb.eval.x86_64."


def identity(reference):
    match = re.fullmatch(re.escape(PREFIX) + r"([a-z0-9_-]+)_1776_([a-z0-9_-]+):latest", reference)
    if not match:
        raise ValueError("Not an official pinned Verified image reference")
    return match[1] + "__" + match[2]


def validate_rows(rows, expected=500):
    if len(rows) != expected or len({r['reference'] for r in rows}) != expected:
        raise ValueError("Wrong image count or duplicate reference")
    if len({identity(r['reference']) for r in rows}) != expected:
        raise ValueError("Duplicate instance ID")
    for row in rows:
        if row.get('format') != 2 or not re.fullmatch(r'sha256:[0-9a-f]{64}', row['id']):
            raise ValueError("Expected content-addressed OCI cache manifest")
        if row['config']['digest'] != row['id']:
            raise ValueError("Config/image identity mismatch")
        if not row['layers']:
            raise ValueError("Image without layers")
        for blob in row['layers']:
            for key in ('digest', 'diff_id'):
                if not re.fullmatch(r'sha256:[0-9a-f]{64}', blob[key]):
                    raise ValueError("Invalid layer digest")
            if blob['uncompressed_size'] < 0 or blob['size'] < 0:
                raise ValueError("Negative layer size")


def layers_of(rows):
    layers = {}
    for row in rows:
        for blob in row['layers']:
            key = blob['diff_id']
            if key in layers and layers[key]['uncompressed_size'] != blob['uncompressed_size']:
                raise ValueError("Inconsistent shared layer size")
            layers.setdefault(key, blob)
    return layers


def batches_of(rows, max_images=25, max_bytes=24 * GIB):
    batches, batch = [], []
    for row in sorted(rows, key=lambda r: r['reference']):
        if sum(b['uncompressed_size'] for b in layers_of([row]).values()) > max_bytes:
            raise ValueError("Single image exceeds bounded archive size")
        candidate = batch + [row]
        size = sum(b['uncompressed_size'] for b in layers_of(candidate).values())
        if batch and (len(candidate) > max_images or size > max_bytes):
            batches.append(batch)
            batch = []
        batch.append(row)
    if batch:
        batches.append(batch)
    return batches


def write_archive(cache, rows, target):
    """A standard complete multi-image Docker-save tar, unique layers per batch.

    No thin archives or daemon-internal assumptions. Verify both compressed and
    expanded hashes using the same readers as RegistryCache.restore.
    """
    from image_cache import HashReader
    from registry_cache import add_bytes, digest_key
    manifest = []
    layers = layers_of(rows)
    with tarfile.open(fileobj=target, mode='w|') as archive:
        for row in rows:
            if cache.get(row['reference']) != row:
                raise ValueError("Cache manifest changed after planning")
            config = cache.blob_path(row['config']).read_bytes()
            if 'sha256:' + hashlib.sha256(config).hexdigest() != row['id']:
                raise ValueError("Corrupt OCI config")
            configuration = json.loads(config)
            if configuration['rootfs']['diff_ids'] != [b['diff_id'] for b in row['layers']]:
                raise ValueError("Config/layer identity mismatch")
            name = digest_key(row['id']) + '.json'
            add_bytes(archive, name, config)
            manifest.append({'Config': name, 'RepoTags': [row['reference']],
                             'Layers': [digest_key(b['diff_id']) + '/layer.tar' for b in row['layers']]})
        for blob in layers.values():
            member = tarfile.TarInfo(digest_key(blob['diff_id']) + '/layer.tar')
            member.size = blob['uncompressed_size']
            member.mode = 0o644
            with cache.blob_path(blob).open('rb') as raw:
                compressed = HashReader(raw)
                with gzip.GzipFile(fileobj=compressed, mode='rb') as source:
                    checked = HashReader(source)
                    archive.addfile(member, checked)
                    if checked.digest.hexdigest() != digest_key(blob['diff_id']) or source.read(1):
                        raise ValueError("Corrupt expanded OCI layer")
                if compressed.digest.hexdigest() != digest_key(blob['digest']):
                    raise ValueError("Corrupt compressed OCI layer")
        add_bytes(archive, 'manifest.json', json.dumps(manifest).encode())
    return {'unique_layers': len(layers), 'streamed_layer_bytes': sum(b['uncompressed_size'] for b in layers.values())}


def verify_images(rows):
    refs = [r['reference'] for r in rows]
    actual = json.loads(subprocess.check_output(['docker', 'image', 'inspect', *refs]))
    indexed = {r['Id']: r for r in actual}
    checked = []
    for row in rows:
        image = indexed.get(row['id'])
        if (image is None or row['reference'] not in image.get('RepoTags', [])
                or image.get('RootFS', {}).get('Layers') != [b['diff_id'] for b in row['layers']]):
            raise ValueError("Missing/changed restored image or layer chain")
        checked.append({'instance_id': identity(row['reference']), 'reference': row['reference'], 'id': image['Id']})
    if subprocess.check_output(['docker', 'ps', '-aq']).strip():
        raise ValueError("Expected clean Docker store without test containers")
    return checked


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + '\n')


def preload(plan):
    from registry_cache import RegistryCache
    rows = plan['images']
    validate_rows(rows)
    if importlib.metadata.version('swebench') != '4.1.0':
        raise ValueError("Unexpected harness")
    if Path('/work/preds.jsonl').exists() or subprocess.check_output(['docker', 'ps', '-aq']).strip():
        raise ValueError("Builder must start clean and without predictions")
    if json.loads(subprocess.check_output(['docker', 'info', '--format', '{{json .Driver}}'])) != 'overlay2':
        raise ValueError("Expected qualified overlay2 driver")
    cache = RegistryCache('/image-cache', 256)
    started = time.monotonic()
    progress = {'completed': 0, 'requested': 500, 'batches': [], 'tests_run': False,
                'predictions_present': False, 'registry_download_bytes': 0}
    for index, batch in enumerate(batches_of(rows)):
        expanded = sum(b['uncompressed_size'] for b in layers_of(batch).values())
        # docker load first untars the batch, then registers missing layers.
        if shutil.disk_usage('/').free < 2 * expanded + 24 * GIB:
            raise RuntimeError("Insufficient disk headroom for complete batch archive")
        t = time.monotonic()
        log = Path('/work') / f'load-{index:02d}.log'
        with log.open('wb') as output:
            proc = subprocess.Popen(['docker', 'load'], stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT)
            try:
                stats = write_archive(cache, batch, proc.stdin)
                proc.stdin.close()
                if proc.wait(timeout=900):
                    raise RuntimeError('docker load failed: ' + log.name)
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.wait()
        verify_images(batch)
        progress['completed'] += len(batch)
        progress['batches'].append(dict(stats, index=index, images=len(batch), seconds=time.monotonic()-t))
        progress.update(elapsed_seconds=time.monotonic()-started, disk_used_bytes=shutil.disk_usage('/').used)
        save('/work/catalog-progress.json', progress)
        print('SNAPSHOT_CATALOG_PROGRESS=' + json.dumps({k:v for k,v in progress.items() if k!='batches'}), flush=True)
    checked = verify_images(rows)
    progress.update(images=checked, no_test_containers=True, runtime=plan['runtime_image_id'],
                    plan_sha256=hashlib.sha256(Path('/work/catalog-plan.json').read_bytes()).hexdigest(),
                    docker_version=subprocess.check_output(['docker','version','--format','{{.Server.Version}}']).decode().strip())
    save('/snapshot-catalog-manifest.json', progress)


def verify(plan):
    rows = plan['images']
    validate_rows(rows)
    saved = json.loads(Path('/snapshot-catalog-manifest.json').read_text())
    digest = hashlib.sha256(Path('/work/catalog-plan.json').read_bytes()).hexdigest()
    if saved['plan_sha256'] != digest or saved['tests_run'] or saved['predictions_present']:
        raise ValueError("Invalid snapshot provenance")
    checked = verify_images(rows)
    # Exercise filesystem access in one image from each repo, without tests,
    # predictions, network, mounted Volume, or registry credentials.
    representatives = {}
    for row in rows:
        representatives.setdefault(identity(row['reference']).split('__')[0], row)
    for row in representatives.values():
        subprocess.run(['docker','run','--rm','--network=none','--entrypoint','/bin/true',row['reference']],
                       check=True, timeout=45)
    verify_images(rows)
    save('/catalog-verification.json', {'passed': True, 'images_verified': len(checked),
         'representative_containers_passed': len(representatives), 'tests_run': False,
         'plan_sha256': digest, 'no_remaining_containers': True,
         'volume_attached': False, 'registry_secrets_injected': False})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['preload', 'verify'])
    args = parser.parse_args()
    plan = json.loads(Path('/work/catalog-plan.json').read_text())
    (preload if args.action == 'preload' else verify)(plan)

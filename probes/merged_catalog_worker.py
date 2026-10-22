"""Extend a clean original N11 Docker store with missing pinned OCI images.

Never copy/merge Docker store directories. Verify the base, load only missing
images through the original archive loader, then verify the complete new store.
"""
import hashlib
import json
from pathlib import Path

from snapshot_catalog_worker import preload, save, validate_rows, verify_images


def partition_missing(full_rows, base_rows):
    full={r['reference']:r for r in full_rows}
    base={r['reference']:r for r in base_rows}
    if len(full)!=len(full_rows) or len(base)!=len(base_rows) or not base or not set(base)<set(full):
        raise ValueError('Base must be a nonempty strict subset of merged images')
    if any(full[ref]!=row for ref,row in base.items()):
        raise ValueError('Base image OCI identity changed')
    return [r for r in full_rows if r['reference'] not in base]


def main():
    path=Path('/work/catalog-plan.json')
    plan=json.loads(path.read_text())
    strategy=json.loads(Path('/work/build-strategy.json').read_text())
    validate_rows(plan['images'],expected=plan['image_count'])
    missing=partition_missing(plan['images'],strategy['base_images'])
    saved=json.loads(Path('/snapshot-catalog-manifest.json').read_text())
    if (saved['plan_sha256']!=strategy['base_snapshot_plan_sha256'] or saved['tests_run']
            or saved['predictions_present']):
        raise ValueError('Original snapshot provenance differs')
    verify_images(strategy['base_images'])
    preload(dict(plan,image_count=len(missing),images=missing))
    loaded=json.loads(Path('/snapshot-catalog-manifest.json').read_text())
    loaded.update(images=verify_images(plan['images']),completed=plan['image_count'],requested=plan['image_count'],
                  plan_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                  base_reuse={'snapshot_image_id':strategy['base_snapshot_image_id'],
                              'original_plan_sha256':strategy['base_snapshot_plan_sha256'],
                              'existing_images_verified':len(strategy['base_images']),
                              'missing_images_loaded':len(missing)})
    save('/snapshot-catalog-manifest.json',loaded)


if __name__=='__main__':main()

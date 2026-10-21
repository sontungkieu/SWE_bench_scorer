# Verified 500-image filesystem snapshot, 90-day retention

This prepares a clean Docker image store for all 500 already-cached official
Verified images on `phamvanvuhoan`. It does not grade 500 patches or switch the
production web/CLI backend. Filesystem snapshots accept a configurable TTL;
this job explicitly passes `90 * 86400` seconds. See
[Modal snapshot retention](https://modal.com/docs/guide/sandbox-snapshots).

The existing immutable runtime `im-xE1XGZwwpKZrzcYW0wqjEI`, SWE-bench 4.1.0 and
Modal SDK 1.6.2 are reused. No dependency/image rebuild or registry pull occurs.
No cloud or registry credentials enter the VMs.

The plan freezes all 500 OCI manifests from Volume v2
`swebench-verified-images-v1` and records the original dataset revision and
dataset checksum. Metadata reads occur on the GCP coordinator. All image data
remains remote. Every config and each streamed layer is hash checked, then the
Docker image IDs, tags and full diff-ID chains are verified. Standard complete
multi-image Docker-save archives deduplicate shared layers within each batch;
there are no thin archives or changes to grading code.

Batches have at most 25 images and 24 GiB of unique expanded layer tar data.
The initial plan has 27 batches, approximately 396.07 GiB of streamed layer
data and 284.49 GiB of unique layers across all images. These are tar byte
counts, not physical disk consumption or billable snapshot storage sizes.
The VM root filesystem is fixed at 512 GiB. Preparation checks free space
before every batch, reserving twice its expanded size plus 24 GiB headroom.
The worker aborts on cache mutation, missing data, checksum error or low disk.

After loading, Docker is stopped cleanly and the filesystem synced. A fresh VM
starts from the resulting snapshot without any Volume or registry secret.
It verifies every image ID/tag/layer chain and executes `/bin/true` with network
disabled in one representative image per repository. These are startup checks,
not SWE-bench grading. No test container or prediction enters the saved snapshot.

Preparation is bounded to 2 physical cores / 8 GiB / 7200 seconds; verification
to 2 cores / 4 GiB / 600 seconds. At the verified Sandbox rates, their combined
CPU/RAM allocation bound is $1.015152. The fail-closed billing guard reserves
$1.10 including an egress allowance, within the previous $2 preload limit even
including the earlier $0.42199984 cache/mock task cost. This is not a provider
hard cap, storage quote, or invoice. No automatic paid retries occur. Both VMs
are terminated after completion or failure; only the snapshot remains.

Run using the existing GCP runtime and scoped service environment:

```bash
python probes/snapshot_catalog.py plan --out RUN \
  --manifests RUN/cache-manifests.json --dataset-manifest DATASET_MANIFEST
python probes/snapshot_catalog.py build --out RUN --source EXISTING_RELEASE
```

Preserve the plan, manifest, receipts, helper/source hashes, restore
verification and settled per-app billing. The receipt stores the Image ID and a
conservative `use_before` deadline. Retention is measured from snapshot creation,
not extended by reading it. Storage pricing for snapshots has not been confirmed;
do not apply the Volume rate to Images without provider evidence.

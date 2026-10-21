# Verified 500-image filesystem snapshot, 90-day retention

**Live result:** the first single-VM attempt loaded/verified 500 images but the
provider rejected filesystem snapshot creation with `InternalError`, reference
`J5RJJ4HP`. All VMs were terminated. No usable 500-image snapshot was produced
by that attempt. The provider has not confirmed a size/inode limit or root cause.
A separate minimal VM successfully created a 90-day snapshot, so a 90-day TTL
alone is supported. The single-image recipe below is retained as experiment
provenance, not a qualified production workflow.

Recovery uses `snapshot_shards.py`: 11 disjoint groups, at most 100 images and
35 GiB of expanded unique layers per group. Each snapshot is restored and
verified before advancing; completed groups survive a later failure. The same
frozen 500-image plan is required by SHA-256. There are no automatic paid retries.
One VM at a time uses 2 cores / 4 GiB, with a 5700-second overall coordinator
deadline and finite per-VM lifetimes. Estimated compute/egress is $0.65; the
CPU/RAM envelope is $0.601464. Including the original cache/mock task, 24-case
snapshot experiment, failed single-store attempt and retention probe bound,
the planned cumulative amount remains below the original $2 preload allowance.
This is not a provider hard cap or a snapshot-storage quote.

```bash
python probes/snapshot_shards.py build --out SHARD_RUN --source EXISTING_RELEASE
python probes/snapshot_shards.py collect --out SHARD_RUN --billing-name billing-first.json
```

`SHARD_RUN` must contain the original byte-identical `catalog-plan.json`, the
successful `retention-probe.json`, and an audited `source-context.json` (Git base,
staged diff SHA-256 and actual packaged file hashes if GPG signing is pending).
The final `usable-shards-catalog.json` requires all 500 unique identities and
successful restore/termination receipts. The validator also binds every image
to its own planned shard index/count and rejects reused snapshot IDs. This
prevents swapped per-shard metadata from passing a global-union-only check.
This does not run 500 grading tests.

The [scientific audit](../evidence/2026-10-22-scientific-audit/README.md) preserves
the executed source checkpoint separately from this later validator fix. Its
independent stdlib checker verifies saved plans, inputs, reports and billing
without launching any VM:

```bash
python probes/audit_snapshot_evidence.py --source-ref e0ffeaea19e51c7b2e0f5d3ccc44493b6aface22
```

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

After successful restoration and termination, collect read-only evidence:

```bash
python probes/summarize_snapshot_catalog.py --out RUN --billing-name billing-first.json
# Wait for provider billing to catch up, then read again.
python probes/summarize_snapshot_catalog.py --out RUN --billing-name billing-second.json \
  --previous-billing RUN/billing-first.json
```

The collector refuses incomplete coverage/provenance or a remaining live VM.
`usable-catalog.json` contains the snapshot Image ID, conservative expiry and
all 500 instance-to-Docker-image identities for a future scoring integration.
It does not change the production web/CLI backend.

# Frozen 24 gold controls on the new 11-snapshot catalog

[Qualified live result](../evidence/2026-10-22-sharded-snapshot-pilot/README.md):
24/24 complete official report matches, $0.50332977 compute/egress before
credits, 515.2492166236043 s wall, all eight VMs terminated. This is 49.14%
cheaper than the original OCI baseline but more expensive than the dedicated
24-image snapshot pilot. Full-500 performance remains unmeasured.

This evaluates exactly the original 24 instances/prediction bytes. It restores
the eight snapshots that contain them (indices 0, 2, 4, 6, 7, 8, 9, 10); the
other three have no selected case. No new snapshot is built, no full-500 grading
is allowed, and the production backend is unchanged.

Each restored VM has the original 16 physical cores / 64 GiB, workers=8 and
test timeout=600s. VMs run sequentially with request=limit, no Volume, no
registry credential and no image-loading fallback. Groups now have 4, 2, 1, 6,
3, 4, 3 and 1 selected cases. This grouping differs from the original 3x8 pilot;
cost/wall comparisons characterize shard operation, not identical scheduling.

The coordinator enforces the published catalog SHA-256, exact frozen input
hashes and original evaluator hashes. Each VM checks its saved build-plan hash,
all restored image ID/tag/layer metadata, clean state and selected baseline
image identities. All six injected source/helper files are read back and
checked on each VM before grading. Report collection rejects missing, extra
or duplicate case coverage and compares full parsed official reports.

A 1800-second overall coordinator envelope at 16 cores/64 GiB has a CPU/RAM
allocation bound of $1.90368 under normal sequential termination. The guard
estimate is $2 including margin. Per-VM lifetime is at most 780 seconds; a VM
is not launched without enough remaining envelope to retain the original
600-second test timeout. This is not an invoice/provider hard cap or storage
quote. No automatic paid retries occur. Every allocated VM is terminated in
the cleanup path, including artifact/worker failure.

Use the existing GCP venv and scoped `phamvanvuhoan` service environment. The
run directory is prepared with `pilot.json`, `preds.jsonl`, `selection.json`,
`image-plan.json`, `baseline-reports.json`, `usable-shards-catalog.json`,
`snapshot-plan-hashes.json` (from retained shard build manifests), and a source
context containing the clean Git checkpoint and packaged file hashes.

```bash
python probes/sharded_pilot.py run --out RUN --source ORIGINAL_RELEASE --baseline ORIGINAL_PILOT
python probes/sharded_pilot.py collect --out RUN --billing-name billing-first.json
# After provider usage settles, collect again; preserve both reads.
python probes/sharded_pilot.py collect --out RUN --billing-name billing-second.json
```

Keep complete reports, per-group raw logs/receipts/source readbacks, image
sources, cleanup confirmation and app-scoped resource billing. Report setup
cost separately from this scoring run; storage and GCP/shared costs are not
included in the app sum. A result on gold controls is not a model score.

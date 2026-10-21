# VM filesystem snapshot: matched 24-case pilot

This experiment keeps the previous frozen 24 gold patches, batch order, official
SWE-bench 4.1.0 evaluator, image IDs, 16-core/64-GiB scoring allocation, eight
workers and 600-second test timeout. It does not run the full 500-instance set
or deploy a new production backend.

The tested change is starting the scoring VM from a clean filesystem snapshot
whose Docker image store already contains all 24 benchmark images. No cache
Volume or registry credential is attached to the scoring VM. Missing or changed
images fail before grading; they are never pulled or restored as a fallback.

Preparation runs on a separate 2-core/8-GiB VM, loading only the 24 baseline
images from the existing pinned Volume cache. It creates no test containers and
receives no predictions. Docker is shut down cleanly and the filesystem synced
before capturing the snapshot. The snapshot expires after 30 days. A small
two-VM sentinel canary validates filesystem snapshots before loading the images.

Compute/egress is reported separately for the canary, one-time preparation and
restored scoring run. All VMs have finite lifetimes, with resource allocation
bounds below the $3 experiment planning estimate; each phase also passes the
existing fail-closed billing guard on `phamvanvuhoan`. Estimates are not provider
hard caps or final invoices. There are no automatic paid retries.

Use the existing GCP runtime and scoped service environment. The previous pilot
directory must contain its frozen inputs, source provenance and raw VM outputs.

```bash
python snapshot_pilot.py canary --out snapshot-canary-UNIQUE --source SOURCE
python snapshot_pilot.py prepare --out snapshot-pilot-UNIQUE --source SOURCE \
  --baseline PREVIOUS_PILOT --canary snapshot-canary-UNIQUE/canary.json
python snapshot_pilot.py score --out snapshot-pilot-UNIQUE --source SOURCE \
  --baseline PREVIOUS_PILOT
```

Copy `snapshot_worker.py` alongside the coordinator. Preserve raw artifacts,
receipts, snapshot IDs/TTL, executed source hashes and provider billing. Compare
all 24 parsed official reports, not only aggregate resolved counts. Report
recurring savings and amortization separately from one-time setup costs.

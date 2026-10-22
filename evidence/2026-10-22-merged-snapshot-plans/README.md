# N7 merge plans alongside the unchanged N11 modes

Date: 2026-10-22. Status: **offline implementation and manifest validation only**.
No new Modal application, VM, snapshot build, billing query or evaluation was
started for this work. Neither N7 mode is qualified for scientific scoring yet.

## What is preserved

`native`, `optimizeN11` and `optimizeN11max` keep their original dispatch and
catalog checks. The original N11 assignments and snapshot IDs are not modified.
The official evaluator and tests are not edited. Planned evaluation requests
remain VM runtime, 16 cores, 64 GiB RAM, 8 workers and 600-second test timeout.
No full-500 evaluation command is added.

The manifest is pinned to original OCI source SHA-256
`2a3831d16e29024f85e732f53200ea863917d68acaf207bcdbdb859c02a59dc3`
and N11 catalog SHA-256
`6d6bb668130943a891fa0d9add90272398344d6588e3a1c6e0eb55c61cdadddb`.
Dataset revision is `78f471bf655a3137b2e8a75af1501690ec009ec3`.
Each saved plan embeds the original complete OCI rows, including config IDs,
compressed digests and expanded layer IDs/sizes. All 500 images occur exactly
once with unchanged identities.

## Requested grouping: optimizeN7merge

Source shard indices below are **zero based**; input order and counts are
`0:100, 1:100, 2:59, 3:8, 4:9, 5:9, 6:27, 7:21, 8:43, 9:100, 10:24`.

| New shard | Source shards | Images | Unique layer tar GiB | Frozen 24 controls | Snapshot action |
|---|---|---:|---:|---:|---|
| 0 | 0 | 100 | 29.909 | 4 | Reuse after live checks |
| 1 | 1 | 100 | 24.691 | 0 | Reuse after live checks |
| 2 | 2 | 59 | 33.684 | 2 | Reuse after live checks |
| 3 | 6 + 7 | 48 | 65.596 | 9 | New build required |
| 4 | 8 | 43 | 34.820 | 4 | Reuse after live checks |
| 5 | 9 | 100 | 32.997 | 3 | Reuse after live checks |
| 6 | 10 + 3 + 4 + 5 | 50 | 95.364 | 2 | New build required |

This is N7, not N6. It requires two new snapshots and can retain five originals
subject to runtime integrity/retention checks. Layer union total is
**317.060535431 GiB**, versus **327.588754654 GiB** for N11. Shared layers are
counted once per group; these are expanded tar bytes, not RAM, actual restored
disk usage, fetched bytes or billable storage. The internal proposed preparation
envelope is 96 GiB, not a provider limit or authorization to allocate resources.

One pilot group contains nine controls. Existing `sharded_pilot.make_plan`
deliberately rejects more than eight controls in a group. A future N7 runner
must support an eight-worker queue or an explicitly documented batching policy;
it must retain all nine tasks and keep resource/test limits unchanged. No
existing guard is weakened here.

## Whole-shard weighted grouping: optimizeN7weighted

| New shard | Source shards | Images | Unique layer tar GiB | Frozen 24 controls |
|---|---|---:|---:|---:|
| 0 | 0 | 100 | 29.909 | 4 |
| 1 | 1 | 100 | 24.691 | 0 |
| 2 | 2 + 3 | 67 | 62.082 | 2 |
| 3 | 4 + 6 | 36 | 63.954 | 7 |
| 4 | 5 + 7 | 30 | 61.854 | 3 |
| 5 | 8 + 10 | 67 | 39.826 | 5 |
| 6 | 9 | 100 | 32.997 | 3 |

Four new snapshots, three reuse candidates; every shard is at most 100 images
and 64 GiB layer tar. Total union bytes are **315.313698292 GiB**. This avoids
the requested grouping's 95.364 GiB peak, but requires two additional builds.
This is a candidate for qualification, not proof of faster startup or lower cost.

The search exhausts partitions of the original eleven **whole shards** into
seven groups under these caps. Its primary objective is the sum of modeled
eight-worker LPT queue times at fixed resource allocation, not equal finish
times. Ties favor fewer new builds, then lower total and peak layer size.
The search does not move individual images between old shards and does not
change real harness task dispatch to LPT. Its optimum applies only to this
restricted proxy problem.

## What the weights can and cannot say

Weights use logged service wall time for 24 historical gold controls. The other
476 tasks receive the median observed service time for their repo. This is a
weak prior: actual predicted patches can have different outcomes and runtime;
within-repo timing variation is not established by these few samples.
Weight provenance, archive/feature hashes and measured sample counts are saved
inside both plans.

For illustration only, at an assumed fixed 15-second startup per VM:

| Layout | Proxy summed test VM-seconds | Proxy with startup |
|---|---:|---:|
| Original N11 | 1857.0150 | 2022.0150 |
| Requested N7 merge | 1765.8235 | 1870.8235 |
| Weighted N7, cap 64 GiB | 1768.5630 | 1873.5630 |

The tiny proxy difference between the N7 candidates is not sufficient evidence
to prefer one on actual cost. Startup is a scenario assumption, not a measured
N7 value. These proxies omit cold-read I/O, resource contention, snapshot build
cost, actual allocation intervals, egress and snapshot storage. They are neither
billing predictions nor full-500 runtime measurements. Parallel scheduling
primarily changes wall time; cost still depends on the sum of VM lifetimes.

## Reproduce the plans and validation

Using an existing Python runtime with standard library only, from repo root:

```bash
python eval_modes.py list
python eval_modes.py plan --mode optimizeN7merge --out NEW_USER_PLAN
python eval_modes.py plan --mode optimizeN7weighted --out NEW_WEIGHTED_PLAN
python -m unittest discover -s probes -p 'test_*.py'
```

Each new output folder contains deterministic `merge-plan.json.gz`. Existing
folders are rejected to preserve evidence. Compressed plans checked into this
directory are `user/merge-plan.json.gz` and `weighted/merge-plan.json.gz`.
The regression tests compare them against recomputation and verify coverage,
identity, caps, layer deduplication, malformed assignments, invalid timing
weights, conflicting layer sizes, a brute-force independent small optimum,
and refusal to execute/collect/prepare an unqualified N7 mode.

Executed locally with the existing Windows Python 3.12 runtime:
`python -m unittest discover -s probes -p 'test_*.py'` — **88 tests passed**.
The plan CLI also passed an actual offline round trip, matched the saved
manifest and refused to overwrite its output. These are code/manifest checks,
not a Modal restore test or scientific evaluation result.

## Remaining steps before any scoring

1. Authorize a separate bounded build/qualification budget. No previous $2
   experiment cap is silently reused as a fresh budget.
2. Build only merged groups remotely from the pinned OCI cache. Docker store
   directories or snapshot IDs cannot simply be concatenated. No local Docker
   or Docker Hub redownload is required by the proposed build path.
3. Restore each new snapshot, verify every image ID/tag/layer chain and a clean
   container store, confirm termination, record billing and retention.
4. Produce a distinct immutable catalog plus per-snapshot manifest hashes;
   validate any retained original snapshot live. Preserve the N11 catalog pin.
5. Add a bounded 24-control runner for that qualified catalog, preserving
   source/image/patch hashes, resource requests, all official tests and timeouts.
6. Compare full official reports, including the existing timing-sensitive
   Matplotlib case, plus provider billing. Keep mismatches visible and retain
   unqualified status if parity fails. Do not proceed to all 500 tasks here.

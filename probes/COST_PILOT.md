# Matched 24-instance cost pilot

This is an infrastructure comparison authorized for `phamvanvuhoan`, not a
model benchmark or a full 500-instance run. All inputs are public gold patches
from Verified revision `78f471bf655a3137b2e8a75af1501690ec009ec3`.

Selection is frozen before execution: two SHA256-ranked IDs per repository,
seed `20261022`; Flask has only one instance, so the remaining slot comes from
Django. The resulting 24 instances cover all 12 repositories. Inputs and the
source dataset have SHA-256 manifests. No failed instance is replaced.

Native uses the audited SWE-bench 4.1.0 / Modal 1.6.2 compatibility overlay,
four Function containers at most, 4 physical CPU cores per Sandbox and a
1–8 GiB memory request/limit. Function lifetime is 1,200 seconds, Sandbox
lifetime 600 seconds and coordinator timeout 1,800 seconds. Sandbox creation
has one attempt. A separate diff records the pilot resource policy and timing
instrumentation; grading, parsers and test specifications remain unchanged.

VM uses the existing production Docker evaluator and existing 500-image
Volume cache. A single VM has 16 physical cores / 64 GiB, a finite 1,800-second
lifetime, and three batches of eight. Each batch reuses `remote_worker.evaluate`
then removes its Docker images, matching the production full scorer's batch
eviction policy. Missing cache entries fail closed; no registry credentials
are injected into the grading VM. Cache access shares the service's file lock.

The remote web billing guard reserves $3 for native and $2 for VM. These are
planning estimates, not provider hard caps. Per-app billing is collected after
termination; delayed provider metering is explicitly labelled. Existing cache
prewarm costs and storage are separate from current scoring compute.

Run from the existing isolated GCP coordinator, with its scoped service
environment loaded. Do not install dependencies on the laptop or mutate the
production runtime. Copy `cost_pilot.py`, `cost_pilot_vm.py`,
`modal_sdk_compat.py` and `summarize_cost_pilot.py` to the coordinator.

```bash
venv/bin/python cost_pilot.py prepare --out cost-pilot-NEW-ID \
  --dataset upstream-modal-compat-20261021-02/verified.json
venv/bin/python cost_pilot.py native --out cost-pilot-NEW-ID \
  --source "$HOME/apps/swebench-web/current"
venv/bin/python cost_pilot.py vm --out cost-pilot-NEW-ID \
  --source "$HOME/apps/swebench-web/current"
venv/bin/python summarize_cost_pilot.py cost-pilot-NEW-ID
```

Native and VM may run concurrently after both guards pass: native does not
write the Docker Volume. Save driver stdout/stderr and retain the frozen
selection, raw reports, app IDs, cleanup evidence and exact source hashes.

Interpret total cost with report coverage and correctness. Native may build
new environments from scripts while VM consumes previously cached images;
this is an operational comparison of the two available paths, not a proof
that one backend is intrinsically cheaper under identical cold caches. The
sample oversamples small repositories, so multiplying its mean cost by 500
does not produce an unbiased full-dataset estimate.

Lightweight local checks:

```powershell
python -B -m unittest -v probes.test_cost_pilot probes.test_upstream_modal_probe
```

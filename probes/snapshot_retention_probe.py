"""One finite, cheap VM to isolate 90-day TTL from large Docker-store size."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from snapshot_pilot import RUNTIME, dump, execute, guard


def main():
    import modal
    if os.environ.get('MODAL_PROFILE') != 'phamvanvuhoan' or modal.__version__ != '1.6.2':
        raise ValueError('Requires the scoped, qualified profile/runtime')
    out=Path(sys.argv[1]).resolve()
    source=Path(sys.argv[2]).resolve()
    if out.exists():raise ValueError('No automatic paid retry')
    out.mkdir(parents=True)
    label=out.name
    guard(source,label,.02)
    guard(source,label,.02,'planned')
    receipt={'started_at':datetime.now(timezone.utc).isoformat(),'cpu':2,'memory_gib':4,
             'vm_lifetime_seconds':120,'allocation_bound_usd':120*(2*.00003942+4*.00000667),
             'ttl_seconds':90*86400,'runtime_image_id':RUNTIME,
             'helper_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    sb=None
    app=modal.App('swebench-snapshot-90d-retention-probe')
    t=time.monotonic()
    try:
        with modal.enable_output(), app.run():
            try:
                receipt['app_id']=app.app_id
                guard(source,label,.02,'running',app.app_id)
                sb=modal.Sandbox.create('sleep','infinity',app=app,image=modal.Image.from_id(RUNTIME),
                      runtime='vm',cpu=(2,2),memory=(4096,4096),timeout=120)
                receipt['sandbox_id']=sb.object_id
                dump(out/'receipt.json',receipt)
                execute(sb,'sh','-c','printf retention-90d > /retention-probe.txt; sync')
                image=sb.snapshot_filesystem(timeout=75,ttl=90*86400)
                receipt.update(passed=True,snapshot_image_id=image.object_id)
            finally:
                if sb is not None:
                    sb.terminate(wait=True)
                    receipt['sandbox_terminated']=True
    except Exception as exc:
        receipt.update(passed=False,error=type(exc).__name__+': '+str(exc))
    finally:
        receipt.update(finished_at=datetime.now(timezone.utc).isoformat(),wall_seconds=time.monotonic()-t)
        dump(out/'receipt.json',receipt)
        guard(source,label,.02,'completed' if receipt.get('passed') else 'failed')
    return 0 if receipt.get('passed') else 1


if __name__=='__main__':
    raise SystemExit(main())

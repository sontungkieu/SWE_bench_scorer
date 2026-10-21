"""Read-only evidence/billing collection; never launches compute."""
import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def dump(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--billing-name',default='billing.json')
    parser.add_argument('--previous-billing',type=Path)
    args=parser.parse_args()
    root=args.out.resolve()
    receipt=json.loads((root/'catalog-receipt.json').read_text())
    plan=json.loads((root/'catalog-plan.json').read_text())
    if not receipt.get('passed') or not receipt.get('all_sandboxes_terminated'):
        raise ValueError('Preparation, restore verification and VM termination must finish first')
    verification=json.loads((root/'catalog-verification.json').read_text())
    manifest=json.loads((root/'catalog-manifest.json').read_text())
    coverage=json.loads((root/'dataset-coverage.json').read_text())
    readback=json.loads((root/'source-readback.json').read_text())
    sha=hashlib.sha256((root/'catalog-plan.json').read_bytes()).hexdigest()
    if (verification['images_verified'] != 500 or verification['plan_sha256'] != sha
            or manifest['plan_sha256'] != sha or not coverage['cache_instance_ids_match']
            or not readback['source_readback_matches'] or not readback['dataset_checksum_matches']):
        raise ValueError('Incomplete coverage, image verification or provenance')
    import modal
    running=[sb.object_id for sb in modal.Sandbox.list(app_id=receipt['app_id'])]
    dump(root/'cleanup.json',{'checked_at':datetime.now(timezone.utc).isoformat(),
                             'app_id':receipt['app_id'],'remaining_sandboxes':running})
    if running:
        raise ValueError('Unexpected remaining VM; inspect before publishing completion')
    now=datetime.now(timezone.utc)
    start=datetime.fromisoformat(receipt['started_at']).strftime('%Y-%m-%d')
    end=(now+timedelta(days=1)).strftime('%Y-%m-%d')
    raw=subprocess.check_output([sys.executable,'-m','modal','billing','report',
                  '--start',start,'--end',end,'--show-resources','--json'],text=True,timeout=90)
    rows=[r for r in json.loads(raw) if r.get('object_id')==receipt['app_id']]
    if not rows:
        raise ValueError('No provider billing rows yet; retry read-only collection later')
    cost=str(sum((Decimal(str(r['cost'])) for r in rows),Decimal(0)))
    billing={'checked_at':now.isoformat(),'app_id':receipt['app_id'],
             'compute_egress_usd':cost,'rows':rows,'before_credits':True,
             'not_final_invoice':True,'snapshot_storage_not_in_app_sum':True}
    dump(root/args.billing_name,billing)
    previous=json.loads(args.previous_billing.read_text()) if args.previous_billing else None
    stable=bool(previous and previous['app_id']==receipt['app_id']
                and previous['compute_egress_usd']==cost)
    summary={'passed':True,'image_count':500,'snapshot_image_id':receipt['snapshot_image_id'],
             'snapshot_ttl_days':receipt['snapshot_ttl_seconds']/86400,
             'use_before':receipt['use_before'],'prepare_seconds':manifest['elapsed_seconds'],
             'snapshot_seconds':receipt['snapshot_seconds'],'total_wall_seconds':receipt['wall_seconds'],
             'restore_docker_ready_seconds':receipt['restore_docker_ready_seconds'],
             'root_filesystem_used_bytes':manifest['disk_used_bytes'],
             'root_usage_is_not_billable_snapshot_size':True,
             'image_ids_and_layer_chains_verified':verification['images_verified'],
             'representative_container_startups_passed':verification['representative_containers_passed'],
             'compute_egress_usd':cost,'two_billing_reads_match':stable,
             'all_vms_terminated':True,'full_500_evaluated':False,
             'snapshot_storage_cost_confirmed':False,'plan_sha256':sha,
             'app_url':'https://modal.com/apps/phamvanvuhoan/main/'+receipt['app_id']}
    dump(root/'summary.json',summary)
    catalog={'schema':1,'profile':'phamvanvuhoan','runtime':'vm',
             'snapshot_image_id':receipt['snapshot_image_id'],'runtime_image_id':plan['runtime_image_id'],
             'use_before':receipt['use_before'],'snapshot_ttl_seconds':receipt['snapshot_ttl_seconds'],
             'dataset':plan['dataset'],'plan_sha256':sha,'restore_verified':True,
             'images':manifest['images']}
    dump(root/'usable-catalog.json',catalog)
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    main()

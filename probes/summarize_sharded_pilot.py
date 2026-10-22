"""Offline evidence verification for the completed 24-case sharded run."""
import argparse
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def summarize(out, evidence, billing_name, previous_name):
    read=lambda p: json.loads(p.read_text())
    receipt=read(out/'receipt.json'); plan=receipt['plan']
    selection=read(evidence/'2026-10-22-cost-pilot/selection.json')
    before=read(evidence/'2026-10-22-cost-pilot/vm-reports.json')
    old=read(evidence/'2026-10-22-vm-snapshot/snapshot-reports.json')
    expected={r['instance_id'] for r in selection['selected']}
    require(len(expected)==24 and set(before)==set(old)==expected, 'Baseline coverage differs')
    require(receipt.get('complete') is True and receipt.get('all_vms_terminated') is True,
            'Run incomplete or cleanup unconfirmed')
    require(plan['selected_count']==24 and plan['full_500_evaluated'] is False
            and len(plan['groups'])==len(receipt['groups'])==8, 'Wrong run scope')
    for name,key in [('pilot.json','pilot_sha256'),('preds.jsonl','predictions_sha256')]:
        require(hashlib.sha256((out/name).read_bytes()).hexdigest()==plan[key]==selection[key],
                'Frozen input bytes differ')
    baseline_sources=read(evidence/'2026-10-22-cost-pilot/source-provenance.json')['vm_sources_read_from_running_sandbox_sha256']
    require(receipt['source_sha256']==baseline_sources, 'Evaluator source differs from baseline')
    reports={}; images=[]; readbacks=[]; results=[]
    for group, row in zip(plan['groups'],receipt['groups']):
        require(group['index']==row['index'] and group['snapshot_image_id']==row['snapshot_image_id']
                and row.get('worker_passed') is True and row.get('terminated') is True,
                'Group not tied to completed VM')
        case=out/f"shard-{group['index']:02d}"
        actual=read(case/'source-readback.json')
        require(actual==dict(receipt['source_sha256'],**receipt['helper_sha256']), 'VM source differs')
        readbacks.append(actual)
        assignment=read(case/'assignment.json')
        require(assignment['instance_ids']==group['instance_ids'], 'Assignment changed')
        found=read(case/'output/reports.json')
        require(set(found)==set(group['instance_ids']) and not(set(found)&reports.keys()), 'Group report coverage differs')
        reports.update(found)
        stats=read(case/'output/summary.json')
        require(stats['complete'] is True and stats['harness_exit_code']==0, 'Harness did not complete successfully')
        batch=case/'output'/f"shard-{group['index']:02d}"
        recorded_images=read(batch/'images.json')
        require(len(recorded_images)==len(group['instance_ids']), 'Wrong image receipt count')
        by_id={r['instance_id']:r for r in group['images']}
        for image in recorded_images:
            require(image['source']=='local' and image['cache_added_bytes']==0
                    and image['reference']==by_id[image['instance_id']]['reference']
                    and image['id']==by_id[image['instance_id']]['id'], 'Image fallback or identity mismatch')
        images.extend(recorded_images)
        require(read(case/'output/environment.json')['selected_count']==len(group['instance_ids']), 'Worker scope differs')
        results.append({'index':group['index'],'count':len(group['instance_ids']),
                        'docker_ready_seconds':row['docker_ready_seconds'],
                        'wall_seconds':row['wall_seconds'],
                        'test_and_cleanup_seconds':stats['test_and_cleanup_seconds'],
                        'snapshot_image_id':row['snapshot_image_id'],'vm_id':row['vm_id']})
    require(set(reports)==expected and reports==read(out/'reports.json'), 'Combined reports differ')
    require(len(set(receipt['terminated_vm_ids']))==8 and
            set(receipt['terminated_vm_ids'])=={row['vm_id'] for row in receipt['groups']},
            'VM cleanup coverage differs')
    different=[iid for iid in sorted(expected) if reports[iid]!=before[iid]]
    different_old=[iid for iid in sorted(expected) if reports[iid]!=old[iid]]
    latest=read(out/billing_name); previous=read(out/previous_name)
    def cost(data):
        require(data['app_id']==receipt['app_id'] and data['remaining_vms']==[], 'Billing/cleanup app differs')
        require(bool(data['rows']) and all(r['object_id']==receipt['app_id'] for r in data['rows']), 'Missing/wrong billing rows')
        costs=[Decimal(str(r['cost'])) for r in data['rows']]
        require(all(c.is_finite() and c>=0 for c in costs), 'Invalid cost')
        total=sum(costs,Decimal(0))
        require(total==Decimal(data['compute_egress_usd']), 'Billing total differs')
        return total
    total=cost(latest); earlier=cost(previous)
    require(total>0, 'Positive-duration scoring billing is still missing')
    gap=(datetime.fromisoformat(latest['checked_at'])-datetime.fromisoformat(previous['checked_at'])).total_seconds()
    require(total==earlier and gap>=60, 'Billing has not stabilized across separated reads')
    baseline_cost=Decimal('0.98970092'); old_cost=Decimal('0.17555287')
    baseline_wall=read(evidence/'2026-10-22-cost-pilot/vm-receipt.json')['wall_seconds']
    old_wall=read(evidence/'2026-10-22-vm-snapshot/score-receipt.json')['wall_seconds']
    return {'qualified':not different and not different_old and all(r['resolved'] is True for r in reports.values()),
        'selected_count':24,'reports':len(reports),'resolved':sum(r['resolved'] is True for r in reports.values()),
        'complete_report_matches_baseline':24-len(different),'different_baseline_ids':different,
        'complete_report_matches_old_snapshot':24-len(different_old),'different_old_snapshot_ids':different_old,
        'used_snapshot_count':8,'unused_catalog_snapshot_count':3,'groups':results,
        'all_8_vms_terminated':True,'all_8_vm_source_readbacks_match':True,
        'image_sources':{'local':len(images)},'image_cache_added_bytes':sum(r['cache_added_bytes'] for r in images),
        'image_ensure_seconds':sum(r['seconds'] for r in images),'full_500_evaluated':False,
        'production_backend_changed':False,'gold_controls_not_model_score':True,
        'frozen_pilot_sha256':plan['pilot_sha256'],'frozen_predictions_sha256':plan['predictions_sha256'],
        'scoring_source_git_commit':receipt['source_context']['git_commit'],
        'cpu_per_vm':16,'memory_gib_per_vm':64,'workers':8,'test_timeout':600,'max_simultaneous_vms':1,
        'batching_changed_from_original_3x8':True,'sharded_wall_seconds':receipt['wall_seconds'],
        'baseline_wall_seconds':baseline_wall,'old_single_snapshot_wall_seconds':old_wall,
        'baseline_over_new_wall_ratio':baseline_wall/receipt['wall_seconds'],
        'new_over_old_single_snapshot_wall_ratio':receipt['wall_seconds']/old_wall,
        'cost_usd':str(total),'baseline_cost_usd':str(baseline_cost),'old_single_snapshot_cost_usd':str(old_cost),
        'saving_vs_oci_baseline_percent':str((baseline_cost-total)/baseline_cost*100),
        'increase_vs_old_single_snapshot_percent':str((total-old_cost)/old_cost*100),
        'before_credits':True,'not_final_invoice':True,'snapshot_storage_not_in_app_sum':True,
        'billing_read_gap_seconds':gap,'billing_stable_reads':[previous_name,billing_name],
        'snapshot_preparation_cost_is_separate':True,'no_new_snapshot_built':True}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--billing-name',default='billing-final.json')
    parser.add_argument('--previous-billing',default='billing-second.json')
    args=parser.parse_args()
    result=summarize(args.out,Path(__file__).resolve().parents[1]/'evidence',args.billing_name,args.previous_billing)
    (args.out/'comparison.json').write_bytes((json.dumps(result,indent=2)+'\n').encode())
    print(json.dumps({k:v for k,v in result.items() if k!='groups'},indent=2))

"""Verify paired reports, unchanged resources, parallel timeline and spend cap."""
import argparse
from decimal import Decimal
import json
from pathlib import Path

from sharded_pilot import RATE
from summarize_sharded_pilot import require, summarize


def parallel_summary(out,evidence,billing_name='billing-final.json',previous_name='billing-second.json'):
    result=summarize(out,evidence,billing_name,previous_name)
    read=lambda p:json.loads(p.read_text())
    receipt=read(out/'receipt.json');plan=receipt['plan']
    require(plan.get('mode')=='optimizeN11max' and plan['max_simultaneous_vms']==2,'Wrong mode or cap')
    require((plan['cpu'],plan['memory_gib'],plan['workers'],plan['test_timeout'])==(16,64,8,600),
            'Scientific resource configuration changed')
    require(plan['cumulative_allocation_seconds']==1800 and plan['estimate_usd']==2,'Budget contract changed')
    require(all(g['lifetime_seconds']==780 for g in receipt['groups']),'VM lifetime changed')
    events=[]
    for g in receipt['groups']:
        start,end=g['start_offset_seconds'],g['end_offset_seconds']
        require(0<=start<end<=receipt['wall_seconds'],'Invalid VM timing interval')
        require(abs((end-start)-g['wall_seconds'])<.1,'VM phase timeline differs')
        events.extend([(start,1),(end,-1)])
    active=peak=0;overlap=0.;last=0.
    for t,delta in sorted(events):
        if active==2:overlap+=t-last
        active+=delta;peak=max(peak,active);last=t
        require(0<=active<=2,'Concurrency cap exceeded')
    require(active==0 and peak==2 and overlap>0,'No actual parallel comparison')
    histories=receipt['reservation_history']
    require(len([r for r in histories if r['event']=='reserve'])==8 and
            len([r for r in histories if r['event']=='finish'])==8,'Missing budget transitions')
    for r in histories:
        require(0<=r['reserved_total_seconds']<=1800 and len(r['active_lifetime_reservations'])<=2,
                'Budget reservation exceeded')
        require(abs(r['reserved_total_seconds']-(r['completed_upper_bound_vm_seconds']+
                sum(r['active_lifetime_reservations'].values())))<1e-6,'Reservation arithmetic differs')
    require(not histories[-1]['active_lifetime_reservations'],'Outstanding VM reservations')
    serial=read(evidence/'2026-10-22-sharded-snapshot-pilot/comparison.json')
    serial_receipt=read(evidence/'2026-10-22-sharded-snapshot-pilot/receipt.json')
    require(receipt['helper_sha256']==serial_receipt['helper_sha256'],'Worker helpers changed from serial run')
    require(plan['groups']==serial_receipt['plan']['groups'],'Shard assignment or snapshot identity changed')
    serial_reports=read(evidence/'2026-10-22-sharded-snapshot-pilot/reports.json')
    actual_reports=read(out/'reports.json')
    different_serial=[iid for iid in sorted(serial_reports) if actual_reports.get(iid)!=serial_reports[iid]]
    cost=Decimal(result['cost_usd']);old=Decimal(serial['cost_usd'])
    require(cost<=2,'Actual app billing exceeded authorized $2')
    vm_wall=sum(g['wall_seconds'] for g in receipt['groups'])
    bound=sum(min(g['wall_seconds'],780) for g in receipt['groups'])
    require(abs(bound-receipt['completed_upper_bound_vm_seconds'])<1e-6,'Released reservation differs from VM timing')
    result.update(mode='optimizeN11max',max_simultaneous_vms=2,observed_peak_vm_intervals=peak,
        overlapping_vm_interval_seconds=overlap,sum_vm_coordinator_intervals_seconds=vm_wall,
        reservation_cpu_memory_upper_bound_usd=bound*RATE,unchanged_scientific_configuration=True,
        complete_report_matches_serial_N11=24-len(different_serial),different_serial_N11_ids=different_serial,
        serial_N11_cost_usd=str(old),
        serial_N11_wall_seconds=serial['sharded_wall_seconds'],
        saving_vs_serial_N11_percent=str((old-cost)/old*100),
        wall_reduction_vs_serial_N11_percent=(1-receipt['wall_seconds']/serial['sharded_wall_seconds'])*100,
        qualified=result['qualified'] and not different_serial and cost<=2,
        one_run_per_mode_no_variance_estimate=True)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--previous-billing',default='billing-second.json')
    a=p.parse_args();r=parallel_summary(a.out,Path(__file__).resolve().parents[1]/'evidence',previous_name=a.previous_billing)
    (a.out/'comparison.json').write_bytes((json.dumps(r,indent=2)+'\n').encode())
    print(json.dumps({k:v for k,v in r.items() if k!='groups'},indent=2))

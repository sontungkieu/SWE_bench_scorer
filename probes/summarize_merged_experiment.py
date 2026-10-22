"""Offline audit of N7 receipts, original reports, phase logs and provider costs."""
import argparse
from collections import Counter
from decimal import Decimal
import gzip
import json
from pathlib import Path
import re

from score_phase_analysis import compare_runs, from_archive, from_tree
from snapshot_pilot import dump, validate_inputs
from summarize_snapshot_pilot import compare


def cost_totals(billing, first):
    values=[Decimal(str(v)) for v in billing['cost_by_role_usd'].values()]
    prior=Decimal(str(first['compute_egress_usd']))
    if any(not v.is_finite() or v<0 for v in values+[prior]):
        raise ValueError('Invalid provider cost')
    combined=sum(values,prior)
    complete=not billing['missing_billing_roles'] and not first.get('missing_billing_roles',[])
    return {'first_stopped_usd':str(prior),'recovery_by_role_usd':billing['cost_by_role_usd'],
            'combined_compute_egress_usd':str(combined),
            'billing_complete':complete,
            'within_approved_eight_usd':complete and combined<=8,
            'before_credits':True,'not_final_invoice':True,'snapshot_storage_excluded':True}


def summarize(out,first_billing,root):
    selection=validate_inputs(out)
    expected=[r['instance_id'] for r in selection['selected']]
    record=json.loads((out/'receipt.json').read_text())
    billing=json.loads((out/'billing.json').read_text()) if (out/'billing.json').exists() else None
    live_cleanup_verified=(billing is not None and billing['app_ids']==record['app_ids']
                           and not any(billing['remaining_vms'].values()))
    context=record['source_context']
    wanted=context['evaluator_sha256'] | {k:v for k,v in context['helper_sha256'].items()
                                         if k!='merged_catalog_worker.py'}
    baseline=json.loads((out/'baseline-reports.json').read_text())
    result={'source_commit':context['git_commit'],'full_500_evaluated':False,
            'all_vms_terminated':record['all_vms_terminated'],'app_ids':record['app_ids'],
            'budget':json.loads((out/'contract.json').read_text()),'modes':{}}
    e=root/'evidence'
    runs={'snapshot24':from_archive(e/'2026-10-22-n7-merged-qualification/baseline24-phase-logs.tar.gz'),
          'N11':from_archive(e/'2026-10-22-sharded-snapshot-pilot/raw-evidence.tar.gz'),
          'N11max_unqualified':from_archive(e/'2026-10-22-optimizeN11max/raw-evidence.tar.gz')}
    for mode,status in record['modes'].items():
        folder=out/mode
        reports=json.loads((folder/'reports.json').read_text()) if (folder/'reports.json').exists() else {}
        checked=compare(expected,baseline,reports)
        readbacks=list(folder.glob('score-*/source-readback.json'))
        image_rows=[r for p in folder.glob('score-*/output/n7-shard-*/images.json') for r in json.loads(p.read_text())]
        environments=[json.loads(p.read_text()) for p in folder.glob('score-*/output/environment.json')]
        timings=[json.loads(p.read_text()) for p in folder.glob('score-*/output/summary.json')]
        models={p.parent.parent.name:json.loads(p.read_text())['cpu_models']
                for p in folder.glob('score-*/output/host-before.json')}
        cli=[float(v) for p in folder.glob('score-*/console.log')
             for v in re.findall(r'^SWE_TEST_SECONDS=([\d.]+)$',p.read_text(),re.M)]
        row={'official_comparison':checked,'catalog_complete':status.get('catalog_complete',False),
             'score_complete':status.get('complete',False),'build_error':status.get('build_error'),
             'score_error':status.get('score_error'),'score_vm_count':len(status['score_groups']),
             'source_readbacks':len(readbacks),
             'live_cleanup_verified':live_cleanup_verified,
             'all_source_readbacks_match':bool(readbacks) and all(json.loads(p.read_text())==wanted for p in readbacks),
             'image_sources':dict(Counter(r['source'] for r in image_rows)),
             'image_count':len(image_rows),'cache_added_bytes':sum(r['cache_added_bytes'] for r in image_rows),
             'harness_cli_invocations':len(cli),'sum_harness_cli_seconds':sum(cli),
             'sum_vm_wall_seconds':sum(g.get('wall_seconds',0) for g in status['score_groups']),
             'sum_docker_ready_seconds':sum(g.get('docker_ready_seconds',0) for g in status['score_groups']),
             'sum_metadata_verification_seconds':sum(v['metadata_verification_seconds'] for v in environments),
             'sum_test_and_cleanup_seconds':sum(v['test_and_cleanup_seconds'] for v in timings),
             'sum_image_cleanup_seconds':sum(v['image_cleanup_seconds'] for v in timings),
             'score_wall_seconds':status.get('score_wall_seconds'), 'cpu_models_by_group':models,
             'effective_environment_configs_match':bool(environments) and all(v['workers']==8 and
                 v['test_timeout']==600 and v['cpu_request']==16 and v['memory_gib']==64 and
                 v['harness']=='4.1.0' and not v['benchmark_tests_or_clock_modified'] for v in environments)}
        row['qualified_24_control_run']=(row['score_complete'] and checked['complete_report_matches']==24
            and not checked['missing_ids'] and not checked['extra_ids'] and not checked['different_report_ids']
            and row['all_source_readbacks_match'] and len(readbacks)==row['score_vm_count']
            and len(image_rows)==24 and row['image_sources']=={'local':24} and row['cache_added_bytes']==0
            and len(environments)==row['score_vm_count'] and len(timings)==row['score_vm_count']
            and len(cli)==row['score_vm_count'] and row['effective_environment_configs_match']
            and record['all_vms_terminated'] and live_cleanup_verified)
        result['modes'][mode]=row
        try:runs[mode]=from_tree(folder)
        except ValueError as exc:row['phase_analysis_error']=str(exc)
    if billing is not None:
        result['costs']=cost_totals(billing,json.loads(first_billing.read_text()))
    phase=compare_runs(runs,'snapshot24')
    dump(out/'comparison.json',result)
    (out/'phase-analysis.json.gz').write_bytes(gzip.compress((json.dumps(phase,indent=2)+'\n').encode(),mtime=0))
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--first-billing',type=Path,required=True)
    p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    a=p.parse_args();print(json.dumps(summarize(a.out,a.first_billing,a.root),indent=2))

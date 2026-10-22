"""Saved evaluation modes for the frozen scientific 24-case comparison.

These are research entry points, separate from the production web/launch.py OCI
backend. N denotes total full-500 catalog shards, not workers per VM.
N7 catalogs have been built and tested; parity failed, so this entry point only
allows offline N7 planning. No full-500 execution is exposed here.
"""
import argparse
import gzip
import json
import os
from pathlib import Path
import shutil
import sys

PROBES = Path(__file__).resolve().parent/'probes'
sys.path.insert(0,str(PROBES))
MODES = {
    'native': {'backend':'official --modal true + qualified SDK overlay', 'scope':24,
               'estimate_usd':3, 'scientific_parity_qualified':False},
    'optimizeN11': {'backend':'VM snapshot', 'catalog_shards':11, 'scope':24,
                    'max_active_vms':1, 'cpu':16, 'memory_gib':64, 'workers':8, 'test_timeout':600},
    'optimizeN11max': {'backend':'VM snapshot', 'catalog_shards':11, 'scope':24,
                       'scientific_parity_qualified':False,
                       'qualification_note':'23/24 reports matched; matplotlib-20859 warning test differed in cap2 pilot',
                       'max_active_vms':2, 'cpu':16, 'memory_gib':64, 'workers':8, 'test_timeout':600,
                       'budget_usd':2, 'cumulative_allocation_seconds':1800, 'lifetime_per_vm_seconds':780},
    'optimizeN7merge': {'backend':'VM snapshot', 'catalog_shards':7,
                       'status':'snapshots_built_parity_failed', 'execution_enabled':False,
                       'layout':'100,100,59,(27+21),43,100,(24+8+9+9)',
                       'max_layer_tar_gib':96, 'scientific_parity_qualified':False,
                       'official_controls_resolved':23, 'baseline_report_matches':23,
                       'catalog':'evidence/2026-10-22-n7-merged-qualification/optimizeN7merge-catalog.json',
                       'catalog_sha256':'39aa9ca5302ccc42cd5ff6adabadc48976451527d8c47af11380ab378328f2c5'},
    'optimizeN7weighted': {'backend':'VM snapshot', 'catalog_shards':7,
                          'status':'snapshots_built_parity_failed', 'execution_enabled':False,
                          'layout':'whole-N11-shard partition minimizing a weak runtime proxy',
                          'max_layer_tar_gib':64, 'scientific_parity_qualified':False,
                          'official_controls_resolved':22, 'baseline_report_matches':22,
                          'catalog':'evidence/2026-10-22-n7-merged-qualification/optimizeN7weighted-catalog.json',
                          'catalog_sha256':'eaba8c886b7e7c9ead2acbc3491863f9152d27df2bc91733042cbf75d2da5fd8'},
}
INPUTS = ['pilot.json','preds.jsonl','selection.json','usable-shards-catalog.json',
          'image-plan.json','snapshot-plan-hashes.json','baseline-reports.json']


def prepare(out, reference, commit):
    from snapshot_pilot import dump, validate_inputs
    validate_inputs(reference)
    if out.exists():
        raise ValueError('Use a new output directory; never overwrite a prior run')
    for name in INPUTS:
        if not (reference/name).is_file():
            raise ValueError('Missing reference input: '+name)
    out.mkdir(parents=True)
    for name in INPUTS:
        shutil.copyfile(reference/name,out/name)
    dump(out/'source-context.json',{'git_commit':commit, 'reference_run':str(reference),
                                   'original_evaluator_sources_unchanged':True})


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['list','plan','prepare','run','collect'])
    p.add_argument('--mode',choices=MODES)
    p.add_argument('--out',type=Path)
    p.add_argument('--reference',type=Path)
    p.add_argument('--commit')
    p.add_argument('--source',type=Path)
    p.add_argument('--baseline',type=Path)
    p.add_argument('--billing-name',default='billing.json')
    a=p.parse_args()
    if a.action=='list':
        print(json.dumps(MODES,indent=2));return 0
    merge_modes=('optimizeN7merge','optimizeN7weighted')
    if a.action=='plan':
        if a.mode not in merge_modes:p.error('plan requires an optimizeN7 merge mode')
        if a.out is None:p.error('--out required; use a new plan directory')
        if a.out.exists():raise ValueError('Use a new output directory; never overwrite a prior plan')
        from merged_snapshot_plan import plan_mode
        value=plan_mode(a.mode)
        a.out.mkdir(parents=True)
        (a.out/'merge-plan.json.gz').write_bytes(gzip.compress((json.dumps(value,indent=2)+'\n').encode(),mtime=0))
        print(json.dumps({'mode':a.mode,'status':value['status'],'summary':value['summary']},indent=2))
        return 0
    if a.mode in merge_modes:
        p.error('N7 catalogs were built, but official report parity failed: run/prepare/collect stay disabled here; use plan or the separate experiment collector for retained runs')
    if a.out is None:p.error('--out required')
    if a.action=='prepare':
        if not a.reference or not a.commit:p.error('prepare requires --reference and immutable --commit')
        prepare(a.out.resolve(),a.reference.resolve(),a.commit);return 0
    if a.mode is None:p.error('--mode required')
    if a.action=='collect':
        if a.mode=='native':p.error('native uses its own cost_pilot evidence collector')
        from sharded_pilot import collect
        collect(a.out.resolve(),a.billing_name);return 0
    if not a.source:p.error('--source required')
    if os.environ.get('MODAL_PROFILE')!='phamvanvuhoan':
        raise ValueError('Only the authorized phamvanvuhoan profile may run')
    if a.mode=='native':
        from snapshot_pilot import validate_inputs
        from cost_pilot import native
        validate_inputs(a.out.resolve())
        native(a.out.resolve(),a.source.resolve());return 0
    if not a.baseline:p.error('--baseline required')
    if a.mode=='optimizeN11':
        from sharded_pilot import run
    else:
        from parallel_sharded_pilot import run
    return run(a.out.resolve(),a.source.resolve(),a.baseline.resolve())


if __name__=='__main__':raise SystemExit(main())

"""Saved evaluation modes for the frozen scientific 24-case comparison.

These are research entry points, separate from the production web/launch.py OCI
backend. N denotes the 11-snapshot full-500 catalog, not workers per VM.
No full-500 execution is exposed here.
"""
import argparse
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
    p.add_argument('action',choices=['list','prepare','run','collect'])
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

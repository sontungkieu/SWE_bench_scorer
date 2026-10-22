"""Regenerate official reports from retained output, never rerun tests or a VM.

Use the existing remote SWE-bench 4.1.0 environment, not a new local install.
"""
import argparse
import hashlib
import importlib.metadata
import inspect
import json
from pathlib import Path


def recheck(out):
    from swebench.harness.grading import get_eval_report
    from swebench.harness.test_spec.test_spec import make_test_spec
    from snapshot_pilot import PILOT_SHA, PREDS_SHA, validate_inputs
    if importlib.metadata.version('swebench')!='4.1.0':raise ValueError('Wrong official parser version')
    validate_inputs(out)
    data={r['instance_id']:r for r in json.loads((out/'pilot.json').read_text())}
    predictions={r['instance_id']:r for r in map(json.loads,(out/'preds.jsonl').read_text().splitlines())}
    result={'harness':'4.1.0','pilot_sha256':PILOT_SHA,'predictions_sha256':PREDS_SHA,
            'benchmark_tests_run':False,'new_vms_created':False,
            'grading_source_sha256':hashlib.sha256(Path(inspect.getfile(get_eval_report)).read_bytes()).hexdigest(),
            'modes':{}}
    for mode in ['optimizeN7merge','optimizeN7weighted']:
        folder=out/mode;official=json.loads((folder/'reports.json').read_text());rows=[]
        if set(official)!=set(data):raise ValueError('Exactly 24 reports required')
        for iid in sorted(data):
            paths=list(folder.glob('score-*/output/**/'+iid+'/test_output.txt'))
            if len(paths)!=1:raise ValueError('Missing/duplicate raw output')
            path=paths[0]
            regenerated=get_eval_report(make_test_spec(data[iid],namespace='swebench'),predictions[iid],str(path),True)
            saved=json.loads(path.with_name('report.json').read_text())
            rows.append({'instance_id':iid,'raw_output_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                         'matches_per_case_report':regenerated==saved,
                         'matches_collected_report':regenerated=={iid:official[iid]},
                         'resolved':regenerated[iid]['resolved'],
                         'required_failures':{k:v['failure'] for k,v in regenerated[iid]['tests_status'].items() if v['failure']}})
        result['modes'][mode]={'count':len(rows),'all_match':all(r['matches_per_case_report'] and r['matches_collected_report'] for r in rows),
                              'resolved_count':sum(r['resolved'] for r in rows),'rows':rows}
    result['all_48_match']=all(v['all_match'] for v in result['modes'].values())
    (out/'parser-recheck.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();value=recheck(a.out)
    print(json.dumps({k:v for k,v in value.items() if k!='modes'}))

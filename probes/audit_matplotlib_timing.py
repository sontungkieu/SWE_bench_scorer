"""Read-only audit of historical mismatch and independently observed clock bounds."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile

IID='matplotlib__matplotlib-20859'
TEST='lib/matplotlib/tests/test_legend.py::test_warn_big_data_best_loc'


def historical(evidence):
    serial=evidence/'2026-10-22-sharded-snapshot-pilot'
    parallel=evidence/'2026-10-22-optimizeN11max'
    read=lambda p:json.loads(p.read_text())
    a=read(serial/'receipt.json');b=read(parallel/'receipt.json')
    assert a['source_sha256']==b['source_sha256'] and a['helper_sha256']==b['helper_sha256']
    assert a['plan']['groups']==b['plan']['groups']
    keys=['cpu','memory_gib','workers','test_timeout']
    assert {k:a['plan'][k] for k in keys}=={k:b['plan'][k] for k in keys}
    before=read(serial/'reports.json')[IID];after=read(parallel/'reports.json')[IID]
    assert before['resolved'] is True and after['resolved'] is False
    assert before['tests_status']['FAIL_TO_PASS']==after['tests_status']['FAIL_TO_PASS']
    aa=before['tests_status']['PASS_TO_PASS'];bb=after['tests_status']['PASS_TO_PASS']
    assert aa['failure']==[] and bb['failure']==[TEST]
    assert set(aa['success'])-set(bb['success'])=={TEST}
    suffix='/shard-02/output/shard-02/logs/run_evaluation/shard-02/PILOT-GOLD-NOT-MODEL/'+IID+'/'
    hashes={};test_outputs=[]
    for filename in ['eval.sh','patch.diff','test_output.txt']:
        values=[]
        for directory in [serial,parallel]:
            with tarfile.open(directory/'raw-evidence.tar.gz') as archive:
                names=[m.name for m in archive if m.isfile() and m.name.endswith(suffix+filename)]
                assert len(names)==1
                content=archive.extractfile(names[0]).read()
                values.append(hashlib.sha256(content).hexdigest())
                if filename=='test_output.txt':test_outputs.append(content.decode())
        if filename!='test_output.txt':
            assert values[0]==values[1]
            hashes[filename]=values[0]
    runtime_lines=[sorted(line for line in text.splitlines() if line.startswith(('Requirement already satisfied:','platform linux --'))) for text in test_outputs]
    assert runtime_lines[0]==runtime_lines[1] and runtime_lines[0]
    return {'resource_configuration':{k:a['plan'][k] for k in keys},'same_shard_mapping_and_snapshot_ids':True,
       'same_four_evaluator_and_two_helper_hashes':True,'same_eval_script_and_gold_patch_sha256':hashes,
       'officially_resolved_serial':True,'officially_resolved_parallel':False,
       'only_changed_required_test':TEST,'primary_FAIL_TO_PASS_still_passes':True,
       'same_dependency_and_python_pytest_lines_in_retained_output':runtime_lines[0],
       'historical_internal_clock_not_recorded':True,'physical_CPU_model_not_pinned_by_request':True}


def observed(out):
    data=json.loads((out/'timing-output/observations.json').read_text())
    official=json.loads((out/'timing-output/official-repetitions.json').read_text())
    assert len(data)==len(official)==2
    results=[]
    for row in data:
        assert row['observer_uses_real_clock'] and row['test_or_clock_monkeypatched'] is False
        assert row['file_hashes_before']==row['file_hashes_after']
        assert len(row['calls'])==2
        calls=[]
        for call in row['calls']:
            if not call['clock_condition_brackets']:
                reason='early_return' if call['badness']==0 else 'condition_clock_not_observed'
                calls.append({'classification':reason,'badness':call['badness']});continue
            assert call['loc_used_default'] and call['badness']>0
            assert len(call['clock_condition_brackets'])==1
            bracket=call['clock_condition_brackets'][0]
            low,high=bracket['lower_seconds'],bracket['upper_seconds']
            assert 0<=low<=high
            state='below_one_second' if high<1 else 'above_one_second' if low>1 else 'straddles_threshold'
            calls.append({'classification':state,'lower_seconds':low,'upper_seconds':high,'badness':call['badness']})
        results.append({'repetition':row['repetition'],'profiled_test_call_exception':row['test_call_exception'],'calls':calls})
    return {'fixed_official_repetitions':[{'repetition':r['repetition'],'matplotlib_resolved':r['reports'][IID]['resolved'],
                'pass_to_pass_failures':r['reports'][IID]['tests_status']['PASS_TO_PASS']['failure']} for r in official],
            'profiled_repetitions':results,'test_and_library_file_hashes_unchanged_during_each_profiled_test':True,
            'profiling_has_overhead':True,'original_24_case_result_not_replaced':True,
            'new_observations_do_not_recover_historical_unrecorded_clock_values':True}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path);p.add_argument('--result',type=Path,required=True)
    a=p.parse_args();e=Path(__file__).resolve().parents[1]/'evidence'
    value={'historical':historical(e)}
    if a.out:value['observed']=observed(a.out)
    a.result.write_bytes((json.dumps(value,indent=2)+'\n').encode());print(json.dumps(value,indent=2))

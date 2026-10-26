import copy
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from batch_plan import (EGRESS_RESERVE,RATE,WAVE_RESERVE,VM_OVERHEAD,assignments,job_id,
                         lifetime_for,merge_output,split_wave,summarize_file,validate_budget,waves)
from n11_batch_predictions import process_wave,validate_assignment,write_results,prepare,validate_inputs


class BatchPlanTests(unittest.TestCase):
    def fixture(self):
        data=[dict(instance_id=f'repo__case-{i}') for i in range(500)]
        files=[dict(id='fp4',rows=[dict(r,model_name_or_path='same-model',model_patch='fix' if i<332 else '') for i,r in enumerate(data)]),
               dict(id='super',rows=[dict(r,model_name_or_path='same-model',model_patch='other fix' if i<389 else '') for i,r in enumerate(data)])]
        groups=[dict(index=i,instance_ids=[r['instance_id'] for n,r in enumerate(data[:389]) if n%11==i],images=[]) for i in range(11)]
        return data,files,groups

    def test_two_same_model_files_have_721_independent_jobs_and_eleven_restores(self):
        data,files,groups=self.fixture();selected,planned=assignments(files,data,groups)
        tasks=[t for g in planned for t in g['jobs']]
        self.assertEqual(len(selected),500);self.assertEqual(len(planned),11)
        self.assertEqual(len(tasks),721);self.assertEqual(len({t['job_id'] for t in tasks}),721)
        self.assertEqual(tasks[:2],[dict(job_id='fp4::repo__case-0',file_id='fp4',instance_id='repo__case-0'),
                                  dict(job_id='super::repo__case-0',file_id='super',instance_id='repo__case-0')])
        for g in planned:
            for wave in waves(g['jobs']):
                self.assertLessEqual(sum(map(len,split_wave(wave).values())),8)

    def test_partial_different_coverage_and_empty_patches_are_not_executed(self):
        data,files,groups=self.fixture()
        files[0]['rows']=files[0]['rows'][:2];files[1]['rows'][0]['model_patch']=''
        selected,planned=assignments(files,data,[dict(index=0,instance_ids=['repo__case-0','repo__case-1'],images=[])],['repo__case-0','repo__case-1','repo__case-499'])
        self.assertEqual(len(selected),3);self.assertEqual(len(planned[0]['jobs']),3)
        self.assertNotIn('super::repo__case-0',{t['job_id'] for t in planned[0]['jobs']})

    def test_job_identity_and_plan_coverage_fail_closed(self):
        data,files,groups=self.fixture();files[1]['id']='fp4'
        with self.assertRaises(ValueError):assignments(files,data,groups)
        data,files,groups=self.fixture();groups.pop()
        with self.assertRaises(ValueError):assignments(files,data,groups)
        with self.assertRaises(ValueError):job_id('../escape','repo__case-0')
        with self.assertRaises(ValueError):waves([dict(job_id='fp4::x',file_id='super',instance_id='x')])

    def test_shared_budget_cannot_be_reused_in_full_for_each_vm(self):
        budget=Decimal('8');validate_budget(budget,Decimal(0),budget)
        first=lifetime_for(200,0,budget)
        second=lifetime_for(200,2000,budget)
        self.assertLess(second,first)
        self.assertLessEqual((Decimal(second)+2000)*RATE+EGRESS_RESERVE,budget)
        used=float((budget-EGRESS_RESERVE)/RATE)-WAVE_RESERVE-VM_OVERHEAD+1
        self.assertIsNone(lifetime_for(8,used,budget))
        for b,p,c in [('NaN','0','8'),('8','0.01','8'),('8','0','Infinity')]:
            with self.assertRaises(ValueError):validate_budget(Decimal(b),Decimal(p),Decimal(c))

    def test_empty_file_and_missing_source_are_separate_zero_credit_categories(self):
        data,files,_=self.fixture();rows=files[0]['rows'][:1];rows[0]['model_patch']=''
        result=summarize_file(rows,[r['instance_id'] for r in data],{},{},[],True)
        self.assertEqual(result['verified_score'],0)
        self.assertEqual(result['empty_patch_count'],1)
        self.assertEqual(result['missing_source_prediction_count'],499)
        self.assertEqual(result['official_report_count'],0)

    def test_timeout_or_missing_report_is_unknown_and_does_not_borrow_other_files_pass(self):
        rows=[dict(instance_id='repo__case-1',model_patch='fix')]
        first=summarize_file(rows,['repo__case-1'],{}, {},['repo__case-1'],False)
        second=summarize_file(rows,['repo__case-1'],{'repo__case-1':{'resolved':True}},
            {'repo__case-1':{'execution_valid':True,'qualified_resolved':True}},['repo__case-1'],False)
        self.assertIsNone(first['cases'][0]['official_resolved'])
        self.assertEqual(first['missing_attempted_report_ids'],['repo__case-1'])
        self.assertIsNone(first['subset_resolved_rate']);self.assertEqual(second['subset_resolved_rate'],1)

    def test_unmaterialized_binary_or_capture_error_cannot_create_a_headline_score(self):
        rows=[dict(instance_id='repo__case-1',model_patch='fix')]
        result=summarize_file(rows,['repo__case-1'],{'repo__case-1':{'resolved':True}},
            {'repo__case-1':{'execution_valid':False,'qualified_resolved':None,'issues':['prediction_not_fully_materialized']}},['repo__case-1'],False)
        self.assertTrue(result['reports_complete']);self.assertFalse(result['qualified_complete'])
        self.assertEqual(result['official_resolved_count'],1);self.assertIsNone(result['subset_resolved_rate'])

    def test_surviving_wave_results_are_retained_without_cross_file_or_duplicate_merges(self):
        tasks=[dict(job_id=job_id(f,'repo__case-1'),file_id=f,instance_id='repo__case-1') for f in ['fp4','super']]
        payload=dict(attempted_job_ids=[t['job_id'] for t in tasks],reports={'fp4::repo__case-1':{'resolved':True}},
            validity={'fp4::repo__case-1':{'execution_valid':True,'qualified_resolved':True}},errors={'super::repo__case-1':{'kind':'official_timeout'}})
        reports={};validity={};attempted=[];merge_output(tasks,payload,reports,validity,attempted)
        self.assertEqual(len(reports),1);self.assertEqual(len(attempted),2)
        with self.assertRaises(ValueError):merge_output(tasks,payload,reports,validity,attempted)
        bad=copy.deepcopy(payload);bad['reports']['outside::repo__case-1']={'resolved':True}
        with self.assertRaises(ValueError):merge_output(tasks,bad,{},{},[])

    def test_changed_worker_assignment_cannot_use_a_frozen_plan(self):
        group=dict(index=0,jobs=[],images=[],snapshot_plan_sha256='hash')
        validate_assignment(dict(groups=[group]),dict(group,work_seconds=780))
        with self.assertRaises(ValueError):validate_assignment(dict(groups=[group]),dict(group,jobs=['wrong'],work_seconds=780))

    def test_prepare_freezes_exact_bytes_and_rejects_later_input_or_config_changes(self):
        import hashlib
        data,files,_=self.fixture()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);manifest=[]
            for f in files:
                path=root/(f['id']+'.jsonl')
                path.write_bytes(('\n'.join(json.dumps(r) for r in f['rows'])+'\n').encode())
                manifest.append(dict(id=f['id'],path=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
            (root/'manifest.json').write_text(json.dumps(dict(files=manifest)))
            dataset=root/'dataset.json';dataset.write_text(json.dumps(data))
            groups=[dict(index=0,instance_ids=[r['instance_id'] for r in data[:389]],images=[])]
            with patch('n11_batch_predictions.DATASET_SHA',hashlib.sha256(dataset.read_bytes()).hexdigest()),patch('n11_batch_predictions.catalog_groups',return_value=groups),patch('n11_batch_predictions.source_files',return_value={'fixture.py':dataset}),patch('n11_batch_predictions.input_errors_for_rows',return_value={}):
                plan=prepare(root/'batch',root,root/'manifest.json',dataset,'a'*40,Decimal('8'),Decimal(0),Decimal('8'))
                self.assertEqual(plan['active_job_count'],721)
                self.assertEqual(len(validate_inputs(root/'batch',plan)),2)
                for f in manifest:self.assertEqual((root/'batch/inputs'/(f['id']+'.jsonl')).read_bytes(),(root/f['path']).read_bytes())
                with self.assertRaises(ValueError):validate_inputs(root/'batch',dict(plan,workers_total=16))
                (root/'batch/inputs/fp4.jsonl').write_bytes(b'changed')
                with self.assertRaises(ValueError):validate_inputs(root/'batch',plan)

    def test_proven_input_format_errors_receive_no_job_or_fabricated_test_verdict(self):
        data,files,_=self.fixture();files[0]['rows']=files[0]['rows'][:1];files[1]['rows']=files[1]['rows'][:1]
        files[0]['input_errors']={'repo__case-0':{'kind':'input_format_error'}}
        selected,planned=assignments(files,data,[dict(index=0,instance_ids=['repo__case-0'],images=[])],['repo__case-0'])
        self.assertEqual([t['file_id'] for t in planned[0]['jobs']],['super'])
        result=summarize_file(files[0]['rows'],selected,{},{},[],False,files[0]['input_errors'])
        self.assertEqual(result['input_error_count'],1);self.assertEqual(result['attempted_count'],0)
        self.assertEqual(result['cases'][0]['status'],'input_error')
        self.assertIsNone(result['cases'][0]['official_resolved']);self.assertIsNone(result['subset_resolved_rate'])

    def test_reports_of_same_model_and_same_instance_are_written_to_separate_file_directories(self):
        rows={f:[dict(instance_id='repo__case-1',model_patch='patch')] for f in ['fp4','super']}
        plan=dict(files=[dict(id=f,sha256=f) for f in rows],selected_instance_ids=['repo__case-1'],full_verified_accounting=False,active_job_count=2)
        reports={job_id('fp4','repo__case-1'):{'resolved':True},job_id('super','repo__case-1'):{'resolved':False}}
        validity={key:dict(execution_valid=True,qualified_resolved=r['resolved']) for key,r in reports.items()}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);write_results(root,plan,rows,reports,validity,list(reports),{})
            a=json.loads((root/'results/fp4/summary.json').read_text());b=json.loads((root/'results/super/summary.json').read_text())
            self.assertEqual(a['official_resolved_count'],1);self.assertEqual(b['official_resolved_count'],0)


class WaveProcessTests(unittest.TestCase):
    def test_all_file_groups_start_before_waiting_and_global_workers_sum_to_eight(self):
        events=[];tasks=[dict(job_id=job_id(f,'repo__case-'+str(i)),file_id=f,instance_id='repo__case-'+str(i)) for i in range(4) for f in ['fp4','super']]
        class Child:
            def __init__(self,command,**kwargs):
                self.returncode=None;group=json.loads(Path(command[-1]).read_text());events.append(('start',group,kwargs['start_new_session']))
            def wait(self,timeout):self.returncode=0;events.append(('wait',));return 0
            def poll(self):return self.returncode
        with tempfile.TemporaryDirectory() as temp,patch('n11_batch_predictions.subprocess.Popen',Child):
            root=Path(temp);process_wave(root,root/'output',0,tasks,time.monotonic()+780)
        self.assertEqual([e[0] for e in events],['start','start','wait','wait'])
        groups=[e[1] for e in events if e[0]=='start']
        self.assertEqual(sum(len(g['instance_ids']) for g in groups),8)
        self.assertEqual(len({g['run_id'] for g in groups}),2)
        self.assertTrue(all(e[2] for e in events if e[0]=='start'))

    def test_process_failure_still_terminates_other_running_groups(self):
        children=[]
        class Child:
            def __init__(self,*args,**kwargs):self.returncode=None;children.append(self)
            def wait(self,timeout):raise subprocess.TimeoutExpired('fixture',timeout)
            def poll(self):return self.returncode
        tasks=[dict(job_id=job_id(f,'repo__case-1'),file_id=f,instance_id='repo__case-1') for f in ['fp4','super']]
        def stop(p):p.returncode=-15
        with tempfile.TemporaryDirectory() as temp,patch('n11_batch_predictions.subprocess.Popen',Child),patch('n11_batch_predictions.terminate_child',side_effect=stop) as cleanup:
            with self.assertRaises(subprocess.TimeoutExpired):process_wave(Path(temp),Path(temp)/'output',0,tasks,time.monotonic()+780)
            self.assertEqual(cleanup.call_count,2)
            self.assertTrue(all(p.returncode==-15 for p in children))


if __name__=='__main__':unittest.main()

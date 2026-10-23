import copy
from datetime import datetime, timezone
from decimal import Decimal
import math
import unittest

from n11_predictions import (BUDGET, EGRESS_RESERVE, PRIOR_COST, RATE, WAVE_RESERVE,
                             VM_OVERHEAD, lifetime_for, make_groups, validate_wave_reports, merge_progress,validate_budget,validate_selection,compute_assignment_rows)


class AllocationTests(unittest.TestCase):
    def test_followup_run_uses_updated_spend_and_remaining_budget(self):
        budget=Decimal('2.70');prior=Decimal('5.29256583')
        validate_budget(budget,prior)
        life=lifetime_for(56,0,budget)
        self.assertLessEqual(Decimal(life)*RATE+EGRESS_RESERVE,budget)
        self.assertIsNone(lifetime_for(12,1600,budget))
        with self.assertRaises(ValueError):validate_budget(Decimal('6.30'),prior)

    def test_invalid_contract_budget_fails_closed(self):
        for budget,prior in [('NaN','0'),('Infinity','0'),('0.1','0'),('1','-1'),('1','NaN')]:
            with self.assertRaises(ValueError):validate_budget(Decimal(budget),Decimal(prior))

    def test_new_user_cap_must_be_explicit_and_finite(self):
        prior=Decimal('5.29256583');budget=Decimal('3.00')
        with self.assertRaises(ValueError):validate_budget(budget,prior)
        validate_budget(budget,prior,Decimal('8.29256583'))
        with self.assertRaises(ValueError):validate_budget(budget,prior,Decimal('NaN'))

    def test_long_shard_cannot_reserve_more_than_remaining_cumulative_budget(self):
        life=lifetime_for(73,0)
        self.assertLess(life,math.ceil(73/8)*WAVE_RESERVE+VM_OVERHEAD)
        self.assertLessEqual(Decimal(life)*RATE+EGRESS_RESERVE,BUDGET)
        self.assertLessEqual(PRIOR_COST+BUDGET,8)

    def test_finish_releases_unused_lifetime_before_the_next_vm(self):
        first=lifetime_for(73,0)
        second=lifetime_for(62,100)
        self.assertGreater(second,first-100-2)
        self.assertLessEqual((Decimal(second)+100)*RATE+EGRESS_RESERVE,BUDGET)

    def test_insufficient_budget_stops_before_the_wave_without_shortening_it(self):
        used=float((BUDGET-EGRESS_RESERVE)/RATE)-900
        self.assertIsNone(lifetime_for(8,used))
        self.assertEqual(WAVE_RESERVE,780)

    def test_invalid_usage_cannot_open_an_allocation(self):
        for used in [-1,float('nan'),float('inf')]:
            with self.assertRaises(ValueError):lifetime_for(8,used)


class InputAccountingTests(unittest.TestCase):
    def test_public_reference_missing_unselected_source_row_is_not_fabricated(self):
        rows=[{'instance_id':str(i),'model_patch':'diff'} for i in range(499)]
        data=[{'instance_id':str(i)} for i in range(500)]
        selected,mask=compute_assignment_rows(rows,data,[str(i) for i in range(24)])
        self.assertEqual(len(selected),24);self.assertEqual(len(rows),499)
        self.assertEqual(sum(bool(r['model_patch']) for r in mask),24)
        with self.assertRaises(ValueError):compute_assignment_rows(rows,data)
        with self.assertRaises(ValueError):compute_assignment_rows(rows,data,['499'])

    def test_reference_subset_cannot_claim_full_score_or_assign_unselected_cases(self):
        rows=[{'instance_id':str(i),'model_patch':'diff'} for i in range(500)]
        plan=dict(selected_count=24,active_count=24,empty_patch_count=0,full_verified_accounting=False,
                  selected_instance_ids=[str(i) for i in range(24)],groups=[{'instance_ids':[str(i) for i in range(24)]}])
        self.assertEqual(len(validate_selection(plan,rows)),24)
        from core import summarize
        result=summarize(rows,{str(i):{'resolved':i<12} for i in range(24)},plan['selected_instance_ids'],full=False)
        self.assertEqual(result['subset_resolved_rate'],.5);self.assertIsNone(result['verified_score'])
        with self.assertRaises(ValueError):validate_selection(dict(plan,full_verified_accounting=True),rows)
        bad=copy.deepcopy(plan);bad['groups'][0]['instance_ids'].append('25')
        with self.assertRaises(ValueError):validate_selection(bad,rows)

    def fixture(self):
        rows=[{'instance_id':f'repo__case-{i}','model_patch':'diff' if i<344 else ''} for i in range(500)]
        shards=[]
        for i in range(11):
            shards.append(dict(index=i,verified=True,all_vms_terminated=True,
                use_before='2027-01-19T00:00:00+00:00',snapshot_image_id=f'im-{i}',
                images=[{'instance_id':r['instance_id'],'reference':'r','id':'id'} for r in rows[i::11]]))
        return rows,dict(complete=True,image_count=500,shards=shards),{str(i):'hash' for i in range(11)}

    def test_500_denominator_and_only_344_nonempty_instances_get_compute(self):
        rows,cat,hashes=self.fixture()
        groups=make_groups(rows,cat,hashes,datetime(2026,10,23,tzinfo=timezone.utc))
        self.assertEqual(sum(len(g['instance_ids']) for g in groups),344)
        self.assertEqual(len(groups),11)
        from core import summarize
        reports={r['instance_id']:{'resolved':i<100} for i,r in enumerate(rows[:344])}
        result=summarize(rows,reports,[r['instance_id'] for r in rows],full=True)
        self.assertEqual(result['verified_score'],.2)
        self.assertEqual(result['empty_patch_count'],156)
        del reports[rows[0]['instance_id']]
        self.assertIsNone(summarize(rows,reports,[r['instance_id'] for r in rows],full=True)['verified_score'])

    def test_duplicate_or_outside_ids_cannot_silently_use_wrong_snapshot(self):
        rows,cat,hashes=self.fixture();cat['shards'][0]['images'][0]=copy.deepcopy(cat['shards'][1]['images'][0])
        with self.assertRaises(ValueError):make_groups(rows,cat,hashes)
        rows,cat,hashes=self.fixture();rows[-1]['instance_id']='outside__case-999'
        with self.assertRaises(ValueError):make_groups(rows,cat,hashes)

    def test_missing_report_is_an_error_not_an_unresolved_model_result(self):
        self.assertEqual(validate_wave_reports({'a':{'resolved':False}},['a','b']),['b'])
        with self.assertRaises(ValueError):validate_wave_reports({'extra':{'resolved':True}},['a'])
        with self.assertRaises(ValueError):validate_wave_reports({'a':{'resolved':'false'}},['a'])

    def test_partial_vm_results_survive_a_later_worker_error_without_fabrication(self):
        reports={'old':{'resolved':False}};attempted=['old']
        merge_progress({'a':{'resolved':True}}, {'attempted_ids':['a','b']},['a','b','c'],reports,attempted)
        self.assertEqual(set(reports),{'old','a'})
        self.assertEqual(attempted,['old','a','b'])
        self.assertNotIn('b',reports)
        with self.assertRaises(ValueError):merge_progress({'a':{'resolved':True}},
            {'attempted_ids':['a']},['a','b','c'],reports,attempted)


if __name__=='__main__':unittest.main()

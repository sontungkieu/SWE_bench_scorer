from copy import deepcopy
from decimal import Decimal
import unittest
from n11_error_replay import TARGET,ESTIMATE,LIFETIME,settled_cost,validate_missing_target,RATE,replay_complete


class ErrorReplayTests(unittest.TestCase):
    def test_finished_worker_with_timeout_missing_report_is_not_complete(self):
        record=dict(worker_completed=True,all_vms_terminated=True)
        progress=dict(complete=True,attempted_ids=[TARGET])
        self.assertFalse(replay_complete(record,{},progress))
        # An official unresolved report is a complete evaluation result.
        self.assertTrue(replay_complete(record,{TARGET:{'resolved':False}},progress))
        self.assertTrue(replay_complete(record,{TARGET:{'resolved':True}},progress))

    def test_cleanup_errors_invalid_scope_and_nonboolean_results_are_not_complete(self):
        record=dict(worker_completed=True,all_vms_terminated=True)
        reports={TARGET:{'resolved':True}};progress=dict(complete=True,attempted_ids=[TARGET])
        self.assertFalse(replay_complete(dict(record,error='collection failed'),reports,progress))
        self.assertFalse(replay_complete(dict(record,all_vms_terminated=False),reports,progress))
        self.assertFalse(replay_complete(record,reports,dict(progress,attempted_ids=[])))
        self.assertFalse(replay_complete(record,{TARGET:{'resolved':1}},progress))

    def billing(self):
        a=dict(app_id='app',remaining_vms=[],rows=[{'object_id':'app','cost':'2'}],
               compute_egress_usd='2',checked_at='2026-10-23T05:00:00+00:00')
        b=deepcopy(a);b['checked_at']='2026-10-23T05:02:00+00:00';return a,b

    def test_complete_lifetime_and_egress_margin_fit_replay_envelope(self):
        self.assertLess(Decimal(LIFETIME)*RATE+Decimal('.03'),ESTIMATE)

    def test_incomplete_or_changing_billing_cannot_authorize_paid_replay(self):
        a,b=self.billing();self.assertEqual(settled_cost(a,b,'app'),2)
        b['rows'][0]['cost']='2.1';b['compute_egress_usd']='2.1'
        with self.assertRaises(ValueError):settled_cost(a,b,'app')

    def test_replay_is_rejected_when_it_exceeds_the_cumulative_eight_dollars(self):
        a,b=self.billing()
        for d in [a,b]:d['rows'][0]['cost']='5.4';d['compute_egress_usd']='5.4'
        with self.assertRaises(ValueError):settled_cost(a,b,'app')
        a,b=self.billing();b['remaining_vms']=['running']
        with self.assertRaises(ValueError):settled_cost(a,b,'app')

    def test_replaying_an_unresolved_result_is_forbidden(self):
        rows=[{'instance_id':TARGET,'model_patch':'diff'}]
        receipt={'execution_complete':True,'all_vms_terminated':True}
        validate_missing_target(rows,{},receipt,'Timeout error: 600 seconds exceeded.')
        with self.assertRaises(ValueError):validate_missing_target(rows,{TARGET:{'resolved':False}},
            receipt,'Timeout error: 600 seconds exceeded.')

    def test_no_replay_before_original_run_finishes_or_without_timeout_evidence(self):
        rows=[{'instance_id':TARGET,'model_patch':'diff'}]
        with self.assertRaises(ValueError):validate_missing_target(rows,{},
            {'execution_complete':False,'all_vms_terminated':False},'Timeout error: 600 seconds exceeded.')
        with self.assertRaises(ValueError):validate_missing_target(rows,{},
            {'execution_complete':True,'all_vms_terminated':True},'unknown failure')


if __name__=='__main__':unittest.main()

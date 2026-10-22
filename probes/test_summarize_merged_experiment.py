import unittest
from summarize_merged_experiment import cost_totals


class BillingTests(unittest.TestCase):
    def test_costs_include_stopped_attempt_and_missing_rows_never_qualify_budget(self):
        billing={'cost_by_role_usd':{'build':'0.7','merge':'0.4','weighted':'0.5'},'missing_billing_roles':[]}
        first={'compute_egress_usd':'0.08545511'}
        result=cost_totals(billing,first)
        self.assertEqual(result['combined_compute_egress_usd'],'1.68545511')
        self.assertTrue(result['within_approved_eight_usd'])
        billing['missing_billing_roles']=['weighted']
        self.assertFalse(cost_totals(billing,first)['within_approved_eight_usd'])

    def test_invalid_cost_and_real_budget_overrun_are_not_hidden(self):
        for value in ['NaN','Infinity','-1']:
            with self.assertRaises(ValueError):cost_totals({'cost_by_role_usd':{'build':value},'missing_billing_roles':[]},{'compute_egress_usd':'0'})
        result=cost_totals({'cost_by_role_usd':{'build':'7.95'},'missing_billing_roles':[]},{'compute_egress_usd':'0.1'})
        self.assertFalse(result['within_approved_eight_usd'])


if __name__=='__main__':unittest.main()

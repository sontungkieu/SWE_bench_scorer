import copy
import unittest
from reference_comparison import compare_reference


class ReferenceComparisonTests(unittest.TestCase):
    def fixture(self):
        data=[dict(instance_id=f'repo__case-{i}') for i in range(500)]
        rows=[dict(instance_id=r['instance_id'],model_name_or_path='reference',model_patch='diff' if i<478 else '') for i,r in enumerate(data[:499])]
        expected={r['instance_id']:i<277 for i,r in enumerate(data)}
        reports={r['instance_id']:{'resolved':i<277} for i,r in enumerate(rows[:478])}
        return data,rows,expected,reports,list(reports)

    def test_499_source_predictions_and_478_reports_keep_500_denominator(self):
        args=self.fixture();result=compare_reference(*args)
        self.assertEqual(result['verified_score'],.554)
        self.assertEqual(result['matching_cases'],500)
        self.assertTrue(result['all_instance_outcomes_agree'])
        self.assertFalse(result['source_coverage_complete'])
        self.assertEqual(result['counts']['empty_patch'],21)
        self.assertEqual(result['counts']['missing_source_prediction'],1)
        self.assertEqual(sum(r['official_report_present'] for r in result['cases']),478)

    def test_equal_aggregate_score_does_not_hide_different_instance_outcomes(self):
        data,rows,expected,reports,ids=self.fixture()
        reports[ids[0]]['resolved']=False;reports[ids[300]]['resolved']=True
        result=compare_reference(data,rows,expected,reports,ids)
        self.assertTrue(result['score_agrees']);self.assertFalse(result['all_instance_outcomes_agree'])
        self.assertEqual(result['matching_cases'],498)

    def test_missing_report_cannot_match_an_expected_negative_result(self):
        data,rows,expected,reports,ids=self.fixture();del reports[ids[300]]
        result=compare_reference(data,rows,expected,reports,ids)
        self.assertIsNone(result['verified_score']);self.assertFalse(result['score_complete'])
        self.assertIn(ids[300],result['mismatched_or_unknown_ids'])
        self.assertEqual(result['counts']['missing_official_report'],1)

    def test_execution_evidence_cannot_include_empty_or_absent_source_cases(self):
        data,rows,expected,reports,ids=self.fixture()
        with self.assertRaises(ValueError):compare_reference(data,rows,expected,reports,ids+[data[499]['instance_id']])
        reports[data[498]['instance_id']]={'resolved':False}
        with self.assertRaises(ValueError):compare_reference(data,rows,expected,reports,ids)

    def test_optional_reference_audit_cannot_excuse_a_different_outcome(self):
        args=self.fixture()
        audit={'cases':[dict(instance_id=args[4][0],current_official_resolved=False,
            reference_official_resolved=True,blocking_reasons=['grading_definition_mismatch'])]}
        with self.assertRaises(ValueError):compare_reference(*args,compatibility=audit)


if __name__=='__main__':unittest.main()

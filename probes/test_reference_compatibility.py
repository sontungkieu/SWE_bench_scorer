import copy
import unittest

from reference_compatibility import audit_reference, audit_bundle, session_versions, attach_compatibility, regrade_saved_log


class ReferenceCompatibilityTests(unittest.TestCase):
    def fixture(self):
        row=dict(instance_id='repo__repo-1',repo='repo/repo',version='1',base_commit='a'*40,
                 test_patch='official unchanged test patch',FAIL_TO_PASS=['fix'],PASS_TO_PASS=['keep'])
        report=dict(resolved=True,tests_status=dict(FAIL_TO_PASS=dict(success=['fix'],failure=[]),
                    PASS_TO_PASS=dict(success=['keep'],failure=[])))
        return dict(current=copy.deepcopy(row),reference=copy.deepcopy(row),
            current_report=copy.deepcopy(report),reference_report=copy.deepcopy(report),
            current_statuses=dict(fix='PASSED',keep='PASSED'),reference_statuses=dict(fix='PASSED',keep='PASSED'))

    def test_changed_official_required_ids_explain_a_raw_difference_without_rewriting_it(self):
        case=self.fixture();case['reference']['PASS_TO_PASS'].append('phantom')
        case['reference_report']['tests_status']['PASS_TO_PASS']['failure']=['phantom']
        case['reference_report']['resolved']=False
        before=copy.deepcopy(case);r=audit_reference(**case)
        self.assertEqual(case,before)
        self.assertIn('grading_definition_mismatch',r['blocking_reasons'])
        self.assertEqual(r['required_test_delta']['PASS_TO_PASS']['only_reference'],['phantom'])
        self.assertIs(r['current_official_resolved'],True)
        self.assertIs(r['reference_official_resolved'],False)
        self.assertFalse(r['raw_outcomes_match'])
        self.assertFalse(r['scientific_reproduction_qualified'])

    def test_actual_assertion_failure_is_not_excused_as_an_invalid_reference(self):
        case=self.fixture();case['reference_statuses']['fix']='FAILED'
        case['reference_report']['resolved']=False
        case['reference_report']['tests_status']['FAIL_TO_PASS']=dict(success=[],failure=['fix'])
        r=audit_reference(**case)
        self.assertEqual(r['blocking_reasons'],[])
        self.assertTrue(r['reference_execution']['valid'])
        self.assertEqual(r['comparison_status'],'unexplained_outcome_difference')

    def test_required_skip_is_not_the_same_as_a_ran_and_failed_test(self):
        case=self.fixture();case['reference_statuses']['keep']='SKIPPED'
        case['reference_report']['resolved']=False
        case['reference_report']['tests_status']['PASS_TO_PASS']=dict(success=[],failure=['keep'])
        r=audit_reference(**case)
        self.assertEqual(r['reference_execution']['skipped_required_ids'],['keep'])
        self.assertEqual(r['reference_execution']['failed_required_ids'],[])
        self.assertIn('reference_execution_not_valid_control',r['blocking_reasons'])

    def test_failed_collection_and_version_delta_are_both_visible(self):
        case=self.fixture();case['reference_statuses']={}
        case['reference_log']='platform linux -- Python 3.9.20, pytest-7.4.0\n___ ERROR collecting test_x.py ___\n'
        case['current_log']='platform linux -- Python 3.9.20, pytest-7.1.2\n'
        r=audit_reference(**case)
        self.assertIn('test_collection_error',r['reference_execution']['issues'])
        self.assertIn('observed_runtime_version_mismatch',r['blocking_reasons'])
        self.assertEqual(r['observed_runtime_version_delta'],{'pytest':dict(current=['7.1.2'],reference=['7.4.0'])})

    def test_even_equal_outcomes_do_not_qualify_different_tests(self):
        case=self.fixture();case['current']['test_patch']='different official test patch'
        r=audit_reference(**case)
        self.assertTrue(r['raw_outcomes_match'])
        self.assertEqual(r['comparison_status'],'comparison_blocked')
        self.assertIn('test_patch_sha256',r['other_grading_fields_differ'])

    def test_legacy_json_strings_and_list_order_have_same_grading_definition(self):
        case=self.fixture();case['reference']['FAIL_TO_PASS']='["fix"]'
        case['reference']['PASS_TO_PASS']='["keep"]'
        r=audit_reference(**case)
        self.assertTrue(r['grading_definition_equal'])
        self.assertEqual(r['comparison_status'],'observed_outcome_match')
        self.assertFalse(r['scientific_reproduction_qualified'])

    def test_missing_current_status_also_blocks_comparison(self):
        case=self.fixture();case['current_statuses']={}
        self.assertIn('current_execution_not_valid_control',audit_reference(**case)['blocking_reasons'])

    def test_mismatched_archived_report_definition_is_not_trusted(self):
        case=self.fixture();case['reference_report']['tests_status']['PASS_TO_PASS']['success']=['other']
        self.assertIn('reference_report_definition_mismatch',audit_reference(**case)['blocking_reasons'])

    def test_current_report_also_must_use_its_pinned_required_ids(self):
        case=self.fixture();case['current_report']['tests_status']['PASS_TO_PASS']['success']=['other']
        self.assertIn('current_report_definition_mismatch',audit_reference(**case)['blocking_reasons'])

    def test_attachment_preserves_outcomes_denominator_and_score_including_blocked_cases(self):
        case=self.fixture();case['reference_statuses']['keep']='SKIPPED'
        audit=dict(cases=[audit_reference(**case)])
        comparison=dict(dataset_count=500,verified_score=0.554,matching_cases=500,
            cases=[dict(instance_id='repo__repo-1',actual_resolved=True,expected_resolved=True)])
        reports={'repo__repo-1':case['current_report']}
        hashes={'repo__repo-1':audit['cases'][0]['current_log_sha256']}
        before=copy.deepcopy(comparison);result=attach_compatibility(comparison,audit,reports,hashes)
        self.assertEqual(comparison,before)
        self.assertEqual(result['verified_score'],.554)
        self.assertEqual(result['dataset_count'],500)
        self.assertEqual(result['cases'],comparison['cases'])
        self.assertFalse(result['reference_reproduction_qualified'])
        self.assertEqual(result['known_incompatible_reference_ids'],['repo__repo-1'])
        comparison['cases'][0]['actual_resolved']=False
        with self.assertRaises(ValueError):attach_compatibility(comparison,audit,reports,hashes)

    def test_same_verdict_from_a_different_log_or_report_cannot_reuse_an_audit(self):
        case=self.fixture();audit=dict(cases=[audit_reference(**case)])
        comparison=dict(cases=[dict(instance_id='repo__repo-1',actual_resolved=True,expected_resolved=True)])
        reports={'repo__repo-1':case['current_report']}
        with self.assertRaisesRegex(ValueError,'report/log provenance'):
            attach_compatibility(comparison,audit,reports,{'repo__repo-1':'0'*64})
        reports['repo__repo-1']=dict(case['current_report'],different_log=True)
        with self.assertRaisesRegex(ValueError,'report/log provenance'):
            attach_compatibility(comparison,audit,reports,{'repo__repo-1':audit['cases'][0]['current_log_sha256']})

    def test_pytest_temp_paths_are_not_runtime_versions_and_quoted_error_text_is_not_collection(self):
        self.assertEqual(session_versions("tmp_path='/tmp/pytest-of-root/pytest-0/test_x'"),{})
        case=self.fixture();case['reference_log']='E    assert message == "ERROR collecting test_x.py"'
        self.assertTrue(audit_reference(**case)['reference_execution']['valid'])

    def test_duplicate_required_ids_or_overlapping_report_buckets_fail_closed(self):
        case=self.fixture();case['current']['FAIL_TO_PASS']=['fix','fix']
        with self.assertRaises(ValueError):audit_reference(**case)
        case=self.fixture();case['reference_report']['tests_status']['PASS_TO_PASS']['failure']=['keep']
        with self.assertRaises(ValueError):audit_reference(**case)

    def test_bundle_requires_pinned_revisions_and_unique_cases(self):
        bundle=dict(provenance=dict(current_revision='a'*40,reference_revision='b'*40),cases=[self.fixture()])
        r=audit_bundle(bundle);self.assertEqual(r['case_count'],1)
        self.assertIsNone(r['new_benchmark_score'])
        bundle['cases']*=2
        with self.assertRaises(ValueError):audit_bundle(bundle)
        bundle['provenance']['current_revision']='main'
        with self.assertRaises(ValueError):audit_bundle(bundle)

    def test_log_regrade_refuses_changed_test_or_source_before_loading_runtime(self):
        case=self.fixture();case['reference']['test_patch']='different official tests'
        with self.assertRaisesRegex(ValueError,'different source/test bodies'):
            regrade_saved_log(case['current'],case['reference'],{},'not-read.log','0'*64)
        case=self.fixture();case['reference']['base_commit']='b'*40
        with self.assertRaisesRegex(ValueError,'different source/test bodies'):
            regrade_saved_log(case['current'],case['reference'],{},'not-read.log','0'*64)

    def test_requested_native_regrade_cannot_silently_fall_back_to_only_audit(self):
        bundle=dict(provenance=dict(current_revision='a'*40,reference_revision='b'*40),cases=[self.fixture()])
        with self.assertRaisesRegex(ValueError,'explicit prediction/log provenance'):
            audit_bundle(bundle,regrade_existing_logs=True)


if __name__=='__main__':unittest.main()

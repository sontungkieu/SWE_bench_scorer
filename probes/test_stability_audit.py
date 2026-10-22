import copy
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from stability_audit import assess, KNOWN_RISKS


class StabilityAuditTests(unittest.TestCase):
    def report(self, iid, passed):
        test = KNOWN_RISKS[iid]['required_test']
        return {'resolved': passed, 'tests_status': {'PASS_TO_PASS': {
            'success': [test] if passed else [], 'failure': [] if passed else [test]}}}

    def test_clean_rerun_cannot_clear_historical_instability_or_change_raw_report(self):
        iid = 'psf__requests-1921'
        reports = {iid: self.report(iid, True)}; original = copy.deepcopy(reports)
        audit = assess(reports, [iid])
        self.assertEqual(audit['status'], 'blocked_known_instability')
        self.assertEqual(audit['raw_resolved_count'], 1)
        self.assertIsNone(audit['research_score'])
        self.assertEqual(audit['known_risks'][0]['current_required_test'], 'pass')
        self.assertEqual(reports, original)
        self.assertEqual(audit['tests_excluded'], [])

    def test_failure_is_preserved_and_not_treated_as_a_free_rerun(self):
        iid = 'matplotlib__matplotlib-20859'
        audit = assess({iid: self.report(iid, False)}, [iid])
        self.assertEqual(audit['raw_resolved_count'], 0)
        self.assertEqual(audit['known_risks'][0]['current_required_test'], 'fail')

    def test_missing_or_malformed_reports_fail_closed(self):
        self.assertEqual(assess({}, ['a'])['status'], 'incomplete')
        self.assertEqual(assess({'a': {'resolved': 'true'}}, ['a'])['status'], 'incomplete')

    def test_unregistered_cases_are_unknown_rather_than_certified_stable(self):
        result = assess({'a': {'resolved': True}}, ['a'])
        self.assertEqual(result['status'], 'stability_not_assessed')
        self.assertFalse(result['scientific_stability_qualified'])

    def test_ambiguous_selection_and_contradictory_test_status_are_rejected(self):
        with self.assertRaises(ValueError): assess({}, ['a','a'])
        with self.assertRaises(ValueError): assess({'b': {'resolved': True}}, ['a'])
        iid='psf__requests-1921'; row=self.report(iid, True)
        row['tests_status']['PASS_TO_PASS']['failure'] = list(row['tests_status']['PASS_TO_PASS']['success'])
        with self.assertRaises(ValueError): assess({iid: row}, [iid])

    def test_successful_n11_run_still_returns_nonzero_when_scientifically_unqualified(self):
        import eval_modes
        iid = 'psf__requests-1921'
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            reports = json.dumps({iid: self.report(iid, True)}).encode()
            (out/'reports.json').write_bytes(reports)
            (out/'selection.json').write_text(json.dumps({'selected': [{'instance_id': iid}]}))
            with patch.object(sys, 'argv', ['eval_modes.py','run','--mode','optimizeN11',
                    '--out',directory,'--source',directory,'--baseline',directory]), patch.dict(
                        'os.environ', {'MODAL_PROFILE': 'phamvanvuhoan'}), patch(
                            'sharded_pilot.run', return_value=0), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(eval_modes.main(), 2)
            self.assertEqual((out/'reports.json').read_bytes(), reports)
            self.assertIsNone(json.loads((out/'scientific-validity.json').read_text())['research_score'])

    def test_billing_collection_success_cannot_be_mistaken_for_scientific_success(self):
        import eval_modes
        iid = 'matplotlib__matplotlib-20859'
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            (out/'reports.json').write_text(json.dumps({iid: self.report(iid, True)}))
            (out/'selection.json').write_text(json.dumps({'selected': [{'instance_id': iid}]}))
            with patch.object(sys, 'argv', ['eval_modes.py','collect','--mode','optimizeN11max',
                    '--out',directory]), patch('sharded_pilot.collect'), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(eval_modes.main(), 2)


if __name__ == '__main__': unittest.main()

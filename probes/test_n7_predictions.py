import unittest
from n7_predictions import validate_reports, LIFETIME, ESTIMATE


class RealPredictionsTests(unittest.TestCase):
    def setUp(self):
        self.rows=[{'instance_id':'a','model_patch':'diff'}, {'instance_id':'b','model_patch':''}]

    def test_empty_patch_has_no_synthetic_official_report(self):
        validate_reports(self.rows,{'a':{'resolved':False}})
        with self.assertRaises(ValueError):validate_reports(self.rows,{'a':{'resolved':False},'b':{'resolved':False}})

    def test_missing_nonempty_report_cannot_be_counted_as_model_failure(self):
        with self.assertRaises(ValueError):validate_reports(self.rows,{})

    def test_malformed_resolved_is_not_coerced_to_pass(self):
        with self.assertRaises(ValueError):validate_reports(self.rows,{'a':{'resolved':'true'}})

    def test_one_full_vm_lifetime_fits_trial_envelope(self):
        self.assertEqual(LIFETIME,900)
        self.assertLess(LIFETIME*(16*.00003942+64*.00000667),ESTIMATE)


if __name__=='__main__':unittest.main()

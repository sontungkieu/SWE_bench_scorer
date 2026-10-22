import unittest
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
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

    def test_direct_cli_from_outside_repository_reaches_dataset_validation(self):
        script=Path(__file__).with_name('n7_predictions.py').resolve()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pred=root/'preds.jsonl';data=root/'data.json'
            pred.write_text(json.dumps({'instance_id':'django__django-11099',
                'model_name_or_path':'test','model_patch':'diff'})+'\n')
            data.write_text('[]')
            result=subprocess.run([sys.executable,str(script),'prepare','--out',str(root/'out'),
                '--source',str(script.parent.parent),'--predictions',str(pred),'--dataset',str(data),
                '--expected-sha256',hashlib.sha256(pred.read_bytes()).hexdigest()],cwd=root,capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('Wrong pinned Verified dataset',result.stderr)
            self.assertNotIn('ModuleNotFoundError',result.stderr)


if __name__=='__main__':unittest.main()

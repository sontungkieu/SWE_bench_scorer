import json
from pathlib import Path
import tempfile
import unittest
from audit_matplotlib_timing import IID,TEST,historical,observed


class AuditTimingTests(unittest.TestCase):
    def test_historical_equality_and_single_changed_test(self):
        e=Path(__file__).resolve().parents[1]/'evidence'
        value=historical(e)
        self.assertEqual(value['only_changed_required_test'],TEST)
        self.assertTrue(value['historical_internal_clock_not_recorded'])
        self.assertEqual(value['resource_configuration']['cpu'],16)

    def test_clock_brackets_do_not_hide_threshold_ambiguity(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);p=out/'timing-output';p.mkdir()
            rows=[]
            for rep in range(2):
                rows.append({'repetition':rep,'observer_uses_real_clock':True,'test_or_clock_monkeypatched':False,
                    'file_hashes_before':{'a':'unchanged'},'file_hashes_after':{'a':'unchanged'},
                    'test_call_exception':'DID NOT WARN','calls':[
                        {'loc_used_default':True,'badness':1,'clock_condition_brackets':[{'lower_seconds':.7,'upper_seconds':.71}]},
                        {'loc_used_default':True,'badness':1,'clock_condition_brackets':[{'lower_seconds':.9999,'upper_seconds':1.0001}]}]})
            (p/'observations.json').write_text(json.dumps(rows))
            official=[{'repetition':rep,'reports':{IID:{'resolved':False,'tests_status':{'PASS_TO_PASS':{'failure':[TEST]}}}}} for rep in range(2)]
            (p/'official-repetitions.json').write_text(json.dumps(official))
            result=observed(out)
            calls=result['profiled_repetitions'][0]['calls']
            self.assertEqual(calls[0]['classification'],'below_one_second')
            self.assertEqual(calls[1]['classification'],'straddles_threshold')
            self.assertTrue(result['new_observations_do_not_recover_historical_unrecorded_clock_values'])


if __name__=='__main__':unittest.main()

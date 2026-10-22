import importlib
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import time

from matplotlib_timing_probe import LIFETIME,OLD_COST,RATE,profiled_script


class TimingProbeTests(unittest.TestCase):
    def test_profile_variant_keeps_every_original_line_except_pytest_invocation(self):
        original='set -uxo pipefail\ngit apply test.patch\npytest -rA test_legend.py\ngit checkout test_legend.py\n'
        changed=profiled_script(original).splitlines()
        self.assertEqual(changed[:2],original.splitlines()[:2])
        self.assertEqual(changed[3],original.splitlines()[3])
        self.assertIn('pytest -rA test_legend.py -p matplotlib_clock_observer',changed[2])
        self.assertNotIn('filterwarnings',changed[2])

    def test_ambiguous_test_commands_are_rejected(self):
        for text in ['echo no-tests\n','pytest a\npytest b\n']:
            with self.assertRaises(ValueError):profiled_script(text)

    def test_original_cost_plus_full_diagnostic_lifetime_stays_under_two_dollars(self):
        self.assertEqual(LIFETIME,780)
        self.assertLess(OLD_COST+RATE*LIFETIME+.02,2)

    def observer(self,clock):
        fake=SimpleNamespace(hookimpl=lambda **kw:lambda fn:fn)
        with patch.dict(sys.modules,{'pytest':fake}):
            module=importlib.import_module('matplotlib_clock_observer')
        token=object();return module.Observer(token,clock),SimpleNamespace(f_code=token,f_locals={})

    def test_clock_samples_are_bracketed_without_replacing_actual_clock(self):
        real=time.perf_counter;samples=iter([10.7,10.70001])
        observer,frame=self.observer(lambda:next(samples))
        observer(frame,'call',None)
        # Initial start_time native call must not be mistaken for the condition.
        observer(frame,'c_call',real);observer(frame,'c_return',real)
        frame.f_locals.update(start_time=10.,self=SimpleNamespace(_loc_used_default=True),badness=5000,candidates=[1]*9)
        observer(frame,'c_call',real);observer(frame,'c_return',real)
        observer(frame,'return',(1.,2.))
        row=observer.calls[0];bracket=row['clock_condition_brackets'][0]
        self.assertLess(bracket['lower_seconds'],bracket['upper_seconds'])
        self.assertLess(bracket['upper_seconds'],1)
        self.assertTrue(row['loc_used_default']);self.assertEqual(row['badness'],5000)
        self.assertIs(time.perf_counter,real)

    def test_early_return_is_distinguishable_from_fast_warning_condition(self):
        observer,frame=self.observer(lambda:0.)
        observer(frame,'call',None)
        frame.f_locals.update(self=SimpleNamespace(_loc_used_default=True),badness=0)
        observer(frame,'return',(1.,2.))
        self.assertEqual(observer.calls[0]['clock_condition_brackets'],[])
        self.assertEqual(observer.calls[0]['badness'],0)


if __name__=='__main__':unittest.main()

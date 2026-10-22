import unittest

from stability_diagnosis import ESTIMATE, IDS, LIFETIME, PRIOR_COST, RATE, observed_script


class StabilityProtocolTests(unittest.TestCase):
    def test_observer_keeps_the_full_test_command_and_all_other_script_lines(self):
        original = 'set -uxo pipefail\ngit apply test.patch\npytest -rA test_requests.py\ngit checkout original\n'
        lines = observed_script(original).splitlines()
        self.assertEqual(lines[:2], original.splitlines()[:2])
        self.assertEqual(lines[3], original.splitlines()[3])
        self.assertIn('pytest -rA test_requests.py -p stability_observer', lines[2])
        for forbidden in ['-k ', '--deselect', 'filterwarnings', '--ignore']:
            self.assertNotIn(forbidden, lines[2])

    def test_ambiguous_or_missing_commands_fail_before_allocation(self):
        for text in ['echo no tests\n', 'pytest a\npytest b\n']:
            with self.assertRaises(ValueError): observed_script(text)

    def test_allocation_envelope_includes_both_full_vm_lifetimes(self):
        self.assertEqual(len(IDS), 2)
        self.assertGreaterEqual(ESTIMATE, 2*LIFETIME*RATE+.05)
        self.assertLess(PRIOR_COST+ESTIMATE, 8)


if __name__ == '__main__': unittest.main()

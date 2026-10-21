"""Regression checks for the probe's outcome interpretation; no cloud imports."""
import unittest
from probes.upstream_modal_probe import control_passed, account_skipped_empty


class ControlOutcomeTests(unittest.TestCase):
    def test_zero_exit_with_infrastructure_error_is_failure(self):
        self.assertFalse(control_passed("wrong", False, "id", 0, False,
                                       {"resolved": False}, {"error_instances": 1,
                                                             "completed_instances": 0}))

    def test_missing_report_cannot_pass_negative_control(self):
        self.assertFalse(control_passed("wrong", False, "id", 0, False, None,
                                       {"error_instances": 0, "completed_instances": 1}))

    def test_nonempty_controls_need_correct_result(self):
        aggregate = {"error_instances": 0, "completed_instances": 1}
        self.assertTrue(control_passed("gold", True, "id", 0, False,
                                      {"resolved": True}, aggregate))
        self.assertTrue(control_passed("wrong", False, "id", 0, False,
                                      {"resolved": False}, aggregate))
        self.assertFalse(control_passed("gold", True, "id", 0, False,
                                       {"resolved": False}, aggregate))

    def test_empty_patch_is_officially_skipped_not_completed(self):
        aggregate = {"error_instances": 0, "completed_instances": 0,
                     "resolved_instances": 0, "empty_patch_ids": ["id"]}
        self.assertTrue(control_passed("empty", False, "id", 0, False, None, aggregate))
        self.assertFalse(control_passed("empty", False, "other", 0, False, None, aggregate))
        self.assertFalse(control_passed("empty", False, "id", 0, True, None, aggregate))

    def test_official_empty_early_return_has_explicit_probe_accounting(self):
        rows = [{"instance_id": "id", "model_patch": ""}]
        value = account_skipped_empty(rows, "warning\nNo instances to run.\n")
        self.assertTrue(control_passed("empty", False, "id", 0, False, None, value))
        self.assertFalse(value["official_report_written"])
        self.assertFalse(account_skipped_empty(rows, ""))
        self.assertFalse(account_skipped_empty(rows, "Traceback\nNo instances to run.\n"))
        self.assertFalse(account_skipped_empty([{"instance_id": "id", "model_patch": "nonempty"}],
                                              "No instances to run.\n"))


if __name__ == "__main__":
    unittest.main()

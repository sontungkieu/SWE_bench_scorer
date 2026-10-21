import json
import tempfile
import unittest
from pathlib import Path

from core import MOCK_ID, SCORER_VERSION, load_predictions, summarize


class InputAndScoreTests(unittest.TestCase):
    def test_version_matches_release_file(self):
        self.assertEqual(SCORER_VERSION, Path(__file__).with_name("VERSION").read_text().strip())

    def rows(self):
        return [{"instance_id": MOCK_ID, "model_name_or_path": "mock", "model_patch": "diff"},
                {"instance_id": "django__django-11133", "model_name_or_path": "mock", "model_patch": ""}]

    def test_empty_patch_remains_in_denominator(self):
        value = summarize(self.rows(), {MOCK_ID: {"resolved": True}}, [r["instance_id"] for r in self.rows()])
        self.assertTrue(value["complete"])
        self.assertEqual(value["subset_resolved_rate"], 0.5)
        self.assertIsNone(value["verified_score"])

    def test_missing_report_is_error_not_valid_score(self):
        value = summarize(self.rows(), {}, [r["instance_id"] for r in self.rows()])
        self.assertFalse(value["complete"])
        self.assertIsNone(value["subset_resolved_rate"])
        self.assertEqual(value["error_or_missing_report_count"], 1)

    def test_duplicate_rejected_before_cloud(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "preds.jsonl"
            path.write_text((json.dumps(self.rows()[0]) + "\n") * 2)
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                load_predictions(path)

    def test_full_score_uses_500(self):
        rows = [{"instance_id": f"django__django-{i}", "model_name_or_path": "mock", "model_patch": ""} for i in range(500)]
        rows[0]["model_patch"] = "diff"
        value = summarize(rows, {rows[0]["instance_id"]: {"resolved": True}}, [r["instance_id"] for r in rows], full=True)
        self.assertEqual(value["verified_score"], 1 / 500)
        self.assertTrue(value["complete"])

    def test_partial_cannot_claim_full_score(self):
        with self.assertRaises(ValueError):
            summarize(self.rows(), {}, [r["instance_id"] for r in self.rows()], full=True)


if __name__ == "__main__":
    unittest.main()

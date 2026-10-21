import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from probes.snapshot_pilot import validate_inputs
from probes.snapshot_worker import check_plan, verify_local_images


class FrozenPilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ("pilot.json", "preds.jsonl"):
            (self.root / name).write_bytes(b"synthetic input\n")
        self.selection = {"count": 24, "gold_control_only": True,
                          "pilot_sha256": hashlib.sha256(b"synthetic input\n").hexdigest(),
                          "predictions_sha256": hashlib.sha256(b"synthetic input\n").hexdigest()}
        self.save()
        for name in ("PILOT_SHA", "PREDS_SHA"):
            p = patch("probes.snapshot_pilot." + name, hashlib.sha256(b"synthetic input\n").hexdigest())
            p.start()
            self.addCleanup(p.stop)

    def save(self):
        (self.root / "selection.json").write_text(json.dumps(self.selection))

    def test_frozen_inputs_are_accepted(self):
        self.assertEqual(validate_inputs(self.root)["count"], 24)

    def test_full_500_expansion_is_rejected(self):
        self.selection["count"] = 500
        self.save()
        with self.assertRaises(ValueError):
            validate_inputs(self.root)

    def test_changed_predictions_are_rejected(self):
        (self.root / "preds.jsonl").write_bytes(b"changed input\n")
        with self.assertRaises(ValueError):
            validate_inputs(self.root)

    def test_forged_selection_manifest_is_rejected(self):
        self.selection["pilot_sha256"] = "0" * 64
        self.save()
        with self.assertRaisesRegex(ValueError, "exact previous"):
            validate_inputs(self.root)


class RestoredImageTests(unittest.TestCase):
    def plan(self):
        return [{"instance_id": str(i), "reference": "swebench/sweb.eval.x86_64.fixture-" + str(i),
                 "id": "sha256:" + "a" * 64} for i in range(24)]

    def test_restored_images_match_pinned_ids(self):
        records = verify_local_images(self.plan(), lambda _: {"Id": "sha256:" + "a" * 64})
        self.assertEqual(len(records), 24)

    def test_missing_image_fails_before_grading(self):
        with self.assertRaises(RuntimeError):
            verify_local_images(self.plan(), lambda _: None)

    def test_changed_image_fails_before_grading(self):
        with self.assertRaises(RuntimeError):
            verify_local_images(self.plan(), lambda _: {"Id": "sha256:" + "b" * 64})

    def test_duplicate_image_selection_fails(self):
        plan = self.plan()
        plan[-1]["instance_id"] = plan[0]["instance_id"]
        with self.assertRaises(ValueError):
            check_plan(plan)


if __name__ == "__main__":
    unittest.main()

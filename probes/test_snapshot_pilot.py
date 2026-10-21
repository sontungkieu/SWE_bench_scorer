import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from probes.snapshot_pilot import validate_inputs


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


if __name__ == "__main__":
    unittest.main()

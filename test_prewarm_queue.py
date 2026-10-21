import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import prewarm_queue


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.args = ["--profile", "test", "--registry-secret", "test-only", "--state", str(self.root),
                     "--run-prefix", "resume-test", "--max-attempts", "1", "--cooldown-seconds", "60"]

    def finish(self, name, complete=False, error=None):
        run = self.root / "runs" / name
        (run / "output").mkdir(parents=True)
        (run / "receipt.json").write_text(json.dumps({"finished_at": "now", "sandbox_terminated": True}))
        (run / "output/cache-summary.json").write_text(json.dumps({"complete": complete, "requested": 500, "cached": 500 if complete else 301,
            "storage_bytes": 100, "failures": [{"error": error}] if error else []}))

    def run_queue(self, extra=(), **patches):
        with patch.object(prewarm_queue, "acquire_lock", return_value=io.BytesIO()), \
                patch.object(prewarm_queue.subprocess, "Popen", **patches) as launch:
            code = prewarm_queue.main(self.args + list(extra))
        return code, launch, json.loads((self.root / "image-cache-status.json").read_text())

    def test_completed_adopted_run_never_launches_a_vm(self):
        self.finish("existing", complete=True)
        code, launch, status = self.run_queue(["--adopt-run", "existing"])
        self.assertEqual(code, 0)
        self.assertEqual(status["status"], "complete")
        launch.assert_not_called()

    def test_non_quota_error_is_not_retried(self):
        self.finish("existing", error="HTTP Error 401: Unauthorized")
        code, launch, status = self.run_queue(["--adopt-run", "existing"])
        self.assertEqual(code, 1)
        self.assertEqual(status["status"], "stopped")
        launch.assert_not_called()

    def test_insufficient_budget_blocks_before_allocating(self):
        code, launch, status = self.run_queue(["--budget-usd", "0.01"])
        self.assertEqual(status["status"], "budget_exhausted")
        launch.assert_not_called()

    def test_quota_wait_holds_no_modal_vm_and_then_resumes(self):
        self.finish("existing", error="HTTP Error 429: Too Many Requests")
        events = []
        def spawn(command, **kwargs):
            events.append("launch")
            self.finish(command[command.index("--run-id") + 1], complete=True)
            return Mock()
        with patch.object(prewarm_queue.time, "time", side_effect=[100, 100, 101, 161]), \
                patch.object(prewarm_queue.time, "sleep", side_effect=lambda _: events.append("cooldown")):
            code, launch, status = self.run_queue(["--adopt-run", "existing"], side_effect=spawn)
        self.assertEqual(code, 0)
        self.assertEqual(events, ["cooldown", "launch"])
        self.assertEqual(status["status"], "complete")
        self.assertEqual(launch.call_count, 1)

    def test_finite_work_boundary_resumes_without_quota_wait(self):
        self.finish("existing")
        def spawn(command, **kwargs):
            self.finish(command[command.index("--run-id") + 1], complete=True)
            return Mock()
        with patch.object(prewarm_queue.time, "sleep") as sleep:
            code, launch, status = self.run_queue(["--adopt-run", "existing"], side_effect=spawn)
        self.assertEqual(code, 0)
        self.assertEqual(status["status"], "complete")
        self.assertEqual(launch.call_count, 1)
        sleep.assert_not_called()

    def test_existing_run_id_blocks_duplicate_launch(self):
        self.finish("resume-test-1")
        code, launch, status = self.run_queue()
        self.assertEqual(code, 1)
        self.assertEqual(status["status"], "stopped")
        launch.assert_not_called()

    def test_unconfirmed_vm_termination_blocks_retry(self):
        self.finish("existing", error="HTTP Error 429: Too Many Requests")
        (self.root / "runs/existing/receipt.json").write_text(json.dumps({"finished_at": "now"}))
        code, launch, status = self.run_queue(["--adopt-run", "existing"])
        self.assertEqual(code, 1)
        self.assertEqual(status["status"], "stopped")
        launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()

import csv
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from core import MOCK_ID, parse_predictions
from webapp import Store, create_handler


def prediction():
    return {"instance_id": MOCK_ID, "model_name_or_path": "org/model",
            "model_patch": 'diff --git a/a.py b/a.py\n@@ -1 +1 @@\n-print("a,b")\n+print("fixed")\n'}


class FormatTests(unittest.TestCase):
    def test_csv_preserves_multiline_patch(self):
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=list(prediction()))
        writer.writeheader()
        writer.writerow(prediction())
        self.assertEqual(parse_predictions("\ufeff" + stream.getvalue(), ".csv"), [prediction()])

    def test_json_list_and_id_mapping(self):
        row = prediction()
        self.assertEqual(parse_predictions(json.dumps([row]), ".json"), [row])
        self.assertEqual(parse_predictions(json.dumps({MOCK_ID: row}), ".json"), [row])

    def test_json_mapping_id_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            parse_predictions(json.dumps({"wrong-key": prediction()}), ".json")

    def test_bad_csv_headers_rejected(self):
        with self.assertRaises(ValueError):
            parse_predictions("instance_id,model_patch\nx,y\n", ".csv")

    def test_malformed_multiline_csv_rejected(self):
        with self.assertRaises((ValueError, csv.Error)):
            parse_predictions('instance_id,model_name_or_path,model_patch\nx,y,"unclosed', ".csv")

    def test_unsafe_model_path_rejected(self):
        row = dict(prediction(), model_name_or_path="../other-run")
        with self.assertRaises(ValueError):
            parse_predictions(json.dumps(row) + "\n")


class WebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = {"profile": "test-profile", "allow_full": False,
                       "full_estimate_usd": 20, "monthly_hard_limit_usd": 29}
        self.store = Store(self.tmp.name, self.config)
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(self.store))
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:" + str(self.http.server_port)

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def post(self, path, body, origin=None):
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        request = urllib.request.Request(self.base + path, json.dumps(body).encode(), headers)
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    def payload(self):
        return {"filename": "preds.json", "content": json.dumps([prediction()]),
                "mode": "smoke", "request_id": "test-request-001"}

    def test_validate_has_no_job_side_effect(self):
        preview = self.post("/api/validate", self.payload())
        self.assertEqual(preview["count"], 1)
        self.assertEqual(self.store.listing(), [])

    def test_submit_normalizes_and_is_idempotent(self):
        first = self.post("/api/jobs", self.payload())
        second = self.post("/api/jobs", self.payload())
        self.assertEqual(first, second)
        self.assertEqual(len(self.store.listing()), 1)
        saved = Path(self.tmp.name) / "inputs" / (first["id"] + ".jsonl")
        self.assertEqual(parse_predictions(saved.read_text()), [prediction()])

    def test_cross_origin_cannot_start_paid_job(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.post("/api/jobs", self.payload(), origin="https://untrusted.example")
        self.assertEqual(error.exception.code, 403)
        self.assertEqual(self.store.listing(), [])

    def test_smoke_rejects_unknown_selected_id(self):
        body = dict(self.payload(), instance_ids=["django__django-999999"])
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.post("/api/jobs", body)
        self.assertEqual(error.exception.code, 400)

    def test_full_cannot_silently_grade_subset(self):
        with self.assertRaises(urllib.error.HTTPError):
            self.post("/api/jobs", dict(self.payload(), mode="full"))

    def test_interrupted_jobs_are_not_automatically_retried(self):
        job_id = self.store.submit(self.payload())
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (job_id,))
        reopened = Store(self.tmp.name, self.config)
        self.assertEqual(reopened.job(job_id)["status"], "interrupted")


if __name__ == "__main__":
    unittest.main()

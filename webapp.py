"""Private SSH-only upload dashboard. Python stdlib; grading runs on Modal."""
import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from core import MOCK_ID, SCORER_VERSION, parse_predictions
from image_cache import DEFAULT_VOLUME

HERE = Path(__file__).resolve().parent
MAX_BODY = 32 * 1024 * 1024


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, state, config):
        self.state = Path(state).resolve()
        self.config = config
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "inputs").mkdir(exist_ok=True)
        (self.state / "runs").mkdir(exist_ok=True)
        with self.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, request_id TEXT UNIQUE, filename TEXT, mode TEXT, model TEXT, count INTEGER, empty_count INTEGER, selected TEXT, status TEXT, created TEXT, started TEXT, finished TEXT, exit_code INTEGER)")
            # Never resubmit a paid job after a crash/restart.
            db.execute("UPDATE jobs SET status='interrupted', finished=? WHERE status='running'", (timestamp(),))

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.state / "jobs.sqlite", timeout=20)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            with conn:
                yield conn
        finally:
            conn.close()

    def validate(self, data):
        name = data.get("filename", "")
        text = data.get("content", "")
        if not isinstance(name, str) or not isinstance(text, str) or len(text.encode()) > 20 * 1024 * 1024:
            raise ValueError("File phải là UTF-8 và không quá 20 MiB")
        if len(name) > 200 or any(ord(c) < 32 for c in name):
            raise ValueError("Tên file không hợp lệ")
        rows = parse_predictions(text, Path(name).suffix)
        return rows

    @staticmethod
    def preview(rows):
        return {"count": len(rows), "empty_count": sum(not r["model_patch"].strip() for r in rows),
                "model": rows[0]["model_name_or_path"], "instance_ids": [r["instance_id"] for r in rows],
                "can_full": len(rows) == 500}

    def submit(self, data):
        mode = data.get("mode", "smoke")
        if mode not in {"mock", "smoke", "full"}:
            raise ValueError("Chế độ không hợp lệ")
        request_id = data.get("request_id")
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", request_id):
            raise ValueError("Thiếu request_id hợp lệ")
        rows = [] if mode == "mock" else self.validate(data)
        if mode == "full":
            if not self.config.get("allow_full", False):
                raise ValueError("Full chưa được bật trong cấu hình server")
            if len(rows) != 500:
                raise ValueError("Full Verified cần đủ 500 prediction")
        if mode == "mock":
            selected = [MOCK_ID]
        elif mode == "full":
            selected = [r["instance_id"] for r in rows]
        else:
            selected = data.get("instance_ids") or [r["instance_id"] for r in rows[:3]]
            if (not isinstance(selected, list) or not 1 <= len(selected) <= 3
                    or len(selected) != len(set(selected))
                    or any(iid not in {r["instance_id"] for r in rows} for iid in selected)):
                raise ValueError("Smoke cần 1-3 ID khác nhau có trong file")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT id FROM jobs WHERE request_id=?", (request_id,)).fetchone()
            if existing:
                return existing["id"]
            if db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0] >= 5:
                raise ValueError("Hàng đợi đã có 5 job; chờ job hiện tại hoàn tất")
            job = "web-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
            if rows:
                path = self.state / "inputs" / (job + ".jsonl")
                path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
            db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                job, request_id, data.get("filename", "Mock controls"), mode,
                rows[0]["model_name_or_path"] if rows else "MOCK-CONTROL",
                len(rows) if rows else 1, sum(not r["model_patch"].strip() for r in rows),
                json.dumps(selected), "queued", timestamp(), None, None, None))
            return job

    def job(self, job_id):
        if not re.fullmatch(r"web-[A-Za-z0-9_-]+", job_id):
            raise ValueError("Job ID không hợp lệ")
        with self.db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise FileNotFoundError("Không tìm thấy job")
        value = dict(row)
        value["selected"] = json.loads(value["selected"])
        output = self.state / "runs" / job_id
        value["summaries"] = {}
        for path in output.glob("output/*/summary.json"):
            value["summaries"][path.parent.name] = json.loads(path.read_text())
        checks = output / "output/mock-checks.json"
        if checks.exists():
            value["mock_checks"] = json.loads(checks.read_text())
        value["artifacts_ready"] = (output / "results.tar.gz").is_file()
        return value

    def listing(self):
        with self.db() as db:
            return [dict(r) for r in db.execute("SELECT id, filename, mode, model, count, status, created, finished FROM jobs ORDER BY created DESC LIMIT 50")]

    def worker(self, stop):
        while not stop.is_set():
            with self.db() as db:
                row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
                if row:
                    db.execute("UPDATE jobs SET status='running', started=? WHERE id=? AND status='queued'", (timestamp(), row["id"]))
            if row is None:
                stop.wait(1)
                continue
            job_id = row["id"]
            log_dir = self.state / "logs"
            log_dir.mkdir(exist_ok=True)
            command = [sys.executable, str(HERE / "launch.py"), row["mode"], "--profile", self.config["profile"],
                       "--run-id", job_id, "--workers", "1", "--lifetime", "86400" if row["mode"] == "full" else "1800",
                       "--estimate-usd", str(self.config["full_estimate_usd"] if row["mode"] == "full" else 0.5),
                       "--guard-script", str(HERE / "web_budget.py"), "--output-root", str(self.state / "runs")]
            command += ["--cache-volume", self.config.get("cache_volume", DEFAULT_VOLUME),
                        "--cache-limit-gib", str(self.config.get("cache_limit_gib", 256))]
            if row["mode"] != "mock":
                command += ["--predictions", str(self.state / "inputs" / (job_id + ".jsonl")),
                            "--instance-ids", *json.loads(row["selected"])]
            code = 1
            try:
                with (log_dir / (job_id + ".log")).open("w", encoding="utf-8") as log:
                    result = subprocess.run(command, cwd=HERE, stdout=log, stderr=subprocess.STDOUT)
                    code = result.returncode
            except Exception:
                # Keep public status useful without exposing environment/secrets.
                code = 1
            with self.db() as db:
                db.execute("UPDATE jobs SET status=?, finished=?, exit_code=? WHERE id=?",
                           ("completed" if code == 0 else "failed", timestamp(), code, job_id))


def create_handler(store):
    class Handler(BaseHTTPRequestHandler):
        server_version = "SWE-Scorer"

        def respond(self, status, body, content_type="application/json; charset=utf-8", extra=None):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            try:
                self.get()
            except FileNotFoundError:
                self.respond(404, {"error": "Không tìm thấy"})
            except (ValueError, OSError):
                self.respond(400, {"error": "Yêu cầu không hợp lệ"})

        def get(self):
            path = urlsplit(self.path).path
            if path in {"/", "/app.js", "/style.css"}:
                file = HERE / "web" / ({"/": "index.html"}.get(path, path[1:]))
                content_type = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}[file.suffix]
                return self.respond(200, file.read_bytes(), content_type)
            if path == "/api/config":
                return self.respond(200, {"version": SCORER_VERSION, "profile": store.config["profile"],
                    "allow_full": store.config.get("allow_full", False), "full_estimate_usd": store.config["full_estimate_usd"],
                    "monthly_hard_limit_usd": store.config["monthly_hard_limit_usd"], "max_upload_mib": 20,
                    "image_cache_enabled": store.config.get("cache_volume", DEFAULT_VOLUME) != "none"})
            if path == "/api/jobs":
                return self.respond(200, {"jobs": store.listing()})
            if path == "/api/cache":
                status_file = store.state / "image-cache-status.json"
                value = json.loads(status_file.read_text()) if status_file.exists() else {"status": "idle"}
                keys = {"status", "cached_count", "requested", "storage_bytes", "next_retry_at", "updated_at"}
                return self.respond(200, {key: val for key, val in value.items() if key in keys})
            if path.startswith("/api/fixtures/"):
                label = path.rsplit("/", 1)[-1]
                if label not in {"gold", "wrong", "empty"}:
                    raise FileNotFoundError()
                return self.respond(200, (HERE / "fixtures/mock" / label / "preds.jsonl").read_bytes(), "application/x-ndjson",
                                    {"Content-Disposition": f'attachment; filename="mock-{label}.jsonl"'})
            match = re.fullmatch(r"/api/jobs/([^/]+)(?:/(log|artifact))?", path)
            if match:
                job_id, kind = match.groups()
                job = store.job(job_id)
                if kind == "log":
                    file = store.state / "logs" / (job_id + ".log")
                    if not file.exists():
                        return self.respond(200, {"text": "Đang chờ worker…"})
                    with file.open("rb") as stream:
                        stream.seek(max(0, file.stat().st_size - 48000))
                        text = stream.read().decode("utf-8", errors="replace")
                    return self.respond(200, {"text": text})
                if kind == "artifact":
                    file = store.state / "runs" / job_id / "results.tar.gz"
                    with file.open("rb") as stream:
                        self.send_response(200)
                        self.send_header("Content-Type", "application/gzip")
                        self.send_header("Content-Length", str(file.stat().st_size))
                        self.send_header("Content-Disposition", f'attachment; filename="{job_id}.tar.gz"')
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("X-Content-Type-Options", "nosniff")
                        self.end_headers()
                        while chunk := stream.read(65536):
                            self.wfile.write(chunk)
                    return
                return self.respond(200, job)
            raise FileNotFoundError()

        def do_POST(self):
            # Private loopback service, same-origin JSON only. Browser forms and
            # cross-site fetches must not be able to start paid jobs.
            origin = self.headers.get("Origin")
            expected = "http://" + self.headers.get("Host", "")
            if origin and origin != expected:
                return self.respond(403, {"error": "Cross-origin request rejected"})
            if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                return self.respond(415, {"error": "JSON request required"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY:
                    return self.respond(413, {"error": "Upload quá lớn"})
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("JSON object required")
                if self.path == "/api/validate":
                    return self.respond(200, store.preview(store.validate(data)))
                if self.path == "/api/jobs":
                    return self.respond(202, {"id": store.submit(data)})
                return self.respond(404, {"error": "Không tìm thấy endpoint"})
            except (ValueError, TypeError, KeyError, csv.Error) as exc:
                self.respond(400, {"error": str(exc)[:300]})

        def log_message(self, fmt, *args):
            # No request bodies, headers, or credentials in access logs.
            print(timestamp(), self.command, urlsplit(self.path).path, flush=True)

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    config = json.loads(Path(os.environ["SWE_WEB_CONFIG"]).read_text())
    store = Store(os.environ["SWE_WEB_STATE"], config)
    stop = threading.Event()
    threading.Thread(target=store.worker, args=(stop,), daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), create_handler(store))
    print(f"SWE Scorer {SCORER_VERSION} on http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    finally:
        stop.set()
        server.server_close()


if __name__ == "__main__":
    main()

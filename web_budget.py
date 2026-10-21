"""Portable, fail-closed billing guard for the GCP-hosted personal web app."""
import argparse
import calendar
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["guard", "record-run"])
    parser.add_argument("--profile", required=True)
    parser.add_argument("--estimate", type=Decimal)
    parser.add_argument("--reserve", default="0")
    parser.add_argument("--run-id")
    parser.add_argument("--status")
    parser.add_argument("--label")
    parser.add_argument("--estimated-cost", type=Decimal)
    parser.add_argument("--run-dir")
    parser.add_argument("--app-id")
    args = parser.parse_args()
    config = json.loads(Path(os.environ["SWE_WEB_CONFIG"]).read_text())
    if args.profile != config["profile"]:
        raise ValueError("Profile differs from server configuration")
    state = Path(os.environ["SWE_WEB_STATE"])
    state.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(state / "budget.sqlite") as db:
        db.execute("CREATE TABLE IF NOT EXISTS events (run_id TEXT PRIMARY KEY, status TEXT, estimate TEXT, updated REAL)")
        if args.action == "record-run":
            if args.status not in {"planned", "running", "completed", "failed"} or not args.run_id:
                raise ValueError("Invalid ledger event")
            db.execute("INSERT INTO events VALUES (?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET status=excluded.status, estimate=excluded.estimate, updated=excluded.updated",
                       (args.run_id, args.status, str(args.estimated_cost), time.time()))
            return 0
        if args.estimate is None or args.estimate <= 0 or args.estimate > Decimal(str(config["max_estimate_usd"])):
            raise ValueError("Estimate outside configured per-job limit")
        now = datetime.now(timezone.utc)
        start = now.strftime("%Y-%m-01")
        end = (datetime(now.year + (now.month == 12), now.month % 12 + 1, 1, tzinfo=timezone.utc)).strftime("%Y-%m-%d")
        env = dict(os.environ, MODAL_PROFILE=args.profile)
        command = [sys.executable, "-m", "modal", "billing", "report", "--start", start, "--end", end, "--show-resources", "--json"]
        result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=90)
        if result.returncode:
            print(json.dumps({"status": "UNKNOWN", "reason": "Cannot refresh Modal billing; launch blocked"}))
            return 2
        rows = json.loads(result.stdout)
        if not isinstance(rows, list):
            raise ValueError("Unexpected billing response")
        reported = sum((Decimal(str(r.get("cost", "0"))) for r in rows), Decimal("0"))
        # Keep recent terminal estimates reserved for 10 minutes to allow for
        # delayed billing. Active runs remain reserved until explicitly terminal.
        reserved = sum((Decimal(row[1]) for row in db.execute("SELECT status, estimate, updated FROM events")
                        if row[0] in {"planned", "running"} or row[2] > time.time() - 600), Decimal("0"))
        limit = Decimal(str(config["monthly_hard_limit_usd"]))
        projected = reported + reserved + args.estimate + Decimal(args.reserve)
        status = "OK" if projected <= limit else "BLOCK"
        summary = {"status": status, "profile": args.profile, "period_start": start,
                   "period_end": end, "reported_usage": str(reported), "reserved_estimates": str(reserved),
                   "estimate": str(args.estimate), "hard_limit": str(limit),
                   "projected_usage": str(projected), "reported_at": now.isoformat(),
                   "note": "Calendar month safety limit; not remaining credits or a provider hard cap"}
        (state / "billing.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary))
        return 0 if status == "OK" else 3


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(json.dumps({"status": "UNKNOWN", "reason": type(exc).__name__ + ": billing guard failed closed"}))
        sys.exit(2)

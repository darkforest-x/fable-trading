"""Collect bounded real-service acceptance evidence, without OHLCV or secrets."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import urllib.request

from yoyo.monitor import SIGNAL_KIND, SIGNAL_PROTOCOL
from yoyo.monitor.policy import is_tv_start

ROOT = Path(__file__).resolve().parents[2]


def collect(label, output):
    def get(path):
        with urllib.request.urlopen("http://127.0.0.1:8766" + path, timeout=10) as response:
            return json.load(response)
    status, health, markets = get("/api/status"), get("/api/health"), get("/api/markets")
    runtime = Path.home() / "Library/Application Support/Fable/ImpulseMonitor/monitor.sqlite3"
    with sqlite3.connect("file:" + str(runtime) + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        receipts = [dict(r) for r in db.execute("SELECT event_id,status,attempts,updated_ms,message_id,error FROM outbox ORDER BY updated_ms")]
        bark_exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='bark_outbox'").fetchone()
        bark_receipts = [dict(r) for r in db.execute("SELECT event_id,status,attempts,updated_ms,server_timestamp,error FROM bark_outbox ORDER BY updated_ms")] if bark_exists else []
        duplicate_groups = db.execute("SELECT COUNT(*) FROM (SELECT COUNT(*) n FROM events GROUP BY json_extract(payload,'$.protocol'),symbol,timeframe,kind,side,close_ms HAVING n>1)").fetchone()[0]
        event_count = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        by_kind = [dict(r) for r in db.execute("SELECT kind,COUNT(*) count FROM events GROUP BY kind")]
        current = [json.loads(r[0]) for r in db.execute(
            "SELECT payload FROM events WHERE kind=? AND json_extract(payload,'$.protocol')=?",
            (SIGNAL_KIND, SIGNAL_PROTOCOL))]
        policy_row = db.execute("SELECT payload FROM meta WHERE key=?", ("notification_policy:" + SIGNAL_PROTOCOL,)).fetchone()
        policy = json.loads(policy_row[0]) if policy_row else None
        current_outbox = [dict(json.loads(r["payload"]), notification_status=r["status"])
                          for r in db.execute("SELECT e.payload,o.status FROM outbox o JOIN events e ON e.id=o.event_id WHERE json_extract(e.payload,'$.protocol')=?", (SIGNAL_PROTOCOL,))]
        bark_policy_row = db.execute("SELECT payload FROM meta WHERE key=?", ("notification_policy:bark:" + SIGNAL_PROTOCOL,)).fetchone()
        bark_policy = json.loads(bark_policy_row[0]) if bark_policy_row else None
        timeframe_policies = {r[0].rsplit(':', 1)[-1]: json.loads(r[1]) for r in db.execute(
            "SELECT key,payload FROM meta WHERE key LIKE ?", ('notification_timeframe:' + SIGNAL_PROTOCOL + ':%',))}
        bark_outbox = [dict(json.loads(r["payload"]), notification_status=r["status"])
                       for r in db.execute("SELECT e.payload,o.status FROM bark_outbox o JOIN events e ON e.id=o.event_id WHERE json_extract(e.payload,'$.protocol')=?", (SIGNAL_PROTOCOL,))] if bark_exists else []
    source = {}
    for file in sorted((ROOT / "yoyo/monitor").rglob("*")):
        if file.suffix in (".py", ".js", ".css", ".html", ".md"):
            source[str(file.relative_to(ROOT))] = hashlib.sha256(file.read_bytes()).hexdigest()
    payload = dict(label=label, captured_at=datetime.now(timezone.utc).isoformat(),
                   source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                   source_sha256=source, status=status, health=health,
                   markets={"total": markets["total"], "warming_up": sum(r.get("phase") == "loading" for r in markets["items"]),
                            "by_timeframe": dict(Counter(r['timeframe'] for r in markets['items'])),
                            "errors": [{"symbol": r["symbol"], "timeframe": r["timeframe"], "error": r["error"]} for r in markets["items"] if r.get("error")],
                            "stale": sum(bool(r.get("stale")) for r in markets["items"])},
                   journal={"event_count": event_count, "duplicate_identity_groups": duplicate_groups,
                            "telegram_receipts": receipts, "bark_receipts": bark_receipts, "by_kind": by_kind},
                   timeframe_audit={"policies": timeframe_policies,
                                    "signals_by_timeframe": dict(Counter(e['timeframe'] for e in current)),
                                    "invalid_telegram_ids": [e['id'] for e in current_outbox
                                        if e['timeframe'] not in ('1H', '4H') and (
                                            e['timeframe'] not in timeframe_policies or
                                            e['bar_close_ms'] <= timeframe_policies[e['timeframe']]['activated_ms'])],
                                    "invalid_bark_ids": [e['id'] for e in bark_outbox
                                        if e['timeframe'] not in ('1H', '4H') and (
                                            e['timeframe'] not in timeframe_policies or
                                            e['bar_close_ms'] <= timeframe_policies[e['timeframe']]['activated_ms'])]},
                   bark_audit={"policy": bark_policy, "current_outbox_count": len(bark_outbox),
                               "invalid_outbox_ids": [e["id"] for e in bark_outbox if not is_tv_start(e)],
                               "pre_activation_outbox_ids": [e["id"] for e in bark_outbox if not bark_policy or e["bar_close_ms"] <= bark_policy["activated_ms"]]},
                   signal_contract_audit={"protocol": SIGNAL_PROTOCOL, "policy": policy,
                                    "signal_count": len(current),
                                    "invalid_signal_ids": [e["id"] for e in current if not is_tv_start(e)],
                                    "current_outbox_count": len(current_outbox),
                                    "invalid_outbox_ids": [e["id"] for e in current_outbox if not is_tv_start(e)],
                                    "pre_activation_outbox_ids": [e["id"] for e in current_outbox if policy and e["bar_close_ms"] <= policy["activated_ms"]]})
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"label": label, "output": str(output), "scan": status["scan"], "events": event_count,
                      "telegram": status["telegram"], "duplicate_groups": duplicate_groups}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    collect(args.label, args.output)

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

from yoyo.monitor import (MODEL_KIND, MODEL_PROTOCOL, MODEL_PROFILE_ID,
                          MODEL_SHA256, MONITORED_TIMEFRAMES)
from yoyo.monitor.policy import is_model_signal

ROOT = Path(__file__).resolve().parents[2]


def _after_activation(event, policy):
    """Both original arrow and later confirmation must follow an explicit cutover."""
    original = event.get("indicator")
    activation = policy.get("activated_ms") if isinstance(policy, dict) else None
    return (type(activation) is int and activation >= 0 and isinstance(original, dict)
            and type(original.get("bar_close_ms")) is int
            and type(event.get("bar_close_ms")) is int
            and original["bar_close_ms"] > activation and event["bar_close_ms"] > activation)


def _stream_eligible(event, policies):
    timeframe = event.get("timeframe")
    return timeframe in MONITORED_TIMEFRAMES and _after_activation(event, policies.get(timeframe))


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
        media_exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='telegram_media'").fetchone()
        media = []
        if media_exists:
            for row in db.execute("SELECT m.*,o.status FROM telegram_media m LEFT JOIN outbox o ON o.event_id=m.event_id ORDER BY m.event_id"):
                photo = row['png']
                media.append(dict(event_id=row['event_id'], bytes=len(photo) if photo else 0,
                                  sha256=row['sha256'], error=row['error'], notification_status=row['status'],
                                  hash_valid=hashlib.sha256(photo).hexdigest() == row['sha256'] if photo else None))
        duplicate_groups = db.execute("""SELECT COUNT(*) FROM (
            SELECT COUNT(*) n FROM events GROUP BY json_extract(payload,'$.protocol'),symbol,timeframe,kind,side,close_ms,
            CASE WHEN kind=? THEN json_extract(payload,'$.source_event_id') ELSE '' END HAVING n>1)""",
                                      (MODEL_KIND,)).fetchone()[0]
        event_count = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        by_kind = [dict(r) for r in db.execute("SELECT kind,COUNT(*) count FROM events GROUP BY kind")]
        current = [json.loads(r[0]) for r in db.execute(
            "SELECT payload FROM events WHERE kind=? AND json_extract(payload,'$.protocol')=?",
            (MODEL_KIND, MODEL_PROTOCOL))]
        candidates_exist = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_candidates'").fetchone()
        candidate_counts = {r[0]: r[1] for r in db.execute("SELECT status,COUNT(*) FROM model_candidates GROUP BY status")} if candidates_exist else {}
        policy_row = db.execute("SELECT payload FROM meta WHERE key=?", ("notification_policy:" + MODEL_PROTOCOL,)).fetchone()
        policy = json.loads(policy_row[0]) if policy_row else None
        current_outbox = [dict(json.loads(r["payload"]), notification_status=r["status"])
                          for r in db.execute("SELECT e.payload,o.status FROM outbox o JOIN events e ON e.id=o.event_id WHERE json_extract(e.payload,'$.protocol')=?", (MODEL_PROTOCOL,))]
        bark_policy_row = db.execute("SELECT payload FROM meta WHERE key=?", ("notification_policy:bark:" + MODEL_PROTOCOL,)).fetchone()
        bark_policy = json.loads(bark_policy_row[0]) if bark_policy_row else None
        timeframe_policies = {r[0].rsplit(':', 1)[-1]: json.loads(r[1]) for r in db.execute(
            "SELECT key,payload FROM meta WHERE key LIKE ?", ('notification_timeframe:' + MODEL_PROTOCOL + ':%',))}
        bark_outbox = [dict(json.loads(r["payload"]), notification_status=r["status"])
                       for r in db.execute("SELECT e.payload,o.status FROM bark_outbox o JOIN events e ON e.id=o.event_id WHERE json_extract(e.payload,'$.protocol')=?", (MODEL_PROTOCOL,))] if bark_exists else []
    gate = status.get("runtime", {}).get("model_gate", {})
    operational = {key: gate.get(key) for key in ("status", "loaded", "queue_depth", "processed_endpoints",
                                                "last_checked_at_ms", "model_sha256", "profile_id")}
    operational["ready_for_confirmation"] = (gate.get("status") == "ready" and gate.get("loaded") is True
                                            and gate.get("model_sha256") == MODEL_SHA256
                                            and gate.get("profile_id") == MODEL_PROFILE_ID
                                            and candidate_counts.get("error", 0) == 0)
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
                            "telegram_receipts": receipts, "bark_receipts": bark_receipts, "by_kind": by_kind,
                            "telegram_media": media},
                   model_audit={"protocol": MODEL_PROTOCOL, "profile_id": MODEL_PROFILE_ID,
                                "candidate_status_counts": candidate_counts, "operational": operational},
                   timeframe_audit={"policies": timeframe_policies,
                                    "signals_by_timeframe": dict(Counter(e['timeframe'] for e in current)),
                                    "invalid_telegram_ids": [e['id'] for e in current_outbox
                                        if not _stream_eligible(e, timeframe_policies)],
                                    "invalid_bark_ids": [e['id'] for e in bark_outbox
                                        if not _stream_eligible(e, timeframe_policies)]},
                   bark_audit={"policy": bark_policy, "current_outbox_count": len(bark_outbox),
                               "invalid_outbox_ids": [e["id"] for e in bark_outbox if not is_model_signal(e)],
                               "pre_activation_outbox_ids": [e["id"] for e in bark_outbox if not _after_activation(e, bark_policy)]},
                   signal_contract_audit={"protocol": MODEL_PROTOCOL, "policy": policy,
                                    "signal_count": len(current),
                                    "invalid_signal_ids": [e["id"] for e in current if not is_model_signal(e)],
                                    "current_outbox_count": len(current_outbox),
                                    "invalid_outbox_ids": [e["id"] for e in current_outbox if not is_model_signal(e)],
                                    "pre_activation_outbox_ids": [e["id"] for e in current_outbox if not _after_activation(e, policy)]})
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

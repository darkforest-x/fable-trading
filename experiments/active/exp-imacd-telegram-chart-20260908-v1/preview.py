"""Build an explicitly unsent local preview from existing monitor API data.

Commit this builder before creating a durable artifact. Example:
  .venv/bin/python experiments/active/exp-imacd-telegram-chart-20260908-v1/preview.py \
      --event-id EXACT_ID --output /absolute/path/preview.png

Only GETs the loopback monitor. Never fetches OKX, loads credentials, sends
notifications or changes a database. Writes the requested PNG plus a sibling
JSON containing event/derived provenance, not raw OHLC or chart arrays.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from yoyo.monitor import TIMEFRAMES
from yoyo.monitor.policy import is_tv_start
from yoyo.monitor.snapshot import MAX_CANDLES, SIZE, render_signal
from yoyo.monitor.telegram import markup, message

BASE_URL = "http://127.0.0.1:8766"
EVENT_FIELDS = (
    "id", "symbol", "timeframe", "kind", "side", "price", "bar_open_ms", "bar_close_ms",
    "detected_at_ms", "protocol", "protocol_version", "confirmed", "ready",
    "source_kind", "tv_marker", "tv_profile", "tv_marker_visible", "tv_show_focus",
    "tv_show_marks", "focus_qualified_before", "near_zero_bars", "zero_bars",
    "md", "sb", "previous_md", "previous_sb", "focus_band", "focus_start_ms",
    "focus_qualified_ms", "dense", "htf_side", "htf_allowed", "is_monitor_signal",
    "notification_status", "bark_notification_status",
)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("preview_monitor_redirect_refused")


def _get(path):
    """Loopback-only GET; no environment proxy or redirect to remote services."""
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    request = Request(BASE_URL + path, headers={"Accept": "application/json"}, method="GET")
    with opener.open(request, timeout=15) as response:
        if response.headers.get_content_type() != "application/json":
            raise ValueError("preview_monitor_response_not_json")
        result = json.load(response)
    if not isinstance(result, dict):
        raise ValueError("preview_monitor_response_not_object")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Render one existing signal locally; nothing is sent.")
    parser.add_argument("--event-id", required=True, help="Exact id returned by the monitor signal API")
    parser.add_argument("--output", required=True, type=Path, help="New PNG path; sibling JSON is also written")
    args = parser.parse_args(argv)
    output = args.output.expanduser().resolve()
    metadata_path = output.with_suffix(".json")
    if output.suffix.lower() != ".png":
        parser.error("--output must end in .png")
    if output.exists() or metadata_path.exists():
        raise FileExistsError("preview_output_already_exists")

    listed = _get("/api/signals?limit=2000")
    matches = [item for item in listed.get("items", [])
               if isinstance(item, dict) and item.get("id") == args.event_id]
    if len(matches) != 1:
        raise ValueError("preview_exact_event_missing_or_duplicate_in_recent_2000")
    event = matches[0]
    if not is_tv_start(event):
        raise ValueError("preview_event_not_visible_tv_start")
    chart = _get("/api/chart?" + urlencode({"symbol": event["symbol"], "timeframe": event["timeframe"]}))
    if chart.get("symbol") != event["symbol"] or chart.get("timeframe") != event["timeframe"]:
        raise ValueError("preview_chart_identity_mismatch")
    candles = chart.get("candles")
    png = render_signal(event, candles)
    # The renderer has already validated these times and the exact final close.
    rendered = [row for row in candles if row["t"] <= event["bar_open_ms"]][-MAX_CANDLES:]
    last_open = int(rendered[-1]["t"])
    source_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    metadata = {
        "mode": "historical_local_preview_not_sent",
        "historical_local_preview_not_sent": True,
        "sent": False,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "event": {key: event[key] for key in EVENT_FIELDS if key in event},
        "caption": message(event),
        "button": markup(event)["inline_keyboard"][0][0],
        "source_commit": source_commit,
        "snapshot_sha256": hashlib.sha256((ROOT / "yoyo/monitor/snapshot.py").read_bytes()).hexdigest(),
        "preview_builder_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "png_sha256": hashlib.sha256(png).hexdigest(),
        "png_bytes": len(png),
        "png_dimensions": list(SIZE),
        "chart": {
            "source": "existing_loopback_monitor_chart",
            "received_candles": len(candles),
            "rendered_candles": len(rendered),
            "first_rendered_open_ms": int(rendered[0]["t"]),
            "last_rendered_open_ms": last_open,
            "last_rendered_close_ms": last_open + TIMEFRAMES[event["timeframe"]],
            "event_open_ms": event["bar_open_ms"],
            "event_close_ms": event["bar_close_ms"],
            "ends_at_event": last_open == event["bar_open_ms"],
            "raw_ohlc_saved": False,
        },
    }
    serialized = json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as file:
        file.write(png)
    with metadata_path.open("x", encoding="utf-8") as file:
        file.write(serialized)
    print(json.dumps({"mode": metadata["mode"], "png": str(output), "metadata": str(metadata_path),
                      "png_sha256": metadata["png_sha256"], "png_bytes": len(png)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

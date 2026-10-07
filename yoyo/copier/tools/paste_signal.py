#!/usr/bin/env python3
"""手动提交 KOL 喊单（无需 Telegram API）。

示例:
  PYTHONPATH=. python scripts/paste_signal.py "BTC 多 入场 65000 止损 64000"
  echo "喊单内容" | PYTHONPATH=. python scripts/paste_signal.py
"""

from __future__ import annotations

import json
import sys
import urllib.request

from yoyo.copier.config import app_config


def main() -> None:
    text = " ".join(sys.argv[1:]).strip() if len(sys.argv) > 1 else sys.stdin.read().strip()
    if not text:
        raise SystemExit("请提供喊单文本")

    url = f"http://{app_config.admin.host}:{app_config.admin.port}/api/messages/ingest"
    body = json.dumps(
        {"content": text, "author": "cli", "channel_id": "manual", "channel_name": "CLI录入"},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        print(resp.read().decode())


if __name__ == "__main__":
    main()

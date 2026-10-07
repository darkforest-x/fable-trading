#!/usr/bin/env python3
"""POST a signal text into Discord via Incoming Webhook.

Usage:
  export DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
  python -m src.bridge.webhook_post "飞扬合约策略\n具体产品: BTC\n..."
"""

from __future__ import annotations

import os
import sys

import httpx


def post_message(content: str, webhook_url: str | None = None) -> None:
    url = webhook_url or os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        raise SystemExit("Set DISCORD_WEBHOOK_URL")
    r = httpx.post(url, json={"content": content}, timeout=30)
    r.raise_for_status()
    print("Posted OK", r.status_code)


if __name__ == "__main__":
    text = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    post_message(text.strip())

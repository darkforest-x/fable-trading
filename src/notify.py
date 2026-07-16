"""Telegram notifier. Credentials live in data/tg_config.json (gitignored,
owner-created; agents never read or echo the token):

    {"bot_token": "...", "chat_id": "..."}

Falls back to env TG_BOT_TOKEN / TG_CHAT_ID. Missing config -> warn + no-op,
so pipelines never crash because of notification plumbing.
"""
from __future__ import annotations

import json
import mimetypes
import os
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parents[1] / "data" / "tg_config.json"


def _load() -> tuple[str, str] | None:
    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text())
        return cfg["bot_token"], str(cfg["chat_id"])
    tok, chat = os.environ.get("TG_BOT_TOKEN"), os.environ.get("TG_CHAT_ID")
    if tok and chat:
        return tok, chat
    return None


def send(text: str) -> bool:
    """Send a Telegram message (HTML parse mode). Returns delivery success."""
    creds = _load()
    if creds is None:
        print("tg_notify: no config (data/tg_config.json) -- message not sent")
        return False
    token, chat_id = creds
    data = urllib.parse.urlencode({
        "chat_id": chat_id, "text": text[:4000],
        "parse_mode": "HTML", "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read()).get("ok", False)
    except Exception as exc:  # noqa: BLE001 -- notification must never crash the caller
        print(f"tg_notify: send failed: {exc}")
        return False


def send_photo(image_path: Path, caption: str = "") -> bool:
    """Send a local image with optional HTML caption (Telegram limit ~1024)."""
    creds = _load()
    if creds is None:
        print("tg_notify: no config (data/tg_config.json) -- photo not sent")
        return False
    path = Path(image_path)
    if not path.exists():
        print(f"tg_notify: photo missing: {path}")
        return False
    token, chat_id = creds
    boundary = f"----fable{uuid.uuid4().hex}"
    caption_safe = (caption or "")[:1024]
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    file_bytes = path.read_bytes()

    def part(name: str, value: bytes, filename: str | None = None, content_type: str | None = None) -> bytes:
        disposition = f'Content-Disposition: form-data; name="{name}"'
        if filename:
            disposition += f'; filename="{filename}"'
        headers = [disposition.encode()]
        if content_type:
            headers.append(f"Content-Type: {content_type}".encode())
        return b"\r\n".join([f"--{boundary}".encode(), *headers, b"", value, b""])

    body = b"".join([
        part("chat_id", str(chat_id).encode()),
        part("caption", caption_safe.encode("utf-8")),
        part("parse_mode", b"HTML"),
        part("photo", file_bytes, filename=path.name, content_type=mime),
        f"--{boundary}--\r\n".encode(),
    ])
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendPhoto",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read()).get("ok", False)
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300] if hasattr(exc, "read") else b""
        print(f"tg_notify: sendPhoto failed: {exc} {detail!r}")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"tg_notify: sendPhoto failed: {exc}")
        return False


if __name__ == "__main__":
    import sys
    ok = send(sys.argv[1] if len(sys.argv) > 1 else "fable-trading 通知链路测试 ✅")
    raise SystemExit(0 if ok else 1)

#!/usr/bin/env python3
"""首次登录 Telegram，生成 session 文件。在项目根目录执行：PYTHONPATH=. python scripts/tg_login.py"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from yoyo.copier.config import ROOT, env  # noqa: E402


async def main() -> None:
    from telethon import TelegramClient

    if not env.telegram_api_id or not env.telegram_api_hash:
        print("请在 .env 配置 TELEGRAM_API_ID 与 TELEGRAM_API_HASH")
        sys.exit(1)

    session = env.telegram_session_path or str(ROOT / "data" / "telegram.session")
    Path(session).parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(session, int(env.telegram_api_id), env.telegram_api_hash)
    await client.start()
    me = await client.get_me()
    print(f"登录成功: {me.username or me.id}")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())

from __future__ import annotations

import logging
import threading

import uvicorn

from yoyo.copier.api.app import create_app
from yoyo.copier.config import app_config
from yoyo.copier.notifications.health_monitor import start_health_monitor
from yoyo.copier.notifications.position_monitor import start_position_monitor
from yoyo.copier.paper.engine import start_paper_engine
from yoyo.copier.store.sqlite import Database
from yoyo.copier.telegram_bot_listener import start_telegram_bot_listener


def _start_thread(target, *args, name: str) -> None:
    thread = threading.Thread(target=target, args=args, name=name, daemon=True)
    thread.start()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    db = Database()
    db.audit("system_start", "discord-okx-copier")
    _start_thread(start_telegram_bot_listener, db, name="telegram-bot")
    _start_thread(start_position_monitor, db, name="position-monitor")
    _start_thread(start_health_monitor, db, name="health-monitor")
    start_paper_engine(db)
    uvicorn.run(
        create_app(db),
        host=app_config.admin.host,
        port=app_config.admin.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()

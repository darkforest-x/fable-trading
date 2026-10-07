"""Background matcher: poll public prices for instruments with paper exposure."""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from yoyo.copier.paper.book import PaperBook
from yoyo.copier.paper.market import PublicMarket
from yoyo.copier.store.sqlite import Database

logger = logging.getLogger(__name__)
POLL_SECONDS = 5


def match_once(book: PaperBook, market: PublicMarket) -> list[dict]:
    """Closed 1m bars since the cursor first (they see the highs/lows between polls),
    then the live last price for the still-forming minute."""
    events = []
    for inst_id in book.active_inst_ids():
        quote = market.last(inst_id)
        if not quote:
            continue
        cursor = book.bar_cursor(inst_id)
        for bar in market.candles_1m(inst_id, quote[1]):
            if bar[0] > cursor:
                for event in book.process_bar(inst_id, *bar):
                    events.append({"inst_id": inst_id, **event})
        for event in book.process_mark(inst_id, quote[0], quote[1]):
            events.append({"inst_id": inst_id, **event})
    return events


def run(db: Database, market: Optional[PublicMarket] = None, stop: Optional[threading.Event] = None) -> None:
    book, market = PaperBook(db), market or PublicMarket()
    logger.info("模拟盘撮合已启动，间隔 %ss", POLL_SECONDS)
    while not (stop and stop.is_set()):
        try:
            for event in match_once(book, market):
                db.audit("paper_" + event["kind"], f"{event['inst_id']} @ {event['px']}", payload=event)
        except Exception as exc:  # keep matching; one bad poll must not stop the paper account
            logger.warning("模拟盘撮合失败: %s", exc)
        time.sleep(POLL_SECONDS)


def start_paper_engine(db: Database) -> threading.Thread:
    thread = threading.Thread(target=run, args=(db,), name="paper-engine", daemon=True)
    thread.start()
    return thread

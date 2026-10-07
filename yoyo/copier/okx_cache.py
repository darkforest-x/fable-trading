"""交易所数据短时缓存，避免管理台刷新反复请求交易所。"""

from __future__ import annotations

import time
from typing import Any

from yoyo.copier.exchange import TradingClient

_TTL_SEC = 60
_cache: dict[str, Any] = {"at": 0.0, "balance": None, "positions": None}


def _fresh() -> bool:
    return (time.time() - float(_cache["at"])) < _TTL_SEC


def get_balance_cached(okx: TradingClient, force: bool = False) -> dict[str, float]:
    if not force and _fresh() and _cache["balance"] is not None:
        return _cache["balance"]
    bal = okx.get_balance_summary()
    _cache["balance"] = bal
    _cache["at"] = time.time()
    return bal


def get_positions_cached(okx: TradingClient, force: bool = False) -> list[dict[str, Any]]:
    if not force and _fresh() and _cache["positions"] is not None:
        return _cache["positions"]
    pos = okx.get_positions()
    _cache["positions"] = pos
    _cache["at"] = time.time()
    return pos

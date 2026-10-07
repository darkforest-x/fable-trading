"""Skip the DeepSeek call for chat that carries no trading content (owner, 2026-10-07).

39% of the 505 historical signals ended as ``noise`` -- each one a paid, slow API
call. This runs only after every local format parser in ``DeepseekClient.analyze``
has declined the message, and only answers "obviously not a trade": no digit, no
ticker-like token and no trading word. Anything else still goes to the model.
Messages with images are never filtered (the levels may be in the picture).

Replayed against the stored history before enabling: see
tests/copier/test_prefilter.py and the replay numbers in the commit message.
"""
from __future__ import annotations

import re

TRADING_WORDS = (
    # English
    "long", "short", "buy", "sell", "entry", "entries", "stop", "sl", "tp", "target", "targets",
    "close", "closed", "closing", "profit", "loss", "breakeven", "break even", "be", "leverage",
    "limit", "market", "position", "trade", "scalp", "swing", "spot", "perp", "liquidat",
    "cancel", "invalid", "hit", "dca", "bid", "fill", "filled", "pump", "dump", "rr",
    # 中文
    "做多", "做空", "多单", "空单", "开多", "开空", "开仓", "平仓", "全平", "半仓", "减仓", "加仓", "补仓",
    "止损", "止盈", "保本", "进场", "入场", "出场", "离场", "挂单", "市价", "限价", "现价", "目标",
    "仓位", "杠杆", "爆仓", "撤单", "取消", "持有", "拿住", "出局", "回调", "突破", "支撑", "压力",
    "阻力", "埋伏", "接多", "接空", "空头", "多头", "看多", "看空", "反弹", "回踩", "点位",
    "盈利", "亏损", "盈亏", "赚", "亏", "单", "挂", "图片",
)
_WORD = re.compile(r"[a-z]+")
# Letter lookarounds instead of \b: "BTC不动" has no word boundary between C and 不.
_TICKER = re.compile(r"(?:[$#][A-Za-z]{2,12})|(?<![A-Za-z])[A-Z]{2,10}(?![a-z])")


def obviously_not_a_signal(content: str) -> bool:
    text = str(content or "").strip()
    if not text:
        return True
    if "[图片消息]" in text or "[附件消息]" in text:
        return False
    if any(ch.isdigit() for ch in text):
        return False
    if _TICKER.search(text):
        return False
    lowered = text.lower()
    words = set(_WORD.findall(lowered))
    for term in TRADING_WORDS:
        if term.isascii():
            if (" " in term and term in lowered) or term in words or (len(term) > 4 and term in lowered):
                return False
        elif term in text:
            return False
    return True

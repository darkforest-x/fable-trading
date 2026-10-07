from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class Intent(str, Enum):
    OPEN = "open"
    CLOSE = "close"
    PARTIAL_CLOSE = "partial_close"
    UPDATE_SL = "update_sl"
    UPDATE_TP = "update_tp"
    CANCEL = "cancel"
    HOLD = "hold"
    NOISE = "noise"


class IntentResult(BaseModel):
    should_act: bool = False
    intent: Literal[
        "open",
        "close",
        "partial_close",
        "update_sl",
        "update_tp",
        "cancel",
        "hold",
        "noise",
    ] = "noise"
    confidence: float = Field(ge=0, le=1, default=0.0)
    reject_reason: Optional[str] = None
    symbol: Optional[str] = None
    side: Optional[Literal["long", "short"]] = None
    entry_low: Optional[float] = None
    entry_high: Optional[float] = None
    entry_note: Optional[str] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    close_pct: Optional[float] = None
    leverage_hint: Optional[int] = None
    strategy_name: Optional[str] = None
    summary: str = ""

    @field_validator("symbol", mode="before")
    @classmethod
    def normalize_symbol(cls, v: object) -> str | None:
        if v is None:
            return None
        s = str(v).upper().strip()
        for suffix in ("-USDT-SWAP", "/USDT", "USDT", "-SWAP"):
            s = s.replace(suffix, "")
        return s or None

    @field_validator("confidence", mode="before")
    @classmethod
    def clamp_confidence(cls, v: object) -> float:
        try:
            f = float(v)
            return max(0.0, min(1.0, f))
        except (TypeError, ValueError):
            return 0.0

    @field_validator("leverage_hint", mode="before")
    @classmethod
    def normalize_leverage_hint(cls, v: object) -> int | None:
        if v is None or v == "":
            return None
        if isinstance(v, (int, float)):
            return int(v)
        import re

        nums = re.findall(r"\d+", str(v))
        if not nums:
            return None
        return int(nums[0])

    def inst_id(self) -> Optional[str]:
        if not self.symbol:
            return None
        return f"{self.symbol}-USDT-SWAP"

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from yoyo.copier.ai.schema import IntentResult
from yoyo.copier.config import app_config
from yoyo.copier.store.sqlite import Database

DEFAULT_NO_STOP_LOSS_CHANNELS = {"1131521990814089276"}


@dataclass
class RiskDecision:
    allowed: bool
    reason: str = ""


class RiskEngine:
    def __init__(self, db: Database) -> None:
        self.db = db

    def _get_risk(self) -> dict:
        """Merge yaml defaults with DB overrides."""
        base = app_config.risk.model_dump()
        overrides = self.db.get_all_settings()
        for key in base:
            if key in overrides:
                val = overrides[key]
                if isinstance(base[key], bool):
                    base[key] = val.lower() in ("true", "1", "yes")
                elif isinstance(base[key], int):
                    base[key] = int(val)
                elif isinstance(base[key], float):
                    base[key] = float(val)
                elif isinstance(base[key], list):
                    base[key] = json.loads(val) if val.startswith("[") else val.split(",")
        if overrides.get("kill_switch", "").lower() in ("true", "1"):
            base["kill_switch"] = True
        if overrides.get("dry_run", "").lower() in ("true", "1"):
            base["dry_run"] = True
        elif overrides.get("dry_run", "").lower() in ("false", "0"):
            base["dry_run"] = False
        return base

    def check(self, result: IntentResult, message_id: int | None = None) -> RiskDecision:
        risk = self._get_risk()

        if risk.get("kill_switch"):
            return RiskDecision(False, "kill_switch 已开启")

        min_conf = float(
            risk.get("min_confidence", app_config.deepseek.min_confidence)
        )
        if result.confidence < min_conf:
            return RiskDecision(False, f"confidence {result.confidence:.2f} < {min_conf}")

        if result.intent != "open":
            return RiskDecision(False, f"intent {result.intent} 暂不执行")

        if not result.symbol:
            return RiskDecision(False, "缺少币种")

        whitelist = risk.get("symbols_whitelist") or []
        if whitelist and result.symbol.upper() not in [s.upper() for s in whitelist]:
            return RiskDecision(False, f"{result.symbol} 不在白名单")

        if risk.get("require_stop_loss") and not result.stop_loss and not self._allows_no_stop_loss(result, message_id):
            return RiskDecision(False, "缺少止损")

        if not result.side:
            return RiskDecision(False, "缺少方向")

        if "spot" in str(result.entry_note or "").lower():
            return RiskDecision(False, "现货信号不能使用永续合约账户执行")

        if "candle closures" in str(result.entry_note or "").lower():
            return RiskDecision(False, "条件止损需要等待 K 线收盘确认，暂不支持自动执行")

        entry_prices = [price for price in (result.entry_low, result.entry_high) if price is not None]
        if entry_prices and result.stop_loss is not None:
            entry = sum(entry_prices) / len(entry_prices)
            if result.side == "long" and result.stop_loss >= entry:
                return RiskDecision(False, f"多单止损 {result.stop_loss} 必须低于入场价 {entry:g}")
            if result.side == "short" and result.stop_loss <= entry:
                return RiskDecision(False, f"空单止损 {result.stop_loss} 必须高于入场价 {entry:g}")

        max_pos = int(risk.get("max_open_positions", 3))
        channel_id = ""
        if message_id is not None:
            channel_id = str((self.db.get_message(message_id) or {}).get("channel_id") or "")
        open_count = self.db.count_open_orders_today(channel_id or None)
        if open_count >= max_pos:
            return RiskDecision(False, f"已达最大持仓数 {max_pos}")

        max_per = int(risk.get("max_position_per_symbol", 1))
        sym_count = self.db.count_positions_by_symbol(result.symbol, channel_id or None)
        if sym_count >= max_per:
            return RiskDecision(False, f"{result.symbol} 已有持仓")

        return RiskDecision(True)

    def _allows_no_stop_loss(self, result: IntentResult, message_id: int | None) -> bool:
        if "allow_no_stop_loss" not in str(result.entry_note or "").lower():
            return False
        if message_id is None:
            return False
        message = self.db.get_message(message_id) or {}
        channel_id = str(message.get("channel_id") or "")
        if not channel_id:
            return False
        return channel_id in self._no_stop_loss_channels()

    def _no_stop_loss_channels(self) -> set[str]:
        raw = self.db.get_setting("allow_no_stop_loss_channels")
        if not raw:
            return set(DEFAULT_NO_STOP_LOSS_CHANNELS)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw.split(",")
        if not isinstance(parsed, list):
            return set(DEFAULT_NO_STOP_LOSS_CHANNELS)
        channels = {str(item).strip() for item in parsed if str(item).strip()}
        return channels or set(DEFAULT_NO_STOP_LOSS_CHANNELS)

    def is_dry_run(self) -> bool:
        risk = self._get_risk()
        return bool(risk.get("dry_run", True))

    def get_position_pct(self) -> float:
        risk = self._get_risk()
        return min(max(float(risk.get("position_pct", 0.01)), 0.0), 1.0)

    def get_max_entry_deviation_pct(self) -> float:
        risk = self._get_risk()
        return min(max(float(risk.get("max_entry_deviation_pct", 0.2)), 0.0), 1.0)

    def get_max_leverage(self) -> int:
        risk = self._get_risk()
        return int(risk.get("max_leverage", 5))

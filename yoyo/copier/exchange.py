from __future__ import annotations

import json
import os
from typing import Any, Protocol

from yoyo.copier.config import env
from yoyo.copier.risk.engine import RiskEngine
from yoyo.copier.runtime_config import get_okx
from yoyo.copier.store.sqlite import Database

CHANNEL_EXCHANGE_MAP_KEY = "exchange_channel_map"
CHANNEL_LEVERAGE_MAP_KEY = "channel_leverage_map"
PAPER = "paper"  # local paper ledger (yoyo/copier/paper); never reaches an exchange
SUPPORTED_EXCHANGES = {"okx", "gate", "binance", PAPER}


class TradingClient(Protocol):
    def health_check(self) -> dict[str, Any]: ...

    def get_balance_usdt(self) -> float: ...

    def get_balance_summary(self) -> dict[str, float]: ...

    def get_positions(self) -> list[dict[str, Any]]: ...

    def get_open_orders(self) -> list[dict[str, Any]]: ...

    def get_open_triggers(self) -> list[dict[str, Any]]: ...

    def close_position(self, inst_id: str, close_pct: float = 100.0) -> dict[str, Any]: ...

    def cancel_open_orders(self, inst_id: str) -> dict[str, Any]: ...

    def update_stop_loss(self, inst_id: str, trigger_px: float) -> dict[str, Any]: ...

    def update_take_profit(self, inst_id: str, trigger_px: float) -> dict[str, Any]: ...

    def cancel_trigger_order(self, order_id: str) -> dict[str, Any]: ...


class TradingExecutor(Protocol):
    def open_with_sl_tp(self, result: Any, message_id: int, signal_id: int) -> dict[str, Any]: ...


def should_use_market_entry(result: Any, mark: float, source_text: str = "") -> bool:
    note = str(getattr(result, "entry_note", "") or "").lower()
    text = f"{source_text or ''} {note}".lower()
    if "market" in text or "市价" in text or "现价" in text:
        return True
    if "limit" in text or "限价" in text:
        return False

    low = getattr(result, "entry_low", None)
    high = getattr(result, "entry_high", None)
    if low is None or high is None or mark <= 0:
        return False
    entry_low = min(float(low), float(high))
    entry_high = max(float(low), float(high))
    return entry_low <= float(mark) <= entry_high


def normalize_exchange(value: Any, fallback: str = "okx") -> str:
    exchange = str(value or "").strip().lower()
    if exchange in SUPPORTED_EXCHANGES or exchange.startswith("gate_"):
        return exchange
    return fallback


def base_exchange(exchange: str) -> str:
    selected = normalize_exchange(exchange)
    if selected.startswith("gate_"):
        return "gate"
    return selected


def gate_account_name(exchange: str) -> str:
    selected = normalize_exchange(exchange)
    return selected[5:] if selected.startswith("gate_") else "default"


def gate_account_label(account: str) -> str:
    labels = {
        "default": "Gate",
        "mia": "Gate Arthur",
        "feiyang": "Gate 飞扬",
    }
    return labels.get(account, f"Gate {account.upper()}")


def _env_name(account: str, key: str) -> str:
    return f"GATE_{account.upper()}_{key}"


def gate_credentials(exchange: str) -> dict[str, Any]:
    account = gate_account_name(exchange)
    if account == "default":
        return {
            "api_key": env.gate_api_key,
            "secret_key": env.gate_secret_key,
            "settle": env.gate_settle,
            "testnet": env.gate_testnet,
            "base_url": env.gate_base_url,
        }
    return {
        "api_key": os.getenv(_env_name(account, "API_KEY"), ""),
        "secret_key": os.getenv(_env_name(account, "SECRET_KEY"), ""),
        "settle": os.getenv(_env_name(account, "SETTLE"), env.gate_settle),
        "testnet": os.getenv(_env_name(account, "TESTNET"), str(env.gate_testnet)).lower()
        in {"1", "true", "yes", "on"},
        "base_url": os.getenv(_env_name(account, "BASE_URL"), env.gate_base_url),
    }


def get_channel_exchange_map(db: Database) -> dict[str, str]:
    raw = db.get_setting(CHANNEL_EXCHANGE_MAP_KEY)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(channel_id): normalize_exchange(exchange)
        for channel_id, exchange in data.items()
        if base_exchange(normalize_exchange(exchange)) in SUPPORTED_EXCHANGES
    }


def set_channel_exchange_map(db: Database, mapping: dict[str, str]) -> dict[str, str]:
    clean = {
        str(channel_id): normalize_exchange(exchange)
        for channel_id, exchange in mapping.items()
        if str(channel_id).strip()
    }
    db.set_setting(CHANNEL_EXCHANGE_MAP_KEY, json.dumps(clean, ensure_ascii=False, sort_keys=True))
    db.audit("exchange_channel_map", json.dumps(clean, ensure_ascii=False))
    return clean


def get_channel_leverage_map(db: Database) -> dict[str, int]:
    raw = db.get_setting(CHANNEL_LEVERAGE_MAP_KEY)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    result: dict[str, int] = {}
    for channel_id, leverage in data.items():
        try:
            result[str(channel_id)] = max(1, int(leverage))
        except (TypeError, ValueError):
            continue
    return result


def set_channel_leverage_map(db: Database, mapping: dict[str, int]) -> dict[str, int]:
    clean = {str(channel_id): max(1, int(leverage)) for channel_id, leverage in mapping.items()}
    db.set_setting(CHANNEL_LEVERAGE_MAP_KEY, json.dumps(clean, ensure_ascii=False, sort_keys=True))
    db.audit("channel_leverage_map", json.dumps(clean, ensure_ascii=False))
    return clean


def selected_leverage(
    db: Database,
    channel_id: str | None = None,
    *,
    fallback: int | None = None,
) -> int:
    if channel_id:
        configured = get_channel_leverage_map(db).get(str(channel_id))
        if configured:
            return configured
    return max(1, int(fallback or get_okx(db).get("default_leverage") or 1))


def selected_exchange(db: Database | None = None, channel_id: str | None = None) -> str:
    if db is not None and channel_id:
        channel_exchange = get_channel_exchange_map(db).get(str(channel_id))
        if channel_exchange:
            return channel_exchange
    if db is not None:
        return normalize_exchange(get_okx(db).get("exchange"), "okx")
    return normalize_exchange(env.exchange, "okx")


def exchange_label(
    db: Database | None = None,
    channel_id: str | None = None,
    exchange: str | None = None,
) -> str:
    selected = normalize_exchange(exchange) if exchange else selected_exchange(db, channel_id)
    if base_exchange(selected) == "gate":
        account = gate_account_name(selected)
        return gate_account_label(account)
    if selected == "binance":
        return "Binance"
    if selected == PAPER:
        return "模拟盘"
    return "OKX"


def exchange_mode_label(
    db: Database | None = None,
    channel_id: str | None = None,
    exchange: str | None = None,
) -> str:
    selected = normalize_exchange(exchange) if exchange else selected_exchange(db, channel_id)
    if base_exchange(selected) == "gate":
        account = gate_account_name(selected)
        creds = gate_credentials(selected)
        prefix = gate_account_label(account)
        return f"{prefix} Testnet" if creds["testnet"] else f"{prefix} Live"
    if selected == "binance":
        return "Binance Testnet" if env.binance_testnet else "Binance Live"
    if selected == PAPER:
        return "模拟盘（本地账本）"
    return "OKX Demo" if env.okx_demo else "OKX Live"


def exchange_configured(
    db: Database | None = None,
    channel_id: str | None = None,
    exchange: str | None = None,
) -> bool:
    selected = normalize_exchange(exchange) if exchange else selected_exchange(db, channel_id)
    if base_exchange(selected) == "gate":
        creds = gate_credentials(selected)
        return bool(creds["api_key"] and creds["secret_key"])
    if selected == "binance":
        return bool(env.binance_api_key and env.binance_secret_key)
    if selected == PAPER:
        return True
    return bool(env.okx_api_key and env.okx_secret_key)


def create_exchange_client(
    db: Database | None = None,
    channel_id: str | None = None,
    exchange: str | None = None,
) -> TradingClient:
    selected = normalize_exchange(exchange) if exchange else selected_exchange(db, channel_id)
    if base_exchange(selected) == "gate":
        from yoyo.copier.gate.client import GateClient

        return GateClient(**gate_credentials(selected))
    if selected == "binance":
        from yoyo.copier.binance.client import BinanceClient

        return BinanceClient()
    if selected == PAPER:
        from yoyo.copier.paper.client import PaperClient

        return PaperClient(db or Database(), account=str(channel_id) if channel_id else None)
    from yoyo.copier.okx.client import OkxClient

    return OkxClient()


def create_exchange_executor(
    db: Database,
    risk: RiskEngine,
    channel_id: str | None = None,
    exchange: str | None = None,
) -> TradingExecutor:
    selected = normalize_exchange(exchange) if exchange else selected_exchange(db, channel_id)
    if base_exchange(selected) == "gate":
        from yoyo.copier.gate.client import GateClient
        from yoyo.copier.gate.executor import GateExecutor

        return GateExecutor(db, risk, GateClient(**gate_credentials(selected)))
    if selected == "binance":
        from yoyo.copier.binance.client import BinanceClient
        from yoyo.copier.binance.executor import BinanceExecutor

        return BinanceExecutor(db, risk, BinanceClient())
    if selected == PAPER:
        from yoyo.copier.paper.client import PaperExecutor

        return PaperExecutor(db, risk, account=str(channel_id or "unrouted"))
    from yoyo.copier.okx.client import OkxClient
    from yoyo.copier.okx.executor import OkxExecutor

    return OkxExecutor(db, risk, OkxClient())


def routed_exchange_names(db: Database) -> list[str]:
    names = {selected_exchange(db), *get_channel_exchange_map(db).values()}
    return sorted(name for name in names if base_exchange(name) in SUPPORTED_EXCHANGES)


def configured_routed_exchange_names(db: Database) -> list[str]:
    return [name for name in routed_exchange_names(db) if exchange_configured(exchange=name)]

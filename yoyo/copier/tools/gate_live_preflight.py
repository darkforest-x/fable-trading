from __future__ import annotations

import json

from yoyo.copier.config import env
from yoyo.copier.exchange import get_channel_exchange_map
from yoyo.copier.gate.client import GateClient, gate_contract
from yoyo.copier.monitor_switches import feature_enabled
from yoyo.copier.runtime_config import get_full_config
from yoyo.copier.store.sqlite import Database


def main() -> int:
    db = Database()
    cfg = get_full_config(db)
    risk = cfg["risk"]
    trading = cfg["okx"]
    channel_exchange_map = get_channel_exchange_map(db)
    gate_routed = trading.get("exchange") == "gate" or "gate" in channel_exchange_map.values()
    gate = GateClient()

    result: dict[str, object] = {
        "exchange": trading.get("exchange"),
        "channel_exchange_map": channel_exchange_map,
        "mode": "testnet" if env.gate_testnet else "live",
        "base_url": f"{gate.host}{gate.prefix}",
        "settle": gate.settle,
        "keys": {
            "api_key": bool(env.gate_api_key),
            "secret_key": bool(env.gate_secret_key),
        },
        "risk": {
            "dry_run": risk["dry_run"],
            "kill_switch": risk["kill_switch"],
            "position_pct": risk["position_pct"],
            "max_leverage": risk["max_leverage"],
            "max_open_positions": risk["max_open_positions"],
            "max_position_per_symbol": risk["max_position_per_symbol"],
            "require_stop_loss": risk["require_stop_loss"],
            "symbols_whitelist": risk["symbols_whitelist"],
        },
        "features": {
            "ingest_messages": feature_enabled(db, "ingest_messages"),
            "ai_parse": feature_enabled(db, "ai_parse"),
            "risk_check": feature_enabled(db, "risk_check"),
            "okx_execute": feature_enabled(db, "okx_execute"),
        },
    }

    balance = gate.get_balance_summary()
    positions = gate.get_positions()
    result["balance"] = balance
    result["positions_count"] = len(positions)
    result["positions"] = [
        {
            "instId": p.get("instId"),
            "contract": p.get("contract"),
            "posSide": p.get("posSide"),
            "pos": p.get("pos"),
            "avgPx": p.get("avgPx"),
            "upl": p.get("upl"),
            "leverage": p.get("leverage"),
        }
        for p in positions
    ]

    instruments: list[dict[str, object]] = []
    for symbol in list(risk["symbols_whitelist"])[:20]:
        inst_id = f"{str(symbol).upper()}-USDT-SWAP"
        inst = gate.get_instrument(inst_id)
        ticker = gate.get_ticker(inst_id) if inst else 0.0
        instruments.append(
            {
                "inst_id": inst_id,
                "contract": gate_contract(inst_id, gate.settle),
                "available": bool(inst),
                "mark_or_last": ticker,
                "quanto_multiplier": inst.get("quanto_multiplier") if inst else None,
                "order_size_min": inst.get("order_size_min") if inst else None,
                "order_price_round": inst.get("order_price_round") if inst else None,
            }
        )
    result["instrument_check"] = instruments

    blockers: list[str] = []
    if not gate_routed:
        blockers.append("Gate is not routed; set EXCHANGE=gate or add a channel exchange map entry")
    if not all(result["keys"].values()):  # type: ignore[union-attr]
        blockers.append("Gate api_key/secret_key is not fully configured")
    if not balance.get("total_usdt") and not balance.get("available_usdt"):
        blockers.append("Gate balance returned 0 USDT; verify key mode and account funds")
    if not risk["dry_run"]:
        blockers.append("dry_run is already false; turn it on before preflight")
    if risk["kill_switch"]:
        blockers.append("kill_switch is on; live execution is blocked")
    if not feature_enabled(db, "okx_execute"):
        blockers.append("exchange execution feature is disabled")
    if not risk["require_stop_loss"]:
        blockers.append("require_stop_loss should stay true before live trading")

    result["blockers"] = blockers
    result["ready_for_live_switch"] = (
        gate_routed
        and not env.gate_testnet
        and not blockers
        and bool(balance.get("available_usdt") or balance.get("total_usdt"))
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not blockers else 2


if __name__ == "__main__":
    raise SystemExit(main())

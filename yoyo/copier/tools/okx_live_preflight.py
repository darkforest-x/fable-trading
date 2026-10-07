from __future__ import annotations

import json

from yoyo.copier.config import env
from yoyo.copier.monitor_switches import feature_enabled
from yoyo.copier.okx.client import OkxClient
from yoyo.copier.runtime_config import get_full_config
from yoyo.copier.store.sqlite import Database


def main() -> int:
    db = Database()
    cfg = get_full_config(db)
    risk = cfg["risk"]
    okx = OkxClient()

    result: dict[str, object] = {
        "mode": "demo" if env.okx_demo else "live",
        "keys": {
            "api_key": bool(env.okx_api_key),
            "secret_key": bool(env.okx_secret_key),
            "passphrase": bool(env.okx_passphrase),
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

    balance = okx.get_balance_summary()
    positions = okx.get_positions()
    raw_account_status = account_status()
    result["okx_account_status"] = raw_account_status
    result["balance"] = balance
    result["positions_count"] = len(positions)
    result["positions"] = [
        {
            "instId": p.get("instId"),
            "posSide": p.get("posSide"),
            "pos": p.get("pos"),
            "avgPx": p.get("avgPx"),
            "upl": p.get("upl"),
        }
        for p in positions
    ]

    instruments: list[dict[str, object]] = []
    for symbol in list(risk["symbols_whitelist"])[:20]:
        inst_id = f"{str(symbol).upper()}-USDT-SWAP"
        inst = okx.get_instrument(inst_id)
        ticker = okx.get_ticker(inst_id) if inst else 0.0
        instruments.append(
            {
                "inst_id": inst_id,
                "available": bool(inst),
                "mark_or_last": ticker,
                "lotSz": inst.get("lotSz") if inst else None,
                "ctVal": inst.get("ctVal") if inst else None,
            }
        )
    result["instrument_check"] = instruments

    blockers: list[str] = []
    if not all(result["keys"].values()):  # type: ignore[union-attr]
        blockers.append("OKX key/secret/passphrase is not fully configured")
    if not balance.get("total_usdt") and not balance.get("available_usdt"):
        if raw_account_status.get("code") and raw_account_status.get("code") != "0":
            blockers.append(
                f"OKX account API error {raw_account_status.get('code')}: {raw_account_status.get('msg')}"
            )
        else:
            blockers.append("OKX balance returned 0 USDT; verify key mode and account funds")
    if not risk["dry_run"]:
        blockers.append("dry_run is already false; turn it on before preflight")
    if risk["kill_switch"]:
        blockers.append("kill_switch is on; live execution is blocked")
    if not feature_enabled(db, "okx_execute"):
        blockers.append("okx_execute feature is disabled")
    if not risk["require_stop_loss"]:
        blockers.append("require_stop_loss should stay true before live trading")

    result["blockers"] = blockers
    result["ready_for_live_switch"] = (
        env.okx_demo is False
        and not blockers
        and bool(balance.get("available_usdt") or balance.get("total_usdt"))
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not blockers else 2


def account_status() -> dict[str, object]:
    if not env.okx_api_key or not env.okx_secret_key or not env.okx_passphrase:
        return {"code": "missing_keys", "msg": "OKX credentials are incomplete"}
    try:
        import okx.Account as Account

        flag = "1" if env.okx_demo else "0"
        account = Account.AccountAPI(
            env.okx_api_key,
            env.okx_secret_key,
            env.okx_passphrase,
            False,
            flag,
        )
        resp = account.get_account_balance()
        data = resp.get("data") or []
        details = data[0].get("details", []) if data else []
        return {
            "code": resp.get("code"),
            "msg": resp.get("msg"),
            "data_count": len(data),
            "detail_count": len(details),
            "currencies": [d.get("ccy") for d in details[:10]],
        }
    except Exception as exc:
        return {"code": "exception", "msg": str(exc)}


if __name__ == "__main__":
    raise SystemExit(main())

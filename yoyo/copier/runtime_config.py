"""运行时配置：YAML 默认值 + SQLite 覆盖（管理台可改，多数无需重启）。"""

from __future__ import annotations

import json
from typing import Any

from yoyo.copier.config import ROOT, app_config, env
from yoyo.copier.store.sqlite import Database

PINNED_SETTING = "pinned_channels"
DISCORD_KEYS = ("discord_signal_channel_id", "allowed_author_names")


def _parse_bool(val: str) -> bool:
    return val.lower() in ("true", "1", "yes")


def _coerce(val: str, template: Any) -> Any:
    if isinstance(template, bool):
        return _parse_bool(val)
    if isinstance(template, int):
        return int(float(val))
    if isinstance(template, float):
        return float(val)
    if isinstance(template, list):
        if val.startswith("["):
            return json.loads(val)
        return [x.strip() for x in val.split(",") if x.strip()]
    return val


def _merge_section(base: dict[str, Any], overrides: dict[str, str], keys: tuple[str, ...]) -> dict[str, Any]:
    out = dict(base)
    for key in keys:
        if key in overrides:
            out[key] = _coerce(overrides[key], out.get(key, ""))
    return out


def load_pinned_from_db(db: Database) -> dict[str, Any] | None:
    raw = db.get_setting(PINNED_SETTING)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("channels"):
            return data
    except json.JSONDecodeError:
        pass
    return None


def save_pinned_to_db(db: Database, guild_id: str, channels: list[str]) -> None:
    names = [str(n).strip().lstrip("#") for n in channels if str(n).strip()]
    payload = {"guild_id": guild_id.strip(), "channels": names}
    db.set_setting(PINNED_SETTING, json.dumps(payload, ensure_ascii=False))
    # 同步写 yaml 便于备份与手动编辑
    path = ROOT / "config" / "pinned_channels.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    import yaml

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(payload, f, allow_unicode=True, default_flow_style=False)


def get_user_listener(db: Database) -> dict[str, Any]:
    base = app_config.user_listener.model_dump()
    ov = db.get_all_settings()
    keys = (
        "poll_interval_min_sec",
        "poll_interval_max_sec",
        "channel_gap_min_sec",
        "channel_gap_max_sec",
        "poll_limit",
        "max_channels",
        "safe_mode",
    )
    for k in keys:
        if k in ov:
            base[k] = _coerce(ov[k], base[k])
    if base.get("safe_mode"):
        base["poll_interval_min_sec"] = max(float(base["poll_interval_min_sec"]), 30.0)
        base["poll_interval_max_sec"] = max(
            float(base["poll_interval_max_sec"]), float(base["poll_interval_min_sec"])
        )
    return base


def get_discord(db: Database) -> dict[str, Any]:
    base = app_config.discord.model_dump()
    ov = db.get_all_settings()
    if ov.get("discord_signal_channel_id"):
        base["signal_channel_id"] = ov["discord_signal_channel_id"].strip()
    if ov.get("allowed_author_names"):
        base["allowed_author_names"] = _coerce(ov["allowed_author_names"], [])
    return base


def get_okx(db: Database) -> dict[str, Any]:
    base = app_config.okx.model_dump()
    if env.exchange:
        base["exchange"] = env.exchange.strip().lower()
    ov = db.get_all_settings()
    for k in ("exchange", "default_leverage", "entry_policy", "td_mode"):
        if k in ov:
            base[k] = _coerce(ov[k], base.get(k, 0))
    base["exchange"] = str(base.get("exchange") or "okx").strip().lower()
    return base


def get_deepseek(db: Database) -> dict[str, Any]:
    base = app_config.deepseek.model_dump()
    ov = db.get_all_settings()
    if ov.get("deepseek_model"):
        base["model"] = ov["deepseek_model"].strip()
    return base


def get_full_config(db: Database) -> dict[str, Any]:
    ov = db.get_all_settings()
    risk = app_config.risk.model_dump()
    for key in risk:
        if key in ov:
            risk[key] = _coerce(ov[key], risk[key])
    if ov.get("kill_switch"):
        risk["kill_switch"] = _parse_bool(ov["kill_switch"])
    if ov.get("dry_run"):
        risk["dry_run"] = _parse_bool(ov["dry_run"])

    pinned = load_pinned_from_db(db)
    if not pinned:
        from yoyo.copier.pinned_channels import load_pinned_config

        pinned = load_pinned_config()

    from yoyo.copier.telegram_channels import list_configured_channels

    tg_channels = list_configured_channels()

    return {
        "risk": risk,
        "discord": get_discord(db),
        "telegram": {
            "enabled": app_config.telegram.enabled,
            "channels": tg_channels,
        },
        "okx": get_okx(db),
        "deepseek": get_deepseek(db),
        "user_listener": get_user_listener(db),
        "pinned": {
            "guild_id": str(pinned.get("guild_id", "")),
            "channels": list(pinned.get("channels") or []),
        },
        "okx_demo": env.okx_demo,
        "telegram_configured": bool(env.telegram_api_id and env.telegram_api_hash),
    }


def _write_risk_keys(db: Database, risk: dict[str, Any]) -> None:
    for key, val in risk.items():
        if val is None:
            continue
        if isinstance(val, list):
            db.set_setting(key, json.dumps(val))
        elif isinstance(val, bool):
            db.set_setting(key, "true" if val else "false")
        else:
            db.set_setting(key, str(val))


def apply_settings_update(db: Database, data: dict[str, Any]) -> None:
    """将管理台提交的配置写入 settings 表。"""
    if "risk" in data and isinstance(data["risk"], dict):
        _write_risk_keys(db, data["risk"])

    if "telegram_channels" in data and isinstance(data["telegram_channels"], list):
        save_telegram_channels(db, data["telegram_channels"])

    if "pinned" in data and isinstance(data["pinned"], dict):
        p = data["pinned"]
        save_pinned_to_db(db, str(p.get("guild_id", "")), list(p.get("channels") or []))
        _sync_monitor_channels(db)

    if "discord" in data and isinstance(data["discord"], dict):
        val = data["discord"]
        if val.get("signal_channel_id") is not None:
            db.set_setting("discord_signal_channel_id", str(val["signal_channel_id"]))
        if val.get("allowed_author_names") is not None:
            db.set_setting("allowed_author_names", json.dumps(val["allowed_author_names"]))

    if "okx" in data and isinstance(data["okx"], dict):
        for k, v in data["okx"].items():
            if v is not None:
                db.set_setting(k, str(v))

    if "deepseek" in data and isinstance(data["deepseek"], dict):
        if data["deepseek"].get("model") is not None:
            db.set_setting("deepseek_model", str(data["deepseek"]["model"]))

    if "user_listener" in data and isinstance(data["user_listener"], dict):
        for k, v in data["user_listener"].items():
            if v is not None:
                db.set_setting(k, str(v))

    db.audit("settings_updated", json.dumps(list(data.keys()))[:200])


def _sync_monitor_channels(db: Database) -> None:
    """Telegram 频道列表变更后，合并进监控开关（新频道默认开启）。"""
    from yoyo.copier.monitor_switches import merge_channel_ids
    from yoyo.copier.telegram_channels import list_configured_channels

    ids = [str(ch["chat_id"]) for ch in list_configured_channels() if ch.get("chat_id")]
    if ids:
        merge_channel_ids(db, ids)


def save_telegram_channels(db: Database, channels: list[dict[str, str]]) -> None:
    from yoyo.copier.telegram_channels import save_telegram_channels_yaml

    save_telegram_channels_yaml(channels)
    db.set_setting("telegram_channels", json.dumps({"channels": channels}, ensure_ascii=False))
    _sync_monitor_channels(db)

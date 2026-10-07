from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parents[1]
DEFAULT_HOME = Path.home() / "Library" / "Application Support" / "Fable" / "DiscordCopier"
# Runtime state (config.yaml, config/, data/, .env) lives outside git. Code stays
# in the repository; COPIER_HOME lets tests and dry runs use a throwaway home.
ROOT = Path(os.environ.get("COPIER_HOME") or DEFAULT_HOME)
ENV_FILE = ROOT / ".env"
DOCS_DIR = REPO_ROOT / "docs" / "copier"
DEFAULT_CONFIG = ROOT / "config.yaml"
PACKAGED_CONFIG = PACKAGE_DIR / "config.default.yaml"
DEFAULT_DB = ROOT / "data" / "copier.db"

load_dotenv(ENV_FILE)


class DiscordConfig(BaseModel):
    signal_channel_id: str = ""
    allowed_author_names: list[str] = Field(default_factory=list)


class OkxConfig(BaseModel):
    exchange: str = "okx"
    inst_type: str = "SWAP"
    td_mode: str = "cross"
    position_mode: str = "net_mode"
    default_leverage: int = 5
    entry_policy: str = "mid"


class RiskConfig(BaseModel):
    position_pct: float = 1.0
    max_entry_deviation_pct: float = 0.2
    max_leverage: int = 5
    require_stop_loss: bool = True
    max_open_positions: int = 3
    max_position_per_symbol: int = 1
    cooldown_minutes: int = 30
    symbols_whitelist: list[str] = Field(
        default_factory=lambda: ["BTC", "ETH", "BNB", "ZEC", "SOL", "LINK", "XRP", "DOGE", "ADA"]
    )
    dry_run: bool = False
    kill_switch: bool = False
    min_confidence: float = 0.8
    max_daily_loss_usdt: float = 500.0


class DeepseekConfig(BaseModel):
    model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com"
    min_confidence: float = 0.8


class AdminConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8080
    approval_mode: str = "auto"
    require_auth: bool = False


class TelegramConfig(BaseModel):
    enabled: bool = True
    channels_config_path: str = "config/telegram_channels.yaml"


class UserListenerConfig(BaseModel):
    enabled: bool = False
    channel_ids: list[str] = Field(default_factory=list)
    use_pinned_list: bool = True
    pinned_config_path: str = "config/pinned_channels.yaml"
    safe_mode: bool = True
    poll_interval_min_sec: float = 30.0
    poll_interval_max_sec: float = 300.0
    channel_gap_min_sec: float = 15.0
    channel_gap_max_sec: float = 60.0
    poll_limit: int = 3
    max_channels: int = 12


class AppConfig(BaseModel):
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    okx: OkxConfig = Field(default_factory=OkxConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    deepseek: DeepseekConfig = Field(default_factory=DeepseekConfig)
    admin: AdminConfig = Field(default_factory=AdminConfig)
    user_listener: UserListenerConfig = Field(default_factory=UserListenerConfig)


class EnvSettings(BaseSettings):
    exchange: str = ""
    deepseek_api_key: str = ""
    telegram_api_id: str = ""
    telegram_api_hash: str = ""
    telegram_session_path: str = ""
    telegram_session_string: str = ""
    telegram_bot_token: str = ""
    telegram_enabled: bool = False
    telegram_notify_chat_id: str = ""
    telegram_notify_enabled: bool = True
    discord_bot_token: str = ""
    discord_user_token: str = ""
    discord_user_listener: bool = False
    okx_api_key: str = ""
    okx_secret_key: str = ""
    okx_passphrase: str = ""
    okx_demo: bool = True
    gate_api_key: str = ""
    gate_secret_key: str = ""
    gate_settle: str = "usdt"
    gate_testnet: bool = False
    gate_base_url: str = ""
    binance_api_key: str = ""
    binance_secret_key: str = ""
    binance_testnet: bool = False
    binance_base_url: str = ""
    admin_password: str = "changeme"
    admin_snapshot_url: str = ""
    chrome_path: str = ""
    jwt_secret: str = "change-me"
    database_path: str = ""
    config_path: str = ""

    class Config:
        env_file = str(ENV_FILE)
        extra = "ignore"


def load_yaml_config(path: Path | None = None) -> AppConfig:
    path = path or Path(os.getenv("CONFIG_PATH", DEFAULT_CONFIG))
    if not path.exists():
        path = PACKAGED_CONFIG
    if not path.exists():
        return AppConfig()
    with open(path, encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}
    return AppConfig.model_validate(data)


env = EnvSettings()
app_config = load_yaml_config(Path(env.config_path) if env.config_path else None)

if env.telegram_enabled:
    app_config.telegram.enabled = True
if env.discord_user_listener:
    app_config.user_listener.enabled = True


def get_db_path() -> Path:
    if env.database_path:
        return Path(env.database_path)
    return DEFAULT_DB

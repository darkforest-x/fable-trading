"""Permissions and immutable configuration for the rotation research observer.

The owner requested a complete research system on 2026-09-09. That request
does not transfer another experiment's holdout permission. Validate the cutoff
and a configuration/source-bound receipt BEFORE constructing any HTTP request.
No mode grants training, promotion, notifications, order or canonical-data writes.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping, Optional

from yoyo.contracts.holdout import HOLDOUT_START, assert_pre_holdout

SCHEMA = "altcoin-rotation-v1"
ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ID = "exp-altcoin-rotation-system-20260909-v1"
EXPERIMENT = ROOT / "experiments" / "active" / EXPERIMENT_ID
INTERVAL_SECONDS = {"1d": 86400, "4h": 14400, "15m": 900}


class RotationError(ValueError):
    """A research observer contract or acquisition failed explicitly."""


def utc(value: Any) -> datetime:
    """Accept an aware instant, normalize UTC, and reject a guessed timezone."""
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RotationError("invalid ISO instant") from exc
    else:
        raise RotationError("instant must be an aware datetime or ISO string")
    if result.tzinfo is None or result.utcoffset() is None:
        raise RotationError("instant must include a timezone")
    return result.astimezone(timezone.utc)


def iso(value: Any) -> str:
    return utc(value).isoformat().replace("+00:00", "Z")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class RotationConfig:
    """Unvalidated observation heuristics; never edits an execution preset."""

    mode: str = "historical_research"
    venue: str = "binance"
    as_of: str = "2026-04-30T00:00:00Z"
    symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT",
                                "AVAXUSDT", "INJUSDT", "ETCUSDT", "DOGEUSDT")
    universe_mode: str = "explicit"
    max_symbols: int = 80
    daily_bars: int = 95
    setup_bars: int = 100
    trigger_bars: int = 100
    lookback: int = 20
    volume_multiple: float = 2.0
    minimum_daily_quote_volume: float = 1_000_000.0
    illustrative_risk_fraction: float = 0.005
    maximum_illustrative_allocation: float = 0.10
    observation_interval_seconds: int = 900
    derivative_context_limit: int = 6

    def __post_init__(self) -> None:
        if self.mode not in {"historical_research", "live_observation", "synthetic_demo"}:
            raise RotationError("unknown mode")
        if self.venue not in {"binance", "okx"}:
            raise RotationError("venue must be explicitly binance or okx; no fallback")
        if self.universe_mode not in {"explicit", "exchange_spot"}:
            raise RotationError("unknown universe mode")
        if self.universe_mode == "exchange_spot" and self.mode != "live_observation":
            raise RotationError("current exchange membership cannot select a historical universe")
        if self.mode != "live_observation":
            assert_pre_holdout(utc(self.as_of), what="rotation configuration cutoff")
        if not self.symbols or len(set(self.symbols)) != len(self.symbols):
            raise RotationError("symbols must be nonempty and unique")
        if any(not isinstance(s, str) or not re.fullmatch(r"[A-Z0-9]{2,24}USDT", s) for s in self.symbols):
            raise RotationError("symbols must be normalized BASEUSDT spot identifiers")
        for name, lower, upper in (("max_symbols", 2, 200), ("daily_bars", 51, 500),
                                   ("setup_bars", 35, 500), ("trigger_bars", 35, 500),
                                   ("lookback", 5, 60), ("observation_interval_seconds", 900, 86400),
                                   ("derivative_context_limit", 0, 6)):
            n = getattr(self, name)
            if isinstance(n, bool) or not isinstance(n, int) or not lower <= n <= upper:
                raise RotationError(name + " is out of bounds")
        if min(self.setup_bars, self.trigger_bars) < self.lookback + 25:
            raise RotationError("setup/trigger coverage must include lookback plus 25 closed bars")
        if len(self.symbols) > self.max_symbols:
            raise RotationError("explicit universe exceeds max_symbols")
        for name, low, high in (("volume_multiple", 1, 10),
                                ("minimum_daily_quote_volume", 0, 1e12),
                                ("illustrative_risk_fraction", 0, .01),
                                ("maximum_illustrative_allocation", 0, .25)):
            number = getattr(self, name)
            if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or not low <= number <= high:
                raise RotationError(name + " must be finite and within bounds")

    def to_dict(self) -> dict:
        value = asdict(self)
        value["symbols"] = list(self.symbols)
        return value

    @property
    def config_hash(self) -> str:
        return digest(self.to_dict())

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RotationConfig":
        allowed = set(cls.__dataclass_fields__)
        unknown = set(value) - allowed
        if unknown:
            raise RotationError("unknown config fields: " + ", ".join(sorted(unknown)))
        payload = dict(value)
        if "symbols" in payload:
            if not isinstance(payload["symbols"], list):
                raise RotationError("symbols must be a JSON array")
            payload["symbols"] = tuple(payload["symbols"])
        return cls(**payload)


def load_config(path: Path) -> RotationConfig:
    return RotationConfig.from_mapping(json.loads(Path(path).read_text()))


def source_identity() -> dict:
    """Content identity includes every observer/scoring source, never a cache path."""
    paths = [ROOT / "yoyo/contracts/rotation.py", ROOT / "yoyo/contracts/holdout.py",
             ROOT / "yoyo/contracts/costs.py", ROOT / "yoyo/data/rotation_features.py",
             ROOT / "yoyo/layers/l1_detection/rotation_setups.py",
             ROOT / "yoyo/layers/l2_judgment/rotation_judgment.py"]
    paths += sorted((ROOT / "yoyo/rotation").glob("*.py"))
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in paths if p.is_file()}
    return {"source_hash": digest(hashes), "files": hashes}


def authorize(config: RotationConfig, as_of: Any, *, source_hash: str,
              receipt: Optional[Mapping[str, Any]] = None, now: Any = None) -> dict:
    """Pre-IO gate. A receipt is a recorded owner decision, not a CLI yes flag.

    A bounded live-observation session may cover repeat scans of one unchanged
    configuration, but never historical parameter evaluation or order activity.
    No caller in this package creates a positive approval receipt automatically.
    """
    cutoff = utc(as_of)
    wall = utc(now) if now is not None else now_utc()
    policy = {"execution_eligible": False, "training_eligible": False,
              "model_scored": False, "raw_candles_persisted": False,
              "holdout_consumed": False, "authorization_reference": None}
    if cutoff > wall:
        raise RotationError("future cutoff is not observable")
    if config.mode in {"historical_research", "synthetic_demo"}:
        assert_pre_holdout(cutoff, what="rotation scan cutoff before market IO")
        return policy
    if config.mode != "live_observation" or cutoff < HOLDOUT_START:
        raise RotationError("live mode requires a current observation cutoff")
    if abs((wall - cutoff).total_seconds()) > 300:
        raise RotationError("live permission does not authorize historical replay")
    if not isinstance(receipt, Mapping):
        raise RotationError("current-market scoring requires an explicit owner holdout receipt for this configuration")
    if receipt.get("scope") != "bounded_live_observation" or receipt.get("experiment_id") != EXPERIMENT_ID:
        raise RotationError("approval scope or experiment does not match")
    if receipt.get("config_hash") != config.config_hash or receipt.get("source_hash") != source_hash:
        raise RotationError("approval does not match the current configuration and source")
    if not isinstance(receipt.get("owner_quote"), str) or not receipt["owner_quote"].strip():
        raise RotationError("approval must record the actual owner quote")
    count = receipt.get("consumption_number")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise RotationError("approval must record the consumption number")
    if not receipt.get("reference"):
        raise RotationError("approval must identify the conversation decision")
    if not (utc(receipt["approved_at"]) <= cutoff <= utc(receipt["valid_until"]) and
            utc(receipt["approved_at"]) <= wall <= utc(receipt["valid_until"])):
        raise RotationError("approval is not yet valid or has expired")
    policy.update(holdout_consumed=True, authorization_reference=receipt["reference"],
                  consumption_number=count, approved_scope=receipt["scope"])
    return policy


def safe_output(path: Path, *, root: Path = ROOT) -> Path:
    """Keep derived snapshots/journals out of candles, models and live logs."""
    resolved = Path(path).expanduser().resolve()
    allowed = (root.resolve() / "experiments/active" / EXPERIMENT_ID).resolve()
    if resolved != allowed and allowed not in resolved.parents:
        raise RotationError("observer output must remain inside " + str(allowed))
    return resolved

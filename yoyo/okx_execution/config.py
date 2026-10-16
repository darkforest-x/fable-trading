"""Owner's Task 59 settings; no extra coin, timeframe or concurrency filter.

2026-10-16: 50 USDT sleeve, 2% initial-stop sizing, venue maximum leverage.
Three-position / six-percent aggregate caps were explicitly rejected.
Credentials are local files outside the repository, never API/UI parameters.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path

DEFAULT_RUNTIME = Path.home() / "Library/Application Support/Fable/OKXExecution"
DEFAULT_SOURCE = Path.home() / "Library/Application Support/Fable/ImpulseMonitor"


@dataclass(frozen=True)
class Config:
    capital_usdt: str = "50"
    risk_fraction: str = "0.02"
    leverage_policy: str = "venue_maximum"
    margin_mode: str = "isolated"
    position_mode: str = "long_short_mode"
    max_positions: None = None
    aggregate_risk_limit: None = None
    source: str = str(DEFAULT_SOURCE)
    host: str = "https://www.okx.com"
    demo: bool = False

    def validate(self):
        # Hosts are not arbitrary credential destinations.
        if self.host not in {"https://www.okx.com", "https://us.okx.com", "https://eea.okx.com"}:
            raise ValueError("unsupported OKX API host")
        if (self.capital_usdt != "50" or self.risk_fraction != "0.02"
                or self.leverage_policy != "venue_maximum" or self.margin_mode != "isolated"
                or self.position_mode != "long_short_mode" or self.max_positions is not None
                or self.aggregate_risk_limit is not None or type(self.demo) is not bool):
            raise ValueError("configuration differs from the reviewed Task 59 contract")


def private_directory(path: Path) -> Path:
    path = path.expanduser().absolute()
    if path.is_symlink():
        raise ValueError("runtime must not be a symlink")
    repo = Path(__file__).resolve().parents[2]
    if path.resolve().is_relative_to(repo):
        raise ValueError("keep credentials and execution state outside the repository")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def write_private(path: Path, value: dict):
    """Create once; refuse replacement of live configuration or credentials."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def read_private(path: Path) -> dict:
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise ValueError("private file must be a regular owner-only file")
    with path.open() as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("invalid private configuration")
    return value

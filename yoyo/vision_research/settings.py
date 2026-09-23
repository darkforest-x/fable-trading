"""Persist owner-authorized local Gemini settings outside the inference ledger.

Only the local server reads this private file. Atomic replacement avoids partial
credentials after interruption; API responses never include the saved secret.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


class LocalSettings:
    def __init__(self, runtime: Path):
        self.directory = runtime / "private"
        self.path = self.directory / "settings.json"

    def load(self):
        if not self.path.exists():
            return {}
        try:
            if self.directory.is_symlink() or self.path.is_symlink():
                raise ValueError()
            self.directory.chmod(0o700)
            self.path.chmod(0o600)
            data = json.loads(self.path.read_text())
            if not isinstance(data, dict) or set(data) - {"api_key", "model"}:
                raise ValueError()
            if any(not isinstance(value, str) for value in data.values()):
                raise ValueError()
            return data
        except (OSError, ValueError):
            raise RuntimeError("本机模型配置无法读取，请检查 private/settings.json") from None

    def save(self, api_key: str, model: str):
        temporary = None
        try:
            if self.directory.is_symlink() or self.path.is_symlink():
                raise ValueError()
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.directory.chmod(0o700)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory,
                                             prefix=".settings-", delete=False) as stream:
                temporary = Path(stream.name)
                os.fchmod(stream.fileno(), 0o600)
                json.dump({"api_key": api_key, "model": model}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except (OSError, ValueError):
            raise RuntimeError("本机模型配置未能保存，请检查目录权限") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

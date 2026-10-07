"""Copier tests run in .venv-copier, against a throwaway COPIER_HOME.

Two guards:

* The research venv does not carry the copier's dependencies (requirements-copier.txt
  is a separate venv on purpose), so in that venv this directory is skipped rather
  than reported as import errors. CI runs it in its own job.
* ``yoyo.copier.config`` resolves the runtime home -- and loads its ``.env`` --
  at import time. Pointing COPIER_HOME at an empty temp dir before any test
  module imports it means no test can read the real API keys or write the live
  database. The old project ran its tests with the production ``.env`` loaded.
"""
from __future__ import annotations

import importlib.util
import os
import tempfile

_REQUIRED = ("okx", "telethon", "jose", "dotenv", "pydantic_settings", "openai")
_MISSING = [name for name in _REQUIRED if importlib.util.find_spec(name) is None]

if _MISSING:
    collect_ignore_glob = ["test_*.py"]
else:
    os.environ["COPIER_HOME"] = tempfile.mkdtemp(prefix="copier-test-home-")
    for _key in [k for k in os.environ if k.startswith(("OKX_", "GATE_", "BINANCE_", "TELEGRAM_", "DEEPSEEK_", "DISCORD_"))]:
        del os.environ[_key]

    import pytest

    @pytest.fixture(autouse=True)
    def placeholder_exchange_credentials(monkeypatch):
        """Make `exchange_configured()` true with obviously fake keys.

        Router tests mock the exchange client but still pass the "is this
        exchange configured?" gate. The old project satisfied that gate by
        loading the production .env into the test process; placeholders do the
        same job, and any unmocked call fails authentication instead of trading.
        """
        from yoyo.copier.config import env

        for field in ("okx_api_key", "okx_secret_key", "okx_passphrase", "gate_api_key",
                      "gate_secret_key", "binance_api_key", "binance_secret_key"):
            monkeypatch.setattr(env, field, "placeholder-" + field)
        for account in ("MIA", "FEIYANG"):
            monkeypatch.setenv(f"GATE_{account}_API_KEY", "placeholder")
            monkeypatch.setenv(f"GATE_{account}_SECRET_KEY", "placeholder")

    import socket

    _LOOPBACK = {"127.0.0.1", "::1", "localhost"}

    @pytest.fixture(autouse=True)
    def no_external_network(monkeypatch):
        """The copier talks to real exchanges; its tests must not reach the internet."""
        real_getaddrinfo = socket.getaddrinfo

        def guarded_getaddrinfo(host, *args, **kwargs):
            if host not in _LOOPBACK:
                raise OSError(f"copier tests are offline; blocked lookup of {host!r}")
            return real_getaddrinfo(host, *args, **kwargs)

        monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
        # A local HTTP(S)_PROXY (this Mac runs one on loopback) would tunnel straight
        # past the lookup guard, so proxies are removed for the test as well.
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            monkeypatch.delenv(name, raising=False)

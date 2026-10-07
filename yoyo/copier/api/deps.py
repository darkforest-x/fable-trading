from __future__ import annotations

from yoyo.copier.exchange import TradingClient, create_exchange_client, selected_exchange
from yoyo.copier.store.sqlite import Database

_db: Database | None = None
_client: TradingClient | None = None
_client_exchange: str | None = None


def init_deps(db: Database) -> None:
    global _db, _client, _client_exchange
    _db = db
    _client = create_exchange_client(db)
    _client_exchange = selected_exchange(db)


def get_db() -> Database:
    assert _db is not None
    return _db


def get_okx() -> TradingClient:
    global _client, _client_exchange
    assert _db is not None
    current = selected_exchange(_db)
    if _client is None or _client_exchange != current:
        _client = create_exchange_client(_db)
        _client_exchange = current
    return _client

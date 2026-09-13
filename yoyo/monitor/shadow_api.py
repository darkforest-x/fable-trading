"""Lightweight read-only API adapter for the isolated V7/V8 shadow book."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path


SHADOW_DATABASE = "v78-shadow-v2.sqlite3"


class ShadowBookUnavailable(RuntimeError):
    pass


def _connect(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise ShadowBookUnavailable("shadow_not_started")
    database = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
    database.row_factory = sqlite3.Row
    return database


def status(path: Path) -> dict[str, object]:
    """Read compact counts without importing pandas or any signal builder."""
    try:
        with _connect(path) as db:
            events = db.execute("SELECT COUNT(*),SUM(v8_admitted) FROM shadow_events").fetchone()
            cells = db.execute("SELECT status,COUNT(*) n FROM shadow_cells GROUP BY status").fetchall()
            bars = db.execute("SELECT COUNT(*) FROM shadow_bars").fetchone()[0]
            snapshots = db.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0]
            meta = {row[0]: json.loads(row[1]) for row in db.execute(
                "SELECT key,payload FROM meta WHERE key IN ('activation','scan')")}
    except (sqlite3.Error, json.JSONDecodeError) as error:
        raise ShadowBookUnavailable("shadow_book_invalid") from error
    return {"configured": True, "events": int(events[0]), "v8_admitted": int(events[1] or 0),
            "path_bars": int(bars), "market_snapshots": int(snapshots),
            "cells": {str(row[0]): int(row[1]) for row in cells},
            "activation": meta.get("activation"), "scan": meta.get("scan")}


def events(path: Path, *, limit: int, timeframe: str | None = None,
           side: int | None = None) -> list[dict[str, object]]:
    filters: list[str] = []
    values: list[object] = []
    if timeframe is not None:
        filters.append("timeframe=?")
        values.append(timeframe)
    if side is not None:
        filters.append("side=?")
        values.append(side)
    where = " WHERE " + " AND ".join(filters) if filters else ""
    try:
        with _connect(path) as db:
            rows = db.execute("SELECT payload FROM shadow_events" + where
                              + " ORDER BY bar_close_ms DESC,id LIMIT ?", values + [limit]).fetchall()
        return [json.loads(row[0]) for row in rows]
    except (sqlite3.Error, json.JSONDecodeError) as error:
        raise ShadowBookUnavailable("shadow_book_invalid") from error


def market_snapshots(path: Path, *, limit: int = 12,
                     timeframe: str | None = None) -> list[dict[str, object]]:
    where = " WHERE timeframe=?" if timeframe is not None else ""
    values: list[object] = [timeframe] if timeframe is not None else []
    try:
        with _connect(path) as db:
            rows = db.execute("SELECT payload FROM market_snapshots" + where
                              + " ORDER BY bar_close_ms DESC,timeframe LIMIT ?", values + [limit]).fetchall()
        return [json.loads(row[0]) for row in rows]
    except (sqlite3.Error, json.JSONDecodeError) as error:
        raise ShadowBookUnavailable("shadow_book_invalid") from error

"""Isolated YOLO-extra worker for persisted, causal SPIKE V1 candidates."""
from __future__ import annotations

import threading
import time
from pathlib import Path

from yoyo.monitor.model_gate import ModelGate
from yoyo.monitor.okx import OKX
from yoyo.monitor.store import Store


def model_forever(database: str, interval_seconds: float = 3.0) -> None:
    """Run model loading/inference outside the FastAPI interpreter.

    The scanner persists only closed V1 charts and candidates. This worker
    reads those records, so expensive Torch initialization cannot delay the
    loopback health or status endpoints and replay rows remain excluded.
    """
    store, client, stop = Store(Path(database)), OKX(), threading.Event()
    try:
        client.synchronize()
    except Exception:
        pass
    gate = ModelGate(store, client.clock, stop)
    thread = threading.Thread(target=gate.run, name="spike-v1-yolo", daemon=True)
    thread.start()
    while True:
        for event in store.list_candidates(2000, pending_only=True):
            market = store.get_market(event["symbol"], event["timeframe"])
            candles = market.get("chart") if market else None
            if isinstance(candles, list) and candles:
                gate.submit(event["symbol"], event["timeframe"], candles)
        status = gate.status()
        store.set_meta("v1:model_gate", status)
        time.sleep(interval_seconds)

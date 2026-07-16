"""HTTP payloads for the scout_mtf side-branch console.

Read latest scan JSON; optionally trigger a new multi-TF rank scan.
Does not touch ACTIVE / forward_log / executor.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[2]
LATEST = PROJECT / "data" / "scout_mtf" / "latest.json"
PAPER_LATEST = PROJECT / "data" / "scout_mtf" / "paper_latest.json"

_run_lock = threading.Lock()
_run_state: dict[str, Any] = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "error": None,
    "last_summary": None,
}


def scout_mtf_status() -> dict[str, Any]:
    return {
        "running": bool(_run_state["running"]),
        "started_at": _run_state["started_at"],
        "finished_at": _run_state["finished_at"],
        "error": _run_state["error"],
        "last_summary": _run_state["last_summary"],
        "latest_exists": LATEST.exists(),
        "latest_mtime": (
            datetime.fromtimestamp(LATEST.stat().st_mtime, tz=timezone.utc).isoformat()
            if LATEST.exists()
            else None
        ),
    }


def scout_mtf_latest() -> dict[str, Any]:
    """Return latest.json or empty shell if never scanned."""
    status = scout_mtf_status()
    if not LATEST.exists():
        return {
            "available": False,
            "status": status,
            "message": "尚无扫描结果。点击「立即扫描」生成。",
            "results": [],
            "summary": {"A": 0, "B": 0, "C": 0},
            "timeframes": ["1m", "3m", "5m", "15m", "30m"],
        }
    try:
        data = json.loads(LATEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "available": False,
            "status": status,
            "message": f"读取 latest.json 失败: {exc}",
            "results": [],
            "summary": {"A": 0, "B": 0, "C": 0},
        }
    data["available"] = True
    data["status"] = status
    # Flatten for table UI
    data["rows"] = _table_rows(data.get("results") or [])
    return data


def _table_rows(results: list[dict]) -> list[dict]:
    rows = []
    for r in results:
        detail = r.get("detail") or {}
        tf = detail.get("tf") or {}
        cells = {}
        for bar in ("1m", "3m", "5m", "15m", "30m"):
            x = tf.get(bar) or {}
            cells[bar] = {
                "ok": bool(x.get("ok")),
                "vote": x.get("vote"),
                "dense": bool(x.get("dense")),
                "above_ema55": x.get("above_ema55"),
                "note": x.get("note"),
                "order_score": x.get("order_score"),
                "atr_pct": x.get("atr_pct"),
            }
        rows.append(
            {
                "symbol": r.get("symbol"),
                "inst_id": r.get("inst_id"),
                "grade": r.get("grade"),
                "composite": r.get("composite"),
                "chg24h_pct": r.get("chg24h_pct"),
                "vol24h_usdt": r.get("vol24h_usdt"),
                "rank_side": r.get("rank_side"),
                "rank": r.get("rank"),
                "last": r.get("last"),
                "tf": cells,
                "base": detail.get("base"),
                "rank_boost": detail.get("rank_boost"),
                "align": detail.get("align"),
            }
        )
    return rows


def scout_mtf_run(
    *,
    top: int = 12,
    min_vol: float = 5_000_000.0,
    include_loss: bool = True,
    max_symbols: int | None = None,
) -> dict[str, Any]:
    """Synchronous scan (may take 30–90s). Rejects if already running."""
    if _run_state["running"]:
        return {
            "ok": False,
            "error": "扫描已在进行中",
            "status": scout_mtf_status(),
        }
    if not _run_lock.acquire(blocking=False):
        return {
            "ok": False,
            "error": "扫描锁被占用",
            "status": scout_mtf_status(),
        }
    _run_state["running"] = True
    _run_state["started_at"] = datetime.now(timezone.utc).isoformat()
    _run_state["finished_at"] = None
    _run_state["error"] = None
    try:
        from src.scout_mtf.pipeline import run_scout

        report = run_scout(
            top_n=int(top),
            min_vol_usdt=float(min_vol),
            include_loss=bool(include_loss),
            max_symbols=int(max_symbols) if max_symbols else None,
        )
        _run_state["last_summary"] = report.get("summary")
        out = scout_mtf_latest()
        out["ok"] = True
        out["ran"] = True
        return out
    except Exception as exc:  # noqa: BLE001
        _run_state["error"] = str(exc)
        return {
            "ok": False,
            "error": str(exc),
            "status": scout_mtf_status(),
        }
    finally:
        _run_state["running"] = False
        _run_state["finished_at"] = datetime.now(timezone.utc).isoformat()
        _run_lock.release()


def scout_mtf_paper_latest() -> dict[str, Any]:
    from src.scout_mtf.paper_sim import load_paper_latest

    data = load_paper_latest()
    data["status"] = scout_mtf_status()
    return data


def scout_mtf_paper_run() -> dict[str, Any]:
    """Run paper sim on latest A/B gainers. No exchange orders."""
    if _run_state["running"]:
        return {"ok": False, "error": "扫描进行中，请稍后再试", "status": scout_mtf_status()}
    if not _run_lock.acquire(blocking=False):
        return {"ok": False, "error": "任务锁被占用", "status": scout_mtf_status()}
    _run_state["running"] = True
    _run_state["started_at"] = datetime.now(timezone.utc).isoformat()
    _run_state["error"] = None
    try:
        from src.scout_mtf.paper_sim import run_paper_test

        report = run_paper_test(grades=("A", "B"), only_gain=True, max_symbols=12)
        return report
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "status": scout_mtf_status()}
    finally:
        _run_state["running"] = False
        _run_state["finished_at"] = datetime.now(timezone.utc).isoformat()
        _run_lock.release()


def scout_mtf_open_positions() -> dict[str, Any]:
    """Read-only open positions from OKX keys file (live or demo env)."""
    try:
        from src.execution.okx_client import OkxDemoClient, OkxDemoError
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"执行模块不可用: {exc}", "positions": []}
    try:
        client = OkxDemoClient()
        raw = client.positions("SWAP")
        positions = []
        for p in raw:
            pos = float(p.get("pos") or 0)
            if abs(pos) < 1e-12:
                continue
            positions.append(
                {
                    "inst_id": p.get("instId"),
                    "symbol": str(p.get("instId") or "").replace("-", "_"),
                    "pos_side": p.get("posSide"),
                    "pos": pos,
                    "avg_px": _f(p.get("avgPx")),
                    "mark_px": _f(p.get("markPx") or p.get("last")),
                    "upl": _f(p.get("upl")),
                    "upl_ratio": _f(p.get("uplRatio")),
                    "lever": p.get("lever"),
                    "mgn_mode": p.get("mgnMode"),
                    "notional_usd": _f(p.get("notionalUsd")),
                    "margin": _f(p.get("margin")),
                    "liab": _f(p.get("liab")),
                    "realized_pnl": _f(p.get("realizedPnl")),
                    "created_time": p.get("cTime") or p.get("uTime"),
                    "raw": {
                        k: p.get(k)
                        for k in (
                            "instId", "posSide", "pos", "avgPx", "upl", "uplRatio",
                            "lever", "mgnMode", "notionalUsd", "margin", "liqPx",
                            "uTime", "cTime",
                        )
                    },
                }
            )
        return {
            "ok": True,
            "environment": client.environment,
            "n": len(positions),
            "positions": positions,
        }
    except OkxDemoError as exc:
        return {"ok": False, "error": str(exc), "positions": []}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "positions": []}


def scout_mtf_chart(
    inst_id: str,
    *,
    bar: str = "15m",
    limit: int = 300,
) -> dict[str, Any]:
    """Recent OHLCV + display MAs for scout drill-down charts (public candles).

    Side-branch only — no forward_log / no ACTIVE. Used so position / paper
    rows can open a TV-style kline with entry/exit overlays.
    """
    inst = str(inst_id or "").strip().upper().replace("_", "-")
    if not inst or "USDT" not in inst:
        return {"ok": False, "error": "需要有效 instId，如 DOGE-USDT-SWAP", "candles": []}
    if not inst.endswith("-SWAP") and inst.endswith("-USDT"):
        inst = inst + "-SWAP"
    bar = str(bar or "15m").strip()
    if bar not in {"1m", "3m", "5m", "15m", "30m", "1H", "4H"}:
        bar = "15m"
    limit = int(min(max(limit, 50), 300))
    try:
        from src.scout_mtf.tf_scan import fetch_candles

        frame = fetch_candles(inst, bar, limit=limit)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "candles": [], "inst_id": inst, "bar": bar}
    if frame is None or frame.empty:
        return {"ok": False, "error": "无K线", "candles": [], "inst_id": inst, "bar": bar}

    # Display MAs: SMA/EMA 20/60/120 (same legend as mainline explore chart)
    close = frame["close"].astype(float)
    mas: dict[str, list[dict]] = {}
    for span in (20, 60, 120):
        sma = close.rolling(span, min_periods=span).mean()
        ema = close.ewm(span=span, adjust=False, min_periods=span).mean()
        mas[f"sma{span}"] = [
            {"time": int(ts // 1000), "value": float(v)}
            for ts, v in zip(frame["ts"], sma)
            if v == v  # not NaN
        ]
        mas[f"ema{span}"] = [
            {"time": int(ts // 1000), "value": float(v)}
            for ts, v in zip(frame["ts"], ema)
            if v == v
        ]

    candles = [
        {
            "time": int(int(ts) // 1000),
            "open": float(o),
            "high": float(h),
            "low": float(l),
            "close": float(c),
            "volume": float(v) if v == v else 0.0,
        }
        for ts, o, h, l, c, v in zip(
            frame["ts"], frame["open"], frame["high"], frame["low"], frame["close"], frame["volume"]
        )
    ]
    return {
        "ok": True,
        "inst_id": inst,
        "bar": bar,
        "candles": candles,
        "mas": mas,
        "ma_legend": "SMA/EMA 20·60·120（展示）",
        "n": len(candles),
    }


def _f(x):
    try:
        if x is None or x == "":
            return None
        return float(x)
    except (TypeError, ValueError):
        return None

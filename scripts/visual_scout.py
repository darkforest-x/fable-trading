"""Visual scout: owner-taste detector watches the RIGHT EDGE of liquid swaps.

Latency design (2026-07-15):
- Always refresh the tip from OKX live candles (never trust stale kline_cache alone).
- Only alert when the dense box right edge is on the last MAX_AGE_BARS bars (default 1).
- Dedupe by last bar timestamp (not coarse hour), so a new 15m bar can re-alert.
- Scout is NOT a trade signal — judgment layer still owns entries.

Loop: scripts/scout_loop.sh (short sleep; full update_okx is NOT on the hot path).
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))
from src.data.loader import FETCHED_DIR  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.detection.data import add_mas, list_cache_files, load_ohlcv_csv  # noqa: E402
from src.detection.render import render_chart  # noqa: E402
from src.notify import send  # noqa: E402

WINDOW = 200
CONF = float(os.environ.get("SCOUT_CONF", "0.25"))
TOP_N = int(os.environ.get("SCOUT_TOP_N", "60"))  # slightly tighter universe = faster cycle
# Only the last bar by default (~0–15m). Set SCOUT_MAX_AGE_BARS=2 for last 30m.
MAX_AGE_BARS = int(os.environ.get("SCOUT_MAX_AGE_BARS", "1"))
CHART_LEFT, CHART_RIGHT = 0.02, 0.99
SCOUT_DIR = PROJECT_DIR / "src/webapp/static/scout"
STATE = PROJECT_DIR / "data/visual_scout_state.json"
LOG = PROJECT_DIR / "data/visual_scout_log.csv"
TMP = PROJECT_DIR / "data/_scout_tmp.png"
BAR_MINUTES = 15


def pick_model() -> Path:
    fixed = PROJECT_DIR / "models/owner_best.pt"
    if fixed.exists():
        return fixed
    runs = sorted(
        PROJECT_DIR.glob("runs/detect/runs/detect/owner_v*/weights/best.pt"),
        key=lambda p: p.stat().st_mtime,
    )
    if not runs:
        raise SystemExit("no owner detector weights found")
    return runs[-1]


def _leaderboard() -> list[str]:
    from src.data.fetch_okx import _request

    payload = _request("https://www.okx.com/api/v5/market/tickers?instType=SWAP")
    rows = []
    for r in payload.get("data", []):
        inst = r.get("instId", "")
        if not inst.endswith("-USDT-SWAP"):
            continue
        symbol = inst.replace("-", "_")
        if is_stockish(symbol):
            continue
        try:
            vol = float(r.get("volCcy24h", 0)) * float(r.get("last", 0))
        except (TypeError, ValueError):
            continue
        rows.append((vol, symbol))
    rows.sort(reverse=True)
    return [s for _, s in rows[:TOP_N]]


def _mini_fetch(symbol: str, need: int = 280, *, allow_partial: bool = True) -> pd.DataFrame | None:
    """Recent 15m bars from OKX. allow_partial=True keeps the open bar (lower lag)."""
    from src.data.fetch_okx import _request

    inst = symbol.replace("_", "-")
    rows, after = [], None
    while len(rows) < need:
        url = (
            f"https://www.okx.com/api/v5/market/{'history-candles' if after else 'candles'}"
            f"?instId={inst}&bar=15m&limit=300"
        )
        if after:
            url += f"&after={after}"
        page = _request(url).get("data") or []
        if not page:
            break
        for r in page:
            if len(r) > 8 and r[8] == "0" and not allow_partial:
                continue
            rows.append(r)
        after = page[-1][0]
        if len(page) < 100:
            break
    if len(rows) < 200:
        return None
    df = pd.DataFrame(
        [
            (int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
            for r in rows
        ],
        columns=["ts", "open", "high", "low", "close", "volume"],
    )
    df = df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    df["open_time"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.drop(columns=["ts"])


def _merge_tip(cache_df: pd.DataFrame, live: pd.DataFrame) -> pd.DataFrame:
    """Keep cache history, overwrite/append with live tip so right edge is fresh."""
    a = cache_df.copy()
    b = live.copy()
    a["open_time"] = pd.to_datetime(a["open_time"], utc=True)
    b["open_time"] = pd.to_datetime(b["open_time"], utc=True)
    cols = ["open_time", "open", "high", "low", "close", "volume"]
    a, b = a[cols], b[cols]
    # Drop cache rows that overlap live tip (keep live prices)
    tip_start = b["open_time"].iloc[0]
    a = a[a["open_time"] < tip_start]
    out = pd.concat([a, b], ignore_index=True)
    return out.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)


def universe_frames():
    """Yield (symbol, ohlcv) with LIVE tip always refreshed from OKX."""
    cache: dict[str, Path] = {}
    for path in list_cache_files(FETCHED_DIR, min_rows=500):
        sym = path.stem.rsplit("_", 2)[0].replace("okx_", "")
        cache[sym] = path
    for symbol in _leaderboard():
        live = _mini_fetch(symbol, need=280, allow_partial=True)
        if live is None:
            if symbol in cache:
                yield symbol, load_ohlcv_csv(cache[symbol])
            continue
        if symbol in cache:
            try:
                base = load_ohlcv_csv(cache[symbol])
                yield symbol, _merge_tip(base, live)
                continue
            except Exception:
                pass
        yield symbol, live


def main() -> int:
    from ultralytics import YOLO

    t0 = time.time()
    weights = pick_model()
    model = YOLO(str(weights))
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    SCOUT_DIR.mkdir(parents=True, exist_ok=True)
    hits = []
    scanned = 0
    now = datetime.now(timezone.utc)

    for symbol, raw in universe_frames():
        scanned += 1
        df = add_mas(raw)
        if len(df) < WINDOW:
            continue
        sub = df.iloc[-WINDOW:].reset_index(drop=True)
        last_ts = pd.Timestamp(sub["open_time"].iloc[-1])
        if last_ts.tzinfo is None:
            last_ts = last_ts.tz_localize("UTC")
        # Skip if the last bar is absurdly old (stale API/cache failure)
        lag_min = (now - last_ts.to_pydatetime().replace(tzinfo=timezone.utc)).total_seconds() / 60.0
        if lag_min > 45:
            continue

        render_chart(sub, out_path=TMP)
        res = model.predict(str(TMP), conf=CONF, verbose=False)[0]
        if res.boxes is None or res.boxes.xywhn is None:
            continue
        live = []
        for b, c in zip(res.boxes.xywhn.cpu().numpy(), res.boxes.conf.cpu().numpy()):
            cx, _, w, _ = map(float, b[:4])
            right = cx + w / 2
            frac = (right - CHART_LEFT) / (CHART_RIGHT - CHART_LEFT)
            age_bars = max(0, int(round((1 - min(max(frac, 0.0), 1.0)) * (WINDOW - 1))))
            if age_bars <= MAX_AGE_BARS:
                live.append((float(c), b, age_bars))
        if not live:
            continue

        # Dedupe: same symbol + same last-bar minute → once
        bar_key = last_ts.strftime("%Y-%m-%d %H:%M")
        prev = state.get(symbol)
        if prev == bar_key:
            continue
        # Also skip if we already alerted this symbol for a NEWER bar (shouldn't happen)
        state[symbol] = bar_key

        conf = max(c for c, _, _ in live)
        age_bars = min(a for _, _, a in live)
        # Forming age = how many bars back the box ends + time since that bar opened
        age_min = int(age_bars * BAR_MINUTES + max(0, lag_min))

        img = cv2.imread(str(TMP))
        ih, iw = img.shape[:2]
        for c, b, _ in live:
            cx, cy, w, h = map(float, b[:4])
            cv2.rectangle(
                img,
                (int((cx - w / 2) * iw), int((cy - h / 2) * ih)),
                (int((cx + w / 2) * iw), int((cy + h / 2) * ih)),
                (60, 200, 120),
                3,
            )
        cv2.imwrite(str(SCOUT_DIR / f"{symbol}.png"), img, [cv2.IMWRITE_PNG_COMPRESSION, 6])
        hits.append(
            {
                "symbol": symbol,
                "conf": round(conf, 3),
                "last_bar": str(last_ts),
                "age_min": age_min,
                "age_bars": age_bars,
                "data_lag_min": round(lag_min, 1),
            }
        )

    if hits:
        LOG.parent.mkdir(exist_ok=True)
        new_file = not LOG.exists()
        with LOG.open("a", newline="") as fh:
            w = csv.DictWriter(
                fh,
                fieldnames=["scanned_at", "symbol", "conf", "last_bar", "age_min", "age_bars", "data_lag_min"],
            )
            if new_file:
                w.writeheader()
            for h in hits:
                w.writerow({"scanned_at": now.isoformat(), **h})
        hits.sort(key=lambda h: -h["conf"])
        lines = [
            f"👁 视觉侦察：{len(hits)} 个币种右缘密集（{weights.name}）",
            f"条件：右缘 ≤ 最近 {MAX_AGE_BARS} 根 15m · live K 线",
        ]
        for h in hits[:10]:
            lines.append(
                f"· {h['symbol']}  conf {h['conf']}  "
                f"右缘约{h['age_min']}分钟前  数据延迟{h['data_lag_min']}m"
            )
        lines.append("图: http://103.214.174.58:8642/scout.html")
        send("\n".join(lines))
    STATE.write_text(json.dumps(state, ensure_ascii=False))

    pngs = sorted(SCOUT_DIR.glob("*.png"), key=lambda p: -p.stat().st_mtime)[:40]
    cards = "".join(
        f'<figure><img src="scout/{p.name}?v={int(p.stat().st_mtime)}">'
        f'<figcaption>{p.stem} · {datetime.fromtimestamp(p.stat().st_mtime):%m-%d %H:%M}</figcaption></figure>'
        for p in pngs
    )
    (PROJECT_DIR / "src/webapp/static/scout.html").write_text(
        f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>视觉侦察 · owner 口味检测器实时哨</title><style>
body{{background:#131519;color:#e8e9eb;font-family:"PingFang SC",system-ui,sans-serif;padding:24px}}
h1{{font-size:20px}} p{{color:#9aa0a8;font-size:13px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(460px,100%),1fr));gap:14px;margin-top:14px}}
figure{{margin:0;background:#1b1e24;border:1px solid #2e3340;border-radius:10px;padding:8px}}
img{{width:100%;border-radius:6px;display:block}} figcaption{{font-size:12px;color:#9aa0a8;margin-top:4px}}
</style></head><body>
<h1>视觉侦察 —— 检测器盯右边缘（live K 线）</h1>
<p>绿框 = owner 模型在最新 200 根 15m 上的检出。仅推送右缘落在最近 {MAX_AGE_BARS} 根 bar 内的框。
侦察 ≠ 交易信号。周期约每 2–3 分钟一轮（不再卡全市场 update）。</p>
<div class="grid">{cards}</div></body></html>""",
        encoding="utf-8",
    )
    elapsed = time.time() - t0
    print(
        f"scout done: scanned={scanned} new={len(hits)} model={weights.name} "
        f"max_age_bars={MAX_AGE_BARS} elapsed={elapsed:.1f}s",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

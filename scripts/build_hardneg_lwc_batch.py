#!/usr/bin/env python3
"""H-FE-1 deepen: hardneg preview stems → offline LWC HTML with layer toggles.

CPU only. Reads hardneg inventory + okx_*_15m_*.csv. Does NOT touch YOLO,
MPS, holdout, LIVE, or FastAPI.

Layers (toggle in HTML):
  - hardneg band  = GT mid-cluster box as time range (approx; ignores MARGIN)
  - tip band      = last 5% of window (x≥0.95) as thin marker zone
  - aftermath     = from box right edge → tip start (post-cluster bars)

Usage:
  PYTHONPATH=. .venv/bin/python scripts/build_hardneg_lwc_batch.py
  PYTHONPATH=. .venv/bin/python scripts/build_hardneg_lwc_batch.py --limit 10
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_SUMMARY = PROJECT / "analysis/output/hardneg_mid_cluster/hardneg_mid_cluster_summary.json"
DEFAULT_CSV = PROJECT / "analysis/output/hardneg_mid_cluster/hardneg_mid_cluster_candidates.csv"
DEFAULT_OUT = PROJECT / "analysis/output/wuzao_lwc_hardneg_batch"
KLINE = PROJECT / "data/kline_fetched"
WIN_BARS = 200
STEM_RE = re.compile(
    r"^(?:okx_)?(?P<sym>[A-Z0-9]+)_USDT(?:_SWAP)?_(?P<end>\d+)$",
    re.I,
)
VENDOR_REL = "../../../src/webapp/static/vendor/lightweight-charts.standalone.production.js"


def resolve_csv(symbol_key: str) -> Path | None:
    """Prefer SWAP 15m series; fall back to spot-style naming."""
    key = symbol_key.upper()
    for pat in (
        f"okx_{key}_USDT_SWAP_15m_*.csv",
        f"okx_{key}_USDT_15m_*.csv",
        f"okx_{key}_15m_*.csv",
    ):
        hits = sorted(KLINE.glob(pat))
        if hits:
            return hits[-1]
    return None


def window_frame(csv_path: Path, end_i: int, n: int = WIN_BARS) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    if end_i < 0 or end_i >= len(df):
        raise IndexError(f"end_i={end_i} out of range for {csv_path.name} len={len(df)}")
    start = max(0, end_i - (n - 1))
    return df.iloc[start : end_i + 1].copy()


def sma(times: list[int], closes: list[float], w: int) -> list[dict]:
    out = []
    for i in range(len(closes)):
        if i + 1 < w:
            continue
        out.append(
            {
                "time": int(times[i]),
                "value": float(sum(closes[i + 1 - w : i + 1]) / w),
            }
        )
    return out


def build_chart(row: dict, preview_rel: str | None) -> dict | None:
    m = STEM_RE.match(row["stem"])
    if not m:
        return None
    sym = m.group("sym").upper()
    end_i = int(m.group("end"))
    csv_path = resolve_csv(row.get("symbol_key") or sym)
    if csv_path is None:
        return {"stem": row["stem"], "error": f"no kline csv for {sym}"}

    win = window_frame(csv_path, end_i)
    if len(win) < 20:
        return {"stem": row["stem"], "error": f"short window n={len(win)}"}

    # unix seconds for LWC
    if "open_time" in win.columns:
        ts = pd.to_datetime(win["open_time"], utc=True)
        times = (ts.astype("int64") // 10**9).astype(int).tolist()
    else:
        times = (win["ts"].astype("int64") // 1000).tolist()

    opens = win["open"].astype(float).tolist()
    highs = win["high"].astype(float).tolist()
    lows = win["low"].astype(float).tolist()
    closes = win["close"].astype(float).tolist()
    candles = [
        {"time": t, "open": o, "high": h, "low": lo, "close": c}
        for t, o, h, lo, c in zip(times, opens, highs, lows, closes)
    ]

    left = float(row["left"])
    right = float(row["right"])
    n = len(candles)
    i0 = max(0, min(n - 1, int(round(left * (n - 1)))))
    i1 = max(0, min(n - 1, int(round(right * (n - 1)))))
    if i1 < i0:
        i0, i1 = i1, i0
    tip_i = max(0, min(n - 1, int(round(0.95 * (n - 1)))))

    return {
        "stem": row["stem"],
        "symbol": sym,
        "csv": csv_path.name,
        "png_rel": preview_rel,
        "n_bars": n,
        "candles": candles,
        "sma20": sma(times, closes, 20),
        "sma60": sma(times, closes, 60),
        "hardneg": {
            "from": candles[i0]["time"],
            "to": candles[i1]["time"],
            "left": left,
            "right": right,
            "bars_after": float(row.get("bars_after") or 0),
            "bars_span": float(row.get("bars_span") or 0),
        },
        "tip_zone": {
            "from": candles[tip_i]["time"],
            "to": candles[-1]["time"],
        },
        "aftermath": {
            "from": candles[i1]["time"],
            "to": candles[tip_i]["time"] if tip_i > i1 else candles[i1]["time"],
        },
    }


def html_page(charts: list[dict]) -> str:
    payload = json.dumps(charts, ensure_ascii=False)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<title>H-FE-1 hardneg LWC batch (offline)</title>
<style>
body {{ font-family: ui-sans-serif, system-ui; margin: 16px; background:#f8fafc; color:#111; }}
h1 {{ font-size: 18px; }}
.note {{ font-size: 13px; color:#444; max-width: 1100px; line-height:1.45; }}
.toolbar {{ display:flex; gap:12px; flex-wrap:wrap; margin:12px 0; align-items:center; }}
.toolbar label {{ font-size:13px; background:#fff; border:1px solid #e5e7eb; padding:6px 10px; border-radius:6px; }}
.row {{ display:grid; grid-template-columns: 320px 1fr; gap: 12px; margin: 18px 0; }}
.card {{ background:#fff; border:1px solid #e5e7eb; border-radius:8px; padding:10px; }}
.card h2 {{ font-size:14px; margin:0 0 8px; }}
.chart {{ height: 300px; width:100%; min-width:0; }}
img {{ width:100%; height:auto; background:#fff; border:1px solid #eee; }}
.err {{ color:#b91c1c; font-size:13px; }}
@media (max-width:900px) {{ .row {{ grid-template-columns:1fr; }} }}
</style>
<script src="{VENDOR_REL}"></script>
</head>
<body>
<h1>Hardneg 批量 LWC（密集窗 / tip 带 / 后文）</h1>
<p class="note">
离线调试页：青色=硬负中段框近似时间带；品红=框右缘→tip 的<strong>后文</strong>；
黄带=右缘 tip 区（x≥0.95）。勾选切换图层。
<strong>禁止</strong>把 LWC 截图喂 YOLO（MA 语义与 cv2 六均线渲染不同）。
不改后端、不抢 MPS。
</p>
<div class="toolbar">
  <label><input type="checkbox" id="tog-hardneg" checked/> 硬负密集窗</label>
  <label><input type="checkbox" id="tog-aftermath" checked/> 硬负后文</label>
  <label><input type="checkbox" id="tog-tip" checked/> tip 右缘带</label>
  <label><input type="checkbox" id="tog-ma" checked/> MA20/60（仅显示）</label>
</div>
<div id="root"></div>
<script>
const charts = {payload};
const state = {{ hardneg:true, aftermath:true, tip:true, ma:true }};
const painted = [];
const root = document.getElementById('root');

for (const ch of charts) {{
  const wrap = document.createElement('div');
  wrap.className = 'row';
  if (ch.error) {{
    wrap.innerHTML = `<div class="card"><h2>${{ch.stem}}</h2><p class="err">${{ch.error}}</p></div>`;
    root.appendChild(wrap);
    continue;
  }}
  const img = ch.png_rel
    ? `<img src="../../${{ch.png_rel}}" alt="cv2 preview"/>`
    : `<p class="note">无 PNG 预览</p>`;
  wrap.innerHTML = `
    <div class="card">
      <h2>cv2 · ${{ch.stem}}</h2>
      ${{img}}
      <p class="note">after≈${{ch.hardneg.bars_after.toFixed(0)}} · span≈${{ch.hardneg.bars_span.toFixed(1)}} · ${{ch.csv}}</p>
    </div>
    <div class="card">
      <h2>LWC · ${{ch.symbol}} (${{ch.n_bars}} bars)</h2>
      <div class="chart" id="c-${{ch.stem}}"></div>
    </div>`;
  root.appendChild(wrap);
}}

function areaSeries(chart, colorTop, colorBot, line) {{
  return chart.addAreaSeries({{
    topColor: colorTop, bottomColor: colorBot, lineColor: line, lineWidth: 1,
    priceLineVisible: false, lastValueVisible: false,
    autoscaleInfoProvider: () => null,
  }});
}}

function paint(ch) {{
  if (ch.error) return;
  const el = document.getElementById('c-' + ch.stem);
  const chart = LightweightCharts.createChart(el, {{
    layout: {{ background: {{ color: '#ffffff' }}, textColor: '#6b7280' }},
    grid: {{ vertLines: {{ color: '#eef1f6' }}, horzLines: {{ color: '#eef1f6' }} }},
    rightPriceScale: {{ borderColor: '#e5e7eb' }},
    timeScale: {{ borderColor: '#e5e7eb', timeVisible: true }},
    width: el.clientWidth, height: 300,
  }});
  const candle = chart.addCandlestickSeries({{
    upColor:'#26a69a', downColor:'#ef5350', borderVisible:false,
    wickUpColor:'#26a69a', wickDownColor:'#ef5350'
  }});
  candle.setData(ch.candles);
  const mid = ch.candles.reduce((a,b)=>a+b.close,0)/ch.candles.length;
  const s20 = chart.addLineSeries({{ color:'#2563eb', lineWidth:1 }});
  const s60 = chart.addLineSeries({{ color:'#f59e0b', lineWidth:1 }});
  s20.setData(ch.sma20); s60.setData(ch.sma60);
  const hn = areaSeries(chart, 'rgba(34,211,238,0.35)', 'rgba(34,211,238,0.05)', 'rgba(34,211,238,0.85)');
  hn.setData([{{time: ch.hardneg.from, value: mid}}, {{time: ch.hardneg.to, value: mid}}]);
  const af = areaSeries(chart, 'rgba(236,72,153,0.28)', 'rgba(236,72,153,0.04)', 'rgba(236,72,153,0.8)');
  af.setData([{{time: ch.aftermath.from, value: mid*0.998}}, {{time: ch.aftermath.to, value: mid*0.998}}]);
  const tip = areaSeries(chart, 'rgba(250,204,21,0.35)', 'rgba(250,204,21,0.05)', 'rgba(234,179,8,0.9)');
  tip.setData([{{time: ch.tip_zone.from, value: mid*1.002}}, {{time: ch.tip_zone.to, value: mid*1.002}}]);
  chart.timeScale().fitContent();
  new ResizeObserver(() => chart.applyOptions({{ width: el.clientWidth }})).observe(el);
  painted.push({{ s20, s60, hn, af, tip }});
}}

function applyToggles() {{
  for (const p of painted) {{
    p.hn.applyOptions({{ visible: state.hardneg }});
    p.af.applyOptions({{ visible: state.aftermath }});
    p.tip.applyOptions({{ visible: state.tip }});
    p.s20.applyOptions({{ visible: state.ma }});
    p.s60.applyOptions({{ visible: state.ma }});
  }}
}}

charts.forEach(paint);
document.getElementById('tog-hardneg').onchange = e => {{ state.hardneg = e.target.checked; applyToggles(); }};
document.getElementById('tog-aftermath').onchange = e => {{ state.aftermath = e.target.checked; applyToggles(); }};
document.getElementById('tog-tip').onchange = e => {{ state.tip = e.target.checked; applyToggles(); }};
document.getElementById('tog-ma').onchange = e => {{ state.ma = e.target.checked; applyToggles(); }};
</script>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    ap.add_argument("--candidates", type=Path, default=DEFAULT_CSV)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()

    preview_by_stem: dict[str, str] = {}
    rows: list[dict] = []
    if args.summary.is_file():
        summary = json.loads(args.summary.read_text())
        for p in summary.get("previews") or []:
            preview_by_stem[p["stem"]] = p["preview"]
            # match full geometry from candidates csv when possible
        import csv as csvmod

        by_stem: dict[str, dict] = {}
        with args.candidates.open() as f:
            for r in csvmod.DictReader(f):
                by_stem.setdefault(r["stem"], r)
        for p in summary.get("previews") or []:
            base = by_stem.get(p["stem"], {})
            merged = {**base, **p}
            rows.append(merged)
    else:
        raise SystemExit(f"missing summary: {args.summary}")

    rows = rows[: args.limit]
    charts = []
    for r in rows:
        ch = build_chart(r, preview_by_stem.get(r["stem"]))
        if ch:
            charts.append(ch)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    html_path = args.out_dir / "index.html"
    html_path.write_text(html_page(charts), encoding="utf-8")
    meta = {
        "n": len(charts),
        "stems": [c.get("stem") for c in charts],
        "errors": [c for c in charts if c.get("error")],
        "html": str(html_path.relative_to(PROJECT)),
        "gpu_used": False,
        "note": "Layer toggles: hardneg / aftermath / tip / MA. Not for YOLO training screenshots.",
    }
    (args.out_dir / "manifest.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(f"wrote {html_path} n={len(charts)} errs={len(meta['errors'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

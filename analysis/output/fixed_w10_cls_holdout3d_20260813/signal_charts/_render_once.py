#!/usr/bin/env python3
"""Entry charts for all 126 deduped holdout signals. Left shows future path; W10 stays causal."""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from html import escape
from pathlib import Path
from tempfile import NamedTemporaryFile

import cv2
import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

YOYO = Path("/Users/zhangzc/yoyo-trading")
sys.path.insert(0, str(YOYO))

from yoyo.data.indicators import add_indicators
from yoyo.datasets.gold_render import render_context
from yoyo.datasets.legacy_gold_migration.renderer import render_w10
from yoyo.layers.l1_detection.data import ALL_MA_COLS, add_mas

SNAPSHOT = Path(
    "/Users/zhangzc/fable-trading/analysis/output/"
    "yoyo_r3a_v3gold_ft_r1_holdout_losers3d_20260813/kline_snapshot"
)
DEDUP = Path(
    "/Users/zhangzc/fable-trading/analysis/output/"
    "fixed_w10_cls_holdout3d_20260813/signals_dedup.jsonl"
)
IMG_DIR = Path(
    "/Users/zhangzc/fable-trading/analysis/output/"
    "fixed_w10_cls_holdout3d_20260813/signal_charts"
)
HTML_PATH = YOYO / "reports/fixed_w10_core4_confirm1_v1/signal_charts/index.html"
YOYO_IMG_PREFIX = (
    "../../../../fable-trading/analysis/output/"
    "fixed_w10_cls_holdout3d_20260813/signal_charts/"
)
SCAN_END = pd.Timestamp("2026-08-13T12:00:00Z")
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")
Y_PAD = 0.05
PRE_BARS = 50
MAX_POST = 72
TP_ATR = 5.0
SL_ATR = 2.0
WORKERS = 8


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open() as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_snapshot(symbol: str) -> pd.DataFrame:
    path = SNAPSHOT / f"{symbol}.csv"
    raw = pd.read_csv(path)
    frame = raw.copy()
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = (
        frame.dropna(subset=["open_time", "open", "high", "low", "close"])
        .drop_duplicates("open_time", keep="last")
        .sort_values("open_time")
        .reset_index(drop=True)
    )
    if (frame["open_time"] < HOLDOUT_START).any():
        raise ValueError(f"{symbol} mixed pre-holdout")
    if pd.Timestamp(frame["open_time"].iloc[-1]) > SCAN_END:
        frame = frame.loc[frame["open_time"] <= SCAN_END].reset_index(drop=True)
    return add_indicators(add_mas(frame))


def outcome_label(row: dict) -> str:
    status = str(row.get("status") or "")
    outcome = str(row.get("outcome") or "")
    if status == "open" or outcome in ("running", "no_entry"):
        return "未平"
    mapping = {"tp": "TP", "sl": "SL", "timeout": "timeout"}
    return mapping.get(outcome, outcome)


def maker_text(row: dict) -> str:
    val = row.get("net_maker")
    if val is None:
        return "maker 净盈亏 未平（未记）"
    num = float(val)
    sign = "+" if num >= 0 else ""
    return f"maker 净盈亏 {sign}{num:.4f}（{sign}{num * 1e4:.1f} bp）"


def find_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in (
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ):
        p = Path(path)
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size, index=0)
            except OSError:
                continue
    return ImageFont.load_default()


def mark_confirm(img: np.ndarray, tf) -> np.ndarray:
    out = img.copy()
    x = tf.x_at(9)
    cv2.line(out, (x, tf.top), (x, tf.top + tf.plot_h), (40, 40, 198), 2, cv2.LINE_AA)
    return out


def context_post_bars(row: dict, frame: pd.DataFrame) -> int:
    decision_i = int(row["decision_i"])
    entry_i = int(row["entry_i"])
    avail = max(0, len(frame) - 1 - decision_i)
    exit_offset = int(row.get("exit_offset") or 0)
    closed = str(row.get("status") or "") == "closed" and exit_offset > 0
    if closed:
        want = min(MAX_POST, exit_offset)
    else:
        want = min(MAX_POST, max(0, entry_i + MAX_POST - decision_i))
    return min(want, avail)


def render_pair(row: dict, frame: pd.DataFrame) -> np.ndarray:
    decision_i = int(row["decision_i"])
    start_i = int(row["window_start_i"])
    end_i = int(row["window_end_i"])
    entry_i = int(row["entry_i"])
    if end_i != decision_i or end_i - start_i + 1 != 10:
        raise ValueError(f"bad window {row['symbol']} {start_i}-{end_i} dec={decision_i}")
    causal = frame.iloc[: decision_i + 1].reset_index(drop=True)
    times = pd.to_datetime(causal["open_time"], utc=True)
    last = times.iloc[-1]
    decision_ts = pd.Timestamp(row["decision_time"])
    if last != decision_ts:
        raise ValueError(f"causal tip {last} != decision {decision_ts}")
    if (times > decision_ts).any():
        raise ValueError("future bars in causal prefix")
    window = causal.iloc[start_i : end_i + 1].reset_index(drop=True)
    if window[list(ALL_MA_COLS)].isna().any().any():
        raise ValueError("NA MA in W10")
    w10, tf = render_w10(window, y_pad_frac=Y_PAD, overlay=False)
    w10 = mark_confirm(w10, tf)
    post = context_post_bars(row, frame)
    entry_px = float(row["entry_price"])
    atr = float(row["atr14"])
    tp_px = entry_px - TP_ATR * atr
    sl_px = entry_px + SL_ATR * atr
    exit_offset = int(row.get("exit_offset") or 0)
    exit_bar = entry_i + exit_offset - 1 if str(row.get("status") or "") == "closed" and exit_offset > 0 else None
    with NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        render_context(
            frame,
            decision_i,
            decision_i - 4,
            decision_i,
            pre_bars=PRE_BARS,
            post_bars=post,
            out_path=tmp_path,
            entry_bar=entry_i,
            exit_bar=exit_bar,
            entry_price=entry_px,
            tp_price=tp_px,
            sl_price=sl_px,
        )
        ctx = cv2.imdecode(np.frombuffer(tmp_path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
    finally:
        tmp_path.unlink(missing_ok=True)
    if ctx is None:
        raise RuntimeError("context decode failed")
    target_h = 742

    def resize_h(img: np.ndarray) -> np.ndarray:
        h, w = img.shape[:2]
        nw = max(1, int(round(w * (target_h / h))))
        return cv2.resize(img, (nw, target_h), interpolation=cv2.INTER_AREA)

    left = resize_h(ctx)
    right = resize_h(w10)
    pair = np.hstack([left, right])
    coin = str(row["symbol"]).replace("_USDT_SWAP", "")
    entry = str(row.get("entry_time") or "")
    p = float(row["p_signal"])
    line1 = f"{coin}  进场 {entry}  p(SIGNAL)={p:.4f}  出场 {outcome_label(row)}"
    line2 = (
        f"{maker_text(row)}    左=上下文+进场后走势（post={post}，回看允许未来K）  "
        f"右=模型输入 W10 overlay=False（红线=confirm；无未来K）"
    )
    header_h = 78
    canvas = np.full((pair.shape[0] + header_h, pair.shape[1], 3), 255, dtype=np.uint8)
    canvas[header_h:] = pair
    rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
    pil = Image.fromarray(rgb)
    draw = ImageDraw.Draw(pil)
    font = find_font(22)
    font2 = find_font(16)
    draw.text((16, 10), line1, fill=(20, 20, 20), font=font)
    draw.text((16, 44), line2, fill=(70, 70, 70), font=font2)
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


def item_from_row(idx: int, row: dict, fname: str) -> dict:
    coin = str(row["symbol"]).replace("_USDT_SWAP", "")
    entry = str(row.get("entry_time") or "")
    label = outcome_label(row)
    caption = (
        f"{coin}  进场 {entry}  p={float(row['p_signal']):.4f}  "
        f"出场 {label}  {maker_text(row)}"
    )
    return {
        "idx": idx,
        "coin": coin,
        "entry": entry,
        "p": round(float(row["p_signal"]), 4),
        "out": {"TP": "tp", "SL": "sl", "timeout": "timeout", "未平": "open"}[label],
        "label": label,
        "file": fname,
        "caption": caption,
    }


def render_symbol_batch(symbol: str, payloads: list[dict]) -> list[dict]:
    frame = load_snapshot(symbol)
    items = []
    coin = symbol.replace("_USDT_SWAP", "")
    for payload in payloads:
        row = payload["row"]
        idx = int(payload["idx"])
        img = render_pair(row, frame)
        fname = (
            f"{idx:03d}_{coin}_"
            f"{pd.Timestamp(row['entry_time']).strftime('%Y%m%dT%H%M%SZ')}.png"
        )
        out = IMG_DIR / fname
        if not cv2.imwrite(str(out), img):
            raise RuntimeError(f"imwrite failed {out}")
        items.append(item_from_row(idx, row, fname))
        print(f"wrote {fname} {img.shape}", flush=True)
    return items


def write_html(items: list[dict], prefix: str, dest: Path) -> None:
    coins = sorted({it["coin"] for it in items})
    n = len(items)
    n_tp = sum(1 for it in items if it["out"] == "tp")
    n_sl = sum(1 for it in items if it["out"] == "sl")
    n_to = sum(1 for it in items if it["out"] == "timeout")
    n_open = sum(1 for it in items if it["out"] == "open")
    payload = json.dumps(items, ensure_ascii=False)
    coin_opts = "\n".join(
        f'<option value="{escape(c)}">{escape(c)}</option>' for c in coins
    )
    html = f"""<!DOCTYPE html>
<html lang="zh-Hans">
<head>
<meta charset="utf-8">
<title>W10 开仓信号图（126 笔全量）</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 0; background: #f6f6f4; color: #222; }}
.wrap {{ display: grid; grid-template-columns: 280px 1fr; min-height: 100vh; }}
.side {{ background: #fff; border-right: 1px solid #ddd; overflow: auto; max-height: 100vh; }}
.side h2 {{ font-size: 14px; margin: 12px 16px 8px; }}
.side button.row {{ display: block; width: calc(100% - 16px); margin: 0 8px 2px; text-align: left;
  border: 0; background: transparent; padding: 6px 8px; cursor: pointer; font-size: 12px; border-radius: 4px; }}
.side button.row:hover, .side button.row.on {{ background: #e8eef8; }}
.main {{ padding: 16px 20px 32px; }}
h1 {{ font-size: 20px; margin: 0 0 8px; }}
.warn {{ background: #fff3cd; border: 1px solid #e6c35c; padding: 8px 12px; font-size: 13px; }}
.bar {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 12px 0; }}
.bar button, .bar select {{ padding: 6px 10px; }}
.meta {{ color: #444; font-size: 14px; margin: 8px 0; }}
#view {{ width: 100%; max-width: 1700px; height: auto; background: #fff; }}
.hint {{ color: #666; font-size: 12px; }}
.empty {{ padding: 40px; color: #888; }}
</style>
</head>
<body>
<div class="wrap">
<nav class="side">
<h2>按时间 <span id="sideCount"></span></h2>
<div id="list"></div>
</nav>
<div class="main">
<h1>开仓信号图（去重后 {n} 笔）</h1>
<p class="warn">这不是成功回测，也未 promote。默认阈值 / 合同 TP/SL 没有改。左图带进场后未来 K（回看用）。右图 W10 仍是模型输入，无未来 K。
出场：TP {n_tp} / SL {n_sl} / timeout {n_to} / 未平 {n_open}。← → 翻页。</p>
<div class="bar">
  出场
  <button data-out="all">全部</button>
  <button data-out="tp">TP</button>
  <button data-out="sl">SL</button>
  <button data-out="timeout">timeout</button>
  <button data-out="open">未平</button>
  币
  <select id="coin"><option value="all">全部币</option>{coin_opts}</select>
  <button id="prev">上一张</button>
  <strong id="counter"></strong>
  <button id="next">下一张</button>
</div>
<p class="meta" id="caption"></p>
<p class="hint">左=上下文+未来走势（红竖=进场，绿/红横=TP/SL，点线=出场）　右=固定 W10 overlay=False，红线=confirm，无未来K</p>
<img id="view" alt="signal chart">
<p class="empty" id="empty" hidden>没有符合筛选的记录。</p>
</div>
</div>
<script>
const ITEMS = {payload};
const PREFIX = {json.dumps(prefix)};
let outF = "all";
let coinF = "all";
let i = 0;
let filtered = ITEMS.slice();
const img = document.getElementById("view");
const cap = document.getElementById("caption");
const counter = document.getElementById("counter");
const list = document.getElementById("list");
const empty = document.getElementById("empty");
function apply() {{
  filtered = ITEMS.filter(x =>
    (outF === "all" || x.out === outF) &&
    (coinF === "all" || x.coin === coinF)
  );
  if (i >= filtered.length) i = Math.max(0, filtered.length - 1);
  renderList();
  show();
}}
function renderList() {{
  list.innerHTML = filtered.map((x, k) =>
    `<button class="row" data-i="${{k}}">${{x.idx}}. ${{x.coin}} ${{x.entry.slice(0,16).replace("T"," ")}} ${{x.label}} p=${{x.p.toFixed(2)}}</button>`
  ).join("");
  document.getElementById("sideCount").textContent = "(" + filtered.length + ")";
}}
function show() {{
  const none = filtered.length === 0;
  empty.hidden = !none;
  img.hidden = none;
  cap.hidden = none;
  if (none) {{ counter.textContent = "0 / 0"; return; }}
  const x = filtered[i];
  img.src = PREFIX + x.file;
  cap.textContent = x.caption;
  counter.textContent = (i + 1) + " / " + filtered.length;
  list.querySelectorAll(".row").forEach((el, k) => el.classList.toggle("on", k === i));
  const on = list.querySelector(".row.on");
  if (on) on.scrollIntoView({{ block: "nearest" }});
}}
document.querySelectorAll("[data-out]").forEach(btn => {{
  btn.onclick = () => {{ outF = btn.dataset.out; i = 0; apply(); }};
}});
document.getElementById("coin").onchange = (e) => {{ coinF = e.target.value; i = 0; apply(); }};
document.getElementById("prev").onclick = () => {{ if (!filtered.length) return; i = (i - 1 + filtered.length) % filtered.length; show(); }};
document.getElementById("next").onclick = () => {{ if (!filtered.length) return; i = (i + 1) % filtered.length; show(); }};
list.onclick = (e) => {{
  const b = e.target.closest("[data-i]");
  if (!b) return;
  i = Number(b.dataset.i);
  show();
}};
document.addEventListener("keydown", (e) => {{
  if (e.key === "ArrowLeft") document.getElementById("prev").click();
  if (e.key === "ArrowRight") document.getElementById("next").click();
}});
apply();
</script>
</body>
</html>
"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(html, encoding="utf-8")


def main() -> int:
    dedup = load_jsonl(DEDUP)
    if len(dedup) != 126:
        raise SystemExit(f"expected 126 dedup rows, got {len(dedup)}")
    ordered = sorted(dedup, key=lambda r: (str(r["entry_time"]), r["symbol"]))
    groups: dict[str, list[dict]] = defaultdict(list)
    for idx, row in enumerate(ordered, start=1):
        groups[str(row["symbol"])].append({"idx": idx, "row": row})
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    items: list[dict] = []
    with ProcessPoolExecutor(max_workers=WORKERS) as pool:
        futs = [
            pool.submit(render_symbol_batch, symbol, payloads)
            for symbol, payloads in groups.items()
        ]
        for fut in as_completed(futs):
            items.extend(fut.result())
    items.sort(key=lambda it: int(it["idx"]))
    if len(items) != 126:
        raise SystemExit(f"rendered {len(items)}, expected 126")
    write_html(items, YOYO_IMG_PREFIX, HTML_PATH)
    write_html(items, "", IMG_DIR / "index.html")
    print("html", HTML_PATH)
    print("html2", IMG_DIR / "index.html")
    print("pngs", len(items))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

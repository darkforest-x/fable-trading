"""Outcome-selected diagnostic panoramas for frozen altcoin trend ledgers.

The selector only draws the baseline 34/9, focus12/band0.10 arm. It shows each
SOPH/USELESS 1H/4H group's maximum, minimum and lower-median net-R observation,
deduplicated by event identity; high-volatility baseline groups show max/min.
A locked arm may add a baseline positive winner absent at the exact same
symbol/timeframe/signal/side. This is not evidence that the arm missed the
whole trend; it may enter at another time. Nonfinite returns are never filled.

Every selection explicitly uses outcomes and is unsuitable for estimating
success rates. These figures are not YOLO inputs, model confirmations, training
data or strategy revisions. No model is run. Prices after decision close are
shaded and written only below out_dir/future_only. Source and image hashes are
recorded. Input OHLC is re-aggregated with the research runner's complete UTC
15m aggregation; event clock, next-open price and baseline release must agree.

The display starts up to 100 bars before the release. It extends at least
toward 72 later bars, or the actual ledger exit plus 12 bars, bounded by the
source, 30 calendar days plus 12 bars, and 500 total candles. If the full exit
does not fit, the chart and receipt say so. Net R/return always describe the
saved full event, separately from its evidenced MFE; neither is a promise or
an assertion of live execution. Static transaction cost is 20bp, no funding.
"""
from __future__ import annotations

import argparse
import hashlib
from html import escape
import json
from pathlib import Path
import re
import subprocess

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Rectangle
from matplotlib.ticker import FuncFormatter
import numpy as np
import pandas as pd

from yoyo.data.altcoin_features import IMACDParams, MA_COLUMNS, build_altcoin_features
from yoyo.evaluation.imacd_formation_research import aggregate


ROOT = Path(__file__).resolve().parents[2]
MAX_CANDLES = 500
NOTICE = ("这些案例按事后净R选择最高、最低及中位，不是随机样本，不能据此估算胜率。"
          "浅蓝区为信号确认后行情，决策当时不可见；本图不运行YOLO，也不是模型输入。"
          "图示为候选事件的历史模拟路径，不代表所有候选都能在单仓组合中入场。"
          "净收益已扣静态往返20bp，未含资金费、真实滑点或市场冲击；MFE不是已实现利润。")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def select_examples(events: pd.DataFrame, phase: str | None = "recent_test", selection: dict | None = None) -> list[dict]:
    """Deterministic outcome-based examples; sparse/negative groups remain honest."""
    required = {"event_id", "symbol", "minutes", "fold", "cohort", "arm", "signal_i", "side", "net_r"}
    if not required.issubset(events.columns):
        raise ValueError("event ledger lacks selection columns")
    rows = events.copy(deep=True)
    rows["net_r"] = pd.to_numeric(rows.net_r, errors="raise")
    rows = rows.loc[np.isfinite(rows.net_r) & rows.minutes.isin([60, 240])]
    if phase is not None:
        rows = rows.loc[rows.fold.eq(phase)]
    base = rows.loc[rows.arm.eq("base")]
    selected: dict[tuple, dict] = {}

    def choose(group, role, rank):
        if group.empty:
            return
        ranked = group.sort_values(["net_r", "event_id", "fold"], kind="mergesort")
        row = ranked.iloc[rank].to_dict()
        key = (str(row["event_id"]), str(row["fold"]))
        if key not in selected:
            selected[key] = dict(event=row, roles=[], selected_with_outcome=True)
        if role not in selected[key]["roles"]:
            selected[key]["roles"].append(role)

    illustrations = base.loc[base.symbol.isin(["SOPH", "USELESS"]) & base.cohort.eq("owner_illustration")]
    for (symbol, minutes), group in illustrations.groupby(["symbol", "minutes"], sort=True):
        choose(group, f"{symbol} {minutes}m · 本组净R最高", -1)
        choose(group, f"{symbol} {minutes}m · 本组净R最低", 0)
        choose(group, f"{symbol} {minutes}m · 本组净R中位", (len(group) - 1) // 2)
    highvol = base.loc[base.cohort.eq("high_vol") & ~base.symbol.isin(["BTC", "ETH", "SOPH", "USELESS"])]
    choices = {int(row["minutes"]): row.get("selected_arm") for row in (selection or {}).get("selections", [])}
    for minutes, group in highvol.groupby("minutes", sort=True):
        choose(group, f"高波动组 {minutes}m · 基准净R最高", -1)
        choose(group, f"高波动组 {minutes}m · 基准净R最低", 0)
        arm = choices.get(int(minutes))
        if arm and arm != "base":
            comparison = rows.loc[rows.cohort.eq("high_vol") & rows.minutes.eq(minutes) & rows.arm.eq(arm)]
            # An absent arm's entire output is not evidence of rejecting events.
            if not comparison.empty:
                identity = lambda r: (r.symbol, r.fold, int(r.signal_i), int(r.side))
                retained = {identity(row) for row in comparison.itertuples()}
                missed = group.loc[[identity(row) not in retained for row in group.itertuples()]]
                choose(missed.loc[missed.net_r > 0], f"{arm} 未保留同一启动时点 · 不等于漏掉整段行情", -1)
    return list(selected.values())


def _font() -> str:
    candidates = ("/System/Library/Fonts/STHeiti Medium.ttc", "/System/Library/Fonts/Hiragino Sans GB.ttc",
                  "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
                  "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    for path in candidates:
        if Path(path).exists():
            font_manager.fontManager.addfont(path)
            return font_manager.FontProperties(fname=path).get_name()
    raise RuntimeError("An installed CJK font is required to render readable Chinese labels")


def _integer(event: dict, name: str) -> int:
    value = event[name]
    if isinstance(value, bool) or not np.isfinite(float(value)) or int(value) != float(value):
        raise ValueError(name + " must be an integer")
    return int(value)


def _clock(value) -> pd.Timestamp:
    value = pd.Timestamp(value)
    if pd.isna(value) or value.tzinfo is None or value.utcoffset().total_seconds() != 0:
        raise ValueError("event clocks require explicit UTC timestamps")
    return value.tz_convert("UTC")


def _flag(value) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if str(value) not in ("True", "False", "true", "false"):
        raise ValueError("ledger flag must be a boolean")
    return str(value).lower() == "true"


def render_event(event: dict, bars: pd.DataFrame, path: Path, roles: list[str], *, features=None) -> dict:
    """Render one verified baseline event without changing its saved economics."""
    if event.get("arm") != "base":
        raise ValueError("Only the baseline may use this baseline-parameter renderer")
    minutes, signal, entry, exit_i, side = (_integer(event, name) for name in ("minutes", "signal_i", "entry_i", "exit_i", "side"))
    if minutes not in (60, 240) or side not in (-1, 1) or not 0 <= signal < entry <= exit_i < len(bars) or entry != signal + 1:
        raise ValueError("event timeframe, direction or positions are invalid")
    step = pd.Timedelta(minutes=minutes)
    if len(bars) < 2 or not bars.index.to_series().diff().iloc[1:].eq(step).all():
        raise ValueError("figure history must use the ledger's continuous timeframe")
    f = build_altcoin_features(bars, IMACDParams()) if features is None else features
    if not f.index.equals(bars.index) or int(f.release_side.iloc[signal]) != side:
        raise ValueError("baseline release does not reproduce the saved event")
    decision = _clock(event["decision_close_time"])
    if bars.index[signal] + step != decision or _clock(event["entry_time"]) != bars.index[entry]:
        raise ValueError("event clock differs from the supplied history origin")
    for name in ("entry_price", "exit_price", "initial_stop", "initial_risk", "net_bp", "net_r", "mfe_r"):
        if not np.isfinite(float(event[name])):
            raise ValueError(name + " must be a finite ledger value")
    entry_price, exit_price = float(event["entry_price"]), float(event["exit_price"])
    stop, risk = float(event["initial_stop"]), float(event["initial_risk"])
    if risk <= 0 or not np.isclose(entry_price, bars.open.iloc[entry], rtol=1e-11, atol=0):
        raise ValueError("ledger next-open price or initial risk differs")
    if not np.isclose(stop, entry_price - side * risk, rtol=1e-11, atol=0):
        raise ValueError("initial stop and frozen risk disagree")
    expected_net = side * (exit_price / entry_price - 1) - .002
    if (not np.isclose(float(event["net_bp"]), expected_net * 10_000, rtol=1e-9, atol=1e-9)
            or not np.isclose(float(event["net_r"]), expected_net / (risk / entry_price), rtol=1e-9, atol=1e-9)):
        raise ValueError("ledger returns disagree with its fills, frozen risk or static 20bp cost")
    if float(event["mfe_r"]) < 0:
        raise ValueError("ledger MFE cannot be negative")
    at_open = _flag(event["exit_at_open"])
    exit_time = _clock(event["exit_time"])
    if exit_time != bars.index[exit_i] + (pd.Timedelta(0) if at_open else step):
        raise ValueError("exit clock differs from the saved bar/timing contract")
    start = max(0, signal - 100)
    desired_end = max(signal + 72, exit_i + 12)
    end = min(len(bars) - 1, desired_end, signal + (30 * 24 * 60 // minutes) + 12, start + MAX_CANDLES - 1)
    exit_visible = exit_i <= end
    view, fv = bars.iloc[start:end+1], f.iloc[start:end+1]
    x = np.arange(len(view))
    sx, ex = signal - start, entry - start - 0.5
    future_boundary = sx + 0.5
    up, down = "#008b7b", "#d04d65"
    direction_color = up if side == 1 else down
    target = entry_price + side * 3 * risk
    chinese_font = _font()
    with plt.rc_context({"font.family": chinese_font, "axes.unicode_minus": False, "font.size": 10}):
        fig, (price_ax, osc) = plt.subplots(2, 1, figsize=(20, 8.5), sharex=True,
            gridspec_kw={"height_ratios": [3.3, 1]}, layout="constrained")
        fig.set_facecolor("#f7f9fb")
        for ax in (price_ax, osc):
            ax.set_facecolor("#ffffff")
            ax.spines[["top", "right"]].set_visible(False)
            ax.spines[["bottom", "left"]].set_color("#c4cdd7")
            ax.grid(axis="y", color="#e1e6ec", linewidth=.6)
            ax.set_axisbelow(True)
            ax.axvspan(future_boundary, len(view)-.5, color="#e3edf8", alpha=.7, zorder=0)
            ax.axvline(sx, color=direction_color, linewidth=1.25, alpha=.8)
            ax.axvline(future_boundary, color="#667d9a", linewidth=.7, linestyle=":")
        for i, candle in enumerate(view.itertuples()):
            color = up if candle.close >= candle.open else down
            price_ax.vlines(i, candle.low, candle.high, color=color, linewidth=.6)
            body = max(abs(candle.close-candle.open), candle.open * 0.000005)
            price_ax.add_patch(Rectangle((i-.32, min(candle.open,candle.close)), .64, body, facecolor=color, edgecolor="none"))
        for name, color in zip(MA_COLUMNS, ["#228f8a", "#85bbb6", "#5c80b8", "#a7bce0", "#5c6674", "#a7afb9"]):
            price_ax.plot(x, fv[name], color=color, linewidth=.9, label=name.upper())
        setup = f.focus_start_i.iloc[signal]
        if not pd.isna(setup):
            setup = max(start, int(setup)) - start
            zone_high, zone_low = float(f.release_zone_high.iloc[signal]), float(f.release_zone_low.iloc[signal])
            price_ax.add_patch(Rectangle((setup-.5, zone_low), sx-setup, zone_high-zone_low,
                facecolor="#e8b54b", edgecolor="#c69b43", linestyle=":", linewidth=.8, alpha=.13))
            osc.axvspan(setup-.5, sx-.5, color="#e8b54b", alpha=.16)
        right = min(exit_i - start + .5, len(view)-.5)
        price_ax.hlines(stop, ex, right, color=down, linewidth=.9, linestyle="--", label=f"初始止损 {stop:.7g}")
        if target > 0:
            price_ax.hlines(target, ex, right, color=direction_color, linewidth=.8, linestyle=":", label="3R观察位 · 非强制止盈")
        price_ax.scatter([sx], [bars.low.iloc[signal] if side == 1 else bars.high.iloc[signal]], marker="^" if side == 1 else "v", s=65, color=direction_color, zorder=6)
        price_ax.scatter([ex], [entry_price], marker="o", s=25, color="#304b70", zorder=6)
        price_ax.annotate(f"收盘启动 → 次开盘 {entry_price:.7g}", (ex,entry_price), xytext=(12,23), textcoords="offset points",
            fontsize=9, color="#304b70", arrowprops={"arrowstyle":"-", "color":"#304b70"},
            bbox={"boxstyle":"round,pad=.3", "fc":"white", "ec":"#d5dfe9", "alpha":.95})
        timing = str(event.get("exit_timing", ""))
        if exit_visible:
            xx = exit_i-start + (-.5 if at_open else 0 if timing == "intrabar_unknown" else .5)
            price_ax.scatter([xx], [exit_price], marker="X", s=55, color="#6d478d", zorder=6)
            suffix = " · 本K内时刻未知" if timing == "intrabar_unknown" else ""
            price_ax.annotate(f"账本退出 {exit_price:.7g}{suffix}", (xx,exit_price), xytext=(12,-32), textcoords="offset points",
                color="#6d478d", fontsize=9, arrowprops={"arrowstyle":"-", "color":"#6d478d"},
                bbox={"boxstyle":"round,pad=.3", "fc":"white", "ec":"#ddd1e5", "alpha":.95})
        else:
            price_ax.text(.99,.02,f"实际退出在图外：{exit_time:%Y-%m-%d %H:%M} UTC / {exit_price:.7g}",
                transform=price_ax.transAxes,ha="right",fontsize=10,color="#6d478d",bbox={"fc":"white","ec":"#ddd1e5","alpha":.95})
        price_ax.yaxis.set_major_formatter(FuncFormatter(lambda value,_: f"{value:,.7g}"))
        price_ax.legend(loc="upper left", ncol=4, fontsize=8, framealpha=.94)
        price_ax.text(.99,1.01,"浅蓝：确认后的事后行情，决策当时不可见",transform=price_ax.transAxes,ha="right",fontsize=9,color="#526f94")
        osc.plot(x,fv.md,color="#4f7cc6",linewidth=1.3,label="IMACD 34")
        osc.plot(x,fv.sb,color="#c78a2c",linewidth=1.15,label="信号线 9")
        osc.axhline(0,color="#637484",linewidth=.9,label="零轴")
        osc.scatter([sx],[f.md.iloc[signal]],s=24,color=direction_color,zorder=6)
        osc.legend(loc="upper left",ncol=3,fontsize=8)
        ticks = np.unique(np.linspace(0,len(view)-1,9).astype(int))
        osc.set_xticks(ticks,[view.index[i].strftime("%m-%d\n%H:%M") for i in ticks])
        osc.set_xlim(-1,len(view))
        osc.set_xlabel(f"UTC K线开盘时间 · 前文 {signal-start} 根 / 后续 {end-signal} 根 · " + ("含完整退出" if exit_visible else "窗口截断，未展示完整退出"))
        side_text = "多头" if side == 1 else "空头"
        settled = "边界强制标记" if _flag(event.get("censored",False)) else "按规则已退出"
        fig.suptitle(f"{event['symbol']} · {minutes//60}H · {side_text} · {decision:%Y-%m-%d %H:%M} UTC\n"
            f"静态20bp后净收益 {float(event['net_bp'])/100:+.2f}% / {float(event['net_r']):+.2f}R  |  "
            f"账本MFE {float(event['mfe_r']):.2f}R（非已实现）  |  {settled} · {event.get('exit_reason','')}\n"
            + "；".join(roles), fontsize=11.5,fontweight="bold")
        path.parent.mkdir(parents=True,exist_ok=True)
        fig.savefig(path,dpi=150)
        plt.close(fig)
    return dict(event_id=str(event["event_id"]),symbol=str(event["symbol"]),minutes=minutes,fold=str(event["fold"]),
        roles=roles,side=side,arm="base",signal_i=signal,entry_i=entry,exit_i=exit_i,
        decision_close_time=decision.isoformat(),future_first_open=decision.isoformat(),exit_time=exit_time.isoformat(),
        first_open=view.index[0].isoformat(),last_close=(view.index[-1]+step).isoformat(),
        history_bars=signal-start,future_bars=end-signal,candle_count=len(view),exit_visible=bool(exit_visible),
        window_capped=bool(end<desired_end),net_bp=float(event["net_bp"]),net_r=float(event["net_r"]),mfe_r=float(event["mfe_r"]),
        initial_stop=stop,initial_risk=risk,selected_with_outcome=True,static_cost_bp=20,
        censored=_flag(event.get("censored",False)),
        portfolio_selected=_flag(event["portfolio_selected"]) if "portfolio_selected" in event else None,
        source_font=chinese_font,png_sha256=sha(path),training_eligible=False,production_eligible=False,
        original_model_input=False,future_visible_at_decision=False)


def _history_bars(manifest: dict, symbol: str, minutes: int) -> tuple[pd.DataFrame,dict]:
    matches=[row for row in manifest.get("symbols",[]) if row.get("symbol")==symbol]
    if len(matches)!=1 or matches[0].get("status")!="complete":
        raise ValueError("missing/ambiguous complete history for "+symbol)
    row=matches[0]
    path=Path(row["output_path"])
    if not path.is_absolute(): path=ROOT/path
    digest=sha(path)
    if digest!=row["output_sha256"]: raise ValueError("history SHA mismatch for "+symbol)
    raw=pd.read_csv(path,usecols=["ts","open","high","low","close","volume"])
    raw.index=pd.DatetimeIndex(pd.to_datetime(raw.pop("ts"),unit="ms",utc=True))
    cutoff=_clock(manifest["end_exclusive"])
    if len(raw) and (raw.index[-1]+pd.Timedelta(minutes=15)>cutoff):
        raise ValueError("history exceeds its frozen right endpoint")
    return aggregate(raw,minutes),dict(path=str(path),sha256=digest)


def build_gallery(events_path: Path, history_path: Path, out_dir: Path, *, phase="recent_test", selection_path: Path | None=None) -> dict:
    """Create a portable HTML gallery and provenance manifest, no model inference."""
    events_path,history_path,out_dir=Path(events_path),Path(history_path),Path(out_dir)
    if out_dir.exists(): raise ValueError("figure output already exists; preserve the prior delivery")
    events=pd.read_csv(events_path)
    history=json.loads(history_path.read_text())
    selection=json.loads(Path(selection_path).read_text()) if selection_path else None
    chosen=select_examples(events,phase,selection)
    out_dir.mkdir(parents=True)
    (out_dir/"future_only").mkdir()
    cache={};receipts=[];input_sources={}
    for i,item in enumerate(chosen):
        event=item["event"];key=(event["symbol"],int(event["minutes"]))
        if key not in cache:
            bars,identity=_history_bars(history,*key)
            cache[key]=(bars,build_altcoin_features(bars,IMACDParams()))
            input_sources[f"{key[0]}_{key[1]}"]=identity
        bars,features=cache[key]
        slug=re.sub(r"[^A-Za-z0-9_-]","_",str(event["event_id"]))
        relative=f"future_only/{i+1:02d}_{slug}.png"
        receipt=render_event(event,bars,out_dir/relative,item["roles"],features=features)
        receipt["path"]=relative;receipts.append(receipt)
    cards=[]
    for r in receipts:
        cards.append(f'<article><div class="caption"><h2>{escape(r["symbol"])} · {r["minutes"]//60}H</h2>'
          f'<span class="metric">{r["net_r"]:+.2f}R · {r["net_bp"]/100:+.2f}%</span></div>'
          f'<p>{escape("；".join(r["roles"]))}</p><a href="{r["path"]}" target="_blank" rel="noopener">'
          f'<img src="{r["path"]}" loading="lazy" alt="{escape(r["symbol"])} 基准启动与后续走势"></a>'
          f'<p class="meta">确认 {escape(r["decision_close_time"])} · 后续 {r["future_bars"]} 根 · '
          +('含完整退出' if r['exit_visible'] else '实际退出在图外，图示窗口截断')+'</p></article>')
    html='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SPIKE · 山寨趋势全景复盘</title><style>
:root{color-scheme:light dark;--bg:#f4f7fa;--surface:#fff;--ink:#243646;--muted:#637487;--line:#dbe3eb;--accent:#087f71}
@media(prefers-color-scheme:dark){:root{--bg:#0d151e;--surface:#16212c;--ink:#e4ecf3;--muted:#a0afbf;--line:#304052;--accent:#5ad9bf}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:1600px;margin:auto;padding:38px 24px}header{margin-bottom:28px}h1{font-size:32px;margin:0}.eyebrow{letter-spacing:.14em;color:var(--accent);font-size:12px}h2{font-size:21px;margin:0}.notice{max-width:1100px;color:var(--muted)}article{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:20px;margin:22px 0;overflow:hidden}.caption{display:flex;justify-content:space-between;gap:15px;align-items:center}.metric{font-variant-numeric:tabular-nums;color:var(--accent)}p{margin:8px 0 16px}img{display:block;width:100%;border-radius:8px}a{color:var(--accent)}.meta{font-size:12px;color:var(--muted);margin-bottom:0}
</style><main><header><div class="eyebrow">SPIKE / RESEARCH</div><h1>山寨趋势 · 全景复盘</h1>'''
    html+=f'<p class="notice">{escape(NOTICE)}</p><p>时段：{escape(phase or "全部已提供时段")} · {len(receipts)} 个去重案例 · 点击图像查看原尺寸</p></header>'
    html+=''.join(cards) if cards else '<article>当前时段没有符合固定选例条件的有限收益记录；没有补零或换成其他时段。</article>'
    html+='</main></html>'
    (out_dir/'index.html').write_text(html)
    result=dict(schema_version=1,phase=phase,notice=NOTICE,selected_count=len(receipts),examples=receipts,
        input_sha256=dict(events=sha(events_path),history_manifest=sha(history_path),selection=sha(Path(selection_path)) if selection_path else None),
        source_histories=input_sources,builder_sha256=sha(Path(__file__)),gallery_sha256=sha(out_dir/'index.html'),
        selected_with_outcomes=True,training_eligible=False,production_eligible=False,model_inference=False)
    (out_dir/'manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--events',type=Path,required=True)
    parser.add_argument('--history',type=Path,required=True)
    parser.add_argument('--out-dir',type=Path,required=True)
    parser.add_argument('--phase',default='recent_test')
    parser.add_argument('--selection',type=Path)
    args=parser.parse_args(argv)
    relative=str(Path(__file__).resolve().relative_to(ROOT))
    if subprocess.check_output(['git','show','HEAD:'+relative],cwd=ROOT)!=Path(__file__).read_bytes():
        raise ValueError('Commit the figure builder before rendering real results')
    result=build_gallery(args.events,args.history,args.out_dir,phase=args.phase,selection_path=args.selection)
    print(json.dumps(dict(selected_count=result['selected_count'],out_dir=str(args.out_dir)),ensure_ascii=False))
    return 0


if __name__=='__main__':
    raise SystemExit(main())

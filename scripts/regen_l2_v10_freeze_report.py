#!/usr/bin/env python3
"""Regenerate analysis/output/l2_v10_reg_freeze_20260731/report.html (charts + CN glossary).

死命令（仓库根目录执行）:
  cd /Users/zhangzc/fable-trading && PYTHONPATH=. python3 scripts/regen_l2_v10_freeze_report.py && open analysis/output/l2_v10_reg_freeze_20260731/report.html

全链路（重建数据集 + 重训冻结 + 报告，会改 ACTIVE）:
  cd /Users/zhangzc/fable-trading && PYTHONPATH=. python3 scripts/build_judgment_yolo_swap_v10.py && PYTHONPATH=. python3 scripts/freeze_model.py --yolo-v10-pool --write-active --date 20260731 && PYTHONPATH=. python3 scripts/regen_l2_v10_freeze_report.py && open analysis/output/l2_v10_reg_freeze_20260731/report.html
"""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import lightgbm as lgb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.stats import spearmanr

from src.costs import SWAP_MAKER
from src.judgment.features import FEATURE_COLUMNS
from src.judgment.train import load_splits

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / "analysis/output/l2_v10_reg_freeze_20260731"
META = PROJECT / "models/frozen_tp5_sl2_swap_yolo_v10_reg_20260731.json"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    if not META.exists():
        raise SystemExit(f"missing {META}; run freeze_model.py --yolo-v10-pool first")
    meta = json.loads(META.read_text())
    thr = float(meta["threshold_val_q90"])
    best = int(meta.get("best_iteration") or 1)
    booster = lgb.Booster(model_file=str(PROJECT / meta["model_path"]))
    train, val, _ = load_splits(PROJECT / meta["dataset_path"], horizon_bars=72)
    wf = meta.get("walkforward") or {}
    folds = wf.get("folds") or []

    plt.rcParams.update(
        {
            "font.sans-serif": ["PingFang SC", "Arial Unicode MS", "Heiti SC", "STHeiti", "DejaVu Sans"],
            "axes.unicode_minus": False,
            "figure.facecolor": "white",
            "axes.facecolor": "#fafcfb",
            "axes.edgecolor": "#d5e0d8",
            "axes.labelcolor": "#2a372e",
            "xtick.color": "#536057",
            "ytick.color": "#536057",
            "grid.color": "#e8efe9",
            "grid.linestyle": "--",
            "grid.alpha": 0.9,
        }
    )

    def fig_to_b64(fig) -> str:
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=140, bbox_inches="tight", facecolor="white")
        plt.close(fig)
        return base64.b64encode(buf.getvalue()).decode("ascii")

    def pack(df):
        sc = booster.predict(df[list(FEATURE_COLUMNS)], num_iteration=best if best > 0 else None)
        y = df["realized_ret"].to_numpy(float)
        k = max(1, len(df) // 10)
        top = np.argsort(-sc)[:k]
        above = sc >= thr
        return {
            "sc": sc,
            "y": y,
            "rho": float(spearmanr(sc, y).statistic),
            "top_net": float((y[top] - SWAP_MAKER).mean()),
            "pool_mean": float(y.mean()),
            "pass_rate": float(above.mean()),
            "pass_net": float((y[above] - SWAP_MAKER).mean()) if above.any() else float("nan"),
            "n": len(df),
            "n_top": int(k),
            "top_win": float((y[top] > 0).mean()),
        }

    tr, va = pack(train), pack(val)

    fig, ax = plt.subplots(figsize=(8.2, 3.6))
    if folds:
        labels = [f"第{i+1}折\n{f['val_start'][5:]}" for i, f in enumerate(folds)]
        nets = [float(f["top_decile_net_maker"]) * 1e4 for f in folds]
        colors = ["#059669" if x >= 0 else "#dc2626" for x in nets]
        ax.bar(labels, nets, color=colors, width=0.62, edgecolor="none")
        ax.axhline(0, color="#94a3b8", lw=1)
        ax.set_ylabel("顶十分位净收益 maker (bp)")
        ax.set_title("五折 walkforward（滚动时间验证）· 顶 10% 净收益")
        ax.grid(True, axis="y")
        for i, v in enumerate(nets):
            ax.text(
                i,
                v + (3 if v >= 0 else -8),
                f"{v:+.0f}",
                ha="center",
                va="bottom" if v >= 0 else "top",
                fontsize=9,
                color="#334155",
            )
    chart_wf = fig_to_b64(fig)

    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    cats = ["池均值\npool mean", "过阈值净\nabove thr", "顶10%净\ntop-decile"]
    x = np.arange(len(cats))
    w = 0.36
    tr_vals = [tr["pool_mean"] * 1e4, tr["pass_net"] * 1e4, tr["top_net"] * 1e4]
    va_vals = [va["pool_mean"] * 1e4, va["pass_net"] * 1e4, va["top_net"] * 1e4]
    ax.bar(x - w / 2, tr_vals, w, label="train 训练集", color="#93c5a4")
    ax.bar(x + w / 2, va_vals, w, label="val 验证集", color="#278f50")
    ax.axhline(0, color="#94a3b8", lw=1)
    ax.set_xticks(x)
    ax.set_xticklabels(cats)
    ax.set_ylabel("bp / 笔（基点）")
    ax.set_title("train vs val · maker 净口径（已扣 0.06% 往返）")
    ax.legend(frameon=False)
    ax.grid(True, axis="y")
    chart_tv = fig_to_b64(fig)

    fig, ax = plt.subplots(figsize=(7.2, 3.8))
    rng = np.random.default_rng(42)
    idx = rng.choice(len(va["sc"]), size=min(1200, len(va["sc"])), replace=False)
    ax.scatter(va["sc"][idx], va["y"][idx] * 1e4, s=10, alpha=0.35, c="#278f50", edgecolors="none", label="全部 val 点")
    ax.axvline(thr, color="#f59e0b", ls="--", lw=1.4, label=f"阈值 thr q90={thr:.5f}")
    ax.axhline(0, color="#94a3b8", lw=0.8)
    k = max(1, len(va["sc"]) // 10)
    top = np.argsort(-va["sc"])[:k]
    ax.scatter(va["sc"][top], va["y"][top] * 1e4, s=14, alpha=0.55, c="#0f766e", edgecolors="none", label="顶十分位 top-decile")
    ax.set_xlabel("模型分数 score")
    ax.set_ylabel("实现收益 realized ret (bp)")
    ax.set_title(f"val 验证集 · 分数 vs 收益 · Spearman 相关 ρ={va['rho']:.3f}")
    ax.legend(frameon=False, fontsize=9)
    ax.grid(True)
    chart_sc = fig_to_b64(fig)

    fig, ax = plt.subplots(figsize=(7.2, 3.4))
    if folds:
        wins = [float(f["top_decile_win"]) * 100 for f in folds]
        ax.plot(range(1, len(wins) + 1), wins, "o-", color="#278f50", lw=2, ms=8)
        ax.axhline(50, color="#94a3b8", ls=":", lw=1, label="50% 基准")
        ax.set_xticks(range(1, len(wins) + 1))
        ax.set_xlabel("折 fold")
        ax.set_ylabel("顶 10% 胜率 win rate %")
        ax.set_title("walkforward 顶档胜率（top-decile win rate）")
        ax.set_ylim(0, 100)
        ax.legend(frameon=False)
        ax.grid(True)
    chart_wr = fig_to_b64(fig)

    for name, b64 in [
        ("walkforward.png", chart_wf),
        ("train_val_compare.png", chart_tv),
        ("val_score_scatter.png", chart_sc),
        ("walkforward_winrate.png", chart_wr),
    ]:
        (OUT / name).write_bytes(base64.b64decode(b64))

    glossary = [
        ("train / training set", "训练集", "按时间切分的前段样本，用来拟合模型"),
        ("val / validation set", "验证集", "按时间切分的后段样本，只评不训"),
        ("n / sample count", "样本笔数", "该切分内的信号条数（非交易账户笔）"),
        ("holdout", "留置集", "≥2026-05-04 的样本；本报告未读、未评"),
        ("ACTIVE", "当前生效指针", "models/ACTIVE 指向的判断层冻结模型"),
        ("PREV / rollback", "上一版 / 回滚", "切走前的 ACTIVE（现为 v11）"),
        ("score", "模型分数", "LightGBM 对实现收益的预测值，越大越偏看多收益"),
        ("threshold / thr / q90", "阈值 / 九十分位门", "验证集分数的 90% 分位；score≥thr 为「过阈值」"),
        ("top-decile / top 10%", "顶十分位", "按 score 排序最高的 10% 样本"),
        ("pass rate / above thr", "过阈值比例", "score≥thr 的样本占比"),
        ("Spearman ρ / rho", "斯皮尔曼等级相关", "分数与收益排序是否同向（−1~+1）"),
        ("realized ret", "实现收益", "本池为 net_barrier_taker（障碍+成本后）"),
        ("pool mean", "池均值", "该切分全部样本的平均实现收益"),
        ("net maker", "净收益(maker)", "再扣 0.06% 往返 maker 成本后的均值"),
        ("bp / basis point", "基点", "1 bp = 0.01% = 万分之一"),
        ("win rate", "胜率", "收益>0 的比例"),
        ("walkforward", "滚动前向验证", "多段时间折上重复评估，测稳定性"),
        ("fold", "折", "walkforward 中的一段验证窗"),
        ("best_iteration", "最佳迭代轮数", "早停选中的树棵数；本冻结为 1，偏异常"),
        ("PF / profit factor", "盈亏比", "总盈利/总亏损（看板 tip-replay 用语）"),
    ]
    gl_rows = "".join(
        f"<tr><td class='mono'>{en}</td><td><strong>{zh}</strong></td><td class='muted'>{desc}</td></tr>"
        for en, zh, desc in glossary
    )
    fold_rows = ""
    for i, f in enumerate(folds):
        net_bp = float(f["top_decile_net_maker"]) * 1e4
        cls = "pos" if net_bp >= 0 else "neg"
        fold_rows += (
            f"<tr><td>第{i+1}折 fold {i+1}</td><td>{f['val_start']}</td>"
            f"<td class='num'>{f['n_val']}</td><td class='num'>{f['spearman']:+.3f}</td>"
            f"<td class='num {cls}'>{net_bp:+.1f} bp</td>"
            f"<td class='num'>{100*float(f['top_decile_win']):.1f}%</td></tr>"
        )

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>L2 v10 回归冻结 · 回测分析报告</title>
<style>
:root{{--bg:#f4f7f5;--card:#fff;--text:#152019;--muted:#6a756d;--border:rgba(47,158,89,.16);--accent:#278f50;--accent-soft:rgba(47,158,89,.08);--warn:#a76f18;--warn-bg:#fff8eb;--neg:#c23b3b;--pos:#1f8a4c;--mono:ui-monospace,Menlo,Consolas,monospace}}
*{{box-sizing:border-box}}body{{margin:0;font:15px/1.55 system-ui,-apple-system,"PingFang SC",sans-serif;color:var(--text);background:radial-gradient(900px 360px at 8% -10%,rgba(47,158,89,.12),transparent 55%),var(--bg)}}
.wrap{{max-width:1040px;margin:0 auto;padding:28px 18px 64px}}.eyebrow{{color:var(--accent);font-family:var(--mono);font-size:11px;letter-spacing:.16em;text-transform:uppercase;margin-bottom:8px}}
h1{{font-size:1.65rem;letter-spacing:-.03em;margin:0 0 8px;font-weight:650}}.lede{{color:var(--muted);margin:0 0 18px;max-width:72ch}}
.banner{{border:1px solid rgba(167,111,24,.28);background:var(--warn-bg);border-radius:14px;padding:12px 14px;margin:0 0 20px;color:#6b4e12;font-size:13.5px}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(158px,1fr));gap:10px;margin:0 0 22px}}
.tile{{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:12px 14px;box-shadow:0 8px 24px rgba(35,72,45,.05)}}
.tile span{{display:block;color:var(--muted);font-size:11px;line-height:1.35}}.tile b{{display:block;margin-top:4px;font-family:var(--mono);font-size:1.02rem;font-weight:600;word-break:break-word}}
.pos{{color:var(--pos)}}.neg{{color:var(--neg)}}.warn{{color:var(--warn)}}
.panel{{background:var(--card);border:1px solid var(--border);border-radius:18px;padding:16px 18px 18px;margin:0 0 16px;box-shadow:0 10px 28px rgba(35,72,45,.045)}}
.panel h2{{margin:0 0 10px;font-size:1rem;font-weight:650}}.panel h2 .unit{{color:var(--muted);font-weight:500;font-size:12px;margin-left:6px}}
.chart{{width:100%;height:auto;border-radius:12px;border:1px solid #e8efe9;background:#fff;display:block;margin:8px 0 4px}}
.grid-2{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}@media(max-width:760px){{.grid-2{{grid-template-columns:1fr}}}}
table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{border-bottom:1px solid #eef2ef;padding:8px;text-align:left;vertical-align:top}}th{{color:var(--muted);font-size:11px;background:#f7faf8}}
.num{{text-align:right;font-variant-numeric:tabular-nums;font-family:var(--mono);font-size:12.5px}}.mono{{font-family:var(--mono);font-size:12px}}.muted{{color:var(--muted);font-size:13px}}
code{{font-family:var(--mono);font-size:12px;background:var(--accent-soft);color:#1d5c35;padding:1px 6px;border-radius:6px}}
pre{{margin:0;padding:12px 14px;overflow:auto;background:#0f1612;color:#d7ebe0;border-radius:12px;font:12px/1.5 var(--mono);white-space:pre-wrap}}
.tag{{display:inline-block;font-family:var(--mono);font-size:10px;padding:2px 7px;border-radius:999px;border:1px solid var(--border);color:var(--accent);background:var(--accent-soft)}}
.tag.warn{{color:var(--warn);border-color:rgba(167,111,24,.3);background:#fff8eb}}.foot{{margin-top:22px;color:var(--muted);font-size:12.5px}}ul{{margin:6px 0 0 1.1rem}}
</style></head><body>
<div class="wrap">
<div class="eyebrow">FABLE · 判断层冻结报告 JUDGMENT FREEZE</div>
<h1>L2 v10 池回归冻结 · 回测分析</h1>
<p class="lede">
  L1 检测 = <code>owner_short_star_v10</code>；L2 判断 = v10 池 LightGBM <strong>回归 regression</strong>（预测实现收益）。
  磁贴「样本 train/val」= <strong>训练集 / 验证集</strong>笔数 = <strong>{tr['n']} / {va['n']}</strong>。
</p>
<div class="banner"><strong>诚实声明：</strong>
walkforward（滚动时间验证）不全为正；best_iteration（最佳迭代轮数）=1；
阈值 thr q90≈{thr:.6g}，验证集过 thr 比例 {va['pass_rate']*100:.1f}%（门几乎失效）。
holdout 留置集未读。看板 tip-replay PF（盈亏比）0.784 是 v16 旧 holdout，不是本组合。
</div>
<div class="tiles">
<div class="tile"><span>ACTIVE 当前生效模型</span><b>v10_reg_20260731</b></div>
<div class="tile"><span>样本 train / val<br>训练集笔数 / 验证集笔数</span><b>{tr['n']} / {va['n']}</b></div>
<div class="tile"><span>阈值 thr (q90)<br>分数九十分位门</span><b class="warn">{thr:.5f}</b></div>
<div class="tile"><span>val 顶10% 净 maker<br>验证集顶十分位净收益</span><b class="pos">{va['top_net']*1e4:+.1f} bp</b></div>
<div class="tile"><span>val 过 thr 净 maker<br>验证集过阈值净收益</span><b class="neg">{va['pass_net']*1e4:+.1f} bp</b></div>
<div class="tile"><span>val Spearman ρ<br>验证集等级相关系数</span><b>{va['rho']:.3f}</b></div>
<div class="tile"><span>walkforward 全正<br>五折顶档是否都盈利</span><b class="neg">否 false</b></div>
<div class="tile"><span>PREV 回滚版</span><b>v11_reg_20260718</b></div>
</div>
<div class="panel"><h2>指标中英对照 glossary</h2>
<table><thead><tr><th>英文 English</th><th>中文</th><th>含义</th></tr></thead><tbody>{gl_rows}</tbody></table></div>
<div class="panel"><h2>图1 · walkforward 顶十分位净收益 top-decile net (maker bp)</h2>
<img class="chart" src="data:image/png;base64,{chart_wf}" alt="wf"><p class="muted">bp 基点=0.01%。有正有负→时间不稳定。</p></div>
<div class="grid-2">
<div class="panel"><h2>图2 · train vs val 训练对比验证</h2>
<img class="chart" src="data:image/png;base64,{chart_tv}" alt="tv"><p class="muted">信号在顶10%；过 thr 在 val 仍为负。</p></div>
<div class="panel"><h2>图3 · walkforward 胜率 win rate</h2>
<img class="chart" src="data:image/png;base64,{chart_wr}" alt="wr"><p class="muted">虚线=50% 基准。</p></div>
</div>
<div class="panel"><h2>图4 · val 分数 score vs 实现收益 realized ret</h2>
<img class="chart" src="data:image/png;base64,{chart_sc}" alt="sc"><p class="muted">橙线=阈值 thr。过 thr {va['pass_rate']*100:.0f}%。深绿=顶十分位。</p></div>
<div class="panel"><h2>单切数值 train / val</h2>
<table><thead><tr>
<th>切分 split</th><th class="num">n 笔数</th><th class="num">Spearman ρ</th>
<th class="num">池均值 pool bp</th><th class="num">顶10%净 maker</th>
<th class="num">顶胜率 win%</th><th class="num">过 thr % pass</th><th class="num">过 thr 净 maker</th>
</tr></thead><tbody>
<tr><td>train 训练集</td><td class="num">{tr['n']}</td><td class="num">{tr['rho']:.3f}</td>
<td class="num neg">{tr['pool_mean']*1e4:+.1f}</td><td class="num pos">{tr['top_net']*1e4:+.1f}</td>
<td class="num">{tr['top_win']*100:.1f}%</td><td class="num">{tr['pass_rate']*100:.1f}%</td>
<td class="num">{tr['pass_net']*1e4:+.1f}</td></tr>
<tr><td><strong>val 验证集</strong></td><td class="num"><strong>{va['n']}</strong></td><td class="num"><strong>{va['rho']:.3f}</strong></td>
<td class="num neg"><strong>{va['pool_mean']*1e4:+.1f}</strong></td><td class="num pos"><strong>{va['top_net']*1e4:+.1f}</strong></td>
<td class="num"><strong>{va['top_win']*100:.1f}%</strong></td><td class="num warn"><strong>{va['pass_rate']*100:.1f}%</strong></td>
<td class="num neg"><strong>{va['pass_net']*1e4:+.1f}</strong></td></tr>
</tbody></table>
<p class="muted" style="margin-top:10px">样本 train/val = <strong>{tr['n']} / {va['n']}</strong>
（训练集 / 验证集；合计 {tr['n']+va['n']} 笔）。顶10% 笔数 train {tr['n_top']} · val {va['n_top']}。</p>
</div>
<div class="panel"><h2>五折 walkforward folds</h2>
<table><thead><tr><th>折 fold</th><th>val 起点</th><th class="num">n_val 验证笔数</th>
<th class="num">ρ Spearman</th><th class="num">顶10%净 maker</th><th class="num">胜率 win rate</th></tr></thead>
<tbody>{fold_rows}</tbody></table>
<p class="muted" style="margin-top:10px">rho_mean 相关均值={wf.get('rho_mean')} · rho_min 最差相关={wf.get('rho_min')} ·
all_folds_net_positive 五折顶档都盈利=<strong class="neg">{wf.get('all_folds_net_positive')} 否</strong></p>
</div>
<div class="panel"><h2>死命令 dead one-liner</h2>
<pre># 仅重出 HTML+指标图（不重渲 200 交易图）
cd /Users/zhangzc/fable-trading && PYTHONPATH=. python3 scripts/regen_l2_v10_freeze_report.py && open analysis/output/l2_v10_reg_freeze_20260731/report.html

# 重渲 200 张交易样图 + 报告
cd /Users/zhangzc/fable-trading && PYTHONPATH=. python3 scripts/build_l2_v10_freeze_sample_gallery.py && PYTHONPATH=. python3 scripts/regen_l2_v10_freeze_report.py && open analysis/output/l2_v10_reg_freeze_20260731/report.html

# 全链路：建表 + 冻结写 ACTIVE + 200 样图 + 报告
cd /Users/zhangzc/fable-trading && PYTHONPATH=. python3 scripts/build_judgment_yolo_swap_v10.py && PYTHONPATH=. python3 scripts/freeze_model.py --yolo-v10-pool --write-active --date 20260731 && PYTHONPATH=. python3 scripts/build_l2_v10_freeze_sample_gallery.py && PYTHONPATH=. python3 scripts/regen_l2_v10_freeze_report.py && open analysis/output/l2_v10_reg_freeze_20260731/report.html

# 回滚 L2 → v11
echo 'models/frozen_tp5_sl2_swap_yolo_v11_reg_20260718.txt' > models/ACTIVE</pre>
</div>
<div class="panel"><h2>风险 risks</h2>
<ul>
<li>walkforward 滚动验证不全为正</li>
<li>best_iteration 最佳轮数=1</li>
<li>q90 阈值门过宽（过 thr 比例 {va['pass_rate']*100:.0f}%）</li>
<li>tip-replay 看板仍是 v16 旧 holdout</li>
</ul></div>
<p class="foot">生成自 scripts/regen_l2_v10_freeze_report.py · 路径 analysis/output/l2_v10_reg_freeze_20260731/report.html</p>
</div></body></html>
"""
    # Inject trade-sample gallery if samples_manifest.json exists (from
    # scripts/build_l2_v10_freeze_sample_gallery.py).
    man_path = OUT / "samples_manifest.json"
    if man_path.is_file():
        try:
            man = json.loads(man_path.read_text(encoding="utf-8"))
            cards = man.get("cards") or []
            gal_cards = []
            for c in cards:
                cls = "pos" if float(c.get("ret_pct") or 0) > 0 else (
                    "neg" if float(c.get("ret_pct") or 0) < 0 else ""
                )
                thr_s = "过 thr ✓" if c.get("passed") else "未过 thr"
                gal_cards.append(
                    "<figure class=\"sample-card\">"
                    f"<img src=\"{c.get('file')}\" alt=\"{c.get('symbol')} {c.get('signal_time')}\" loading=\"lazy\">"
                    "<figcaption>"
                    f"<b>#{c.get('i')}</b> <span class=\"mono\">{c.get('symbol')}</span><br>"
                    f"{str(c.get('signal_time') or '')[:16]} UTC · {c.get('split')}<br>"
                    f"<span class=\"tag\">{c.get('band')}</span><br>"
                    f"score 分数 <span class=\"mono\">{c.get('score')}</span> · {thr_s}<br>"
                    f"realized 实现收益 <span class=\"{cls}\">{float(c.get('ret_pct') or 0):+.2f}%</span>"
                    f" · label={c.get('label')}"
                    "</figcaption></figure>"
                )
            gallery = (
                "<div class=\"panel\">"
                f"<h2>抽样 {len(cards)} 张交易图 sample trade charts "
                "<span class=\"unit\">因果窗 200 根 · 与训练同渲染</span></h2>"
                "<p class=\"muted\">抽样：val 上 100 顶十分位 + 50 底十分位 + 50 中间；"
                "每张 = 信号 bar 及之前 200 根 15m + SMA/EMA 20/60/120。"
                "图目录 <code>samples/</code> · 清单 <code>samples_manifest.json</code>。"
                "重生样图："
                "<code>PYTHONPATH=. python3 scripts/build_l2_v10_freeze_sample_gallery.py</code>"
                "</p>"
                f"<div class=\"sample-grid\">{''.join(gal_cards)}</div></div>"
            )
            css_extra = (
                ".sample-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));"
                "gap:12px;margin-top:12px}"
                ".sample-card{margin:0;border:1px solid var(--border);border-radius:12px;"
                "overflow:hidden;background:#fff}"
                ".sample-card img{width:100%;height:auto;display:block;background:#f8faf9}"
                ".sample-card figcaption{padding:8px 10px;font-size:12px;line-height:1.45;color:#334}"
                ".sample-card .tag{display:inline-block;font-size:10px;padding:1px 6px;"
                "border-radius:999px;background:var(--accent-soft);color:var(--accent);margin:2px 0}"
            )
            if "</style>" in html:
                html = html.replace("</style>", css_extra + "\n</style>", 1)
            if '<p class="foot">' in html:
                html = html.replace('<p class="foot">', gallery + "\n<p class=\"foot\">", 1)
            else:
                html = html.replace("</div></body>", gallery + "\n</div></body>", 1)
            print(f"gallery injected n={len(cards)}")
        except Exception as exc:  # noqa: BLE001
            print(f"gallery skip: {exc}")

    (OUT / "report.html").write_text(html, encoding="utf-8")
    print(f"wrote {OUT / 'report.html'}")
    print(f"train/val n = {tr['n']} / {va['n']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

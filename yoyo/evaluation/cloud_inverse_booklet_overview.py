"""Publish the frozen OKX inverse account summary and human-readable chart bundle."""
import argparse
from datetime import datetime, timedelta
import csv
import gzip
import io
import json
import math
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / "experiments/active/exp-cloud-close-alt-season-delivery-20261109-v1"
SOURCE = EXPERIMENT / "opposite"
STATIC = ROOT / "yoyo/monitor/static/strategy-booklet"
BOOK = STATIC / "cloud-v17"
HISTORY = STATIC / "cloud-v17-history"


def read_csv(path):
    with gzip.open(path, "rt", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def publish_shared_history(hours, trades, overview):
    """Project frozen hourly valuations and funded parent trades into the shared reader."""
    from yoyo.evaluation.strategy_booklet_metrics import histogram

    metrics = overview["metrics"]
    first_end = datetime.fromisoformat(hours[0]["hour_end_utc"])
    previous_end = first_end - timedelta(hours=1)
    points = [[int(previous_end.timestamp() * 1000), 1000., 0., 0., 1000.]]
    month_ends = {}
    peak = 1000.
    for row in hours:
        end = datetime.fromisoformat(row["hour_end_utc"])
        if end - previous_end != timedelta(hours=1):
            raise ValueError("Non-contiguous valuation clock")
        previous_end = end
        worst = 0.
        for stage in ("open_nav", "open_exit_nav", "post_trim_nav", "nav"):
            nav = float(row[stage])
            peak = max(peak, nav)
            worst = min(worst, 100 * (nav / peak - 1))
        # The hour ending at 00:00 closes the previous UTC day/month.
        month_ends[(end - timedelta(microseconds=1)).strftime("%Y-%m")] = nav
        point = [int(end.timestamp()*1000), round(nav, 6), round((nav/1000-1)*100, 6),
                 round(worst, 6), 1000.]
        if len(points) >= 2 and point[1:] == points[-1][1:] == points[-2][1:]:
            points[-1] = point
        else:
            points.append(point)
    if not math.isclose(-min(p[3] for p in points), metrics["dd_mark_pct"], abs_tol=1e-6):
        raise ValueError("Marked drawdown differs from the frozen ledger")
    monthly, previous = [], 1000.
    for period, last in month_ends.items():
        monthly.append([period, 100 * (last / previous - 1)])
        previous = last
    returns = [100 * float(t["net_return"]) for t in trades]
    durations = [(int(t["exit_bar_ms"]) - int(t["entry_ms"])) / 3600000 for t in trades]
    sides = []
    for side, title in [("1","做多"),("-1","做空")]:
        amounts = [float(t["cash_pnl"]) for t in trades if t["side"] == side]
        sides.append([title, len(amounts), 100 * sum(p > 0 for p in amounts) / len(amounts), sum(amounts)])
    final = metrics["final_usdt"]
    profile = dict(schema=1, meta=dict(track="live-cloud-v17", basis="hourly_marked",
                       note="OKX USDT 永续 · 小时收盘净值含浮盈亏，已扣实际成交手续费。"),
                   refilled=False, points=points, monthly=monthly,
                   yearly=[[str(y["year"]), y["pct"]] for y in overview["years"]],
                   summary=dict(start=1000., end=final, pnl=final-1000, return_pct=100*(final/1000-1),
                                max_drawdown_pct=metrics["dd_mark_pct"], win_pct=metrics["win_pct"],
                                trades=len(trades), profit_factor=overview["grossProfitFactor"],
                                profit_factor_state="finite", cash_added=0., avg_win=overview["avgWin"],
                                avg_loss=-overview["avgLoss"]),
                   distribution=histogram(returns, [-math.inf,-30,-10,-3,-1,0,1,3,10,30,math.inf],
                       ["<−30","−30~−10","−10~−3","−3~−1","−1~0","0~1","1~3","3~10","10~30","≥30"]),
                   holding=histogram(durations, [-math.inf,1,4,12,24,72,math.inf],
                       ["<1h","1–4h","4–12h","12–24h","1–3天","≥3天"]), sides=sides,
                   recent=[[t["symbol"], int(t["entry_ms"]), int(t["exit_bar_ms"]),
                            "多" if t["side"] == "1" else "空", float(t["cash_pnl"]), 100*float(t["net_return"])]
                           for t in sorted(trades, key=lambda t: int(t["exit_bar_ms"]), reverse=True)[:50]])
    (STATIC / "live-cloud-v17-history.json").write_text(json.dumps(profile, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False) + "\n")


def publish_handbook(overview):
    from markdown_it import MarkdownIt
    guide = json.loads((BOOK / "guide.json").read_text())
    url = "https://spikeok.com/?book=live-cloud-v17"
    def link(chapter, trade=None):
        return url + "&chapter=" + chapter + ("&trade=" + trade if trade else "") + "#signals?pane=reclaim"
    def table(headers, rows):
        return "\n".join("| " + " | ".join(r) + " |" for r in [headers, ["---"]*len(headers), *rows])
    parts = ["# " + guide["title"], "## 概览", guide["intro"],
             " · ".join(f'{f["label"]} {f["value"]}{f["unit"]}' for f in guide["facts"]),
             "## 历史回测", "1,000U 起步，期末 147,590.50U。1,252 笔资金单，胜率 21.01%，按金额计盈亏比 5.35，最大账面回撤 57.83%。",
             "![历史资金路径与回撤](equity.png)",
             table(["年份（UTC）", "期末净值（U）", "本段收益率"],
                   [[str(y["year"]), f'{y["end"]:,.2f}', f'{y["pct"]:+.2f}%'] for y in overview["years"]]),
             *guide["risk"], "## 执行规则", table(**guide["direction"])]
    parts.extend(f'**{i}. {step["title"]}。** {step["text"]}' for i, step in enumerate(guide["flow"], 1))
    parts.append("### 三笔交易回放")
    parts.extend(f'**{c["title"]}。** {c["text"]} [查看开仓图]({link("history", c["id"])})' for c in guide["cases"])
    parts.extend(["## 前向资金", guide["sources"][2], "[查看独立模拟账户](" + link("forward") + ")",
                  "## 口径与来源", *[guide["sources"][i] for i in (0,1,3)],
                  "实验编号 " + overview["experiment"] + "，opposite 分支，1.5 倍，仅手续费。",
                  "[全部历史开仓图](" + link("history") + ") · [下载资金单 CSV](funded.csv) · [下载开仓图 ZIP](cloud-v17-charts.zip)"])
    (BOOK / "handbook.md").write_text("\n\n".join(parts) + "\n")
    rules = parts[parts.index("## 执行规则")+1:parts.index("## 前向资金")]
    (BOOK / "rules.html").write_text(MarkdownIt("commonmark", {"html": False}).enable("table").render("\n\n".join(rules)))


def publish_overview():
    cases = json.loads((EXPERIMENT / "summary.json").read_text())["arms"]["opposite"]["cases"]
    metrics = cases["1.5x"]["metrics"]["all"]
    trades = read_csv(SOURCE / "funded_inverse_1.5x.csv.gz")
    hours = read_csv(SOURCE / "hourly_inverse_1.5x.csv.gz")
    pnl = [float(row["cash_pnl"]) for row in trades]
    wins, losses = [p for p in pnl if p > 0], [p for p in pnl if p < 0]
    if len(set(r["event_id"] for r in trades)) != metrics["trades"] or not math.isclose(
        1000 + sum(pnl), metrics["final_usdt"], abs_tol=.001
    ):
        raise ValueError("Funded trades and frozen hourly NAV do not reconcile")
    if not math.isclose(float(hours[-1]["nav"]), metrics["final_usdt"], abs_tol=.001):
        raise ValueError("Frozen terminal NAV differs from the published metrics")
    # Dates denote right edges. Midnight Jan 1 is the previous year's final hour.
    years = {}
    for h in hours:
        t = h["hour_end_utc"]
        year = int(t[:4]) - int(t[4:19] == "-01-01 00:00:00")
        years[year] = float(h["nav"])
    previous, periods = 1000., []
    for year, end in years.items():
        periods.append(dict(year=year, end=end, pct=100 * (end / previous - 1)))
        previous = end
    summary = dict(experiment=EXPERIMENT.name, metrics=metrics,
                   stress=cases["1.5x_stress"]["metrics"]["all"], years=periods,
                   endUtc=hours[-1]["hour_end_utc"], winners=len(wins), losers=len(losses),
                   symbols=len({r["symbol"] for r in trades}),
                   avgWin=sum(wins)/len(wins), avgLoss=-sum(losses)/len(losses),
                   grossProfitFactor=sum(wins)/-sum(losses),
                   top10Pct=100*sum(sorted(pnl, reverse=True)[:10])/sum(pnl))
    BOOK.mkdir(exist_ok=True)
    (BOOK / "overview.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    shutil.copyfile(SOURCE / "equity-drawdown-1.5x.png", BOOK / "equity.png")
    with gzip.open(SOURCE / "funded_inverse_1.5x.csv.gz", "rb") as src, (BOOK / "funded.csv").open("wb") as dest:
        shutil.copyfileobj(src, dest)
    publish_shared_history(hours, trades, summary)
    publish_handbook(summary)


def publish_zip():
    manifest = json.loads((HISTORY / "manifest.json").read_text())
    rows = manifest["records"]
    expected = {r["event_id"] for r in read_csv(SOURCE / "funded_inverse_1.5x.csv.gz")}
    if len(rows) != len(expected) or {r["eventId"] for r in rows} != expected:
        raise ValueError("The chart bundle must cover every funded event exactly once")
    dest = BOOK / "cloud-v17-charts.zip"
    index = io.StringIO()
    listing = csv.writer(index)
    listing.writerow(["图号", "币种", "方向", "入场毫秒（UTC）", "净盈亏（U）", "文件", "原始交易键"])
    with zipfile.ZipFile(dest.with_suffix(".tmp"), "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for r in rows:
            file = HISTORY / r["chartUrl"]
            name = f'{r["id"]}-{r["symbol"]}{file.suffix}'
            archive.write(file, name)
            listing.writerow([r["id"], r["symbol"], "多" if r["side"] == 1 else "空",
                              r["entryMs"], r["netPnl"], name, r["eventId"]])
        archive.writestr("index.csv", index.getvalue().encode("utf-8-sig"))
    dest.with_suffix(".tmp").replace(dest)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", action="store_true", help="package all generated charts")
    if parser.parse_args().zip:
        publish_zip()
    else:
        publish_overview()

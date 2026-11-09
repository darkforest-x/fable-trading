"""Publish the frozen OKX inverse account summary and human-readable chart bundle."""
import argparse
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


def publish_overview():
    from markdown_it import MarkdownIt
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
    handbook = (BOOK / "handbook.md").read_text()
    body = handbook.split("## 交易规则\n", 1)[1].split("## 账本与图谱", 1)[0]
    (BOOK / "rules.html").write_text(MarkdownIt("commonmark", {"html": False}).enable("table").render(body))


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

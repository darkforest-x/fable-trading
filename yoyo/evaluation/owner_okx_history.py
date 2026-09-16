"""Audit an explicitly supplied personal OKX position-history export.

This is descriptive accounting, not a signal evaluation: no market data,
holdout, model, production configuration, or exchange connection is read.
OKX position history reports price P&L separately from signed fees, funding,
and liquidation penalties. Linear and inverse settlements must stay separate.
All chronological attribution uses the export's UTC+8 position update time,
which is not a fill ledger or a mark-to-market account equity series.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import platform
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd


RENAME = {
    "仓位创建时间": "opened", "仓位更新时间": "updated", "交易产品": "instrument",
    "保证金模式": "mode", "持仓方向": "side", "杠杆倍数": "leverage",
    "最大持仓量": "max_contracts", "累计平仓量": "closed_contracts",
    "开仓均价": "entry", "平仓均价": "exit", "保证金币种": "currency",
    "收益额": "gross", "收益率": "reported_return", "累计手续费": "fee",
    "累计资金费用": "funding", "强平清算费": "liquidation_fee", "平仓类型": "close_type",
    "合约面值": "contract_value", "合约乘数": "multiplier",
}


def metrics(d: pd.DataFrame) -> dict:
    p = d.net
    wins, losses = p[p > 0], p[p < 0]
    return dict(n=len(d), gross=float(d.gross.sum()), fee=float(d.fee.sum()),
                funding=float(d.funding.sum()), liquidation_fee=float(d.liquidation_fee.sum()),
                net=float(p.sum()), mean=float(p.mean()) if len(p) else None,
                median=float(p.median()) if len(p) else None,
                win_rate=float((p > 0).mean()) if len(p) else None,
                gross_win_rate=float((d.gross > 0).mean()) if len(p) else None,
                profit_factor=float(wins.sum() / -losses.sum()) if len(losses) else None,
                win_sum=float(wins.sum()), loss_sum=float(losses.sum()),
                avg_win=float(wins.mean()) if len(wins) else None,
                avg_loss=float(losses.mean()) if len(losses) else None,
                liquidation_count=int(d.close_type.eq("全部强平").sum()),
                median_hours=float(d.hours.median()) if len(d) else None,
                median_notional=float(d.closed_notional.median()) if len(d) else None,
                gross_bp=float(d.gross.sum()/d.closed_notional.sum()*10000) if d.closed_notional.sum() else None,
                net_bp=float(d.net.sum()/d.closed_notional.sum()*10000) if d.closed_notional.sum() else None)


def groups(d: pd.DataFrame, col: str) -> list[dict]:
    return [dict(group=str(key), **metrics(g)) for key, g in d.groupby(col, observed=True, sort=True)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    with zipfile.ZipFile(args.input) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1:
            raise ValueError("Expected one position-history CSV")
        raw = z.read(names[0])
    decoded = raw.decode("utf-8-sig").replace("\ufeff", "")
    # Skip the UID metadata line; never persist the user's account identifier.
    original = pd.read_csv(io.StringIO(decoded), skiprows=1)
    d = original.rename(columns=RENAME).copy()
    d.insert(0, "source_row", np.arange(len(d)) + 3)
    for col in ["opened", "updated"]:
        d[col] = pd.to_datetime(d[col], errors="raise")
    d["hours"] = (d.updated - d.opened).dt.total_seconds() / 3600
    d["net"] = d[["gross", "fee", "funding", "liquidation_fee"]].sum(axis=1)
    d["net_without_liqfee"] = d[["gross", "fee", "funding"]].sum(axis=1)
    d["direction"] = d.side.map({"做多": 1, "做空": -1})
    d["closed_notional"] = d.closed_contracts * d.contract_value * d.multiplier * d.entry
    d["max_notional_proxy"] = d.max_contracts * d.contract_value * d.multiplier * d.entry
    d["year"] = d.updated.dt.year.astype(str)
    d["month"] = d.updated.dt.strftime("%Y-%m")
    d["date"] = d.updated.dt.strftime("%Y-%m-%d")
    d["asset"] = d.instrument.str.split("-").str[0]
    d["universe"] = np.where(d.asset.isin(["BTC", "ETH"]), "BTC/ETH", "其他币")
    d["duration"] = pd.cut(d.hours, [-1, 5/60, .25, 1, 4, 24, 72, np.inf],
                           labels=["≤5分钟", "5–15分钟", "15–60分钟", "1–4小时", "4–24小时", "1–3天", ">3天"])
    d["leverage_band"] = pd.cut(d.leverage, [0, 10, 20, 50, np.inf],
                                labels=["≤10倍", "11–20倍", "21–50倍", ">50倍"])
    linear = d.instrument.str.endswith("-USDT-SWAP")
    u = d[linear].sort_values(["updated", "source_row"]).copy()
    other = d[~linear].copy()
    u["calc_gross"] = u.closed_notional * (u.exit / u.entry - 1) * u.direction
    u["gross_error"] = u.calc_gross - u.gross
    closed = u[~u.close_type.eq("部分平仓")].copy()
    error = u.gross_error.abs()
    audit = dict(raw_rows=len(original), linear_usdt_rows=len(u), other_rows=len(other),
                 null_cells=int(original.isna().sum().sum()), exact_duplicates=int(original.duplicated().sum()),
                 lifecycle_key_duplicates=int(d.duplicated(["opened", "instrument", "side", "mode"]).sum()),
                 invalid_duration=int((d.hours < 0).sum()), invalid_entry=int((d.entry <= 0).sum()),
                 unit_missing_linear=int(u.currency.eq("-").sum()),
                 linear_pnl_max_error=float(error.max()), linear_pnl_errors_above_cent=int((error > .01).sum()),
                 partial_rows=int(u.close_type.eq("部分平仓").sum()),
                 open_min=str(d.opened.min()), update_max=str(d.updated.max()),
                 currencies=d.currency.value_counts().to_dict(),
                 row_number_definition="CSV source line: metadata=1, header=2, first data=3",
                 archive_sha256=hashlib.sha256(args.input.read_bytes()).hexdigest(),
                 csv_sha256=hashlib.sha256(raw).hexdigest(),
                 timezone="UTC+08:00 from original export metadata",
                 python=platform.python_version(), pandas=pd.__version__, numpy=np.__version__)
    monthly = groups(u, "month")
    daily = u.groupby("date", as_index=False).agg(net=("net", "sum"), gross=("gross", "sum"),
                                                  fee=("fee", "sum"), n=("net", "size"))
    daily["cumulative_net"] = daily.net.cumsum()
    daily["cumulative_gross"] = daily.gross.cumsum()
    daily["drawdown_usdt"] = daily.cumulative_net.cummax().clip(lower=0)-daily.cumulative_net
    # Same timestamp positions are one bookkeeping event, not an arbitrary ordering.
    events = u.groupby("updated", as_index=False).net.sum()
    events["cumulative"] = events.net.cumsum()
    events["peak"] = events.cumulative.cummax().clip(lower=0)
    events["drawdown"] = events.peak-events.cumulative
    trough_idx = events.drawdown.idxmax()
    before = events.loc[:trough_idx]
    peak_idx = before.cumulative.idxmax()
    event_drawdown = dict(amount=float(events.loc[trough_idx,"drawdown"]),
                          trough=str(events.loc[trough_idx,"updated"]),
                          peak=str(events.loc[peak_idx,"updated"]) if before.cumulative.max()>0 else "initial zero",
                          peak_cumulative=float(events.loc[trough_idx,"peak"]),
                          trough_cumulative=float(events.loc[trough_idx,"cumulative"]))
    cols = ["source_row", "opened", "updated", "instrument", "side", "leverage", "mode", "close_type",
            "gross", "fee", "funding", "liquidation_fee", "net", "hours", "closed_notional"]
    def recs(frame):
        return json.loads(frame.to_json(orient="records", date_format="iso", force_ascii=False))
    top = u.nlargest(10,"net")
    bottom = u.nsmallest(10,"net")
    sensitivity=[]
    for k in [1, 5, 10, 20, 50]:
        removed=u.nlargest(k,"net")
        sensitivity.append(dict(remove_top=k, removed_net=float(removed.net.sum()), remaining_net=float(u.net.sum()-removed.net.sum()),
                                remaining_gross=float(u.gross.sum()-removed.gross.sum())))
    # Calendar-week clusters preserve within-week co-movement. Ratio bootstrap
    # does not pretend rows are independent; stationarity is still unproven.
    w=u.set_index("updated").resample("W-SUN").agg(net=("net","sum"), n=("net","size"))
    rng=np.random.default_rng(20260916)
    idx=rng.integers(0,len(w),(10000,len(w)))
    boot_n=w.n.to_numpy()[idx].sum(axis=1)
    boot_mean=w.net.to_numpy()[idx].sum(axis=1)/boot_n
    # Null sign-flip of weekly P&L; exploratory, requires symmetric independent blocks.
    signs=rng.choice([-1,1],size=(10000,len(w)))
    null=(signs*w.net.to_numpy()).sum(axis=1)
    null_result=dict(n_weeks=len(w), replicates=10000, seed=20260916,
                     weekly_cluster_mean_ci95=np.quantile(boot_mean,[.025,.975]).tolist(),
                     observed_mean=float(u.net.mean()),
                     p_positive_signflip=float((1+(null>=u.net.sum()).sum())/10001),
                     caveat="Exploratory weekly sign symmetry/stationarity assumptions; no alpha/random-entry benchmark")
    # Chronological segments are descriptive, never rebranded blind validation.
    periods=[]
    for name,start,end in [("2024","2024-01-01","2025-01-01"),("2025H1","2025-01-01","2025-07-01"),
                           ("2025H2","2025-07-01","2026-01-01"),("2026H1","2026-01-01","2026-07-01"),
                           ("2026H2截至导出","2026-07-01","2027-01-01")]:
        periods.append(dict(group=name,**metrics(u[(u.updated>=start)&(u.updated<end)])))
    # Only earlier completed lifecycle summaries are attached to each new opening.
    # This is an association; actual fills and motivations are not observable.
    prev=closed.groupby("updated",as_index=False).net.sum().rename(columns={"updated":"prev_closed","net":"prev_net"})
    openings=pd.merge_asof(u.sort_values("opened"),prev.sort_values("prev_closed"),left_on="opened",right_on="prev_closed",allow_exact_matches=False)
    openings["gap_minutes"]=(openings.opened-openings.prev_closed).dt.total_seconds()/60
    openings["previous_outcome"]=np.where(openings.prev_net>0,"上一结算组盈利",np.where(openings.prev_net<0,"上一结算组亏损","无/零"))
    openings["reentry"]=pd.cut(openings.gap_minutes,[-1,5,30,120,np.inf],labels=["≤5分钟","5–30分钟","30–120分钟",">120分钟"])
    reentry=[dict(previous=str(a),gap=str(b),**metrics(g)) for (a,b),g in openings.groupby(["previous_outcome","reentry"],observed=True)]
    starts=u.groupby(u.opened.dt.strftime("%Y-%m-%d")).size()
    u["open_day_count"]=u.opened.dt.strftime("%Y-%m-%d").map(starts)
    u["frequency_band"]=pd.cut(u.open_day_count,[0,3,10,20,50,np.inf],labels=["1–3条/日","4–10条/日","11–20条/日","21–50条/日",">50条/日"])
    u["notional_band"]=pd.cut(u.closed_notional,[0,500,2000,10000,50000,np.inf],labels=["≤500U","500–2000U","2000–1万U","1万–5万U",">5万U"])
    by_year_side=[dict(year=str(y),side=str(s),**metrics(g)) for (y,s),g in u.groupby(["year","side"])]
    result=dict(audit=audit, overall=metrics(u), completed=metrics(closed), other_settlement=recs(other[cols]),
                by_year=groups(u,"year"), by_period=periods, by_month=monthly, by_side=groups(u,"side"),
                by_year_side=by_year_side, by_mode=groups(u,"mode"), by_close_type=groups(u,"close_type"),
                by_duration=groups(closed,"duration"), by_leverage=groups(u,"leverage_band"),
                by_universe=groups(u,"universe"), by_asset=groups(u,"asset"), by_notional=groups(u,"notional_band"),
                by_frequency=groups(u,"frequency_band"), reentry=reentry, top_wins=recs(top[cols]), top_losses=recs(bottom[cols]),
                sensitivity=sensitivity, bootstrap=null_result, realized_drawdown=event_drawdown,
                positive_to_negative_fee_count=int(((u.gross>0)&(u.net<=0)).sum()),
                closed_notional_total=float(u.closed_notional.sum()),
                net_no_liquidation_fee=float(u.net_without_liqfee.sum()),
                profitable_months=sum(x["net"]>0 for x in monthly), months_with_records=len(monthly),
                active_open_days=len(starts), max_opens_day=int(starts.max()), max_opens_date=str(starts.idxmax()),
                median_opens_active_day=float(starts.median()),
                winner_hold_median=float(closed.loc[closed.net>0,"hours"].median()),
                loser_hold_median=float(closed.loc[closed.net<0,"hours"].median()))
    args.out.mkdir(parents=True,exist_ok=True)
    (args.out/"summary.json").write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    u.drop(columns=["业务线","标的指数"],errors="ignore").to_csv(args.out/"positions_usdt.csv",index=False)
    other[cols].to_csv(args.out/"positions_other_currency.csv",index=False)
    daily.to_csv(args.out/"daily_realized.csv",index=False)
    for k in ["by_year","by_period","by_month","by_side","by_year_side","by_mode","by_close_type","by_duration","by_leverage","by_universe","by_asset","by_notional","by_frequency","reentry"]:
        pd.DataFrame(result[k]).to_csv(args.out/f"{k}.csv",index=False)
    print(json.dumps({k:result[k] for k in ["audit","overall","completed","by_year","by_close_type","by_duration","by_leverage","by_universe","sensitivity","bootstrap","realized_drawdown"]},ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()

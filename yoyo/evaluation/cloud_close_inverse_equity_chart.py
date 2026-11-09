"""Plot the original 1.5x inverse OKX account from its frozen hourly valuation ledger."""
import json
from pathlib import Path

import matplotlib as mpl
mpl.use("Agg")
import matplotlib.dates as mdates
from matplotlib import font_manager
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

EXP = Path(__file__).resolve().parents[2] / "experiments/active/exp-cloud-close-alt-season-delivery-20261109-v1"
BASE = EXP / "opposite"
STARTING_CASH = 1000.
HOUR = pd.Timedelta(hours=1)
STAGES = ["open_nav", "open_exit_nav", "post_trim_nav", "nav"]
GREEN, RED, GRAY, INK = "#08786b", "#b24040", "#526471", "#182b37"


def label(ax, x, y, text, offset, color):
    ax.scatter(x, y, s=26, c=color, edgecolor="white", lw=.8, zorder=5)
    ax.annotate(text, (x, y), xytext=offset, textcoords="offset points",
                ha="left" if offset[0] >= 0 else "right", va="center", color=color, fontsize=10.5,
                bbox=dict(boxstyle="round,pad=.18", fc="white", ec="none", alpha=.92),
                arrowprops=dict(arrowstyle="-", color=color, lw=.7, shrinkB=4), zorder=6)


def main():
    font = "/System/Library/Fonts/Hiragino Sans GB.ttc"
    font_manager.fontManager.addfont(font)
    mpl.rcParams.update({"font.family":font_manager.FontProperties(fname=font).get_name(),
                         "axes.unicode_minus":False,"timezone":"Asia/Shanghai"})
    curve = pd.read_csv(BASE/"hourly_inverse_1.5x.csv.gz", index_col="hour_end_utc", parse_dates=True)
    curve.index = curve.index.tz_convert("Asia/Shanghai")
    if not curve.index.to_series().diff().iloc[1:].eq(HOUR).all():
        raise ValueError("Missing or duplicated valuation hours")
    summary = json.loads((EXP/"summary.json").read_text())["arms"]["opposite"]["cases"]["1.5x"]["metrics"]["all"]
    stages = np.r_[STARTING_CASH, curve[STAGES].to_numpy().ravel()]
    dd = 100 * (stages / np.maximum.accumulate(stages) - 1)
    # Preserve the deepest observed stage in every hour, even for intrahour recoveries.
    hourly_dd = pd.Series(dd[1:].reshape(-1,len(STAGES)).min(axis=1), index=curve.index)
    upper_peak = np.maximum.accumulate(np.r_[STARTING_CASH, curve.upper_nav])[1:]
    bound = 100 * (curve.lower_nav / upper_peak - 1)
    trough = int(np.argmin(dd))
    peak = int(np.argmax(stages[:trough+1]))
    stage_hours = curve.index.repeat(len(STAGES)).insert(0,curve.index[0]-HOUR)
    peak_time, trough_time = stage_hours[peak], stage_hours[trough]
    recovery = curve.nav.loc[trough_time:][lambda nav: nav >= stages[peak]].index[0]
    end_time, final = curve.index[-1], curve.nav.iat[-1]
    np.testing.assert_allclose([final, -hourly_dd.min(), -bound.min()],
                               [summary["final_usdt"], summary["dd_mark_pct"], summary["dd_bound_pct"]])
    equity = pd.concat([pd.Series([STARTING_CASH], index=[curve.index[0]-HOUR]),curve.nav])

    fig,(ax,under)=plt.subplots(2,1,figsize=(12,6.55),sharex=True,
                                gridspec_kw={"height_ratios":[1.95,1],"hspace":.49})
    fig.subplots_adjust(left=.085,right=.966,top=.806,bottom=.086)
    fig.text(.083,.926,"云带 v1.7  /  资金路径与回撤",fontsize=21,color=INK)
    fig.text(.084,.864,"OKX 永续   ·   12H 收盘入场   ·   ETH/BTC 近 30 天逆向筛选   ·   1.5 倍仓位",
             fontsize=11,color=GRAY)
    fig.text(.966,.926,f"{STARTING_CASH:,.0f} → {final:,.0f} U",ha="right",fontsize=19,
             color=GREEN,fontfamily="DejaVu Sans",weight="medium")
    fig.text(.966,.864,f"{equity.index[0]:%Y.%m.%d} — {end_time:%Y.%m.%d}   ·   北京时间",
             ha="right",fontsize=10,color=GRAY)
    for panel in (ax, under):
        panel.spines[:].set_visible(False)
        panel.grid(axis="y",color="#e1e8ea",lw=.7)
        panel.set_axisbelow(True)
        panel.tick_params(axis="both",length=0,pad=6,colors=GRAY,labelsize=10)
        panel.axvspan(peak_time,recovery,facecolor="#eee7de",alpha=.55,zorder=0)
    ax.set_title("账户净值（U，对数轴）",loc="left",fontsize=11.5,color=INK,pad=6)
    ax.set_yscale("log")
    ax.set_yticks([1000,3000,10000,30000,100000,200000],
                  ["1,000","3,000","10,000","30,000","100,000","200,000"])
    ax.yaxis.set_minor_locator(mpl.ticker.NullLocator())
    ax.set_ylim(750,290000)
    ax.plot(equity.index,equity,color=GREEN,lw=1.18)
    ax.hlines(stages[peak],peak_time,recovery,color="#977e63",ls="--",lw=.9)
    label(ax,peak_time,stages[peak],f"前高 {stages[peak]:,.0f}",(-6,19),INK)
    label(ax,trough_time,stages[trough],f"谷底 {stages[trough]:,.0f}",(1,-20),RED)
    label(ax,recovery,curve.nav.at[recovery],"收复前高",(12,-22),INK)
    ax.scatter(end_time,final,c=GREEN,s=32,ec="white",lw=.8,zorder=5)

    under.set_title("相对历史高点的账面回撤（每小时最深值）",loc="left",fontsize=11.5,color=INK,pad=7)
    under.fill_between(hourly_dd.index,0,hourly_dd,color="#f6dddd",lw=0)
    under.plot(hourly_dd.index,hourly_dd,color=RED,lw=.75)
    under.axhline(-50,color="#7c4952",ls=(0,(5,4)),lw=1)
    under.text(.004,-49.5,"50% 回撤上限",transform=under.get_yaxis_transform(),va="bottom",
               fontsize=9.5,color="#7c4952",bbox=dict(fc="white",ec="none",alpha=.87,pad=1))
    label(under,trough_time,dd[trough],f"{dd[trough]:.2f}%",(-22,-2),RED)
    under.set_ylim(-66,3)
    under.set_yticks([0,-20,-40,-60],["0%","−20%","−40%","−60%"])
    years = pd.date_range("2020-01-01",end_time.normalize(),freq="YS",tz=curve.index.tz)
    under.set_xticks(years,[t.strftime("%Y") for t in years])
    ax.set_xlim(min(equity.index[0],years[0]),end_time+pd.Timedelta(days=22))
    fig.savefig(BASE/"equity-drawdown-1.5x.png",dpi=165,facecolor="white")
    plt.close(fig)
    print("Peak, trough-hour, first recovered close:",peak_time,trough_time,recovery)
    print("Peak, trough, recovered, final:",stages[peak],stages[trough],curve.nav.at[recovery],final)


if __name__=="__main__":
    main()

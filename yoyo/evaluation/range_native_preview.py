"""Render saved native fills with Nautilus 1.231.0's existing interactive chart.

The offline view implements the same cache/bar/fill interface used by Nautilus'
BacktestNode view. No orders or outcomes are recomputed. Native fill timestamps
are 5m bar closes, not claimed intrabar execution times. Frozen-account balances
and independent R are deliberately not converted into a portfolio equity curve.
Source: installed nautilus_trader.analysis.tearsheet.create_bars_with_fills.
"""
from pathlib import Path

import pandas as pd
from nautilus_trader.analysis.tearsheet import create_bars_with_fills
from nautilus_trader.model.data import BarType


class SavedNativeView:
    """Read-only adapter over the actual native fill ledger and source candles."""

    def __init__(self, bars, fills):
        self.cache = self
        self._bars, self._fills = bars, fills

    def bars(self, bar_type):
        return self._bars

    @staticmethod
    def bar_to_dict(bar):
        return bar

    def generate_fills_report(self):
        return self._fills


def native_view(data, trades, fills, symbol, start, end):
    """Join by immutable trade ID; invert entry side for each exit fill."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    data = data.copy()
    data["ts_init"] = pd.to_datetime(data.ts, unit="ms", utc=True) + pd.Timedelta(minutes=5)
    bars = data[data.ts_init.between(start, end)].to_dict("records")
    signals = trades[trades.population == "signal"]
    if not signals.trade_id.is_unique:
        raise ValueError("Trade IDs must be unique")
    merged = fills.merge(signals[["trade_id", "side", "net_r", "peak_r", "reason"]],
                         on="trade_id", how="inner", validate="many_to_one")
    merged["ts_init"] = pd.to_datetime(merged.ts_ns, unit="ns", utc=True)
    merged = merged[merged.ts_init.between(start, end)].copy()
    direction = merged.side.where(merged.phase == "entry", -merged.side)
    merged["order_side"] = direction.map({1: "BUY", -1: "SELL"})
    merged["instrument_id"] = f"{symbol}-USDT.RESEARCH"
    merged["strategy_id"] = "Range V2.8"
    merged["last_qty"], merged["last_px"] = merged.quantity, merged.price
    merged["action"] = [
        ("开多" if side == 1 else "开空") if phase == "entry"
        else ("平多" if side == 1 else "平空")
        for side, phase in zip(merged.side, merged.phase)
    ]
    return SavedNativeView(bars, merged)


def preview(data_path, trade_path, fill_path, output, symbol, start, end):
    data = pd.read_csv(data_path, float_precision="round_trip")
    trades = pd.read_csv(trade_path, float_precision="round_trip")
    fills = pd.read_csv(fill_path, float_precision="round_trip")
    view = native_view(data, trades, fills, symbol, start, end)
    if not view._bars or view._fills.empty:
        raise ValueError("No native candles/fills in requested preview interval")
    bar_type = BarType.from_str(f"{symbol}-USDT.RESEARCH-5-MINUTE-LAST-EXTERNAL")
    fig = create_bars_with_fills(
        view, bar_type, theme="plotly_dark",
        title=f"{symbol} · Nautilus 原生成交图 · {start[:10]} 至 {end[:10]} UTC"
              "<br><sup>基线规则 · 买卖标记含开仓和平仓 · 时间为5分钟收盘 · 悬停查看结果R</sup>",
    )
    # Formatting correction: the upstream default .2f rounds PEPE prices to 0.
    # Keep the native layout, controls, candlesticks, fill markers and slider.
    for trace in fig.data:
        if trace.name not in ("Buy Fills", "Sell Fills"):
            continue
        side = "BUY" if trace.name == "Buy Fills" else "SELL"
        rows = view._fills[view._fills.order_side == side]
        trace.customdata = rows[["trade_id", "action", "net_r", "peak_r"]].to_numpy()
        trace.hovertemplate = (
            "%{x} UTC<br>%{customdata[0]} · %{customdata[1]}"
            "<br>成交价 %{y:.10g}<br>该单最终净结果 %{customdata[2]:.2f}R"
            "<br>该单最大浮盈 %{customdata[3]:.2f}R<extra></extra>"
        )
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(output, include_plotlyjs=True)
    receipt = dict(path=str(output), renderer="Nautilus create_bars_with_fills",
                   bars=len(view._bars), fills=len(view._fills),
                   trades=int(view._fills.trade_id.nunique()),
                   start=start, end=end, timestamps="UTC 5m bar close",
                   notes="Signal population only. Full-trade R is retrospective, not an entry feature. "
                         "MFE excludes stop-hit bars. Interval may clip a trade's other endpoint.")
    return fig, receipt

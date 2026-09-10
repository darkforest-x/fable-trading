"""Audit one previously selected extreme winner and static cash contribution.

Uses only authenticated output ledgers, summaries, and original feature prices.
This does not replay trade decisions or simulate removal of any instrument.
Future source bars are outcome-verification evidence, never signal features.
"""
from pathlib import Path
import hashlib
import json

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
STUDY = Path(__file__).resolve().parents[1]
RESULTS = STUDY / "results"
vm = json.loads((RESULTS / "validation_manifest.json").read_text())
dm = json.loads((RESULTS / "dataset_manifest.json").read_text())
assert vm["status"] == dm["status"] == "complete"
auth = {str(Path(x["path"]).resolve()): x["sha256"] for x in vm["artifacts"] + dm["artifacts"]}
verified = {}


def verify(path):
    path = Path(path).resolve()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest == auth[str(path)], path
    verified[str(path)] = digest
    return path


ledger = pd.read_csv(verify(RESULTS / "accounts/60_altcoins_burst_trail_full_ledger.csv.gz"))
selected = ledger.loc[ledger.portfolio_selected.eq(True)].copy()
trade = selected.loc[selected.symbol.eq("RAVEUSDT")].sort_values("realized_net_pnl").iloc[-1]
frame = pd.read_pickle(verify(trade.features_path))
annual = pd.read_csv(verify(RESULTS / "annual_summary.csv"))
accounts = pd.read_csv(verify(RESULTS / "accounts_summary.csv"))
entry = pd.Timestamp(trade.entry_time)
exit_lower = pd.Timestamp(trade.exit_time_lower)
exit_upper = pd.Timestamp(trade.exit_time_upper)
quantity = trade.notional / trade.entry_price
pnl = quantity * (trade.exit_price - trade.entry_price) - trade.notional * 0.002
limits = {
    "ten_percent_entry_equity": trade.entry_equity * 0.10,
    "half_percent_equity_risk": trade.entry_equity * 0.005 / trade.initial_risk_frac,
    "one_percent_signal_quote_volume": trade.signal_quote_volume * 0.01,
    "one_per_mille_prior_24h_quote_volume": trade.prior24h_quote_volume * 0.001,
}
assert np.isclose(quantity, trade.quantity)
assert np.isclose(pnl, trade.realized_net_pnl)
assert np.isclose(trade.notional, min(limits.values()))
assert frame.loc[entry, "open"] == trade.entry_price
bar = frame.loc[exit_lower]
assert trade.exit_timing == "intrabar_unknown"
assert exit_upper - exit_lower == pd.Timedelta(hours=1)
assert bar.low <= trade.exit_price <= bar.high
account = accounts.loc[(accounts.minutes == 60) & (accounts.cohort == "altcoins") & (accounts.arm == "burst_trail") & (accounts.account == "full")].iloc[0]
other_pnl = selected.loc[selected.event_id.ne(trade.event_id), "realized_net_pnl"].sum()
assert np.isclose(pnl + other_pnl, account.return_pct / 100 * 100000)
annual_burst = annual.loc[(annual.cohort == "altcoins") & (annual.arm == "burst_trail") & (annual.account == "full")]
fields = ["minutes", "period", "start", "end", "opening_equity", "ending_equity", "return_pct", "max_drawdown_pct", "exits", "win_rate", "natural_exits", "natural_win_rate"]
fields = [k for k in fields if k in annual_burst]
payload = {
    "schema": "rave-fixed-selected-cash-contribution-v1",
    "method": "Arithmetic reconciliation of fixed selected fills; not a removal/reallocation backtest",
    "validation_manifest_sha256": hashlib.sha256((RESULTS / "validation_manifest.json").read_bytes()).hexdigest(),
    "dataset_manifest_sha256": hashlib.sha256((RESULTS / "dataset_manifest.json").read_bytes()).hexdigest(),
    "verified_sources": verified,
    "trade": json.loads(trade.to_json()),
    "position_caps": limits,
    "binding_cap": min(limits, key=limits.get),
    "independent_quantity": float(quantity),
    "independent_net_cash_pnl": float(pnl),
    "same_hour_exit_source_bar": dict(open_time=exit_lower.isoformat(), **{k: float(bar[k]) for k in ["open", "high", "low", "close", "volume", "quote_volume"]}),
    "account_static_contribution": {
        "selected_trades": len(selected),
        "total_net_pnl": float(selected.realized_net_pnl.sum()),
        "rave_trade_net_pnl": float(pnl),
        "all_other_selected_trades_net_pnl": float(other_pnl),
        "all_positive_cash_pnl": float(selected.loc[selected.realized_net_pnl.gt(0), "realized_net_pnl"].sum()),
    },
    "annual_burst_altcoins": annual_burst[fields].to_dict("records"),
    "limits": [
        "The exit time is an OHLC-bar interval, not an observed execution timestamp.",
        "Price-range inclusion supports the stop simulation but does not establish real fill, orderbook liquidity, or slippage.",
        "Removing RAVE could alter allocation and future sizing; the other-trade sum is static attribution only.",
        "Dataset authenticity is checked against the study manifests; raw-exchange source audit is a separate review.",
    ],
}
output = STUDY / "diagnostics/rave_cash_contribution_audit.json"
output.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n")
print(json.dumps({"output": str(output), "sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "pnl": float(pnl), "other_pnl": float(other_pnl)}, indent=2))

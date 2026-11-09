"""Extract previously unsaved order fills for four examples of the frozen OKX inverse account."""
import hashlib

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_alt_season_delivery as original

BASE = original.EXP / "opposite"
OUT = original.EXP / "examples-20261110"


def samples(funds, tape):
    """Keep the largest cash long and fixed-seed draws for losing longs, early 2R winning shorts, losing shorts."""
    winners = funds.cash_pnl.gt(0)
    longs = funds.side.eq(1)
    early_targets = tape[tape.tp_r.eq(2) & tape.exit_reason.str.startswith("take_profit")]
    picks = [funds.loc[longs & winners, "cash_pnl"].idxmax()]
    for mask in [longs & ~winners, ~longs & winners & funds.index.isin(early_targets.event_id), ~longs & ~winners]:
        picks.append(min(funds.index[mask], key=lambda key: hashlib.sha256(("cloud-v17-20261110|" + key).encode()).digest()))
    return picks


def main():
    OUT.mkdir(exist_ok=True)
    funds = pd.read_csv(BASE / "funded_inverse_1.5x.csv.gz", index_col="event_id", float_precision="round_trip")
    tape = pd.read_csv(BASE / "selected_paths.csv.gz", dtype={"symbol": str}, float_precision="round_trip")
    ids = samples(funds, tape)
    _, quotes, _ = original.source.inputs()
    data, _ = original.old.parent.assemble(tape, original.old.changed_limits(tape, quotes), "OKX", quotes)
    market = original.old.exposure.underlying(data, quotes)
    multiple = pd.Series(1.5, index=market[-1].event_id)
    samples_out = []

    def sample_orders(fills):
        part = fills[fills.underlying_event_id.isin(ids)].copy()
        selected = data["selected"].set_index(["event_id", "slot"])
        selected_rows = selected.loc[list(zip(part.underlying_event_id, part.col))]
        part["action"] = np.where(part.stage.eq(0), "entry", np.where(part.stage.eq(2), "trim", selected_rows.exit_reason))
        part["fill_price"] = part.underlying_entry_price * (1 + part.underlying_side * (part.price - 1) / 1.5)
        part["symbol"] = part.underlying_event_id.map(funds.symbol)
        part["side"] = part.underlying_event_id.map(funds.side)
        # The native engine only gives an H1 interval for intrabar fills.
        part["phase"] = np.where(part.stage.eq(3), "intrabar", "open")
        part = part.rename(columns={"underlying_event_id": "event_id", "hour": "hour_ms", "col": "child_slot"})
        keep = ["event_id", "symbol", "side", "hour_ms", "phase", "action", "child_slot", "fill_price", "net_units", "fees", "underlying_cash"]
        samples_out.append(part[keep])

    def verify(_, data, replay, curve, multipliers, slip):
        hourly = pd.read_csv(BASE / "hourly_inverse_1.5x.csv.gz", index_col=0, parse_dates=True, float_precision="round_trip")
        assert hourly.index.equals(curve.index) and set(funds.index) == set(replay.index)
        np.testing.assert_allclose(curve[hourly.columns], hourly, rtol=1e-9, atol=1e-5)
        np.testing.assert_allclose(replay.cash_pnl.reindex(funds.index), funds.cash_pnl, rtol=1e-9, atol=1e-5)

    result = original.exposure.simulate(data, market, quotes, multiple, 0., OUT, "sample", snapshot=verify, fill_sink=sample_orders)
    if result.get("unresolved") or len(samples_out) != 1:
        raise ValueError("No verified native fill receipt")
    part = samples_out[0]
    np.testing.assert_allclose(part.groupby("event_id").underlying_cash.sum().reindex(ids), funds.loc[ids].cash_pnl, atol=1e-6)
    part.to_csv(OUT / "sample_fills.csv", index=False)


if __name__ == "__main__":
    main()

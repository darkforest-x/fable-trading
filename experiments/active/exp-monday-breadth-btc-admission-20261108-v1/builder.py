"""Move the ordinary BTC 4h EMA20 veto ahead of its six-day admission clock.

All trade returns come from the frozen VectorBT outcome table or native funded
fill cache. This builder changes only the ordering of the ordinary BTC EMA gate
and its six-day reservation. It preserves case-first admission, the BTC exits,
and the independent 50/50 BTC/ALT sleeves with the original ALT 24h spacing.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Running this experiment by file path puts only its own directory on sys.path.
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from yoyo.evaluation import monday_breadth_extension_btc as trend
from yoyo.evaluation import monday_breadth_extension_partial_marks as partial_marks
from yoyo.evaluation import monday_breadth_extension_risk as risk
from yoyo.evaluation import monday_breadth_opt_funding as funding
from yoyo.evaluation import monday_breadth_opt_inputs as inputs
from yoyo.evaluation import monday_breadth_opt_payoffs as prices
from yoyo.evaluation import monday_breadth_opt_selection as selection
from yoyo.evaluation.monday_breadth_opt_match_exits import paired_month_p


OUT = Path(__file__).resolve().parent
FREQ = ROOT / "experiments/active/exp-monday-breadth-frequency-20261108-v1"
OPT = ROOT / "experiments/active/exp-monday-breadth-optimization-20261108-v1"
EXT = ROOT / "experiments/active/exp-monday-breadth-extension-20261108-v1"
HOLD = pd.Timedelta(days=6)
HOLD_HOURS = 144
HOLD_SLOTS = HOLD_HOURS * 12
ENTRY_FEE = 0.0005
EXIT_POLICY_ID = 4  # existing native BTC half-target / half-to-six-days fill
RULE = (720, 10, "N2_100000", "zero", "reclaim_ge75")
TARGET_DECISION = pd.Timestamp("2026-09-15 19:20", tz="UTC")


def git_revision() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def frequency_admissions(frame: pd.DataFrame, epoch: np.ndarray,
                         slots: np.ndarray, candidate_indices: np.ndarray) -> np.ndarray:
    """Use the original per-epoch, six-day plus 24h minimum BTC clock."""
    picked: list[int] = []
    for period in range(4):
        within = candidate_indices[epoch[candidate_indices] == period]
        picked.extend(selection.admissions(
            within, slots, HOLD_SLOTS, selection.ENDS[period],
        ).tolist())
    return np.asarray(picked, dtype=np.int64)


def source_schedule(medium: pd.DataFrame, cases: pd.DataFrame,
                    variant: str) -> pd.DataFrame:
    """Reserve one BTC slot for six days; preserve the original case tie priority."""
    med = medium[["decision", "epoch"]].assign(source="medium")
    case = cases[["decision", "epoch"]].assign(source="case")
    both = pd.concat([case, med], ignore_index=True).sort_values(
        ["decision", "source"], kind="stable",
    ).reset_index(drop=True)
    admitted: list[bool] = []
    blocked_by_time: list[pd.Timestamp | pd.NaT] = []
    blocked_by_source: list[str | None] = []
    reserved_until: list[pd.Timestamp | pd.NaT] = []
    after = pd.Timestamp.min.tz_localize("UTC")
    owner: tuple[pd.Timestamp, str] | None = None
    for row in both.itertuples():
        take = row.decision >= after
        admitted.append(take)
        blocked_by_time.append(pd.NaT if take or owner is None else owner[0])
        blocked_by_source.append(None if take or owner is None else owner[1])
        reserved_until.append(pd.NaT if take else after)
        if take:
            after = row.decision + HOLD
            owner = (row.decision, row.source)
    both["variant"] = variant
    both["admitted"] = admitted
    both["blocked_by_decision"] = blocked_by_time
    both["blocked_by_source"] = blocked_by_source
    both["reserved_until"] = reserved_until
    return both


def read_case_cache() -> pd.DataFrame:
    rows = pd.read_parquet(
        funding.FUNDED_CACHE, filters=[("policy_id", "=", EXIT_POLICY_ID)],
    ).copy()
    rows["decision"] = pd.to_datetime(rows.entry_time, utc=True)
    if rows.decision.duplicated().any():
        raise ValueError("Native half-then-six-day BTC fills contain duplicate entry clocks")
    return rows.set_index("decision", drop=False)


def case_events(decisions: pd.DataFrame, cache: pd.DataFrame) -> pd.DataFrame:
    selected = decisions[decisions.source.eq("case")].copy()
    if not selected.decision.isin(cache.index).all():
        missing = selected.loc[~selected.decision.isin(cache.index), "decision"].tolist()
        raise ValueError(f"Missing native case fills: {missing}")
    fill = cache.loc[selected.decision].reset_index(drop=True)
    out = selected.reset_index(drop=True)
    for name in (
        "exit_time", "entry_open", "exit_vwap", "net_pct", "net_points",
        "exit_legs", "first_exit_time", "first_exit_price", "reason",
        "ambiguous", "funding_points", "funded_points", "funded_pct",
    ):
        out[name] = fill[name].to_numpy()
    out["entry_time"] = out.decision
    out["exit_time"] = pd.to_datetime(out.exit_time, utc=True)
    out["first_exit_time"] = pd.to_datetime(out.first_exit_time, utc=True)
    out["gross_price_points"] = out.exit_vwap - out.entry_open
    out["fee_points"] = out.gross_price_points - out.net_points
    out["gross_price_pct"] = 100 * out.gross_price_points / out.entry_open
    out["fee_bps_effective"] = 10000 * out.fee_points / out.entry_open
    out["runner_exit_price"] = np.where(
        out.exit_legs.eq(2), 2 * out.exit_vwap - out.first_exit_price,
        out.exit_vwap,
    )
    out["return_source"] = "native_vectorbt_funded_policy_4"
    return out


def medium_events(decisions: pd.DataFrame, raw: pd.DataFrame,
                  frame: pd.DataFrame, returns: np.ndarray,
                  outcomes: pd.DataFrame) -> pd.DataFrame:
    selected = decisions[decisions.source.eq("medium")].copy()
    index_by_time = raw.set_index("decision").raw_index
    if not selected.decision.isin(index_by_time.index).all():
        raise ValueError("An admitted medium decision is absent from raw candidates")
    selected["raw_index"] = selected.decision.map(index_by_time).astype(np.int64)
    px = outcomes.set_index("decision").reindex(selected.decision)
    if px[["btc_entry_open", "btc_exit_open", "btc_net_return_pct"]].isna().any().any():
        raise ValueError("A selected ordinary BTC candidate lacks a native VectorBT outcome")
    idx = selected.raw_index.to_numpy(dtype=np.int64)
    vbt_net = returns[idx, selection.HOLDS.index(HOLD_HOURS), selection.ASSETS.index("BTC")]
    cached_net = px.btc_net_return_pct.to_numpy(dtype=float)
    np.testing.assert_allclose(vbt_net, cached_net, atol=1e-10, rtol=0)
    selected["entry_time"] = selected.decision
    selected["exit_time"] = selected.decision + HOLD
    selected["entry_open"] = px.btc_entry_open.to_numpy(dtype=float)
    selected["exit_vwap"] = px.btc_exit_open.to_numpy(dtype=float)
    selected["net_pct"] = vbt_net
    selected["net_points"] = selected.entry_open * selected.net_pct / 100
    selected["gross_price_points"] = selected.exit_vwap - selected.entry_open
    selected["fee_points"] = selected.gross_price_points - selected.net_points
    selected["gross_price_pct"] = 100 * selected.gross_price_points / selected.entry_open
    selected["fee_bps_effective"] = 10000 * selected.fee_points / selected.entry_open
    selected["first_exit_time"] = selected.exit_time
    selected["first_exit_price"] = np.nan
    selected["runner_exit_price"] = selected.exit_vwap
    selected["exit_legs"] = 1
    selected["reason"] = 1
    selected["ambiguous"] = 0
    selected["funding_points"] = np.nan
    selected["funded_points"] = np.nan
    selected["funded_pct"] = np.nan
    selected["return_source"] = "frozen_vectorbt_event_outcomes"
    if selected.decision.duplicated().any():
        raise ValueError("Ordinary BTC stream contains duplicate decision clocks")
    return selected


def drawdown_pct(curve: pd.Series) -> float:
    clean = curve.dropna().astype(float)
    dd = 1 - clean / clean.cummax().clip(lower=1.0)
    return float(100 * dd.max()) if len(dd) else float("nan")


def settlement_curve(btc: pd.DataFrame, alt: pd.DataFrame,
                     return_field: str) -> pd.Series:
    """Portfolio close-only curve; partial legs roll up at final position close."""
    rows = pd.concat([
        btc[["exit_time", return_field]].assign(sleeve="BTC"),
        alt[["exit_time", return_field]].assign(sleeve="ALT"),
    ], ignore_index=True).sort_values("exit_time", kind="stable")
    if rows[return_field].isna().any():
        return pd.Series(dtype=float)
    btc_wealth = alt_wealth = 1.0
    stamps = [pd.Timestamp("1970-01-01", tz="UTC")]
    values = [1.0]
    for stamp, group in rows.groupby("exit_time", sort=True):
        for row in group.itertuples():
            factor = 1 + float(getattr(row, return_field)) / 100
            if row.sleeve == "BTC":
                btc_wealth *= factor
            else:
                alt_wealth *= factor
        stamps.append(stamp)
        values.append((btc_wealth + alt_wealth) / 2)
    return pd.Series(values, index=pd.DatetimeIndex(stamps), dtype=float)


def sleeve_settlement_curve(events: pd.DataFrame, return_field: str) -> pd.Series:
    """One independently funded sleeve, sampled only when positions close."""
    rows = events[["exit_time", return_field]].sort_values("exit_time", kind="stable")
    if rows[return_field].isna().any():
        return pd.Series(dtype=float)
    wealth = 1.0
    stamps = [pd.Timestamp("1970-01-01", tz="UTC")]
    values = [wealth]
    for stamp, group in rows.groupby("exit_time", sort=True):
        wealth *= float(np.prod(1 + group[return_field].to_numpy(dtype=float) / 100))
        stamps.append(stamp)
        values.append(wealth)
    return pd.Series(values, index=pd.DatetimeIndex(stamps), dtype=float)


def growth_pct(events: pd.DataFrame, field: str) -> float:
    values = pd.to_numeric(events[field], errors="coerce").dropna().to_numpy(dtype=float)
    if (values <= -100).any():
        return -100.0
    return float(100 * (np.prod(1 + values / 100) - 1))


def alt_paths_from_cache(alt: pd.DataFrame) -> dict[pd.Timestamp, np.ndarray]:
    cache = EXT / "risk/alt_6h_marks.npz"
    if not cache.is_file():
        raise FileNotFoundError(cache)
    with np.load(cache) as data:
        times = pd.to_datetime(data["timestamps_ns"], utc=True)
        values = data["unit_fee_values"]
    paths = dict(zip(times, values, strict=True))
    missing = set(alt.decision) - set(paths)
    if missing:
        raise ValueError(f"Missing cached original ALT fee paths: {sorted(missing)}")
    return paths


def funded_btc_paths(events: pd.DataFrame, fee_paths: dict,
                     settlements: pd.Series, quotes: pd.DataFrame,
                     *, validate_existing: bool) -> tuple[dict, pd.DataFrame]:
    paths: dict[pd.Timestamp, np.ndarray] = {}
    rows = []
    for row in events.sort_values("decision").itertuples():
        times = pd.date_range(row.decision, row.exit_time, freq="5min")
        price_path = fee_paths[row.decision]
        if len(price_path) != len(times) or not np.isfinite(price_path).all():
            raise ValueError(f"Incomplete BTC 5m fee marks: {row.decision}")
        cost = partial_marks.accumulated_cost(
            settlements, row.decision, row.exit_time, times,
        )
        if row.exit_legs == 2:
            first_cost = partial_marks.accumulated_cost(
                settlements, row.decision, row.first_exit_time, times,
            )
            cost = .5 * (cost + first_cost)
        funded = price_path - cost / row.entry_open
        funded_pct = 100 * (funded[-1] - 1)
        if hasattr(row, "funded_pct") and np.isfinite(row.funded_pct):
            if validate_existing:
                np.testing.assert_allclose(funded_pct, row.funded_pct, atol=1e-7, rtol=0)
        if hasattr(row, "funding_points") and np.isfinite(row.funding_points):
            np.testing.assert_allclose(cost[-1], row.funding_points, atol=1e-6, rtol=0)
        paths[row.decision] = funded
        rows.append(dict(decision=row.decision, exit_time=row.exit_time,
                         computed_funding_points=float(cost[-1]),
                         computed_funded_pct=funded_pct,
                         mark_bars=len(times),
                         quote_coverage_pct=100.0,
                         funding_coverage=True))
    return paths, pd.DataFrame(rows)


def matched_controls(candidate: pd.DataFrame, alt: pd.DataFrame) -> dict:
    """Only reuse existing 2026 controls when their exact old event remains."""
    path = EXT / "risk/integrated_control_events.csv"
    controls = pd.read_csv(path, parse_dates=["decision"])
    controls["decision"] = pd.to_datetime(controls.decision, utc=True)
    # BTC controls were matched for ordinary medium events only. Do not let an
    # ALT clock (or a case-source BTC clock) accidentally inherit that control.
    ordinary_btc = set(candidate.loc[candidate.source.eq("medium"), "decision"])
    alt_clocks = set(alt.decision)
    keep = controls[
        (controls.asset.eq("BTC") & controls.decision.isin(ordinary_btc))
        | (controls.asset.eq("ALT") & controls.decision.isin(alt_clocks))
    ].copy()
    original = pd.read_csv(EXT / "risk/integrated_controls.csv").iloc[0]
    if len(keep):
        keep["weighted_edge"] = np.nan
        for _, group in keep.groupby("asset"):
            keep.loc[group.index, "weighted_edge"] = (
                (group.real - group.control) * 0.5 / len(group)
            )
        retained_edge = float(keep.weighted_edge.sum())
        retained_p = float(paired_month_p(keep.set_index("decision").weighted_edge))
    else:
        retained_edge = float("nan")
        retained_p = float("nan")
    return dict(
        old_matched_pairs=int(original.matched), old_eligible_entries=int(original.total),
        retained_exact_pairs=int(len(keep)),
        retained_exact_coverage_pct=(100 * len(keep) / max(1, len(candidate) + len(alt))),
        old_edge_pp=float(original.edge_pp), old_monthly_p=float(original.p_month),
        retained_exact_edge_pp=retained_edge,
        retained_exact_monthly_p=retained_p,
        new_entries_without_control=int(len(candidate) + len(alt) - len(keep)),
        interpretation=("Only matched controls for unchanged exact entry clocks are reused; "
                        "newly admitted entries have no matched random control."),
    )


def run() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    width, wait, breadth, diffusion, confirmation = RULE
    frame, returns = inputs.partition(width, wait)
    slots, epoch = selection.time_axes(frame)
    raw_ids = selection.rule_indices(frame, width, wait, breadth, diffusion, confirmation)
    raw = frame.iloc[raw_ids].copy()
    raw["raw_index"] = raw_ids
    raw["epoch"] = epoch[raw_ids]
    raw["source"] = "medium"

    quotes_all, price_receipt = prices.load_full_ohlc(
        "BTCUSDT", raw.decision.min() - pd.Timedelta(days=60),
        raw.decision.max() + HOLD, repair_missing=False,
    )
    htf = trend._higher_timeframe(quotes_all, 4)
    features = trend._asof_features(raw.decision, {4: htf})
    ema_valid = (
        features["4h_close"].notna() & features["4h_ema20"].notna()
    ).to_numpy(dtype=bool)
    ema_pass = trend._gate(features, "below_4h_ema20") & ema_valid
    raw["ema4h_valid"] = ema_valid
    raw["ema4h_close"] = pd.to_numeric(features["4h_close"], errors="coerce").to_numpy()
    raw["ema20"] = pd.to_numeric(features["4h_ema20"], errors="coerce").to_numpy()
    raw["ema_pass"] = ema_pass

    old_indices = frequency_admissions(frame, epoch, slots, raw_ids)
    new_indices = frequency_admissions(
        frame, epoch, slots, raw.loc[raw.ema_pass, "raw_index"].to_numpy(dtype=np.int64),
    )
    fixed_locked = pd.read_parquet(FREQ / "fixed/locked_events.parquet")
    fixed_locked = fixed_locked[fixed_locked.candidate_id.eq("freq36_BTC")]
    old_times = set(frame.iloc[old_indices].decision)
    if len(old_indices) != 246 or old_times != set(fixed_locked.decision):
        raise ValueError("Original freq36_BTC admission no longer reproduces its 246-event frozen ledger")

    candidates = raw.copy()
    candidates["baseline_freq36_admitted"] = candidates.raw_index.isin(old_indices)
    candidates["ema_before_admission_freq36_admitted"] = candidates.raw_index.isin(new_indices)
    candidates["old_admitted_then_ema"] = candidates.baseline_freq36_admitted & candidates.ema_pass
    candidates["new_frequency_admitted"] = candidates.ema_before_admission_freq36_admitted

    outcome_file = OPT / "payoffs/event_outcomes_minutes720_wait10.csv.gz"
    outcomes = pd.read_csv(
        outcome_file,
        usecols=["known", "hold_hours", "btc_entry_open", "btc_exit_open", "btc_net_return_pct"],
        parse_dates=["known"],
    )
    outcomes["known"] = pd.to_datetime(outcomes.known, utc=True)
    outcomes["decision"] = outcomes.known + pd.Timedelta(minutes=wait)
    outcomes = outcomes[outcomes.hold_hours.eq(HOLD_HOURS)].set_index("decision", drop=False)
    if outcomes.index.duplicated().any():
        raise ValueError("Native BTC VectorBT outcome clocks are not unique")

    # Publish the standalone pre-case ordinary stream as soon as its 177 fills
    # reconcile to both the cached VectorBT matrix and native price outcomes.
    ordinary = medium_events(
        pd.DataFrame({"decision": frame.iloc[new_indices].decision,
                      "epoch": epoch[new_indices], "source": "medium"}),
        raw, frame, returns, outcomes,
    )
    if len(ordinary) != 177 or ordinary.net_pct.isna().any():
        raise ValueError("The standalone post-EMA ordinary BTC stream must validate all 177 native fills")
    ordinary["candidate_id"] = "freq36_BTC"
    ordinary["fee_bps_round_trip"] = 10.0
    ordinary["admission_rule"] = "below_4h_close_ema20_then_original_6d_clock"
    ordinary_path = OUT / "ordinary_stream.parquet"
    ordinary_tmp = OUT / ".ordinary_stream.parquet.tmp"
    ordinary.to_parquet(ordinary_tmp, index=False)
    os.replace(ordinary_tmp, ordinary_path)

    cases = pd.read_parquet(FREQ / "btc_target/threshold_events.parquet")
    cases = cases[cases.threshold_pct.eq(5)][["decision", "epoch"]].copy()
    cases["decision"] = pd.to_datetime(cases.decision, utc=True)
    if cases.decision.duplicated().any() or len(cases) != 53:
        raise ValueError("Expected the frozen 53-event BTC 5% case signal stream")

    old_schedule = source_schedule(
        frame.iloc[old_indices][["decision"]].assign(epoch=epoch[old_indices]),
        cases, "baseline_below_post_reservation",
    )
    old_gate_times = set(raw.loc[raw.ema_pass, "decision"])
    old_schedule["selected"] = old_schedule.admitted & (
        old_schedule.source.eq("case") | old_schedule.decision.isin(old_gate_times)
    )
    new_schedule = source_schedule(
        frame.iloc[new_indices][["decision"]].assign(epoch=epoch[new_indices]),
        cases, "ema_before_reservation",
    )
    new_schedule["selected"] = new_schedule.admitted
    frozen_dual = pd.read_csv(EXT / "dual/admission.csv", parse_dates=["decision"])
    frozen_dual["decision"] = pd.to_datetime(frozen_dual.decision, utc=True)
    old_admitted = old_schedule[old_schedule.admitted]
    if set(zip(old_admitted.decision, old_admitted.source)) != set(
        zip(frozen_dual.loc[frozen_dual.admitted, "decision"], frozen_dual.loc[frozen_dual.admitted, "source"])
    ):
        raise ValueError("The original case-first dual reservation does not reproduce")

    baseline = pd.read_parquet(EXT / "risk/integrated_btc_events.parquet").copy()
    baseline["decision"] = pd.to_datetime(baseline.decision, utc=True)
    baseline["exit_time"] = pd.to_datetime(baseline.exit_time, utc=True)
    baseline["first_exit_time"] = pd.to_datetime(baseline.first_exit_time, utc=True)
    baseline["variant"] = "baseline_below_post_reservation"
    selected_old = old_schedule[old_schedule.selected]
    if len(baseline) != len(selected_old) or set(baseline.decision) != set(selected_old.decision):
        raise ValueError("The reconstructed old post-reservation EMA baseline does not match its frozen 159 events")
    if baseline.groupby("source").size().to_dict() != {"case": 32, "medium": 127}:
        raise ValueError("Frozen 159-event baseline source composition changed")

    case_cache = read_case_cache()
    new_selected = new_schedule[new_schedule.selected].copy()
    new_case = case_events(new_selected, case_cache)
    new_medium = medium_events(new_selected, raw, frame, returns, outcomes)
    new_events = pd.concat([new_medium, new_case], ignore_index=True, sort=False)
    new_events["variant"] = "ema_before_reservation"
    new_events["decision"] = pd.to_datetime(new_events.decision, utc=True)
    new_events["exit_time"] = pd.to_datetime(new_events.exit_time, utc=True)
    new_events["first_exit_time"] = pd.to_datetime(new_events.first_exit_time, utc=True)
    new_events = new_events.sort_values(["decision", "source"], kind="stable").reset_index(drop=True)
    if len(new_events) != len(new_selected) or new_events.decision.duplicated().any():
        raise ValueError("New BTC schedule has duplicate or missing fills")
    if new_events.groupby("source").size().to_dict() != {"case": 37, "medium": 155}:
        raise ValueError("Unexpected new case-first schedule composition")

    # Validate all ordinary candidate returns against the original native VectorBT matrix.
    np.testing.assert_allclose(
        new_medium.net_pct.to_numpy(dtype=float),
        returns[new_medium.raw_index.to_numpy(dtype=np.int64),
                selection.HOLDS.index(HOLD_HOURS), selection.ASSETS.index("BTC")],
        atol=1e-10, rtol=0,
    )
    old_medium = baseline[baseline.source.eq("medium")]
    old_raw_lookup = outcomes.set_index("decision").btc_net_return_pct
    parity = old_medium.decision.map(old_raw_lookup)
    if parity.isna().any():
        raise ValueError("An old medium baseline fill is absent from the frozen native outcome table")
    np.testing.assert_allclose(old_medium.net_pct, parity, atol=1e-8, rtol=0)

    alt_all = pd.read_parquet(FREQ / "audit/fixed/all_rule_and_account_events.parquet")
    alt_all = alt_all[alt_all.candidate_id.eq("freq36_ALT")].copy()
    alt_all["decision"] = pd.to_datetime(alt_all.decision, utc=True)
    alt_all["exit_time"] = pd.to_datetime(alt_all.exit_time, utc=True)
    if len(alt_all) != 239 or int(alt_all.epoch.eq(3).sum()) != 25:
        raise ValueError("The original ALT 24h spacing stream must stay at 239 / 25 events")

    # Produce fee-only all-history 5m marks and validate against the frozen account row.
    required_quote_start = min(baseline.decision.min(), new_events.decision.min(), alt_all.decision.min())
    required_quote_end = max(baseline.exit_time.max(), new_events.exit_time.max(), alt_all.exit_time.max())
    if quotes_all.index.min() > required_quote_start or quotes_all.index.max() < required_quote_end:
        raise ValueError("The already-loaded BTC quote frame does not cover every historical admitted interval")
    # Reuse the frame used for causal EMA features instead of rescanning the same
    # registered multi-year source files a second time.
    btc_all_quotes, all_price_receipt = quotes_all, price_receipt
    partials = case_cache.set_index("entry_time")
    baseline_fee_paths = risk.btc_marks(baseline, btc_all_quotes, partials)
    new_fee_paths = risk.btc_marks(new_events, btc_all_quotes, partials)
    alt_fee_paths = alt_paths_from_cache(alt_all)
    all_start = min(baseline.decision.min(), new_events.decision.min(), alt_all.decision.min()).floor("D")
    all_end = max(baseline.exit_time.max(), new_events.exit_time.max(), alt_all.exit_time.max())
    all_index = pd.date_range(all_start, all_end, freq="5min")

    # The 50/50 setup has independent, non-rebalanced sleeves. Each sleeve deploys
    # its own half-capital fully; the account is the mean of the two sleeve curves.
    full_paths = {
        "baseline": (baseline, baseline_fee_paths),
        "candidate": (new_events, new_fee_paths),
    }
    all_fee_curves: dict[str, pd.Series] = {}
    all_fee_metrics: dict[str, dict] = {}
    for name, (btc, paths) in full_paths.items():
        btc_curve = risk.sleeve(btc, paths, all_index, 1.0, 0.0)
        alt_curve = risk.sleeve(alt_all, alt_fee_paths, all_index, 1.0, 0.0)
        account = (btc_curve + alt_curve) / 2
        all_fee_curves[name] = account
        settlement = settlement_curve(btc, alt_all, "net_pct")
        btc_growth = growth_pct(btc, "net_pct")
        alt_growth = growth_pct(alt_all, "net_pct")
        all_fee_metrics[name] = dict(
            btc_growth_pct=btc_growth,
            alt_growth_pct=alt_growth,
            growth_pct=(btc_growth + alt_growth) / 2,
            settlement_dd_pct=drawdown_pct(settlement),
            marked_dd_pct=drawdown_pct(account),
            marked_dd_scope="all_history_fee_only_5m_close_marks",
        )
    frozen_all_fee = pd.read_csv(EXT / "risk/accounts.csv").query(
        "strategy == 'below_plus_case' and scope == 'all_fee' "
        "and sleeve_deployment == 1 and extra_roundtrip_pp == 0"
    ).iloc[0]
    np.testing.assert_allclose(
        all_fee_metrics["baseline"]["growth_pct"], frozen_all_fee.growth_pct,
        atol=1e-8, rtol=0,
    )
    np.testing.assert_allclose(
        all_fee_metrics["baseline"]["marked_dd_pct"], frozen_all_fee.max_dd_pct,
        atol=1e-7, rtol=0,
    )

    # Actual 2026 Binance USD-M funding and exact five-minute close marks.
    btc26_quotes = btc_all_quotes
    history = funding.rates("BTCUSDT", history=True, local_only=True)
    if history.empty:
        raise ValueError("No locally registered Binance BTC funding history is available")
    settlements = history.realized_rate * history.mark_price.fillna(
        btc26_quotes.open.reindex(history.index),
    )
    alt26 = alt_all[alt_all.epoch.eq(3)].copy()
    cached_curves = pd.read_parquet(EXT / "sleeves/equity_5m.parquet")
    alt_funded_paths = risk.funded_relative(alt26, cached_curves.ALT)
    if len(alt26) != 25 or len(alt_funded_paths) != len(alt26):
        raise ValueError("Frozen ALT 2026 funded marks do not cover all original 25 events")

    funded_paths: dict[str, dict] = {}
    funded_event_frames: dict[str, pd.DataFrame] = {}
    coverage_rows: list[pd.DataFrame] = []
    for name, (btc, fee_paths) in full_paths.items():
        btc26 = btc[btc.epoch.eq(3)].copy()
        funded_paths[name], covered = funded_btc_paths(
            btc26, fee_paths, settlements, btc26_quotes, validate_existing=True,
        )
        coverage_rows.append(covered.assign(variant=name))
        btc26["funding_points"] = btc26.decision.map(covered.set_index("decision").computed_funding_points)
        btc26["funded_pct"] = btc26.decision.map(covered.set_index("decision").computed_funded_pct)
        btc26["funded_points"] = btc26.net_points - btc26.funding_points
        if btc26.funded_pct.isna().any():
            raise ValueError(f"Missing funded returns in 2026 {name} BTC schedule")
        frozen26 = btc[btc.epoch.eq(3)].set_index("decision")
        computed26 = btc26.set_index("decision")
        if name == "baseline":
            np.testing.assert_allclose(
                computed26.funded_pct,
                frozen26.funded_pct.reindex(computed26.index),
                atol=1e-7, rtol=0,
            )
        funded_event_frames[name] = btc26
        for field in ("funding_points", "funded_points", "funded_pct"):
            mapping = computed26[field]
            btc.loc[btc.epoch.eq(3), field] = btc.loc[btc.epoch.eq(3), "decision"].map(mapping)
        btc_curve = risk.sleeve(btc26, funded_paths[name], cached_curves.index, 1.0, 0.0)
        alt_curve = risk.sleeve(alt26, alt_funded_paths, cached_curves.index, 1.0, 0.0)
        account = (btc_curve + alt_curve) / 2
        all_fee_curves[name + "_2026_funded"] = account
        funded_settlement = settlement_curve(btc26, alt26, "funded_pct")
        btc_growth = growth_pct(btc26, "funded_pct")
        alt_growth = growth_pct(alt26, "funded_pct")
        all_fee_metrics[name + "_2026_funded"] = dict(
            btc_growth_pct=btc_growth,
            alt_growth_pct=alt_growth,
            growth_pct=(btc_growth + alt_growth) / 2,
            settlement_dd_pct=drawdown_pct(funded_settlement),
            marked_dd_pct=drawdown_pct(account),
            marked_dd_scope="2026_actual_funding_5m_close_marks",
        )

    frozen_funded = pd.read_csv(EXT / "risk/accounts.csv").query(
        "strategy == 'below_plus_case' and scope == '2026_funded' "
        "and sleeve_deployment == 1 and extra_roundtrip_pp == 0"
    ).iloc[0]
    np.testing.assert_allclose(
        all_fee_metrics["baseline_2026_funded"]["growth_pct"], frozen_funded.growth_pct,
        atol=1e-7, rtol=0,
    )
    np.testing.assert_allclose(
        all_fee_metrics["baseline_2026_funded"]["marked_dd_pct"], frozen_funded.max_dd_pct,
        atol=1e-7, rtol=0,
    )

    old_decisions = set(baseline.decision)
    new_decisions = set(new_events.decision)
    additions = new_decisions - old_decisions
    removals = old_decisions - new_decisions
    target_case = cases[cases.decision.eq(TARGET_DECISION)]
    if len(target_case) != 1:
        raise ValueError("The motivating 2026-09-15 19:20Z case clock is absent")
    new_target_row = new_schedule[new_schedule.decision.eq(TARGET_DECISION)]
    if len(new_target_row) != 1:
        raise ValueError("The motivating case clock is absent from the candidate schedule audit")
    target_fill = case_cache.loc[TARGET_DECISION]
    blocker_time = new_target_row.iloc[0].blocked_by_decision
    blocker = new_events[new_events.decision.eq(blocker_time)] if pd.notna(blocker_time) else new_events.iloc[0:0]
    if len(blocker) != 1:
        raise ValueError("The blocked case does not resolve to exactly one admitted candidate BTC fill")

    summary_rows: list[dict] = []
    for name, btc in [("baseline_below_post_reservation", baseline),
                      ("ema_before_reservation", new_events)]:
        metric_name = "baseline" if name == "baseline_below_post_reservation" else "candidate"
        for scope in ("all_fee", "2026_funded"):
            stats = all_fee_metrics[metric_name if scope == "all_fee" else metric_name + "_2026_funded"]
            abtc = funded_event_frames[metric_name] if scope == "2026_funded" else btc
            aalt = alt26 if scope == "2026_funded" else alt_all
            dset = set(abtc.decision) | set(aalt.decision)
            portfolio_n = len(abtc) + len(aalt)
            matched = matched_controls(abtc, aalt) if scope == "2026_funded" else None
            settlement_field = "net_pct" if scope == "all_fee" else "funded_pct"
            summary_rows.append(dict(
                variant=name,
                scope=scope,
                btc_count=len(abtc),
                alt_count=len(aalt),
                total_legs=portfolio_n,
                distinct_entries=len(dset),
                growth_pct=stats["growth_pct"],
                settlement_dd_pct=stats["settlement_dd_pct"],
                marked_dd_pct=stats["marked_dd_pct"],
                marked_dd_scope=stats["marked_dd_scope"],
                btc_growth_pct=stats["btc_growth_pct"],
                alt_growth_pct=stats["alt_growth_pct"],
                btc_settlement_dd_pct=drawdown_pct(sleeve_settlement_curve(abtc, settlement_field)),
                alt_settlement_dd_pct=drawdown_pct(sleeve_settlement_curve(aalt, settlement_field)),
                sep16_admitted=bool(new_target_row.iloc[0].admitted) if name == "ema_before_reservation" else True,
                sep16_status=("admitted" if bool(new_target_row.iloc[0].admitted) else "blocked_by_prior_medium")
                             if name == "ema_before_reservation" else "admitted_frozen_baseline",
                random_control_coverage_pct=(
                    matched["retained_exact_coverage_pct"] if matched else np.nan
                ),
                random_control_pairs=(matched["retained_exact_pairs"] if matched else 0),
                random_control_edge_pp=(matched["retained_exact_edge_pp"] if matched else np.nan),
                random_control_monthly_p=(matched["retained_exact_monthly_p"] if matched else np.nan),
                random_control_status=(
                    matched["interpretation"] if matched else
                    "No full-history random controls recomputed; 2026 controls do not cover this scope."
                ),
            ))
    summary = pd.DataFrame(summary_rows)

    # Dedicated single-trade audit for the known 2026 owner target and its new blocker.
    baseline_target = baseline[baseline.decision.eq(TARGET_DECISION)].iloc[0]
    key_trade = pd.DataFrame([dict(
        target_decision=TARGET_DECISION,
        baseline_admitted=True,
        candidate_admitted=bool(new_target_row.iloc[0].admitted),
        candidate_blocked_by_time=blocker_time,
        candidate_blocked_by_source=new_target_row.iloc[0].blocked_by_source,
        candidate_reserved_until=new_target_row.iloc[0].reserved_until,
        baseline_entry_open=baseline_target.entry_open,
        baseline_first_exit_time=baseline_target.first_exit_time,
        baseline_first_exit_price=target_fill.first_exit_price,
        baseline_runner_exit_price=2 * target_fill.exit_vwap - target_fill.first_exit_price,
        baseline_exit_vwap=target_fill.exit_vwap,
        baseline_fee_points=target_fill.exit_vwap - baseline_target.entry_open - baseline_target.net_points,
        baseline_exit_time=baseline_target.exit_time,
        baseline_net_points=baseline_target.net_points,
        baseline_funding_points=baseline_target.funding_points,
        baseline_funded_points=baseline_target.funded_points,
        baseline_funded_pct=baseline_target.funded_pct,
        blocker_decision=blocker.iloc[0].decision,
        blocker_entry_open=blocker.iloc[0].entry_open,
        blocker_exit_vwap=blocker.iloc[0].exit_vwap,
        blocker_exit_time=blocker.iloc[0].exit_time,
        blocker_net_points=blocker.iloc[0].net_points,
        blocker_net_pct=blocker.iloc[0].net_pct,
        target_net_points_if_missed=np.nan,
        note=("New ordinary BTC candidate reserves six days through 2026-09-18 00:10Z; "
              "the baseline 50/50 policy4 case had first half at target, then runner to six days."),
    )])

    # Build year and source attribution from exact selected event identities.
    yearly = []
    for year in sorted(set(baseline.decision.dt.year) | set(new_events.decision.dt.year) | set(alt_all.decision.dt.year)):
        old_year_decisions = set(baseline.loc[baseline.decision.dt.year.eq(year), "decision"])
        for name, btc in [("baseline_below_post_reservation", baseline),
                          ("ema_before_reservation", new_events)]:
            b = btc[btc.decision.dt.year.eq(year)]
            a = alt_all[alt_all.decision.dt.year.eq(year)]
            year_decisions = set(b.decision)
            yearly.append(dict(
                variant=name, year=int(year), btc_count=len(b),
                btc_medium_count=int(b.source.eq("medium").sum()),
                btc_case_count=int(b.source.eq("case").sum()),
                alt_count=len(a), total_legs=len(b) + len(a),
                distinct_entries=len(set(b.decision) | set(a.decision)),
                btc_added_vs_baseline=(len(year_decisions - old_year_decisions) if name == "ema_before_reservation" else 0),
                btc_removed_vs_baseline=(len(old_year_decisions - year_decisions) if name == "ema_before_reservation" else 0),
            ))

    # Save all owned outputs only after every reconciliation and coverage assertion passed.
    raw["baseline_admitted_then_ema"] = candidates.old_admitted_then_ema.to_numpy()
    candidates.to_parquet(OUT / "candidate_audit.parquet", index=False)
    pd.concat([old_schedule, new_schedule], ignore_index=True).to_csv(
        OUT / "admission_audit.csv", index=False,
    )
    events = pd.concat([baseline, new_events], ignore_index=True, sort=False)
    events.to_parquet(OUT / "btc_events.parquet", index=False)
    summary.to_csv(OUT / "summary.csv", index=False)
    pd.DataFrame(yearly).to_csv(OUT / "yearly_counts.csv", index=False)
    key_trade.to_csv(OUT / "key_trade.csv", index=False)
    curve_2026 = pd.DataFrame(index=cached_curves.index)
    curve_2026["alt_funded"] = cached_curves.ALT
    curve_2026["baseline_btc_funded"] = risk.sleeve(
        baseline[baseline.epoch.eq(3)], funded_paths["baseline"],
        cached_curves.index, 1.0, 0.0,
    )
    curve_2026["candidate_btc_funded"] = risk.sleeve(
        new_events[new_events.epoch.eq(3)], funded_paths["candidate"],
        cached_curves.index, 1.0, 0.0,
    )
    curve_2026["baseline_portfolio_50_50"] = (
        curve_2026.alt_funded + curve_2026.baseline_btc_funded
    ) / 2
    curve_2026["candidate_portfolio_50_50"] = (
        curve_2026.alt_funded + curve_2026.candidate_btc_funded
    ) / 2
    curve_2026.to_parquet(OUT / "curve_2026_5m.parquet")
    pd.concat(coverage_rows, ignore_index=True).to_csv(OUT / "btc_2026_mark_coverage.csv", index=False)
    controls = {
        "builder_commit": git_revision(),
        "rule": {"minutes": width, "wait_min": wait, "breadth": breadth,
                 "diffusion": diffusion, "confirmation": confirmation,
                 "hold_hours": HOLD_HOURS, "ema_gate": "completed BTC 4h close <= EMA20"},
        "raw_candidates": int(len(raw)),
        "ema_valid": int(raw.ema4h_valid.sum()),
        "ema_pass": int(raw.ema_pass.sum()),
        "ema_veto": int((raw.ema4h_valid & ~raw.ema_pass).sum()),
        "baseline_freq36_admitted": int(len(old_indices)),
        "ema_before_freq36_admitted": int(len(new_indices)),
        "old_admitted_then_ema": int(candidates.old_admitted_then_ema.sum()),
        "ordinary_stream_native_vectorbt_coverage": int(ordinary.net_pct.notna().sum()),
        "ordinary_stream_native_vectorbt_count": int(len(ordinary)),
        "new_case_policy4_cache_coverage": int(new_case.net_pct.notna().sum()),
        "new_case_policy4_cache_count": int(len(new_case)),
        "baseline_event_count": int(len(baseline)),
        "candidate_event_count": int(len(new_events)),
        "candidate_events_added_vs_baseline": int(len(additions)),
        "baseline_events_removed_vs_candidate": int(len(removals)),
        "candidate_events_shared_with_baseline": int(len(new_decisions & old_decisions)),
        "btc_quote_receipt": str(price_receipt),
        "all_history_btc_quote_receipt": str(all_price_receipt),
        "btc_2026_native_quote_events": int(sum(len(x) for x in coverage_rows)),
        "btc_2026_mark_bar_count": int(sum(pd.concat(coverage_rows).mark_bars)),
        "btc_2026_funding_coverage_events": int(pd.concat(coverage_rows).funding_coverage.sum()),
        "alt_2026_funded_mark_events": int(len(alt_funded_paths)),
        "random_controls": {
            "recomputed_for_new_candidate": False,
            "old_baseline_source": "extension/risk/integrated_controls.csv",
            "candidate_use": "only exact existing matched clocks that remain; no controls for added signals",
        },
        "limitation": (
            "Historical growth and marks are fee-only. 2026 uses realized funding and 5m closes. "
            "No matched random controls were generated for newly admitted BTC clocks."
        ),
    }
    (OUT / "coverage.json").write_text(json.dumps(controls, indent=2) + "\n")


if __name__ == "__main__":
    run()

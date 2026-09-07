"""V35 no-outcome projection and clock audit over frozen V4 mothers and V34 flow.

Uses only whitelisted event identities, directions, folds, K1/K2 clocks and
waiting statuses. Market columns are the genuine same-bar quote-flow fields
documented in yoyo.data.k1k2_genuine_flow_alignment. No economic labels, fills,
OHLC, model, trading thresholds, exits or new data downloads. All event windows
and data files are declared and hash checked before materialization.
Pandas source: https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.read_csv.html
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pandas as pd

from yoyo.data.k1k2_genuine_flow_alignment import AMOUNTS, align_windows, clocks

ROOT = Path(__file__).resolve().parents[2]
REL = Path("experiments/active/exp-btcusdtp-k1k2-genuine-flow-alignment-20260907-v35")
HERE = ROOT / REL
MOTHER = ["event_id", "signal_time", "decision_time", "direction", "fold"]
REQUEST = ["event_id", "direction", "fold", "mother_signal_time", "mother_decision_time",
           "k2_time", "decision_time", "wait_hours"]
STATUS = ["event_id", "mother_signal_time", "mother_decision_time", "terminal_time", "status", "k2_time"]
KINDS = ["k1", "k2", "post_k1_through_k2"]
ALLOWED_STATUS = {"request_emitted", "invalidated_wrong_close", "invalidated_ma_colour",
                  "expired_no_k2", "data_gap", "waiting_censored"}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def checked() -> tuple[str, dict, dict]:
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    config = json.loads((HERE / "config.json").read_text())
    for name in config["builder_paths"] + [str(REL / "config.json"), str(REL / "PROJECT_PLAN.md")]:
        if subprocess.check_output(["git", "show", commit + ":" + name], cwd=ROOT) != (ROOT / name).read_bytes():
            raise ValueError("Commit exact builder and contract before reading inputs: " + name)
    for name, expected in config["input_hashes"].items():
        if sha(ROOT / name) != expected:
            raise ValueError("Frozen input drift: " + name)
    summary = json.loads((ROOT / config["flow_summary"]).read_text())
    if summary["status"] != "complete" or len(summary["monthly"]) != 24:
        raise ValueError("V34 flow not complete")
    if [r["month"] for r in summary["monthly"]] != [str(p) for p in pd.period_range("2023-01", "2024-12", freq="M")]:
        raise ValueError("Unauthorized flow months")
    for r in summary["monthly"]:
        if r["output_path"] != "data/genuine_flow_coverage_v34/bars/BTCUSDT-5m-" + r["month"] + "-flow.csv":
            raise ValueError("Unrecognized flow file")
        if sha(ROOT / r["output_path"]) != r["output_sha256"]:
            raise ValueError("V34 bar bytes drift")
    return commit, config, summary


def project(path: Path, columns: list[str]) -> pd.DataFrame:
    """Only identity/clock/status fields enter memory; no price or label columns."""
    return pd.read_csv(path, usecols=columns)[columns]


def make_windows(mothers: pd.DataFrame, requests: pd.DataFrame, statuses: pd.DataFrame,
                 cohort: str) -> pd.DataFrame:
    """K1 windows use ALL mothers; future terminal status never filters K1 features."""
    m, r, s = mothers.copy(), requests.copy(), statuses.copy()
    if cohort not in {"case", "control"}:
        raise ValueError("unknown cohort")
    for frame in [m, r, s]:
        if frame.event_id.isna().any() or frame.event_id.duplicated().any():
            raise ValueError("invalid/duplicated event identity")
    if set(m.event_id) != set(s.event_id) or not set(r.event_id).issubset(m.event_id):
        raise ValueError("mother/status/request identity mismatch")
    if not set(s.status).issubset(ALLOWED_STATUS):
        raise ValueError("unrecognized wait status")
    if set(r.event_id) != set(s.loc[s.status.eq("request_emitted"), "event_id"]):
        raise ValueError("request/status disagreement")
    for frame, columns in [(m, ["signal_time", "decision_time"]),
                           (r, ["mother_signal_time", "mother_decision_time", "k2_time", "decision_time"]),
                           (s, ["mother_signal_time", "mother_decision_time", "terminal_time", "k2_time"])]:
        for col in columns:
            frame[col] = clocks(frame[col], col, missing=col in {"terminal_time", "k2_time"} and frame is s)
    hour = pd.Timedelta(hours=1)
    if not (m.decision_time == m.signal_time + hour).all():
        raise ValueError("K1 close differs from K1 open plus one hour")
    if (m.signal_time < pd.Timestamp("2023-01-01", tz="UTC")).any() or (
        m.decision_time > pd.Timestamp("2025-01-01", tz="UTC")
    ).any():
        raise ValueError("mother outside approved period")
    if not (m.signal_time.astype("int64") % hour.value == 0).all():
        raise ValueError("non-hourly K1")
    ms = m.set_index("event_id")
    for state in s.itertuples(index=False):
        mother = ms.loc[state.event_id]
        if state.mother_signal_time != mother.signal_time or state.mother_decision_time != mother.decision_time:
            raise ValueError("status mother clock disagreement")
    rows = []
    def add(event_id, direction, fold, kind, start, end):
        rows.append(dict(window_id=f"{cohort}:{event_id}:{kind}", event_id=event_id,
            cohort=cohort, fold=fold, window_kind=kind, window_start=start,
            window_end=end, decision_time=end, direction=direction))
    for mother in m.itertuples(index=False):
        add(mother.event_id, mother.direction, mother.fold, "k1", mother.signal_time, mother.decision_time)
    ss = s.set_index("event_id")
    for req in r.itertuples(index=False):
        mother, state = ms.loc[req.event_id], ss.loc[req.event_id]
        gap = (req.k2_time - req.mother_signal_time) / hour
        if (req.mother_signal_time != mother.signal_time or req.mother_decision_time != mother.decision_time
            or req.direction != mother.direction or req.fold != mother.fold
            or gap not in range(1, 9) or req.wait_hours != gap
            or req.decision_time != req.k2_time + hour or state.k2_time != req.k2_time
            or state.terminal_time != req.decision_time):
            raise ValueError("request clock, gap or identity disagreement")
        if req.decision_time > pd.Timestamp("2025-01-01", tz="UTC"):
            raise ValueError("K2 outside approved period")
        add(req.event_id, req.direction, req.fold, "k2", req.k2_time, req.decision_time)
        add(req.event_id, req.direction, req.fold, "post_k1_through_k2", req.mother_decision_time, req.decision_time)
    return pd.DataFrame(rows, columns=["window_id", "event_id", "cohort", "fold", "window_kind",
        "window_start", "window_end", "decision_time", "direction"])


def run() -> None:
    commit, config, flow_summary = checked()
    output = ROOT / config["output_dir"]
    if output.exists() or (HERE / "summary.json").exists():
        raise ValueError("One-shot output already exists; do not overwrite evidence")
    projections, windows, counts = {}, [], {}
    for cohort in ["case", "control"]:
        inputs = config["rosters"][cohort]
        m, r, s = [project(ROOT / inputs[key], columns) for key, columns in
                   [("mothers", MOTHER), ("requests", REQUEST), ("statuses", STATUS)]]
        windows.append(make_windows(m, r, s, cohort))
        projections.update({cohort + "_" + key: frame for key, frame in
                            [("mothers", m), ("requests", r), ("statuses", s)]})
        counts[cohort] = dict(mothers=len(m), requests=len(r), wait_status_counts=s.status.value_counts().to_dict())
    win = pd.concat(windows, ignore_index=True)
    frames = []
    for row in flow_summary["monthly"]:
        f = pd.read_csv(ROOT / row["output_path"], float_precision="round_trip",
            usecols=["open_time", "earliest_available_at", "source_exchange", "symbol"] + AMOUNTS)
        if not f.source_exchange.eq("binance_usdm").all() or not f.symbol.eq("BTCUSDT").all():
            raise ValueError("Wrong venue/symbol")
        frames.append(f)
    flow = pd.concat(frames, ignore_index=True)
    flow.open_time = clocks(flow.open_time, "open_time")
    flow.earliest_available_at = clocks(flow.earliest_available_at, "earliest_available_at")
    if (flow.open_time < pd.Timestamp("2023-01-01", tz="UTC")).any() or (
        flow.open_time >= pd.Timestamp("2025-01-01", tz="UTC")
    ).any():
        raise ValueError("Unauthorized flow clock")
    aligned = align_windows(flow, win)
    live = align_windows(flow, win, availability_mode="observed_delivery")
    if live.flow_defined.any() or not live.status.eq("unavailable_bars").all():
        raise ValueError("Historical input incorrectly called observed delivery")
    # Exact-prefix replay uses every window in each calendar quarter, not winners.
    prefix_checks = []
    for cutoff in pd.date_range("2023-04-01", "2025-01-01", freq="QS", tz="UTC"):
        ids = win.decision_time.le(cutoff)
        truncated = flow.loc[flow.earliest_available_at.le(cutoff)]
        prefix = align_windows(truncated, win.loc[ids])
        pd.testing.assert_frame_equal(prefix, aligned.loc[ids].reset_index(drop=True))
        prefix_checks.append(dict(cutoff=cutoff.isoformat(), windows=int(ids.sum()), exact=True))
    if checked() != (commit, config, flow_summary):
        raise ValueError("Sources changed during audit")
    output.mkdir(parents=True)
    files = {}
    for name, frame in {**projections, "windows": win, "historical_features": aligned,
                        "observed_delivery_features": live}.items():
        path = output / (name + ".csv")
        frame.to_csv(path, index=False, float_format="%.17g")
        files[str(path.relative_to(ROOT))] = dict(sha256=sha(path), rows=len(frame), columns=list(frame))
    grouped = aligned.groupby(["cohort", "window_kind", "status"], sort=True).agg(
        windows=("window_id", "size"), defined=("flow_defined", "sum"),
        source_bars=("available_bars", "sum"), zero_bars=("zero_volume_bars", "sum")).reset_index()
    result = dict(status="alignment_verified_not_economics", generated_at=pd.Timestamp.now(tz="UTC").isoformat(),
        source_commit=commit, config_sha256=sha(HERE / "config.json"), cohorts=counts,
        groups=grouped.to_dict("records"), windows=len(win),
        historical_status_counts=aligned.status.value_counts().to_dict(),
        observed_delivery_status_counts=live.status.value_counts().to_dict(),
        prefix_checks=prefix_checks, files=files, source_flow_bars=len(flow),
        source_flow_zero_bars=int(flow.quote_volume.eq(0).sum()),
        zero_bars_in_event_windows=int(aligned.zero_volume_bars.sum()),
        economic_labels_read=False, holdout_consumed=False, execution_source_replaced=False,
        training_eligible=False, production_eligible=False)
    write_json(HERE / "summary.json", result)
    print(json.dumps({k: v for k, v in result.items() if k != "files"}, indent=2))


if __name__ == "__main__":
    run()

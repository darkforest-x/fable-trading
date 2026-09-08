"""Frozen cross-coin transfer diagnostics; no parameter search or network IO.

The preregistration is exp-imacd-altcoin-transfer-20260909-v1/PROJECT_PLAN.md.
The externally classified 277-name snapshot, original 54 names and development
selection are pinned by SHA256. Local-file availability defines the external
intersection before prices are validated; invalid history keeps its cash sleeve.
SOPH/USELESS remain separate Owner examples. Neither recent gains nor history
length chooses membership. This is a survivor-biased historical cross-coin
diagnostic, not an unseen holdout or evidence for live deployment.

Prepare validates continuous, closed 15m prefixes only, with source/prefix
hashes and read-only source references. Evaluate reuses the parent's 550-bar warmup, prior-week membership,
next-open entries, stop-first exits and matched random controls. Features use
only completed candles; future bars are used exclusively for outcome labels.
Costs remain 20bp of entry notional, without funding. Fixed quantities are 1x
at entry and do not claim a maintained real-time notional/equity cap.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import numpy as np
import pandas as pd

from yoyo.data.altcoin_history import HistoryValidationError, merge_symbol, utc_cutoff
from yoyo.evaluation import altcoin_trend_research as parent


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / "experiments/active/exp-imacd-altcoin-transfer-20260909-v1"
DATA = ROOT / "data/altcoin_transfer_20260909_v1"
CRYPTO_UNIVERSE = ROOT / "experiments/active/exp-1h-okx-model-first-standing-top10-20260904-v1/results/universe.json"
MAIN_UNIVERSE = ROOT / "experiments/active/exp-imacd-yolo-expanded-20260908-v1/universe.json"
SELECTION = parent.EXPERIMENT / "results_clock_v2/development"
CRYPTO_SHA = "e9bf6421dc2fddb61d34a6fed2204f2a04c191d81c9157b52c70213b74f58682"
MAIN_SHA = "023e1e031c64c5d693ada72fa063d30392355e2790861fc30403989f941d66d7"
SELECTION_SHA = "c306749e038da29c0468d44407f378cada9079cc3d8305f5ddcc0b50f94ba951"
# The parent corrected Monday membership to the decision-close clock; the
# replacement identity and unchanged arms are recorded in the preregistration.
SELECTION_CORRECTION_PENDING = False
END = "2026-08-14T04:00:00Z"
PERIODS = (60, 240)
FOLDS = (("audit_pre", "2026-01-01", "2026-05-04"),
         ("audit_seen", "2026-05-04", "2026-07-12"),
         ("external_recent", "2026-07-12", END))
ALLOWED = {
    60: {"base", "exit_chandelier", "exit_fixed3r", "exit_sma60", "gate_box_break"},
    240: {"base", "exit_chandelier", "exit_fixed3r", "exit_sma60", "signal5"},
}
COHORTS = ("all_core", "high_vol")


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc(value):
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def _guard_paths():
    """Include every actually imported local yoyo module, including adapters."""
    paths = {Path(__file__).resolve(), EXPERIMENT / "PROJECT_PLAN.md",
             parent.EXPERIMENT / "PROJECT_PLAN.md"}
    for name, module in tuple(sys.modules.items()):
        file = getattr(module, "__file__", None)
        if name == "yoyo" or name.startswith("yoyo."):
            if file and Path(file).suffix == ".py":
                path = Path(file).resolve()
                if ROOT not in path.parents:
                    raise ValueError("Imported yoyo source lies outside this repository")
                paths.add(path)
    return sorted(paths)


def committed_sources():
    """Use one fixed HEAD for every source comparison, before any real work."""
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    hashes = {}
    for path in _guard_paths():
        relative = str(path.relative_to(ROOT))
        saved = subprocess.check_output(["git", "show", f"{commit}:{relative}"], cwd=ROOT)
        if saved != path.read_bytes():
            raise ValueError("Commit all transfer builders before building: " + relative)
        hashes[relative] = hashlib.sha256(saved).hexdigest()
    return dict(commit=commit, hashes=hashes, numpy=np.__version__, pandas=pd.__version__)


def _selection(folder):
    if SELECTION_CORRECTION_PENDING:
        raise ValueError("Wait for the corrected decision-close membership selection lock")
    selections, digest = parent.read_selection(folder, PERIODS)
    if digest != SELECTION_SHA:
        raise ValueError("The preregistered selection lock changed")
    for minutes in PERIODS:
        values = selections[minutes]["allowed_audit_arms"]
        if len(values) != len(set(values)) or set(values) != ALLOWED[minutes]:
            raise ValueError("Transfer arms differ from the fixed selection")
    return selections, digest


def freeze_universe(crypto_path, main_path, canonical_dir):
    """Choose identities from existing manifests and filenames, never prices."""
    crypto_path, main_path, canonical_dir = map(Path, (crypto_path, main_path, canonical_dir))
    if sha(crypto_path) != CRYPTO_SHA or sha(main_path) != MAIN_SHA:
        raise ValueError("Frozen universe identity changed")
    crypto = json.loads(crypto_path.read_text())
    instruments = crypto["eligible_instruments"]
    if len(instruments) != len(set(instruments)) or any(
            re.fullmatch(r"[A-Z0-9]+-USDT-SWAP", item) is None for item in instruments):
        raise ValueError("Invalid or duplicate classified instrument identities")
    main = json.loads(main_path.read_text())
    excluded = {row["symbol"] for row in main["sources"]} | {"SOPH", "USELESS"}
    wanted = {item.split("-")[0] for item in instruments} - excluded
    paths = {}
    for symbol in sorted(wanted):
        paths[symbol] = sorted(str(p.resolve()) for p in canonical_dir.glob(
            f"okx_{symbol}_USDT_SWAP_15m_*.csv") if re.fullmatch(
                rf"okx_{re.escape(symbol)}_USDT_SWAP_15m_[0-9]+\.csv", p.name))
    pool = sorted(symbol for symbol, files in paths.items() if files)
    if not pool:
        raise ValueError("External local intersection is empty")
    return dict(crypto_universe_path=str(crypto_path.resolve()), crypto_universe_sha256=CRYPTO_SHA,
        crypto_frozen_at=crypto["frozen_at"], classification_rule=crypto["rule"],
        main_universe_path=str(main_path.resolve()), main_universe_sha256=MAIN_SHA,
        excluded_symbols=sorted(excluded), intended_external_symbols=sorted(wanted),
        missing_source_symbols=sorted(wanted-set(pool)), pool_symbols=pool,
        capital_sleeves=len(pool), source_paths={symbol: paths[symbol] for symbol in pool},
        selection_rule="Frozen classified crypto intersect local filenames, minus main54 and Owner illustrations; no length/price/outcome selection")


def _bounded_prefix(source: Path, target: Path, end):
    """Hash all bytes; after the first excluded timestamp, never parse the tail."""
    cutoff = int(utc_cutoff(end).value // 1_000_000)
    full, prefix = hashlib.sha256(), hashlib.sha256()
    with source.open("rb") as original, target.open("wb") as bounded:
        header = original.readline()
        full.update(header)
        if not header.startswith(b"ts,"):
            raise ValueError("Expected a ts-first source CSV")
        bounded.write(header); prefix.update(header)
        for line in original:
            full.update(line)
            timestamp = int(line.partition(b",")[0])
            if timestamp >= cutoff:
                for chunk in iter(lambda: original.read(1024 * 1024), b""):
                    full.update(chunk)
                break
            bounded.write(line); prefix.update(line)
    return dict(path=str(source.resolve()), sha256=full.hexdigest(), prefix_sha256=prefix.hexdigest(),
                validation_scope="Only candles closing by the frozen cutoff; later price fields were not parsed")


def _new_research_path(path):
    path = Path(path).resolve()
    for name in ("kline_fetched", "kline_deep", "kline_cache", "kline_preholdout_archive15m_202109_202306"):
        canonical = (ROOT / "data" / name).resolve()
        if path == canonical or canonical in path.parents:
            raise ValueError("Cannot write into canonical market data")
    return path


def prepare_dataset(out_dir=DATA, *, crypto_path=CRYPTO_UNIVERSE, main_path=MAIN_UNIVERSE,
                    canonical_dir=ROOT/"data/kline_fetched", selection_dir=SELECTION):
    """Freeze all identities first, then validate each prefix without outcomes."""
    sources = committed_sources()
    _, lock_hash = _selection(selection_dir)
    universe = freeze_universe(crypto_path, main_path, canonical_dir)
    target = _new_research_path(out_dir)
    if target.exists():
        raise ValueError("Prepared directory already exists; never overwrite a freeze")
    report = dict(schema_version=1, created_at=pd.Timestamp.now(tz="UTC").isoformat(),
        end_exclusive=END, universe=universe, source_guard=sources,
        selection_dir=str(Path(selection_dir).resolve()), selection_sha256=lock_hash,
        allowed_arms={str(m): sorted(ALLOWED[m]) for m in PERIODS}, symbols=[],
        outcome_calculation=False, historical_first_seen_known=False,
        canonical_market_data_written=False, production_eligible=False, training_eligible=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".transfer-prepare-", dir=target.parent) as temporary:
        staged = Path(temporary)/"prepared"; staged.mkdir()
        for symbol in universe["pool_symbols"]:
            paths = universe["source_paths"][symbol]
            record = dict(symbol=symbol, cohort="external_pool", status="rejected", source_paths=paths)
            if len(paths) != 1:
                record["reason"] = "ambiguous_multiple_canonical_files"
            else:
                prefix = Path(temporary)/"prefix.csv"
                try:
                    receipt = _bounded_prefix(Path(paths[0]), prefix, END)
                    record["source"] = receipt
                    frame, audit = merge_symbol(symbol, [prefix], end=END)
                    # The temporary prefix is a derived receipt, not a durable
                    # market source. Preserve its hash under the canonical ID.
                    audit.pop("sources")
                    record.update(status="complete", audit=audit, rows=len(frame),
                                  prepared_storage="read_only_canonical_reference")
                except HistoryValidationError as error:
                    audit = error.audit.copy(); audit.pop("sources", None)
                    record.update(reason=error.audit["reason"], audit=audit)
                except (OSError, ValueError, OverflowError) as error:
                    record.update(reason="source_prefix_unavailable", error_type=type(error).__name__)
                    if "source" not in record and Path(paths[0]).is_file():
                        record["source"] = dict(path=paths[0], sha256=sha(Path(paths[0])))
            report["symbols"].append(record)
            print(json.dumps(dict(stage="prepare", symbol=symbol, status=record["status"])), flush=True)
        report["complete_symbols"] = sum(row["status"] == "complete" for row in report["symbols"])
        report["unavailable_symbols"] = len(report["symbols"])-report["complete_symbols"]
        report["status"] = "prepared" if not report["unavailable_symbols"] else "prepared_with_unavailable_cash_sleeves"
        parent.dump_json(staged/"manifest.json", report)
        if target.exists():
            raise ValueError("Prepared output appeared during build")
        os.rename(staged, target)
    return report


def _calendar(fold, minutes):
    _, start, end = fold
    return pd.date_range(_utc(start)+pd.Timedelta(minutes=minutes), _utc(end), freq=f"{minutes}min")


def combine_sleeves(curves, calendar, denominator):
    """Equal fixed sleeves: no redistribution when sources/trades are absent."""
    if denominator <= 0 or len(curves) > denominator:
        raise ValueError("Invalid frozen capital denominator")
    total = pd.Series(float(denominator-len(curves)), index=calendar)
    for curve in curves.values():
        if not isinstance(curve.index, pd.DatetimeIndex) or str(curve.index.tz) != "UTC":
            raise ValueError("Equity curves require UTC close timestamps")
        if curve.empty or not np.isfinite(curve.to_numpy()).all():
            raise ValueError("Equity curve is empty or nonfinite")
        expected = calendar[calendar >= curve.index[0]]
        if not curve.index.equals(expected):
            raise ValueError("Missing, duplicated or misaligned equity closes")
        total += curve.reindex(calendar).fillna(1.)
    return total/denominator


def summarize_transfer(out_dir, manifest, *, periods=PERIODS, folds=FOLDS):
    """Summarize fixed arms with common matched coverage and fixed cash weights."""
    out_dir = Path(out_dir)
    denominator = manifest["universe"]["capital_sleeves"]
    events, control_frames, curves, diagnostics = [], [], {}, []
    for record in manifest["symbols"]:
        symbol = record["symbol"]
        for minutes in periods:
            folder = out_dir/f"{symbol}_{minutes}"
            if not (folder/"completion.json").exists():
                continue
            for file, accumulator in (("events.csv.gz", events), ("controls.csv.gz", control_frames)):
                try: frame = pd.read_csv(folder/file)
                except pd.errors.EmptyDataError: frame = pd.DataFrame()
                if len(frame): accumulator.append(frame)
            for row in json.loads((folder/"diagnostics.json").read_text()):
                diagnostics.append(row)
            frame = pd.read_pickle(folder/"curves.pkl.gz", compression="gzip")
            for key in frame:
                parts = key.split("|")
                fold, cohort, arm = parts[:3]
                if arm not in ALLOWED[minutes] or cohort not in COHORTS:
                    raise ValueError("Unexpected event arm/cohort in transfer result")
                fullkey = (fold, minutes, cohort, arm, len(parts) == 4)
                curves.setdefault(fullkey, {})[symbol] = frame[key].dropna()
    events = pd.concat(events, ignore_index=True) if events else pd.DataFrame()
    events.to_csv(out_dir/"events_all.csv.gz", index=False)
    (pd.concat(control_frames, ignore_index=True) if control_frames else pd.DataFrame()).to_csv(
        out_dir/"controls_all.csv.gz", index=False)
    rows, monthly, portfolio = [], [], {}
    for fold in folds:
        for minutes in periods:
            calendar = _calendar(fold, minutes)
            for cohort in COHORTS:
                for arm in sorted(ALLOWED[minutes]):
                    key = (fold[0], minutes, cohort, arm)
                    if set(curves.get((*key, False), {})) != set(curves.get((*key, True), {})):
                        raise ValueError("Adverse-bound curve coverage differs from close equity")
                    equity = combine_sleeves(curves.get((*key, False), {}), calendar, denominator)
                    adverse = combine_sleeves(curves.get((*key, True), {}), calendar, denominator)
                    name = "|".join(map(str, key))
                    portfolio[name], portfolio[name+"|adverse"] = equity, adverse
                    g = events.loc[(events.fold==fold[0]) & (events.minutes==minutes)
                        & (events.cohort==cohort) & (events.arm==arm)].copy() if len(events) else pd.DataFrame()
                    if len(g) and (not g.valid.eq(True).all() or not np.isfinite(g.net_bp).all()):
                        raise ValueError("Invalid events cannot enter transfer summaries")
                    matched = g.loc[g.excess_bp.notna()] if len(g) else g
                    net = g.net_bp if len(g) else pd.Series(dtype=float)
                    top = g.sort_values(["net_bp", "event_id"], ascending=[False, True]).head(3) if len(g) else g
                    profit, loss = net.clip(lower=0).sum(), -net.clip(upper=0).sum()
                    natural = g.loc[g.natural_exit] if len(g) else g
                    row = dict(fold=fold[0], minutes=minutes, cohort=cohort, arm=arm,
                        study="external_cross_coin_diagnostic", n=len(g), symbols=g.symbol.nunique() if len(g) else 0,
                        natural_n=len(natural), censored_n=len(g)-len(natural),
                        selected_n=int(g.portfolio_selected.sum()) if len(g) else 0,
                        matched_n=len(matched), unmatched_n=len(g)-len(matched),
                        matched_symbols=matched.symbol.nunique() if len(matched) else 0,
                        mean_gross_bp=g.gross_bp.mean() if len(g) else np.nan,
                        mean_net_bp=net.mean(), median_net_bp=net.median(),
                        natural_mean_net_bp=natural.net_bp.mean() if len(natural) else np.nan,
                        win_pct=net.gt(0).mean()*100 if len(net) else np.nan,
                        profit_factor=profit/loss if loss else np.nan,
                        matched_case_net_bp=matched.net_bp.mean() if len(matched) else np.nan,
                        control_net_bp=matched.control_mean_net_bp.mean() if len(matched) else np.nan,
                        mean_net_ex_top3_bp=g.loc[~g.index.isin(top.index)].net_bp.mean() if len(g)>3 else np.nan,
                        top3_positive_profit_pct=top.net_bp.clip(lower=0).sum()/profit*100 if profit else np.nan,
                        median_mfe_r=g.mfe_r.median() if len(g) else np.nan,
                        median_net_r=g.net_r.median() if len(g) else np.nan,
                        median_capture_ratio=g.capture_ratio.median() if len(g) else np.nan,
                        portfolio_net_pct=(equity.iloc[-1]-1)*100,
                        mdd_pct=(equity/equity.cummax().clip(lower=1)-1).min()*100,
                        adverse_mdd_bound_pct=(adverse/equity.cummax().clip(lower=1)-1).min()*100,
                        capital_sleeves=denominator, curve_symbols=len(curves.get((*key, False), {})),
                        cost_bp=20, funding_included=False,
                        **(parent.inference(matched.excess_bp, matched.month) if len(matched) else
                           dict(excess_bp=np.nan, p=np.nan, ci_low=np.nan, ci_high=np.nan, months=0)))
                    rows.append(row)
                    delta = equity.diff(); delta.iloc[0] = equity.iloc[0]-1
                    month = (calendar-pd.Timedelta(nanoseconds=1)).strftime("%Y-%m")
                    for month_name in sorted(set(month)):
                        loc = np.flatnonzero(month == month_name)
                        starting = equity.iloc[loc[0]-1] if loc[0] else 1.
                        ending = equity.iloc[loc[-1]]
                        monthly.append(dict(fold=fold[0], minutes=minutes, cohort=cohort, arm=arm,
                            month=month_name, capital_sleeves=denominator,
                            start_equity=starting, end_equity=ending,
                            initial_capital_change_bp=delta.iloc[loc].sum()*10000,
                            net_pct=(ending/starting-1)*100 if starting>0 else np.nan))
    summary = pd.DataFrame(rows)
    summary["p_holm"] = np.nan
    for _, group in summary.groupby(["fold", "minutes", "cohort"]):
        summary.loc[group.index, "p_holm"] = parent.holm(group.p, family_size=len(ALLOWED[int(group.minutes.iloc[0])]))
    for _, group in summary.loc[summary.cohort.eq("high_vol")].groupby("fold"):
        summary.loc[group.index, "p_holm_primary"] = parent.holm(group.p, family_size=sum(len(ALLOWED[m]) for m in periods))
    summary.to_csv(out_dir/"summary.csv", index=False)
    pd.DataFrame(monthly).to_csv(out_dir/"monthly_portfolio.csv", index=False)
    pd.DataFrame(portfolio).to_pickle(out_dir/"portfolio_curves.pkl.gz", compression="gzip")
    parent.dump_json(out_dir/"diagnostics_all.json", diagnostics)
    return summary


def _result_receipt(folder, identity):
    names = ("events.csv.gz", "controls.csv.gz", "curves.pkl.gz", "diagnostics.json")
    return dict(identity=identity, files={name: sha(folder/name) for name in names})


def evaluate_dataset(prepared_dir=DATA, out_dir=EXPERIMENT/"results", *, selection_dir=SELECTION):
    """Evaluate the locked arms only; no new winners or promotion are selected."""
    sources = committed_sources()
    selections, lock_hash = _selection(selection_dir)
    prepared_dir = _new_research_path(prepared_dir)
    manifest_path = prepared_dir/"manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest["end_exclusive"] != END or manifest["selection_sha256"] != lock_hash:
        raise ValueError("Prepared cutoff or selection differs from preregistration")
    universe = manifest["universe"]
    symbols = [row["symbol"] for row in manifest["symbols"]]
    if (symbols != universe["pool_symbols"] or len(symbols) != len(set(symbols))
            or len(symbols) != universe["capital_sleeves"]
            or universe["crypto_universe_sha256"] != CRYPTO_SHA or universe["main_universe_sha256"] != MAIN_SHA):
        raise ValueError("Prepared universe/denominator identity changed")
    if manifest["allowed_arms"] != {str(m): sorted(ALLOWED[m]) for m in PERIODS}:
        raise ValueError("Prepared allowed arms changed")
    target = _new_research_path(out_dir)
    if target == prepared_dir or prepared_dir in target.parents:
        raise ValueError("Results must be separate from frozen prepared inputs")
    identity = dict(prepared_sha256=sha(manifest_path), selection_sha256=lock_hash,
                    source_guard=sources, folds=FOLDS, periods=PERIODS)
    # Normalize tuples before comparison with the serialized resume identity.
    identity = json.loads(json.dumps(identity))
    if target.exists():
        state = target/"run_manifest.json"
        if not state.exists() or json.loads(state.read_text())["identity"] != identity:
            raise ValueError("Output identity changed; use a new result directory")
    else:
        target.mkdir(parents=True)
        parent.dump_json(target/"run_manifest.json", dict(identity=identity,
            created_at=pd.Timestamp.now(tz="UTC").isoformat(),
            historical_cross_coin_diagnostic=True, unseen_holdout=False,
            parameter_selection_permitted=False, production_eligible=False))
    cache_key = json.dumps(dict(identity=identity, out_dir=str(target)), sort_keys=True)
    cache = prepared_dir/"feature_cache"/hashlib.sha256(cache_key.encode()).hexdigest()[:16]
    cache.mkdir(parents=True, exist_ok=True)
    base = {m: {} for m in PERIODS}; paths = {}; availability = []
    for record in manifest["symbols"]:
        symbol = record["symbol"]
        if record["status"] != "complete":
            for minutes in PERIODS:
                availability.append(dict(symbol=symbol, minutes=minutes, fold="all",
                    status="unavailable_cash", reason=record["reason"]))
            continue
        raw = parent.read_history(dict(output_path=record["source"]["path"],
            output_sha256=record["source"]["sha256"]), _utc(END))
        for minutes in PERIODS:
            bars = parent.aggregate(raw, minutes)
            if len(bars) < parent.COMMON_WARMUP+2:
                availability.append(dict(symbol=symbol, minutes=minutes, fold="all",
                    status="unavailable_cash", reason="insufficient_common_warmup", bars=len(bars)))
                continue
            features = parent.build_altcoin_features(bars, parent.PARAMS["base"])
            for column in bars: features[column] = bars[column]
            path = cache/f"{symbol}_{minutes}_base.pkl.gz"
            temporary = path.with_suffix(".tmp")
            features.to_pickle(temporary, compression="gzip"); temporary.replace(path)
            paths[(symbol, minutes)] = {"base": path}
            base[minutes][symbol] = features[["prior7d_atr_pct_mean", "ready"]]
            for fold in FOLDS:
                inside = (bars.index >= _utc(fold[1])) & (bars.index+pd.Timedelta(minutes=minutes) <= _utc(fold[2]))
                ready = inside & (np.arange(len(bars)) >= parent.COMMON_WARMUP) & features.ready
                availability.append(dict(symbol=symbol, minutes=minutes, fold=fold[0], status="prepared",
                    bars=int(inside.sum()), warmed_bars=int(ready.sum()),
                    source_start=bars.index[0].isoformat(), source_end_close=(bars.index[-1]+pd.Timedelta(minutes=minutes)).isoformat()))
        print(json.dumps(dict(stage="features", symbol=symbol)), flush=True)
    memberships = {m: parent.weekly_membership(base[m]) for m in PERIODS}
    for minutes, members in memberships.items():
        members.to_csv(target/f"weekly_membership_{minutes}.csv", index=False)
    pd.DataFrame(availability).to_csv(target/"availability.csv", index=False)
    for record in manifest["symbols"]:
        symbol = record["symbol"]
        for minutes in PERIODS:
            if (symbol, minutes) not in paths: continue
            folder = target/f"{symbol}_{minutes}"
            if folder.exists():
                receipt = json.loads((folder/"completion.json").read_text())
                if receipt != _result_receipt(folder, identity):
                    raise ValueError("Completed symbol output changed")
                for path in paths[(symbol, minutes)].values(): path.unlink()
                continue
            with tempfile.TemporaryDirectory(prefix=".transfer-evaluate-", dir=target) as temporary:
                work = Path(temporary)
                parent.evaluate_symbol(symbol, record, minutes, paths[(symbol, minutes)], memberships[minutes],
                    FOLDS, work, allowed_arms=selections[minutes]["allowed_audit_arms"])
                staged = work/f"{symbol}_{minutes}"
                parent.dump_json(staged/"completion.json", _result_receipt(staged, identity))
                os.rename(staged, folder)
            for path in paths[(symbol, minutes)].values(): path.unlink()
            print(json.dumps(dict(stage="evaluated", symbol=symbol, minutes=minutes)), flush=True)
    summary = summarize_transfer(target, manifest, periods=PERIODS, folds=FOLDS)
    parent.dump_json(target/"completion.json", dict(completed_at=pd.Timestamp.now(tz="UTC").isoformat(),
        identity=identity, summary_sha256=sha(target/"summary.csv"), summary_rows=len(summary),
        capital_sleeves=universe["capital_sleeves"], no_parameter_selection=True))
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="stage", required=True)
    prepare = sub.add_parser("prepare", help="Freeze identities and validate bounded history; no outcomes")
    prepare.add_argument("--out-dir", type=Path, default=DATA)
    prepare.add_argument("--crypto-universe", type=Path, default=CRYPTO_UNIVERSE)
    prepare.add_argument("--main-universe", type=Path, default=MAIN_UNIVERSE)
    prepare.add_argument("--canonical-dir", type=Path, default=ROOT/"data/kline_fetched")
    prepare.add_argument("--selection", type=Path, default=SELECTION)
    evaluate = sub.add_parser("evaluate", help="Evaluate only the locked cross-coin transfer arms")
    evaluate.add_argument("--prepared-dir", type=Path, default=DATA)
    evaluate.add_argument("--out-dir", type=Path, default=EXPERIMENT/"results")
    evaluate.add_argument("--selection", type=Path, default=SELECTION)
    args = parser.parse_args(argv)
    try:
        if args.stage == "prepare":
            report = prepare_dataset(args.out_dir, crypto_path=args.crypto_universe,
                main_path=args.main_universe, canonical_dir=args.canonical_dir, selection_dir=args.selection)
            print(json.dumps(dict(status=report["status"], capital_sleeves=report["universe"]["capital_sleeves"],
                                  complete_symbols=report["complete_symbols"], unavailable_symbols=report["unavailable_symbols"])))
        else:
            summary = evaluate_dataset(args.prepared_dir, args.out_dir, selection_dir=args.selection)
            print(json.dumps(dict(status="complete", summary_rows=len(summary))))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print("Transfer study refused: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

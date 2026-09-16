"""Package the frozen RSI-zone comparison evidence; never reads market data."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EXP = Path(__file__).resolve().parent
REPORT = "analysis/p1_eth_bb_stoch_rsi_filter_20260916.md"
HTML = "analysis/html/p1_eth_bb_stoch_rsi_filter_20260916.html"
CHART = "analysis/html/eth_bb_stoch_rsi_filter_20260916_equity.png"
EXPERIMENT_ID = "exp-eth-bb-stoch-rsi-filter-20260916-v1"
ARTIFACT_ID = "eth-bb-stoch-rsi-filter-20260916-delivery"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def number(value, digits=4):
    return format(value, f".{digits}f")


def main():
    result = json.loads((EXP / "results.json").read_text())
    cfg = result["config"]
    primary = result["arms"][cfg["primary_arm"]][cfg["primary_path"]]["all"]
    base = result["arms"]["unfiltered"][cfg["primary_path"]]["all"]
    ps, pc, events = primary["stats"], primary["control"], result["event_level"]
    bs = base["stats"]
    excluded = {"delivery_manifest.json", "delivery_validation.json",
                "artifacts_registry_append.yaml", "experiments_registry_append.yaml"}
    files = [p for p in EXP.iterdir() if p.is_file() and p.name not in excluded]
    files += [ROOT / REPORT, ROOT / HTML, ROOT / CHART]
    files += [ROOT / p for p in json.loads((EXP / "code_receipt.json").read_text())["files"]
              if ROOT / p not in files]
    records = [{"path": str(p.relative_to(ROOT)), "sha256": digest(p), "size_bytes": p.stat().st_size}
               for p in sorted(set(files))]
    manifest = dict(experiment_id=EXPERIMENT_ID, source_commit=result["source_commit"],
                    holdout_consumed=False, native_full_ledger_parity=False,
                    pine_saved_to_tradingview=False, files=records)
    target = EXP / "delivery_manifest.json"
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    block = f"""
  - experiment_id: {EXPERIMENT_ID}
    source_repo: darkforest-x/fable-trading
    source_commit: {result['source_commit']}
    status: inconclusive
    question: Does a ChartPrime Parabolic RSI zone gate on entries improve the frozen BB x Stoch v2 rule?
    single_variable: Only new-entry admission changes; exits, costs, stop and targets stay at v2 values. rsi_both is a predeclared exit-side sensitivity, not a second tuning trial.
    result: {ps['natural']}complete trades net{number(ps['net_r'])}R vs baseline {bs['natural']}/{number(bs['net_r'])}R; per-trade {number(bs['mean_net_r'])}->{number(ps['mean_net_r'])}R, maxlossstreak {bs['max_loss_streak']}->{ps['max_loss_streak']}. Matched {pc['paired_n']}pairs excess{number(pc['excess_mean_net_r'], 5)}R,p{number(pc['p'])}. Event-level passed {events['passed_n']} {number(events['passed_mean_net_r'])}R vs blocked {events['blocked_n']} {number(events['blocked_mean_net_r'])}R,diff{number(events['mean_difference'])},p{number(events['p'], 3)}. No improvement established;both arms net negative.
    holdout_consumed: false
    training_eligible: false
    production_eligible: false
    canonical_report: {REPORT}
    artifacts: [{ARTIFACT_ID}]
    notes: Same OKXnative5m prefix ending2026-05-01 as the v2 replay;unfiltered arm reproduces that ledger trade by trade. Gate blocks {result['counts']['gate_blocked']}of{result['counts']['admissible']} candidates;long median RSI{number(result['counts']['long']['median_rsi'], 2)},short{number(result['counts']['short']['median_rsi'], 2)}. Pine v3 written but NOT saved or compiled on TradingView;owner TV inputs unverified.
"""
    ablock = f"""
  - artifact_id: {ARTIFACT_ID}
    artifact_type: manifest
    role: frozen_rsi_zone_entry_gate_comparison_on_bb_stoch_v2
    source_repo: darkforest-x/fable-trading
    source_commit: {result['source_commit']}
    source_path: {target.relative_to(ROOT)}
    sha256: {digest(target)}
    size_bytes: {target.stat().st_size}
    storage_uri: repo://{target.relative_to(ROOT)}
    holdout_status: pre_holdout
    training_eligible: false
    production_eligible: false
    notes: Three arms x two paths plus {events['events']}event-level replays;24focused checks pass,468repository gate checks pass with7 pre-existing unrelated failures. No live change,no promotion.
"""
    for name, registry, key, text in [("experiments_registry_append.yaml", "experiments/registry.yaml", EXPERIMENT_ID, block),
                                      ("artifacts_registry_append.yaml", "artifacts/registry.yaml", ARTIFACT_ID, ablock)]:
        (EXP / name).write_text(text)
        path = ROOT / registry
        current = path.read_text()
        if key not in current:
            path.write_text(current.rstrip() + "\n" + text)
    for record in records:
        assert digest(ROOT / record["path"]) == record["sha256"]
    html = (ROOT / HTML).read_text()
    assert '<meta charset="utf-8">' in html and "<table>" in html and "<img " in html
    (EXP / "delivery_validation.json").write_text(json.dumps(dict(
        passed=True, files=len(records), manifest_sha256=digest(target),
        html_has_tables_and_chart=True, source_price_read=False), indent=2) + "\n")
    print(json.dumps({"files": len(records), "manifest_sha256": digest(target)}))


if __name__ == "__main__":
    main()

"""Package the frozen 28-month diagnosis evidence; never reads market data."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EXP = Path(__file__).resolve().parent
REPORT = "analysis/p1_eth_bb_stoch_longrun_20260916.md"
HTML = "analysis/html/p1_eth_bb_stoch_longrun_20260916.html"
CHART = "analysis/html/eth_bb_stoch_longrun_20260916_equity.png"
EXPERIMENT_ID = "exp-eth-bb-stoch-longrun-diagnosis-20260916-v1"
ARTIFACT_ID = "eth-bb-stoch-longrun-diagnosis-20260916-delivery"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def num(value, digits=4):
    return format(value, f".{digits}f")


def main():
    result = json.loads((EXP / "results.json").read_text())
    cfg = result["config"]
    ref = cfg["reference_arm"]
    arms = {a: result["arms"][a][cfg["primary_path"]]["all"] for a in cfg["arms"]}
    rs, rc = arms[ref]["stats"], arms[ref]["control"]
    base = arms["v2_baseline"]["stats"]
    star = result["fee_curves"][ref][-1]["value"]
    excluded = {"delivery_manifest.json", "delivery_validation.json",
                "artifacts_registry_append.yaml", "experiments_registry_append.yaml"}
    files = [p for p in EXP.iterdir() if p.is_file() and p.name not in excluded]
    files += [ROOT / REPORT, ROOT / HTML, ROOT / CHART]
    files += [ROOT / p for p in json.loads((EXP / "code_receipt.json").read_text())["files"]
              if ROOT / p not in files]
    records = [{"path": str(p.relative_to(ROOT)), "sha256": digest(p), "size_bytes": p.stat().st_size}
               for p in sorted(set(files))]
    manifest = dict(experiment_id=EXPERIMENT_ID, source_commit=result["source_commit"],
                    holdout_consumed=False, parameter_search=False,
                    native_full_ledger_parity=False, pine_saved_to_tradingview=False, files=records)
    target = EXP / "delivery_manifest.json"
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    gates = " ".join(f"{g}:{result['counts'][g]['passed']}" for g in
                     ("rsi_line", "sar_strong_same_bar", "sar_strong_window", "sar_direction"))
    block = f"""
  - experiment_id: {EXPERIMENT_ID}
    source_repo: darkforest-x/fable-trading
    source_commit: {result['source_commit']}
    status: negative
    question: Over 28 months, does owner-approved break-even-with-cost or any ChartPrime RSI/SAR entry gate rescue the BB x Stoch v2 rule?
    single_variable: Seven pre-registered arms, each one change from the be_cost reference. No parameter search exists in the builder.
    result: All seven arms lose after fees AND before fees. Reference {rs['natural']}trades net{num(rs['net_r'], 2)}R perTrade{num(rs['mean_net_r'])}R PF{num(rs['profit_factor'], 3)} grossR{num(rs['gross_r'], 2)}; v2 baseline {base['natural']}/{num(base['net_r'], 2)}R. Breakeven fee f*={num(1e4 * star, 2)}bp per side, i.e. negative, so zero fees still lose. Every gate made per-trade WORSE; event-level label permutations p 0.49-0.74 with no gate carrying information. Matched control excess{num(rc['excess_mean_net_r'], 5)}R p{num(rc['p'], 3)}.
    holdout_consumed: false
    training_eligible: false
    production_eligible: false
    canonical_report: {REPORT}
    artifacts: [{ARTIFACT_ID}]
    notes: OKX official monthly 1m archive aggregated to 5m, {result['counts']['bars']}bars 2023-12-31..2026-04-30, cross-checked byte-identical against the prior CSV on 37800 overlapping bars. 23 of these months were unused by this strategy family and are now spent. Gate pass counts {gates} of {result['counts']['admissible']}. Owner approved BE-with-cost only; the losing-side target guard was NOT approved and its losses remain.
"""
    ablock = f"""
  - artifact_id: {ARTIFACT_ID}
    artifact_type: manifest
    role: frozen_28_month_bb_stoch_gate_and_cost_diagnosis
    source_repo: darkforest-x/fable-trading
    source_commit: {result['source_commit']}
    source_path: {target.relative_to(ROOT)}
    sha256: {digest(target)}
    size_bytes: {target.stat().st_size}
    storage_uri: repo://{target.relative_to(ROOT)}
    holdout_status: pre_holdout
    training_eligible: false
    production_eligible: false
    notes: Seven arms plus {result['event_level']['events']}occupancy-free event replays;43focused checks pass,468repository gate checks pass with7 pre-existing unrelated failures. No live change,no promotion.
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

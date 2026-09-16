"""Package the BTC/XAU timeframe evidence; never reads market data."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EXP = Path(__file__).resolve().parent
REPORT = "analysis/p1_btc_xau_bb_stoch_timeframes_20260916.md"
HTML = "analysis/html/p1_btc_xau_bb_stoch_timeframes_20260916.html"
CHART = "analysis/html/btc_xau_bb_stoch_timeframes_20260916_equity.png"
EXPERIMENT_ID = "exp-btc-xau-bb-stoch-timeframes-20260916-v1"
ARTIFACT_ID = "btc-xau-bb-stoch-timeframes-20260916-delivery"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def num(value, digits=4):
    return "NA" if value is None else format(value, f".{digits}f")


def main():
    cfg = json.loads((EXP / "config.json").read_text())
    loaded = {k: json.loads((EXP / k / "results.json").read_text()) for k in cfg["datasets"]}
    ref = cfg["reference_arm"]
    excluded = {"delivery_manifest.json", "delivery_validation.json",
                "artifacts_registry_append.yaml", "experiments_registry_append.yaml"}
    files = [p for p in EXP.rglob("*") if p.is_file() and p.name not in excluded]
    files += [ROOT / REPORT, ROOT / HTML, ROOT / CHART]
    files += [ROOT / p for p in json.loads((EXP / "btc_5m" / "code_receipt.json").read_text())["files"]
              if ROOT / p not in files]
    records = [{"path": str(p.relative_to(ROOT)), "sha256": digest(p), "size_bytes": p.stat().st_size}
               for p in sorted(set(files))]
    target = EXP / "delivery_manifest.json"
    target.write_text(json.dumps(dict(experiment_id=EXPERIMENT_ID,
                                      source_commit=loaded["btc_5m"]["source_commit"],
                                      holdout_consumed=False, parameter_search=False,
                                      retuned_for_new_market=False, files=records),
                                 ensure_ascii=False, indent=2) + "\n")
    summary = "; ".join(
        f"{k} n={loaded[k]['arms'][ref]['all']['stats']['natural']} "
        f"perTrade{num(loaded[k]['arms'][ref]['all']['stats']['mean_net_r'])}R "
        f"gross{num(loaded[k]['fee_curves'][ref][0]['net_r'], 2)}R "
        f"fstar{num(1e4 * (loaded[k]['fee_curves'][ref][-1]['value'] or 0), 2)}bp" for k in loaded)
    block = f"""
  - experiment_id: {EXPERIMENT_ID}
    source_repo: darkforest-x/fable-trading
    source_commit: {loaded['btc_5m']['source_commit']}
    status: negative
    question: Does the frozen ETH BB x Stoch rule work on BTC 5m/1m and XAU 1m without any retuning?
    single_variable: Market and bar duration only. BB200, Stoch5/3/3, 3percent stop and 10bp per side stay at their ETH values.
    result: No arm is net positive anywhere. Reference {summary}. Unlike ETH, BTC gross is POSITIVE but tiny on both durations (best +4.98R/697 on 5m, +11.74R/3159 on 1m); the breakeven fee is +0.51bp per side against an OKX minimum maker of 2bp, so it is not reachable. XAU gross is about zero on 3.5 months only. The RSI zone gate reduced gross on every market.
    holdout_consumed: false
    training_eligible: false
    production_eligible: false
    canonical_report: {REPORT}
    artifacts: [{ARTIFACT_ID}]
    notes: OKX official monthly archives; BTC 245088bars 5m and 1225440bars 1m 2023-12-31..2026-04-30, XAU-USDT-SWAP only lists from 2026-01-15 so 151674bars. XAUT-USDT-SWAP has no archive data. Each series declares its own bar duration; the engine five-minute gap default is never used for 1m.
"""
    ablock = f"""
  - artifact_id: {ARTIFACT_ID}
    artifact_type: manifest
    role: frozen_cross_market_cross_timeframe_bb_stoch_replay
    source_repo: darkforest-x/fable-trading
    source_commit: {loaded['btc_5m']['source_commit']}
    source_path: {target.relative_to(ROOT)}
    sha256: {digest(target)}
    size_bytes: {target.stat().st_size}
    storage_uri: repo://{target.relative_to(ROOT)}
    holdout_status: pre_holdout
    training_eligible: false
    production_eligible: false
    notes: Three markets/durations x three arms run as parallel processes;37focused checks pass,468repository gate checks pass with7 pre-existing unrelated failures. No live change,no promotion,no retuning.
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

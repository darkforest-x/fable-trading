"""Package existing pre-only BB replay evidence; never reads raw market data."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EXP = Path(__file__).resolve().parent

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    result = json.loads((EXP / "results.json").read_text())
    excluded = {"delivery_manifest.json", "delivery_validation.json", "artifacts_registry_append.yaml", "experiments_registry_append.yaml"}
    files = [p for p in EXP.iterdir() if p.is_file() and p.name not in excluded]
    files += [ROOT / "analysis/p1_eth_bb_stoch_backtest_20260916.md", ROOT / "analysis/html/p1_eth_bb_stoch_backtest_20260916.html", ROOT / "analysis/html/eth_bb_stoch_backtest_20260916_equity.png"]
    files += [ROOT / p for p in json.loads((EXP / "code_receipt.json").read_text())["files"] if ROOT / p not in files]
    records = [{"path":str(p.relative_to(ROOT)), "sha256":digest(p), "size_bytes":p.stat().st_size} for p in sorted(set(files))]
    manifest = dict(experiment_id=result["config"]["experiment_id"], source_commit=result["source_commit"], holdout_consumed=False, native_full_ledger_parity=False, files=records)
    target = EXP / "delivery_manifest.json"
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n")
    expid=result["config"]["experiment_id"]
    artifactid="eth-bb-stoch-backtest-20260916-delivery"
    report="analysis/p1_eth_bb_stoch_backtest_20260916.md"
    block=f"""
  - experiment_id: {expid}
    source_repo: darkforest-x/fable-trading
    source_commit: {result['source_commit']}
    status: inconclusive
    question: Does frozen owner whole-wick BB200 Stoch5m with3percent stop and dynamic half exit have a net entry advantage?
    single_variable: Fixed v2 first economic replay; no strategy parameter search. Two predeclared OHLC paths are execution sensitivity only.
    result: 75complete trades net-11.0176R,win45.33percent,PF(R).7043,realizedDD13.7338R. Matched74events excess-.03273R,p.75;positive profitability not established.
    holdout_consumed: false
    training_eligible: false
    production_eligible: false
    canonical_report: {report}
    artifacts: [{artifactid}]
    notes: OKXnative5m permitted37896bars ending2026-05-01;1boundary position separate. 5marketable protection approximations;no native broker ledger parity,no V1 gate.
"""
    ablock=f"""
  - artifact_id: {artifactid}
    artifact_type: manifest
    role: frozen_bb_stoch_pre_only_economic_replay_with_matched_null
    source_repo: darkforest-x/fable-trading
    source_commit: {result['source_commit']}
    source_path: {target.relative_to(ROOT)}
    sha256: {digest(target)}
    size_bytes: {target.stat().st_size}
    storage_uri: repo://{target.relative_to(ROOT)}
    holdout_status: pre_holdout
    training_eligible: false
    production_eligible: false
    notes: 75natural plus1censored,380controls per path,23focused checks pass;projectvenv45checks pass. Seven unrelated registry/migration failures remain. No live change.
"""
    for name, registry, key, blocktext in [("experiments_registry_append.yaml", "experiments/registry.yaml", expid, block), ("artifacts_registry_append.yaml", "artifacts/registry.yaml", artifactid, ablock)]:
        (EXP/name).write_text(blocktext)
        path=ROOT/registry
        current=path.read_text()
        if key not in current: path.write_text(current.rstrip()+"\n"+blocktext)
    for record in records:
        assert digest(ROOT/record["path"]) == record["sha256"]
    html=(ROOT/"analysis/html/p1_eth_bb_stoch_backtest_20260916.html").read_text()
    assert '<meta charset="utf-8">' in html and '<table>' in html and '<img ' in html
    (EXP/"delivery_validation.json").write_text(json.dumps(dict(passed=True, files=len(records), manifest_sha256=digest(target), html_has_tables_and_chart=True, source_price_read=False),indent=2)+"\n")
    print(json.dumps({"files":len(records), "manifest_sha256":digest(target)}))

if __name__ == "__main__":
    main()

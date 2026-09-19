"""Retrospective semantic anchors, never promoted to geometric gold.

Use only the originally proposed L5/core bounds and past OHLC for features.
The owner's old verdicts were retrospective. Rejected boxes have no approved
core; measuring their machine proposals cannot estimate classifier accuracy.
"""
import json
from pathlib import Path
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.spike_burst_replay import features
from yoyo.evaluation.spike_ma_density import MAS, diagnostics
from yoyo.evaluation.spike_ma_density_study import EXP, digest

V1 = Path("experiments/active/exp-15m-ma-launch-ma-box-review50-v1/results/review_manifest.jsonl")
V5 = Path("experiments/active/exp-15m-ma-launch-owner-strict-review50-v5/results/review_manifest.jsonl")


def geometry(f):
    """Read six MA values and open/close within the explicitly supplied core."""
    ma = f[list(MAS)].to_numpy()
    body_lo = f[["open","close"]].min(axis=1).to_numpy()
    body_hi = f[["open","close"]].max(axis=1).to_numpy()
    inside = (ma >= body_lo[:,None]) & (ma <= body_hi[:,None])
    span = ma.max(axis=1) - ma.min(axis=1)
    return dict(core_bars=len(f),width_atr_median=float(np.median(span/f.atr)),
                width_atr_max=float(np.max(span/f.atr)),price_width_median_pct=float(np.median(span/f.close)*100),
                body_ma_count_mean=float(inside.sum(axis=1).mean()),body_touches_3plus_bars=int((inside.sum(axis=1)>=3).sum()),
                body_touches_any_bars=int(inside.any(axis=1).sum()))


def main():
    own = Path(__file__).relative_to(Path.cwd())
    assert not subprocess.check_output(["git","status","--porcelain","--",str(own)],text=True).strip()
    old = [json.loads(line) for line in V1.read_text().splitlines()]
    new = [json.loads(line) for line in V5.read_text().splitlines()]
    rows = []
    provenance = {str(p):digest(p) for p in (own,V1,V5)}
    for order in (3,8,20,21,22,34,42,44,48):
        a,b = old[order-1],new[order-1]
        assert a["sample_id"] == b["sample_id"] and not b["sample_owner_geometry_confirmed"]
        path = Path(b["source_path"])
        provenance[str(path)] = digest(path)
        base = pd.read_csv(path)
        clock = pd.to_datetime(base.ts,unit="ms",utc=True)
        assert str(clock.iloc[a["source_anchor_i"]]) == str(pd.Timestamp(a["anchor_time"]))
        start = a["source_anchor_i"] + (b["core_start_offset"] if b["has_box_proposal"] else a["l5_start_offset"])
        end = a["source_anchor_i"] + (b["core_end_offset"] if b["has_box_proposal"] else a["l5_end_offset"])
        base.index = clock
        f = features(base.iloc[:end+2].copy())
        d = diagnostics(f,15).iloc[-1]
        row = dict(order=order,sample_id=b["sample_id"],symbol=b["symbol"],status=b["status"],
                   reason=b["reason"],source_path=str(path),core_start_i=start,core_end_i=end,
                   core_start=str(clock.iloc[start]),core_end=str(clock.iloc[end]),
                   boundary_status="unconfirmed_proposal",owner_geometry_confirmed=False,
                   legacy12_at_core_end=bool(d.legacy_raw),legacy_mean_atr12=float(d.mean_atr12),
                   legacy_crosses12=int(d.cross_count12),legacy_group_edges12=int(d.group_edges12),
                   **geometry(f.iloc[start:end+1]))
        image_path = Path(b["review_image_path"])
        assert digest(image_path) == b["review_image_sha256"]
        provenance[str(image_path)] = digest(image_path)
        rows.append(row)
    out = EXP / "run_v1" / "anchor_audit_v1"
    out.mkdir(exist_ok=False)
    pd.DataFrame(rows).to_csv(out / "anchors.csv",index=False)
    # Same descriptive body measure for all purposefully selected new cases.
    selected = pd.read_csv(EXP / "run_v1/delivery_v1/selected_cases.csv")
    body = []
    for _,r in selected.iterrows():
        w = pd.read_csv(EXP / "run_v1/streams" / r.symbol / "case_windows.csv.gz")
        core = w[(w.case_id==r.case_id)&w.relative_i.between(-12,-1)]
        body.append(dict(case_id=r.case_id,**geometry(core)))
    pd.DataFrame(body).to_csv(out / "case_body_geometry.csv",index=False)
    (out / "receipt.json").write_text(json.dumps(dict(source_commit=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),
            provenance=provenance,files={p.name:digest(p) for p in out.glob("*.csv")},
            no_accuracy_claim=True,no_threshold_selection=True),indent=2)+"\n")
    print(pd.DataFrame(rows).drop(columns=["reason","source_path"]).to_string(index=False))


if __name__ == "__main__": main()

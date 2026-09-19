"""Read-only lineage inventory for reusing Owner-reviewed MA morphology.

Source: Owner 2026-09-20 asks whether old L2 features and historical true
density regions can support feature engineering. This audit joins existing
event clocks to current OHLC files; it does not fit a model, infer labels,
rewrite boxes or estimate profitability. Historical file-hash parity is
unknown unless the old manifest supplied an expected source digest.
"""
from __future__ import annotations
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import pandas as pd

EXP = Path("experiments/active/exp-ma-morphology-reuse-20260920-v1")
MANIFEST = Path("datasets/manifests/dataset_v3_2_reviewed_core_v1_rows.jsonl")
STARS = Path("data/benchmark_exemplars.json")
GALLERY = Path("analysis/output/star_benchmark_originals/manifest.json")


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run():
    source = Path(__file__).relative_to(Path.cwd())
    for p in (source,EXP/"PROJECT_PLAN.md"):
        assert not subprocess.check_output(["git","status","--porcelain","--",str(p)],text=True).strip(), "Commit audit first"
    rows = [json.loads(x) for x in MANIFEST.read_text().splitlines()]
    joined, inputs = [], {}
    for path, group in pd.DataFrame(rows).groupby("source_path"):
        p = Path(path)
        if p.exists():
            f = pd.read_csv(p)
            clock = pd.to_datetime(f.ts,unit="ms",utc=True) if "ts" in f else pd.to_datetime(f.open_time,utc=True)
            inputs[path] = dict(sha256=sha(p),rows=len(f))
        else:
            f,clock=None,None
        for r in group.to_dict("records"):
            lo,hi = int(r["window_start_bar"]),int(r["window_end_bar"])
            exists = f is not None
            bounds = bool(exists and 0<=lo<=hi<len(f))
            stamps = bool(bounds and clock.iloc[lo] == pd.Timestamp(r["window_start_time"]) and clock.iloc[hi] == pd.Timestamp(r["decision_time"]))
            continuous = bool(bounds and clock.iloc[lo:hi+1].diff().dropna().eq(pd.Timedelta(minutes=15)).all())
            img = Path(r["image_path"])
            match = bool(img.exists() and sha(img)==r["image_sha256"])
            positive = r["class_status"] == "confirmed"
            core = bool(positive and pd.notna(r["box_start_bar"]) and pd.notna(r["box_end_bar"]) and lo<=int(r["box_start_bar"])<=int(r["box_end_bar"])<=hi)
            joined.append(dict(sample_id=r["sample_id"],symbol=r["symbol"],source_path=path,source_exists=exists,
                               class_status=r["class_status"],box_status=r["box_status"],review_via=r["review_r1_via"],
                               split=r["split"],timeframe=r["timeframe"],decision_time=r["decision_time"],
                               window_start_bar=lo,window_end_bar=hi,box_start_bar=r["box_start_bar"],box_end_bar=r["box_end_bar"],
                               index_in_range=bounds,clock_match=stamps,contiguous_window=continuous,
                               core_within_window=core,image_exists=img.exists(),image_sha_match=match,
                               post_core_visible_bars=hi-int(r["box_end_bar"]) if core else None,
                               source_sha_historical_parity="not_recorded",eligible_for_lineage_exploration=stamps and continuous))
    frame = pd.DataFrame(joined)
    registry=json.loads(STARS.read_text()); gallery=json.loads(GALLERY.read_text())
    star_rows=[dict(stem=x["stem"],path=str(GALLERY.parent/x["raw"]),exists=(GALLERY.parent/x["raw"]).exists(),boxes=len(x["boxes"])) for x in gallery["items"]]
    summary = dict(rows=len(frame),unique_samples=frame.sample_id.nunique(),unique_symbols=frame.symbol.nunique(),
                   class_counts=frame.class_status.value_counts().to_dict(),split_class=frame.groupby(["split","class_status"]).size().to_dict(),
                   box_status=frame.box_status.value_counts().to_dict(),source_files=len(inputs),
                   clock_matches=int(frame.clock_match.sum()),contiguous_windows=int(frame.contiguous_window.sum()),
                   exact_images=int(frame.image_sha_match.sum()),missing_images=int((~frame.image_exists).sum()),
                   duplicate_symbol_windows=int(frame.duplicated(["symbol","window_start_bar","window_end_bar"]).sum()),
                   earliest=frame.decision_time.min(),latest=frame.decision_time.max(),
                   post_core_visible=frame.post_core_visible_bars.dropna().value_counts().to_dict(),
                   stars_registered=registry["count"],star_boxes=sum(len(x["boxes"]) for x in registry["exemplars"].values()),
                   star_originals_present=sum(x["exists"] for x in star_rows),star_missing_declared=gallery["missing_originals"],
                   training_eligible=False,production_eligible=False)
    summary["split_class"]={"/".join(k):int(v) for k,v in summary["split_class"].items()}
    out=EXP/"inventory_v1";out.mkdir(exist_ok=False)
    frame.sort_values("sample_id").to_csv(out/"reviewed221_lineage.csv",index=False)
    pd.DataFrame(star_rows).to_csv(out/"star_original_availability.csv",index=False)
    (out/"summary.json").write_text(json.dumps(summary,indent=2)+"\n")
    (out/"identity.json").write_text(json.dumps(dict(source_commit=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),
               code_and_manifests={str(p):sha(p) for p in (source,MANIFEST,STARS,GALLERY,EXP/"PROJECT_PLAN.md")},inputs=inputs,
               files={p.name:sha(p) for p in out.iterdir() if p.is_file()}),indent=2)+"\n")
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__ == "__main__": run()

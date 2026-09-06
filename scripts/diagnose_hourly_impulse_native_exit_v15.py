"""Saved-only V15 distribution audit, descriptive and never a selection gate.

The statistical-analysis assumption utility cannot import because optional
seaborn is absent. Use installed SciPy1.13 Shapiro and NumPy2 IQR directly;
no dependency installation, trimming, transformation or method selection.
Canonical report bins provide the visual shape; do not build parallel plots.
Sources: scipy.stats.shapiro and numpy.percentile official documentation.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT=Path(__file__).resolve().parents[1]
EXPERIMENT=ROOT/"experiments/active/exp-btcusdtp-1h-native15-exit-preholdout-20260906-v15"


def main():
    results=EXPERIMENT/"results"
    summary=json.loads((results/"summary.json").read_text())
    path=results/"case_delta.csv"
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    if digest!=summary["output_hashes"]["case_delta.csv"]: raise ValueError("Delta pin mismatch")
    frame=pd.read_csv(path)
    if len(frame)!=251 or not frame.event_id.is_unique: raise ValueError("Original251 required")
    info={}
    for column in ("before","after","difference"):
        x=frame[column].to_numpy(dtype=float)*1e4; x=x[np.isfinite(x)]
        q=np.quantile(x,[0,.05,.25,.5,.75,.95,1]); iqr=q[4]-q[2]
        outliers=(x<q[2]-1.5*iqr)|(x>q[4]+1.5*iqr)
        w,p=stats.shapiro(x)
        info[column]={"n":len(x),"unknown":len(frame)-len(x),"quantiles_bp":q.tolist(),
            "shapiro_w":float(w),"shapiro_p":float(p),"iqr_outliers":int(outliers.sum()),"outliers_removed":0}
    output={"source_sha256":digest,"distributions":info,"raw_prices_read":False,
        "fallback":"statistical-analysis assumption_checks.py import failed: no seaborn; direct installed scipy.stats.shapiro and numpy.quantile",
        "inference_method_changed":False,"warning":"Shapiro is descriptive under temporal dependence; it cannot establish independent observations or drive posthoc method choice."}
    path=EXPERIMENT/"distribution_diagnostics.json"
    if path.exists(): raise ValueError("Preserve prior diagnostics")
    path.write_text(json.dumps(output,indent=2,allow_nan=False)+"\n")
    print(json.dumps(output,allow_nan=False))


if __name__=="__main__":main()

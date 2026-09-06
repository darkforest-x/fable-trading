"""Saved V16 return-shape diagnostics, without changing inference or filtering.

The optional skill assumption_checks import needs unavailable seaborn. Reuse
installed SciPy1.13 Shapiro/NumPy2 quantiles only. Zero IQR from many unchanged
pairs marks every nonzero change an IQR outlier, not a data error or exclusion.
https://docs.scipy.org/doc/scipy-1.13.1/reference/generated/scipy.stats.shapiro.html
"""
from pathlib import Path
import hashlib
import json

import numpy as np
import pandas as pd
from scipy import stats

ROOT=Path(__file__).resolve().parents[1]
EXPERIMENT=ROOT/"experiments/active/exp-btcusdtp-1h-dual-partial-preholdout-20260906-v16"


def main():
    source=EXPERIMENT/"results/case_delta.csv"
    summary=json.loads((EXPERIMENT/"results/summary.json").read_text())
    sha=hashlib.sha256(source.read_bytes()).hexdigest()
    if sha!=summary["output_hashes"]["case_delta.csv"]: raise ValueError("Source pin mismatch")
    frame=pd.read_csv(source)
    if len(frame)!=251 or not frame.event_id.is_unique: raise ValueError("All251 required")
    info={}
    for column in ("before","after","difference"):
        x=frame[column].to_numpy(dtype=float)*1e4; x=x[np.isfinite(x)]
        q=np.quantile(x,[0,.05,.25,.5,.75,.95,1]); iqr=q[4]-q[2]
        w,p=stats.shapiro(x)
        info[column]={"n":len(x),"unknown":len(frame)-len(x),"mean_bp":float(np.mean(x)),
            "sd_bp":float(np.std(x,ddof=1)),"quantiles_bp":q.tolist(),"iqr_bp":float(iqr),
            "shapiro_w":float(w),"shapiro_p":float(p),
            "iqr_outliers":int(((x<q[2]-1.5*iqr)|(x>q[4]+1.5*iqr)).sum()),"outliers_removed":0}
    result={"source_sha256":sha,"distributions":info,"raw_prices_read":False,
        "inference_method_changed":False,
        "fallback":"assumption_checks.py import failed: no seaborn; installed scipy.stats.shapiro/numpy.quantile",
        "warning":"Temporal dependence and repeated development exploration remain; Shapiro is descriptive, not a test-selection rule. Zero-IQR nonzero deltas are not invalid data."}
    out=EXPERIMENT/"distribution_diagnostics.json"
    if out.exists(): raise ValueError("Preserve prior diagnostics")
    out.write_text(json.dumps(result,indent=2,allow_nan=False)+"\n")
    print(json.dumps(result))


if __name__=="__main__":main()

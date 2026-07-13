"""30m deep grid (H8 follow-up): the strongest cell so far (+0.466%/trade)
gets a proper tp x horizon grid on the 30m swap pool, val-only.
Reuses run_config from mtf_first_pass with explicit label params.
"""
from __future__ import annotations
import json, sys
from pathlib import Path
PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "scripts"))
import mtf_first_pass as M

OUT = PROJECT_DIR / "analysis/output/mtf_30m_deep.json"
rows = []
for tp in (4.0, 5.0, 6.0):
    for h in (36, 48, 60, 72):
        M.label_candidate.__defaults__  # keep import used
        cfg = (f"30m_tp{int(tp)}_h{h}", "30m", 30, h)
        import functools
        orig = M.label_candidate
        M.label_candidate = functools.partial(orig, tp_mult=tp, sl_mult=2.0)
        try:
            r = M.run_config(*cfg)
        finally:
            M.label_candidate = orig
        if r:
            rows.append(r); print(r, flush=True)
OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=2))

"""Period accounting must not import later research outcomes."""
import numpy as np
import pandas as pd
from yoyo.evaluation.spike_exit_post import describe_grid

def test_validation_ruin_is_not_development_ruin():
    ix=pd.to_datetime(['2025-09-09T00:00Z','2025-09-11T00:00Z'],utc=True)
    nav=np.array([[11000.],[0.]])
    meta=[dict(risk_fraction=.1,notional_cap='uncapped_stress',ruined=True,allocated_trades=2)]
    rows={r['period']:r for r in describe_grid(ix,nav,meta,ix)}
    assert not rows['development']['ruined']
    assert rows['development']['full_run_ruined']
    assert rows['development']['period_entry_signals']==1
    assert rows['validation']['ruined']
    assert rows['validation']['opening_equity']==11000
    assert rows['validation']['net_return']==-1

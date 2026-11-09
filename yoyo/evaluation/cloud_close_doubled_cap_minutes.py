"""Refine only the ambiguous peak and trough hours of the capped long-winner account."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_doubled_cap as study
from yoyo.evaluation import cloud_close_double_minute_risk as minute

base = study.base
EXP = study.EXP.parent/'exp-cloud-close-doubled-cap-minutes-20261109-v1'


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed synchronized-minute runner risk')
    _,quotes,_ = study.source.inputs()
    label = 'cap_0.1_stress'
    equity,capture = minute.snapshots(quotes,EXP,study.EXP,label=label)
    if not (EXP/'post_trim_units.csv.gz').exists():
        tape = pd.read_csv(study.runner.EXP/'account/selected_paths.csv.gz',dtype={'symbol':str},float_precision='round_trip')
        data,_ = study.old.parent.assemble(tape,study.old.changed_limits(tape,quotes),'OKX',quotes)
        native,previous,side,parents = base.underlying(data,quotes)
        mask,_ = study.switched_hours(data,previous,parents)
        model,_ = base.study.leverage._syntheticize(dict(data,actions=data['actions'].copy()),1.5,.0005)
        pf = base.build_portfolio(data,model,native,previous,side,1.5,.0005,caps=np.where(mask,.1,.2))
        funds,curve,_ = base.receipts(pf,data,model,1.5)
        capture(pf,data,funds,curve,1.5,.0005)
    metrics = json.loads((study.EXP/'summary.json').read_text())['arms'][label]['metrics']['all']
    minute.refine(EXP,equity,quotes,metrics)


if __name__=='__main__':
    main()

"""Scale opposed trades without excluding their signals from the shared wallet."""
import pandas as pd

from yoyo.evaluation import cloud_close_verified_exposure as engine

EXP = engine.EXP.parent/'exp-cloud-close-daily-exposure-20261109-v1'


def conditional(parents):
    daily = engine.source.correction.parent.EXP/'opportunities.csv.gz'
    frozen = pd.read_csv(daily,index_col='event_id').reindex(parents.index)
    if frozen.prior_daily_direction.isna().any() or not frozen.side.eq(parents.side).all():
        raise ValueError('Missing original pre-signal daily regime for a corrected funded parent')
    aligned = parents.side.eq(frozen.prior_daily_direction)
    return dict(less_opposed=aligned.map({True:1.5,False:1.}),
                more_opposed=aligned.map({True:1.5,False:2.}))


if __name__=='__main__':
    engine.run(EXP,conditional)

"""Measure the headroom below the drawdown ceiling of the selected 1x/2x conditional account."""
import json

from yoyo.evaluation import cloud_close_cvc_regime as correction
from yoyo.evaluation import cloud_close_alt_season_minutes as prior

EXP = correction.EXP.parent/'exp-cloud-close-cvc-half-minutes-20261109-v1'


def main():
    if (EXP/'summary.json').is_file():
        raise FileExistsError('Completed 1x/2x stress margin below 50 percent')
    data,market,quotes = correction.account()
    weights = correction.study.weights(market[-1].set_index('event_id'))['contrarian_half']
    key = 'contrarian_half_stress'
    eq,capture = prior.minute.snapshots(quotes,EXP,correction.EXP,label=key,threshold=49.5)
    if not (EXP/'post_trim_units.csv.gz').exists():
        correction.engine.simulate(data,market,quotes,weights,.0005,EXP,key,snapshot=capture)
    old = json.loads((correction.EXP/'summary.json').read_text())['arms'][key]['metrics']['all']
    prior.minute.refine(EXP,eq,quotes,old,market_slip=.0005,threshold=49.5,
            scope='Native traded-price range on the documented corrected 360-name universe, including actual minute OHLC at critical peaks and troughs of the extra-5bp account. No funding or liquidity model.')


if __name__=='__main__':
    main()

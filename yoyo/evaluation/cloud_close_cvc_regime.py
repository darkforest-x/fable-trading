"""Transfer the two previously measured ETH/BTC sizing tilts to the complete repaired CVC history."""
import pandas as pd

from yoyo.evaluation import cloud_close_alt_season_sizing as study
from yoyo.evaluation import cloud_close_cvc_wallet as repair

engine = study.engine
EXP = repair.EXP.parent/'exp-cloud-close-cvc-regime-20261109-v1'


def account():
    quotes,_ = repair.checked_quotes()
    path = repair.EXP/'account/selected_paths.csv.gz'
    rows = pd.read_csv(path,dtype={'symbol':str},float_precision='round_trip')
    data,_ = repair.old.parent.assemble(rows,repair.old.changed_limits(rows,quotes),'OKX',quotes)
    return data,engine.base.underlying(data,quotes),quotes


if __name__=='__main__':
    engine.run(EXP,study.weights,account_loader=account,reference_dir=repair.EXP/'account')

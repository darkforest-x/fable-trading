"""Recheck the 1.25/1.75 exposure boundaries with corrected CVC prices and actual portfolio units."""
from yoyo.evaluation import cloud_close_alt_season_minutes as audit
from yoyo.evaluation import cloud_close_cvc_regime as correction

EXP = correction.EXP.parent/'exp-cloud-close-cvc-regime-minutes-20261109-v1'

if __name__=='__main__':
    audit.main(EXP,correction.EXP,correction.account,
               scope='CVC repaired native price path with conditional ETH/BTC size. Funding, account-tier history and capacity remain unmodeled.')

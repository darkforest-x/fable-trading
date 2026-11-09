"""Compare relative sizing with a fixed midpoint permitting an independently observed Binance control."""
from yoyo.evaluation import cloud_close_binance_relative_minutes as native

EXP = native.EXP.parent/'exp-cloud-close-binance-relative-control-20261109-v1'

if __name__=='__main__':
    native.transfer.main(directory=EXP,inspect_flag=native.inspect,midpoint=1.25,
                         policies=[('uniform',0.),('contrarian',.25),('momentum',-.25)])

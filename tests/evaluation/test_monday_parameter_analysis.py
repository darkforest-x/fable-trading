"""Do not call next-week confirmation entries matched to the old pin week."""
import unittest
import pandas as pd
from yoyo.evaluation.monday_parameter_analysis import add_actual_week,strict_pairs


class ControlWeekTest(unittest.TestCase):
    def test_actual_entry_week_not_pin_week(self):
        rows=[]
        for pair,signal_time in [('valid','2026-10-20T12:00Z'),('crossed','2026-10-26T12:00Z')]:
            for k in range(4):
                rows.append(dict(pair_id=pair,population='signal' if k==0 else 'random',
                    entry_time=signal_time if k==0 else '2026-10-21T12:00Z',
                    symbol='BTCUSDT',side=1,bucket=3,closed=True,week='2026-10-19',net_r=1.,net_bp=10.))
        f=add_actual_week(pd.DataFrame(rows));p=strict_pairs(f,'net')
        assert p.index.tolist()==['valid']
        f.loc[f.index==3,'bucket']=4
        assert strict_pairs(f,'net').empty


if __name__=='__main__':unittest.main()

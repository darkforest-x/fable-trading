"""Rebuild the same 12H cloud reclaim from Sunday or Tuesday NY local calendar ranges."""
import pandas as pd

from yoyo.evaluation import cloud_close_session_clock as session

EXP=session.EXP.parent/'exp-cloud-close-weekday-anchor-20261109-v1'


def calendar_check():
    for when,hours in [('2024-03-11T12:00Z',23),('2024-11-04T12:00Z',25)]:
        ms=pd.Timestamp(when).value//1_000_000
        window=session.branch.engine._ny_week_window(ms,6)
        if (window['monday_end']-window['start'])//session.H!=hours:
            raise ValueError('Sunday did not respect New York daylight saving transition')


if __name__=='__main__':
    calendar_check()
    session.main(directory=EXP,settings=(('sunday',0,6),('tuesday',0,1)))

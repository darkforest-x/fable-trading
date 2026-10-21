"""Window selection is by entry close and includes complete matched pairs."""
import pandas as pd
import pytest

from yoyo.evaluation.range_recent_window import select_window


def fixture():
    matches=pd.DataFrame({'pair_id':[1,2,3], 'entry_time':[
        '2026-08-20T23:45:00+08:00','2026-08-21T00:00:00+08:00','2026-10-21T00:00:00+08:00']})
    candidates=pd.concat([matches.assign(population=p) for p in ['signal','random']],ignore_index=True)
    return matches,candidates


def test_start_inclusive_end_exclusive_and_preserve_both_populations():
    m,c=select_window(*fixture(),'2026-08-21T00:00:00+08:00','2026-10-21T00:00:00+08:00')
    assert m.pair_id.tolist()==[2]
    assert len(c)==2 and set(c.population)=={'signal','random'}


def test_control_outside_window_requires_new_execution():
    m,c=fixture()
    c.loc[(c.pair_id==2)&(c.population=='random'),'entry_time']='2026-08-20T23:45:00+08:00'
    with pytest.raises(ValueError,match='Matched control outside'):
        select_window(m,c,'2026-08-21T00:00:00+08:00','2026-10-21T00:00:00+08:00')

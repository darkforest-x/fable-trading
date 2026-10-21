"""Entry-key restrictions must run before pairing, without moving the grid."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.monday_range_v2_31_reference import select_matches


KEYS = ['0.618:up', '0.618:down']


def fixture(side=-1):
    q = pd.DataFrame([[104., 104.8, 103.8, 104.5]] * 9,
                     columns=['open', 'high', 'low', 'close'],
                     index=pd.date_range('2026-10-20T21:30+08:00', periods=9, freq='15min'))
    a = np.zeros(9, dtype=int); a[5] = side
    p = np.tile([100., 110., 105., 106.18, 103.82], (9, 1))
    return q, a, p, ['0:up', '0:down', '0.5:up', *KEYS], np.array(['week'] * 9)


def test_first_disallowed_touch_does_not_consume_later_0618():
    q, a, p, n, w = fixture()
    q.loc[q.index[5], 'high'] = 105.5
    q.loc[q.index[6], 'high'] = 107.
    old = select_matches(q, a, p, n, w)[0]
    new = select_matches(q, a, p, n, w, allowed_keys=KEYS)[0]
    assert (old['touch_i'], old['key']) == (5, '0.5:up')
    assert (new['touch_i'], new['entry_i'], new['key']) == (6, 6, '0.618:up')
    for end in range(1, 10):
        got = select_matches(q.iloc[:end], a[:end], p[:end], n, w[:end], allowed_keys=KEYS)
        assert got == ([new] if end > new['entry_i'] else [])


def test_0618_can_replace_an_earlier_key_on_same_touch():
    q, a, p, n, w = fixture()
    q.loc[q.index[5], 'high'] = 107.
    assert select_matches(q, a, p, n, w)[0]['key'] == '0.5:up'
    row = select_matches(q, a, p, n, w, allowed_keys=KEYS)[0]
    assert (row['touch_i'], row['entry_i'], row['key']) == (5, 5, '0.618:up')


@pytest.mark.parametrize('side', [-1, 1])
def test_price_side_and_endpoint_snapshot_are_retained(side):
    q, a, p, n, w = fixture(side)
    if side == 1:
        q.loc[q.index[5], 'low'] = 103.
    else:
        q.loc[q.index[5], 'high'] = 107.
    before = p.copy()
    row = select_matches(q, a, p, n, w, allowed_keys=KEYS)[0]
    assert row['key'] == ('0.618:down' if side == 1 else '0.618:up')
    np.testing.assert_array_equal(p, before)


@pytest.mark.parametrize('key,price', [('0:up', 100.), ('0:down', 110.),
                                     ('0.5:up', 105.), ('0.705:up', 107.05)])
def test_other_keys_are_excluded_even_when_the_only_valid_touch(key, price):
    q, a, p, n, w = fixture()
    # Move the allowed levels away and supply one eligible non-0.618 key.
    p[:, 3:] = [200., 0.]
    p = np.column_stack([p, np.full(9, price)])
    n = [*n, key]
    q.loc[:, ['open', 'high', 'low', 'close']] = [price-1., price-.2, price-1.2, price-.5]
    q.loc[q.index[5], 'high'] = price+1.
    assert select_matches(q, a, p, n, w, allowed_keys=KEYS) == []


def test_empty_whitelist_rejects_all_and_unknown_key_fails():
    data = fixture()
    assert select_matches(*data, allowed_keys=[]) == []
    with pytest.raises(ValueError, match='Unknown entry key'):
        select_matches(*data, allowed_keys=['missing'])

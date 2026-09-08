"""Known-coordinate controls for annotation diagnostics, without market data."""
from dataclasses import asdict

from yoyo.datasets.review_annotation_audit import geometry, clipping
from yoyo.layers.l1_detection.render import ChartTransform


def transform():
    return asdict(ChartTransform(n_bars=18, width=1280, height=742, left=12,
        top=12, plot_w=1256, plot_h=718, price_min=0, price_max=100, candle_half_w=23))


def box():
    return {'x':27.8125, 'y':30., 'width':21.25, 'height':40., 'label':'空头'}


def test_one_bar_translation_uses_n_minus_one_and_preserves_size():
    p = box(); t = transform()
    a = {**p, 'x':p['x'] + 1256/17/1280*100}
    g = geometry(a, p, t)
    assert abs(g['dx_bars'] - 1) < 1e-12
    assert g['proposal_bar_centers'] == [6,7,8,9]
    assert g['owner_bar_centers'] == [7,8,9,10]
    assert g['dw_px'] == g['dh_px'] == 0


def test_clipping_distinguishes_wick_from_body_and_ignores_outside_candles():
    candles = [dict(open=48, close=52, high=55, low=45) for _ in range(18)]
    assert clipping(box(), transform(), candles) == {'body_outside':[], 'wick_outside':[]}
    candles[5] = dict(open=48,close=52,high=95,low=45)
    candles[6] = dict(open=48,close=95,high=95,low=45)
    candles[15] = dict(open=0,close=100,high=100,low=0)
    assert clipping(box(), transform(), candles) == {'body_outside':[7], 'wick_outside':[6,7]}

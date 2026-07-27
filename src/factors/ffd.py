"""Fractional differentiation: stationarity without throwing away memory.

Why this belongs here. Integer differencing (returns) makes a price series
stationary but erases its level information -- the model can no longer see "we
are near the top of a 6-month range", only "we moved 0.3% last bar". Lopez de
Prado's fractional differencing takes the smallest exponent d that passes an ADF
test, so the series becomes usable by a model while retaining as much of the
original memory as statistics allow.

It matters for this project specifically. Every feature the judgment layer has
ever seen is either a ratio or a short-window return, i.e. already fully
memory-erased. If there is level-dependent structure in these setups, no feature
built so far could express it.

Implementation notes that are easy to get wrong:

* The weights are computed by the recursion w_k = w_{k-1} * (k-1-d)/k with
  w_0 = 1, and truncated once |w_k| falls below a threshold. Using a fixed
  window instead makes the transform non-stationary across the series.
* Every output value at time t uses only x[t-len(w)+1 : t+1]. The convolution is
  strictly backward-looking, so the result is safe under iron rule 3 -- but only
  because the weight vector is fixed in advance, never fitted on the series.
* The first len(w)-1 values are NaN rather than partially-weighted, since a
  truncated weight vector applied to a short prefix silently changes d.

Usage:
    from src.factors.ffd import frac_diff, find_min_d
    d_star, report = find_min_d(close)          # smallest d passing ADF
    x = frac_diff(close, d_star)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

DEFAULT_TAU = 1e-4          # weight truncation threshold
DEFAULT_D_GRID = np.round(np.arange(0.05, 1.001, 0.05), 3)
ADF_ALPHA = 0.05


def ffd_weights(d: float, tau: float = DEFAULT_TAU, max_len: int = 10_000) -> np.ndarray:
    """Backward-looking FFD weights, truncated where |w_k| < tau.

    Returned newest-first: w[0] multiplies x[t], w[1] multiplies x[t-1], ...
    """
    w = [1.0]
    for k in range(1, max_len):
        nxt = -w[-1] * (d - k + 1) / k
        if abs(nxt) < tau:
            break
        w.append(nxt)
    return np.asarray(w, dtype=float)


def frac_diff(series: pd.Series | np.ndarray, d: float,
              tau: float = DEFAULT_TAU) -> pd.Series:
    """Fixed-width fractionally differenced series. Leading values are NaN.

    Causal by construction: output[t] depends only on input[t-len(w)+1 .. t].
    """
    s = pd.Series(series).astype(float)
    w = ffd_weights(d, tau)
    n = len(w)
    if len(s) < n:
        return pd.Series(np.full(len(s), np.nan), index=s.index)
    vals = s.to_numpy()
    # np.convolve with the reversed kernel gives, at position i, sum_k w[k]*x[i-k]
    out = np.convolve(vals, w[::-1], mode="valid")
    res = np.full(len(s), np.nan)
    res[n - 1:] = out
    return pd.Series(res, index=s.index)


def adf_pvalue(x: np.ndarray, maxlag: int = 1) -> tuple[float, float]:
    """Augmented Dickey-Fuller t-statistic and an approximate p-value.

    Written here rather than imported: statsmodels is not a project dependency
    and CLAUDE.md forbids adding heavy ones for a single test. The regression is
    the standard one with a constant,

        dx_t = a + rho * x_{t-1} + sum_i b_i dx_{t-i} + e_t

    and the statistic is rho_hat / se(rho_hat). Returns (tstat, p_approx).

    The p-value is interpolated over the asymptotic MacKinnon critical values
    for the constant-only case (-3.43 / -2.86 / -2.57 at 1/5/10%), NOT the full
    response surface, so treat it as a screen: it is accurate enough to answer
    "does this pass at 5%", not to quote as a precise p.
    """
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 50:
        return np.nan, np.nan
    dx = np.diff(x)
    lag = max(0, int(maxlag))
    rows = n - 1 - lag
    if rows < 20:
        return np.nan, np.nan
    y = dx[lag:]
    cols = [x[lag:-1], np.ones(rows)]
    for i in range(1, lag + 1):
        cols.append(dx[lag - i:-i] if i else dx[lag:])
    # Contiguous float64 copies: non-contiguous stride views out of the diff
    # slices made BLAS emit divide-by-zero/overflow warnings on this host even
    # though the result was correct, which would mask a genuine numerical fault.
    X = np.ascontiguousarray(np.column_stack(cols), dtype=np.float64)
    y = np.ascontiguousarray(y, dtype=np.float64)
    if not (np.isfinite(X).all() and np.isfinite(y).all()):
        return np.nan, np.nan
    try:
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        if not np.isfinite(beta).all():
            return np.nan, np.nan
        resid = y - X @ beta
        if not np.isfinite(resid).all():
            return np.nan, np.nan
        dof = max(rows - X.shape[1], 1)
        s2 = float(resid @ resid) / dof
        if not np.isfinite(s2) or s2 <= 0:
            return np.nan, np.nan
        xtx_inv = np.linalg.pinv(X.T @ X)
        se = float(np.sqrt(s2 * xtx_inv[0, 0]))
    except np.linalg.LinAlgError:
        return np.nan, np.nan
    if se <= 0:
        return np.nan, np.nan
    t = float(beta[0] / se)
    crit = np.array([-3.43, -2.86, -2.57])          # 1%, 5%, 10%
    lvl = np.array([0.01, 0.05, 0.10])
    if t <= crit[0]:
        p = 0.005
    elif t >= crit[-1]:
        p = min(0.99, 0.10 + (t - crit[-1]) * 0.15)
    else:
        p = float(np.interp(t, crit, lvl))
    return t, p


@dataclass
class FFDReport:
    d: float | None
    adf_pvalue: float | None
    corr_with_original: float | None
    n_weights: int
    passed: bool
    tried: list[dict]


def find_min_d(series: pd.Series | np.ndarray,
               grid: np.ndarray = DEFAULT_D_GRID,
               tau: float = DEFAULT_TAU,
               alpha: float = ADF_ALPHA,
               log_price: bool = True) -> tuple[float | None, FFDReport]:
    """Smallest d on the grid whose FFD series passes ADF at `alpha`.

    Smallest, not best-scoring: the point is to spend the least memory that buys
    stationarity. Correlation with the original is reported so the cost is
    visible, but it is not optimised -- maximising it would just pick d=0.

    log_price=True differences log prices, which is the usual choice for price
    series and makes the result scale-free across symbols.
    """
    s = pd.Series(series).astype(float).dropna()
    if log_price:
        if (s <= 0).any():
            log_price = False
        else:
            s = np.log(s)
    tried: list[dict] = []
    for d in grid:
        x = frac_diff(s, float(d), tau).dropna()
        if len(x) < 100:
            tried.append({"d": float(d), "skipped": "too short"})
            continue
        _t, p = adf_pvalue(x.to_numpy(), maxlag=1)
        if not np.isfinite(p):
            tried.append({"d": float(d), "error": "adf failed"})
            continue
        corr = float(np.corrcoef(x.to_numpy(), s.loc[x.index].to_numpy())[0, 1])
        tried.append({"d": float(d), "adf_p": round(p, 5), "corr": round(corr, 4),
                      "n_w": len(ffd_weights(float(d), tau))})
        if p < alpha:
            return float(d), FFDReport(float(d), p, corr,
                                       len(ffd_weights(float(d), tau)), True, tried)
    return None, FFDReport(None, None, None, 0, False, tried)


def add_ffd_features(frame: pd.DataFrame, d: float,
                     cols: tuple[str, ...] = ("close", "volume"),
                     tau: float = DEFAULT_TAU) -> pd.DataFrame:
    """Append `<col>_ffd` columns. Causal; NaN for the warmup prefix."""
    out = frame.copy()
    for c in cols:
        if c not in out.columns:
            continue
        s = out[c].astype(float)
        if c != "volume" and (s > 0).all():
            s = np.log(s)
        out[f"{c}_ffd"] = frac_diff(s, d, tau).to_numpy()
    return out

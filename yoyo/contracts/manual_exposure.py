"""Owner's discretionary allowance, 2026-10-23; independent of stop distance.

BTC and ETH each own a 10x equity pool; other USDT swaps share a 5x pool.
Gross open positions and opening reservations consume the pool, never netted.
Exchange leverage is collateral only. No 1R risk sizing is applied here.
"""
from decimal import Decimal


def pool(symbol):
    return symbol if symbol in ('BTC-USDT-SWAP', 'ETH-USDT-SWAP') else 'alts'


def allowance(symbol, equity, exposures):
    equity = Decimal(str(equity))
    if not equity.is_finite() or equity <= 0:
        raise ValueError('账户总权益不足或暂不可用')
    multiple = 5 if pool(symbol) == 'alts' else 10
    used = Decimal(0)
    for other, value in exposures:
        if pool(other) != pool(symbol):
            continue
        value = Decimal(str(value))
        if not value.is_finite() or value < 0:
            raise ValueError('已有持仓或挂单金额暂不可用')
        used += value
    cap = equity * multiple
    return dict(equity=str(equity), exposure_pool=pool(symbol), exposure_multiple=multiple,
                exposure_cap=str(cap), exposure_used=str(used),
                exposure_remaining=str(max(Decimal(0), cap-used)))


def target(budget, allocation):
    share = Decimal(str(allocation))
    if not share.is_finite() or not 0 < share <= 100:
        raise ValueError('仓位额度须在 0 至 100% 之间')
    return Decimal(budget['exposure_remaining'])*share/100

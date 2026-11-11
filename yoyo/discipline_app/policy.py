"""Owner's 2026-10-18 paper policy and decimal arithmetic; never a live authorization.

R is the immutable cash risk at entry: initial stop distance plus modeled costs.
The cost allowance is a labeled paper assumption, not an actual OKX fee schedule.
"""
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_UP, ROUND_CEILING
from dataclasses import dataclass, asdict, replace
from typing import Optional


class Rejected(ValueError):
    """A user-visible risk rejection."""


def dec(value, label='数值'):
    try:
        if isinstance(value, bool):
            raise ValueError()
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError()
        return result
    except (InvalidOperation, ValueError):
        if value is None or (isinstance(value, str) and not value.strip()):
            raise Rejected(f'请填写{label}') from None
        raise Rejected(f'{label}格式不正确，请输入有效数字') from None


def positive(value, label='数值'):
    value = dec(value, label)
    if value <= 0:
        raise Rejected(f'{label}必须大于零')
    return value


def floor_step(value, step):
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def ceil_step(value, step):
    return (value / step).to_integral_value(rounding=ROUND_UP) * step


def json_number(value):
    """Return a JSON-native number without discarding fractional leverage."""
    value = dec(value)
    return int(value) if value % 1 == 0 else float(value)


@dataclass(frozen=True)
class Policy:
    # New policies allocate a share of the remaining gross-notional budget.
    # Saved policies predating this field are parsed as legacy margin policies.
    entry_sizing: str = 'margin'
    risk_fraction: str = '0.05'
    margin_mode: str = 'cross'
    major_exposure_multiple: Optional[str] = '10'
    alt_exposure_multiple: Optional[str] = '5'
    major_margin_fraction: Optional[str] = '0.10'
    alt_margin_fraction: Optional[str] = '0.05'
    major_allowance_scope: str = 'independent'
    major_leverage: int = 100
    alt_leverage: int = 20
    max_positions: int = 3
    max_entries: int = 3
    profit_entry_bonus: int = 1
    loss_streak: int = 3
    cooldown_after_losses: int = 3
    daily_loss_fraction: str = '0.17'
    loss_cooldown_seconds: int = 86400
    breakeven_r: str = '1'
    take_profits: tuple = (('3', '0.6'), ('5', '0.4'))
    fee_rate: str = '0.0005'
    slippage_rate: str = '0.001'
    quote_max_age_seconds: int = 15
    # A paper reservation expires rather than silently becoming a stale entry.
    pending_ttl_seconds: int = 900
    policy_change_delay_seconds: int = 86400

    def validate(self):
        if self.entry_sizing not in ('margin', 'exposure'):
            raise Rejected('开仓额度计算方式无效')
        if self.major_allowance_scope not in ('shared', 'independent'):
            raise Rejected('主流币额度范围无效')
        if type(self.profit_entry_bonus) is not int or self.profit_entry_bonus not in (0, 1):
            raise Rejected('盈利平仓奖励次数只能为 0 或 1')
        if self.margin_mode not in ('cross', 'isolated'):
            raise Rejected('保证金模式无效')
        if self.entry_sizing == 'exposure':
            if self.margin_mode != 'cross':
                raise Rejected('名义额度规则仅支持全仓')
            if dec(self.major_leverage) != 100:
                raise Rejected('BTC 和 ETH 的规则杠杆固定为 100x')
            if (self.major_exposure_multiple is None or self.alt_exposure_multiple is None
                    or dec(self.major_exposure_multiple) != 10 or dec(self.alt_exposure_multiple) != 5
                    or self.major_allowance_scope != 'independent'):
                raise Rejected('名义额度规则固定为 BTC/ETH 各 10x、山寨共享 5x')
            if self.major_margin_fraction is not None or self.alt_margin_fraction is not None:
                raise Rejected('名义额度规则不使用保证金比例上限')
        if (self.major_exposure_multiple is None) != (self.alt_exposure_multiple is None):
            raise Rejected('两类账户名义仓位上限必须同时配置')
        for name in ('major_exposure_multiple', 'alt_exposure_multiple'):
            value = getattr(self, name)
            if value is None:
                if self.margin_mode == 'cross':
                    raise Rejected('全仓必须配置账户名义仓位上限')
            else:
                positive(value)
        for name in ('major_margin_fraction', 'alt_margin_fraction'):
            value = getattr(self, name)
            if value is not None and not 0 < dec(value) < 1:
                raise Rejected('保证金比例必须在 0 和 1 之间')
        for name in ('risk_fraction', 'daily_loss_fraction'):
            if not 0 < dec(getattr(self, name)) < 1:
                raise Rejected(f'{name} 必须在 0 和 1 之间')
        for name in ('major_leverage', 'alt_leverage', 'max_positions', 'max_entries',
                     'loss_streak', 'cooldown_after_losses', 'loss_cooldown_seconds', 'quote_max_age_seconds',
                     'pending_ttl_seconds', 'policy_change_delay_seconds'):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise Rejected(f'{name} 必须为正整数')
        if dec(self.fee_rate) < 0 or dec(self.slippage_rate) < 0:
            raise Rejected('成本预留不能为负')
        if dec(self.fee_rate) + dec(self.slippage_rate) >= 1:
            raise Rejected('成本预留无效')
        positive(self.breakeven_r)
        previous = Decimal(0)
        total = Decimal(0)
        for target, fraction in self.take_profits:
            target, fraction = positive(target), positive(fraction)
            if target <= previous or fraction > 1:
                raise Rejected('止盈档位必须递增且比例有效')
            previous, total = target, total + fraction
        if total != 1:
            raise Rejected('止盈比例必须合计 100%')
        return self

    def document(self):
        return asdict(self)

    def with_exposure_sizing(self):
        """Return the new gross-notional policy while preserving other gates.

        ``alt_leverage`` remains a legacy-only integer for older clients that
        decode it as required. Exposure sizing never reads that field.
        """
        return replace(self, entry_sizing='exposure', margin_mode='cross',
                       major_exposure_multiple='10', alt_exposure_multiple='5',
                       major_margin_fraction=None, alt_margin_fraction=None,
                       major_allowance_scope='independent', major_leverage=100).validate()

    @classmethod
    def parse(cls, data):
        try:
            # Missing fields identify the old isolated policy, not permission
            # to silently activate new cross-margin defaults in a saved ledger.
            parsed = dict(data)
            # Missing field means a historical ledger and must retain its old
            # margin sizing semantics rather than silently activating a new rule.
            parsed.setdefault('entry_sizing', 'margin')
            parsed.setdefault('profit_entry_bonus', 0)
            parsed.setdefault('cooldown_after_losses', 1)
            parsed.setdefault('major_margin_fraction', None)
            parsed.setdefault('alt_margin_fraction', None)
            parsed.setdefault('major_allowance_scope', 'shared')
            if 'margin_mode' not in parsed:
                parsed.update(margin_mode='isolated', major_exposure_multiple=None,
                              alt_exposure_multiple=None)
            return cls(**parsed).validate()
        except TypeError:
            raise Rejected('规则字段不匹配') from None

    def leverage_cap(self, symbol):
        if self.entry_sizing == 'exposure' and symbol not in ('BTC-USDT-SWAP', 'ETH-USDT-SWAP'):
            return None  # Alt leverage comes from OKX instrument and position tiers.
        return self.major_leverage if symbol in ('BTC-USDT-SWAP', 'ETH-USDT-SWAP') else self.alt_leverage

    def target_leverage(self, symbol, instrument, inherited=None):
        """Resolve the policy leverage before any OKX tier adjustment.

        Existing shared leverage always wins. New exposure-mode major entries
        require 100x support; alts start at the instrument maximum and LiveBook
        narrows that against the size-specific OKX position tier.
        """
        if inherited is not None:
            return positive(inherited, '杠杆')
        if self.entry_sizing == 'exposure':
            if symbol in ('BTC-USDT-SWAP', 'ETH-USDT-SWAP'):
                if dec(instrument['max_leverage']) < 100:
                    raise Rejected('BTC 和 ETH 不支持固定 100x 杠杆，暂停开仓')
                return Decimal(100)
            return positive(instrument['max_leverage'], '交易所最高杠杆')
        return positive(self.major_leverage if symbol in ('BTC-USDT-SWAP', 'ETH-USDT-SWAP')
                        else self.alt_leverage, '规则杠杆')

    def margin_cap(self, symbol):
        value = self.major_margin_fraction if symbol in ('BTC-USDT-SWAP', 'ETH-USDT-SWAP') else self.alt_margin_fraction
        return dec(value) if value is not None else None

    def exposure_cap(self, symbol):
        value = self.major_exposure_multiple if symbol in ('BTC-USDT-SWAP', 'ETH-USDT-SWAP') else self.alt_exposure_multiple
        return positive(value) if value is not None else None

    def allowance_pool(self, symbol, *, exposure=False):
        """Persist pool identity separately from the size of its allowance."""
        major = symbol in ('BTC-USDT-SWAP', 'ETH-USDT-SWAP')
        if self.major_allowance_scope == 'independent':
            return symbol if major else 'alts'
        return 'portfolio' if exposure else ('majors' if major else 'alts')


def plan(policy, equity, entry, stop, side, fraction, leverage, instrument, quantity_limit=None,
         sizing_mode='risk', inherited_leverage=None, requested_quantity=None):
    """Size from cash loss, independently of the chosen margin leverage.

    Uses only the current server quote, explicit initial stop and instrument
    metadata. No future observations or client-supplied account equity are used.
    """
    entry, stop, fraction = positive(entry, '委托价格'), positive(stop, '初始止损价格'), dec(fraction, '仓位比例')
    if side not in ('long', 'short') or fraction < 0 or fraction > 1:
        raise Rejected('方向或仓位比例无效')
    distance = (entry - stop) * (1 if side == 'long' else -1)
    if distance <= 0:
        raise Rejected('多单止损须低于入场价，空单止损须高于入场价')
    lever = positive(leverage, '杠杆')
    if sizing_mode == 'exposure':
        cap = dec(inherited_leverage) if inherited_leverage is not None else dec(instrument['max_leverage'])
        policy_cap = policy.leverage_cap(instrument['symbol'])
        if policy_cap is not None:
            cap = min(cap, dec(policy_cap))
        if lever > cap:
            raise Rejected('杠杆超过币种或交易所上限')
        if inherited_leverage is not None and lever != dec(inherited_leverage):
            raise Rejected('已有共享杠杆必须沿用')
    elif type(leverage) is not int or not 1 <= leverage <= min(
            inherited_leverage if inherited_leverage == leverage else policy.leverage_cap(instrument['symbol']),
            int(instrument['max_leverage'])):
        raise Rejected('杠杆超过币种或交易所上限')
    tick, step = positive(instrument['tick']), positive(instrument['step'])
    if stop % tick:
        raise Rejected('止损价格不符合最小价格单位')
    unit_cost = entry * (2 * dec(policy.fee_rate) + dec(policy.slippage_rate))
    if sizing_mode not in ('risk', 'margin', 'exposure'):
        raise Rejected('仓位计算方式无效')
    if policy.entry_sizing == 'exposure' and sizing_mode != 'exposure':
        raise Rejected('当前规则要求按剩余名义额度计算仓位')
    if policy.entry_sizing == 'margin' and sizing_mode == 'exposure':
        raise Rejected('旧规则不支持名义额度仓位，请按旧规则重新预览')
    if sizing_mode == 'margin' and (policy.margin_cap(instrument['symbol']) is None or fraction <= 0 or fraction > policy.margin_cap(instrument['symbol'])):
        raise Rejected('保证金比例超过当前币种上限')
    if sizing_mode == 'risk' and fraction <= 0:
        raise Rejected('仓位比例必须大于零')
    budget = positive(equity) * dec(policy.risk_fraction) * (fraction if sizing_mode == 'risk' else 1)
    risk_quantity = budget / (distance + unit_cost)
    if sizing_mode == 'margin':
        requested_quantity = positive(equity) * fraction * lever / entry
    elif sizing_mode == 'exposure':
        if requested_quantity is None:
            raise Rejected('名义额度计划缺少剩余额度')
        requested_quantity = dec(requested_quantity) * fraction / entry
    else:
        requested_quantity = risk_quantity
    quantity = floor_step(min(risk_quantity, requested_quantity,
                              quantity_limit if quantity_limit is not None else risk_quantity), step)
    if quantity < positive(instrument['minimum']):
        raise Rejected('风险允许数量低于最小委托量')
    notional = quantity * entry
    margin = notional / lever
    risk = quantity * (distance + unit_cost)
    # This isolated collateral check is necessary but not sufficient.
    # Live execution separately reconciles actual fees and liquidation distance.
    if policy.margin_mode == 'isolated' and margin <= risk:
        maximum = int((entry / (distance + unit_cost)).to_integral_value(rounding=ROUND_CEILING)) - 1
        if maximum >= 1:
            raise Rejected(f'当前执行逐仓规则，杠杆过高。按此止损距离与成本预留，杠杆需降至 {maximum}x 或以下；也可返回检查初始止损价格。降低仓位比例不能解除此限制。')
        raise Rejected('当前执行逐仓规则。此止损距离与成本预留已超过整笔保证金，即使 1x 也不满足要求，请检查初始止损价格。')
    result = {k: str(v) for k, v in dict(quantity=quantity, notional=notional,
            margin=margin, risk=risk, budget=budget, entry=entry, stop=stop,
            price_risk=quantity * distance, costs=quantity * unit_cost,
            notional_equity_multiple=notional / positive(equity)).items()}
    result.update(margin_mode=policy.margin_mode, sizing_mode=sizing_mode,
                  leverage=json_number(lever),
                  risk_limited=risk_quantity < requested_quantity,
                  exposure_limited=quantity_limit is not None and quantity_limit < risk_quantity)
    return result

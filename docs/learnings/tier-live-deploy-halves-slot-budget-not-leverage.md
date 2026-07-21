# 仓位乘数上实盘时：减半槽预算，而不是提杠杆

- **问题**：tiered sizing（1x/1.5x/2x）要上 VPS 真金，但
  `equity_times_leverage × max_concurrent=1` 下「1x」已经是满权益保证金；
  直接 `base × mult` 会让 1.5x/2x 吃 OKX 51008 并丢高分档信号。
- **死胡同**：把回测里的「相对资本 10% 的 1x」当成实盘「满槽预算的 1x」；
  或用提杠杆/充值当默认解（红线：杠杆与充值须 owner 亲手）。只记账不乘仓位
  （shadow-only）则实盘零收益、与批准口径不符。
- **有效路径**：owner 选口径①——`unit = 满槽预算 / max_mult`，再
  `notional = unit × size_mult`。于是 1x≈半仓、2x≈贴满权益；核验用
  `2x_margin = unit×2/leverage ≤ equity` 作为上线门。VPS 实测 equity≈92.46、
  lev=3 时 2x 保证金恰好≈权益。
- **通用规则**：动态乘数上线前先固定「1x 相对什么」；若 1x 已是交易所硬上限，
  必须先缩单位再乘系数。部署清单含：真乘路径（非 shadow）、sidecar 分界、
  老行缺列=1x、保证金不等式数字证明、回滚（KILL + 恢复公式）。
- **牵连**：`src/execution/executor.py`（`tier_headroom` / `unit_notional`）、
  `docs/learnings/tier-multiplier-needs-margin-headroom-in-base-notional.md`、
  VPS `data/executor_config.json`、OKX 51008、`tests/test_tiered_sizing.py`。

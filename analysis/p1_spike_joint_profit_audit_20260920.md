# 前端突破+spike：止盈在执行，但收盘4ATR追踪允许明显回吐

2026-09-20；只读运行诊断，不是新策略实验。唯一键：joint-profit-audit-20260920。

## 结论

Owner 原始观察：“昨天我看浮赢200多r”，随后确认是当前前端接入的“突破+spike”。
实际菜单使用 V11.2 多头框内联合信号及原模拟持仓规则，不是 TV 最新 V12.4。
当前没有证据证明止盈漏触发；账本确实已有28笔追踪退出。可复现的问题是大赢家回吐明显。
“昨天同时浮盈200多R”未能核实，保留为待确认观察，不能用当前重建值否认历史屏幕。

## 原始快照与统计

本机 API 快照北京时间2026-09-20 18:53，见 `output/qa/joint_profit_audit_20260920/ledger.json`。
同目录保留 `status.json`、只读checkpoint的 `candles.json.gz`（81个symbol×timeframe流）、
`reconstructed_curve.json` 与 `reconstruction_summary.json`。未读取或写入订单接口。

| 筛选 | 信号 | 已结束 | 运行中 | 已实现净R | 浮动净R |
|---|---:|---:|---:|---:|---:|
| 全部 | 83 | 63 | 20 | 87.3028 | 19.6369 |
| 启用前回算 | 61 | 50 | 11 | 96.3702 | 16.7867 |
| 启用后实时发现 | 22 | 13 | 9 | -9.0674 | 2.8502 |

全部净R合计106.9397。63笔退出分为28笔追踪、32笔初始止损、3笔反向信号退出。
这不是账户收益率：每笔R按自己的初始价格风险归一，未假设统一账户仓位；全部范围包含启动前历史回算。
当前扫描idle、0错误；当前活跃记录分别更新到本周期最后已完成K线，未见停更证据。

## 止盈的实际含义

`yoyo/monitor/spike_lines.py` 调用 `spike_v10_4_increment.attempt()`，后者调用
`spike_v1_v8_be05.replay_fixed_entry(..., enable_be=False)`。

1. 联合信号后下一根开盘模拟入场。初始止损沿用5根低点、0.2ATR缓冲及2ATR风险下限。
2. **收盘毛浮盈达到2R**才激活追踪，不是盘中摸到2R就保本，也不是2R固定止盈。
3. 保护价格为既有保护与“本根收盘价−4×本根ATR”的较大值，按tick向下取整，收盘更新，下一根生效。
4. 下一根开盘/最低价触碰已生效保护会退出；原始V9空头确认也在下一根开盘退出。
5. 没有按单笔最高R回撤比例或全页面总浮盈回撤止盈的逻辑。保护不会降低，但盘中冲高和ATR扩张可能让保护落后于高点。

| 单笔 | 页面最高毛R | 扣同成本的最高净R | 当前/退出净R | 同口径回吐净R |
|---|---:|---:|---:|---:|
| XTZ 15m，已追踪退出 | 66.5455 | 66.3178 | 39.3633 | 26.9545 |
| ENA 30m，已追踪退出 | 18.5396 | 18.4702 | 11.1201 | 7.3501 |
| ZIL 15m，仍运行 | 23.5763 | 23.4753 | 13.2041 | 10.2712 |

最高R是盘中毛收益；当前/退出R扣0.2%往返成本，不能直接把两列差额全部称为回吐。
XTZ入场0.2504、初始风险0.0022、退出0.3375；净R算式为
`(0.3375-0.2504-0.002*0.2504)/0.0022 = 39.3632727`。
ENA入场0.14458、初始风险0.00417、退出0.19124，净R11.1201055。

父代理本地复核保存于 `exit_reconciliation.json`：XTZ在北京时间9月19日22:15开盘的bar收盘后，
以close0.3766、ATR0.009768831将保护升到0.3375；9月20日10:45开盘bar最低0.3367，触发该保护。
ENA在9月20日09:30开盘bar收盘后，以close0.21304、ATR0.005449481升到0.19124；
11:00开盘bar最低0.19081，触发退出。两笔初始风险、退出价和净R与冻结API完全一致。
卡片记录的退出时间采用该bar收盘时点，不能把它当作精确盘中成交时刻。

附带显示歧义：`spike_lines.py:_position` 对已结束交易把 `stop_price` 回填为 `initial_stop`，
且 `trailing_active=false`；尽管退出说明仍显示正确“跟随保护”和退出价，卡片SL字段容易误导。
这没有改变模拟结算，本轮未改页面。现有测试 `PYTHONPATH=. python3 -m pytest tests/monitor/test_spike_lines.py -q`
由代理执行得到12通过、1失败；失败为环境缺少httpx导致API测试无法加载，未安装依赖或宣称全绿。

## 昨天到今天的条件重建

按当前83条事件的首次发现时间、固定入场/退出记录及保存的OHLC，
每15分钟读取当时最后已收盘价格；未发现的事件和首根入场bar未收盘的持仓不计入。
这是**条件于当前账本的重建**，没有重跑历史每次扫描，不是历史网页快照。

| 时间/口径 | 已实现净R | 浮动净R | 合计净R |
|---|---:|---:|---:|
| 9月19日22:45，昨日重建浮盈最高点 | 28.9247 | 116.8206 | 145.7453 |
| 9月20日18:45，与当前快照数值一致 | 87.3028 | 19.6369 | 106.9397 |
| 两者之差 | +58.3781 | -97.1837 | -38.8056 |

昨天峰值时那31笔持仓单独固定成一批：116.8206R → 当前已实现68.0970R＋仍浮动19.2835R，
同批合计87.3806R，回吐29.4401R。整体38.8056R下降还包含之后新记录等样本变化，不能全归给该31笔。
全程没有缺失用于估值的bar。不能从这个结果推断昨天页面不可能显示200多R；旧性能投影、扫描延迟及具体筛选没有历史快照。

## 核对与复现

冻结数据来自 `GET http://127.0.0.1:8766/api/lines/ledger?kind=joint&limit=2000&period=all&scope=all`
与 `/api/lines/status`；OHLC从本机 `ImpulseMonitor/monitor.sqlite3` 的 `candle_checkpoints` 以 `mode=ro` 读取。
以下命令从保存的原始快照重新计算昨日峰值，标准库即可运行，不调用线上API或修改运行库：

```bash
cd /Users/zhangzc/fable-trading
python3 - <<'PY'
import bisect, datetime, gzip, json, math
from pathlib import Path
p = Path('output/qa/joint_profit_audit_20260920')
d = json.loads((p / 'ledger.json').read_text())
cs = json.loads(gzip.decompress((p / 'candles.json.gz').read_bytes()))
steps = {'15m':900000, '30m':1800000, '1H':3600000, '4H':14400000}
tz = datetime.timezone(datetime.timedelta(hours=8))
start = int(datetime.datetime(2026,9,19,tzinfo=tz).timestamp()*1000)
curve = []
for t in range(start, d['as_of_ms']+1, 900000):
    floating, realized = [], []
    for row in d['items']:
        q, step = row['performance'], steps[row['timeframe']]
        if row['detected_at_ms'] > t or q['entry_time_ms']+step > t:
            continue
        if q['exit_time_ms'] and q['exit_time_ms'] <= t:
            realized.append(q['exit_r'])
            continue
        bars = cs[row['symbol']+'|'+row['timeframe']]
        i = bisect.bisect_right([b['t']+step for b in bars], t)-1
        assert i >= 0
        floating.append((bars[i]['c']-q['entry_price']-.002*q['entry_price'])/q['initial_risk'])
    curve.append((t, math.fsum(floating), math.fsum(realized)))
peak = max((v for v in curve if v[0] < start+86400000), key=lambda v:v[1])
print('yesterday peak:', datetime.datetime.fromtimestamp(peak[0]/1000,tz), peak[1:])
print('latest:', curve[-1])
assert math.isclose(curve[-1][1], d['stats']['floating_r'], abs_tol=1e-10)
assert math.isclose(curve[-1][2], d['stats']['realized_r'], abs_tol=1e-10)
# Deterministic counterexample: closing a position transfers R without loss.
before_realized, before_floating, transfer = 28., 100., 40.
assert before_realized+before_floating == (before_realized+transfer)+(before_floating-transfer)
PY
```

这是账本与机制诊断，没有新入场规则、模型排序或策略收益比较；val AUC、top-decile、置换p及匹配随机收益对照不适用。
对应可证伪对照是转入已实现而总值守恒的反例、当前重建与API两项精确对账，以及退出价格的逐根OHLC核验。

## 风险与诚实声明

- 没有昨天实际页面或性能状态变更历史，200多R未对上；不能把后验重建等同当时屏幕。
- 现有账本性能会重写；当前记录的入场风险和终点状态未必逐次等于昨日扫描当时版本。
- 模拟价格、闭合K线估值、固定成本不包括账户仓位、资金费和真实成交滑点。
- 所列最高R本身不是能保证成交的收益；此处没有检验更紧止盈能否提高总体收益。
- 只读代理请求使用budget-debugger角色，但代理自述模型标识为Astra、无法确认effort；不把本次当作轻量模型已核验的委派成功。

## 下一步

先补存每次统计快照及性能状态变化，才能精确归因下次页面浮盈变化；这是可单独实施的可观测性改进。
若研究更强利润保护，应固定同一批入场、只改退出机制并检验漏掉的大赢家；具体阈值/障碍变更需要Owner决策。
本次只记录诊断，未改止盈、成本、Pine、模拟账本或生产进程。

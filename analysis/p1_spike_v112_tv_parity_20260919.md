# SPIKE V11.2：看板止损与 TradingView 缺信号核查

核查日期：2026-09-19，北京时间。唯一键：`spike-v112-tv-parity-20260919`。

结论：GAS 已查到实际输入 K 线不一致，足以造成母 V9 和联合信号差异。“启用前”只是监控激活前的历史回算分类，不能解释全部缺信号，也不是 TV 一致性认证。TSLA、LIGHT 尚未定案；BTC 首次核查时 TV 选中 15m，卡片是 30m，尚不能直接比较。

## 1. 启用前与模拟止损分别是什么意思

监控激活时间为 **2026-09-19 01:14:07.720（北京）**，原值 `1789751647720`。API 把信号收盘时间早于或等于激活时间的事件标为 `history`，页面显示“启用前”。含义是监控启动后回算了此前已经收盘的历史信号；不是用户何时启用 TV 指标。

“实时”表示激活之后及时被后台发现（允许一根周期加 5 分钟的发现延迟）；不是当前仍然新鲜，更不是 Pine 已确认。GAS 的记录属于此类。“只看实时”按这一分类筛选。

卡片的止损来自 Python 引擎独立模拟：联合信号后下一根开盘入场、既定 V9 退出规则、扣 0.2% 往返成本。不是账户实际成交，也不是从 TV 导入的交易。后台能复算出事件，不等于 TV 会画出同一个信号。

## 2. 四个案例与控制检查

从用户截图指定四张卡片核查；属于针对故障的样本，不是随机抽样。

| 案例 | 卡片联合信号收盘（北京） | 原始后台复算 | 删除零量行后的复算 | TV 结论 |
|---|---|---|---|---|
| TSLA 1H | 09/17 21:00 | 事件存在，-1.2348R | 无零量行，事件不变 | 尚未定位缺标签原因 |
| GAS 15m | 09/19 07:15 | 事件存在，-1.2322R | 母 V9、联合事件均消失 | 已观察到关键缺 K，见下一节 |
| LIGHT 15m | 09/18 12:15 | 事件存在，-1.1745R | 无零量行，事件不变 | 尚未定位缺标签原因 |
| BTC 30m | 09/14 22:30 | 事件存在，-1.1580R | 无零量行，事件不变 | 核查时 TV 实际为 15m，不能按此图判定 30m 信号 |

原始后台样本：TSLA 925 根（UTC 08/11 22:00—09/19 10:00）；GAS、LIGHT 各 1478 根（09/04 02:15—09/19 11:30）；BTC 1131 根（08/26 22:00—09/19 11:00）。仅检查这四个目标事件，不推算全站错配率。

这是一致性故障诊断，不是收益优化实验：正类率、val 样本数、AUC、排序置换 p、top-decile 收益、策略胜率和随机入场收益均不适用。严格对照为：同一后台引擎、同一参数和上级数据，只改变本周期是否保留零量行，检验“输入变化不会改变目标信号”的零假设；另三个无零量案例作为不受影响对照。该检查不是统计显著性检验。

## 3. GAS 的原因链

1. TV 数据窗口在 **09/18 09:45 开盘**的 K 上显示 OHLC 为 `1.2385 / 1.2409 / 1.2385 / 1.2409`，与后台相同，但“V9 全部入场过滤通过”为 0。后台在这根 K 上的 V9 为真，对应卡片母信号 **10:00 收盘**。
2. TV 面板最后 SPIKE 为 09/07 20:30；上级突破为 09/19 07:00，状态是“等待 SPIKE”。这先把差异定位到母 V9，而不是上级突破时间。
3. TV 两根相邻可见 K 的时间是 **09/18 01:30 → 02:00**，缺少 01:45。后台 01:45 是成交量为 0、OHLC 均为 `1.2283` 的平价 K。01:30 和 02:00 的 OHLC 也已通过数据窗口核对。
4. Pine 在时间断档后重置 `bbSegmentBars`。默认 BB 准入要求连续 `200+500+12=712` 根，09:45 距该断档远未达到要求。后台保留中间 K，连续段不在这里重置。
5. 诊断副本删除 GAS 两根零量 K（UTC 09/12 21:45、09/17 17:45）后，同一目标母 V9 的 BB ready 从真变假，母 V9 和联合事件一起消失。第二根删除位置与 TV 直接观察一致；第一根是否也被 TV 省略未逐根导出核验。因此这是原因链的有力验证，不是完整 TV 数据集的逐行复制。

源码：`spike_burst_v11_2.pine:121-145`（断档与 BB 准入）；`okx.py:41-47`、`spike_lines.py:55-65`（后台保留零量 K）；`spike_lines_api.py:45-57`（显示分类）。均位于 `yoyo/` 下相应 monitor/evaluation 目录。

## 4. 复现与证据边界

已留存后台原始复算摘要 `experiments/active/exp-spike-v112-tv-parity-20260919-v1/backend-replay.json` 和零量反事实摘要 `zero-volume-diagnostic.json`。核查代码基线为 `d7c9acf5d15dd5b4e1cfffa6d627fb9ccfc15c9f`；没有新增或修改策略实现。

以下命令使用当前本机只读行情库重做 GAS 定位；行情缓存会滚动，未来可能不再覆盖该日期，因此不是冻结原始行情的长期逐字节复现包。原始 TV 观察来自本机原生应用的截图及数据窗口读取，没有导出完整 TV OHLCV。

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python - <<'PY'
from pathlib import Path
from yoyo.monitor.spike_lines_worker import Primary
from yoyo.monitor import spike_lines as s
p = Primary(Path.home() / 'Library/Application Support/Fable/ImpulseMonitor/monitor.sqlite3')
chart = s.frame_of(p.candles('GAS-USDT-SWAP', '15m'))
higher = s.frame_of(p.candles('GAS-USDT-SWAP', '1H'))
parent_ms, joint_ms = 1789695900000, 1789773300000
for label, bars in [('original', chart), ('omit_zero_volume_bars', chart[chart.volume > 0])]:
    facts = s.study.v9_facts(bars, 15, 'GAS', 0.0001)
    rows = [i for i, t in enumerate(bars.index) if t.value // 1000000 == parent_ms]
    if len(rows) != 1:
        raise RuntimeError('Target has left the rolling cache; original input must be restored.')
    result = s.analyze(bars, '15m', tick=0.0001, asset='GAS', higher=higher)
    print(label, 'parent_v9=', bool(facts['v9'][rows[0]]),
          'joint=', any(e['bar_close_ms'] == joint_ms for e in result['joints']))
PY
python3 scripts/md_to_html.py analysis/p1_spike_v112_tv_parity_20260919.md --out-dir analysis/html
```

## 5. 风险与诚实声明

- 不能把四张止损卡片全部归因于 GAS 的缺 K 原因。TSLA、LIGHT 尚未完成事件级 TV 状态对齐，BTC 需在正确周期继续核验。
- 没有证明“删除所有零量 K”就是正确修复。交易所无成交、供应商省略、数据缺失是不同语义；需要先确定数据契约。
- 还存在待验分歧：Python 截取最近 1500 根；Pine 与 Python 的断档后量能历史、ready 和 BB 连续段细节不同；TV 参数可修改，后台使用固定默认框内规则。它们是后续候选原因，不是其他案例的已证实原因。
- 没有改 Pine、后台数据处理、止损/成本参数、通知或实盘。GAS 的 -1.23R 只能作为当前后台定义下的模拟结果，不能宣称它就是 TV V11.2 的交易表现。

## 6. 下一步

先完成同合约、同周期、同 K 线集合、同参数的逐事件一致性，再用失败交易诊断策略逻辑。用参数搜索解决这类输入差异会优化错误对象。只读核查可以继续；采用统一缺 K 政策、改变策略门槛或上线修复，需要在独立方案中说明影响，不能把本次诊断副本直接用于生产。

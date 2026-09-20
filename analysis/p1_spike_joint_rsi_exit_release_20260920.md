# 突破＋SPIKE：同周期连续第7个RSI大菱形退出

日期：2026-09-20，Asia/Shanghai。范围：本机监控前端的模拟联合持仓。

已交付并在本机运行。退出计算／存储源码提交`fd298c88853674f731f28ab0b8a3f23b9ed4ca4e`，原生浏览器发现的切换瞬间残留旧值修复提交`99e2dc83c5460cd8b05d96e975712bdc1aa10c26`。策略收益尚未验证。

## Owner口径与实现

Owner确认“连续同色计数、异色重置”“同周期”，并否定另加亏损分支；此前已批准保留原价格保护、先触发先退出。当前联合只做多，因此退出事件为同周期第7个空头大菱形。指标序列不在入场时重置；小菱形、圆点、无事件bar不影响计数；第8个不补触发。若第7个在入场之前出现，本仓不会追溯成交。

复用仓内ChartPrime RSI14／Parabolic SAR实现，参数`.02/.02/.2`，强信号按SAR的30/70阈值定义。收盘确认后沿用原退出引擎在下一开盘全平，原初始止损、4ATR跟随保护与V9原始反向退出保留。止损已在当前bar触发时先止损；下一开盘跳过保护价时保留跳空止损原因；同根原V9反向与RSI同时确认时保留原V9原因。不改入场、风险倍数、0.2%往返成本。

新basis：`v11_2_box_joint_rsi7_same_tf_streak_next_open_v1_net_cost`。`positions`的旧基线调用默认仍关闭RSI，运行的`analyze`默认启用；每个同周期行情序列只计算一次RSI特征。闭合后的计数元数据取退出前最后存活收盘，不能读取出场bar未来的收盘。

沿用1500根尾窗。左截断、缺口及非有限收盘之后，首色的真实连续序号不可知，直至观察到异色大菱形才建立新序列第1个。界面显示“计数待完整序列”。没有扩大扫描窗或另加扫描任务。

## 账本与前端

首次切换在同一事务内归档全部既有联合performance，保留事件ID、信号时间、首次发现时间。新的行情投影写当前events；`performance_versions`保存切换前旧performance，重复启动不重写快照。跨basis刷新若没有对应政策初始化会拒绝，避免无记录覆盖。

前端默认“RSI第7个退出 · 当前回算”，可选择“切换前快照 · 原退出”。旧快照固定估值时间，并按快照时钟计算今天／本周；不作为新鲜信号或当前持仓展示，也不纳入切换后新事件。亏损RSI退出显示“亏损退出”，不误标为价格止损。界面显示空头连续计数、未知计数或已确认等待下一开盘状态。

切换前只读备份：`output/qa/joint_rsi_exit_20260920/cutover_backup_20260920T115054Z/`。SQLite备份SHA256：`ebea9439ed4526775b7d23807f1a28777c14e8b074dac12227ab9b91cb0d836d`。备份83条联合事件；此前19:45:46接口为68闭合、15活跃，已实现97.3057120893R、浮动6.2493403065R。

## 复现与验证

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m pytest -q tests/evaluation/test_spike_joint_rsi_exit.py tests/monitor/test_spike_lines_rsi.py tests/evaluation/test_parabolic_rsi_sar.py tests/evaluation/test_spike_v10_4_increment.py tests/monitor/test_spike_lines.py tests/monitor/test_spike_lines_versions.py tests/boundaries/test_layer_imports.py
node --test tests/monitor/frontend_lines.test.cjs tests/monitor/frontend_cards.test.cjs
```

已存两笔行情和事件链的复核（使用交付证据，不再访问网络或运行账本）：

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path
import pandas as pd
from yoyo.evaluation.spike_joint_rsi_exit import chartprime_strong_side
p = Path('output/qa/joint_rsi_exit_20260920/release_20260920T115620Z')
for record in json.loads((p / 'rsi_exit_event_chains.json').read_text()):
    frame = pd.read_csv(p / (record['symbol'] + '_' + record['timeframe'] + '_ohlcv.csv'), index_col=0, parse_dates=True)
    minutes = {'15m': 15, '30m': 30}[record['timeframe']]
    trigger = pd.Timestamp(record['exit_at_next_open']) - pd.Timedelta(minutes=minutes)
    features = chartprime_strong_side(frame, minutes=minutes)
    assert features.loc[:trigger].query('strong_side != 0').tail(8).strong_side.tolist() == [1, -1, -1, -1, -1, -1, -1, -1]
    assert int(features.loc[trigger, 'strong_streak']) == 7
    assert frame.loc[pd.Timestamp(record['exit_at_next_open']), 'open'] == record['exit_price']
print('2 event chains and next-open fills matched')
PY
```

这是功能与数据保全变更，不是收益实验。val AUC、置换p、top-decile收益、单特征基线、交易随机对照不适用，未编造数值。等效严格对照包括：RSI关闭／零触发时与冻结原引擎逐笔一致；打断同色序列后不出现旧第7；前缀／完整行情闭合元数据一致；冻结快照在当前收益更新、插入后续事件和重启后不变；异步旧版本响应不能覆盖新选择。

最终Python选择集124项通过（含层间守门），前端选择集20项通过。开发中存储测试曾把两条均为新版的fixture错期望为混合版本，修正预期并增加真实混合行后通过；浏览器验收发现切换清空状态但未即时绘制的短暂残留，已修复并补行为测试。没有把中途失败从记录中省去。

经既有`manage restart`更新监控，19:52:49开始首次新策略全量扫描，482合约、2410格、138.4秒、0错误，更新83条performance。`/api/health`确认`service_alive/market_ready/model_ready/ok=true`。所有83条当前basis为新版；旧快照的performance和首次发现时间逐条与SQLite备份一致；当前15笔活跃的计数全部已知。信号数、首次发现时间及事件ID没有变化。

验收接口与哈希收据：`output/qa/joint_rsi_exit_20260920/release_20260920T115620Z/`。当前规则与原快照的原生页面、XTZ卡片及切换加载状态均经过真实浏览器验收，截图和AX在上级QA目录。最终页面恢复当前规则／全部合约。

## 当前回算的实际触发

这次83条记录中，2笔因RSI提前全平；其他已闭合交易的退出原因与净R没有变化。以下是功能生效的描述性核对，不是样本外绩效结论。

| 项目 | 切换前旧快照 | 当前RSI规则回算 |
|---|---:|---:|
| 联合信号 | 83 | 83 |
| 闭合／运行中 | 68／15 | 68／15 |
| 已闭合净R合计 | 97.3057120893 | 87.5693484530 |
| 浮动净R（本次验收截点） | 6.4715625287 | 6.4715625287 |
| XTZ 15m净退出R | 39.3632727273（跟随保护） | 29.5905454545（RSI7） |
| LIGHT 30m净退出R | 0.2665818182（V9反向） | 0.3029454545（RSI7） |

已闭合合计减少9.7363636364R，主要是XTZ提前退出后未参与后续涨幅；这批记录不能报告为收益改善。对这2笔另从主扫描器只读checkpoint提取OHLC，逐个检查大菱形事件链：均为1个多头强菱形后连续7个空头强菱形，退出价逐笔等于下一根open。事件链与1500根原始OHLC保存在上述release目录；XTZ确认时点为09-19 11:30北京、价格0.316，LIGHT为09-20 11:00北京、价格0.1689。

旧快照时钟固定19:52:49，当前接口验收19:56:20；此次浮动值相同，后续会分离。不得把未来两个界面数值之差继续当作规则改善量。

## 风险与诚实声明

- 事件次数不是价格回撤上限，不能保证兑现最高浮盈；没有新收益改善、最优7或实盘准入结论。
- 有限窗口不能证明首色序号时明确未知；没有把缺失历史默认为1。当前没有跨任意长窗口的持久化指标计数状态。
- 下一开盘在既有模拟引擎的数据时钟内确认；末端尚无下一根行情时保持待退出，不捏造成交。
- 旧快照与滚动当前值的行情截止不同，不能据两者差值归因策略效果。切换也可能改变完整串行占仓，单笔独立截短并不等于完整新账本。
- 原有urllib3／LibreSSL环境提示保留。未改Pine源或TradingView私有脚本，本次交付对象是本机前端模拟账本。

## 后续

继续观察新规则触发及原保护优先的实际模拟记录。如需评估是否减少回吐，需要另做相同行情、同成本、同一截止时间的双版本串行回放；不能用此功能验收代替收益检验。

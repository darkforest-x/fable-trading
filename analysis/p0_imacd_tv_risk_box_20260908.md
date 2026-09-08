# TradingView IMACD 启动 K 线盈亏比框：工程验收

**结论：V2.4 已在 TradingView 编译成功，云端脚本与桌面图表均已保存。** 已确认的主图蓄势释放信号现在带有红绿盈亏比参考框，以及入场、止损、目标三档价格。原信号核心、六条均线、副图双线与零轴保持原样。本次没有收益评估，也没有修改或重启 TG、Bark、Mac 监控服务。

验收日期：2026-09-08（北京时间）。源码提交：`5c1c75ea3fb3968e7a8b70a82657ce015d493411`，10:30:45 入库，早于后续 TradingView 编译和 UI 验收。实验：`exp-imacd-tv-risk-box-20260908-v1`。本报告截至桌面 11:00:57 显示“所有更改已保存”的核对；没有追加刷新验收。

## 交付范围与默认设置

用户明确要求展示位置为 **TradingView**。信号收盘、信号极值止损、3R、右延 12 根等数值是本次已向用户明示的可调图形默认假设，**不是用户逐项明确确认的交易参数，也不是经过收益验证的最优配置**。

| 项目 | V2.3 | V2.4 / 当前保存设置 |
| --- | --- | --- |
| 盈亏比框 | 无 | 默认启用，仅跟随可见的蓄势释放 |
| 入场参考 | 无 | 默认信号 K 线收盘；可选下一根开盘 |
| 止损参考 | 无 | 多头用信号 K 线最低价，空头用最高价 |
| 目标参考 | 无 | 默认 3R；可调 0.25–20R |
| 水平延伸 | 无 | 默认向右 12 根，可调 1–100 根 |
| 历史保留 | 无 | 默认 8 组，上限 20 组 |
| 价格标签 | 无 | 默认显示入场、止损、目标；可关闭 |
| 原信号、六均线、副图、零轴 | 既有逻辑与样式 | 剥离新增显示块及对象预算变更后，旧可执行代码逐字一致 |
| TG / Bark / Mac 服务 | 既有配置 | 未改动、未重启、未重放通知 |

绘制条件是 `showRr`、`showFocus` 与已确认的 `focusRelease`。普通系统启动、退出和影线回踩均不生成盈亏框。多头收益区在入场上方、风险区在下方；空头镜像。框仅表达价格距离，向右延伸不代表预计持仓时间、实际成交或已实现收益。

“下一根开盘”模式等该 K 线实际出现后才使用其开盘价，并从该根开始绘制；信号高低点仍取上一根已确认释放 K 线。代码使用当前开盘与上一根已确认信号，不读取之后的高低收数据，不根据未来触价删除框。零风险、方向无效或非正目标价不绘制整组。

## 已完成的验证

| 验证项 | 结果 | 证据与边界 |
| --- | --- | --- |
| 原版保护 | PASS | V2.3 独立文件保留；新增块与对象额度以外的旧可执行代码一致，报告编写时再次运行断言通过 |
| 算术与无效输入 | 9 / 9 PASS | `monitor_signals` 实现验证的 9 组合成边界；属于算术检查，不是 9 笔历史交易 |
| 对象预算 | PASS | lines / boxes / labels 上限为 300 / 150 / 420；含原图形与瞬时创建的保守峰值 263 / 121 / 383 |
| TradingView 网页编译 | PASS | 完整源码保存并实际编译成功 |
| 网页编辑器源码回读 | PASS | Ctrl+A / Ctrl+C 取回完整内容，仅 CRLF 换行不同；换行归一化后与本地 V2.4 全字一致 |
| 多头与空头显示 | PASS | LIT 1H 多头、BTC 1H 空头真实截图；不是合成行情 |
| 桌面实例更新 | PASS | 原实例为云端 v8，重新加载后换入已保存的 v9；当前 ETHUSDT.P 4H 深色图可见对应框 |
| 输入保留 | PASS | 新增 08 组符合默认值；原信号、MA、显示、回踩等输入逐项仍与快照一致 |
| 最终保存状态 | PASS | 11:00:57 桌面无障碍状态显示“所有更改已保存”，控件 disabled；参数弹窗已关闭 |

云端沿用原脚本名称 **IMACD 零轴密集启动 · 多周期趋势 V1**。TradingView 历史版本从今天 09:01 的 v8 更新到今天 10:43 的 v9；这是云端保存版本号，与本地文件的 V2.3 / V2.4 版本名分别记录。

[结构化 UI 验收记录](../../experiments/active/exp-imacd-tv-risk-box-20260908-v1/results/ui-verification.json) 保存了本地与网页源码回读哈希、云端版本、桌面保存状态和三个图形案例。9 组合成边界依据 `monitor_signals` 当时执行的 Python 标准输入断言汇报；具体输入、预期和实际结果已转录至 [算术案例](../../experiments/active/exp-imacd-tv-risk-box-20260908-v1/results/arithmetic-cases.json)。没有独立落盘测试脚本，不将它描述为 Pine 取值、入场模式或实时回滚集成测试。

## 真实图形对照

| 样本 | 方向 | 入场参考 | 止损参考 | 3R 目标 | 检查 |
| --- | --- | --- | --- | --- | --- |
| BTC 1H | 空头 | 77124.9 | 77296 | 76611.6 | 风险 171.1；目标为 77124.9 − 3 × 171.1 |
| ETH 4H | 多头 | 1922.23 | 1914.33 | 1945.93 | 风险 7.90；目标为 1922.23 + 3 × 7.90 |
| LIT 1H | 多头 | 见截图 | 见截图 | 见截图 | 核对主图多头红绿框与价格标签，不另行转录未经提供的价格 |

ETH 参考信号开盘时间为 **2026-08-19 08:00 UTC**，收盘确认时间为 **12:00 UTC**（北京时间分别为 16:00、20:00）。显示点位 1922.23 属于该根已确认的蓄势释放，未换用更早的精确零轴离开事件。

![LIT 1H 多头实际图形](../../experiments/active/exp-imacd-tv-risk-box-20260908-v1/results/tv-long-lit-1h.png)

![BTC 1H 空头实际图形](../../experiments/active/exp-imacd-tv-risk-box-20260908-v1/results/tv-short-btc-1h.png)

![桌面 ETH 4H 已换入 V2.4 的深色图形](../../experiments/active/exp-imacd-tv-risk-box-20260908-v1/results/tv-desktop-eth-4h.png)

[TradingView 输入快照](../../experiments/active/exp-imacd-tv-risk-box-20260908-v1/results/tv-inputs.txt)

## 复现步骤

以下命令在仓库根目录运行，不安装依赖，不修改交易服务。

```bash
cd /Users/zhangzc/fable-trading
git show --no-patch --format=fuller 5c1c75e
shasum -a 256 yoyo/evaluation/pine/imacd_dense_mtf_v2_3.pine yoyo/evaluation/pine/imacd_dense_mtf_v2_4.pine
python3 - <<'PY'
from pathlib import Path
import re
base = Path('yoyo/evaluation/pine/imacd_dense_mtf_v2_3.pine').read_text()
new = Path('yoyo/evaluation/pine/imacd_dense_mtf_v2_4.pine').read_text()
for name in ('INPUTS', 'RISK/REWARD'):
    pattern = rf'// BEGIN V2\.4 DISPLAY {name}\n.*?// END V2\.4 DISPLAY {name}\n'
    new = re.sub(pattern, '', new, flags=re.S)
new = new.replace('max_lines_count=300, max_boxes_count=150, max_labels_count=420',
                  'max_lines_count=250, max_boxes_count=85, max_labels_count=350')
def executable(text):
    return '\n'.join(line for line in text.splitlines()
                     if line.strip() and not line.lstrip().startswith('//'))
assert executable(base) == executable(new)
print('old_executable_parity: PASS')
PY
```

V2.3 SHA-256：`db58b5a037b58cde306b3c78654082c782cbab1427751592921b58b3a73268b4`。

V2.4 SHA-256：`087f569040e8c95adae6b2b852aa4cb6dc128fc2fda8a991d75a8676bef141ec`。

TradingView 人工复现：

1. 打开已保存的同名脚本，在版本历史核对 v9；确认编辑器完整内容与本地 V2.4 一致后编译并更新到图表。
2. 在编辑器聚焦代码，Ctrl+A / Ctrl+C 复制完整源码；将复制结果与本地源码仅归一化 CRLF 后比较，不跳过任何代码行。本轮该比较已由主任务完成。
3. 打开输入，按快照核对原设置，再核对 08 组：启用、信号收盘、3R、12 根、8 组、价格标签开启。
4. 查看 LIT 1H 多头与 BTC 1H 空头，再查看 ETH 4H 指定释放；核对三价、红绿方向和原副图。缩放至标签能正常分辨的范围。
5. 检查云端保存版本和桌面“所有更改已保存”状态；本轮最终核对后未再次刷新图表。

报告转换命令：

```bash
python3 scripts/md_to_html.py analysis/p0_imacd_tv_risk_box_20260908.md --out-dir analysis/html
```

## 非经济验收与时间范围

本次是显示功能工程验收，没有构建交易候选池、标签集、训练集或验证集。候选数、正类率、val 样本数、AUC、置换检验 p、top-decile 毛净收益、胜率、单特征基线和匹配随机入场收益均 **不适用**；不得从图上的 3R 参考目标推导盈利能力。

替代对照检验是：以“原有可执行代码完全不变”为零差异基准；以普通启动、退出、回踩和无效价格关系为不应画框的负例；以独立手算的多空三价检验图形公式。9 组合成边界与真实编译、源代码回读提供工程证据，不给不存在的收益指标或统计 p 值。

数据使用沿用用户“任何时间段数据都可以使用不要有任何限制”的明确授权。**这是该配置第 1 次 live-observation 用途使用，其中包含 ≥2026-05-04 的数据。** 用途仅为真实 UI 与点位显示核对，没有 holdout 经济评分、训练、调参、特征选择或配置优选。记录了 LIT 1H、BTC 1H、ETH 4H 三个真实显示样本；完整截图覆盖的所有时间范围未逐根清点，不把这三图描述为全币种或全周期验证。

## 风险与诚实声明

- 宽时间范围下三价标签可能挤在一起，放大后可读；没有完成所有缩放比例、币种和周期的穷举显示测试。
- 当风险距离只有一个最小跳动且 R 很小时，价格标签按 `format.mintick` 舍入，标签可能看起来重合；图形内部仍按浮点公式计算。这不是交易所可成交价格或可执行订单校验。
- “下一根开盘”的实时行为已按 Pine rollback 语义进行代码检查，但没有独立观察逐 tick 重放；已留证的真实三价案例使用信号收盘模式。
- 框不模拟成交、手续费、滑点、止盈止损触发或盈亏结算，也不会根据之后价格判定“成功”。
- 最初桌面大段粘贴失败的临时内容，已通过版本历史不保存地丢弃。最终 v9 来自网页完整保存、编译和源码回读核对，不把失败粘贴算作成功部署。
- 桌面刷新时曾载回旧布局浅色背景；随后恢复黑色背景与浅灰坐标，并保留 ETH 4H 启动区域放大实图。报告未声称额外刷新或没有留证的截图验收。
- 登记表全量契约运行得到 15 通过、1 失败：holdout 消耗白名单已有 8 个旧登记未同步。本次新实验已加入明确授权白名单，新登记的 9 项文件哈希与结构校验全部通过；未将旧任务的白名单缺口静默补齐，也未声称全库测试全绿。
- 本次不会改变实盘模型、仓位、信号定义、通知条件或订单；没有新增收益结论。

## 后续选项

当前需求已完成。若用户希望调整参考入场方式、R 倍数、延伸长度或标签密度，可在 08 组修改图形设置。若要把这些参考价升级为回测出场规则或实际交易参数，需要另行确定研究与执行范围，不能从本次图形验收直接推导。

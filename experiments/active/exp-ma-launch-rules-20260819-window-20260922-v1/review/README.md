# 2026-08-19 18:00–22:00 双均线密集启动规则回放

已完成：825标的×6周期共4950组输入，唯一Grade-A命中为Binance JUP 5分多头，北京时间20:55确认。197个初筛几何、125个独立端点、98个严格评分、97个拒绝。该命中不是Owner逐样本金标，也不是收益验证。

Owner更正将范围固定为北京时间18:00–22:00，所有可用币种，3m/5m/15m/30m/1h/4h。这里的检查时间是原规则完成核心后第5根确认K线的**收盘时间**，不是核心启动时刻。更早历史只作预热/形态上下文，所有价格输入与图片不超过22:00。无训练、生产或收益评估。

## 冻结方法

原Grade-A粗筛、50张认可家族距离、严格形态门、参考对照与质量阈值保持不变。发现规则用CLOSE均线，后来的HL2重渲染不改变本次规则定义。保留左14根连续性约束；删除只供出图的第6根右边距，右端止于原5根确认。按原60分钟固定簇去重，再严格评分，先筛Grade-A，再按同币同周期同方向240分钟质量优先去重。没有8000张数据集的配额约束。

数据池来自既有Binance/OKX USDT永续30分归档；同资产选择可见截止覆盖较新的场所，再优先Binance。完整UTC桶聚合1h/4h；同场所公共接口补3m/5m/15m。目标前1200根预热，逐源SHA冻结。目录不是历史上市币完整普查，可能含幸存者偏差。参考资料： [Binance官方K线接口](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)、[OKX官方历史K线接口](https://www.okx.com/docs-v5/en/)。

## 复现命令

```bash
.venv/bin/python -m pytest tests/test_ma_snapshot_inputs.py tests/test_ma_launch_snapshot_scan.py -q
.venv/bin/python -u -m yoyo.data.ma_snapshot_inputs --plan experiments/active/exp-ma-launch-rules-20260819-window-20260922-v1/plan.json
# 执行同场所缓存恢复，再将scan_plan.json与recovered_sources.json合并为scan_plan_final.json（按周期/资产/场所排序），在main冻结提交：
.venv/bin/python -m yoyo.data.ma_snapshot_recover --plan experiments/active/exp-ma-launch-rules-20260819-window-20260922-v1/plan.json
.venv/bin/python -u -m yoyo.datasets.ma_launch_snapshot_scan --plan experiments/active/exp-ma-launch-rules-20260819-window-20260922-v1/scan_plan_final.json
.venv/bin/python -m yoyo.datasets.ma_snapshot_gallery --plan experiments/active/exp-ma-launch-rules-20260819-window-20260922-v1/scan_plan_final.json
```

首次源码743e91fc4d，修正/图册7bf22434bb。高周期预跑结果results_high已失效保留：epoch-ms读取器将+1ns向下取整，遗漏恰好22:00收盘bar；完整端点计数发现，正式全量改+1ms并新增毒化集成测试。初次输入准备在聚合阶段主动中断，改为批量聚合并保留已生成输入；没有规则扫描结果或调参。此前模型、训练图、50张后续图及生产路径均不改。

## 指标适用性与对照

本轮检查原规则在固定时段触发哪些形态，不读取未来标签，不属于收益或新模型评估。val AUC、val样本、置换p、top-decile毛/净收益、胜率及匹配随机交易对照均不适用；不以命中数量证明有效性。以时间边界毒化、完整高周期桶、原NMS行为、参考校准相等和输入SHA/端点交叉检查作为相应严格对照。

## 实测结果与证据

| 周期 | 标的 | 每标的确认端点 | 初筛几何 | 严格评分 | Grade-A |
|---|---:|---:|---:|---:|---:|
| 3m | 825 | 81 | 104 | 50 | 0 |
| 5m | 825 | 49 | 64 | 31 | 1 |
| 15m | 825 | 17 | 23 | 14 | 0 |
| 30m | 825 | 9 | 3 | 1 | 0 |
| 60m | 825 | 5 | 3 | 2 | 0 |
| 240m | 825 | 1 | 0 | 0 | 0 |

- 唯一命中：JUP / Binance / 5m / LONG。核心4根，最后一根开盘20:25（收盘20:30），第5根确认收盘20:55；质量分0.5170502446，不是成功概率。完整阈值未变。
- WIF：3m/5m/15m/30m分别有10/12/2/2个粗筛几何（含4/5根重复），均未通过形态门；1h/4h无粗筛通过者。无WIF进入严格评分。图册中的WIF全貌不是命中。
- 对比原版本的6个参考校准标量：最大绝对差=0。原分数门槛0.3611898958919062，新版相同；distance_scale1.1995844782712832、good combined1.1405596810737586、good lockstep1.2079561976679911、family p950.9086119663618403、strong0.32507090630271557均相同。
- 825个标的中Binance653、OKX172。共有133650个确认端点。4950源的目标区间无缺bar、无越界价格；原始197几何全部核对core+5及确认时间。30组在18:00前不足120根历史（3m1/5m1/15m2/30m4/1h4/4h18），没有放宽有限值/连续性门；因此“有数据”不能理解为全部有同等充分的预热历史。
- 本地5项源问题：0G两场所时间列异常，已从Binance官方原生接口补回6周期；US100/US500/XOM的上市时间为9月10日，目标日尚未上市。所有825×6任务完成，未解决源资产数=0。
- 97项相关测试通过，保留15条已有依赖警告。独立QA核对全部输入SHA、时间、197候选、6校准值及16幅1920×960图；图右端均不晚于22:00。BTC/WIF的30m/1h/4h最后两根OHLC与原生API全部相等；1200bar预热相比完整已批准前缀的末端MA最大差：BTC1.6152e-5价格单位，WIF1.4985e-10（抽样预热对照，不宣称所有浮点值逐字节相同）。
- 浏览器已验收：全部16图、命中筛选1图、WIF筛选6图；16图=1命中+9拒绝示例+6WIF全貌。完整98严格候选CSV单独保留，未只报告成功样本。
- 已失效高周期预跑没有最终22:00 bar；正式运行加入该bar后1h严格候选2个，含STABLE22:00；最终1h仍0命中。旧预跑保留SUPERSEDED.json，不作为结论。

正式扫描冻结提交1c712bed861b5af3c8fe47b67b4d9d899d1926eb；图册源码21f6a30ffb；全部输入和参考证据见scan_plan_final.json和fetch_receipt_manifest.json。源CSV/PNG/ZIP仅保存在本地，元数据和报告入git。源码先于对应正式产物。

图册：http://127.0.0.1:8789/ 。知识库记录：https://app.notion.com/p/3e28856479af817695b6c0dca69c80bc 。

最终独立验收命令：

```bash
.venv/bin/python scripts/verify_ma_snapshot.py --plan experiments/active/exp-ma-launch-rules-20260819-window-20260922-v1/scan_plan_final.json
.venv/bin/python -m http.server 8789 --bind 127.0.0.1 --directory experiments/active/exp-ma-launch-rules-20260819-window-20260922-v1/review
```

## 风险与诚实声明

原规则5根确认意味着1h要等5小时、4h要等20小时。此回放不能称为在核心启动当下提前预测；规则本身是后来建立的，也不是8月19日真实在线运行记录。其他周期沿用15分参考和阈值，未经跨周期质量验证。命中不是逐样本Owner金标，不自动进入训练。缺失/损坏数据与不足预热必须列出，不能隐去。

## 下一步选项

Owner可逐图确认是否符合目标形态。如果希望识别18:00–22:00刚开始的核心，需要另定义无需未来5根的实时任务；本轮不为找回已知截图调低阈值。

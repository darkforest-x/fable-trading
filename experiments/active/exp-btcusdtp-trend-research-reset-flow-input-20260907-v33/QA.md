# V33 输入模块交付检查

## 范围

仅方法查重、独立纯解析模块与合成 ZIP 测试。真实行情行、新经济标签、holdout 本轮均读取 0；没有下载、缓存重建、交易规则/成本/TP-SL/Pine/模型/生产改动。

源码计划先提交于 `384053c`，解析器和测试提交于 `247039c`。旧 `yoyo/data/binance_um_archives.py` 未改；另修复旧测试中的已过期字段名并补 5m/15m 缺口反例。

## 实际执行

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m pytest -q tests/test_binance_um_flow_archives.py
.venv/bin/python -m pytest -q tests/test_binance_um_flow_archives.py tests/test_binance_um_archives.py tests/contracts/test_registries.py tests/boundaries
python3 scripts/md_to_html.py experiments/active/exp-btcusdtp-trend-research-reset-flow-input-20260907-v33/PROJECT_PLAN.md --out-dir analysis/html/v33_research_reset
```

- 新模块最终 70 项合成测试通过（执行者 0.55 秒）。
- Root 最终联合 **258 passed, 13 warnings in 23.76s**；警告为既有 Matplotlib/Pyparsing 弃用提示，未新增依赖。
- 初次联合检查 256 passed / 1 failed；旧 `test_parse_checksum_and_month_zip` 仍取 `non_15m_gaps`，但已提交旧解析器返回 `non_bar_gaps`。不导入新模块单独运行也复现。确认两旧文件无工作树差异后，修正测试字段、补真正缺一根的正反验证；没有修改源解析器或删除失败测试。
- Root 独立阅读全部新源码及测试后，要求并核对额外覆盖：同 OHLCV 仅真实买量改变时 delta 反号、base/quote 的无买卖状态一致、原生时间网格及 microsecond 亚毫秒拒绝。
- 输出原七列与旧解析器逐字段/类型精确相同；另外保存真实量、派生买卖差、来源/币种、官方 inclusive close 与理论完整 bar 可用边界。
- SHA、成员名、头列、数值/整数边界、下溢/上溢、零量与买量越界、保留极小余量、数据缺口不补均有合成检查。
- HTML 是已提交计划的标准转换，检查 UTF-8、viewport、三条路线标题及表格开闭；**未做浏览器/手机视觉 QA**，不称新的收益报告。

## 技能如何影响本轮

- `experimental-design` 与 `validate-data`：区分固定终点诊断、实际路径收益和新入口母群，先查重再定义新路线；旧失败不改判。
- `source-driven-development` 与 `analyze-data-quality`：依据官方真实成交方向字段，按单位/时钟/一致性检查接口；合成对照验证它不是烛色代理，不从格式通过推断真实历史可用。
- `documentation-and-adrs`：沿项目既有实验计划和 learning 约定记录接口选择，不另外新建冲突的设计文档体系。
- `extract-approach`：保存终点门与路径目标不等价、输入投影丢失真实量两条教训。

## 未验证与下一步

真实归档覆盖、资金费/盘口/OI、Binance 到 OKX 的跨源可用性、实际网络延迟、Pine 可取得字段的能力和任何新经济价值都未验证。图上没有新增可交易信号。下一步是另冻结 2023–2024 的来源与完整性检查，不直接从当前合成模块启动自动交易。

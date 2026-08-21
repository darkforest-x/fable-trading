# ETH 15m Pine V12F：官方编译与 Venue 锁定（2026-08-22）

## 结论先行

1. **冻结 V12F 已在 TradingView 官方 Pine 编译器通过。** 实际页面是
   `OKX:ETHUSDT.P`、`15m`、Candles，源码 SHA-256 为
   `9e03c2959e403632a8db06c66ee43487d7388e0dfdaf31abe5ae32218c7567de`，
   Pine v6，编译错误为 0，策略标题为 `ALLIN ETH 15m V12F Paper`。
2. **Owner 已把精确 venue 锁定为 `OKX:ETHUSDT.P · 15m`。** 这次确认只覆盖替换未保存的
   V9 编辑器草稿和执行 V12F 官方编译；不覆盖保存、发布、警报、paper/live 订单、forward
   启动、收益读取、参数修改或模型训练。
3. **这不是收益回测通过。** V12F 源码默认研究窗口仍是
   `2023-01-01 <= time < 2026-03-01`，而精确 parity 合同要求
   `2025-01-01 <= time < 2026-03-01`。本轮没有打开设置面板核验输入，也没有得到 97 笔
   TradingView 交易导出，因此 exact parity 仍为 NO-GO。
4. **经济结论没有变化。** V12F 仍只是目前冻结的研究 comparator；它此前最近半年正式
   holdout 为 `-3.46%`、最大回撤 `24.74%`、58 笔、胜率 `8.62%`、PF `0.912`，不能称为
   盈利版本。本轮没有再次读取、重算或用该 holdout 选参。
5. **Paper V2 仍未启动。** scanner、forward log、paper/live order、LR/LightGBM 训练和
   promote 均未发生。

## 官方编译证据

| 检查项 | 实际观察 | 门禁 |
|---|---|---|
| 冻结源码 | `allin_eth_15m_v12f_ma6_w8_full_gate_paper.pine` | PASS |
| SHA-256 | `9e03c295...7567de` | PASS |
| 源码规模 | 12,447 bytes / 243 lines | 记录 |
| Pine 版本 | v6 | PASS |
| TradingView symbol | `OKX:ETHUSDT.P` | PASS |
| 图表周期 | 15 minutes | PASS |
| 图表类型 | Candles | PASS |
| 官方编译错误 | 0 | PASS |
| 编译后活动策略 | `ALLIN ETH 15m V12F Paper` | PASS |
| 精确 parity 窗口 | 未在设置面板核验；源码默认从 2023-01-01 开始 | FAIL-CLOSED |
| V12F 97 笔逐笔导出 | 未取得 | FAIL-CLOSED |
| price/time/commission/net 对账 | 未运行 | FAIL-CLOSED |

点击 `Add to chart` 后，TradingView 将 V12F 显示为活动策略，且页面没有编译错误。当前已加载
图表区间约为 `2026-06-01` 至 `2026-08-22`，晚于脚本的 `researchEnd`，所以 Strategy
Report 没有交易数据。这只能证明源码能在官方编译器执行，不能提供收益、胜率或账本一致性证据。

验证结束后已从图表移除临时 V12F 策略，保留原来的“双均线”指标；没有保存布局、保存脚本、
发布脚本、创建警报或发送订单。Pine Editor 仍保留未保存的 V12F 文本，这是 Owner 明确批准的
编辑器覆盖结果。

## Owner 确认的边界

确认记录单独保存在
`experiments/active/exp-pine-eth-15m-forward-v2/owner_venue_confirmation.json`。协议生成器会校验：

- schema 必须是 `pine-eth-15m-owner-venue-confirmation-v1`；
- symbol 必须精确等于 `OKX:ETHUSDT.P`；
- timeframe 必须精确等于 `15m`；
- `compile_only=true`；
- `paper_forward_activation_approved=false`。

任何一项漂移，venue 门都会重新 fail closed。Owner 的这次“确认”不会被解释成 prospective
paper 启动批准。

## Paper V2 门禁变化

| 门 | 上一版 | 本轮 | 当前裁决 |
|---|---|---|---|
| Owner exact venue | proposed / 未确认 | `OKX:ETHUSDT.P · 15m` 已确认 | PASS |
| V12F official compiler | 无 receipt | 官方编译 0 error、hash/venue/15m/v6 一致 | PASS |
| V12F exact window/input | 无 | 默认 2023 start，未核验设置 | NO-GO |
| V12F 97-trade ledger parity | 无 | 无导出 | NO-GO |
| V9 exact window/input | compiler smoke only | 未变化 | NO-GO |
| V9 110-trade ledger parity | 无 | 未变化 | NO-GO |
| venue-exact funding/slippage | 未审 | 未变化 | NO-GO |
| P0/P1 | 未通过 | 未变化 | NO-GO |
| prospective paper approval | 无 | 本次明确不包含 | NO-GO |

V12F 的 compile gate 现在有五个真实 PASS：官方编译、源码哈希、venue、15m、Pine v6；但
`parity_window_matches=false` 与 `input_values_match_frozen_contract=false`，因此协议仍正确地保留
`V12F official compiler/source/venue/timeframe/window/input receipt` 阻塞项。

## 数据统计与零假设对照

本轮是外部编译/合同审计，不是方向性收益实验：

| 项目 | 数值 |
|---|---:|
| 新读取市场 bar | 0 |
| repository holdout rows read | 0 |
| compact pre-holdout ledger identity rows | 207（V9 110 + V12F 97） |
| TradingView 新导出交易 | 0 |
| 新策略收益路径 | 0 |
| 训练模型 / 选择阈值 | 0 / 0 |
| paper/live orders | 0 |

因此 val AUC、置换检验 p、top-decile 毛/净收益、胜率、PF 和匹配随机入场对照均不适用；填入
任何新数字都会把“编译 smoke”伪装成经济回测。等价的严格零假设是：源码 hash、symbol、
timeframe、Pine 版本、窗口/input 或官方错误数任一不匹配，编译门即失败；没有完整交易导出时，
ledger parity 必须保持 false。测试还把确认 symbol 改成 `BINANCE:ETHUSDT.P`，协议会重新加入
venue blocker，证明确认记录不会宽松匹配。

浏览器中已经打开的当前价格图在编译时可见，但没有读取交易或绩效指标，也没有用于任何参数、
收益或版本选择；该可见性已写入编译凭证，不冒充一次新的经济 holdout 验收。

## 风险与诚实声明

- 官方编译成功只回答“Pine 语法/运行时能否在当前 symbol 与周期加载”，不回答“策略是否赚钱”。
- 本轮没有改变止损、止盈、break-even、ATR、手续费、风险、六线 W8 gate 或任何超参数。
- `input_values_match_frozen_contract=false` 是有意 fail closed：没有在设置面板看到精确输入，就不猜。
- V12F 当前已知的低胜率与负半年收益没有被这次编译改善。
- `venue_owner_confirmed=true` 不等于 `paper_forward_activation_approved=true`。
- 当前项目仍处于 P0→P1；P4 paper-forward 不能越级启动。

## 下一步

若 Owner 另行批准精确 parity 操作，才执行下面两步：

1. 在 V9 与 V12F 设置中明确锁定 `2025-01-01 00:00 UTC` 至
   `2026-03-01 00:00 UTC`，分别生成 exact input receipts。
2. 导出 V9 110 笔与 V12F 97 笔 Strategy Tester trades，运行逐笔
   entry/exit time、price、commission、net-profit 对账。

即使这两步通过，仍需 P0/P1、funding/slippage review 和一次独立的 prospective log-only
paper 启动批准；activation timestamp 之前禁止回填。

## 复现命令

以下命令只校验编译证据与 blocked 合同，不读取 repository holdout：

```bash
cd /Users/zhangzc/fable-trading

shasum -a 256 \
  experiments/active/exp-pine-eth-15m-v1/pine/allin_eth_15m_v12f_ma6_w8_full_gate_paper.pine

PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_design_pine_eth_15m_paper_protocol_v2.py \
  tests/test_build_pine_eth_15m_artifact_manifest.py

PYTHONPATH=. .venv/bin/python \
  scripts/design_pine_eth_15m_paper_protocol_v2.py

PYTHONPATH=. .venv/bin/python \
  scripts/build_pine_eth_15m_artifact_manifest.py --verify

python3 scripts/md_to_html.py \
  analysis/p0_pine_eth_15m_v12f_compile_venue_lock_20260822.md \
  --out-dir analysis/html
```

# 盈利滚仓 V0.3：Python 研究版

先选 Python：需要逐腿费用、风险档位、标记价和资金费账本。Pine 适合后续画图与信号演示，但 TradingView 的 broker emulator 不是 OKX 强减执行器；本版不发布 Pine 或真实订单机器人。

## 已实现的管理规则

- 外部提供已确认入场时间和初始止损；本模块不冒充截图中未知的信号指标。
- 只在已完成 1h 回踩后突破确认、共同止损提高、达到初始 2R 后，于次开计算加仓。
- 不设固定加仓次数上限；可以切换成最多两次或不加仓，作同口径对照。
- 初仓和每次加仓都取利润风险、当时保证金、保护价附近维持保证金、合约数量规则所允许的数量。初仓请求 25000 不代表一定买 25000；实际缩量会写入结果。
- 保护价处仍有利润不等于现实中不会强减。代码使用当前标记价偏差和一跳缓冲；未来偏差、跳空、滑点仍可能突破模型假设。
- 不设“卖在最高点”规则。共同结构止损才是可事前计算的退出；没有拿 0.2296 回填 WIF 止盈。
- 原始分钟内顺序无法确定时，返回 ambiguous 和空 final_balance，反事实余额另列。

## 文件与运行

模块 `yoyo/evaluation/profitable_roll_v03.py`；整池研究 `yoyo/evaluation/profitable_roll_v03_study.py`；单笔 CSV 入口 `yoyo/evaluation/profitable_roll_v03_cli.py`。不访问账户、不自动下单。

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m pytest tests/evaluation/test_profitable_roll_v03.py tests/evaluation/test_profitable_roll_v03_invariants.py tests/evaluation/test_profitable_roll_v03_study.py -q
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python -m yoyo.evaluation.profitable_roll_v03_study --output /tmp/roll-v03-reproduction --workers 3
.venv/bin/python -m yoyo.evaluation.profitable_roll_v03_cli --help
```

单笔 CSV 应含 `open_time`（有时区的 ISO8601）或 `ts`（毫秒），以及 open/high/low/close。标记价 CSV 时间必须与价格完全对齐。JSON 风险档位每行含 max_quantity/mmr/max_leverage；资金费 JSON 是结算时间毫秒到费率的映射。参数来自你的合约规格与明确研究假设，不要把示例参数当实盘推荐。

```bash
.venv/bin/python -m yoyo.evaluation.profitable_roll_v03_cli \
  --prices /path/to/hourly.csv --marks /path/to/mark-hourly.csv \
  --tiers /path/to/risk-tiers.json --funding /path/to/funding.json \
  --entry '2026-08-19T18:00:00Z' --stop 0.1362 --requested-quantity 25000 \
  --tick 0.0001 --quantity-step 1 --min-quantity 1 \
  --capital 100 --leverage 40 --max-adds unlimited --output /tmp/roll-one-case
```

输入行情按项目规定来自 VPS/已有只读资料；本 CLI 不下载或写回 K 线。输出目录必须不存在，避免覆盖实验。缺少标记价会显式降为价格代理模型，缺少资金费显式标为未计，不能据此声称交易所真实收益。

## 跨币种研究范围

复用原 29 个 Binance 币种的全部 451 笔实际入场与 451 笔匹配随机入场。每条路径独立本金 100U；不是一个 100U 账户同时交易全部信号。模型可能让原本串行的入场路径重叠，不代表允许叠加同币实盘仓位。

完整历史标记价/资金费/风险档位缺失，因此这里使用明确假设的固定 5% 维持保证金率、40 倍杠杆上限；它不是 Binance 的真实档位。数量规则来自归档公开规格，未证明每个历史时刻相同。既有亏损信号、数据截尾、拒单和强减歧义全部保留。

另做已定位的 OKX WIF 案例，使用历史标记价和资金费、当前档位敏感性。不能把两类来源的证据等级混为一谈。5 个旧高收益币只用于展示；按原不加仓收益事后挑选，不能证明选币能力或整体胜率。

实盘还需要独立验证信号接入、账户模式、真实档位、成交/部分成交、保护单确认、断线恢复和资金占用；本交付不构成实盘准入。

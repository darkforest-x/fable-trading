# Grok 整晚任务批次（无人监督·规格明确·不碰主线·全走 val 纪律）

## 铁律（违反=作废，你无权修改）
- 禁改 features.py 主线特征表、禁改冻结模型/frozen 配置、禁碰 data/forward_log.csv
- 禁评估 holdout（train.py 不加 --eval-holdout）、禁改 2026-05-04 后窗口任何参数
- 所有实验只用 train/val；无论好坏都写报告；负结果也是资产（照样入库）
- 每任务独立 commit push 到 grok/overnight 分支（不合 main，owner 晨审）
- judgment/factor/回测用系统 python3；YOLO 用 .venv/bin/python；OKX 请求已封装 UA
- 时间戳换算用 pd.Timedelta，禁 astype(int64)//1e9（见 docs/learnings/）
- 每个新实验脚本放 scripts/，报告放 analysis/，命名带假设号
- 判定用现有纪律：净@maker 与 baseline 比、置换检验 p<0.01、单特征 baseline 对照

## === 特征/因子族（判断层最大盲区=成交量，28维里量只占3）===

### 任务1：成交量因子三连（H14/H17/H18）
src/factors/library.py 追加因果无前视因子并跑 factor_ic_screen：
- obv_slope（20根OBV斜率）、vol_dryup（密集期量/前48均量）、
  taker_imbalance（(close-low)/(high-low)*volume 的20根均值近似买压）
判定：|IC|≥0.03且符号稳定→候选，报告注明待owner单变量验证。→ analysis/p2b_factor_ic_vol.md

### 任务2：H15 密集质量二阶特征
加因子：ma_order_score（六均线排列有序度）、convergence_speed（spread二阶差分）、
ma_bandwidth_pct（均线束宽/close）。同上跑IC。→ analysis/p2b_h15_quality.md

### 任务3：H13 BTC大盘状态共享特征
用BTC_USDT_SWAP的1h EMA55斜率、1h ATR分位作为全币种共享特征，
在SWAP池测IC与单变量净增益。→ analysis/p2b_h13_btc_regime.md

## === 出场结构族（研究议程）===

### 任务4：H3 结构出场
labeling加label_candidate_ma_exit（入场后收盘跌破EMA21即出，其余同TP5）。
对比TP5基线：val AUC/p/top净@maker/持仓时长。→ analysis/p15_h3_ma_exit.md

### 任务5：H4 时间衰减紧缩出场
labeling加变体：持仓每12根SL收紧0.25×ATR。同上对比。→ analysis/p15_h4_time_decay.md

### 任务6：H5 波动率自适应障碍
TP/SL倍数按atr_pct三分位缩放（低波动收窄、高波动放宽），barrier_sweep对比固定TP5。
分波动率层看净收益。→ analysis/p15_h5_vol_adaptive.md

## === 宇宙/分层（研究议程）===

### 任务7：H11 市值分层模型
SWAP池按24h成交额中位数二分（大盘/山寨），各自训练 vs 合池训练，
比较val AUC与top净@maker。判定：分层是否稳定优于合池。→ analysis/p2b_h11_tiered.md

### 任务8：H8后续 30m深挖确认
30m池已知最强线索。用mtf_sweep在30m上跑TP{4,5,6}×horizon{48,60,72}完整网格，
确认h60是否稳定最优。→ analysis/p2b_h8_30m_grid.md（注意样本小，报告标注置信度）

## === 工程卫生（低风险规格明确）===

### 任务9：代码去重
- 合并重复隧道脚本tunnel_labelstudio.sh与ls_reverse_tunnel.sh（保留前者）
- queue8/9/10/12/14/15手写F1评估→ from src.detection.owner_eval import evaluate_owner_f1
- 不改行为只去重，跑pytest确认绿

### 任务10：测试覆盖补强
tests/加：factor库因果性测试（构造未来数据不影响当前值）、
label_candidate_scaled/breakeven的障碍数学四路径、
single_var_feature_gain的增益计算正确性。跑pytest确认全绿。

## 交付
每任务：代码+报告+独立commit+push grok/overnight。
全部完成写 grok_tasks/RESULTS.md（每任务一段：做了什么/结论/异常）。
按1→10顺序，某任务卡住就跳过并在RESULTS记录，不要停在一个任务上耗整晚。

# Local Signal V2 — 进度一页纸

**更新**：2026-08-11 00:32 CST

## 当前裁决

P1 历史发现级对照已完成，B2 30 根固定因果窗胜出；生产级仍未验收。

| 阶段/实验 | 状态 | 核心结果 |
|---|---|---|
| P0 strict-negative V2 | ✅ 8/8 PASS | 2,388 positive + 2,388 easy negative；时间切分与守恒通过 |
| A legacy 200 | ❌ FAIL | max Recall 7.54%，FP/1000=1,239.16 |
| B1 fixed 24 | ❌ FAIL | 无合格工作点；best-F1 点 FP/1000=904.90 |
| B2 fixed 30 | ✅ PASS / selected | conf=.35；P 81.93%、R 73.46%、F1 77.47%、FP/1000 81.12 |
| C3 range 20–30 | ✅ PASS | conf=.45；P 74.71%、R 70.95%、F1 72.78%、FP/1000 120.28 |
| P1 machine decision | ✅ historical accepted | `production_eligible=false`；holdout 消耗 0 |
| P2 | ⏸ owner gate | 推荐只增加 hard-negative mining，固定 B2 其余条件 |

## 当前项目方向

L1 YOLO 从旧 200 根全局图转向 30 根严格因果局部图与小结构框，只负责候选发现；L2
LightGBM/规则层继续负责交易判断。当前结果只证明历史发现可行，不证明经济 edge 或实盘精度。

## 禁止

- 自动 promote B2 / ACTIVE 或部署
- 未批准读取 holdout
- 把 P1 历史 precision 当成 forward/生产 precision
- 同时改变窗口、hard negatives、seed 或事件尺
- 真下单、改仓、清 forward_log

## 交付入口

```bash
open analysis/html/p1_local_signal_v2_report_20260811.html
cat reports/ACCEPTANCE_DECISION.json
cat analysis/output/p1_local_signal_v2/comparison.json
```

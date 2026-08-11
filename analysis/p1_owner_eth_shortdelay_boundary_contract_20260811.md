# ETH完美平台：竖线内核心与3–5根短延迟合同

日期：2026-08-11  
状态：**合同和审查池已纠正；尚未生成新训练金标，尚未训练新模型**

## Executive Summary

- **上一版框确实偏了。** 红框错误地向右覆盖了启动后的快速下跌。Owner新图明确：两条青色
  竖线之间的平台/转折段才是核心，本例约6根K；右侧下跌只可用于短确认，不能进入标签框。
- **10根延迟已撤销。** 新合同只允许核心结束后3–5根确认：3根优先、5根硬上限，delay6–10
  不再进入新训练和验收。
- **输入窗口不再固定20–30根。** 新训练图从最短充分上下文开始动态变化；首轮只试约14–22根
  （约6–10根前文 + 核心5/7根 + 3–5根后文），再根据precision继续向更短收缩。这是试探课程，
  不是新的固定模板。
- **旧资产保留，但不能直接训练。** 旧W20–30候选收紧到delay3–5后只有316张，其中265张
  （83.86%）仍处在right位置带。它们只用于复核形态和框边界；Stage A权重继续作初始化底座。

修正版合同图：`analysis/reference/owner_ethusdt_15m_semantic_delay_contract_20260811.png`。

## 红框边界已经从“估计区域”改成Owner两条竖线

本轮按Owner第二张图重画参考：左竖线是核心起点，右竖线是核心终点。框内必须能看到均线密集、
平台试探/拒绝以及启动转换；右竖线之后出现的快速行情属于结果和确认信息。

| 项目 | 被撤销的上一版 | 当前合同 |
|---|---|---|
| 核心横向边界 | 向右偏，包入启动后下跌 | 只在两条Owner竖线之间，本例约6根K |
| 核心常见宽度 | 机械沿用旧5/7根 | 语义约4–7根；旧5/7根只是待复核提案 |
| 输入窗口 | 固定20–30根 | 动态最短充分上下文；首轮试约14–22根并继续缩短 |
| 框后确认 | 0–10根，10为上限 | 3–5根；3优先、5硬封顶 |
| 框位置 | 人为覆盖四个宽位置带 | 随最短充分上下文自然变化，不固定坐标 |
| 正例依据 | 不能靠后续涨跌 | 仍只由框内平台语义与边界准确性决定 |

## 旧候选收紧后暴露出右侧位置偏置

沿用修复后的Stage-A时间split，只检查旧Owner提案中框后正好3–5根的事件：

| 检查项 | 结果 | 数据质量裁决 |
|---|---:|---|
| Stage-A事件与旧Owner manifest联结 | 2,378 / 2,378 | 完整，无orphan |
| Stage-A val排除 | 358 | 未参与候选选择 |
| delay3–5训练期候选 | **316** | 只是审查母池，不是训练集 |
| delay3 / 4 / 5 | 94 / 107 / 115 | 三档均有覆盖 |
| 旧框5根 / 7根 | 171 / 145 | 仅作边界提案 |
| middle / right / far-right | 36 / 265 / 15 | right占83.86%，位置偏置明显 |
| 旧物理路径含`images/val` | 45 / 316 | 历史错split目录名；修复后的Stage-A split均为train |
| holdout读取 | 0 | 未消耗holdout |

位置偏置的含义不是“强行把框移到中间”，而是旧W20–30图不符合新的动态短窗合同。新训练渲染
必须从同一事件生成不同的短上下文，先试约6–10根框前上下文，并保持框后3–5根；这样总窗口
约14–22根、框中心自然约落在53%–71%，不会锁死在一个X坐标。

## V2审查页同时审“形态”和“框是否偏”

从316张旧候选中确定性抽取200张：delay3/4/5分别80/65/55。页面按钮不再只问“是不是平台”，
而是分成：

- `形态和框都准`：形态成立，旧框也准确覆盖核心。
- `形态像但框要改`：形态可能成立，但旧框包多、包少或整体偏移。
- `不是目标`：不属于Owner终极目标。

所有候选仍是 `semantic_status=unreviewed`、`geometry_status=unreviewed`、
`training_eligible=false`。页面只读取既有pre-holdout图片与manifest；没有读取后续收益、模型
置信度、修复后的Stage-A val事件或holdout。200张中有30张源文件物理路径仍在旧
`dense_owner_w20_midbox/images/val/`，这是旧按币种split错误留下的目录位置，不代表本轮使用了
Stage-A val；事件资格一律以修复后的Stage-A时间split为准。

## 下一步：先重新定框，再构建真正的动态短窗训练集

1. 用V2审查结果冻结第一批“形态和框都准”正例；“形态像但框要改”进入边界重标，不直接训练。
2. 对确认正例重新渲染短窗：框前上下文先试6–10根、框后只取3–5根，同一事件按时间split
   分组，禁止跨split复制。
3. 构建同时间块、同窗口长度分布的真实空背景和难负例；Stage A `best.pt`只作初始化。
4. 单变量比较短窗档位，分别报告delay3/4/5的event precision、recall、FP/1000和首次命中；
   选择满足precision要求的最短窗口与最早delay。
5. Owner确认前不训练、不promote、不部署，不把事后形态模型表述成盘口tip模型。

## Further questions

- 约14–22根只是首轮搜索范围，不是永久合同；如果14根档已经保持精度，后续继续向更短测试。
- 当前只冻结了空头ETH参考。多头镜像仍需在进入同一类别前单独确认语义与颜色方向处理。

## Caveats and assumptions

- 316张是旧W20–30图片中的审查母池，不能代表新动态短窗已经构建完成。
- 旧5/7根框是历史Owner提案，但最新ETH图证明“历史Owner框”也不能自动等于今天的准确边界。
- 本轮是YOLO检测层的数据合同与数据质量审计。AUC、置换检验、top-decile收益、胜率和匹配
  随机对照组均为N/A；没有进行交易回测。
- 未读取holdout，未改阈值、成本、障碍参数、ACTIVE或forward配置。

## 复现命令

```bash
PYTHONPATH=. .venv/bin/python scripts/build_owner_eth_target_review.py
python3 scripts/md_to_html.py \
  analysis/p1_owner_eth_shortdelay_boundary_contract_20260811.md \
  --out-dir analysis/html
```

机器可读产物：

- `analysis/output/owner_eth_target_review_v2_shortdelay/summary.json`
- `analysis/output/owner_eth_target_review_v2_shortdelay/contract.json`
- `analysis/output/owner_eth_target_review_v2_shortdelay/candidates.jsonl`
- `analysis/output/owner_eth_target_review_v2_shortdelay/candidates.csv`

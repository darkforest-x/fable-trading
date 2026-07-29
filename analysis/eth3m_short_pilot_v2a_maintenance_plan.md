# ETH 3m pilot v2a 大脚本维护例外与拆分计划

日期：2026-07-29

## 例外范围

以下三个文件超过通用的 250 行审查线：

- `scripts/build_eth3m_short_pilot_dataset_v2.py`
- `scripts/validate_eth3m_short_pilot_dataset_v2.py`
- `scripts/build_eth3m_short_pilot_v2_dataset_report.py`

它们当前只用于一次性的冻结数据审计，不在 live、forward pulse、下单或 ACTIVE 路径中。v2a 已经按
manifest、图片 SHA256、owner receipt 和独立验证回执冻结；在本轮交付前做纯结构拆分，会扩大改动面并
迫使已经通过的语义证据重新定版。因此本轮保留显式 `SIZE_OK` 例外，而不是把行数问题隐藏起来。

## 失效条件

该例外只对当前 v2a 冻结构建有效。出现以下任一情况前，必须先完成拆分，不能继续往大文件追加逻辑：

1. owner 授权 v2b，把相邻时点逐图标签加入训练；
2. 新增 classification 训练入口或连续 replay 入口；
3. 复用构建器到 ETH 5m/10m 或其他币种；
4. 修改标签合同、事件合并、split purge 或 receipt 格式。

## 计划模块边界

| 当前文件 | 拆分目标 | 职责 |
|---|---|---|
| dataset builder | `src/detection/eth3m_v2_evidence.py` | 读取 owner 证据、固定回执、标签白名单 |
| dataset builder | `src/detection/eth3m_v2_events.py` | 重叠区间归组、事件级时间切分、embargo |
| dataset builder | `src/detection/eth3m_v2_render.py` | 200-bar 因果渲染、manifest/hash 导出 |
| dataset builder | 原脚本保留薄 CLI | 参数解析、调用与最终摘要 |
| validator | `src/detection/eth3m_v2_validation.py` | 纯函数式语义、文件、receipt、split 检查 |
| validator | 原脚本保留薄 CLI | 读产物、写 validation receipt |
| report builder | `src/reporting/eth3m_v2_report_data.py` | snapshot/source/table 数据 |
| report builder | `src/reporting/eth3m_v2_report_narrative.py` | Markdown 与 artifact blocks |
| report builder | 原脚本保留薄 CLI | 组装并写 artifact/report notes |

## 拆分验收

- 现有 18 个相关测试全部通过；
- 当前 `manifest.csv`、137 张 train/val 图片和 `owner_confirmation_receipt.json` 的 SHA256 不变；
- `weak_or_review_manifest.csv` 仍为 150 行、每类 30、target/event_id 全空；
- 独立验证 `passed`，378-bar embargo 与 30 图/29事件不变；
- 不读取 holdout，不启动模型，不改 ACTIVE。

在完成上述等价性证明前，v2a 继续标记 `diagnostic_pilot_only=true`、
`pilot_training_eligible=false`。

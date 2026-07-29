# 冻结数据管道重构要先证明产物等价

- **问题**：v2a 训练前必须把三个一次性大脚本拆成模块，但这些脚本已经绑定了 manifest、图片 SHA256、owner receipt 和验证回执。只看单测通过，不能证明冻结证据没有被结构重构改写。
- **死胡同**：把函数搬到新模块后直接跑 import/pytest 会漏掉渲染、CSV 排序、receipt JSON 顺序这类产物级变化；报告脚本能运行也只能证明当前输入可读，不证明数据集等价。
- **有效路径**：保留原脚本为薄 CLI 和向后兼容 re-export，在 `/private/tmp` 重建完整数据集，再逐文件比较 frozen 与 rebuilt 的 manifest/event/smoke/weak manifest、owner receipt、classes/README，以及 137 张 labeled 图和 150 张 weak/review 图 SHA256。
- **通用规则**：冻结数据管道的结构重构第一验收项应是“临时重建产物与 frozen 产物哈希一致”，测试和验证器只是第二层证据。
- **牵连**：`scripts/build_eth3m_short_pilot_dataset_v2.py`、`scripts/validate_eth3m_short_pilot_dataset_v2.py`、`scripts/build_eth3m_short_pilot_v2_dataset_report.py`、`src/detection/eth3m_v2_*.py`、`src/reporting/eth3m_v2_*.py`；不得读取 holdout、不得训练、不得 promote/ACTIVE。

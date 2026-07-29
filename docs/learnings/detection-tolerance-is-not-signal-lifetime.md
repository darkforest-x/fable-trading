# 检测定位容差不能当成信号有效期标签

- **问题**：ETH 3m v2 初稿把生产扫描允许检查 `tip/tip-1/tip-2` 的几何容差，解释成事件时点
  `T/T+1/T+2` 都是正例、`T+3` 必然过期。于是 87 张正图中有 58 张、规则负图中有 71 张
  没有 owner 对这些具体时点的判断。
- **死胡同**：把执行管道的检框位置集合直接复制成监督标签。扫描门回答“一个框右缘允许离盘口多远仍被
  识别”，有效期标签回答“晚一根或三根是否还值得入场”；二者对象和语义不同，不能互推。
- **有效路径**：训练集只保留用户实际看到并确认的当前 tip 正例，以及 Label Studio 明确判“不是”的
  当前 tip 负例。T-1/T+1/T+2/T+3/原 v10 时点全部写入无 target 的
  `weak_or_review_manifest.csv`；只有逐时点复核或 owner 明确批准寿命规则后，才能作为一次单变量加入。
- **验收**：验证器不仅检查构建器是否忠实执行规则，还要检查规则的证据来源：训练 `sample_kind`
  必须落在人工证据白名单；批量聊天确认要绑定固定 HTML、manifest 与图片 SHA256，且明确不冒充逐行
  Label Studio 金标。
- **通用规则**：任何从线上 gate、去重窗口、cooldown、扫描容差推导训练标签的做法，都必须先证明
  gate 与标签回答同一个问题。否则它只能生成待复核候选，不能生成真值。
- **牵连**：`scripts/build_eth3m_short_pilot_dataset_v2.py`、
  `scripts/validate_eth3m_short_pilot_dataset_v2.py`、`datasets/eth_3m_short_pilot_v2/`、
  连续 replay 的开火密度与事件级精度门。

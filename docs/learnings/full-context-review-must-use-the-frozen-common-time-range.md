# 全景复核范围必须取冻结快照的共同连续交集

- **问题**：全景审核图预设为完整 UTC 日前后各两小时，但所有冻结快照实际都在次日 01:15 截止，
  比预设少两根 15m K。继续按预设生成会得到不完整或被迫补造的尾部。
- **死胡同**：先把想要的视觉范围写成固定常量，再到逐币渲染时才检查 112 根；第一张即被
  fail-closed 拦截。静默缩短单个币、联网补数据或复制最后一根都会破坏跨图同口径和冻结身份。
- **有效路径**：在任何结果写出前先求全部目标快照的共同连续时间交集，确认 19/19 都是 110 根、
  0 gap；把失败且无产物的预检和合同修订时间记录下来，再统一改为 22:00–次日 01:15。
- **通用规则**：全景图先验证“目标范围 × 所有样本”的完整笛卡尔覆盖，再冻结画布；可用范围由
  冻结数据决定，不由视觉模板决定。缺 K 时 fail-closed，禁止静默换源、补齐或逐图漂移。
- **牵连**：`scripts/render_15m_ma_launch_owner_yolo_20260827_fullcontext.py`、
  `experiments/active/exp-15m-ma-launch-owner-yolo-20260827-fullcontext-v3/preregistration.json`、
  `analysis/output/ma_launch_owner_yolo_recent5d_v1/kline_snapshot/`。

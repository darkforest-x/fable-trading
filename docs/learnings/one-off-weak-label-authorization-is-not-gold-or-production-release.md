# 一次性弱标签训练授权既不能被 Gold 门阻断，也不能冒充 Gold 或生产放行

- **问题**：Owner 已明确要求把冻结的机器候选按指定 `t-3` 几何生成正负样本并在 3060
  训练，但执行过程把“候选不是逐样本金标”错误解释成必须再次等待 Owner 逐张审核，导致已授权
  的实验没有完成。
- **死胡同**：把仓库中“人工 Gold 必须逐样本确认”的正确规则无条件套到所有训练数据上，等于
  把 `training_authorized`、`gold_confirmed` 和 `production_eligible` 合并成一个布尔门。这样既会
  擅自缩小 Owner 的一次性实验授权，也容易在反方向把“已经训练”误记成 Gold 或可上线。
- **有效路径**：把三层资格分开落盘：对话中的明确指令只授权本实验使用
  `owner_authorized_weak_labels_not_gold` 和启动训练；数据与报告继续诚实记录逐样本 Gold=false；
  权重保持 production/promote/deploy=false。预注册同时绑定候选 SHA、机械几何、负例排斥、
  时间切分和禁止动作，执行器只检查这份精确授权，不再发明额外审核门。
- **通用规则**：收到 Owner 明确的一次性训练指令时，第一步写清四个独立字段：标签证据等级、
  本轮训练授权、可复用训练资格、生产/晋升资格。只阻断没有被授权的层级；不能用更高层级的门
  撤销低层级的明确授权，也不能因低层级已完成而自动提升证据等级。
- **牵连**：`experiments/active/exp-15m-ma-launch-t3-yolo10000-v1/preregistration.json`、
  `yoyo/datasets/ma_launch_t3_training.py`、`scripts/train_15m_ma_launch_t3_on_3060.sh`、
  `experiments/registry.yaml`、`artifacts/registry.yaml`；外部约束包括 holdout、ACTIVE、promote、
  部署、forward 与真金操作仍需各自的 Owner 授权。

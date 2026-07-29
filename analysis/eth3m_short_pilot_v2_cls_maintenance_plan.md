# ETH 3m v2 分类诊断脚本维护例外

日期：2026-07-30

`scripts/evaluate_eth3m_short_pilot_v2_cls.py` 是本次冻结诊断的单次审计 CLI：它只读取已经准备好的
train/val manifest、完成的本地 run 产物以及从 3060 只读复制的日志/退出码，并按预注册的固定
`p=0.50` 输出可复核 CSV/JSON。它不在 live、forward pulse、下单、promote 或 ACTIVE 路径中。

本轮保留显式 `# noqa: SIZE_OK`，以避免在结果已经冻结后为了纯结构变化重写指标与证据链。出现以下
任一情况前，必须把 manifest 选择、推理、指标和 artifact writer 拆到独立模块，并保持现有测试及
固定产物逐项等价：

1. 用于 ETH 5m/10m、其他币种或第二个实验；
2. 新增阈值、阈值扫描、smoke、经济性或 holdout 评估；
3. 接入定时任务、生产扫描、promote 或 ACTIVE；
4. 修改标签合同、split、门槛或基线定义。

当前例外不授权上述任何扩展，也不改变本轮 `failed_gates` 结论。

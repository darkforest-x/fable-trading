# Semantic target is not a fixed crop template

- **问题**：Owner 要模型尽可能准确识别“完美平台/启动形态”，但讨论反复把一张 ETH 示例的
  `11根前文 + 6根核心 + 11根后文`、框居中和约10根确认误写成数据集硬合同。
- **死胡同**：用中心40%–60%筛正例、把框后8–12根当目标，并按后续下跌强度给候选排序。
  这些做法会让模型学坐标和结果，而不是平台语义；10根本来只是最大可接受延迟，后面跌了也不能
  反向证明核心框正确。
- **有效路径**：把变量分成两类。语义变量由 Owner 判断核心4–7根是否是完美平台，并确认红框
  只落在两条形态边界之间；干扰变量用动态短窗覆盖。2026-08-11 二次收紧后，首轮输入试探约
  14–22根、后文只保留3–5根（3优先、5封顶），旧W20–30和delay6–10退出训练合同。候选审查
  按 delay×box-width×position 分层，不看收益或模型置信度；验收只画delay3/4/5并继续向更短窗压缩。
- **通用规则**：参考图负责说明“是什么”，不能直接规定“永远画在哪”。在构建视觉检测数据前，
  先明确 semantic target、nuisance axes 和 latency ceiling；正例纯度靠语义裁决，鲁棒性靠干扰
  轴覆盖，最早检出靠逐延迟评估，三者不得混成一个固定模板。
- **牵连**：`AGENTS.md`、`CLAUDE.md`、`HANDOFF.md`、`PROJECT_PLAN.md`；
  `datasets/dense_owner_w20_midbox/w20_manifest.json` 的原始5/7根框；
  `datasets/local_signal_v2_stagea_randomcrop_v1/w20_manifest.json` 的修复时间split；
  `analysis/output/owner_eth_target_review_v2_shortdelay/`；Stage A `best.pt`；后续短延迟训练、难负例与
  earliest-detection curve。

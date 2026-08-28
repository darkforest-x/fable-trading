# 编号反馈必须先绑定 manifest 血缘

- **问题**：Owner 说“第 42、44、48 张”时，仓库里同时存在多轮各 50 张的审核包；相同编号在不同 manifest 对应完全不同的币、时间和框。
- **死胡同**：直接把自然语言编号套到最新审核包，会选错参考图；即使图片看起来相近，后续阈值、距离和淘汰理由都建立在错误事实之上。
- **有效路径**：先追溯产生反馈当时的审核包，再把 `source_order + sample_id + manifest SHA-256 + 框坐标` 一起冻结；本轮由此确认 #44=`a20a0a4e50a94b1a017d38a0`、#42=`4e86ddc32a5401c49bf4aeb3`、#48=`0846b4f53090c2980df602b9`。
- **通用规则**：任何“第 N 张”的视觉反馈，第一步不是看图或调阈值，而是解析到不可歧义的 sample ID 和带哈希的 manifest；解析失败就停止使用该反馈。
- **牵连**：`experiments/active/*/results/review_manifest.jsonl`、`preregistration.json`、视觉相似度参考池、自动标签与重框流程。

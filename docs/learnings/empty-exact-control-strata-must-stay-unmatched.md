# 精确对照分层为空时应保留候选并拒绝伪造匹配

- **问题**：部分末月的信号占据很小的月份×时段×ATR 分层，叠加信号前后禁入半径后，精确随机对照池可能少于预注册的三笔，首次运行因此 fail-closed。
- **死胡同**：运行失败后偷偷借用相邻 ATR 桶、别的月份、缩小禁入区或有放回重复同一根；这些办法都让“匹配对照”在结果出现后换了问题，也会给末端赢家制造虚假基线。
- **有效路径**：主候选始终保留在绝对收益账本；匹配键和禁入区完全不动，少于要求数量就写 `unmatched_insufficient_exact_stratum`、对照均值与配对差为 NA，只从配对检验排除，并把运行期补丁单独提交留痕。
- **通用规则**：匹配设计必须预先有 insufficient-stratum policy；默认是“不放宽、不补齐、不删除主候选”，并同时报告全候选绝对结果、可匹配候选结果、unmatched 身份及其集中度。
- **牵连**：`protocol_amendment_01.json`、`build_matched_controls()`、47 个 matched 候选、2 个 partial-September unmatched 候选。

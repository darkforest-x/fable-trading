# 长密集段要用收核，不是加 pad

- **问题**：审计看到超宽框，E1 只减 x_pad 12px，肉眼无感；根因是 dense run 长达 74 根 bar。
- **死胡同**：继续抠像素边距 / 上 SAHI / 换大模型。
- **有效路径**：`MAX_DENSE_BARS` 单变量，run 内取 full_spread 最紧窗口；PAXG 74→24，w>0.25 份额归零。
- **通用规则**：对象定义（segment）错了，几何 pad 只是边框装饰；先定「一个目标最多多长」。
- **牵连**：`auto_label.py`、`p2a_e2_max_dense_report.md`、对照页 `label_audit_e2_compare.html`。

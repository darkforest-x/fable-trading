# 15m 候选逐样本边界审核包

本页覆盖 9,000 个新候选（SHORT 4,500 / LONG 4,500）。
它只在浏览器 localStorage 保存进度并导出 JSON，不会写训练标签、负例或启动训练。

直接打开：`public/index.html`

若浏览器限制本地文件，可在仓库根目录运行：

```bash
python3 -m http.server 8769 --directory experiments/active
```

然后打开
`http://127.0.0.1:8769/exp-15m-ma-launch-boundary-review9000-v1/results/public/index.html`。

每张 KEEP 必须明确选择：完整输入 W14–22、核心 4–7 根、确认 3–5 根。
蓝色 t-3 竖线不等于答案。LONG 仍为 `mirror_unconfirmed`。完整导出返回后，运行
`scripts/summarize_15m_candidate_boundary_review.py` 做 fail-closed 校验；校验结果仍不自动授权训练。

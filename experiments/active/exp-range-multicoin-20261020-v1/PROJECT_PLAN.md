# Native preview and cross-coin replication

Owner request: “先生成一个给我看看，同时回测一下其他币种” (2026-10-20).

- Render actual PEPE native baseline fills through installed Nautilus 1.231.0's bars-with-fills template. No new UI layout, account simulation, or external upload.
- Reuse BTC/ETH OKX minute archives and confirmed API extension. Restrict input to August 2024 onward because earlier monthly audits contain confirm=0. Require continuous minutes and complete 5m bins.
- Compare BTC, ETH and PEPE over the same 2024-08-02 through 2026-09-22 14:00 UTC interval, split at 2025/2026 year boundaries. Recompute each symbol's causal signal stream and matched controls.
- Freeze existing baseline and BE3 policies, 5-bar structural stop, 0.05% buffer, 20bp cost and independent overlapping positions. No new parameter search, production changes or promotion.
- Use the existing native runner and caches. Preserve raw ledgers, input receipts, summaries and weekly paired inference. Do not call independent summed R account return.
- BTC stop tick 0.1 reused from `spike_v8_lowtf_study.py`; ETH 0.01 from Task62; PEPE prior study 1e-9 protection / 1e-10 historical precision. Enforce every input price on its precision grid.

Reproduce with the existing isolated runtime:
`~/.local/share/crypto-toolkit/venvs/nautilus/bin/python -m yoyo.evaluation.range_multicoin`

Append `--preview-only` to render/reuse the preview without running other coins.

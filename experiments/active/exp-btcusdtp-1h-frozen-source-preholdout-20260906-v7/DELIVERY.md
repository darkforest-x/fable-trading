# V7 delivery receipt

2026-09-06. Economic rejection; profitability goal stays active.

- Source builder/config/plan/tests committedad8f5c8 before first price run.
  Presentation builder/tests/contract committed87ac4a6 before new artifact build.
- Pre-price733tests passed. Final command:
  `PYTHONPATH=. .venv/bin/pytest -q tests/test_hourly_impulse*.py
  tests/contracts/test_registries.py tests/boundaries/test_layer_imports.py`
  returned784passed in12.64s. V7new195tests:144research+51presentation.
- Two independent read-only saved-ledger reviews and root numerical recheck:
  `VERIFICATION.md`. No strategy reoptimization or raw-price reread in reviews.
- A replication-example error was found before delivery: paired-request CSV
  has286rows, not283;3unmatched remain. Corrected example now explicitly filters
  the common283 before means and was actually executed successfully. Frozen
  evaluator results were already correct and never changed.
- Full technical report uses existing definitions, ExecutiveSummary, methods,
  old experiment comparisons, four V7 evidence sections, limitations, next
  falsifiable hypothesis and reproducibility. No required section omitted.
- Exact gate/fold/taxonomy tables intentionally retain lookup values. New time
  comparison uses24observed months/two series;48rows with283matched/286all
  denominators. Original V1eight-policy bar andV6distribution remain unchanged.
- Compared withac02180,22prior markdown sections unchanged. Six dependent
  sections changed:title,summary,scope,next experiment,caveats,reproducibility.
  Four V7sections andonechart added;35totalblocks,3nativecharts.
- Native blue/orange, solid/dashed andlegend, not an unsupported gold override.
  Chart contract `CHART_CONTRACT.md`; actual SQLite/source paths inartifact.
- RepositoryMD converter ran immediately after finalMD edit. Primary delivery
  then rebuilt from canonicalartifact, not left as the plainMarkdown conversion.
- Standard DataAnalytics0.2.10 portable delivery: validationPASS,packagePASS,
  verificationstructural_only. No compatibleChromiumheadless-shell installed.
  HTML payload equality and required reader/fallback roots verified; chartSVG,
  source dialogs/interactions and desktop/mobile viewportQA not performed.
  No browserdownload and no custom renderer/browser workaround.
- FinalMD62951bytes SHA256
  `1363aa96ae5185d1a63f343b3bfc545674aaa2c029e4663e6317051d378a758f`.
- Finalartifact120113bytes SHA256
  `3858f03294125cac75a602f70dd79144363ac8c0dae8ef8110282c2508d8c5a4`.
- FinalHTML911728bytes SHA256
  `196cd148249fd4f13f4deb985b9044ac6000a9a714bb953885c0faed580755a1`.
- No TradingView, ACTIVE/frozen, VPS/forward, modeltraining, deployment, order,
  stop/position changes or claim of a profitable production candidate.

For standalone repeat packaging of the exact saved full artifact:

```bash
node /Users/zhangzc/.codex/plugins/cache/openai-curated-remote/data-analytics/0.2.10-13ceeea1f599/skills/build-report/scripts/deliver_portable_artifact.mjs --input analysis/output/btcusdtp_1h_impulse_ltf_exit_20260906_v1/artifact.json --output analysis/html/p1_btcusdtp_hourly_impulse_ltf_exit_20260906.html
```

This is an existing local Codex file-based technicalreport task, not a Sites
publication or MCPapp delivery. No second delivery surface was created.

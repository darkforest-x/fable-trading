# V8 delivery receipt

## Result and goal status

Rejected management candidate, not a profitable system. Goal stays active.
Exact286caseD=-2.1187bp;283matchedI=-3.0774bp;959serialintentiondelta+1.2046bp,
all95% intervals includezero. No2025+price/audit/holdout/live/Pine mutation.
Next proposed test, not executed: keep5mSMA40 memory, sample its decisions at
15m closes to distinguish cadence from the10h native15m memory. Preregister first.

## Build chronology

- e460539: management/spec/context/L3/synthetic tests/config/plan BEFORE real V8 run.
- 061f254: paired post-run diagnostic builder BEFORE generated diagnostics.
- f86324c: standalone native report builder/tests/design BEFORE artifact packaging.
- Old complete V1–V7 MD/HTML/artifact compare byte-identical to7ee6219.
- Source report was immediately passed through scripts/md_to_html.py; final
  HTML then replaced by canonical Data Analytics portable artifact packaging.

## Tests and calculation QA

```bash
.venv/bin/python -m pytest tests/test_hourly_impulse*.py tests/boundaries/test_layer_imports.py tests/causality/test_holdout_boundary_is_single_valued.py -q
```

Observed **1100 passed in11.44s**, no failures/warnings. Report-specific four
suites independently134passed; frozen source/nativeclock/helper synthetic checks
and old five-mode L3 synthetic exact-parity precede the real outcome run.

The exact Python block in VERIFICATION.md was executed from its saved text and
returned:

> PASS:2270 formulas/clocks,unchanged entries,fixed286/283/959pairings and independent serial loop

Report14blocks/1nativechart/12bins,all286cases. Count bins sum286, zero25,
negative188,positive73,unknown0. Mean/count/time/arithmetic and source identity
reconciliation passed. Every authored## section remains a distinct native
markdown block; tables are authored markdown, not separate native table blocks.

## Official portable builder receipt

```json
{"ok":true,"stages":{"validation":"passed","package":"passed","verification":"structural_only"},"browserWarning":{"code":"browser_unavailable"},"counts":{"blocks":14,"charts":1,"html":0,"metrics":0,"tables":0},"sourceDialog":"not_verified","sourceInteraction":"not_verified","viewports":[]}
```

No installed compatible Chromium headless-shell. Do not claim desktop/mobile,
light/dark,source-menu or chartSVG visual QA. Canonical payload equality and
required portable/runtime/semantic roots passed; native chart data fallback
remains present. No browser installation or bespoke automation was attempted.

## Final artifact identities

| Artifact | Bytes | SHA256 |
|---|---:|---|
| results/summary.json | 25732 | 8deef40bafe83d9e955b6b039adf0474aa4dcbf26f5870c070b6eccb02357f9f |
| diagnostics/report_facts.json | 38018 | 46aeeccbdb572b036d0dc6923a7732f0f1c880044cf446c53af0afafa0bca2bd |
| VERIFICATION.md | 6828 | b6938816f230031f989c5880b5708c183e8ef9607c9f1dedc681afd0d16e2215 |
| standalone MD | 17193 | 52e1969c0a3ef30bb6e6c2797c09b5138f4dc8845084063ee9338cf81a9fa73b |
| standalone HTML | 504856 | d2b6426598d60d0509685940c7fe049cf2ac2d549c83d77384c61604be89e3b4 |
| canonical artifact.json | 38625 | 3f1b068261cb571f6038713c51b12ce92c05e45f7e7b7fcb14b231c9767f8bfb |

Owner report: analysis/html/p1_btcusdtp_hourly_management_v8_20260906.html.
Reproduction and scope in PROJECT_PLAN.md and source report. Registries keep
training_eligible/production_eligible false. No guarantee of future returns.

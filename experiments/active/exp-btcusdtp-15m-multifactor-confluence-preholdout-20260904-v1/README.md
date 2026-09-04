# BTCUSDT.P 15m multifactor confluence research

This experiment keeps the broad EMA30 `state_reset3` entry ledger and the
SMA60 `ma_trail1_after_2atr` execution contract unchanged. It changes only the
registered feature bundle used to rank events.

The study is intentionally split into three commands:

```bash
PYTHONPATH=. .venv/bin/python -m scripts.research_btcusdtp_15m_multifactor_confluence --phase select
PYTHONPATH=. .venv/bin/python -m scripts.research_btcusdtp_15m_multifactor_confluence --phase confirm
PYTHONPATH=. .venv/bin/python -m scripts.research_btcusdtp_15m_multifactor_confluence --phase audit
```

`select` may read only the committed 2023 parent ledger. Its receipt, chosen
variant, fitted research model(s), thresholds and hashes must be committed
before `confirm` can read the 2024 parent ledger. The confirmation receipt must
then be committed before `audit` can read the already-seen 2025-through-2026-02
ledger.

No command reads repository holdout rows (2026-05-04 onward), changes the saved
TradingView indicator, promotes a model, changes ACTIVE/frozen/forward state,
or places an order. The output is retrospective hypothesis generation only.

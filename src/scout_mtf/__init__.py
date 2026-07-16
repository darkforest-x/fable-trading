"""Multi-timeframe rank scout — side branch, not mainline.

Pipeline:
  OKX SWAP 24h movers → pull 1m/3m/5m/15m/30m candles → per-TF dense/regime
  votes → composite grade (A/B/C).

Does NOT write forward_log, does NOT retrain, does NOT touch ACTIVE.
"""

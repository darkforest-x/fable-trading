# ETH3m v2 classifier fixed-threshold evidence

- invocation: `/Users/zhangzc/fable-trading/scripts/evaluate_eth3m_short_pilot_v2_cls.py --device cpu --batch 8`
- binary observable: exit_code=0, status=failed_gates
- artifacts: `/Users/zhangzc/fable-trading/analysis/output/eth3m_short_pilot_v2_cls_diag_20260730/summary.json`, `/Users/zhangzc/fable-trading/analysis/output/eth3m_short_pilot_v2_cls_diag_20260730/predictions.csv`, `/Users/zhangzc/fable-trading/analysis/output/eth3m_short_pilot_v2_cls_diag_20260730/metrics_by_split.csv`
- val gate: TP=0 / min 6; FP=0 / max 2; passed=False
- remote completion: exit_code=0; log_sha256=b8e6487b27a11e4dac060fffa787c1335326820f8b6397c0928da02d1437d86b; exit_receipt_sha256=13bf7b3039c63bf5a50491fa3cfd8eb4e699d1ba1436315aef9cbe5711530354
- remote/local best.pt SHA256 match: True (3ce89b668096e79eb00ae0ee8b4913024f91f46356626d22cbe11d3a98c30056)
- guard: holdout_read=False, weak_or_review_read=False, smoke_read=False

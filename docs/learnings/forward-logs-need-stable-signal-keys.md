# Forward logs need stable signal keys

- **问题**：Forward tracking must record new threshold signals immediately and later fill exits when TP/SL/timeout becomes known, while repeated daily runs must not duplicate rows.
- **死胡同**：A pure append-only CSV cannot fill an open signal's exit without creating a second row, and a naive rewrite can overwrite the original detection timestamp/model identity.
- **有效路径**：Use `(source, symbol, signal_time)` as the stable signal key. New keys are added once; existing open keys preserve detection/model fields and only receive outcome columns when they close.
- **通用规则**：Any paper-trading log with delayed outcomes needs a stable key plus field-level update rules before it is scheduled.
- **牵连**：`scripts/forward_track.py`, `src/judgment/forward_records.py`, `data/forward_log.csv`, frozen model path and dataset SHA.

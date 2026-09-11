# Display feature backfills must use full read-only seeds

- **Problem**: Existing live chart rows predated the IMACD signal-line field, so the UI could show OHLC and moving averages but not the MD/SB pair needed to interpret the lower panel.
- **Dead end**: Replaying the cached 240-chart window would restart the frozen feature recurrence from the wrong history. Rewriting `last_closed` or persisting the rebuilt result would also risk replaying historical arrows and notification receipts.
- **Effective path**: Read the selected cell's complete, validated checkpoint only when its stored chart lacks `sb`; calculate the existing feature/replay function over that full seed, return its trailing display chart in memory, and leave the stored market, journal, metadata, candidates, and outboxes untouched.
- **General rule**: A chart-only field migration may repair a read result, but it must validate the same recurrence seed used by the scanner and prove that every durable signal/notification table is byte-for-byte unchanged.
- **Involved**: `yoyo/monitor/replay_chart.py`, `yoyo/monitor/signals.py`, `yoyo/monitor/store.py`, `yoyo/monitor/service.py`, and monitor replay/chart-limit tests.

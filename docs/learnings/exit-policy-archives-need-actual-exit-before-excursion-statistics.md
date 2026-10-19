# Exit-policy archives need actual exit times before excursion statistics

- **问题**：Owner asks for V13.1 floating losses and profits. The 1H trail2 archive retains the incumbent exit timestamp even when tighter protection exits earlier; it stores correct returns but not the actual new exit time.
- **死胡同**：Slicing OHLC through that timestamp would include post-exit prices. Using the whole stop candle would also claim favorable wicks whose order relative to the stop is unknown. Existing peak-close fields alone cannot answer maximum floating loss.
- **有效路径**：Replay the unchanged protection rules from original entry and stop, recover the first actual fill, and reconcile every net R and close peak with the canonical exit study. Measure adverse excursion through that fill and favorable excursion as known/upper bounds on intrabar stop bars.
- **通用规则**：Before path statistics, establish whether an exit timestamp describes the tested policy or its parent. Stop reading prices at the actual exit; preserve intrabar ambiguity instead of inventing an OHLC sequence.
- **牵连**：`yoyo/evaluation/spike_exit_giveback.py`, `yoyo/evaluation/spike_v131_excursions.py`; original 2R activation, 1H 2ATR / other periods 4ATR, 20bp cost and frozen entries remain unchanged. No historical artifact is overwritten.

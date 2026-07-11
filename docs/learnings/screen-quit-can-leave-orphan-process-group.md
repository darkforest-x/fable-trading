# A vanished screen socket does not prove its workload stopped

- **问题**：A VPS deploy intermittently lost row-count-suffixed kline CSV files during `rsync`, even though the preceding data update had completed and the q80 `screen` session no longer appeared in `screen -ls`.
- **死胡同**：Treating an absent `screen` socket as proof of process termination hid a surviving login shell, cycle script and `update_okx` descendant. Re-running deploy alone could only race the same filename rotation again.
- **有效路径**：Inspect the process tree and process-group IDs, correlate the missing filename suffixes with the concurrently rewritten files, terminate the completed workload's whole process group, then rerun one complete update before deployment. The same deploy succeeding afterward provided toggle proof for the cause.
- **通用规则**：After stopping a terminal multiplexer workload, verify both its socket and descendants by command plus process-group ID. Serialize versioned-file writers with consumers such as `rsync`; a missing session UI is not a lifecycle guarantee.
- **牵连**：`scripts/run_q80_shadow_cycle.sh`, `src.data.update_okx`, `scripts/deploy_vps.sh`, `data/kline_fetched/`; strategy parameters and source code were not changed.

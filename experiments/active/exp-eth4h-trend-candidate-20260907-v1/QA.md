# ETH4h candidate QA

- Frozen builder096dbc6 evaluated21 preregistered arms/windows once, without holdout.
- Original replay regression:69 trades,7 fields exactly equal to preceding golden ledger.
-85 synthetic/behavior/boundary tests passed; matplotlib dependency deprecation warnings only.
- Independent saved-ledger verification54508d2:234 checks passed, including cash, fees, size, entry timing, control strata, paired means, date bounds and permutation round-trip stability.
- Initial report serialization failed on a NumPy boolean after all results were saved. Report-only recovery preserved all original CSV/JSON results; no strategy rerun.
- HTML opened and screenshot inspected in Codex browser; source download resolves to an exact Pine byte copy. Plot is embedded.
- No native Pine compiler or TradingView trade parity run. Independent accounting does not independently prove intrabar execution.
- S5 passes only the declared economic screen; final status inconclusive because historical data were reused, positive trades are sparse, posterior replication does not meet ranking acceptance, funding/slippage excluded.
- All economic outputs retained locally and hashed in artifact_manifest.json. No large ledger pack staged into git. Summary, validation and reports are retained in git.

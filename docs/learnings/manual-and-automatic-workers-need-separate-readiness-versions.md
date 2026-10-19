# Separate executor revision contracts when worker lifecycles are independent

- **Problem**: The manual worker still advertised its account-isolation revision after an automatic-worker clock fix changed the shared `ENGINE_REVISION`. The manual preview gate compared against that new automatic revision, rejected every preview and hid the distinction behind a reconciliation message.
- **Dead end**: Reloading only the web page or retrying the preview cannot change a running worker's revision. A generic “connected” badge proves neither executor compatibility nor entry readiness.
- **Effective path**: Give the manual worker an explicit `MANUAL_ENGINE_REVISION`, write it at manual-worker startup and check that same contract in the preview and read-only readiness projection. Keep the automatic revision and behavior unchanged. Display stopped, stale, incompatible and reconciliation-blocked states separately.
- **General rule**: Independently deployed execution paths need independently owned compatibility versions. A deploy must identify which running process loads the changed code and verify its advertised revision afterward.
- **Related code**: `okx_execution/__main__.py`, `manual_engine.py`, `manual_readiness.py`, `manual_api.py`; no risk or confirmation policy changes. The separate position issue is documented in `external-flat-reconciliation-must-not-send-another-close.md`.

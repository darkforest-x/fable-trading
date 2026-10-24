# Native streaming must preserve global event indices

- **Problem**: Multi-year minute histories expanded into explicit OHLC events exceed a comfortable memory budget if converted to native bars in one allocation.
- **Dead end**: Running each chunk in a fresh engine would terminate pending orders and positions at artificial boundaries. Replaying already successful assets after a memory fix would also waste work and change frozen evidence.
- **Effective path**: Use the installed Nautilus 1.231.0 `run(streaming=True)` contract, clear only input data between chunks and call `end()` once. Translate chunk-local OHLC phase indices into original history indices. The same engine, orders, clock and position state survive across boundaries.
- **General rule**: Batching is a storage concern, not an execution boundary. Verify a fixture that enters in one batch, confirms in another and exits in a third before processing history. Freeze input/config/code identity per successful asset and refuse silent replay under a changed identity.
- **Scope**: `run_native(..., batch_bars=...)` and `test_streaming_preserves_pending_fill_and_active_stop_across_batches`; existing callers keep the default non-streaming path. Installed API docstrings were checked before implementation.

# Research UI reuse must preserve read-only boundaries

- **Problem**: A separate Gemini workbench needed existing SPIKE signals without creating another scanner or exposing future chart information to the model.
- **Dead end**: Importing the existing monitor app looked reusable, but source inspection showed that application/store construction initializes runtime state and can activate workers. Reusing its current chart unchanged would also let bars after an older signal enter the prompt. Neither approach was executed.
- **Effective path**: Read the existing loopback GET APIs, whitelist signal fields, truncate candles and existing causal SMA/EMA values at the selected signal close, and require an exact, gap-free ending. Render only that window, freeze the pixels, and keep the research ledger in its own directory. Insufficient cache returns a visible error.
- **General rule**: Before reusing a running system, inspect construction side effects and data time boundaries independently. API reuse is safe only when both remain explicit.
- **Related files**: `yoyo/vision_research/source.py`, `server.py`, `store.py`, `tests/vision_research/test_source.py`. No changes to production qualification, monitor workers, trading or notifications.

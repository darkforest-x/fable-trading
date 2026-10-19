# Automatic inputs need an explicit mode and a response generation

- **Problem**: One-shot 1R suggestions become stale when collateral, leverage or limit price changes; delayed replies can overwrite a subsequent manual stop.
- **Dead end**: Repeating every request or inferring automatic mode from the current number mixes manual intent with network timing. Modals also interrupt a local calculation.
- **Effective path**: Model long/short automatic mode explicitly; debounce dependent parameter edits, invalidate calculations until the latest generation resolves, and cancel that generation immediately on a manual edit. Wrong-side opening remains disabled while an automatic side is selected. Server sizing is unchanged.
- **General rule**: A generated value is valid only for its parameter snapshot and active mode. Stale display values must never enable a submit.
- **Connections**: `mobile/src/useAutoStop.ts`, `App.tsx`, field validation and fixture acceptance for repeated parameter changes and manual edits.

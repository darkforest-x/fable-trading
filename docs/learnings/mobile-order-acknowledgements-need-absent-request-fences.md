# Mobile order acknowledgement must survive both slow execution and absent delivery

- **Problem**: A phone timed out while the server was still reconciling exchange state. Returning to confirmation made one instruction look like another trade opportunity.
- **Dead end**: A longer HTTP timeout alone does not distinguish a slow exchange from a request that never reached the server. Polling a missing ID forever avoids duplicates but traps the user.
- **Effective path**: Persist an immutable request envelope before acknowledging, execute once, and query receipts by identity. After repeated absent reads, atomically reserve a cancelled tombstone only if neither an envelope nor a trading intent exists. A delayed POST then cannot execute. Restart never replays envelopes.
- **Rule**: Separate transport acknowledgement from financial completion; an absent lookup needs a serialized fence before it can be treated as a failed delivery.
- **Scope**: `yoyo/discipline_app/order_requests.py`, `api.py`, `mobile/src/App.tsx`; synthetic blocked execution, duplicate POST, restart, and delayed delivery tests in `tests/discipline_app/test_mobile_flow.py`. No real order was used for acceptance.

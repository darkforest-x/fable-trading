# A bound button is not evidence of a working workflow

- **Problem**: Pro controls had click handlers but opened unrelated screens, left static follower cards behind, or focused disabled fields. A renamed runtime button lost its handler because the selector still searched its previous text.
- **Dead end**: Counting `data-pro-bound` markers missed semantic failures, and inspecting only visible buttons missed decorative switches and clickable model cards.
- **Effective path**: Load all ten immutable exported documents with their real adapters in a network-isolated DOM harness. Test visible outcomes: sorting changes rows, filters affect the current table, account updates refresh options, and settings expose the relevant form. Verify representative flows in the running browser.
- **General rule**: Audit intent → event → request/state → visible result, including keyboard submission, loading, missing configuration, and failure. Preserve mandatory execution checks; make their controls show the actual configuration or receipts instead of presenting fictitious toggles.
- **Files / boundaries**: `yoyo/monitor/static/pro/`, `tests/monitor/pro/frontend_interactions.test.cjs`. Stitch source/CSS and Standard remain unchanged. No live activation or exchange mutations were performed.

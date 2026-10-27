# Local storage read failures must survive memory caching

- **问题**：Drawings can remain usable when a browser denies storage or an existing record cannot be decoded. Returning to the same symbol must not turn a temporary edit into permission to replace that unreadable original.
- **死胡同**：Caching only drawing items drops the blocked-write state and its warning. Clearing a validation message during placement can also hide the persistence warning even though storage is still unavailable.
- **有效路径**：Cache items, blocked state and warning together. Reassert the persistence warning at save time; skip writes when the original could not be read. A failed write retains session items without claiming persistence.
- **通用规则**：A cache must preserve the safety state that determines whether its data may replace the source. Navigation does not repair an unreadable source.
- **牵连**：`yoyo/monitor/static/chart-drawings.js`; `output/playwright/chart-drawings-20261027/storage-qa.js` verifies corrupted-record preservation across symbol changes, storage denial, usable temporary drawings and native gesture restoration on blur.

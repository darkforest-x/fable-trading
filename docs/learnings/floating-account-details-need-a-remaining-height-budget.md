# Floating account details need a remaining-height budget

- **问题**：Adding capital metrics and refill history made the mobile allin3 account disclosure tall enough to extend behind the bottom chart toolbar. A 65dvh body limit did not account for the panel's vertical offset and wrapped summary.
- **死胡同**：Checking only document width and the collapsed account card passed while the expanded records remained clipped. A percentage of the whole viewport is not the available space below a floating header.
- **有效路径**：Observe the summary's actual height, reuse the existing context-card height, and subtract both from the mobile body's viewport budget while reserving space for the bottom toolbar. Keep the body scrollable. Browser QA expands refill history, scrolls the final amount into view, and asserts that the account panel ends above the toolbar at 390 and 320 pixels.
- **通用规则**：Validate long, expanded content as well as empty and collapsed states. Floating panels must budget the remaining viewport after their anchored offset, variable header and persistent controls.
- **牵连**：`monday-reclaim-paper.js` summary ResizeObserver; the mobile `.rp-account.has-capital .rp-account-body` height rule. This keeps the existing Stitch layout and account semantics.

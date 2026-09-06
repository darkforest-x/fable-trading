# V21 report contract

Audience technical: owner requests reproducible strategy development, causal
backtest, detailed failures and parameter/logic attribution. Primary portable
HTML, repository-mandated source markdown; no parallel Site or MCP report.
Question: does fixed external rank50 mean direction improve original BTC1h
entry selection? Answer spine: support passes, trade quality worsens, matched
incremental evidence weak, original winners disproportionately filtered.

## Reader path and technical specification mapping

Title External Rank Pressure; technical summary first. Definitions precede
comparisons because trade/opportunity/matched denominators differ. Visible
sections: 技术摘要; 固定比较; 时间覆盖; 每笔质量; 对照增量; 逐笔失败;
收益增量分布; 源码与时钟; 复核范围; 风险与诚实声明; 下一步; 复现命令.
Key findings use support bars plus exact economic tables. Methodology and source
contract have their own section; uncertainty sits beside D/I and concentration.
Recommended next step and further questions are combined explicitly. Full
reproduction commands required by repository/owner, not added boilerplate.

## Chart map and omission reasons

1. Support: one semantic BTC cohort, four fixed halfyears, native bar. x fold,
y accepted count, zero baseline and12 minimumreference, blue single root/no
legend/direct values. Retain total/abstain/unknown/counts in dataset. Discrete
support comparison, not a four-point trend or economic performance. Actual
SQLite aggregates713 entrycontexts into8 population/fold rows; four case rows
plot. Adjacent narrative explains gates and denominators.
2. D distribution: all251 paired-net increments, native categorical count bars
for explicit unequal bp bins; not density. Blue single root, zero count
baseline, direct labels; retain mean before/after/delta and binorder. SQL
directly aggregates saved case_delta, not a fabricated provenance label.
Zero outcomes and tails stay included.
3. Economic/exitcause/D/I tables require exact small-n values and incompatible
denominators; omit redundant charts rather than mix trade/opportunity means.
Ex-topone is concentration diagnostic only, not a second strategy result.
No account-equity line from event-bp sums or unknown position sizing.

## Sources and QA

Complete markdown preserved as separate native sections. Canonical sources:
reviewed MD; frozen713contexts; summary; independent verifier; case_delta.
Report refers to complete sourceMD with underlying evidence and commands;
chart SQL/path preserves direct transformation lineage. Builder committed
before artifact generation; official portable packaging validates canonical
payload and retains semantic fallback. No browser installs, bespoke renderer,
localhost workaround or rerouting around prior CUA denial. No compatible
installed browser means structural_only, not mobileQA. Bundled statistical
utility uses existing system Python; no dependency changes/outlier removal.

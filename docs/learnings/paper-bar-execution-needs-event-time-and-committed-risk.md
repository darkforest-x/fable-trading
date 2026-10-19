# Paper bar execution needs event time and committed risk

Date: 2026-10-15

## Problem

The Discord paper matcher filled a limit from a completed minute bar using wall-clock time. The subsequent position eligibility check then skipped that same bar, including a stop touched after entry. R also used final weighted entry times total size, so partial exits followed by additions rewrote the risk denominator.

## Failed assumptions

A bar touching both the entry and target does not prove entry preceded target. A recomputed average entry does not preserve previously committed risk. Neither a parsed signal nor an old dry-run execution record proves a paper fill.

## Working approach

Use the processed bar's close time for bar-derived fills and exits. Include newly filled positions in same-bar stop checks. For an intrabar limit, require a favorable close beyond the target before assuming a same-bar target fill; otherwise retain the position. Keep the existing conservative stop-first policy. Accumulate each actual entry's initial stop-distance risk, preserving it through partial exits; legacy multi-entry risk remains unknown. Compute all closed-trade statistics after filtering the account, before display pagination. Freeze accounts outside the explicit watchlist.

## General rule

Execution time, source time, and ingestion time are distinct. Performance denominators require immutable economic meaning and an explicit unknown state. OHLC bars cannot establish every intrabar ordering; this remains a paper model, not verified author execution.

## Evidence and related files

`tests/copier/test_focus_monitor.py` covers same-bar stops, ambiguous targets in both directions, immutable risk after partial exit/addition, archived accounts and a 501-trade statistic beyond the detail page limit. Implementation: `yoyo/copier/paper/book.py`, `yoyo/copier/api/routes/paper.py`.

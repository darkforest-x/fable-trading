# Market observation needs first-seen evidence and a regular clock

Date: 2026-10-15

## Problem

The market page treated retrospectively backfilled events after a research cutoff as forward observations. A missing BTC bar compressed the timeline. The latest-state table ignored event cooldowns. An unrelated invalid YAML scalar in research metadata rejected the whole page, hiding an HTTP 451 market-feed failure and stale cache.

## Failed assumptions

A post-cutoff timestamp is not proof that an event was detected in real time. Removing missing bars changes fixed-hour windows and horizons. A raw threshold hit is not necessarily an emitted event. Optional research metadata should not control availability of live-feed status.

## Working approach

Persist first observation times and the observer protocol start; label only promptly observed new events as forward. Reindex on regular clock slots and retain missing values. Return actual emission/cooldown decisions from the same function generating events. Withhold a basket return when any fixed member lacks endpoints. Calculate cache age at read time. Load live and research metadata independently and display source failure explicitly; keep the existing source and cost assumptions.

## General rule

Event time, observation time, data availability and study selection must be separately recorded. Missing data must not silently redefine a sample, time interval or basket.

## Evidence and related files

`tests/monitor/test_market_sync.py` verifies missing clock slots, incomplete baskets, first-seen persistence, stale cache and cooldown/cluster states. Implementation: `yoyo/monitor/market_sync.py`, `static/market-sync.js`. Two registry scalars containing colon-space were quoted without changing their text.

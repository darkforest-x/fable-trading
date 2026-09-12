# Exit-policy engine

The engine reads only the authenticated frozen `control_cache.pkl.gz` plus
`signals.csv.gz` in the V7/V1 two-year replay streams.  The cache SHA-256 is
checked against `control_cache.receipt.json` before replay.

`trades.csv.gz` holds one original-position row. `fills.csv.gz` is the
account-input ledger: `execution_phase` is `open`, `intrabar`, or `close`, and
every partial fraction is measured against the original position. The entry
fill carries `0.001` cost and exit/partial fills carry `0.001 * qty_fraction`;
all fills of one complete original position sum to 0.2% of entry notional.
`events.csv.gz` records close-confirmed protection changes and deferred actions.

`completion.json` is written only after the three gzip artifacts are atomically
renamed, making full-pool processing resumable per source stream.

# Approved V2.30 exit comparison

Owner approved the parameter list and both staged-stop variants on 2026-10-21.
Frozen inputs are BTC131/ETH99 and their existing matched random controls over
2025-10-21..2026-10-21 Beijing time, with the original touch-extreme risk and20bp.

Compare fixed1/2/3/5/10R; halves at1/3R and2/5R; two entry-known key targets;
5R with1/2/3R price or fee-cover BE;5R with half stopped at0.5R or first adverse
key; and key targets plus the adverse-key half stop. BE uses5m closed bars.
Each family retains its explicit baseline; no outcome-driven parameter changes.

Execution remains Nautilus1.231.0. Tranches conserve original shares and are
aggregated to original risk. Verify native orders, proportional fees, causal
key snapshots, opposite sides, gaps, target/stop collision sensitivity and
missing-key boundary exposure. Reuse the existing C++ audit kernel only.

Report every arm plus matched controls and boundary contribution. Annual
leaders are descriptive, not a selected live preset. No Pine/model/live change.

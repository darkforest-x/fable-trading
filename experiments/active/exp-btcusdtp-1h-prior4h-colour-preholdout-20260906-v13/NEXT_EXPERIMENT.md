# Next question after V13 — isolated prior20 breakout replication

Status: proposal only, NOT implemented, registered as a new experiment, run,
selected or accepted. V13 made actual progress and rejected pureprior4h entry
colour. Goal remains active and unmet, not blocked. No new price materialization
was used for the following bounded deduplication.

## Evidence and previous tests

V13 retains121 trades averaging-18.954bp, all4foldsnegative. It skips95losers but
32winners; matchedI154-1.653bp. Of91 retained losses,75have recorded heldMFE<1R,
83have nonpositivegrossreturn. This does not imply most losses merely need a
better take-profit. Those held-path statistics are outcomes, never entry gates.

`require_breakout20` is NOT new. In V1
`results/development_search.csv`, it was tested after `require_ma_slope=true` had
already been selected, with oldcolour exit:111to16trades,net-40.5567bp,failed
samplegate. `yoyo/data/hourly_impulse.py` defines strict K1close beyond
`high.shift(1).rolling(20).max()` or mirrored low; K1itself excluded.
`max_cross_count` and `min_efficiency` grids were also already searched.
Do not repackage these as fresh factors or imply the oldnegative is erased.

BoundedV1--V13 inspection did not find the exact isolated contrast on original251
without1h slope, withV5 true aligned-to-opposite exit and fixed462owncontrols.
Absence in a bounded search is not proof no related method exists elsewhere.

## Narrow next proposal

Keep original251cases462controls154triples,K1stop,72h,20bp,V5trueflip5m exit.
Remove V13gate; add ONLY own K1close beyond the previous20completed hourly highs
or lows according to direction. Fix20from existing specification, noNgrid or
optimization after seeingreturns. Controls use their OWN prior20boundary and
own signalhourclose, not the matchedcase gate. Preservealloriginalopportunities.

First freeze713 entry-known boundary/support/gate rows before outcomes. Prior
20hour source must be completecontiguous, excludedK1, causally available atK1
close; missingcontextunknown, knownfailedgatezero/noentry/nofee. Before any
return calculation reportselectedsupport andheritedsamplegates. Ifselection
itself cannot clear minimum80/min12fold, treatas scope/samplefailure and do not
use outcome ranking to rescue it or repeatedly consume data to pick anotherN.

If support warrants execution, actualreplay and alloldfieldparity; all251D and
same154I, completeunknowncounts, actualtradeecon/foregonewinners/serial. No1hslope
or4hcolourbundling, no exit alterations. Pre-register/commit builder/config/tests
before source run. This is a confound-reduced replication of an old hypothesis,
not new independent validation. Old61.35% coverage still cannot clear90%; any
fresh independent or productionclaim needs separate evidence/ownerdecision.

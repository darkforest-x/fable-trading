# Native V1 triple-exit engine

`yoyo.evaluation.spike_v1_triple_exit` is the eval-only replay builder for
this experiment.  It receives already prepared bars and an explicit native
event feed; it neither fetches data nor recomputes signals.  The default
`baseline` arm is the frozen V1 execution contract.  The extra arms use only
completed-bar MAE/MFE and activate on the following bar.

No historical replay output belongs in this directory before the builder and
the parent-owned protocol are frozen.

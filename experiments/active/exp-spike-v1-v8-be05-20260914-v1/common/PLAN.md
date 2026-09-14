# Common-execution V1 and V8 0.5R price-BE replay

This directory owns only `v1_common_execution_long` and frozen-admission V8.
Native V1 is deliberately excluded because it has a distinct execution
contract and a separate implementation owner in `native_v1/`.

The one predeclared change is: after a completed bar first reaches favourable
0.5 frozen actual next-open initial R, protection ratchets to actual entry for
the next bar. Prior protection keeps priority; gaps fill at the next open;
existing tighter protection remains tighter. Initial stop, 2R/4ATR trail, raw
opposite-signal priority, rounding, and 0.2% round-trip cost are frozen. Both
serial replay and fixed-original-entry paired exits are output.

The four canonical tables (`serial_baseline`, `serial_be05`, `fixed_baseline`,
and `fixed_be05`) use one clean execution contract. A pending reverse belongs
only to the position that scheduled it and is cleared whenever that position
closes, including an opening gap; a later entry cannot inherit it. This keeps
the BE treatment as the single experimental variable. Each arm also writes an
archival `legacy_baseline` receipt, which must exactly match its frozen oracle,
and a `legacy_to_clean_events` audit table containing every changed outcome and
its net-R/net-return delta. The archival receipt is never used as the baseline
for a BE comparison.

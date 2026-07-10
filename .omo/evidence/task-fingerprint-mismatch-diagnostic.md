# ACTIVE fingerprint mismatch diagnostic

**Result:** Root cause confirmed; no repair attempted.

**When:** 2026-07-10

## Finding

The ACTIVE model metadata points to a mutable dataset path that was rewritten
after the model was frozen.

| Field | Frozen metadata | Current file |
|---|---|---|
| Path | `data/swap_replication/swap_tp5_sl2.csv` | same path |
| SHA-256 | `818304cffcdb410612780e9d42dcdf7f8488c97e0044f93c1406ed2cb4856180` | `c38c08de00eeda27313e4979d22fbea52bf335f76a200c94eb69d17d1c2034fb` |
| Size | 5,555,342 bytes | 5,858,903 bytes |
| Frozen/model time | 2026-07-09 16:12 CST | file mtime 2026-07-09 16:46:57 CST |
| Rows | not stored | 9,312 data rows |

The current file is byte-identical to
`data/sweep_exits_swap/tp5_sl2_base.csv`. The original 5,555,342-byte file is
not present anywhere under `data/`, so the frozen training dataset cannot be
reconstructed from current local artifacts.

## Interpretation

- The pipeline warning is correct; this is not a dashboard bug.
- The ACTIVE model and threshold remain unchanged, so this evidence does not
  prove model-file corruption.
- Replacing the expected hash with the current hash would falsely claim that
  the model was trained on the rewritten dataset.
- The durable fix is content-addressed or dated immutable dataset snapshots at
  freeze time, with metadata pointing to that snapshot.

## Boundaries

- No metadata, model, dataset, ACTIVE pointer, threshold, holdout, or live-order
  path was changed.
- Repair is impossible without the original dataset bytes; future freezes must
  prevent recurrence instead of rewriting history.

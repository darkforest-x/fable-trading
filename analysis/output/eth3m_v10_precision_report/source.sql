-- Reproduce the metric-card and confidence-bin datasets from the diagnostic JSON.
-- Run from the repository root with:
-- sqlite3 -header -csv :memory: < analysis/output/eth3m_v10_precision_report/source.sql
WITH raw AS (
  SELECT CAST(
    readfile('analysis/output/eth3m_v10_precision_diagnosis.json') AS TEXT
  ) AS payload
),
summary AS (
  SELECT
    1.0 * json_extract(payload, '$.breakdown_state_at_fire.ret8_below_minus_2atr_n')
      / json_extract(payload, '$.population.n_tasks') AS already_broken_share,
    json_extract(payload, '$.future_outcome_not_shape_precision.future_3h_down_rate')
      AS future_down_rate,
    json_extract(payload, '$.temporal_repetition.events_gap_gt_60m') AS events_1h,
    json_extract(payload, '$.clock_mismatch.clock_compression_factor')
      AS clock_compression
  FROM raw
),
conf_fine AS (
  SELECT
    json_extract(bin.value, '$.bin') AS conf_bin,
    json_extract(bin.value, '$.n') AS n,
    json_extract(bin.value, '$.future_3h_down_rate') AS future_down_rate
  FROM raw
  JOIN json_each(
    raw.payload,
    '$.future_outcome_not_shape_precision.confidence_fine_bins'
  ) AS bin
)
SELECT
  summary.already_broken_share,
  summary.future_down_rate AS overall_future_down_rate,
  summary.events_1h,
  summary.clock_compression,
  conf_fine.conf_bin,
  conf_fine.n,
  conf_fine.future_down_rate
FROM summary
CROSS JOIN conf_fine
ORDER BY conf_fine.conf_bin;

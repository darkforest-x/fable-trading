-- Reproduce the headline, timing, and confidence datasets.
-- Run from the repository root with:
-- sqlite3 -header -csv :memory: < analysis/output/eth3m_v10_label_timing/source.sql
WITH raw AS (
  SELECT CAST(readfile(
    'analysis/output/eth3m_v10_label_timing/summary.json'
  ) AS TEXT) AS payload
)
SELECT
  json_extract(payload, '$.task_count') AS task_count,
  json_extract(payload, '$.owner_yes_count') AS owner_yes_count,
  json_extract(payload, '$.owner_no_count') AS owner_no_count,
  json_extract(payload, '$.owner_yes_rate_pct') / 100.0 AS owner_yes_rate,
  json_extract(payload, '$.owner_yes.box_elapsed_min_median') AS box_elapsed_min_median,
  json_extract(payload, '$.owner_yes.consumed_atr_median') AS consumed_atr_median,
  json_extract(payload, '$.owner_yes.share_consumed_exceeds_remaining_pct') / 100.0
    AS consumed_exceeds_remaining_rate
FROM raw;

WITH raw AS (
  SELECT CAST(readfile(
    'analysis/output/eth3m_v10_label_timing/summary.json'
  ) AS TEXT) AS payload
)
SELECT
  json_extract(bin.value, '$.confidence_bucket') AS confidence_bucket,
  json_extract(bin.value, '$.task_count') AS task_count,
  json_extract(bin.value, '$.owner_yes_count') AS owner_yes_count,
  json_extract(bin.value, '$.owner_yes_rate_pct') / 100.0 AS owner_yes_rate
FROM raw
JOIN json_each(raw.payload, '$.confidence_buckets') AS bin;

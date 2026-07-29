-- Material source query for the v10 ten-day pattern report.
-- `signals_raw` is the existing signals.csv loaded into an in-memory SQLite DB.
SELECT
    day,
    CAST(rank AS INTEGER) AS rank,
    symbol,
    CAST(daily_return AS REAL) AS daily_return,
    signal_time,
    CAST(conf AS REAL) AS conf,
    status,
    outcome,
    CAST(gross AS REAL) AS gross,
    CAST(net_taker AS REAL) AS net_taker,
    CAST(held_bars AS REAL) AS held_bars,
    CAST(gross AS REAL) - 0.002 AS net_project
FROM signals_raw;

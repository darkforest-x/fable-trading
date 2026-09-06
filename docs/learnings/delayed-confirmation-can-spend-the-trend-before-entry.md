# Delayed confirmation can spend the trend before entry

- **问题**：BTC 1h impulse/K2 experiments lost after 20bp costs. Requiring a
  real 5m aligned-to-opposite exit reduced premature exits but exposed trades
  to hard stops before alignment. Would waiting flat for alignment repair it?
- **死胡同**：Calling fewer hard stops or a more aligned entry an improvement.
  Flat waiting reduced hard stops11→4, yet same46 executed opportunities fell
  from −24.235 to −30.424bp. All24 actually delayed entries lost. The original
  K1 stop price stayed fixed, but directional chasing widened risk for14 trades.
  The ten improved entry prices still did not produce a net winner. A filter
  on hindsight winners or future MFE would conceal rather than solve this.
- **有效路径**：Retain all251 original mothers with known nonentries0 and
  unknownsNaN, then separate participation from execution on the same events.
  Participation saved119.897 summed event bp, but common executed events lost
  another284.702, net−164.804 (−0.657bp per mother, not portfolio P/L). In19 of24
  delayed trades the exit time and price were identical: lateness alone lost
  296.109 event bp. Check MFE in price-return units as well as R to avoid a
  changing risk denominator: median recorded holding-path MFE fell15.543→8.152bp;
  20/24 new paths did not cover20bp even at their recorded favourable extreme.
  This is a diagnosis of these paths, not a future-MFE entry gate or a claim
  that later trends cannot occur. Reject the waiting mechanism on this sample.
- **通用规则**：For delayed-entry experiments, freeze the original opportunity
  clock and stops, reprice the actual next open, retain nonparticipation and
  unknowns, and compare identical exit prices/times before blaming the exit.
  Confirmation may consume favourable movement rather than predict more of it.
  Next test a preregistered causal entry hypothesis, not another hindsight filter.
- **牵连**：`experiments/active/exp-btcusdtp-1h-flat-realignment-preholdout-20260906-v6/results/`;
  `VERIFICATION.md`; `yoyo/evaluation/hourly_impulse_realign_research.py`;
  report `analysis/p1_btcusdtp_hourly_impulse_ltf_exit_20260906.md`.
  Fixed20bp, original mother+8h/+72h clocks, repeated2023–2024 development only.
  Matching154/251=61.35% and sample46 fail promotion gates. No audit or live change.

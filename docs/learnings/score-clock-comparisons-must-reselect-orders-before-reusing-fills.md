# A score-clock comparison must reselect orders before reusing native fills

- **Problem**: The owner requested the original touch-bar score after a comparison
  used the pre-touch score. Both score clocks affect targets, linear sizing and
  add eligibility even when primary admission uses only EMA order >=3.
- **Dead end**: Relabelling the prior headline or changing only the admission
  gate would leave 109 of the 1,500 selected events in the wrong target/add band.
  Conversely, rerunning every native fill would waste already verified results.
- **Effective path**: Freeze the same 20261028 full account rules, explicitly
  choose `stored` and its `leaky_rest_touchbar` reference, and preserve the older
  causal experiment. Reuse a complete symbol only when score availability and
  the target/add band are unchanged for every selected event. Otherwise compare
  full order identity (prices, timing, protection and parent link), reuse exact
  fills and known nonfills, and run only missing orders with required native
  parent dependencies. Recompute the score-dependent account allocation.
- **General rule**: A score is not merely an admission flag. Enumerate all its
  downstream roles before changing its clock. Order identity determines fill
  reuse; unchanged prices alone do not establish identical orders. Match the
  comparator's score clock as well as the treatment's, and fail on disagreement.
- **Connections**: `yoyo/evaluation/monday_order_only.py`, the `stored` and
  `causal` columns of the frozen score audit, and
  `experiments/active/exp-monday-order-only-originalscore-20261031-v1/`. The
  original touch-bar convention retains known lookahead by explicit owner
  request. It is not an executable pre-touch result. BE17/lock50 and seed
  ensembles remain unchanged; the earlier booklet's BE0 is a separate baseline.

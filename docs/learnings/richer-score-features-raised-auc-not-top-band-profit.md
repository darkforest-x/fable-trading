# Richer entry-score features raised AUC but not the top-band profit; refits that included 2025 did worse on 2026

- **Problem**: The owner asked whether market context, volatility expansion, MA structure (the L2 features), per-coin history or LightGBM could improve the Monday 1.618 entry score. The study used a walk-forward split by year: each year was tested with a model trained only on earlier years.
- **Dead ends**: Ranking the options by AUC would have picked LightGBM on all features (AUC 0.68–0.69 against 0.65). In the 2025–2026 test years, however, that model's top 20 percent earned +9bp per trade against +26bp for the frozen 10-factor logistic score, and its equity replays were worse. The first comparison was also unfair: 16 percent of fills lack some base feature. The frozen score skips those fills, while the new models imputed them, so the two sides were ranking different trades.
- **What worked**:
  - Compare every model on the same rows.
  - Confirm the pipeline reproduces the frozen score in the year it covers: the 2025 fold matched exactly at +25.9bp on 2,967 fills.
  - Judge models by top-band net return per trade and by equity replays, not by AUC.
  - Outcome:
    - The extra feature groups help in some years (2023: +70 to +100bp; 2024: +56 to +81bp) but not reliably in 2025–2026.
    - Refits that included 2025 in training did worse on 2026: +10.6bp, against +25.6bp for the frozen pre-2025 model.
- **General rule**: Treat AUC as a diagnostic only. Before adopting a richer score, show that it beats the frozen score on the same rows, in top-band profit, in more than one out-of-sample year. Also check whether adding the most recent year to training helps the next year; here it hurt, which suggests the relationship drifted.
- **Related**: `yoyo/evaluation/monday_limit_score_features_v2.py`, `monday_limit_score_v2_train.py`, the CLAUDE.md rule "把 AUC 当成功标准".

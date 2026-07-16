"""Trading-cost assumptions: the route table (single source of truth).

CLAUDE.md lists cost assumptions among the four things only the project owner
may change -- yet by 2026-07-16 they had grown ~30 independent copies across
src/ and scripts/, with SWAP_MAKER_COST=0.0006 hard-coded seven times. Nothing
contradicted anything (different routes legitimately cost differently), but no
single place said which number belongs to which route, and a fee change would
have meant hunting down every copy. Owner approved consolidation 2026-07-16.

Live code under src/ imports from here. Experiment scripts under scripts/ that
back published reports keep their inline copies ON PURPOSE: editing them would
break reproduction of the numbers those reports state.

All values are ROUND-TRIP fractions of notional (entry + exit combined),
including the slippage allowance noted per route. Owner decisions, do not tune.
"""
from __future__ import annotations

from typing import Final

# ---- Spot routes -----------------------------------------------------------
# Stage-3 base case: taker both sides + slippage allowance (P0 assumption
# was 0.002; stage 3 tightened to 0.003 after the PF 1.01 failure showed the
# strategy's breakeven sat near 0.30%).
SPOT_TAKER: Final = 0.003   # 0.15%/side incl. slippage
SPOT_MAKER: Final = 0.0016  # 0.08%/side limit orders, owner route D

# ---- Swap (perpetual) routes -- the mainline universe -----------------------
SWAP_MAKER: Final = 0.0006  # OKX swap maker 0.02%/side + slippage allowance
SWAP_TAKER: Final = 0.0010  # OKX swap taker 0.05%/side (fills always)

# ---- Legacy / reporting-only -------------------------------------------------
# P0-era blanket assumption; kept because judgment train.py reports net returns
# with it for continuity with published p2b numbers. Not used for decisions.
LEGACY_P0_ROUND_TRIP: Final = 0.002

# ---- Stage-3 backtest knobs --------------------------------------------------
BASE_COST: Final = SPOT_TAKER            # base case for acceptance checks
COST_SWEEP: Final = (0.002, 0.003, 0.004)

# Forward validation reports net at the mainline execution route.
FORWARD_COST: Final = SWAP_MAKER

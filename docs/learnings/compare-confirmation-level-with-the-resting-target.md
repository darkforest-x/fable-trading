# Check whether an open position can reach a proposed confirmation level

- **Question.** Can a pre-entry swing identify which weekly reversal positions are worth holding once their first hourly reclaim has closed?
- **Dead end.** A swing can be causal and easy to measure while being unreachable during the position. On 63 of the 66 surviving trades, the resting target was closer than the swing. The target would fill first on a favorable move. On the remaining three, the confirmation arrived before a close crossed the swing.
- **Working method.** Freeze the swing before entry, count positions still open strictly after the decision time, and compare each target and swing in the trade's direction. Check the exit clock against the native bar index for gaps whose open shares the timestamp of the prior close.
- **Reusable check.** Test both reachability and coverage in the decision-time population before comparing group returns. Separate profit already marked at the checkpoint from the subsequent contribution to the original trade.
- **Evidence.** [Frozen rules](../../experiments/active/exp-live-wick-confirmed-swing-20261107-v1/config.json) and [event counts](../../experiments/active/exp-live-wick-confirmed-swing-20261107-v1/summary.json).

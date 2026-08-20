"""Evaluation semantics shared by every layer: splits, controls, gates.

These four modules exist because the same four questions were being answered
differently in four repositories:

    walk_forward      where does train end and test begin, and what is purged
    matched_controls  what would random entry have earned in the same conditions
    permutation       is the ranking better than a shuffle of itself
    economic_gates    does the result clear the standard this project accepts on

None of them knows about YOLO, LightGBM or the executor. They take frames and
return numbers, so a research layer and a backtest can be judged by the same
rule instead of by two rules that happen to agree.
"""

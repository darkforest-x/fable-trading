#!/bin/sh
set -eu
# From the repository root; reads saved native fills, never replays candles.
/Users/zhangzc/.local/bin/nautilus-python -m yoyo.evaluation.spike_booklet_close_compare

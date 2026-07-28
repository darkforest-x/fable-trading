#!/bin/bash
set -u
cd /Users/zhangzc/fable-trading
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
export MPLBACKEND=Agg MPLCONFIGDIR=/tmp/mpl-fable PYTHONPATH=/Users/zhangzc/fable-trading
export PATH="/Users/zhangzc/fable-trading/.venv/bin:/usr/bin:/bin:$PATH"
exec .venv/bin/python scripts/dump_short_tip_detect_sample.py \
  --count 1000 --preview 40 --device cpu \
  --out analysis/output/owner_side_short_tip_v1b_detect1000

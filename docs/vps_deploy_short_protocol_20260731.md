# VPS deploy checklist — short protocol P0 fix (2026-07-31)

**Goal:** ship side-aware forward (short ledger, no fake long buys), ACTIVE-as-runtime, empty 100-trade clock.  
**Does not:** enable short market execution; ENABLE_JOB_EXECUTOR stays **0**.

## Pre-flight (Mac)

- [x] `models/ACTIVE` → `models/frozen_tp5_sl2_swap_yolo_v10_reg_20260731.txt`
- [x] `load_runtime_artifact()` → side=`short`
- [x] Local `data/forward_log.csv` archived → `data/forward_log_pre_short_protocol_20260731.csv` then empty schema
- [ ] Code committed (see git log)
- [ ] SSH works: `ssh root@206.237.14.112 'hostname && ls /opt/fable-trading/models/ACTIVE'`

## Deploy command

```bash
cd /Users/zhangzc/fable-trading
bash scripts/deploy_vps.sh
# or enhanced path that also syncs scripts/tests and archives VPS log:
bash scripts/deploy_vps_short_protocol.sh
```

## On VPS after rsync

```bash
ssh root@206.237.14.112 '
set -e
cd /opt/fable-trading
# archive polluted log once
if [ -f data/forward_log.csv ] && [ ! -f data/forward_log_pre_short_protocol_20260731.csv ]; then
  cp -a data/forward_log.csv data/forward_log_pre_short_protocol_20260731.csv
fi
# empty mainline log if archive exists (owner 2026-07-31)
if [ -f data/forward_log_pre_short_protocol_20260731.csv ]; then
  # keep header-only empty if Python available
  python3 - <<PY
from pathlib import Path
import pandas as pd
from src.judgment.forward_types import FORWARD_COLUMNS
pd.DataFrame(columns=list(FORWARD_COLUMNS)).to_csv("data/forward_log.csv", index=False)
print("VPS forward_log reset")
PY
fi
cat models/ACTIVE
systemctl list-timers | grep -i fable || true
systemctl restart fable-dashboard || true
# do NOT enable job executor for short market without owner OK
'
```

## Verify

```bash
ssh root@206.237.14.112 '
cd /opt/fable-trading
PYTHONPATH=. python3 -c "
from src.judgment.frozen import load_runtime_artifact
a=load_runtime_artifact()
print(a.relative_model_path, a.config.side, a.threshold)
"
wc -l data/forward_log.csv data/forward_log_pre_short_protocol_20260731.csv
# one dry pulse if timer not immediate
# PYTHONPATH=. python3 scripts/forward_track.py  # if entrypoint exists
'
```

Local dashboard (if tunneled): 前向 should show **0/100** decision samples after reset.

## Rollback

```bash
# code: git checkout previous; re-deploy
# log: cp data/forward_log_pre_short_protocol_20260731.csv data/forward_log.csv
# model: echo models/frozen_tp5_sl2_swap_yolo_v11_reg_20260718.txt > models/ACTIVE
```

## Red lines

- Do **not** rsync Mac `data/kline_fetched` onto VPS  
- Do **not** set `ENABLE_JOB_EXECUTOR=1` until short execution is implemented + owner-approved  
- Do **not** mix archived long-protocol rows into the new 100-trade gate  

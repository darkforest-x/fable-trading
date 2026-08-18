#!/bin/bash
# Wait for the v10 mining run, then build both dataset arms and train both on the 3060.
#
# Arms differ in exactly one thing: whether the 40-bar render keeps the vertical
# scale a 200-bar window would have given it. Cropping the time axis inflates the
# six-MA cluster from 2.9% to 6.5% of frame height, and tightness is what the
# pattern is, so the arm that pins the scale back is the one to beat.
#
# Three things this machine needs and does not tell you about:
#   - a process started over ssh dies with the ssh session; WMI Win32_Process
#     survives it
#   - Windows spawns dataloader workers, so the trainer needs a __main__ guard
#     AND workers=0; the guard alone is not enough
#   - a log path from a previous run stays locked, so each run gets a new name
set -uo pipefail
cd /Users/zhangzc/fable-trading

MINE=analysis/output/v10_mine_preholdout/detections.jsonl
MINE_PID="${1:-}"   # empty = mining already finished, start now
HOST=zzc@192.168.1.3
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=15)
STAMP=$(date +%Y%m%d_%H%M)
LOG=analysis/output/v10_mine_preholdout/pipeline_${STAMP}.log
exec > >(tee -a "$LOG") 2>&1

say() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }

say "waiting for mining pid ${MINE_PID:-<none>}"
while [[ -n "$MINE_PID" ]] && ps -p "$MINE_PID" >/dev/null 2>&1; do sleep 120; done
say "mining finished: $(wc -l < "$MINE") detections"

remote_ps() { "${SSH[@]}" "$HOST" "powershell.exe -NoLogo -NoProfile -NonInteractive -Command -"; }

W=16; BOX=5; SUB=20
for ARM in plain yanchor; do
  if [[ $ARM == plain ]]; then ANCHOR=0; else ANCHOR=200; fi
  DS=datasets/smallwin_w${W}_${ARM}
  say "building $DS (y-anchor=$ANCHOR)"
  .venv/bin/python scripts/build_smallwin_dataset.py \
    --detections "$MINE" --out "$DS" --window "$W" --y-anchor-bars "$ANCHOR" \
    --box-bars "$BOX" --right-pad 0 --max-per-symbol "$SUB" \
    || { say "build failed: $ARM"; continue; }

  say "shipping $ARM"
  COPYFILE_DISABLE=1 tar czf "/tmp/${ARM}.tgz" -C datasets "smallwin_w${W}_${ARM}"
  scp -q -o ConnectTimeout=15 "/tmp/${ARM}.tgz" "$HOST:C:/fable/datasets/_smallwin/" || \
    { say "scp failed: $ARM"; continue; }

  printf 'path: C:/fable/datasets/_smallwin/smallwin_w%s_%s\ntrain: images/train\nval: images/val\nnc: 2\nnames: [dense_short, dense_long]\n' "$W" "$ARM" > "/tmp/${ARM}_data.yaml"

  "${SSH[@]}" "$HOST" "cmd /c \"cd /d C:\\fable\\datasets\\_smallwin && tar xzf ${ARM}.tgz\"" >/dev/null 2>&1
  scp -q -o ConnectTimeout=15 "/tmp/${ARM}_data.yaml" \
    "$HOST:C:/fable/datasets/_smallwin/smallwin_w${W}_${ARM}/data.yaml"

  printf '@echo off\r\nsetlocal\r\ncd /d C:\\fable\r\nC:\\fable\\.venv\\Scripts\\python.exe -u C:\\fable\\train_smallwin.py --data C:/fable/datasets/_smallwin/smallwin_w%s_%s/data.yaml --name %s_%s --epochs 30 --batch 12 --workers 0 > C:\\fable\\logs\\smallwin_%s_%s.log 2>&1\r\necho DONE_RC=%%%%ERRORLEVEL%%%% >> C:\\fable\\logs\\smallwin_%s_%s.log\r\n' \
    "$W" "$ARM" "$ARM" "$STAMP" "$ARM" "$STAMP" "$ARM" "$STAMP" > "/tmp/run_${ARM}.cmd"
  scp -q -o ConnectTimeout=15 "/tmp/run_${ARM}.cmd" "$HOST:C:/fable/run_${ARM}.cmd"

  say "launching $ARM on the 3060"
  remote_ps <<PS | tr -d '\r'
\$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='cmd.exe /d /c "C:\fable\run_${ARM}.cmd"'}
Write-Output ('$ARM RC=' + \$r.ReturnValue + ' PID=' + \$r.ProcessId)
PS

  # arms run one after the other: 12GB is not enough for two 960px detectors
  say "waiting for $ARM to finish"
  while :; do
    sleep 120
    done_line=$(remote_ps <<PS | tr -d '\r'
if (Test-Path C:\fable\logs\smallwin_${ARM}_${STAMP}.log) {
  (Select-String -Path C:\fable\logs\smallwin_${ARM}_${STAMP}.log -Pattern 'DONE_RC=' | Select-Object -Last 1).Line
}
PS
)
    [[ -n "${done_line// }" ]] && { say "$ARM finished: $done_line"; break; }
  done
done

say "both arms done. results:"
remote_ps <<'PS' | tr -d '\r'
Get-ChildItem C:\fable\runs\smallwin -Directory | ForEach-Object {
  $c = Join-Path $_.FullName 'results.csv'
  if (Test-Path $c) { $_.Name + ' -> ' + (Get-Content $c -Tail 1) }
}
PS
say "pipeline complete"

# Train owner_v14_pad200 on Windows + NVIDIA. Do NOT promote; do NOT eval holdout.
# Prerequisites (Mac ships data over SSH — do NOT USB-copy by default):
#   1) On Mac: bash scripts/sync_v14_to_windows.sh  (host zzc@192.168.1.5 → C:/fable)
#   2) On this box: git pull (docs/scripts); dataset already under datasets/dense_owner_v14_pad200
#   3) Base weights: models/owner_v12_htip.pt (or models/owner_best.pt = v12)
# Usage (from repo root, PowerShell) — or start via WMI from Mac (see p_v14_windows_train.md):
#   .\scripts\train_v14_pad200_windows.ps1
# Optional env:
#   $env:BATCH=16; $env:WORKERS=8; $env:DEVICE=0

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

$Out = "datasets\dense_owner_v14_pad200"
$Data = Join-Path $Out "data.yaml"
if (-not (Test-Path $Data)) {
  Write-Error "Missing $Data — on Mac run: bash scripts/sync_v14_to_windows.sh"
}

$Base = "models\owner_v12_htip.pt"
if (-not (Test-Path $Base)) { $Base = "models\owner_best.pt" }
if (-not (Test-Path $Base)) {
  Write-Error "Missing base weights (owner_v12_htip.pt or owner_best.pt)."
}

$Batch = if ($env:BATCH) { [int]$env:BATCH } else { 16 }
$Workers = if ($env:WORKERS) { [int]$env:WORKERS } else { 8 }
$Device = if ($env:DEVICE) { $env:DEVICE } else { "0" }
$Py = if ($env:PY) { $env:PY } else { "python" }

New-Item -ItemType Directory -Force -Path logs, models, analysis\output | Out-Null
$env:PYTHONPATH = "."
$env:PYTHONUNBUFFERED = "1"

Write-Host "=== v14 pad200 train $(Get-Date) base=$Base batch=$Batch workers=$Workers device=$Device ==="
# SAFE_AUG is enforced inside src.detection.train (fliplr/flipud/mosaic/mixup/hsv off).
& $Py -m src.detection.train `
  --data $Data `
  --model $Base `
  --epochs 40 --patience 10 `
  --batch $Batch --workers $Workers `
  --device $Device `
  --cache disk `
  --name owner_v14_pad200

$W = "runs\detect\owner_v14_pad200\weights\best.pt"
if (-not (Test-Path $W)) { $W = "runs\detect\runs\detect\owner_v14_pad200\weights\best.pt" }
if (-not (Test-Path $W)) { Write-Error "TRAIN FAILED: no best.pt" }

Copy-Item -Force $W "models\owner_v14_pad200.pt"
Write-Host "stable: models\owner_v14_pad200.pt"
Write-Host "Next (Mac/VPS ok): tip_detectability + tip-smoke — see analysis/p_v14_windows_train.md"
Write-Host "NOT promoted. Owner must approve promote separately."

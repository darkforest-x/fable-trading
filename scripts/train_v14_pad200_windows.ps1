# Train owner_v14_pad200 on Windows + NVIDIA. Do NOT promote; do NOT eval holdout.
# Prerequisites:
#   1) git pull (docs/scripts)
#   2) Copy datasets/dense_owner_v14_pad200/ onto this machine (see analysis/p_v14_windows_train.md)
#   3) Base weights: models/owner_v12_htip.pt (or models/owner_best.pt = v12)
# Usage (from repo root, PowerShell):
#   .\scripts\train_v14_pad200_windows.ps1
# Optional env:
#   $env:BATCH=16; $env:WORKERS=8; $env:DEVICE=0

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

$Out = "datasets\dense_owner_v14_pad200"
$Data = Join-Path $Out "data.yaml"
if (-not (Test-Path $Data)) {
  Write-Error "Missing $Data — copy dense_owner_v14_pad200 from Mac first."
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

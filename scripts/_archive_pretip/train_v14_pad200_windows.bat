@echo off
REM Train owner_v14_pad200 on Windows + NVIDIA. See analysis/p_v14_windows_train.md
cd /d "%~dp0\.."
set PYTHONPATH=.
set PYTHONUNBUFFERED=1
if "%BATCH%"=="" set BATCH=16
if "%WORKERS%"=="" set WORKERS=8
if "%DEVICE%"=="" set DEVICE=0
if "%PY%"=="" set PY=python

if not exist datasets\dense_owner_v14_pad200\data.yaml (
  echo Missing datasets\dense_owner_v14_pad200\data.yaml — on Mac: bash scripts/sync_v14_to_windows.sh
  exit /b 1
)

set BASE=models\owner_v12_htip.pt
if not exist "%BASE%" set BASE=models\owner_best.pt
if not exist "%BASE%" (
  echo Missing base weights.
  exit /b 1
)

echo === v14 pad200 train base=%BASE% batch=%BATCH% workers=%WORKERS% device=%DEVICE% ===
%PY% -m src.detection.train --data datasets\dense_owner_v14_pad200\data.yaml --model %BASE% --epochs 40 --patience 10 --batch %BATCH% --workers %WORKERS% --device %DEVICE% --cache disk --name owner_v14_pad200
if errorlevel 1 exit /b 1

set W=runs\detect\owner_v14_pad200\weights\best.pt
if not exist "%W%" set W=runs\detect\runs\detect\owner_v14_pad200\weights\best.pt
if not exist "%W%" (
  echo TRAIN FAILED: no best.pt
  exit /b 1
)
copy /Y "%W%" models\owner_v14_pad200.pt
echo stable: models\owner_v14_pad200.pt
echo NOT promoted.

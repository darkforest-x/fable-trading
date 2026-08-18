@echo off
setlocal
cd /d C:\fable
if not exist C:\fable\logs mkdir C:\fable\logs
echo [launcher] started %DATE% %TIME% > C:\fable\logs\fixed_w10_core4_confirm1_v1_cls.log
C:\fable\.venv\Scripts\python.exe -u C:\fable\train_fixed_w10_cls.py --data C:/fable/datasets/fixed_w10_core4_confirm1_v1/classification --model C:/fable/inputs/c62d41bf9625777760018bf914d2e6cd472420ccd01706d97a61cb6c82502bd7/yolo11n-cls.pt --name fixed_w10_core4_confirm1_v1_cls --project C:/fable/runs/classify >> C:\fable\logs\fixed_w10_core4_confirm1_v1_cls.log 2>&1
set RC=%ERRORLEVEL%
echo [launcher] exit_code=%RC% %DATE% %TIME% >> C:\fable\logs\fixed_w10_core4_confirm1_v1_cls.log
echo %RC% > C:\fable\logs\fixed_w10_core4_confirm1_v1_cls.exit_code
exit /b %RC%

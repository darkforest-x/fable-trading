@echo off
setlocal
set "PYTHONPATH=C:\fable"
cd /d C:\fable
>> "C:/fable/runs/ma_profit3r_owner1500_neg_v5/job.log" echo [launcher] started %DATE% %TIME%
C:\fable\.venv\Scripts\python.exe -u -m scripts.windows.run_ma_profit_negative_redo --experiment "C:/fable/experiments/active/exp-ma-profit3r-negatives-20260922-v2" --run-root "C:/fable/runs/ma_profit3r_owner1500_neg_v5" >> "C:/fable/runs/ma_profit3r_owner1500_neg_v5/job.log" 2>&1
set FABLE_TRAIN_RC=%ERRORLEVEL%
>> "C:/fable/runs/ma_profit3r_owner1500_neg_v5/job.log" echo [launcher] exit_code=%FABLE_TRAIN_RC% %DATE% %TIME%
> "C:/fable/runs/ma_profit3r_owner1500_neg_v5/wmi_exit_code.txt" echo %FABLE_TRAIN_RC%
exit /b %FABLE_TRAIN_RC%

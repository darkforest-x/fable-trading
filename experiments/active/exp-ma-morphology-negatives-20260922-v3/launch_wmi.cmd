@echo off
setlocal
set "PYTHONPATH=C:\fable"
cd /d C:\fable
>> "C:\fable\experiments\active\exp-ma-morphology-negatives-20260922-v3\complete.log" echo [launcher] started %DATE% %TIME%
C:\fable\.venv\Scripts\python.exe -u -m scripts.windows.complete_ma_morphology_redo --experiment "C:\fable\experiments\active\exp-ma-morphology-negatives-20260922-v3" --dataset "C:\fable\datasets\ma_launch_owner1500_morph_v6" --run-root "C:\fable\runs\ma_launch_owner1500_morph_v6" --launch-contract "C:\fable\experiments\active\exp-ma-morphology-negatives-20260922-v3\launch_contract.json" >> "C:\fable\experiments\active\exp-ma-morphology-negatives-20260922-v3\complete.log" 2>&1
set FABLE_TRAIN_RC=%ERRORLEVEL%
>> "C:\fable\experiments\active\exp-ma-morphology-negatives-20260922-v3\complete.log" echo [launcher] exit_code=%FABLE_TRAIN_RC% %DATE% %TIME%
> "C:\fable\experiments\active\exp-ma-morphology-negatives-20260922-v3\wmi_exit_code.txt" echo %FABLE_TRAIN_RC%
exit /b %FABLE_TRAIN_RC%

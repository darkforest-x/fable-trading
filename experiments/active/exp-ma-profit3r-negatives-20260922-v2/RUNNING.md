# Negative redo: continuation entry

## STOPPED — label semantics rejected by Owner, 2026-09-22

This supersedes the continuation instructions below. Windows training was stopped after A completed 8 epochs and before B started. PID5360 identity was revalidated; taskkill /T returned 0 and no matching job processes remained. See label_semantics_stop.json and analysis/p1_ma_profit3r_negative_semantics_correction_20260922.md. Do not resume this recipe, collect it as completed, or call its empty-label candidates morphology negatives. Preserve all files. Local monitoring stays paused. Historical notes below describe the state before this correction.

This is a pointer to evidence, not proof that a process is still alive. Recheck current state before acting.

- Owner requested monitoring and automatic follow-ups paused on 2026-09-22; Windows training/evaluation continues independently. Local collection is deferred until resumed. Goal is not complete; do not complete until both40-epoch arms, evaluation, local collection and final report are verified. No promotion/deployment.
- Dataset: datasets/ma_profit3r_owner1500_neg_v5. Build and local/3060 preflight passed. Actual A9330=1506positive+7824negative; B18660=3012positive+15648negative. Shared val1305=175+1130; test989=160+829.
- Remote: Administrator@192.168.1.2, C:/fable/runs/ma_profit3r_owner1500_neg_v5. Frozen job handles A, then B, then both external evaluations.
- Actual successful dispatch: recovery_launch.json, WMI supervisorPID5360. First detachedPID6448 exited without job state; remote_launch.json explicitly records failure. WMI tool response had a local encoding error, but remote job was verified live; NEVER retry that dispatch.
- WMI command and Python job survive SSH closure. Model/recipe/input SHA remain frozen. Do not edit any launch_contract.files while work is running.
- Local watcher PID34284 was stopped at Owner request; task Mac awake guard PID32830 was also stopped. Do not restart monitoring unless Owner requests it.
- Latest recorded progress: A completed2/40; B not started. One A epoch about7.5minutes; two arms likely take many hours. Do not call initial metrics final results.

## Read-only status

```bash
.venv/bin/python -m scripts.research.run_ma_profit_negative_redo snapshot
tail -3 experiments/active/exp-ma-profit3r-negatives-20260922-v2/watch.log
```

Snapshot receipts alone do not prove liveness; inspect the named Windows process or fresh log/CSV progression. SSH observation failures are not proof of training failure; inspect before any retry. Monitoring is paused at Owner request: do not poll or restart the watcher automatically.

## After completion

1. Verify Windows job_receipt/training_receipt completed and A/B CSV contain exactlyepochs1..40, best/last hashes and proper3060 environment. After Owner resumes, use the driver's collect command to download completed Windows results and evaluate frozen random controls; do not use start.
2. Run `.venv/bin/python -m yoyo.evaluation.ma_profit_negative_delivery`; inspect actual resulting metrics and receipts, not only the command exit code. Frozen delivery module validates evaluator/metric SHA and exact per-arm controls inputs.
3. Add recovery_launch/launch_wmi/first_training_observation evidence to a supplementary execution manifest or final report; frozen delivery automatically includes original dispatch record only, which honestly says exited_before_job_started.
4. Final report: analysis/p1_ma_profit3r_negatives_redo_20260922.md. Compare same previous-v4 A/B, quality_score and matched random controls; include negative failure outcomes. CSV highestmAP row is not proven checkpoint epoch. Candidate direction is supplied by rules; score does not use future profit or GT IoU.
5. Update experiment/artifact registry and HANDOFF without staging other tasks' dirty changes. Global relevant gates were468passed/8existingfailures; targeted original46passed plus self-contained binding test passed. Do not claim fullrepo green.
6. Update existing Notion record from notion_sync.json (3e388564-79af-81ba-b30a-da0a6daed56d), do not duplicate. Final conclusions remain non-production.
7. Stop only this task's awake guard after all transfers and delivery finish. Goal complete only after requirement-by-requirement evidence review.

Manual221 source images are in read-only /Users/zhangzc/yoyo-trading/datasets/dataset_v3_2_reviewed_core_v1, not fable's manifest-relative path. Its28val images(8positive/20negative) are morphology labels, not this profitability validation.28flipped-negative manifest label hashes are stale; images match. They are used only for exclusion protection here.

# Next Iteration Packet

model: deep

## Todo: Todo 5 — P2.5 local harden（继续执行）

Three atomic tasks landed this batch:

| # | Result | Evidence / commit |
|---|--------|-------------------|
| Phase C browser QA | PASS | `.omo/evidence/task-4a-phase-c-browser-qa.md` `693dc5f` |
| OSS tool benchmark | PASS | `.omo/evidence/task-oss-label-tool-benchmark.md` `63714f8` |
| Writeback design | PASS | `.omo/evidence/task-label-writeback-design.md` `220143a` |
| Full-80 export baseline | PASS | `.omo/evidence/task-full80-writeback-baseline.md` `49596be` |

### Owner review gate（阻塞，需 owner）

- URL: `https://103.214.174.58:8081`（自签）用于 `dense_15m_val_audit`；80 任务中当前仅 53 条预标注且 `completed_by=53` 条均为 `0`，仍无人工确认（owner-review 阶段）。

### Current gating state

- 2026-07-10 snapshot: 80 tasks present, 53 prelabel payloads, **no completed_by!=0 human edits yet** (`tasks_val.json`).
- Blocker: owner review required before any annotation promote.

### Next worker task now（先执行）

1. 补齐 `TODO 5 P2.5 local harden` 产物：复现 `Iteration 2` 步骤，写明 `output/label_studio/writeback_dryrun/MANIFEST.json` 与 `tasks_val.json` 的一致性（count/source_counts）证据。
2. 保留 owner 审核阻塞项不变；仅在 owner 完成复核后，才执行 `scripts/export_ls_yolo_writeback.py --limit 80 --force` 再生成新导出。
3. 若 `output/` 无新证据或校验不一致，写“修复任务”并只允许本地可回滚动作。
4. 结果更新回 `GROK_2DAY_STATUS.md`，并触发下一轮 `grok-4.5`。

### Pass criteria for that future task

- MANIFEST covers all exported stems; datasets/ git-clean; secrets absent.

### Out of scope

- Holdout, champion promotion, Telegram, job executor on, force-push, main.

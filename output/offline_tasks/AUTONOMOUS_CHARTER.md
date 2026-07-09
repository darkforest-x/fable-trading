# 多日无人值守章程（owner 2026-07-10 授权）

> 人：睡觉 / 上班 / 周末不在。  
> Agent：**不要停**；任务空了也按优先级自迭代。  
> 可问可不问：**不问**（除 holdout / 真金密钥 外一律按推荐执行）。

## 绝对红线（违反=返工）

1. 禁止评估 holdout（≥2026-05-04 窗口调参/偷看）
2. 禁止 git 写入真实 token/密码
3. 禁止 VPS 上 `ENABLE_JOB_EXECUTOR=1`（Mac 默认可开测试）
4. 禁止 YOLO flip/mosaic/mixup/hsv 方向破坏增强
5. 禁止为 mAP 放宽 IoU/conf 定义
6. 禁止替换冻结主线 TP5/SL2 除非前向终审+明确指令
7. 破坏性操作（rm -rf、force push）不做

## 价值排序（永远按这个捡活）

| 优先级 | 方向 | 具体 |
|--------|------|------|
| P0 | 前向生命线 | `forward_track` 健康；日志增长；看板前向 tab；每日 digest |
| P1 | 数据不断 | expand 完成→audit→FINAL 报告；update_okx；stale 清理 |
| P2 | 检测标签/模型 | E2.1 训练完成→mAP 报告→consistency_check；FO 难例驱动单变量 |
| P3 | 操作台 | P2.5 Phase3 数据/模型只读 hub；Phase2 runner 修 bug |
| P4 | 研究影子 | H1 scaled 影子日志实现（不换主线）；30m 线索文档化 |
| P5 | 工程卫生 | 测试、learnings、文档同步、VPS deploy、CI |

## 空闲时自迭代清单

- [ ] `pytest` 全绿
- [ ] HANDOFF / PROJECT_STATUS / NEXT_STEPS 与代码一致
- [ ] FO hard list top20 归类 → 下一单变量提案（只实现已批准族：core-trim 参数）
- [ ] `deploy_vps.sh` 在 dashboard 变更后
- [ ] `OVERNIGHT_STATUS.md` / `MULTI_DAY_STATUS.md` 更新
- [ ] 读 `docs/learnings/*` 避免重复坑

## fable 说过的关键拍板（遵守）

- 主线宇宙 **SWAP**
- 均线主线 **EMA 8-55**
- 冻结 **TP5/SL2** + maker；H1 是挑战者不是主线
- YOLO **非关键路径**；识别迭代可以做但不挡前向
- 看板访问控制曾暂不加；P2.5 ops 可 token
- 实盘目标合约；模拟盘需 demo key（无 key 只写对接骨架）

## 调度

- 每 1h：续跑检查 / 合并 / 小修复 / 前向 smoke
- 每 6h：深度任务（Phase3 hub、H1 影子实现、报告）
- 训练/拉取 screen 永不杀（除非 traceback 死透需重启）

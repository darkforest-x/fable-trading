# 两日任务最终审计（2026-07-11）

## 结论

两日执行清单的工程、检测终验、SAHI、VPS 和安全检查已经完成。系统能够更新全合约数据、
扫描 MA206 候选、用冻结 LightGBM 评分、写独立前向账本并在 VPS 只读展示；但不能宣称
已形成可盈利交易系统。

盈利证据仍未通过：MA206 历史 val maker PF `1.072` 只略高于 1，固定 `0.20%-0.30%`
成本不盈利；因果方向 YOLO 净@0.20% `-0.15236%/笔`、PF `0.7472`；q80 影子不足
24 小时且已裁决样本极少。最终裁决只能等待冻结前向样本，不得用旧 val 或短影子调参。

## 任务对账

| 项目 | 结果 | 证据 |
|---|---|---|
| MA206 全链统一 | 完成 | SMA/EMA20/60/120；ACTIVE hash 固定 |
| q80 同窗漏斗 | 运行中 | 358 SWAP；19:45 UTC 为 67→q90 10/q80 16 可执行 |
| 候选因果去重 | 完成 | 未来 bar 不再反向替换旧候选 |
| E2.1b HSV0 | 失败关闭 | mAP50 0.8505；一致率 51.27% |
| 固定 SAHI 全 val | 失败关闭 | matched 665→625；pred +69%；latency 11.27× |
| 因果方向 YOLO | 失败关闭 | accuracy 34.78%；净@0.2% -0.15236%；PF 0.7472 |
| VPS 流水线 | 通过 | dashboard active/enabled；pipeline 200；ops anonymous 401 |
| VPS executor | 关闭 | `ENABLE_JOB_EXECUTOR=0` |
| 实盘 | 未开启 | 无 live order、无真实资金执行器 |

## 质量与安全

| 检查 | 结果 |
|---|---|
| 全仓测试 | `210 passed` |
| compileall | `src scripts tests` 通过 |
| direct 口径复现 | 1,255 images / 1,297 GT / 1,629 pred / 665 matched，完全一致 |
| SAHI checkpoints | direct 1,255 + SAHI 1,255 行，完整结束 |
| 分支同步 | `origin/codex/grok-2day...HEAD = 0/0`（审计前） |
| 密钥扫描 | 未发现真实 TG bot token、API key 或 PEM 私钥；命中项仅为测试断言文本 |
| ACTIVE | `42df83c98247188873613eec3af04ffd258520a98e8b4b089c5f322b9db8b9c7` |
| q90 主账本 | `c903d37798d374bef59404adcc18c92e3024ac77ab348b1435bb760e19198527` |
| H1 账本 | `02ecccec22dceca0dd324460e6a9baa6e73997aabd22783784502a870a87af36` |

最终部署后，公网 `/api/pipeline` 已选择
`analysis/p2a_e21b_hsv0_report.md`，显示 mAP50 `0.850523`、gate=false 和 SAHI rejected；
鉴权实验索引可见 SAHI/方向经济性两份新证据。合法白名单 job 请求在 VPS 返回 `403`，
三个服务均 active，executor 仍为 0。公开 payload 的 token 状态值保持 `[redacted]`。

终检额外修复了训练配置模块的顶层重依赖：纯配置测试不再要求 torch/Ultralytics，实际
训练入口仍在执行时按需加载。轻量 CI 因此可以完整收集 210 个测试。

## 诚实边界

- E2.1b 和 SAHI 只评检测框，不决定交易方向或收益。
- 方向 YOLO 有相对 numeric baseline 的毛优势，但连 0.06% 低成本都无法覆盖。
- q80 只是 owner 批准的独立诊断，不改 ACTIVE，不写 q90/H1 账本，不可据此选阈值。
- judgment holdout 本轮未读取；历史迁移 QA 的一次意外读取已隔离，不得复用。
- 当前系统是研究与只读前向阶段，不是可承诺收益的实盘系统，更不能支持年度收益保证。

## 唯一主线

1. 保持 MA206、TP5/SL2、成本、q90 阈值和 ACTIVE 冻结。
2. q80 独立影子累计至少 24 小时后只做漏斗/早期诊断；盈利判断仍需更长前向样本。
3. q90 主账本达到预注册样本门槛后做一次终审；通过才进入模拟盘，失败则回阶段 2。
4. 不重复 E2.1b、固定 SAHI 或当前方向 YOLO 配方，不在已使用 val 上继续救数字。

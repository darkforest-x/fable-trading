# 短周期支线（1m / 5m）

## 目的

15m YOLO 主线有 **中图打标 vs tip 右缘** 结构问题 + **bar 收盘结构延迟**。  
短周期支线用 **规则密集 + tip 近端 bar**，独立日志，探索更快反馈。

## 隔离铁律

| 项目 | 短周期 | 15m 主线 |
|------|--------|----------|
| 候选 | 规则 expanded | YOLO live |
| 日志 | `data/short_tf/signal_log.csv` | `data/forward_log.csv` |
| 执行 | **默认不接** executor | live executor |
| 通知 | 需显式 `--notify` | pulse 自动（新鲜） |

## 命令

```bash
# 扫一轮（写日志，不 TG）
PYTHONPATH=. python3 scripts/short_tf_scan.py --once

# 只看结果不落盘
PYTHONPATH=. python3 scripts/short_tf_scan.py --once --dry-run

# 循环 60s
PYTHONPATH=. python3 scripts/short_tf_scan.py --loop --interval 60

# 可选：拉 5m 历史做离线对照
PYTHONPATH=. python3 scripts/short_tf_fetch.py --bar 5m --days 30
```

看板：侧栏 **短周期** → `/#shorttf` · API `GET /api/short-tf`

## 与 H7 关系

H7（主流 5m 全池 LGB）发现级曾证伪。本支线 **不做全池 YOLO/LGB 主线替换**，只做 tip 规则观察 + 可选 TG，给 owner 更快的形态反馈。

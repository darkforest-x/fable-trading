# Local Signal V2 Owner YES / NO 审核

启动：

```bash
cd /Users/zhangzc/fable-trading
PYTHONPATH=.:/Users/zhangzc/yoyo-trading .venv/bin/python scripts/serve_local_signal_v2_semantic_review.py
```

浏览器打开 `http://127.0.0.1:8766/`。Y=YES，N=NO，S=SKIP，左右方向键前后移动。
每次判断立即写入本目录的 `owner_verdicts.jsonl`；可中断后继续，也可修改上一张。
主图严格止于 decision bar，不含未来 K、收益、TP/SL、模型来源和置信度。
审核完成前不要运行任何训练；完成后只生成解盲诊断报告。

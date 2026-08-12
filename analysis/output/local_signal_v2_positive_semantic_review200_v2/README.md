# Local Signal V2 Owner YES / NO 审核

启动：

```bash
cd /Users/zhangzc/fable-trading
PYTHONPATH=.:/Users/zhangzc/yoyo-trading .venv/bin/python scripts/serve_local_signal_v2_semantic_review.py
```

浏览器打开 `http://127.0.0.1:8766/`。Y=YES，N=NO，S=SKIP，左右方向键前后移动。
每次判断立即写入本目录的 `owner_verdicts.jsonl`；可中断后继续，也可修改上一张。
左图严格止于 decision bar并使用人眼自适应纵轴；右图显示独立缩放的安全未来走势，
紫色区域不进入模型输入、训练标签或自动裁决。页面不显示模型来源、置信度和推荐答案。
最新Canary为避免读取holdout，每张只显示边界前实际可用的16–46根；Positive显示48根。
审核完成前不要运行任何训练；完成后只生成解盲诊断报告。

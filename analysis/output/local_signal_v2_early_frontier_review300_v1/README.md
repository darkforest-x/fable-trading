# Local Signal V2 · 早期启动前沿 300 张审核

本包是发现集，不是独立验证集。内部用上一轮 Canary 的 11 YES / 89 NO 做因果相似度检索，
页面不显示检索分层、模型置信度或推荐答案。

启动：

```bash
cd /Users/zhangzc/fable-trading
PYTHONPATH=.:/Users/zhangzc/yoyo-trading .venv/bin/python scripts/serve_local_signal_v2_semantic_review.py --out analysis/output/local_signal_v2_early_frontier_review300_v1 --port 8766
```

浏览器打开 `http://127.0.0.1:8766/`。Y=YES，N=NO，S=SKIP，左右键切换。
每次判断追加保存到 `owner_verdicts.jsonl`，可中断继续和修改。左图止于 decision；
右图未来48根只供人工对照，不进入检索、模型输入或训练。

# Review results

把主页面导出的 JSON 放在这里，再运行：

```bash
python3 tools/datasets/fixed_w10_p1_audit.py score --answers <导出的JSON>
```

先完成、导出并冻结主包答案，再打开 Cleanlab 28 张；后者答案单独保存，不参与主包错误率。两个队列自然重叠的项目也必须遵守这个顺序。

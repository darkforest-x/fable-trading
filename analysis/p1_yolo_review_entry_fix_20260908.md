# YOLO本周1043题审核入口已修正

2026-09-08。原项目77保留3556题，其中2513历史题、1043最新HL2事件。现在项目名为“YOLO 本周审核 · 1043题 · 未来40根”，tab44名为“本周审核 · 1043题”。默认蓝色按钮在此列表进入当前筛选，真实Chrome实测进入37277，预框和未来参考图可见。没有新增项目或改写人工答案。

3060入口：[本周1043题](http://192.168.1.4:8081/projects/77/data?tab=44)。先保留未提交修改，再回列表Ctrl+F5一次。选择“本周审核 · 1043题”，直接点击蓝色按钮逐题审核。右键提交继续有效。其他分组不在本补丁适用范围内。

## 原因与修复

以前只写“本周审核”，无法辨认1043批次；3556是项目总库存。原生Label All会把浏览器队列模式设为all，即使列表已经筛选1043也会进入旧题。项目当前选择HL2预标版本，旧未审题可能不显示历史版本的框。原框仍在；明确无目标的人工答案不能据此统一补框。

源码94fdc657a992592815c2424e3cc38ca772e09d6b先提交后安装。独立可恢复的入口脚本只在项目77/tab44列表把主按钮点击转交原生AsDisplayed。目标不唯一或不可用就停止，不进入全部任务。保留原生导航、验证和提交；不直接写答案、草稿、预测或队列存储。项目和视图只改名称，筛选与预标版本保持不变。

## 前后对照与验证

| 检查 | 原入口 | 修复后 |
| --- | --- | --- |
| 项目总题数 / 当前筛选 | 3556 / 1043 | 3556 / 1043 |
| 原预测 / 人工答案 | 3556 / 368 | 3556 / 368 |
| 默认按钮启动模式 | all | filtered |
| 真实按钮启动任务 | 35131，历史题 | 37277，HL2新题 |
| 新题预框 | 不适用 | 1个原预测；0答案、0草稿 |
| 操作影响 | 容易混入旧题 | 名称2处；答案/草稿/任务/预测写入0 |

32个Node队列测试、28个Python安装/恢复/命名/队列测试通过。否定对照覆盖其他项目和tab、编辑页、菜单弹窗、缺失/歧义目标、禁用按钮与修饰键；不应接管的点击保留原行为，目标异常的受保护入口不会回退All。

第一次真实验收失败：浏览器仍用1262058字节旧包，尽管服务器与磁盘已是1266839字节新包。普通刷新及发送强刷按键没有证明资源已更新。用Page.reload(ignoreCache=true)后页面主执行环境出现20260908-v1；实际点击记录forwardedActivations=1、模式filtered，HL2题37277的预框和后续40根可见，Undo/Redo/Reset禁用，右键提示保留。没有测试Submit或调整框。审核页“101 of 101”是原生流历史长度，不能当作批次总数。

安装与浏览器验收收据：`experiments/active/exp-yolo-dataset-consolidation-20260908-v1/results/review_entry_fix.json`。真实Windows浏览器本轮未远程点击，需由Owner在保留未提交修改后Ctrl+F5加载更新；既有Windows同源访问证据沿用此前报告。

## 复现与恢复

```bash
git branch --show-current
node --test tests/label_studio_queue_entry.test.cjs
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_label_studio_shortcut_install.py tests/test_label_studio_queue_entry_install.py tests/test_label_studio_dataset_union.py
PYTHONPATH=. .venv/bin/python -m yoyo.review.install_label_studio_queue_entry --bundle .venv_label_studio/lib/python3.9/site-packages/web/dist/apps/labelstudio/main.js --state-dir output/offline_tasks/label_studio_queue_entry_20260908 --name-entry
python3 scripts/md_to_html.py analysis/p1_yolo_review_entry_fix_20260908.md --out-dir analysis/html
```

源码必须与main中提交一致；安装器校验3556任务和原预测、1043过滤身份。已有安装重复运行不重复追加。恢复命令用同一入口安装器加`--restore`，不加`--name-entry`；仅恢复队列脚本，名称不回退。若需恢复右键脚本，必须先恢复队列脚本，按安装逆序处理。原始包、哈希和事务收据留在各自state-dir，未知漂移拒绝覆盖。

## 风险与诚实声明

这是界面入口修复，没有模型实验；val AUC、置换收益检验、top-decile收益、胜率、金融随机对照和新val样本数不适用，未计算。替代验证是确定性作用域否定对照和真实原生按钮前后对照。原数据时间与split继承既有导入审计，本轮没有新行情/holdout读取、模型训练或资格变更。1043个框均为自动原训练proposal，不等于人工金标或1043个确认正例。1042题有未来40根，1题到保留集边界仅13根；未来图继续独立于训练主图。

## 下一步

Owner继续当前1043题，调整预框或选择无目标/拿不准。旧2513保留原答案，Codex继续既定形态预筛与冲突整理；不要求重审整池。本修复不解除P0/P1训练门。

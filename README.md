# fable-trading

K 线双均线密集"启动初期"信号的量化验证与建模项目。

## 项目定位

替代旧的 YOLO 视觉检测方案（`/Users/zhangzc/Documents/Codex/2026-06-17/yolo-yolo-okx-20-k`，只读参考）。
旧方案迭代 180+ 版本后确认失败，根因诊断：

1. 所有正样本标注框坐标固定在图像右缘，任务本质是"右缘是否为启动初期"的**二分类**，不是目标检测；
2. v176–v181 误开 fliplr/mosaic/hsv 增强，破坏时间方向与红绿颜色语义；
3. 正样本仅 79–271 个，验证集指标全是噪声；2911 组回测参数搜索是过拟合发生器；
4. ETH 近一年回测 671 笔，收益 -26.3%，胜率 34.9%，PF 0.47。

本项目从头验证：**信号定义本身是否含 alpha**（P0），有 alpha 才进入特征工程与建模。

## 目录说明

```
fable-trading/
├── README.md            # 本文件
├── PROJECT_PLAN.md      # 三阶段路线图（目标 + 验收标准）
├── requirements.txt     # Python 依赖
├── analysis/            # P0 alpha 检验脚本与产出
│   ├── p0_alpha_check.py    # 分析脚本（可复现）
│   ├── p0_alpha_report.md   # P0 分析报告（核心交付物）
│   └── output/              # 统计表 CSV 与分布对比图 PNG
├── src/                 # 预留：阶段 2 特征与模型代码（当前为空）
└── data/                # 软链接/小型提取数据（已 gitignore，不复制大文件）
```

## 如何运行

```bash
# 依赖（系统 python3 已有 pandas/scipy/matplotlib 则可跳过）
pip3 install -r requirements.txt

# P0 分析：读取旧项目各版本 metadata.csv，输出统计表 + 图到 analysis/output/
python3 analysis/p0_alpha_check.py
```

脚本只读旧项目文件，不做任何修改；所有产出写入本仓库 `analysis/output/`。

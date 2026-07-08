#!/bin/bash
# 双击运行：启动 fable-trading 看板（总览 / 回测 / 信号浏览）
cd "$(dirname "$0")"
echo "启动看板... 浏览器将自动打开 http://127.0.0.1:8642"
echo "（首次启动需 ~10 秒训练模型建立信号分数缓存；关闭本窗口即停止服务）"
(sleep 2 && open "http://127.0.0.1:8642") &
python3 -m uvicorn src.webapp.server:app --host 127.0.0.1 --port 8642

#!/bin/bash
# 双击运行：拉取 OKX 15m 历史 K 线（约 45-60 分钟，可随时关闭窗口、再次双击会续传）
cd "$(dirname "$0")"
echo "开始拉取 OKX 数据（约 55 个币种 x 400 天，45-60 分钟）..."
echo "中断后重新双击本文件即可断点续传。"
echo
caffeinate -i python3 -m src.data.fetch_okx
echo
echo "全部完成。可以回到 Claude 告诉它数据拉好了。"
read -p "按回车关闭窗口"

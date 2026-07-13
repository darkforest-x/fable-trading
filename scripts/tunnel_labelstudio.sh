#!/bin/bash
# 反向隧道：把本机 Label Studio(8081) 暴露到 VPS 公网 18081（断线自动重连）
# 启动: nohup bash scripts/tunnel_labelstudio.sh & | 停止: pkill -f "tunnel_labelstudio"
while true; do
  ssh -N -R 0.0.0.0:18081:localhost:8081 \
      -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
      -o ExitOnForwardFailure=yes root@103.214.174.58
  sleep 10
done

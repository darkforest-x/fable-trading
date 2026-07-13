#!/bin/bash
# 反向隧道：本机 Label Studio(8081) -> VPS 公网 80 和 18081（双端口，断线自动重连）
while true; do
  ssh -N -R 0.0.0.0:80:localhost:8081 -R 0.0.0.0:18081:localhost:8081 \
      -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
      -o ExitOnForwardFailure=yes root@103.214.174.58
  sleep 10
done

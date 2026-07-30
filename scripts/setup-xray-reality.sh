#!/bin/bash
set -e
echo "=== Xray Reality Setup (206.237.14.112) ==="
if [ "$(id -u)" != "0" ]; then echo "Run as root"; exit 1; fi
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl wget unzip uuid-runtime openssl ca-certificates
bash <(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh) install
UUID=$(cat /proc/sys/kernel/random/uuid)
KEYS=$(/usr/local/bin/xray x25519)
PRIV=$(echo "$KEYS" | grep "Private key:" | awk '{print $3}')
PUB=$(echo "$KEYS" | grep "Public key:" | awk '{print $3}')
SID=$(openssl rand -hex 8)
SNI="www.microsoft.com"
cat > /usr/local/etc/xray/config.json << CONFIG
{
  "log": {"loglevel": "warning"},
  "inbounds": [{
    "port": 443,
    "protocol": "vless",
    "settings": {
      "clients": [{"id": "$UUID", "flow": "xtls-rprx-vision", "level": 0}],
      "decryption": "none"
    },
    "streamSettings": {
      "network": "tcp",
      "security": "reality",
      "realitySettings": {
        "show": false,
        "dest": "$SNI:443",
        "xver": 0,
        "serverNames": ["$SNI"],
        "privateKey": "$PRIV",
        "shortIds": ["$SID"]
      }
    },
    "sniffing": {"enabled": true, "destOverride": ["http","tls","quic"]}
  }],
  "outbounds": [
    {"protocol": "freedom", "tag": "direct"},
    {"protocol": "blackhole", "tag": "block"}
  ],
  "routing": {"rules": [{"type": "field", "ip": ["geoip:private"], "outboundTag": "block"}]}
}
CONFIG
ufw allow 443/tcp 2>/dev/null || true
ufw allow 443/udp 2>/dev/null || true
ufw --force enable 2>/dev/null || true
systemctl enable xray
systemctl restart xray
sleep 3
if systemctl is-active --quiet xray; then
  echo ""
  echo "========== SETUP COMPLETE =========="
  echo "IP: 206.237.14.112"
  echo "Port: 443"
  echo "UUID: $UUID"
  echo "PublicKey: $PUB"
  echo "ShortId: $SID"
  echo "SNI: $SNI"
  echo "Flow: xtls-rprx-vision"
  echo "Fingerprint: chrome"
  echo "UDP: enabled"
  echo "===================================="
else
  echo "Failed to start"
  journalctl -u xray -n 20 --no-pager
fi

#!/usr/bin/env bash
# KBÜ MCP sunucu kurulumu (Ubuntu 22.04/24.04, Oracle Cloud Always Free ARM veya x86).
#   bash setup.sh                      # alan adı yoksa <ip>.sslip.io kullanılır
#   DOMAIN=mcp.ornek.com bash setup.sh # kendi alan adınız (A kaydı bu sunucuyu göstermeli)
# İsteğe bağlı: ~/kbu.db.gz varsa indeks oradan kurulur (yoksa ilk tarama 1-2 saat sürer).
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Docker kuruluyor"
if ! command -v docker >/dev/null; then
  sudo apt-get update -y
  sudo apt-get install -y docker.io docker-compose-v2 || sudo apt-get install -y docker.io docker-compose-plugin
  sudo systemctl enable --now docker
fi
sudo usermod -aG docker "$USER" || true

echo "==> Güvenlik duvarı: 80 ve 443 açılıyor (Oracle Ubuntu imajları iptables ile kapatır)"
for p in 80 443; do
  sudo iptables -C INPUT -p tcp -m state --state NEW --dport "$p" -j ACCEPT 2>/dev/null \
    || sudo iptables -I INPUT 1 -p tcp -m state --state NEW --dport "$p" -j ACCEPT
done
if command -v netfilter-persistent >/dev/null; then sudo netfilter-persistent save; fi

IP=$(curl -fsS https://api.ipify.org || curl -fsS https://ifconfig.me)
DOMAIN=${DOMAIN:-$(echo "$IP" | tr . -).sslip.io}
KBU_VERSION=${KBU_VERSION:-main}
printf 'DOMAIN=%s\nKBU_VERSION=%s\n' "$DOMAIN" "$KBU_VERSION" > .env
echo "==> Adres: https://$DOMAIN/mcp (sürüm: $KBU_VERSION)"

if [ -f "$HOME/kbu.db.gz" ] && ! sudo docker volume inspect kbu-mcp_kbu-data >/dev/null 2>&1; then
  echo "==> İndeks anlık görüntüsü kuruluyor"
  sudo docker volume create kbu-mcp_kbu-data >/dev/null
  sudo docker run --rm -v kbu-mcp_kbu-data:/data -v "$HOME/kbu.db.gz":/s.gz:ro busybox \
    sh -c 'gunzip -c /s.gz > /data/kbu.db && chown -R 1000:1000 /data'
fi

echo "==> İmaj GitHub'dan derleniyor (ilk derleme birkaç dakika sürer)"
sudo docker build -t "kbu-mcp:$KBU_VERSION" "https://github.com/1ffatihgur/kbu-mcp.git#$KBU_VERSION"
echo "==> Kapsayıcılar başlatılıyor"
sudo docker compose up -d

echo "==> Sunucu bekleniyor"
for i in $(seq 1 60); do
  if curl -fsS "https://$DOMAIN/health" >/dev/null 2>&1; then break; fi
  sleep 5
done
curl -fsS "https://$DOMAIN/health" && echo
echo
echo "Tamam. Remote MCP adresi: https://$DOMAIN/mcp"
echo "Tanıtım sayfası:          https://$DOMAIN/"

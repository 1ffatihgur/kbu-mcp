---
title: KBÜ MCP
emoji: 🎓
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Karabük Üniversitesi için uzak MCP sunucusu
---

# KBÜ MCP: Karabük Üniversitesi Remote MCP

Karabük Üniversitesi web sitesindeki (`*.karabuk.edu.tr`) sayfalar, PDF/Office belgeleri, telefon rehberi,
AKTS ders planları, yemek listesi ve akademik takvim için uzak MCP sunucusu.

**MCP adresi:** `https://<bu-space>.hf.space/mcp`

Claude Desktop / claude.ai: **Settings → Connectors → Add custom connector** → URL olarak yukarıdaki adresi girin.

Kaynak kod, yerel kurulum ve ayrıntılar: https://github.com/1ffatihgur/kbu-mcp

### Space ayarları (Settings → Variables and secrets)

| Ad | Tür | Açıklama |
|---|---|---|
| `KBU_SNAPSHOT_REPO` | Variable | İndeks anlık görüntüsünün tutulduğu özel HF dataset (ör. `kullanici/kbu-mcp-index`) |
| `HF_TOKEN` | Secret | O dataset'i okuyup yazabilen token (günlük güncellemeden sonra yeni anlık görüntü yüklenir) |

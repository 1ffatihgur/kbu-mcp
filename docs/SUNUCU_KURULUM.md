# KBÜ MCP — Üniversite Sunucusuna Kurulum Rehberi (Bilgi İşlem için)

Bu belge, sistemi dışarıya kapalı üniversite ağındaki bir sunucuda çalıştırmak için gereken her şeyi adım adım
anlatır. Sunucuya internetten paket / model indirmek gerekmez; her şey hazır paket olarak taşınır.

## 1. Sunucudan beklenenler

| Kaynak | Öneri | Not |
|---|---|---|
| İşletim sistemi | Linux (Ubuntu 22.04+ / Debian 12 / RHEL 9) | Docker çalıştırabilen herhangi bir dağıtım |
| Docker | Docker Engine 24+ ve Compose v2 | `docker compose version` |
| CPU / RAM | 4 çekirdek, **8 GB RAM** (16 GB rahat) | PostgreSQL + vektör indeksi ~3 GB bellek kullanır |
| Disk | **40 GB** boş alan | Veritabanı ~6 GB, büyüme payı dahil |
| Ağ (giden) | `*.karabuk.edu.tr` (80/443) | Tarama için. Yurt dışı erişim GEREKMEZ |
| Ağ (gelen) | 7860/tcp (veya ters vekil üzerinden 443) | Uzak MCP adresi: `https://<alan-adı>/mcp` |
| Embedding servisi | Kurumun LLM sunucusunda OpenAI uyumlu `/v1/embeddings` | Yoksa aynı sunucuda Ollama + `bge-m3` (bkz. 5) |

## 2. Paketin taşınması (internet erişimi olan bir makinede hazırlanır)

```bash
git clone https://github.com/1ffatihgur/kbu-mcp.git && cd kbu-mcp
docker compose build
docker pull pgvector/pgvector:pg17
docker save kbu-mcp:2 pgvector/pgvector:pg17 | gzip > kbu-mcp-images.tar.gz        # ~0,4 GB
# Hazır indeksle başlamak için (tam taramayı beklemeden):
docker compose exec db pg_dump -U kbu -Fc kbu > kbu-index.dump                       # ~1,8 GB
```

Sunucuya kopyalanacaklar: depo klasörü (`kbu-mcp/`), `kbu-mcp-images.tar.gz`, isteğe bağlı `kbu-index.dump`.

## 3. Kurulum

```bash
cd kbu-mcp
docker load < kbu-mcp-images.tar.gz
cp .env.example .env
nano .env        # aşağıdaki tabloya göre doldurun
docker compose up -d db
# (isteğe bağlı) hazır indeksi yükle:
docker compose exec -T db pg_restore -U kbu -d kbu --no-owner < kbu-index.dump
docker compose up -d
docker compose ps        # db (healthy), mcp (healthy), worker (running)
curl http://127.0.0.1:7860/health
```

`.env` ayarları:

| Değişken | Ne yazılacak |
|---|---|
| `POSTGRES_PASSWORD` | Güçlü bir parola |
| `KBU_API_KEYS` | Virgülle ayrılmış erişim anahtarları, ör. `openssl rand -hex 24` çıktıları (kişi/uygulama başına bir anahtar) |
| `KBU_EMBED_URL` | Embedding servisi, ör. `http://llm.karabuk.local:8000/v1` |
| `KBU_EMBED_MODEL` | Servisteki model adı (`bge-m3`). **Hazır indeks bge-m3 ile üretildi; farklı model seçilirse `kbu embed` ile tüm vektörler yeniden üretilmelidir** |
| `KBU_EMBED_API_KEY` | Servis anahtar istiyorsa |
| `KBU_CRAWL_MAX_PER_HOUR` | Sitelere toplam saatlik istek sınırı, ör. `1000` (sunucular yoğun istekte engelliyor) |

## 4. İlk tarama (önce küçük test, sonra tam tarama)

```bash
docker compose exec worker kbu sample                 # 5 site sayfası + 10 UNIS hocası + 10 KBÜ Medya haberi
docker compose exec worker cat /data/sample/RAPOR.md  # doğruluk raporu → onay
docker compose exec worker kbu jobs add site --all    # her site ayrı iş (sırayla)
docker compose exec worker kbu jobs add unis
docker compose exec worker kbu jobs add medya
docker compose exec worker kbu jobs add ocr
docker compose exec worker kbu jobs add embed
docker compose exec worker kbu jobs list              # ilerleme / hata nedenleri
docker compose exec worker kbu errors                 # taranamayan adresler (site başına)
docker compose logs -f worker                          # canlı log
```

Tam tarama `KBU_CRAWL_MAX_PER_HOUR=1000` ile bir gece sürer. İş durdurulursa (yeniden başlatma, elektrik kesintisi)
`worker` açıldığında kaldığı yerden devam eder. Sonrasında güncellemeler kendiliğinden çalışır: saatlik (duyuru ve
ana sayfalar) ve günlük (tam yenileme + rehber + OCR + embedding); UNIS 3 günde bir.

## 5. Embedding servisi yoksa (aynı sunucuda Ollama)

Model dosyası internetli makinede indirilip taşınır:

```bash
# internetli makinede
docker run -d --name ollama -v ollama:/root/.ollama ollama/ollama && docker exec ollama ollama pull bge-m3
docker run --rm -v ollama:/v -v $PWD:/out alpine tar czf /out/ollama-bge-m3.tgz -C /v .
docker save ollama/ollama | gzip > ollama-image.tar.gz
# sunucuda
docker load < ollama-image.tar.gz
docker volume create ollama && docker run --rm -v ollama:/v -v $PWD:/in alpine tar xzf /in/ollama-bge-m3.tgz -C /v
docker run -d --name ollama --restart unless-stopped -v ollama:/root/.ollama -p 127.0.0.1:11434:11434 ollama/ollama
# .env: KBU_EMBED_URL=http://host.docker.internal:11434/v1  (compose bu adı sunucuya yönlendirir)
```

Embedding servisi hiç yoksa sistem yalnızca anahtar kelime aramasıyla çalışır (doğruluk %96 yerine %93).

## 6. Dışarıya açma ve istemciler

Ters vekil (nginx / Caddy) ile `https://kbu-mcp.karabuk.edu.tr` → `http://127.0.0.1:7860`. Örnek nginx:

```nginx
location / { proxy_pass http://127.0.0.1:7860; proxy_set_header Host $host;
             proxy_set_header X-Forwarded-For $remote_addr; proxy_set_header X-Forwarded-Proto https;
             proxy_buffering off; proxy_read_timeout 300s; }
```

| İstemci | Bağlantı |
|---|---|
| Kurumun sohbet arayüzü (Qwen) | MCP istemcisiyle `https://.../mcp`, başlık `Authorization: Bearer <anahtar>`; sistem promptu: MCP `prompts/get kbu_asistan` |
| Claude Code | `claude mcp add --transport http karabuk-universitesi https://.../mcp --header "Authorization: Bearer <anahtar>"` |
| Claude Desktop / claude.ai | Connectors → Add custom connector → `https://.../mcp?key=<anahtar>` |

## 7. Bakım

| İş | Komut |
|---|---|
| Durum | `docker compose exec worker kbu status` · `kbu stats` · `kbu jobs list` |
| Yedek | `docker compose exec db pg_dump -U kbu -Fc kbu > yedek_$(date +%F).dump` (cron ile günlük) |
| Geri yükleme | `docker compose exec -T db pg_restore -U kbu -d kbu --clean --no-owner < yedek.dump` |
| Güncelleme | yeni imajı `docker load` → `docker compose up -d` |
| Bir siteyi yeniden tarat | `docker compose exec worker kbu jobs add site https://sks.karabuk.edu.tr/` |

Kullanıcı soruları veritabanında tutulmaz. Uzak MCP yalnızca araç çağrılarını isteğe bağlı olarak
`/data/queries.jsonl` dosyasına yazar (`KBU_QUERY_LOG=0` ile tamamen kapatılır).

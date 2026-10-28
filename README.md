# KBÜ MCP: Karabük Üniversitesi Arama Motoru ve MCP Sunucusu

[![Sürüm](https://img.shields.io/github/v/release/1ffatihgur/kbu-mcp?label=s%C3%BCr%C3%BCm)](https://github.com/1ffatihgur/kbu-mcp/releases)
[![Lisans: MIT](https://img.shields.io/badge/lisans-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

Karabük Üniversitesi'nin **bütün web varlığını** (`*.karabuk.edu.tr`: 140+ site, sayfalar, binlerce PDF / Word /
Excel / PowerPoint belgesi, UNIS akademisyen profilleri, KBÜ Medya haberleri) tarayıp indeksleyen bir
**hibrit arama motoru** ve onu yapay zekâ uygulamalarına açan bir
[Model Context Protocol (MCP)](https://modelcontextprotocol.io) sunucusu. Claude, Codex, Cursor veya kurumun
kendi LLM'iyle (ör. Qwen) çalışan bir sohbet arayüzü; üniversiteyle ilgili soruları **yalnızca üniversitenin kendi
kaynaklarından**, kaynak bağlantısı ve tarih vererek cevaplar.

> ⚠️ Karabük Üniversitesi'nin resmi ürünü değildir. Yalnızca herkese açık sayfaları okur; giriş gerektiren
> sistemlere (OBS, EBYS, e-posta vb.) erişmez.

```
Kullanıcı: Yemekhane ücretleri ne kadar?
Asistan:   SKS'nin "Yemekhane Fiyat Listesi"ne (25.06.2026) göre personel öğle yemeği ücreti ek göstergeye göre
           82,56 TL ile 105,68 TL arasında; 4/D sürekli işçiler için 165,12 TL ...
           Kaynaklar: sks.karabuk.edu.tr/yuklenen/dosyalar/12625062026154907.pdf (taranmış belge, OCR ile okundu)
           Şunları da sorabilirsiniz: Yemekhane hangi saatlerde açık? · Bu haftaki yemek menüsü ne?
```

---

## 🎯 Neler var?

| | |
|---|---|
| **Hibrit arama** | Anahtar kelime (PostgreSQL tam metin, Türkçe kök/ek işleme, BM25F) **+** anlamsal arama (pgvector, `bge-m3` embedding) → Reciprocal Rank Fusion. Sonuçlar ilgililiğe ve **tarihe** göre sıralanır (yeni belge önde). |
| **Temiz veri** | Her şey **Markdown**'a çevrilir (başlık, liste, tablo); site şablonlarının menü / üst bilgi / alt bilgi kısımları atılır. Taranmış PDF'ler **OCR** ile önceden metne çevrilir; sorgu anında beklenmez. |
| **Kaynaklar** | Birim siteleri ve dosyaları · **UNIS** (her akademisyen için profil + idari görevler + tüm yayın, proje, tez, ödül) · **KBÜ Medya** (son 2 yıl) · OBS Bologna (ders planı, AKTS) · canlı telefon rehberi, yemek listesi, akademik takvim. Her sonuç `kaynak` etiketiyle gelir; filtrelenebilir. |
| **Kişi grafiği** | `academic_profile`: bir hocanın UNIS profili, rehber kaydı ve adının geçtiği en yeni site/medya belgeleri tek çağrıda. |
| **Üretim altyapısı** | PostgreSQL + pgvector, Docker Compose, kalıcı **iş kuyruğu** (kesilirse kaldığı yerden), saatlik istek sınırı, hata logları, **API anahtarıyla kimlik doğrulama**. |
| **Doğruluk kuralları** | Model yalnızca araç çıktısına dayanır, bulamadığını uydurmaz (başka üniversitenin bilgisini vermez), kaynak + tarih + "şunları da sorabilirsiniz" önerileriyle cevaplar, KBÜ dışı sorulara girmez. |

---

## 🏗️ Mimari

```
 *.karabuk.edu.tr ─┐
 UNIS (DataTables) ├─► worker: iş kuyruğu ─► çıkarıcılar ─────► PostgreSQL ◄──── mcp: uzak MCP sunucusu ◄── Claude / Codex /
 KBÜ Medya         │   (hız sınırı,          (Markdown, OCR,     docs, chunks      (Streamable HTTP,        kurum sohbet arayüzü
 rehber, takvim ───┘    kaldığı yerden)       UNIS profilleri)    tsvector + vektör  API anahtarı, 10 araç)
                                                    │                   ▲
                                                    └─► embedding servisi (Ollama / vLLM / kurum LLM sunucusu, OpenAI uyumlu)
```

---

## 🚀 Sunucu Kurulumu (üretim, Docker)

Gerekenler: Docker + Docker Compose, bir embedding servisi (OpenAI uyumlu `/v1/embeddings`; ör. Ollama'da
`ollama pull bge-m3`, ya da kurumun LLM sunucusunun embedding uç noktası).

```bash
git clone https://github.com/1ffatihgur/kbu-mcp.git && cd kbu-mcp
cp .env.example .env        # POSTGRES_PASSWORD, KBU_API_KEYS, KBU_EMBED_URL, KBU_CRAWL_MAX_PER_HOUR
docker compose up -d --build
```

Üç servis kalkar: `db` (PostgreSQL 17 + pgvector), `mcp` (`:7860/mcp`), `worker` (kuyruk + zamanlanmış
güncellemeler). İlk açılışta indeks boşsa worker tam taramayı kendiliğinden başlatır.

**Dışarıdan model/paket indirilemeyen ağlarda:** imajları internet erişimi olan bir makinede hazırlayıp taşıyın:

```bash
docker compose build && docker pull pgvector/pgvector:pg17
docker save kbu-mcp:2 pgvector/pgvector:pg17 | gzip > kbu-mcp-images.tar.gz
# sunucuda:
docker load < kbu-mcp-images.tar.gz && docker compose up -d
```

Mevcut bir indeksi taşımak için: `pg_dump -Fc` → `pg_restore` (veya v1 SQLite dosyasından `kbu migrate-sqlite`).

### Önce küçük ölçekli test, sonra tam tarama

```bash
docker compose exec worker kbu sample                  # 5 site sayfası + 10 UNIS hocası + 10 KBÜ Medya haberi
#   → /data/sample/*.md (indekse girecek Markdown) ve RAPOR.md (başlık, tarih, uzunluk, menü/alt bilgi kalıntısı)
docker compose exec worker kbu jobs add site --all     # her site ayrı iş, sırayla taranır
docker compose exec worker kbu jobs add unis           # UNIS akademisyen profilleri
docker compose exec worker kbu jobs add medya          # KBÜ Medya (son 2 yıl)
docker compose exec worker kbu jobs list               # durum ve hata nedenleri
```

### İstemcileri bağlama

| İstemci | Nasıl |
|---|---|
| **Claude Code** | `claude mcp add --scope user --transport http karabuk-universitesi https://SUNUCU/mcp --header "Authorization: Bearer ANAHTAR"` |
| **Codex** | `codex mcp add karabuk-universitesi --url https://SUNUCU/mcp --bearer-token-env-var KBU_API_KEY` |
| **Claude Desktop / claude.ai** | Settings → Connectors → Add custom connector → URL: `https://SUNUCU/mcp?key=ANAHTAR` |
| **Kurumun sohbet arayüzü** (Qwen vb.) | Herhangi bir MCP istemci kütüphanesiyle aynı adrese bağlanın; sistem promptu olarak sunucunun `kbu_asistan` prompt'unu (MCP `prompts/get`) kullanın. |

Sunucunun MCP talimatları ve araç açıklamaları, modelin KBÜ ile ilgili her soruda araçları **kendiliğinden**
kullanmasını sağlar ("MCP kullan" demeye gerek yoktur).

---

## 🛠️ MCP Araçları

| Araç | Ne yapar |
|---|---|
| **`search`** | Hibrit arama. Parametreler: `query`, `kaynak` (`site` · `unis` · `medya` · `bologna`), `unit` (birim), `file_type` (`pdf`, `word`, `excel`…), `since`, `limit`. Eşleşen rehber kayıtlarını da ekler. |
| **`read_document`** | Belgenin tamamı (PDF'te sayfa numaralı Markdown). İndeksteki kopya 24 saatten eskiyse canlı siteden okur. |
| **`academic_profile`** | Bir hoca hakkında her şey: UNIS profili (unvan, birim, idari görevler, tüm yayın/proje/tez), rehber, adının geçtiği en yeni belgeler. |
| **`phone_directory`** | Resmi telefon rehberi (canlı). |
| **`food_menu`** · **`academic_calendar`** · **`latest_announcements`** | Yemek listesi (gün gün), akademik takvim, birim duyuruları (canlı). |
| **`list_units`** · **`fetch_page`** · **`index_status`** | Birimler, herhangi bir KBÜ sayfasını canlı okuma, indeks durumu. |
| prompt **`kbu_asistan`** | Kapsam, doğruluk ve cevap biçimi kurallarını içeren sistem promptu. |

---

## 🧰 Komut Satırı

| Komut | Açıklama |
|---|---|
| `kbu worker` | Kuyruk işçisi: işleri sırayla çalıştırır, boştayken saatlik/günlük güncellemeleri sıraya koyar |
| `kbu jobs add <tür> [url…]` · `list` · `retry` | İş kuyruğu. Türler: `site` (`--all`), `unis`, `medya`, `update`, `update_hot`, `people`, `ocr`, `embed` |
| `kbu errors [--host H]` | Taranamayan adresler ve nedenleri (site başına) |
| `kbu sample [--url …]` | Küçük ölçekli parse testi → `data/sample/` |
| `kbu search "…" [--source unis] [--keyword-only]` | Deneme araması |
| `kbu update [--hot]` · `kbu unis` · `kbu embed` · `kbu ocr` · `kbu people` | Tek seferlik işler |
| `kbu stats` · `kbu status` · `kbu queries` | İstatistik, durum, uzak sorgu logu |
| `kbu migrate-sqlite [kbu.db]` | v1 SQLite indeksini PostgreSQL'e taşı |
| `kbu serve [--http --port 7860]` | MCP sunucusu (stdio veya uzak) |

### ⚙️ Ortam değişkenleri (`.env`)

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `KBU_DATABASE_URL` | `postgresql://kbu:…@127.0.0.1:5432/kbu` | PostgreSQL bağlantısı (compose'da otomatik) |
| `KBU_API_KEYS` | — | Uzak MCP anahtarları (virgülle). Boşsa kimlik doğrulama kapalı |
| `KBU_EMBED_URL` / `KBU_EMBED_MODEL` | `http://127.0.0.1:11434/v1` / `bge-m3` | OpenAI uyumlu embedding servisi; yoksa arama anahtar kelimeyle sürer |
| `KBU_EMBED_DIM` | `1024` | Vektör boyutu (model değişirse) |
| `KBU_CRAWL_MAX_PER_HOUR` | `0` (sınırsız) | Tüm sitelere toplam saatlik istek sınırı (ör. 1000) |
| `KBU_MEDYA_MAX_AGE_DAYS` | `730` | KBÜ Medya'da geriye dönük gün sınırı |
| `KBU_MAX_FILE_MB` / `KBU_MAX_OCR_PAGES` | `300` / `300` | Büyük dosya sınırı / OCR sayfa tavanı |
| `KBU_RATE_LIMIT_PER_MIN` | `120` | Uzak MCP'de IP başına dakikalık istek |
| `KBU_QUERY_LOG` | `1` | `0`: uzak araç çağrılarını `data/queries.jsonl`'e yazma (veritabanında tutulmaz) |

---

## 👩‍💻 Geliştirici Kurulumu (yerel)

```bash
git clone https://github.com/1ffatihgur/kbu-mcp.git && cd kbu-mcp
cp .env.example .env                 # yerelde: POSTGRES_PORT=5433, KBU_EMBED_URL=http://127.0.0.1:11434/v1
docker compose up -d db              # yalnızca veritabanı
ollama pull bge-m3                   # anlamsal arama için (isteğe bağlı)
python -m venv .venv && .venv/Scripts/activate    # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
kbu update                           # ilk tarama (saatler sürer) — ya da: kbu migrate-sqlite data/kbu.db
pytest && python eval/run_eval.py    # birim testleri + arama kalitesi (altın soru seti)
```

Depo kopyasındaki `.env` otomatik okunur. Yerel MCP (stdio) için istemcide `command` olarak `.venv` içindeki
Python'u, `args` olarak `["-m", "kbu_mcp.server"]` verin. Kendi bilgisayarınızdan geçici paylaşım için
`scripts/remote.ps1` (Cloudflare quick tunnel) hâlâ kullanılabilir.

---

## 🔍 Sitelere özgü çözülmüş tuzaklar

* Alt sitelerde `BA` parametresi olmadan sayfa ana sayfaya yönlenir; aynı parametre bölümü de belirtir (`BA=bilgisayar`).
* Duyuru arşivleri `Referer` olmadan açılmaz; ASP.NET GridView sayfalamaları `__doPostBack` ile gezilir.
* UNIS'in arama sayfaları sonsuz filtre kombinasyonu üretir; bu yüzden sitenin kendi veri uç noktasından
  akademisyen başına tek belge derlenir.
* KBÜ Medya tarihleri "25 Şubat, 2025" biçimindedir; 2 yıldan eski haberler indekslenmez.
* Telefon rehberinde birimler fakülte düzeyindedir; bölüm bilgisi bölüm sitesiyle çapraz doğrulanır.
* Yemek listesi PDF'inde yemekler kelime koordinatlarıyla günlere atanır.
* Geçici bağlantı hatası indekslenmiş bir belgeyi artık aramadan düşürmez (yalnızca iki kez 404/410 alan belge kaldırılır).

## ⚠️ Bilinen Sınırlar

* Giriş gerektiren sistemler ve kişisel öğrenci bilgileri kapsam dışıdır.
* Resim olarak yayınlanan duyurular (JPG/PNG afişler) okunmaz. OCR rakam ve isimlerde hata yapabilir; araçlar bu
  durumda orijinal belgeyi önerir.
* Cevapların doğruluğu sitedeki bilginin güncelliğiyle sınırlıdır; bu yüzden her cevap kaynak ve tarihle verilir.

## 🙏 Sorumlu Kullanım

Tarayıcı tanımlayıcı bir User-Agent, host başına eşzamanlılık/gecikme sınırı ve isteğe bağlı saatlik toplam
sınırla çalışır. Üretimde `KBU_CRAWL_MAX_PER_HOUR` tanımlamanız önerilir. İçeriğin hakları Karabük Üniversitesi'ne
aittir.

📜 **Lisans:** [MIT](LICENSE)

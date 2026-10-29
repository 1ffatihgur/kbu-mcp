# Sürüm Notları

## v2.0.0 (2026-10-29)

28.10.2026 toplantısındaki revizeler. Sistem bir "veritabanında arama" olmaktan çıkıp hibrit bir arama motoruna dönüştü.

### Arama motoru
- **Hibrit arama:** PostgreSQL tam metin (Türkçe katlama, kök önekleri, eş anlamlılar, IDF + BM25F alan
  ağırlıkları) **+** pgvector anlamsal arama (OpenAI uyumlu embedding servisi, varsayılan `bge-m3`) →
  Reciprocal Rank Fusion, ardından tazelik (yeni tarihli belge önde). Embedding servisi yoksa anahtar kelimeyle sürer.
  Altın soru setinde (55 soru) recall@5: yalnızca anahtar kelime %93 → hibrit %96; toplantı kabul soruları 5/5.
- Açık kaynak motor denemesi: Meilisearch aynı veri ve embedding'lerle %89 (`eval/compare_meilisearch.py`).
- `docs/SUNUCU_KURULUM.md`: internetsiz üniversite sunucusuna adım adım kurulum (imaj/indeks taşıma, Ollama, nginx).
- Sonuçlarda `kaynak` (`site` / `unis` / `medya` / `bologna`) ve `eslesme` (`anahtar`, `anlamsal`, ikisi) alanları;
  `search(kaynak=...)` filtresi.
- **`academic_profile`** aracı: bir hocanın UNIS profili, idari görevleri, tüm yayın/proje/tez/ödülleri, rehber
  kaydı ve adının geçtiği en yeni site/medya belgeleri tek çağrıda.

### Veri kaynakları ve parse kalitesi
- **UNIS** (unis.karabuk.edu.tr): ~1.200 akademisyen; sitenin kendi veri uç noktasından akademisyen başına tek,
  menüsüz Markdown belge (profil, araştırma alanları, atıf özetleri, tüm çalışmalar). 3 günde bir yenilenir.
- **KBÜ Medya:** haber içeriği ve yayın tarihi doğru çıkarılıyor ("25 Şubat, 2025" biçimi); yalnızca son 2 yıl
  (`KBU_MEDYA_MAX_AGE_DAYS`).
- Tüm çıktılar **Markdown** (başlık, liste, tablo). Şablon menü / üst bilgi / alt bilgi temizliği genişletildi.
- PDF içindeki tablolar Markdown tablosuna çevrilir (çizgili tablolar; ör. kantin fiyat tarifesi).
- OCR, ilk denemede metin çıkmayan sayfalarda 300 dpi ile ikinci kez dener; `kbu ocr --retry-failed`.
- Büyük dosya sınırı 60 MB → 300 MB (`KBU_MAX_FILE_MB`), OCR sayfa tavanı (`KBU_MAX_OCR_PAGES`).
- Çıkarıcı sürümü (`docs.parser`): çıkarıcı değişince belgeler koşullu GET'e takılmadan yeniden işlenir.
- `kbu sample`: tam taramadan önce 5 site sayfası + 10 UNIS hocası + 10 KBÜ Medya haberiyle parse doğruluk raporu.

### Altyapı
- **SQLite → PostgreSQL 17 + pgvector.** `kbu migrate-sqlite` v1 indeksini taramadan taşır.
- **Docker production:** `Dockerfile` (Tesseract Türkçe dahil) ve `docker-compose.yml` (`db`, `mcp`, `worker`).
- **İş kuyruğu:** `kbu jobs add site --all | unis | medya | …`, `kbu worker`. Durdurulan/çöken iş kaldığı yerden
  sürer, başarısız iş 3 kez denenir, nedeni `kbu jobs list` / `kbu errors` ile görülür.
- **Saatlik istek sınırı:** `KBU_CRAWL_MAX_PER_HOUR` (tüm sitelere toplam).
- **Kimlik doğrulama:** `KBU_API_KEYS`; `Authorization: Bearer`, `X-API-Key` veya `?key=`.

### Cevap kalitesi
- Model KBÜ ile ilgili her soruda araçları kendiliğinden kullanır ("MCP kullan" demek gerekmez).
- Kapsam kısıtı: KBÜ dışı sorulara cevap verilmez; UNIS ve KBÜ Medya kapsam içindedir.
- Bulunamayan bilgi uydurulmaz, başka üniversitenin bilgisi verilmez; cevaplar "Kaynaklar" ve
  "Şunları da sorabilirsiniz" bölümleriyle biter. Aynı kurallar `kbu_asistan` MCP prompt'u olarak da sunulur.

### Düzeltilen hatalar
- Geçici bağlantı hatası, OCR ile okunmuş belgeleri aramadan düşürüyordu ("Yemekhane Fiyat Listesi" toplantıda bu
  yüzden bulunamadı). Artık yalnızca iki kez 404/410 alan belge kaldırılır.
- Günlük tam yenileme sırasında yeniden kontrol kuyruğundaki belgeler aramada görünmüyordu.
- `read_document` 24 saatten eski taranmış PDF'i canlı okurken boş metin dönüyordu; artık indeksteki OCR metni verilir.


## v1.1.0 (2026-10-25)

### Yeni: Uzak (Remote) MCP
- `kbu-mcp --http`: Streamable HTTP aktarımı (oturumsuz, JSON yanıt). Claude Desktop / claude.ai
  "Custom connector", Claude Code (`--transport http`) ve Codex (`--url`) ile kullanılabilir.
- `scripts/remote.ps1`: sunucuyu kendi bilgisayarınızdan ücretsiz Cloudflare quick tunnel ile
  `https://<rastgele>.trycloudflare.com/mcp` adresinde yayınlar (hesap, kart, alan adı gerekmez).
- Açılış sayfası (`/`) bağlantı yönergeleriyle, sağlık kontrolü (`/health`).
- IP başına hız sınırı (`KBU_RATE_LIMIT_PER_MIN`, varsayılan 120/dk).
- Sunucu içi zamanlayıcı: saatlik ve günlük güncellemeler HTTP modunda da kendiliğinden çalışır.

### İyileştirmeler
- Canlı araçlara kısa süreli önbellek (`KBU_LIVE_CACHE_SECONDS`, varsayılan 600 sn); aynı anda gelen
  aynı istekler tek isteğe indirilir: üniversite sitesine yük binmez.

## v1.0.0 (2026-10-25)

İlk kararlı sürüm.

### Özellikler
- **9 MCP aracı:** `search`, `read_document`, `phone_directory`, `list_units`, `latest_announcements`,
  `food_menu`, `academic_calendar`, `fetch_page`, `index_status`.
- **Tüm site kapsamı:** `*.karabuk.edu.tr` altındaki HTML sayfaları ile PDF, Word (doc/docx), Excel (xls/xlsx),
  PowerPoint (ppt/pptx), ODT/ODS, RTF, TXT/CSV ve ZIP içindeki belgeler.
- **Taranmış PDF'ler için OCR:** Tesseract (isteğe bağlı); Türkçe model otomatik indirilir.
- **Bologna / AKTS:** OBS içindeki herkese açık program sayfaları (ders planları, AKTS, kontenjan, mezuniyet koşulları).
- **Canlı telefon rehberi:** rehber.karabuk.edu.tr sorgusu; bölüm sekreteri gibi bilgiler bölüm sitesiyle çapraz doğrulanır.
- **Gün gün yemek listesi:** PDF'teki kelime konumlarıyla günlere doğru atama.
- **Türkçe arama:** İ/ı ve aksan katlama, Snowball kök bulma, ünsüz yumuşaması (k→ğ) desteği; başlık, birim,
  iletişim ve tazelik ağırlıklı sıralama.
- **Kendiliğinden güncellenen indeks:** Sunucu açılınca indeks yoksa ilk tarama, eskiyse artımlı yenileme
  arka planda başlar. `kbu update` ile elle veya zamanlanmış görevle de çalıştırılabilir.
- **Nazik tarayıcı:** Host başına hız sınırı, koşullu GET (ETag/Last-Modified), kaldığı yerden devam,
  tarama tuzaklarına ve ölü hostlara karşı koruma.

### Kalite
- 50 soruluk altın sette recall@5 %96.
- Claude Code ve Codex ile uçtan uca testler.

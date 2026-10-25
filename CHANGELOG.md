# Sürüm Notları

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

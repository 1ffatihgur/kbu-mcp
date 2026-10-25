# KBÜ MCP — Mimari ve Yol Haritası

> **Durum (25.10.2026):** Faz 0–7 tamamlandı. 28 bin belge (7 bin PDF) indekste; 9 araçlı MCP sunucusu
> Claude Code (her klasörde) ve Codex'e kayıtlı ve iki istemcide de uçtan uca test edildi; taranmış
> PDF'ler OCR'lanıyor; saatlik ve gecelik güncelleme görevleri kurulu; 50 soruluk altın sette recall@5 %96.
> **İsteğe bağlı:** vektör (anlamsal) arama, uzak HTTP yayın (Faz 8). Kullanım için [README.md](../README.md).

Amaç: `*.karabuk.edu.tr` altındaki **tüm** içeriği (HTML sayfaları, PDF, Word, Excel, PowerPoint vb.) tarayıp aranabilir hale getirmek ve bir MCP sunucusu üzerinden Claude'a **kaynaklı, güncel** cevaplar ürettirmek.

---

## 1. Sahadan tespitler (25.10.2026 itibarıyla)

| Tespit | Sonuç / Etkisi |
|---|---|
| Site ASP.NET WebForms, sunucu IIS | API yok, HTML parse edeceğiz. Bazı listeler `__doPostBack` ile sayfalanıyor; `__VIEWSTATE` ile form POST taklidi gerekecek. |
| `robots.txt` ve `sitemap.xml` 404 | URL keşfini kendimiz yapacağız (link takibi + alt alan adı keşfi). |
| İçerik onlarca alt alan adına dağılmış (`oidb.`, `sks.`, `muh.`, `tip.`, `kalite.`, `bislem.`, `iso.` …) | Alan adı beyaz listesi: `^([a-z0-9-]+\.)*karabuk\.edu\.tr$`. Alt alan adları ana sayfa linkleri + sertifika kayıtları (crt.sh) + tarama ile çıkarılacak. |
| Dosyalar `/yuklenen/dosyalar/126GGAAYYYYSSDDss.pdf` formatında (ör. `12618092026160736.pdf` = 18.09.2026 16:07:36) | Dosya adından **yükleme tarihi** çıkarılabiliyor → güncellik sıralaması için kullanılacak. Diğer kalıplar: `/belgeler/...`, `/yonerge/...`, `/dosya/...`. |
| Sunucu `ETag` ve `Last-Modified` döndürüyor | **Artımlı tarama** mümkün: `If-None-Match` ile değişmeyen dosyayı tekrar indirmeyiz. |
| Örnek PDF'ler metin tabanlı (font içeriyor), boyutlar 170 KB – 4.5 MB | Çoğu doğrudan metin çıkarılabilir; taranmış (görüntü) PDF'ler için OCR yedeği gerekli. |
| Ana sayfada kırık link var (sks PDF → HTML 404) | Kırık/silinmiş içerik tespit edilip aramadan düşürülmeli. |
| Telefon rehberi `rehber.karabuk.edu.tr` arama formu (personel tipi, unvan, ad, soyad, **birim**, dahili) ile çalışıyor | Link takibiyle taranamaz; ASP.NET form POST'u yapan özel `phone_directory` aracı gerekir. Kullanıcı örneği ("Bilgisayar Müh. bölüm sekreteri numarası") buna dayandığı için Faz 3'e alındı. |
| OBS, EBYS vb. giriş gerektiren sistemler | **Kapsam dışı.** Kişisel veriye dokunulmayacak. |

---

## 2. Mimari

```
                ┌──────────────────────── TOPLAMA (offline, zamanlanmış) ───────────────────────┐
 Tohum URL'ler  │  Keşif ──► URL Kuyruğu ──► Getirici ──► Ham Depo ──► Çıkarıcılar ──► Parçalayıcı │
 + alt alanlar  │  (link,    (öncelik,      (httpx,      (sha256,     (HTML/PDF/     (başlık/     │
                │  crt.sh)   tazelik)       ETag, hız    içerik       Office/OCR)    sayfa bazlı) │
                │                           sınırı)      adresli)                       │         │
                └───────────────────────────────────────────────────────────────────────┼─────────┘
                                                                                        ▼
                                      ┌──────────── İNDEKS (SQLite) ────────────┐
                                      │ documents · chunks · FTS5 (BM25)        │
                                      │ vektörler (sqlite-vec) · crawl_log      │
                                      └───────────────────┬─────────────────────┘
                                                          ▼
                ┌──────────────────────── SUNUM (MCP sunucusu, FastMCP) ────────────────────────┐
                │ search · get_document · fetch_live · list_announcements · list_units · status │
                │ Hibrit arama: BM25 + vektör → RRF birleştirme → (reranker) → tazelik etkisi    │
                │ Her sonuçta: URL, sayfa no, başlık, birim, tarih, son kontrol zamanı           │
                └───────────────────────────────────────────────────────────────────────────────┘
                                                          ▼
                                            Claude Desktop / Claude Code
```

### 2.1 Keşif ve tarama
- **Tohumlar:** `www.karabuk.edu.tr` + ana sayfadaki tüm alt alan adları + crt.sh'den çıkan `*.karabuk.edu.tr` sertifika kayıtları (canlı olanlar).
- **URL normalizasyonu:** şema/host küçük harf, `#` atılır, takip parametreleri temizlenir, `http`→`https` denemesi, yönlendirmeler sonrası nihai URL saklanır.
- **Nezaket:** host başına en fazla 2 eşzamanlı istek, istekler arası gecikme, tanımlayıcı User-Agent, 429/5xx'te geri çekilme (exponential backoff).
- **Artımlı:** `ETag`/`Last-Modified` ile koşullu GET; içerik hash'i aynıysa yeniden işleme yok.
- **Postback listeleri:** duyuru arşivlerindeki sayfalama için `__VIEWSTATE`/`__EVENTVALIDATION` taşıyan form POST'u.
- **Silinme tespiti:** art arda 2 kez 404/410 → `status=removed`, aramadan çıkar.

### 2.2 Çıkarma (extraction)
| Tür | Araç | Not |
|---|---|---|
| HTML | selectolax + site şablon kuralları (fallback: trafilatura) | Menü/altbilgi atılır, sadece içerik alanı; tablolar Markdown'a. |
| PDF (metin) | PyMuPDF | Sayfa bazlı metin, sayfa numarası korunur; tablolar `find_tables` ile Markdown. |
| PDF (taranmış) | Tesseract (`tur`) / ocrmypdf | Sayfa başına karakter sayısı eşiğin altındaysa OCR'a gönderilir. |
| DOCX / XLSX / PPTX | python-docx / openpyxl / python-pptx | Excel'de her sayfa ayrı tablo. |
| DOC / XLS / PPT (eski) | LibreOffice headless → yeni formata çevirip işleme | |
| ZIP | İçindeki belge dosyaları açılıp ayrı belge olarak işlenir | RAR şimdilik kapsam dışı. |
| Görsel duyurular (JPG/PNG) | OCR (opsiyonel, Faz 4) | Afiş şeklinde duyurular için. |

### 2.3 Parçalama ve metadata
- Başlık hiyerarşisine ve PDF sayfa sınırlarına göre ~800–1200 token'lık parçalar, %10–15 örtüşme.
- Her parçada: `url`, `doc_title`, `unit` (alt alan adından: muh → Mühendislik Fakültesi), `doc_type`, `page`, `published_at` (dosya adı / sayfa tarihi / Last-Modified), `last_checked`, `sha256`.

### 2.4 İndeks ve arama
- **SQLite** tek dosya: kurulumu sıfır, binlerce–on binlerce belge için yeterli.
- **FTS5 (BM25):** Türkçe normalizasyon (İ/ı doğru küçültme, aksan katlamalı ek kolon, Snowball Türkçe kök bulma). Tam ifade, yönetmelik madde numarası, isim aramada çok güçlü.
- **Vektör arama:** çok dilli embedding modeli (başlangıç: `intfloat/multilingual-e5-base`, yerel çalışır) + `sqlite-vec`. Anlamsal sorular için ("yaz okulunda kaç ders alabilirim?").
- **Birleştirme:** Reciprocal Rank Fusion → opsiyonel çapraz-kodlayıcı reranker (`bge-reranker-v2-m3`) → yeni belgeye hafif tazelik etkisi.

### 2.5 Güncellik stratejisi
| İçerik | Kontrol sıklığı |
|---|---|
| Ana sayfalar, duyuru/haber listeleri | 30–60 dk |
| Normal HTML sayfaları | Günlük |
| PDF ve ofis dosyaları (çoğu değişmez, adında tarih var) | Haftalık koşullu GET |
| Sorgu anında | `get_document` belgeyi döndürmeden önce koşullu GET yapar; değiştiyse anında yeniden çıkarır |

### 2.6 MCP araçları
| Araç | Görev |
|---|---|
| `search(query, unit?, doc_type?, date_from?, limit?)` | Hibrit arama; parça metni + kaynak metadata'sı döner. |
| `get_document(url, pages?)` | Bir belgenin tam metni (sayfalı), canlı tazelik kontrolüyle. |
| `fetch_live(url)` | Beyaz listedeki herhangi bir URL'yi anlık çekip temiz metin döner. |
| `list_announcements(unit?, since?)` | Birim bazlı güncel duyurular. |
| `list_units()` | Fakülte/MYO/daire başkanlığı ↔ alt alan adı eşleşmesi. |
| `get_academic_calendar()`, `get_food_menu(date?)`, `phone_directory(q)` | Sık sorulan dinamik içerikler (Faz 6). |
| `index_status()` | Belge sayıları, son tarama zamanları, hata oranları. |

**Sunucu talimatı:** "Sadece araç çıktılarına dayan; her bilgiyi URL (+ PDF sayfa no) ve tarihle kaynakla; bulamazsan bulamadığını söyle, tahmin etme; çelişen belgelerde en yeni tarihliyi öne çıkar ve çelişkiyi belirt."

---

## 3. Proje yapısı

```
KBU MCP/
├── pyproject.toml
├── YOL_HARITASI.md
├── src/kbu_mcp/
│   ├── config.py            # beyaz liste, hız limitleri, yollar
│   ├── db.py                # şema + migration
│   ├── crawler/  discover.py · frontier.py · fetcher.py · aspnet.py · scheduler.py
│   ├── extract/  html.py · pdf.py · office.py · ocr.py · chunk.py
│   ├── index/    normalize_tr.py · fts.py · vectors.py · search.py
│   ├── server.py            # FastMCP sunucusu
│   └── cli.py               # kbu discover | crawl | extract | index | serve | stats
├── tests/
├── eval/questions.yaml      # altın soru-cevap seti
└── data/                    # (git dışı) kbu.db, ham dosyalar, loglar
```

---

## 4. Fazlar ve çalışma planı

### Faz 0: Kurulum ve keşif
- [ ] git init, Python 3.11 sanal ortam, `pyproject.toml`, bağımlılıklar
- [ ] Alt alan adı envanteri: ana sayfa linkleri + crt.sh → canlılık kontrolü → `units` tablosu
- [ ] 5–10 farklı alt sitenin HTML şablonunu inceleyip içerik seçicilerini çıkarmak
- [ ] 100–200 belgelik örneklemde çıkarma kalitesi raporu (metin / taranmış PDF oranı, tablo durumu)
- **Bitti kriteri:** alt alan adı listesi + şablon kuralları + örneklem raporu

### Faz 1: Tarayıcı ve veritabanı
- [ ] SQLite şeması: `documents`, `fetches`, `links`, `chunks`, `units`
- [ ] Asenkron getirici (httpx): hız limiti, retry, koşullu GET, yönlendirme takibi
- [ ] URL kuyruğu: öncelik (liste sayfaları > içerik > dosya), derinlik sınırı, tekrar önleme
- [ ] ASP.NET postback sayfalama desteği
- [ ] `kbu crawl` ile tam ilk tarama (tahmini birkaç saat; arka planda)
- **Bitti kriteri:** tüm alt alan adlarında keşfedilen URL ve dosya sayısı raporu, hata oranı < %2

### Faz 2: Çıkarma hattı
- [ ] HTML içerik çıkarıcı (şablon kuralları + fallback)
- [ ] PDF metin + tablo çıkarıcı (sayfa bazlı)
- [ ] DOCX/XLSX/PPTX + LibreOffice ile eski formatlar
- [ ] Parçalayıcı + metadata (birim, tarih tahmini)
- **Bitti kriteri:** belgelerin ≥ %95'i metinli; başarısızlar listelenmiş

### Faz 3: İndeks + MVP MCP sunucusu (ilk kullanılabilir sürüm)
- [ ] Türkçe normalizasyon + FTS5 indeksi
- [ ] `search`, `get_document`, `fetch_live`, `index_status` araçları
- [ ] `phone_directory(ad?, soyad?, birim?, unvan?)`: rehber.karabuk.edu.tr formunu canlı sorgular
- [ ] Claude Desktop / Claude Code / Codex CLI'a stdio ile bağlama
- [ ] İlk 30 soruluk altın set ile deneme
- **Bitti kriteri:** Claude'dan sorulan sorulara kaynaklı cevap geliyor

### Faz 4: Kalite artırma
- [ ] Taranmış PDF ve görsel duyurular için OCR
- [ ] Embedding + `sqlite-vec` + RRF hibrit arama
- [ ] Reranker (ölçüme göre karar)
- **Bitti kriteri:** altın sette recall@5 ≥ %90

### Faz 5: Güncellik
- [ ] Zamanlayıcı (sıcak/normal/dosya katmanları)
- [ ] Silinen içerik tespiti, değişen belgeyi yeniden indeksleme
- [ ] Sorgu anında tazelik kontrolü
- **Bitti kriteri:** sitede yeni yayınlanan bir duyuru ≤ 1 saat içinde aranabilir

### Faz 6: Özel araçlar
- [ ] `list_announcements`, `list_units`, akademik takvim, yemek menüsü
- **Bitti kriteri:** bu araçlar için 20 test sorusu doğru

### Faz 7: Değerlendirme (sürekli)
- [ ] `eval/questions.yaml`'ı 100+ soruya çıkarmak (birim, yönetmelik, tarih, kişi, tablo soruları)
- [ ] Otomatik ölçüm: doğru kaynağın bulunması (recall@k) + cevap doğruluğu (elle / LLM hakem)

### Faz 8 (opsiyonel): Uzak yayın
- [ ] Docker, Streamable HTTP, VPS, zamanlanmış tarama, claude.ai connector olarak ekleme

---

## 5. Riskler ve önlemler
| Risk | Önlem |
|---|---|
| Taranmış PDF'lerde OCR hatası | OCR çıktısını işaretle; cevapta "OCR'dan okunmuştur" notu; mümkünse orijinal PDF linki. |
| PDF tablolarının bozuk çıkması | Tablo çıkarımını sayfa metniyle birlikte sakla; eval setine tablo soruları koy. |
| Çelişen / eski belgeler (eski yönetmelik vs yeni) | Tarih metadata'sı + tazelik etkisi + talimatta "en yeni tarihliyi öne çıkar, çelişkiyi söyle". |
| Sitenin aşırı yüklenmesi | Hız limiti, gece saatlerinde tam tarama, koşullu GET. |
| Hukuki/izin | Herkese açık yayından önce Bilgi İşlem'den izin; kişisel veri içeren sayfaları indekslememe filtresi. |
| Depolama | Ham dosyaları opsiyonel tut; çıkarılmış metin + hash yeterli (ham dosya on GB'ları bulabilir). |

---

## 6. Açık kararlar (varsayılanlar)
- **Dil/çatı:** Python 3.11 + FastMCP ✅ (makinede Python 3.11.9 mevcut)
- **Embedding:** yerel `multilingual-e5-base` (ücretsiz, CPU'da çalışır); gerekirse API'ye geçilir
- **OCR:** Tesseract + Türkçe dil paketi — Windows'a kullanıcı tarafından kurulacak (Faz 4'te)
- **Ham dosya saklama:** başlangıçta evet (yeniden çıkarma için), disk dolarsa kapatılır

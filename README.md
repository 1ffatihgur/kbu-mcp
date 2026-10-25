# KBÜ MCP: Karabük Üniversitesi için MCP Sunucusu

[![Sürüm](https://img.shields.io/github/v/release/1ffatihgur/kbu-mcp?label=s%C3%BCr%C3%BCm)](https://github.com/1ffatihgur/kbu-mcp/releases)
[![Lisans: MIT](https://img.shields.io/badge/lisans-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

Bu proje, **Karabük Üniversitesi web sitesindeki (`*.karabuk.edu.tr`) tüm bilgiye** erişim sağlayan bir
[Model Context Protocol (MCP)](https://modelcontextprotocol.io) sunucusudur. Claude Code, Codex, Claude Desktop,
5ire, Cursor gibi MCP destekleyen yapay zekâ uygulamalarına eklendiğinde, üniversiteyle ilgili sorular
**yalnızca üniversitenin kendi sitelerindeki içerikten**, kaynak bağlantısı ve tarih verilerek cevaplanır:
sayfalar, binlerce PDF / Word / Excel / PowerPoint belgesi, telefon rehberi, AKTS ders planları, yemek listesi,
akademik takvim ve duyurular.

> ⚠️ Bu proje Karabük Üniversitesi'nin resmi bir ürünü değildir. Üniversitenin herkese açık web sitelerini okur;
> giriş gerektiren sistemlere (OBS, EBYS, e-posta vb.) erişmez.

```
Kullanıcı: Bilgisayar mühendisliği bölüm sekreterinin numarası nedir?

Claude:    Bilgisayar Mühendisliği bölüm sekreteri [Ad SOYAD].
           • Telefon: 0 (370) 418 XX XX (dahili XXXX) • E-posta: …@karabuk.edu.tr
           Rehberde fakültede iki bölüm sekreteri görünüyor; bölüm sitesinin üst bilgisindeki numara
           bu kişinin dahilisiyle eşleşiyor.
           Kaynaklar: rehber.karabuk.edu.tr (canlı sorgu, 25.10.2026) · bbf.karabuk.edu.tr/bilgisayar
```

---

🎯 **Temel Özellikler**

* **Tüm site, tüm belgeler:** 140'tan fazla alt site; HTML sayfaları ile **PDF, Word (doc/docx), Excel (xls/xlsx),
  PowerPoint (ppt/pptx), ODT/ODS, RTF, TXT/CSV ve ZIP** içindeki belgeler (ilk taramada ~28.000 belge, ~7.000 PDF).
* **Taranmış PDF'ler için OCR:** Resim olarak taranmış belgeler (ör. yemekhane fiyat listesi) Tesseract ile okunur.
* **Canlı veriler:** Telefon rehberi, yemek listesi, akademik takvim ve birim duyuruları her soruda doğrudan
  siteden okunur.
* **Güncellik:** İndeks kendiliğinden güncellenir. Sunucu açılırken indeks yoksa ilk tarama, eskiyse artımlı
  yenileme arka planda başlar. Okunan belge 24 saatten eskiyse siteden yeniden çekilir.
* **Kaynaklı cevaplar:** Her cevap URL, PDF sayfa numarası ve tarihle verilir. Bulunamayan bilgi uydurulmaz.
* **AKTS / Bologna:** Programların ders planları, AKTS kredileri, kontenjanları ve mezuniyet koşulları.
* **Türkçe'ye özel arama:** İ/ı ve ç/ğ/ş/ü/ö katlama, kök bulma, ünsüz yumuşaması ("mühendislik" →
  "mühendisliği") ve eş anlamlılar.
* **Akıllı çapraz doğrulama:** Örneğin bölüm sekreteri, rehberdeki dahili numara bölüm sitesindeki telefonla
  eşleştirilerek belirlenir.

---

## 🚀 Hızlı Kurulum

### 1. Ön Gereksinimler

* **Python 3.11 veya üzeri:** [python.org/downloads](https://www.python.org/downloads/). Windows'ta kurulum
  sırasında "**Add Python to PATH**" seçeneğini işaretleyin.
* **Git:** [git-scm.com/downloads](https://git-scm.com/downloads). Windows için "Git for Windows/x64 Setup".
* **`uv`** (Python paket yöneticisi):
    * **Windows (PowerShell):** `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
    * **macOS / Linux:** `curl -LsSf https://astral.sh/uv/install.sh | sh`
* **(İsteğe bağlı, önerilir) Tesseract OCR:** Taranmış PDF'leri okumak için. Türkçe modeli sunucu kendisi indirir.
    * **Windows:** `winget install UB-Mannheim.TesseractOCR`
    * **macOS:** `brew install tesseract`
    * **Debian/Ubuntu:** `sudo apt install tesseract-ocr`

Kurulumdan sonra paketi bir kez önceden indirip doğrulamanız önerilir (ilk açılışı hızlandırır):

```bash
uvx --from git+https://github.com/1ffatihgur/kbu-mcp kbu --version
```

### 2. Kullandığınız uygulamaya ekleyin

#### 🟣 Claude Code

Tek komut. Tüm klasörlerde kullanılabilir olarak eklenir:

```bash
claude mcp add --scope user karabuk-universitesi -- uvx --from git+https://github.com/1ffatihgur/kbu-mcp kbu-mcp
```

Kontrol: `claude mcp list`. Ardından yeni bir Claude Code oturumunda üniversiteyle ilgili bir soru sorun.

#### ⚫ Codex (CLI / uygulama)

```bash
codex mcp add karabuk-universitesi -- uvx --from git+https://github.com/1ffatihgur/kbu-mcp kbu-mcp
```

veya `~/.codex/config.toml` dosyasına elle ekleyin (Windows: `%USERPROFILE%\.codex\config.toml`):

```toml
[mcp_servers.karabuk-universitesi]
command = "uvx"
args = ["--from", "git+https://github.com/1ffatihgur/kbu-mcp", "kbu-mcp"]
startup_timeout_sec = 120   # ilk açılışta paket indirildiği için
```

#### 🟠 Claude Desktop

1. Claude Desktop → **Settings → Developer → Edit Config**
2. Açılan `claude_desktop_config.json` dosyasına `mcpServers` altına ekleyin:

    ```json
    {
      "mcpServers": {
        "karabuk-universitesi": {
          "command": "uvx",
          "args": ["--from", "git+https://github.com/1ffatihgur/kbu-mcp", "kbu-mcp"]
        }
      }
    }
    ```

3. Claude Desktop'ı tamamen kapatıp yeniden açın.

#### 🔵 5ire ve diğer istemciler (Cursor, Windsurf, Antigravity, VS Code …)

* **5ire:** **Tools → +Local / New**
    * **Tool Key:** `karabukuniversitesi`
    * **Name:** `Karabük Üniversitesi`
    * **Command:** `uvx --from git+https://github.com/1ffatihgur/kbu-mcp kbu-mcp`
    * Kaydedip etkinleştirin (yeşil ışık).
* **Diğerleri:** İstemcinin MCP yapılandırma dosyasına (`mcp.json`, `mcp_config.json` …) yukarıdaki Claude
  Desktop örneğindeki `command` / `args` değerlerini girin.

> 💡 **`uvx` bulunamadı hatası alırsanız:** `command` alanına `uvx`'in tam yolunu yazın
> (Windows: `%USERPROFILE%\.local\bin\uvx.exe`, macOS/Linux: `~/.local/bin/uvx`).

### 3. İlk indeks (otomatik)

Sunucu ilk kez açıldığında yerel indeks yoksa **arka planda kendiliğinden oluşturulur**. Bunun için bir şey
yapmanız gerekmez.

* İlk tarama sitenin büyüklüğü nedeniyle **yaklaşık 1–2 saat** sürer (siteyi yormamak için hız sınırlıdır).
  Bilgisayar kapanırsa bir sonraki açılışta kaldığı yerden devam eder.
* Bu sırada **telefon rehberi, yemek listesi, akademik takvim, duyurular ve sayfa okuma** araçları canlı siteden
  çalışır. Tam metin arama, indeks doldukça gelişir.
* Gerekli disk alanı: **yaklaşık 2,5 GB**.
* İlerlemeyi görmek için yapay zekâya "*KBÜ indeks durumu nedir?*" diye sorabilir (`index_status`) ya da
  `uvx --from git+https://github.com/1ffatihgur/kbu-mcp kbu status` çalıştırabilirsiniz.

İsterseniz ilk taramayı elle, ilerlemeyi izleyerek de başlatabilirsiniz:

```bash
uvx --from git+https://github.com/1ffatihgur/kbu-mcp kbu update
```

**Veri klasörü:** Windows `%LOCALAPPDATA%\kbu-mcp`, macOS `~/Library/Application Support/kbu-mcp`,
Linux `~/.local/share/kbu-mcp` (`KBU_DATA_DIR` ile değiştirilebilir).

---

## 💬 Örnek Sorular

* "Bilgisayar mühendisliği bölüm sekreterinin telefon numarası nedir?"
* "Tıp Fakültesi dekanı kim?"
* "Yatay geçiş başvuruları ne zaman?"
* "28 Ekim'de yemekhanede ne var? Öğrenci yemek ücreti ne kadar?"
* "Bilgisayar mühendisliği 3. yarıyılda hangi dersler var, kaç AKTS?"
* "Erasmus başvuru şartları ve belgeleri neler?"
* "Kütüphane hafta sonu açık mı?"
* "Bilgisayar mühendisliğinde staj kaç iş günü?"
* "Öğrenci işleri daire başkanlığına nasıl ulaşırım?"
* "Lisansüstü eğitim yönetmeliğine göre tez savunma süreci nasıl?"

---

🛠️ **Kullanılabilir Araçlar (MCP Tools)**

| Araç | Ne yapar | Başlıca parametreler |
|---|---|---|
| **`search`** | Tüm sayfa ve belgelerde (PDF, Word, Excel …) Türkçe tam metin arama. Eşleşen rehber kayıtlarını da ekler. | `query`, `unit` (ör. `bbf`, `oidb`, `bologna`), `file_type` (`sayfa`, `pdf`, `word`, `excel`, `sunum`, `dosya`), `since`, `limit` |
| **`read_document`** | Bir sayfanın/belgenin tam metni (PDF'te sayfa numaralı). İndeksteki kopya eskiyse canlı siteden okur. | `url`, `offset`, `max_chars`, `force_live` |
| **`phone_directory`** | Resmi telefon rehberini canlı sorgular: ünvan, ad, görev, birim, telefon, dahili, e-posta. | `ad`, `soyad`, `birim`, `unvan`, `personel_tipi`, `dahili`, `gorev_icerir` |
| **`list_units`** | Rehber birimleri (fakülte, MYO, daire başkanlığı …) ve alt siteler. | `filtre` |
| **`latest_announcements`** | Bir birim sitesinin güncel duyuru, etkinlik ve haberleri (canlı). | `site` (ör. `oidb.karabuk.edu.tr`, `bbf.karabuk.edu.tr/bilgisayar`) |
| **`food_menu`** | Güncel aylık yemek listesi, gün gün (canlı). | — |
| **`academic_calendar`** | Güncel akademik takvim (canlı). | `tur` (`önlisans-lisans`, `lisansüstü`, `tıp`, `diş hekimliği`, `hazırlık`, `yaz okulu`, `liste`) |
| **`fetch_page`** | Herhangi bir KBÜ sayfasını canlı okur ve linklerini döner (sitede gezinme). | `url`, `link_filter` |
| **`index_status`** | İndeks istatistikleri, son güncellemeler, arka plan güncellemesi. | — |

Sunucu, yapay zekâya şu kuralları talimat olarak verir: yalnızca araç çıktılarına dayan, her bilgi için kaynak
ve tarih ver, bulamadığını açıkça söyle, çelişen belgelerde en günceli esas al.

---

## 🔄 Güncel Tutma

* **Otomatik:** MCP sunucusu her açıldığında indeksin yaşını kontrol eder. 24 saatten eskiyse tam yenileme
  (koşullu GET, değişmeyen dosyalar indirilmez + rehber + OCR), 1 saatten eskiyse duyuru/ana sayfa yenilemesini
  arka planda başlatır. Kapatmak için ortam değişkeni: `KBU_AUTO_UPDATE=0`.
* **Zamanlanmış (isteğe bağlı):** Yapay zekâ uygulaması kapalıyken de güncel kalsın istiyorsanız:

    **Windows (PowerShell):**
    ```powershell
    $act = New-ScheduledTaskAction -Execute "uvx" -Argument "--from git+https://github.com/1ffatihgur/kbu-mcp kbu update --hot"
    Register-ScheduledTask -TaskName "KBU MCP saatlik" -Action $act -Trigger (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Hours 1))
    $act2 = New-ScheduledTaskAction -Execute "uvx" -Argument "--from git+https://github.com/1ffatihgur/kbu-mcp kbu update"
    Register-ScheduledTask -TaskName "KBU MCP gecelik" -Action $act2 -Trigger (New-ScheduledTaskTrigger -Daily -At 03:00)
    ```

    **macOS / Linux (`crontab -e`):**
    ```cron
    0 * * * *  ~/.local/bin/uvx --from git+https://github.com/1ffatihgur/kbu-mcp kbu update --hot
    0 3 * * *  ~/.local/bin/uvx --from git+https://github.com/1ffatihgur/kbu-mcp kbu update
    ```

Aynı anda yalnızca bir güncelleme çalışır; sunucunun ve zamanlanmış görevin aynı anda tetiklenmesi sorun olmaz.

---

## 🧰 Komut Satırı

`uvx --from git+https://github.com/1ffatihgur/kbu-mcp kbu <komut>` (veya geliştirici kurulumunda `kbu <komut>`):

| Komut | Açıklama |
|---|---|
| `kbu update [--hot]` | İndeksi oluştur/güncelle (ilk seferde tam tarama) |
| `kbu status` | Veri klasörü, son güncellemeler, çalışan güncelleme |
| `kbu search "yatay geçiş"` | İndekste deneme araması |
| `kbu stats [--hosts] [--errors]` | Belge/dosya istatistikleri |
| `kbu people` | Telefon rehberi önbelleğini yenile |
| `kbu ocr` | Taranmış PDF'leri OCR ile işle |
| `kbu reindex` | Arama indeksini yeniden kur (siteyi yeniden taramadan) |
| `kbu-mcp` | MCP sunucusunu stdio üzerinden başlat |

### ⚙️ Ortam Değişkenleri

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `KBU_DATA_DIR` | işletim sistemine göre (yukarıda) | İndeks, log ve OCR modellerinin klasörü |
| `KBU_DB_PATH` | `KBU_DATA_DIR/kbu.db` | Veritabanı dosyası |
| `KBU_AUTO_UPDATE` | `1` | `0`: sunucu açılışında otomatik güncelleme yapma |
| `TESSDATA_PREFIX` | — | Kendi Tesseract dil verisi klasörünüz |

---

## 👩‍💻 Geliştirici Kurulumu

```bash
git clone https://github.com/1ffatihgur/kbu-mcp.git
cd kbu-mcp
python -m venv .venv
# Windows: .venv\Scripts\activate   ·   macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
kbu update                 # depo kopyasında veriler ./data altına yazılır
pytest                     # birim testleri
python eval/run_eval.py    # arama kalitesi ölçümü (altın soru seti)
```

Depo kopyasından MCP istemcisine eklemek için `command` olarak `.venv` içindeki Python'u, `args` olarak
`["-m", "kbu_mcp.server"]` verin.

---

## 🏗️ Nasıl Çalışır?

```
 *.karabuk.edu.tr ──► Tarayıcı ──► Çıkarıcılar ──► SQLite FTS5 indeksi ──► MCP sunucusu ──► Claude / Codex / …
                      (nazik,      (HTML, PDF,     (Türkçe normalizasyon,  (9 araç; canlı
                       artımlı)     Office, OCR)    BM25 + ağırlıklar)       doğrulama)
```

* **Tarayıcı:** Host başına hız sınırı, ETag/Last-Modified ile koşullu GET, kaldığı yerden devam, ASP.NET
  GridView sayfalamalarını (`__doPostBack`) gezme, tarama tuzaklarına ve ölü hostlara karşı koruma.
* **Çıkarıcılar:** Site şablonunun menü/altbilgi kısmını atıp içerik alanını alır; PDF'leri sayfa numarasıyla,
  tabloları satır satır çıkarır; görüntü PDF'leri OCR'a gönderir.
* **Arama:** BM25 + başlık / birim / iletişim / tazelik ağırlıkları; telefon rehberi kayıtlarıyla zenginleştirme.

Siteye özgü çözülmüş tuzaklar:

* Alt sitelerdeki `BA` parametresi olmadan sayfalar ana sayfaya yönlenir; aynı parametre bölümü de belirtir
  (`BA=bilgisayar`).
* Duyuru arşivleri `Referer` başlığı olmadan açılmaz.
* Telefon rehberinde birimler fakülte düzeyindedir; bölüm bilgisi bölüm sitesiyle çapraz doğrulanır.
* Yemek listesi PDF'inde düz metin sütunları karıştırdığı için yemekler kelime koordinatlarıyla günlere atanır.

Ayrıntılı tasarım: [docs/YOL_HARITASI.md](docs/YOL_HARITASI.md).

---

## ⚠️ Bilinen Sınırlar

* **Giriş gerektiren sistemler** (OBS öğrenci/personel ekranları, EBYS, e-posta, başvuru sistemleri) bilerek
  kapsam dışıdır; kişisel öğrenci bilgisine erişilmez.
* **Resim olarak yayınlanan duyurular** (JPG/PNG afişler) şimdilik okunmaz.
* **Ders detay sayfaları** (haftalık konular, kaynaklar) taranmaz; ders planları ve AKTS bilgileri taranır.
* Çok büyük/dinamik siteler (`avesis`, `unis`) taranmaz, istenirse `fetch_page` ile canlı okunur.
* **OCR** rakam ve isimlerde hata yapabilir; araç bu durumda kullanıcıya orijinal belgeyi önerir.
* Cevapların doğruluğu sitedeki bilginin güncelliğiyle sınırlıdır; bu yüzden her cevap kaynak ve tarihle verilir.

---

## 🤝 Katkı

Hata bildirimi ve önerileriniz için [Issues](https://github.com/1ffatihgur/kbu-mcp/issues). Yeni bir soru türünün
doğru cevaplanmadığını görürseniz, soruyu ve beklenen kaynağı `eval/questions.json` dosyasına ekleyen bir PR
özellikle değerlidir.

## 🙏 Sorumlu Kullanım

Tarayıcı üniversite sunucularını yormamak için hız sınırıyla ve tanımlayıcı bir User-Agent ile çalışır. Varsayılan
ayarları (eşzamanlılık, gecikme) artırmayın. Site içeriğinin hakları Karabük Üniversitesi'ne aittir; bu proje
içeriği yeniden dağıtmaz, her kullanıcı kendi yerel indeksini oluşturur.

📜 **Lisans**

Bu proje MIT Lisansı altında lisanslanmıştır. Ayrıntılar için [`LICENSE`](LICENSE) dosyasına bakınız.

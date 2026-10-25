"""Karabük Üniversitesi MCP sunucusu (stdio).

Araçlar yalnızca *.karabuk.edu.tr içeriğiyle çalışır. Yerel indeks belgeyi
bulmak için kullanılır; cevabın dayandığı içerik mümkün olduğunda canlı
siteden doğrulanır.
"""

import asyncio
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from . import __version__, config, db, live, updater, urls
from .crawler import now_iso
from .search import search as fts_search, search_people
from .textnorm import normalize_for_index

log = logging.getLogger("kbu.server")

INSTRUCTIONS = """\
Bu sunucu Karabük Üniversitesi'nin resmi web sitesi (*.karabuk.edu.tr) içeriğine erişim sağlar.
Üniversiteyle ilgili HER soruda şu kurallara uy:

1. Cevabı YALNIZCA bu araçların döndürdüğü içeriğe dayandır. Genel bilgini, tahminini veya başka
   kaynakları kullanma. Araçlarda bulamadığın bilgiyi "sitede bulamadım" diye açıkça söyle ve
   kullanıcıyı en ilgili sayfaya (URL) yönlendir.
2. Her bilgi için kaynak URL'sini (PDF ise sayfa numarasını) ve belgenin tarihini / son kontrol
   zamanını ver.
3. Doğru araç seçimi:
   - Kişi, telefon, dahili numara, e-posta, "sekreter", "bölüm başkanı" vb. → phone_directory
     (gerekirse ardından search ile bölümün "iletişim"/"personel" sayfası).
   - "X'in dekanı / müdürü / bölüm başkanı / rektör yardımcıları kim?" → phone_directory(birim=..., gorev_icerir="dekan")
     ile rehberin görev sütununa bak, birimin yönetim sayfasıyla (search) doğrula. search sonucundaki
     `rehber_kayitlari` alanı da bu soruların cevabını içerebilir.
   - Yemek menüsü → food_menu. Akademik takvim / sınav-ders başlangıç tarihleri → academic_calendar.
   - Bir birimin güncel duyuruları → latest_announcements.
   - Ders planı / müfredat, AKTS, haftalık ders saatleri, program kontenjanı, mezuniyet koşulları,
     program çıktıları, kabul koşulları → search(..., unit="bologna") (OBS Bologna Bilgi Paketi).
   - Diğer her şey (yönetmelik, yönerge, başvuru, burs, harç, staj, Erasmus, kontenjan, ders
     programı, sınav programı, formlar, birimler, haberler...) → search, ardından en ilgili
     sonucun tamamını read_document ile oku.
4. search sonuçları parça (snippet) içerir; kesin bilgi (tarih, sayı, isim, madde) vermeden önce
   ilgili belgeyi read_document ile oku. Gerekirse farklı kelimelerle (eş anlamlı, kısaltma,
   birim adı) birkaç kez ara; birim adını `unit` filtresiyle daralt.
5. Aynı konuda birden fazla belge varsa (ör. eski ve yeni yönetmelik, farklı yılların takvimi)
   EN GÜNCEL tarihli olanı esas al, eskisi olduğunu belirt; çelişki varsa kullanıcıya söyle.
6. Bilgi bir bölüm/birim düzeyindeyse doğru birimin sitesinden geldiğinden emin ol
   (ör. Bilgisayar Mühendisliği → bbf.karabuk.edu.tr/bilgisayar; Bilgisayar ve Bilişim Bilimleri Fakültesi).
   Telefon rehberinde birimler fakülte düzeyindedir: aynı fakültede birden fazla "Bölüm Sekreteri"
   çıkarsa, bölüm sitesinin üst bilgisindeki bölüm telefonunu (ör. akademikPersonel.aspx?BA=bilgisayar
   veya bölüm ana sayfası) rehberdeki dahili numarayla eşleştirerek doğru kişiyi belirle; eşleşmezse
   adayların hepsini belirsizliği açıkça söyleyerek ver.
   Alt site sayfalarında BA parametresi bölümü gösterir (BA=bilgisayar, BA=yazilim ...).
7. Giriş gerektiren sistemler (OBS, EBYS, e-posta vb.) ve kişisel öğrenci bilgileri kapsam dışıdır.
"""

mcp = MCPServer(name="karabuk-universitesi", title="Karabük Üniversitesi", instructions=INSTRUCTIONS,
                version=__version__, website_url=config.REPO_URL)

NO_INDEX = {
    "hata": "Yerel indeks henüz hazır değil; ilk kurulumda arka planda oluşturuluyor (yaklaşık 1-2 saat).",
    "ipucu": "Bu sırada phone_directory, food_menu, academic_calendar, latest_announcements ve fetch_page "
             "araçları canlı siteden çalışır. İlerleme için index_status.",
}

RO = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=True)

_conn = None


def conn():
    global _conn
    if _conn is None:
        if not config.DB_PATH.exists():
            raise RuntimeError(f"İndeks bulunamadı ({config.DB_PATH}). Önce `kbu crawl` çalıştırılmalı.")
        _conn = db.connect(readonly=True)
    return _conn


def _stale(checked_at: str | None, hours: float) -> bool:
    if not checked_at:
        return True
    try:
        t = datetime.strptime(checked_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - t > timedelta(hours=hours)


def _sections_to_text(sections, offset: int, max_chars: int) -> tuple[str, int, int]:
    parts = []
    for page, text in sections:
        parts.append((f"[Sayfa {page}]\n" if page else "") + text)
    full = "\n\n".join(parts)
    return full[offset: offset + max_chars], len(full), offset + max_chars


# ============================================================================ araçlar

@mcp.tool(annotations=RO)
def search(
    query: Annotated[str, Field(description="Türkçe arama ifadesi, ör. 'yatay geçiş başvuru tarihleri', 'bilgisayar mühendisliği bölüm sekreteri'")],
    unit: Annotated[str | None, Field(description="Birim/site filtresi (ör. 'bbf', 'bilgisayar', 'oidb', 'Tıp Fakültesi', 'sks')")] = None,
    file_type: Annotated[str | None, Field(description="'sayfa', 'pdf', 'word', 'excel', 'sunum' veya 'dosya' (tüm dosyalar)")] = None,
    since: Annotated[str | None, Field(description="Bu tarihten (YYYY-AA-GG) yeni belgeler")] = None,
    limit: Annotated[int, Field(ge=1, le=25)] = 8,
) -> dict:
    """Karabük Üniversitesi sitelerindeki tüm sayfa ve dosyalarda (PDF, Word, Excel...) tam metin arama.
    Sonuç parçalarını kesin bilgi için kullanmadan önce read_document ile belgenin tamamını oku."""
    try:
        c = conn()
    except RuntimeError:
        return NO_INDEX
    res = fts_search(c, query, unit=unit, kind=file_type, date_from=since, limit=limit)
    out = {"sorgu": query, "sonuc_sayisi": len(res), "sonuclar": res}
    if updater.lock_active() and not db.get_meta(c, "last_update_full") and not db.get_meta(c, "last_crawl_finished"):
        out["uyari"] = "İlk indeksleme sürüyor; sonuçlar eksik olabilir. Bulunamayan bilgi için fetch_page ile ilgili birim sitesine bakın."
    people = search_people(conn(), query, limit=11) if not unit and not file_type else []
    if 0 < len(people) <= 10:
        out["rehber_kayitlari"] = {
            "not": "Telefon rehberi önbelleğinden; güncel numara için phone_directory ile canlı doğrula.",
            "onbellek_tarihi": db.get_meta(conn(), "people_refreshed"),
            "kisiler": people,
        }
    if not res:
        out["ipucu"] = "Sonuç yok. Daha az / farklı kelime, eş anlamlı veya birim adı deneyin; unit filtresini kaldırın."
    return out


@mcp.tool(annotations=RO)
async def read_document(
    url: Annotated[str, Field(description="search sonucundaki veya herhangi bir *.karabuk.edu.tr URL'si")],
    offset: Annotated[int, Field(ge=0, description="Uzun belgelerde devam etmek için karakter konumu")] = 0,
    max_chars: Annotated[int, Field(ge=1000, le=60000)] = 20000,
    force_live: Annotated[bool, Field(description="İndeks yerine her zaman canlı siteden oku")] = False,
) -> dict:
    """Bir sayfanın veya dosyanın (PDF/Word/Excel...) tam metnini döner. İndeksteki kopya 24 saatten eskiyse
    belge canlı siteden yeniden okunur; böylece içerik güncel olur."""
    n = urls.normalize(url)
    if not n or not urls.is_live_fetchable(n):
        return {"hata": "Sadece *.karabuk.edu.tr altındaki herkese açık adresler okunabilir."}
    row = None
    try:
        row = conn().execute("SELECT * FROM docs WHERE url=? OR final_url=? ORDER BY status='ok' DESC LIMIT 1", (n, n)).fetchone()
    except RuntimeError:
        pass

    if row and row["status"] == "ok" and not force_live and not _stale(row["checked_at"], 24):
        chunks = conn().execute("SELECT page, text FROM chunks WHERE doc_id=? ORDER BY ord", (row["id"],)).fetchall()
        sections = _merge_chunks(chunks)
        text, total, nxt = _sections_to_text(sections, offset, max_chars)
        return _doc_result(row["final_url"] or n, row["title"] or row["anchor_text"], row["kind"], row["content_date"],
                           f"indeks (son kontrol {row['checked_at']} UTC)", text, total, nxt, row["pages"], row["needs_ocr"])

    try:
        ex, final, status, kind, lm = await live.fetch_and_extract(n)
    except live.NotAllowed as e:
        return {"hata": str(e)}
    except Exception as e:
        if row and row["status"] == "ok":
            chunks = conn().execute("SELECT page, text FROM chunks WHERE doc_id=? ORDER BY ord", (row["id"],)).fetchall()
            text, total, nxt = _sections_to_text(_merge_chunks(chunks), offset, max_chars)
            return _doc_result(row["final_url"] or n, row["title"], row["kind"], row["content_date"],
                               f"indeks (canlı siteye ulaşılamadı: {type(e).__name__}; son kontrol {row['checked_at']} UTC)",
                               text, total, nxt, row["pages"], bool(row["needs_ocr"]))
        return {"hata": f"Sayfaya ulaşılamadı: {type(e).__name__}: {e}"}
    if status >= 400:
        return {"hata": f"Sayfa şu an erişilemiyor (HTTP {status}). Bağlantı kırık veya içerik kaldırılmış olabilir.", "url": final}
    if ex is None:
        return {"hata": f"Bu dosya türü okunamıyor ({kind}).", "url": final}
    text, total, nxt = _sections_to_text(ex.sections or [(None, ex.text)], offset, max_chars)
    date = ex.content_date
    if lm and kind != "html":
        from .crawler import last_modified_date
        date = last_modified_date(lm) or date
    return _doc_result(final, ex.title or (row["anchor_text"] if row else None), kind, date,
                       f"canlı site ({now_iso()} UTC)", text, total, nxt, ex.pages, ex.needs_ocr)


def _merge_chunks(chunks) -> list[tuple[int | None, str]]:
    """Örtüşmeli parçaları sayfa bazında birleştirir; bir önceki parçanın sonunu tekrar eden başlangıcı atar."""
    out: list[tuple[int | None, str]] = []
    for c in chunks:
        page, text = c["page"], c["text"]
        if out and out[-1][0] == page:
            prev = out[-1][1]
            for k in range(min(config.CHUNK_OVERLAP + 50, len(text)), 15, -1):
                if prev.endswith(text[:k]):
                    text = text[k:].lstrip("\n")
                    break
            out[-1] = (page, prev + "\n" + text)
        else:
            out.append((page, text))
    return out


def _doc_result(url, title, kind, date, source, text, total, nxt, pages, needs_ocr) -> dict:
    res = {
        "url": url, "baslik": title, "tur": kind, "tarih": date, "kaynak": source,
        "toplam_karakter": total, "metin": text,
    }
    if pages:
        res["sayfa_sayisi"] = pages
    if nxt < total:
        res["devami_icin_offset"] = nxt
    if needs_ocr == 2:
        res["not"] = "Metin taranmış görüntüden OCR ile okundu; rakam ve isimlerde okuma hatası olabilir, kritik bilgide orijinal dosyayı da gösterin."
    elif needs_ocr:
        res["uyari"] = "Belge taranmış görüntü içeriyor olabilir; metin eksik olabilir. Kullanıcıya orijinal dosyayı da önerin."
    return res


@mcp.tool(annotations=RO)
async def phone_directory(
    ad: Annotated[str, Field(description="Kişinin adı (kısmi olabilir)")] = "",
    soyad: Annotated[str, Field(description="Kişinin soyadı (kısmi olabilir)")] = "",
    birim: Annotated[str, Field(description="Birim adı, ör. 'Bilgisayar ve Bilişim Bilimleri Fakültesi', 'Öğrenci İşleri'. Rehberde birimler fakülte/daire düzeyindedir; bölümler fakülte altında listelenir.")] = "",
    unvan: Annotated[str, Field(description="Ünvan, ör. 'Prof. Dr.', 'Sekreter', 'Memur', 'Bilgisayar İşletmeni', 'Fakülte Sekreteri'")] = "",
    personel_tipi: Annotated[str, Field(description="'akademik' veya 'idari'")] = "",
    dahili: Annotated[str, Field(description="Dahili numara")] = "",
    gorev_icerir: Annotated[str, Field(description="Görev sütununda geçmesi gereken ifade, ör. 'bölüm', 'sekreter', 'dekan', 'başkan'")] = "",
) -> dict:
    """Karabük Üniversitesi resmi telefon rehberini (rehber.karabuk.edu.tr) CANLI sorgular: ünvan, ad, soyad,
    görev, birim, telefon, dahili, e-posta döner. Bölüm sekreteri gibi bilgiler için birimi (fakülte) ve
    gerekirse unvan/görev filtresini ver; sonuç yoksa search ile bölümün iletişim sayfasına bak."""
    if not any([ad, soyad, birim, unvan, dahili, gorev_icerir]):
        return {"hata": "En az bir arama alanı (ad, soyad, birim, unvan, dahili veya gorev_icerir) verin."}
    try:
        res = await live.rehber_search(ad=ad, soyad=soyad, birim=birim, unvan=unvan, personel_tipi=personel_tipi, dahili=dahili)
    except Exception as e:
        q = " ".join(x for x in (ad, soyad, birim, unvan, gorev_icerir) if x)
        try:
            cached = search_people(conn(), q)
        except Exception:
            cached = []
        return {"uyari": f"Canlı rehbere ulaşılamadı ({type(e).__name__}); önbellekteki kayıtlar gösteriliyor.",
                "kaynak": live.REHBER_URL, "kisiler": cached}
    if gorev_icerir:
        g = normalize_for_index(gorev_icerir)
        filtered = [k for k in res["kisiler"] if g in normalize_for_index(f"{k.get('gorev', '')} {k.get('unvan', '')}")]
        res["filtre_oncesi_sayi"] = len(res["kisiler"])
        res["kisiler"] = filtered
        res["sonuc_sayisi"] = len(filtered)
    if len(res["kisiler"]) > 120:
        res["kisiler"] = res["kisiler"][:120]
        res["not"] = "İlk 120 kayıt gösteriliyor; daha dar filtre kullanın."
    return res


@mcp.tool(annotations=RO)
async def list_units(
    filtre: Annotated[str, Field(description="Ad içinde aranacak ifade (boş: hepsi)")] = "",
) -> dict:
    """Rehberdeki birimleri (fakülte, MYO, daire başkanlığı, koordinatörlük) ve indekslenmiş alt siteleri listeler.
    Birim adını doğru yazmak veya bir bölümün hangi fakülteye/siteye bağlı olduğunu bulmak için kullan."""
    f = normalize_for_index(filtre) if filtre else ""
    try:
        opts = await live.rehber_options()
        birimler = opts["birimler"]
    except Exception:
        birimler = [r["name"] for r in conn().execute("SELECT name FROM units WHERE source='rehber'")]
    sites = []
    try:
        for r in conn().execute(
            """SELECT url, site_title FROM docs WHERE status='ok' AND kind='html' AND site_title IS NOT NULL
               AND (url LIKE '%.karabuk.edu.tr/' OR url LIKE '%/index.aspx' OR url GLOB 'https://*.karabuk.edu.tr/[a-z]*' )
               ORDER BY url"""):
            path = urls.urlsplit(r["url"]).path
            if path in ("/", "/index.aspx") or re.fullmatch(r"/[a-z]+/?", path):
                sites.append({"ad": r["site_title"], "url": r["url"]})
    except RuntimeError:
        pass
    seen, uniq = set(), []
    for s in sites:
        key = (s["ad"], urls.host_of(s["url"]), urls.urlsplit(s["url"]).path.strip("/").replace("index.aspx", ""))
        if key not in seen:
            seen.add(key)
            uniq.append(s)
    if f:
        birimler = [b for b in birimler if f in normalize_for_index(b)]
        uniq = [s for s in uniq if f in normalize_for_index(s["ad"] + " " + s["url"])]
    return {"rehber_birimleri": birimler, "siteler": uniq[:400]}


@mcp.tool(annotations=RO)
async def latest_announcements(
    site: Annotated[str, Field(description="Birim sitesi, ör. 'www.karabuk.edu.tr', 'oidb.karabuk.edu.tr', 'bbf.karabuk.edu.tr/bilgisayar', 'muh.karabuk.edu.tr'")] = "www.karabuk.edu.tr",
    limit: Annotated[int, Field(ge=1, le=50)] = 20,
) -> dict:
    """Bir birim sitesinin ana sayfasındaki güncel duyuru, etkinlik ve haber listesini CANLI okur.
    Detay için ilgili URL'yi read_document ile aç."""
    try:
        return await live.site_announcements(site, limit=limit)
    except live.NotAllowed as e:
        return {"hata": str(e)}
    except Exception as e:
        return {"hata": f"Siteye ulaşılamadı: {type(e).__name__}: {e}"}


@mcp.tool(annotations=RO)
async def food_menu() -> dict:
    """Sağlık Kültür ve Spor Daire Başkanlığı sitesindeki güncel aylık yemek listesini CANLI okur
    (yemekhane fiyat listesi bağlantısıyla birlikte)."""
    try:
        links = await live.find_links("https://sks.karabuk.edu.tr/", r"yemek|men[üu]")
    except Exception as e:
        return {"hata": f"SKS sitesine ulaşılamadı: {type(e).__name__}"}
    menu_links = [(u, t) for u, t in links if urls.kind_from_url(u) and re.search(r"liste|men[üu]", t, re.I) and not re.search(r"fiyat", t, re.I)]
    others = [{"baslik": t, "url": u} for u, t in links if (u, t) not in menu_links]
    async with live.client() as c:
        for u, t in menu_links:
            try:
                resp = await c.get(u, headers={"Referer": "https://sks.karabuk.edu.tr/index.aspx"})
            except Exception:
                continue
            if resp.status_code >= 400 or not resp.content.startswith(b"%PDF"):
                continue  # kırık link (sitede eski "Yemek Menüsü" bağlantısı 404 veriyor)
            from .crawler import last_modified_date
            from .extract import extract_pdf, pdf_date_grid
            data = resp.content
            grid = await asyncio.to_thread(pdf_date_grid, data)
            res = {"kaynak": str(resp.url), "baslik": t, "dosya_tarihi": last_modified_date(resp.headers.get("last-modified")),
                   "okunma": now_iso() + " UTC", "ilgili_baglantilar": others}
            if grid:
                res["gunler"] = [{"tarih": label, "menu": items} for label, items in grid]
                res["not"] = "Günlere atama PDF'teki kelime konumlarından yapıldı; sayılar kaloridir (kcal)."
            else:
                res["metin"] = (await asyncio.to_thread(extract_pdf, data)).text[:30000]
            return res
    return {"hata": "Yemek listesi dosyası açılamadı.", "bulunan_baglantilar": [{"baslik": t, "url": u} for u, t in links]}


@mcp.tool(annotations=RO)
async def academic_calendar(
    tur: Annotated[str, Field(description="'önlisans-lisans' (varsayılan), 'lisansüstü', 'tıp', 'diş hekimliği', 'hazırlık', 'yaz okulu' veya 'liste' (sadece bağlantılar)")] = "önlisans-lisans",
) -> dict:
    """akademiktakvim.karabuk.edu.tr'deki güncel akademik takvimi CANLI okur (ders başlangıcı, sınav haftaları,
    ekle-sil, bütünleme, mezuniyet vb. tarihler)."""
    page = "https://akademiktakvim.karabuk.edu.tr/index.aspx"
    try:
        links = await live.find_links(page, r".")
    except Exception as e:
        return {"hata": f"Akademik takvim sitesine ulaşılamadı: {type(e).__name__}"}
    files = [(u, t) for u, t in links if urls.kind_from_url(u) == "pdf" and t and "yemek" not in t.lower()]
    listing = [{"baslik": t, "url": u} for u, t in files]
    if normalize_for_index(tur) == "liste":
        return {"kaynak": page, "takvimler": listing}
    want = normalize_for_index(tur).replace(" ", "")
    aliases = {"onlisanslisans": "onlisans", "lisans": "onlisans", "disiekimligi": "dis", "dishekimligi": "dis",
               "tip": "tip", "hazirlik": "hazirlik", "yazokulu": "yaz", "lisansustu": "lisansustu"}
    key = aliases.get(want, want)
    match = None
    for u, t in files:
        tn = normalize_for_index(t).replace(" ", "")
        if key in tn and not (key == "onlisans" and "lisansustu" in tn) and not (key == "tip" and "dis" in tn):
            match = (u, t)
            break
    if not match:
        return {"hata": f"'{tur}' için takvim bulunamadı.", "kaynak": page, "takvimler": listing}
    ex, final, status, kind, lm = await live.fetch_and_extract(match[0])
    if status >= 400 or not ex:
        return {"hata": f"Takvim dosyası açılamadı (HTTP {status}).", "url": final, "takvimler": listing}
    from .crawler import last_modified_date
    return {"kaynak": final, "baslik": match[1], "dosya_tarihi": last_modified_date(lm), "okunma": now_iso() + " UTC",
            "metin": ex.text[:40000], "diger_takvimler": listing}


@mcp.tool(annotations=RO)
async def fetch_page(
    url: Annotated[str, Field(description="*.karabuk.edu.tr altındaki bir adres")],
    link_filter: Annotated[str | None, Field(description="Sadece metni/adresi bu ifadeyle eşleşen linkleri döndür (regex, ör. 'staj|form')")] = None,
    max_chars: Annotated[int, Field(ge=500, le=60000)] = 15000,
) -> dict:
    """Bir sayfayı CANLI okur ve sayfadaki linkleri de döner. Sitede gezinmek (menüden alt sayfaya inmek),
    indekste olmayan yeni bir sayfayı görmek veya bir sayfadaki dosya bağlantılarını bulmak için kullan."""
    try:
        ex, final, status, kind, lm = await live.fetch_and_extract(url)
    except live.NotAllowed as e:
        return {"hata": str(e)}
    except Exception as e:
        return {"hata": f"Sayfaya ulaşılamadı: {type(e).__name__}: {e}"}
    if status >= 400 or ex is None:
        return {"hata": f"Sayfa açılamadı (HTTP {status}, tür {kind}).", "url": final}
    links, seen = [], set()
    rx = re.compile(link_filter, re.I) if link_filter else None
    for u, t in ex.links:
        if u in seen or not urls.is_allowed_host(urls.host_of(u)):
            continue
        if rx and not (rx.search(t or "") or rx.search(u)):
            continue
        seen.add(u)
        links.append({"metin": t, "url": u})
    return {"url": final, "baslik": ex.title, "birim_sitesi": ex.site_title, "tur": kind, "okunma": now_iso() + " UTC",
            "metin": ex.text[:max_chars], "kesildi": len(ex.text) > max_chars, "linkler": links[:300]}


@mcp.tool(annotations=RO)
def index_status() -> dict:
    """Yerel indeksin durumu: belge/dosya sayıları, son güncellemeler, arka planda güncelleme çalışıyor mu."""
    out = {"surum": __version__, **updater.status()}
    if os.environ.get("KBU_REMOTE"):
        out.pop("veri_klasoru", None)
    try:
        c = conn()
    except RuntimeError:
        return {**out, **NO_INDEX}
    out.update({
        "belgeler": {r[0]: r[1] for r in c.execute("SELECT kind, COUNT(*) FROM docs WHERE status='ok' GROUP BY kind")},
        "parca_sayisi": c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
        "site_sayisi": c.execute("SELECT COUNT(DISTINCT host) FROM docs WHERE status='ok'").fetchone()[0],
        "ocr_ile_okunan_pdf": c.execute("SELECT COUNT(*) FROM docs WHERE needs_ocr=2").fetchone()[0],
        "rehber_onbellek": db.get_meta(c, "people_refreshed"),
    })
    return out


def main(argv: list[str] | None = None) -> None:
    import argparse

    ap = argparse.ArgumentParser(prog="kbu-mcp", description="Karabük Üniversitesi MCP sunucusu")
    ap.add_argument("--http", action="store_true", help="Uzak sunucu (Streamable HTTP) olarak çalış")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "7860")))
    ap.add_argument("--version", action="version", version=f"kbu-mcp {__version__}")
    args = ap.parse_args(argv)
    if args.http or os.environ.get("KBU_TRANSPORT") == "http":
        from .remote import run

        run(args.port)
        return

    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        updater.maybe_auto_update()  # indeks yoksa/eskiyse ayrık süreçte güncelle; sunucu beklemez
    except Exception as e:
        log.warning("otomatik güncelleme kontrolü başarısız: %r", e)
    mcp.run("stdio")


if __name__ == "__main__":
    main()

"""Canlı (sorgu anında) veri kaynakları: telefon rehberi, sayfa okuma, duyurular, menü, takvim."""

import asyncio
import copy
import functools
import os
import re
import time
from difflib import SequenceMatcher
from urllib.parse import urlsplit

import httpx
from selectolax.lexbor import LexborHTMLParser

from . import config, urls
from .crawler import decode_html, now_iso, referer_for
from .extract import clean_text, extract_bytes, extract_html, sniff_kind
from .textnorm import fold, normalize_for_index

REHBER_URL = "https://rehber.karabuk.edu.tr/"

# ======================================================================== kısa süreli önbellek
# Uzak (çok kullanıcılı) sunucuda aynı sorunun üniversite sitesine tekrar tekrar gitmesini önler.
# Süre kısa tutulur; içerik "canlı" olma özelliğini korur.

CACHE_TTL = float(os.environ.get("KBU_LIVE_CACHE_SECONDS", "600"))
_CACHE_MAX = 1000
_cache: dict[tuple, tuple[float, object]] = {}
_inflight: dict[tuple, asyncio.Future] = {}


def ttl_cached(name: str, key=None):
    """Async fonksiyonun sonucunu CACHE_TTL saniye saklar; aynı anda gelen aynı istekleri tek isteğe indirir."""
    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            if CACHE_TTL <= 0:
                return await fn(*args, **kwargs)
            k = (name,) + (key(*args, **kwargs) if key else (args, tuple(sorted(kwargs.items()))))
            hit = _cache.get(k)
            if hit and time.monotonic() - hit[0] < CACHE_TTL:
                return copy.deepcopy(hit[1])  # çağıran sonucu değiştirebilir (ör. filtreleme)
            if k in _inflight:
                return copy.deepcopy(await asyncio.shield(_inflight[k]))
            fut = asyncio.get_running_loop().create_future()
            _inflight[k] = fut
            try:
                res = await fn(*args, **kwargs)
                if len(_cache) >= _CACHE_MAX:
                    for old in sorted(_cache, key=lambda x: _cache[x][0])[: _CACHE_MAX // 5]:
                        _cache.pop(old, None)
                _cache[k] = (time.monotonic(), res)
                fut.set_result(res)
                return copy.deepcopy(res)
            except BaseException as e:
                fut.set_exception(e)
                fut.exception()  # bekleyen yoksa "hiç alınmadı" uyarısını sustur
                raise
            finally:
                _inflight.pop(k, None)
        return wrapper
    return deco


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": config.USER_AGENT, "Accept-Language": "tr-TR,tr;q=0.9"},
        timeout=httpx.Timeout(40, connect=15), follow_redirects=True, verify=False,
    )


# ======================================================================== sayfa okuma

class NotAllowed(ValueError):
    pass


@ttl_cached("fetch", key=lambda url, c=None: (urls.normalize(url) or url,))
async def fetch_and_extract(url: str, c: httpx.AsyncClient | None = None):
    """URL'yi canlı çekip (Extracted, son_url, http_durum, içerik_türü, last_modified) döner."""
    n = urls.normalize(url)
    if not n or not urls.is_live_fetchable(n):
        raise NotAllowed("Sadece *.karabuk.edu.tr altındaki herkese açık sayfalar okunabilir.")
    own = c is None
    c = c or client()
    try:
        hdr = {"Referer": referer_for(n)}
        try:
            resp = await c.get(n, headers=hdr)
        except httpx.ConnectError:
            resp = await c.get(n.replace("https://", "http://", 1), headers=hdr)
        final = urls.normalize(str(resp.url)) or n
        if not urls.is_allowed_host(urls.host_of(final)):
            raise NotAllowed(f"Sayfa site dışına yönlendirdi: {final}")
        data = resp.content
        kind = sniff_kind(data, resp.headers.get("content-type", ""), urls.kind_from_url(final))
        if kind == "html":
            ex = extract_html(decode_html(resp, data), final)
        elif kind:
            ex = await asyncio.to_thread(extract_bytes, data, kind)
        else:
            ex = None
        return ex, final, resp.status_code, kind, resp.headers.get("last-modified")
    finally:
        if own:
            await c.aclose()


# ======================================================================== telefon rehberi

def _form_state(tree) -> dict:
    return {i.attributes.get("name"): i.attributes.get("value") or "" for i in tree.css("input[type=hidden]") if i.attributes.get("name")}


def _options(tree, select_id: str) -> list[tuple[str, str]]:
    return [(o.attributes.get("value"), clean_text(o.text())) for o in tree.css(f"#{select_id} option")]


def _parse_grid(tree) -> list[dict]:
    grid = tree.css_first("#ctl00_GridView1")
    if not grid:
        return []
    rows = grid.css("tr")
    if not rows:
        return []
    header = [clean_text(c.text()) for c in rows[0].css("th, td")]
    keys = {"Personel": "tip", "Ünvan": "unvan", "Ad": "ad", "Soyad": "soyad", "Görev": "gorev", "Birim": "birim",
            "Birim No": "telefon", "Dahili No": "dahili", "E-Mail": "eposta"}
    out = []
    for tr in rows[1:]:
        cells = [clean_text(c.text()) for c in tr.css("td")]
        if len(cells) != len(header) or tr.css_first("table"):
            continue  # sayfalama satırı
        out.append({keys.get(h, h): v for h, v in zip(header, cells)})
    return out


def _best_option(options: list[tuple[str, str]], wanted: str | None) -> tuple[str, str] | None:
    if not wanted:
        return None
    w = normalize_for_index(wanted)
    best, best_score = None, 0.0
    for val, name in options:
        if val == "ALL":
            continue
        n = normalize_for_index(name)
        if w == n:
            return val, name
        score = SequenceMatcher(None, w, n).ratio()
        if w in n:
            score = max(score, 0.75 + 0.25 * len(w) / max(1, len(n)))
        tokens = w.split()
        if tokens and all(any(t2.startswith(t[:5]) for t2 in n.split()) for t in tokens):
            score = max(score, 0.85)
        if score > best_score:
            best, best_score = (val, name), score
    return best if best_score >= 0.6 else None


@ttl_cached("rehber_options")
async def rehber_options() -> dict:
    async with client() as c:
        r = await c.get(REHBER_URL)
        tree = LexborHTMLParser(r.text)
        return {
            "birimler": [n for v, n in _options(tree, "ctl00_ddlPersonelBirim") if v != "ALL"],
            "unvanlar": [n for v, n in _options(tree, "ctl00_ddlPersonelUnvan") if v != "ALL"],
        }


@ttl_cached("rehber")
async def rehber_search(ad: str = "", soyad: str = "", birim: str = "", unvan: str = "", personel_tipi: str = "",
                        dahili: str = "", max_pages: int = 60) -> dict:
    """Telefon rehberini canlı sorgular. Birim/unvan adları en yakın seçeneğe eşlenir."""
    async with client() as c:
        r = await c.get(REHBER_URL)
        tree = LexborHTMLParser(r.text)
        birim_opt = _best_option(_options(tree, "ctl00_ddlPersonelBirim"), birim)
        unvan_opt = _best_option(_options(tree, "ctl00_ddlPersonelUnvan"), unvan)
        tip = {"akademik": "1", "idari": "2"}.get(fold(personel_tipi or ""), "ALL")
        form = _form_state(tree)
        form.update({
            "ctl00$ddlPersonelTip": tip,
            "ctl00$ddlPersonelUnvan": unvan_opt[0] if unvan_opt else "ALL",
            "ctl00$ddlPersonelBirim": birim_opt[0] if birim_opt else "ALL",
            "ctl00$txtPersonelAd": ad or "", "ctl00$txtPersonelSoyad": soyad or "",
            "ctl00$txtPersonelDahili": dahili or "", "ctl00$btnSearch": "ARA",
        })
        r = await c.post(REHBER_URL, data=form)
        tree = LexborHTMLParser(r.text)
        rows = _parse_grid(tree)
        page = 1
        while page < max_pages:
            nxt = f"Page${page + 1}"
            if f"&#39;{nxt}&#39;" not in r.text and f"'{nxt}'" not in r.text:
                break
            form = _form_state(tree)
            form.update({"__EVENTTARGET": "ctl00$GridView1", "__EVENTARGUMENT": nxt})
            for sel in ("ctl00$ddlPersonelTip", "ctl00$ddlPersonelUnvan", "ctl00$ddlPersonelBirim"):
                cur = tree.css_first(f"select[name='{sel}'] option[selected]")
                if cur:
                    form[sel] = cur.attributes.get("value")
            for inp in ("ctl00$txtPersonelAd", "ctl00$txtPersonelSoyad", "ctl00$txtPersonelDahili"):
                n = tree.css_first(f"input[name='{inp}']")
                form[inp] = n.attributes.get("value") or "" if n else ""
            r = await c.post(REHBER_URL, data=form)
            tree = LexborHTMLParser(r.text)
            new = _parse_grid(tree)
            if not new:
                break
            rows.extend(new)
            page += 1
    return {
        "kaynak": REHBER_URL,
        "sorgu_zamani": now_iso() + " UTC",
        "eslenen_birim": birim_opt[1] if birim_opt else None,
        "eslenen_unvan": unvan_opt[1] if unvan_opt else None,
        "uyari": (f"'{birim}' rehberdeki birimlerle eşleşmedi, tüm birimlerde arandı." if birim and not birim_opt else None),
        "sonuc_sayisi": len(rows),
        "kisiler": rows,
    }


# ======================================================================== duyurular / haberler

@ttl_cached("duyuru")
async def site_announcements(site_url: str, limit: int = 20) -> dict:
    """Bir birim sitesinin ana sayfasındaki duyuru ve etkinlik bloklarını okur."""
    n = urls.normalize(site_url if "://" in site_url else f"https://{site_url}")
    if not n or not urls.is_live_fetchable(n):
        raise NotAllowed("Sadece *.karabuk.edu.tr siteleri.")
    async with client() as c:
        r = await c.get(n)
        html = decode_html(r, r.content)
    tree = LexborHTMLParser(html)
    base = str(r.url)
    out = {}
    blocks = {
        "duyurular": ["#alanDuyuru", "#duyurularBlok", "[id*=Duyuru]", ".duyurular", "#duyurular"],
        "etkinlikler": ["#alanEtkinlikler", "#etkinliklerBlok", "[id*=Etkinlik]"],
        "haberler": ["#alanHaberler", "#haberlerBlok", "[id*=Haber]", ".haberler"],
    }
    for name, sels in blocks.items():
        items = []
        for sel in sels:
            for node in tree.css(sel):
                for a in node.css("a[href]"):
                    title = clean_text(a.text(separator=" "))
                    u = urls.normalize(a.attributes.get("href"), base)
                    if not u or len(title) < 6 or title.lower() in ("tümü", "tüm duyurular", "devamı", "daha fazla"):
                        continue
                    # tarih genelde linkin hemen önündeki kardeş/ebeveyn düğümde
                    date = None
                    # tarih genelde linkin ebeveyn/büyük-ebeveyn düğümünde ("23 Ekim", "Ekim 23")
                    for anc in (a.parent, a.parent.parent if a.parent else None):
                        if anc is None:
                            continue
                        ctx = clean_text(anc.text(separator=" ")).replace(title, " ")[:120]
                        m = re.search(r"(\d{1,2})\s*(Ocak|Şubat|Mart|Nisan|Mayıs|Haziran|Temmuz|Ağustos|Eylül|Ekim|Kasım|Aralık)(\s*20\d{2})?|"
                                      r"(Ocak|Şubat|Mart|Nisan|Mayıs|Haziran|Temmuz|Ağustos|Eylül|Ekim|Kasım|Aralık)\s*(\d{1,2})|"
                                      r"\b\d{1,2}[./]\d{1,2}[./]20\d{2}\b", ctx, re.I)
                        if m:
                            date = clean_text(m.group(0))
                            break
                    if not any(i["url"] == u for i in items):
                        items.append({"baslik": title, "tarih": date, "url": u})
            if items:
                break
        if items:
            out[name] = items[:limit]
    if not out:
        # Şablon tanınmadı: sayfadaki duyuru/haber linklerini topla
        ex = extract_html(html, base)
        out["linkler"] = [{"baslik": t, "url": u} for u, t in ex.links
                          if t and len(t) > 15 and re.search(r"icerikGoster|duyuru|haber|Sayfa", u, re.I)][:limit]
    return {"kaynak": base, "sorgu_zamani": now_iso() + " UTC", **out}


# ======================================================================== bağlantı bulucu

@ttl_cached("links")
async def find_links(page_url: str, pattern: str) -> list[tuple[str, str]]:
    async with client() as c:
        r = await c.get(page_url)
        ex = extract_html(decode_html(r, r.content), str(r.url))
    rx = re.compile(pattern, re.I)
    seen, out = set(), []
    for u, t in ex.links:
        if (rx.search(t) or rx.search(u)) and u not in seen:
            seen.add(u)
            out.append((u, t))
    return out

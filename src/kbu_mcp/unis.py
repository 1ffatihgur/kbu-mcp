"""UNIS (unis.karabuk.edu.tr — Akademik Veri Yönetim Sistemi) akademisyen profilleri.

UNIS'in arama sayfaları sonsuz filtre kombinasyonu ürettiği için genel tarayıcıyla gezilmez. Bunun yerine
sitenin kendi DataTables uç noktasından:
  1. tüm akademisyenlerin listesi (ad, unvan, idari görevler, birim, yayın/proje/tez sayıları),
  2. her akademisyenin profil sayfası (araştırma alanları, atıf/h-index özetleri),
  3. her akademisyenin bütün çalışmaları (yayın, proje, ödül, patent, tez danışmanlığı...)
alınır ve akademisyen başına TEK Markdown belge olarak indekslenir. Menü / üst bilgi / alt bilgi alınmaz.
"""

import asyncio
import html as htmlmod
import logging
import re
import time
from collections import defaultdict
from datetime import date

import httpx
from selectolax.lexbor import LexborHTMLParser

from . import config, db
from .crawler import now_iso
from .extract import Extracted, _node_text, clean_text
from .indexer import content_hash, store

log = logging.getLogger("kbu.unis")

BASE = f"https://{config.UNIS_HOST}/"
PEOPLE_GRID = BASE + "?act=guest&act2=search&Tur=Kisi&grid=1&table_id=SolrRecord"
WORKS_GRID = (BASE + "?act=guest&act2=akademik&act3=akademisyen_detay&act4=calismalar&act5={u}&Tur=Yayin"
              "&act7=calismalar&act8={u}&grid=1&table_id=SolrRecord")
PAGE = 100
DELAY = 0.4      # saniye, aynı akademisyenin istekleri arası
CONCURRENCY = 3  # aynı anda işlenen akademisyen (genel tarayıcının host başı sınırıyla aynı)

TYPE_TITLES = {"Yayın": "Yayınlar", "Proje": "Projeler", "Tez": "Tez Danışmanlıkları", "Ödül": "Ödüller",
               "Patent": "Patentler / Fikri Mülkiyet", "Sanatsal Etkinlik": "Sanatsal Etkinlikler", "Tasarım": "Tasarımlar"}


def _cell_text(h: str) -> list[str]:
    """Izgara hücresindeki HTML → boş olmayan metin parçaları."""
    h = re.sub(r"(?s)<svg.*?</svg>", "", h)
    parts = [re.sub(r"\s+", " ", htmlmod.unescape(p)).strip() for p in re.split(r"<[^>]+>", h)]
    out: list[str] = []
    for p in parts:
        if not p.strip(" ,"):
            if "," in p and out:
                out[-1] += ","
            continue
        if out and (p.startswith(",") or out[-1].endswith(",")):   # yazar listesi: "A, " + "B"
            out[-1] = (out[-1] + " " + p).replace(" ,", ",")
        elif out and re.fullmatch(r"\d+", out[-1]) and not re.fullmatch(r"(19|20)\d{2}", out[-1]):  # "282" + "Yayın"
            out[-1] = f"{out[-1]} {p}"
        else:
            out.append(p)
    return out


async def _grid(client: httpx.AsyncClient, url: str, start: int) -> dict:
    for attempt in range(4):
        try:
            r = await client.post(url, data={"sEcho": 1, "iDisplayStart": start, "iDisplayLength": PAGE},
                                  headers={"X-Requested-With": "XMLHttpRequest", "Referer": BASE})
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as e:
            if attempt == 3:
                raise
            log.debug("UNIS ızgara hatası (%s), tekrar: %s", url, e)
            await asyncio.sleep(3 * (attempt + 1))
    return {}


async def list_academics(client: httpx.AsyncClient, limit: int | None = None) -> list[dict]:
    """Tüm akademisyenler (arama ızgarasında kişiler diğer kayıt türlerinden önce gelir)."""
    out, start = [], 0
    while True:
        d = await _grid(client, PEOPLE_GRID, start)
        rows = d.get("aaData") or []
        for r in rows:
            if not str(r[2]).startswith("Kisi-"):
                return out
            m = re.search(r"akademisyen-detay/([^'\"\\/]+)", str(r))
            if not m:
                continue
            out.append({"kullanici": m.group(1), "ad": htmlmod.unescape(str(r[3])).strip(), "ozet": _cell_text(str(r[5]))})
            if limit and len(out) >= limit:
                return out
        if not rows:
            return out
        start += PAGE
        await asyncio.sleep(DELAY)


async def works(client: httpx.AsyncClient, user: str) -> list[dict]:
    url = WORKS_GRID.format(u=user)
    out, start = [], 0
    while True:
        d = await _grid(client, url, start)
        rows = d.get("aaData") or []
        for r in rows:
            parts = _cell_text(str(r[5]))
            tur = htmlmod.unescape(str(r[4])).strip()
            title = htmlmod.unescape(str(r[3])).strip()
            rest = [p for p in parts if p not in (tur, title)]
            year = next((p for p in rest if re.fullmatch(r"(19|20)\d{2}", p)), None)
            m = re.search(r"href='([^']+-detay/[^']+)'", str(r[5]))
            out.append({"tur": tur, "baslik": title, "yil": year, "detay": " · ".join(p for p in rest if p != year),
                        "url": BASE + m.group(1) if m else None})
        total = int(d.get("iTotalDisplayRecords") or 0)
        start += PAGE
        if not rows or start >= total:
            return out
        await asyncio.sleep(DELAY)


def profile_summary(html: str) -> str:
    """Profil sayfasının özeti: unvan/ad, birim, idari görev, sayılar, araştırma alanları, atıf özetleri."""
    tree = LexborHTMLParser(html)
    root = tree.css_first(".content-wrapper") or tree.body
    if root is None:
        return ""
    for sel in ("script", "style", "nav", "aside", ".layout-navbar", ".layout-menu", ".menu", "footer",
                ".content-footer", "form", "select", ".modal", "svg"):
        for n in root.css(sel):
            n.decompose()
    text = _node_text(root)
    text = re.split(r"\n(Son Akademik Aktiviteler|Akademik Çalışmalar)\n", text)[0]
    text = re.sub(r"(?s)Birleşmiş Milletler Sürdürülebilir.*?(?=\n#|\Z)", "", text)
    text = re.sub(r"(?s)Profil Görüntülenmesi.*?Son 30 gün", "", text)
    text = re.sub(r"\n-\s*\n", "\n", text)
    # "80\nYayın" → "80 Yayın"
    text = re.sub(r"(?m)^(\d+)\n(?=[A-Za-zÇĞİÖŞÜçğıöşü-])", r"\1 ", text)
    return clean_text(text)


def build_document(person: dict, summary: str, items: list[dict]) -> Extracted:
    name = person["ad"]
    lines = [f"# {name} — UNIS Akademik Profil", "", f"Kaynak: {BASE}akademisyen-detay/{person['kullanici']}", ""]
    if person.get("ozet"):
        lines += ["## Özet", " · ".join(person["ozet"]), ""]
    if summary:
        lines += ["## Profil", summary, ""]
    sections = [(None, "\n".join(lines))]
    by_type: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        by_type[it["tur"]].append(it)
    for tur, lst in sorted(by_type.items(), key=lambda kv: (kv[0] != "Yayın", kv[0])):
        lst.sort(key=lambda it: it["yil"] or "", reverse=True)
        body = [f"## {name} — {TYPE_TITLES.get(tur, tur)} ({len(lst)} adet)"]
        for it in lst:
            body.append(f"- {it['yil'] or 's.t.'} · {it['baslik']}" + (f" · {it['detay']}" if it["detay"] else ""))
        sections.append((None, "\n".join(body)))
    ex = Extracted()
    ex.sections = sections
    ex.text = "\n\n".join(t for _, t in sections)
    ex.title = f"{name} — UNIS akademik profil"
    unit = next((p for p in person.get("ozet", []) if re.search(r"Fakültesi|Yüksekokulu|Konservatuvar|Rektörlük|Enstitüsü", p)), "")
    ex.site_title = f"UNIS Akademik Veri Yönetim Sistemi · {unit}".strip(" ·")
    ex.content_date = date.today().isoformat()
    return ex


async def refresh_unis(conn, limit: int | None = None, users: list[str] | None = None, heartbeat=None) -> dict:
    """UNIS profillerini çeker ve indeksler. `users` verilirse yalnızca o kullanıcı adları (örnek test)."""
    stats = defaultdict(int)
    started = time.time()
    async with httpx.AsyncClient(headers={"User-Agent": config.USER_AGENT, "Accept-Language": "tr-TR,tr;q=0.9"},
                                 timeout=60, follow_redirects=True, verify=False) as client:
        people = await list_academics(client, limit=None if users else limit)
        if users:
            people = [p for p in people if p["kullanici"] in users]
        log.info("UNIS: %d akademisyen", len(people))
        seen = {f"{BASE}akademisyen-detay/{p['kullanici']}" for p in people}
        sem = asyncio.Semaphore(CONCURRENCY)
        done = 0

        async def fetch(person):
            url = f"{BASE}akademisyen-detay/{person['kullanici']}"
            async with sem:
                r = await client.get(url, headers={"Referer": BASE})
                summary = profile_summary(r.text) if r.status_code == 200 else ""
                await asyncio.sleep(DELAY)
                items = await works(client, person["kullanici"])
                await asyncio.sleep(DELAY)
            return url, person, summary, items

        tasks = [asyncio.create_task(fetch(p)) for p in people]
        for fut in asyncio.as_completed(tasks):
            try:
                url, person, summary, items = await fut
            except Exception as e:
                stats["hata"] += 1
                log.warning("UNIS profili alınamadı: %r", e)
                continue
            ex = build_document(person, summary, items)
            h = content_hash(ex.text)
            row = conn.execute("SELECT id, content_hash FROM docs WHERE url=?", (url,)).fetchone()
            if row is None:
                row = conn.execute(
                    "INSERT INTO docs(url, host, source, kind, status) VALUES(?,?, 'unis', 'html', 'ok') RETURNING id, content_hash",
                    (url, config.UNIS_HOST)).fetchone()
            if row["content_hash"] != h:
                n = store(conn, row["id"], url, ex)
                stats["parca"] += n
                stats["guncellenen"] += 1
            conn.execute(
                "UPDATE docs SET status='ok', source='unis', kind='html', final_url=?, title=?, site_title=?, content_hash=?, "
                "content_date=?, text_len=?, parser=?, error=NULL, fail_count=0, fetched_at=?, checked_at=? WHERE id=?",
                (url, ex.title, ex.site_title, h, ex.content_date, len(ex.text), config.PARSER_VERSION, now_iso(), now_iso(), row["id"]))
            conn.commit()
            done += 1
            stats["profil"] += 1
            stats["calisma"] += len(items)
            if heartbeat and done % 10 == 0:
                heartbeat()
            if done % 50 == 0:
                log.info("UNIS %d/%d (%.1f profil/dk)", done, len(people), done / max(1, time.time() - started) * 60)
        if not users and not limit and len(seen) > 100:  # listeden çıkan akademisyenlerin profili kaldırılır
            for r in conn.execute("SELECT id, url FROM docs WHERE source='unis' AND status='ok'").fetchall():
                if r["url"] not in seen:
                    db.delete_chunks(conn, r["id"])
                    conn.execute("UPDATE docs SET status='removed', checked_at=? WHERE id=?", (now_iso(), r["id"]))
                    stats["kaldirilan"] += 1
            # eski genel tarayıcıdan kalan UNIS arama/filtre sayfaları
            conn.execute("UPDATE docs SET status='skipped', error='UNIS modülü kullanılıyor' "
                         "WHERE host=? AND url NOT LIKE ? AND status IN ('ok','duplicate')", (config.UNIS_HOST, BASE + "akademisyen-detay/%"))
            db.set_meta(conn, "unis_refreshed", now_iso())
            conn.commit()
    return dict(stats, saniye=int(time.time() - started))

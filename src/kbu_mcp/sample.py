"""Küçük ölçekli doğruluk testi: tam taramadan önce birkaç adresin nasıl parse edildiğini gösterir.

    kbu sample                       # 5 site sayfası + 10 UNIS hocası (farklı birimlerden) + 10 KBÜ Medya haberi
    kbu sample --url https://...     # kendi seçtiğiniz adresler

Çıktı: data/sample/*.md (her belgenin indekse girecek Markdown hali) ve data/sample/RAPOR.md
(başlık, tarih, uzunluk, parça sayısı ve menü / üst-alt bilgi kalıntısı kontrolü). Veritabanına yazılmaz.
"""

import asyncio
import re
from pathlib import Path

import httpx

from . import config, urls
from .crawler import decode_html, referer_for
from .extract import extract_bytes, extract_html, sniff_kind
from .indexer import split_text

DEFAULT_SITE_URLS = [
    "https://www.karabuk.edu.tr/",
    "https://bbf.karabuk.edu.tr/bilgisayar/",
    "https://sks.karabuk.edu.tr/icerikGoster.aspx?BA=index.aspx&K=S&id=76",  # Beslenme Hizmetleri (yemek saatleri)
    "https://oidb.karabuk.edu.tr/",
    "https://www.karabuk.edu.tr/1-54-Sayfa-Rektor-Yardimcilari",
]
# Menü / üst bilgi / alt bilgi sızdığını gösteren kalıplar
LEAK_RE = re.compile(r"Tüm Hakları Saklıdır|Copyright|Sosyal Medya|Linki Kopyala|Okunma Sayısı|Anasayfa\s*\n|"
                     r"SDG Kategorileri|Talent Yazılım|Paylaş\n|Çerez|KVKK Aydınlatma", re.I)


def _slug(u: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", u.lower().split("://", 1)[-1])[:80].strip("-")


async def _medya_urls(client, n: int) -> list[str]:
    r = await client.get(f"https://{config.MEDYA_HOST}/")
    found = []
    for m in re.finditer(r'href="(/[a-z0-9-]{25,})"', r.text):
        u = f"https://{config.MEDYA_HOST}{m.group(1)}"
        if u not in found:
            found.append(u)
    return found[:n]


async def _unis_users(client, n: int) -> list[dict]:
    from .unis import list_academics

    people = await list_academics(client)
    picked, units = [], set()
    for p in people:  # farklı birimlerden seç
        unit = next((x for x in p["ozet"] if "Fakülte" in x or "Yüksekokul" in x), "")
        if unit not in units:
            units.add(unit)
            picked.append(p)
        if len(picked) >= n:
            break
    return picked


async def run_sample(extra_urls: list[str] | None = None, n_unis: int = 10, n_medya: int = 10) -> Path:
    out = config.DATA_DIR / "sample"
    out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("*.md"):
        f.unlink()
    report = ["# Örnek parse raporu", "", "| # | Kaynak | Başlık | Tarih | Karakter | Parça | Kalıntı | Dosya |", "|---|---|---|---|---|---|---|---|"]
    rows = []
    async with httpx.AsyncClient(headers={"User-Agent": config.USER_AGENT}, timeout=60, follow_redirects=True, verify=False) as client:
        targets = [("site", u) for u in (extra_urls or DEFAULT_SITE_URLS)]
        if not extra_urls:
            targets += [("medya", u) for u in await _medya_urls(client, n_medya)]
        for kaynak, u in targets:
            try:
                r = await client.get(u, headers={"Referer": referer_for(u)})
                kind = sniff_kind(r.content, r.headers.get("content-type", ""), urls.kind_from_url(u))
                ex = extract_html(decode_html(r, r.content), str(r.url)) if kind == "html" else extract_bytes(r.content, kind)
                rows.append((kaynak, u, ex.title, ex.content_date, ex.text))
            except Exception as e:
                rows.append((kaynak, u, f"HATA: {type(e).__name__}: {e}", None, ""))
        if not extra_urls and n_unis:
            from .unis import build_document, profile_summary, works

            for p in await _unis_users(client, n_unis):
                u = f"https://{config.UNIS_HOST}/akademisyen-detay/{p['kullanici']}"
                try:
                    r = await client.get(u)
                    ex = build_document(p, profile_summary(r.text), await works(client, p["kullanici"]))
                    rows.append(("unis", u, ex.title, ex.content_date, ex.text))
                except Exception as e:
                    rows.append(("unis", u, f"HATA: {type(e).__name__}: {e}", None, ""))
    for i, (kaynak, u, title, d, text) in enumerate(rows, start=1):
        fname = f"{i:02d}_{kaynak}_{_slug(u)[:50]}.md"
        (out / fname).write_text(f"<!-- {u} -->\n\n{text}\n", encoding="utf-8")
        leaks = sorted({m.group(0).strip() for m in LEAK_RE.finditer(text)})
        report.append(f"| {i} | {kaynak} | {(title or '').replace('|', '/')[:70]} | {d or '-'} | {len(text)} | "
                      f"{len(split_text(text))} | {', '.join(leaks) or 'yok'} | [{fname}]({fname}) |")
    (out / "RAPOR.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    return out


def main(extra_urls=None) -> Path:
    return asyncio.run(run_sample(extra_urls))

"""Taranmış (görüntü) PDF'ler için OCR. Tesseract gerektirir (isteğe bağlı).

Kurulum:  Windows `winget install UB-Mannheim.TesseractOCR` · macOS `brew install tesseract` ·
Debian/Ubuntu `sudo apt install tesseract-ocr`.
Türkçe model (tur.traineddata) yoksa resmi tesseract-ocr/tessdata deposundan veri klasörüne indirilir.
"""

import logging
import os
import shutil
import sqlite3
import time
from pathlib import Path

import httpx

from . import config, db
from .crawler import now_iso
from .extract import Extracted, clean_text, find_date
from .indexer import content_hash, store

log = logging.getLogger("kbu.ocr")

_CANDIDATES = [
    r"C:\Program Files\Tesseract-OCR",
    r"C:\Program Files (x86)\Tesseract-OCR",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR"),
    "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin",
]
_SYSTEM_TESSDATA = [
    "/opt/homebrew/share/tessdata", "/usr/local/share/tessdata", "/usr/share/tesseract-ocr/5/tessdata",
    "/usr/share/tesseract-ocr/4.00/tessdata", "/usr/share/tessdata",
]
TESSDATA_URL = "https://github.com/tesseract-ocr/tessdata/raw/main/{lang}.traineddata"


def _tesseract_dir() -> str | None:
    exe = shutil.which("tesseract")
    if exe:
        return str(Path(exe).parent)
    for d in _CANDIDATES:
        if (Path(d) / "tesseract.exe").exists() or (Path(d) / "tesseract").exists():
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")  # PyMuPDF tesseract'ı PATH'te arar
            return d
    return None


def find_tessdata(auto_download: bool = False) -> str | None:
    """tur.traineddata içeren klasör (Tesseract kurulu değilse None).
    Öncelik: TESSDATA_PREFIX, veri klasörü/tessdata, Tesseract kurulumu, sistem klasörleri."""
    tdir = _tesseract_dir()
    if not tdir:
        return None
    local = config.DATA_DIR / "tessdata"
    install_td = [str(Path(tdir) / "tessdata"), str(Path(tdir).parent / "share" / "tessdata")] + _SYSTEM_TESSDATA
    for td in [os.environ.get("TESSDATA_PREFIX"), str(local)] + install_td:
        if td and (Path(td) / "tur.traineddata").exists():
            return td
    if not auto_download:
        return None
    # Türkçe model yok: veri klasörüne tur + eng (+osd) hazırla
    try:
        local.mkdir(parents=True, exist_ok=True)
        for lang in ("eng", "osd"):
            src = next((Path(td) / f"{lang}.traineddata" for td in install_td if (Path(td) / f"{lang}.traineddata").exists()), None)
            if src and not (local / src.name).exists():
                shutil.copy2(src, local / src.name)
        for lang in ("tur", "eng"):
            if not (local / f"{lang}.traineddata").exists():
                log.info("%s.traineddata indiriliyor", lang)
                r = httpx.get(TESSDATA_URL.format(lang=lang), follow_redirects=True, timeout=300)
                r.raise_for_status()
                (local / f"{lang}.traineddata").write_bytes(r.content)
        return str(local)
    except Exception as e:
        log.warning("Türkçe OCR modeli indirilemedi: %r", e)
        return None


def ocr_pdf(data: bytes, tessdata: str, max_pages: int = 200) -> Extracted:
    import pymupdf

    ex = Extracted()
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        ex.pages = doc.page_count
        for i, page in enumerate(doc, start=1):
            if i > max_pages:
                break
            txt = clean_text(page.get_text("text", sort=True))
            if len(txt) < 30:
                tp = page.get_textpage_ocr(language="tur+eng", dpi=200, full=True, tessdata=tessdata)
                txt = clean_text(page.get_text("text", textpage=tp, sort=True))
            if txt:
                ex.sections.append((i, txt))
    ex.text = "\n\n".join(t for _, t in ex.sections)
    ex.title = ex.text.split("\n", 1)[0][:150] if ex.text else None
    ex.content_date = find_date(ex.text)
    return ex


def _ocr_worker(args):
    """Ayrı süreçte çalışır: (doc_id, url) → (doc_id, Extracted | hata metni)."""
    doc_id, url, tessdata = args
    os.environ["OMP_THREAD_LIMIT"] = "1"  # paralel süreçlerde Tesseract iş parçacığı patlamasını önle
    find_tessdata()  # PATH ayarı
    try:
        with httpx.Client(headers={"User-Agent": config.USER_AGENT, "Referer": f"https://{httpx.URL(url).host}/index.aspx"},
                          timeout=120, follow_redirects=True, verify=False) as c:
            r = c.get(url)
        if r.status_code >= 400 or not r.content.startswith(b"%PDF"):
            return doc_id, f"indirilemedi (HTTP {r.status_code})"
        return doc_id, ocr_pdf(r.content, tessdata)
    except Exception as e:
        return doc_id, f"{type(e).__name__}: {e}"[:200]


def run_ocr(conn, limit: int | None = None, workers: int | None = None, heartbeat=None) -> dict:
    from concurrent.futures import ProcessPoolExecutor, as_completed

    tessdata = find_tessdata(auto_download=True)
    if not tessdata:
        raise SystemExit("Tesseract bulunamadı. Kurulum: Windows `winget install UB-Mannheim.TesseractOCR`, "
                         "macOS `brew install tesseract`, Linux `sudo apt install tesseract-ocr`")
    rows = {r["id"]: r for r in conn.execute(
        "SELECT * FROM docs WHERE status='ok' AND needs_ocr=1 AND kind='pdf' ORDER BY id" + (f" LIMIT {int(limit)}" if limit else "")
    ).fetchall()}
    stats = {"toplam": len(rows), "basarili": 0, "hata": 0}
    if not rows:
        return stats
    workers = workers or max(1, min(16, (os.cpu_count() or 4) - 2))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(_ocr_worker, (i, r["final_url"] or r["url"], tessdata)) for i, r in rows.items()]
        for n, fut in enumerate(as_completed(futs), start=1):
            doc_id, ex = fut.result()
            r = rows[doc_id]
            if isinstance(ex, str) or len(ex.text) < 50:
                stats["hata"] += 1
                conn.execute("UPDATE docs SET needs_ocr=3 WHERE id=?", (doc_id,))  # OCR denendi, metin çıkmadı
                conn.commit()
                log.warning("OCR başarısız (%d/%d) %s: %s", n, len(rows), r["url"], ex if isinstance(ex, str) else "metin yok")
                continue
            for attempt in range(10):  # aynı anda çalışan tarayıcı kısa süreli kilit tutabilir
                try:
                    store(conn, doc_id, r["final_url"] or r["url"], ex, r["anchor_text"])
                    conn.execute(
                        "UPDATE docs SET needs_ocr=2, text_len=?, pages=?, content_hash=?, title=COALESCE(title, ?), "
                        "content_date=COALESCE(content_date, ?), checked_at=? WHERE id=?",
                        (len(ex.text), ex.pages, content_hash(ex.text), ex.title, ex.content_date, now_iso(), doc_id),
                    )
                    conn.commit()
                    break
                except sqlite3.OperationalError as e:
                    conn.rollback()
                    if "locked" not in str(e) or attempt == 9:
                        raise
                    time.sleep(15)
            stats["basarili"] += 1
            if heartbeat:
                heartbeat()
            if n % 20 == 0:
                log.info("OCR ilerleme %d/%d (başarılı %d)", n, len(rows), stats["basarili"])
    return stats

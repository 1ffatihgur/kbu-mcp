"""Çıkarılan metni parçalara bölüp veritabanına ve FTS indeksine yazar."""

import hashlib
import re

from . import config, db
from .extract import Extracted
from .textnorm import normalize_for_index
from .units import unit_label


def split_text(text: str, size: int = config.CHUNK_CHARS, overlap: int = config.CHUNK_OVERLAP) -> list[str]:
    """Paragraf sınırlarına saygı göstererek ~size karakterlik parçalar."""
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    paras = re.split(r"\n{1,}", text)
    chunks, cur = [], ""
    for p in paras:
        p = p.strip()
        if not p:
            continue
        while len(p) > size:  # çok uzun paragraf: cümle/kelime sınırından böl
            cut = p.rfind(". ", 0, size)
            cut = cut + 1 if cut > size // 2 else (p.rfind(" ", 0, size) if p.rfind(" ", 0, size) > 0 else size)
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(p[:cut].strip())
            p = p[cut:].strip()
        if len(cur) + len(p) + 1 > size and cur:
            chunks.append(cur)
            tail = cur[-overlap:]
            sp = tail.find(" ")
            cur = (tail[sp + 1:] if sp >= 0 else tail) + "\n" + p
        else:
            cur = f"{cur}\n{p}" if cur else p
    if cur:
        chunks.append(cur)
    return chunks


PHONE_RE = re.compile(r"(\+?90[\s(]*)?\(?0?\s?370\)?[\s.-]*\d{3}[\s.-]*\d{2}[\s.-]*\d{2}|444\s?0\s?478|\bdahili\b", re.I)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.karabuk\.edu\.tr|[\w.+-]+@karabuk\.edu\.tr", re.I)


def body_for_index(text: str) -> str:
    """FTS'e yazılan gövde: normalize metin + numara/e-posta varsa aranabilir etiketler.
    (Bölüm sayfalarında numaralar çoğu zaman 'Tel' etiketi olmadan yazılıyor.)"""
    body = normalize_for_index(text)
    tags = []
    if PHONE_RE.search(text):
        tags.append("telefon tel numara dahili iletisim")
    if EMAIL_RE.search(text):
        tags.append("eposta mail iletisim")
    return body + (" " + " ".join(tags) if tags else "")


def content_hash(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", text).encode("utf-8")).hexdigest()


def store(conn, doc_id: int, url: str, ex: Extracted, anchor_text: str | None = None) -> int:
    """Belgenin eski parçalarını silip yenilerini yazar. Parça sayısını döner."""
    db.delete_chunks(conn, doc_id)
    unit = unit_label(url, ex.site_title)
    title = ex.title or anchor_text or ex.site_title or url
    # Dosyalarda link metni (ör. "2026-2027 Yemek Menüsü") başlıktan çoğu zaman daha açıklayıcı.
    title_for_index = " ".join(x for x in {title, anchor_text or ""} if x)
    n = 0
    for page, sec_text in ex.sections:
        for part in split_text(sec_text):
            cur = conn.execute(
                "INSERT INTO chunks(doc_id, ord, page, text) VALUES(?,?,?,?)", (doc_id, n, page, part)
            )
            conn.execute(
                "INSERT INTO chunks_fts(rowid, title, unit, body) VALUES(?,?,?,?)",
                (cur.lastrowid, normalize_for_index(title_for_index), normalize_for_index(unit), body_for_index(part)),
            )
            n += 1
    return n


def rebuild_fts(conn) -> int:
    """FTS indeksini mevcut parçalardan yeniden kurar (siteyi yeniden taramadan)."""
    conn.execute("DELETE FROM chunks_fts")
    rows = conn.execute(
        """SELECT c.id, c.text, d.url, d.final_url, d.title, d.anchor_text, d.site_title
           FROM chunks c JOIN docs d ON d.id = c.doc_id"""
    )
    batch, n = [], 0
    for r in rows:
        url = r["final_url"] or r["url"]
        title = r["title"] or r["anchor_text"] or r["site_title"] or url
        title_for_index = " ".join(x for x in {title, r["anchor_text"] or ""} if x)
        batch.append((r["id"], normalize_for_index(title_for_index), normalize_for_index(unit_label(url, r["site_title"])), body_for_index(r["text"])))
        if len(batch) >= 2000:
            conn.executemany("INSERT INTO chunks_fts(rowid, title, unit, body) VALUES(?,?,?,?)", batch)
            n += len(batch)
            batch = []
    if batch:
        conn.executemany("INSERT INTO chunks_fts(rowid, title, unit, body) VALUES(?,?,?,?)", batch)
        n += len(batch)
    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('optimize')")
    conn.commit()
    return n

"""v1 SQLite indeksini (data/kbu.db) PostgreSQL'e taşır — siteyi yeniden taramadan.

    kbu migrate-sqlite [data/kbu.db]

Belgeler, parçalar (tam metin indeksi yeniden hesaplanır), rehber ve birimler aktarılır.
Embedding'ler sonra `kbu embed` ile doldurulur.
"""

import logging
import sqlite3
import time

from . import config, db
from .indexer import TSV_SQL, body_for_index, tags_for
from .textnorm import normalize_for_index
from .units import unit_label

log = logging.getLogger("kbu.migrate")

DOC_COLS = ["id", "url", "final_url", "host", "kind", "status", "depth", "parent_url", "anchor_text", "site_title",
            "title", "http_status", "etag", "last_modified", "content_hash", "content_date", "text_len", "pages",
            "needs_ocr", "error", "fail_count", "first_seen", "fetched_at", "checked_at"]


def migrate(path=None) -> dict:
    src = sqlite3.connect(f"file:{path or config.SQLITE_PATH}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    conn = db.connect()
    db.init(conn)
    if conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]:
        raise SystemExit("PostgreSQL'de zaten belge var; taşıma yalnızca boş veritabanına yapılır.")
    t0 = time.time()
    stats = {}

    rows = src.execute(f"SELECT {', '.join(DOC_COLS)} FROM docs").fetchall()
    # v1 sürümünde geçici bağlantı hatası indekslenmiş belgeleri 'error'a düşürüyordu: parçası olanları geri al
    has_chunks = {r[0] for r in src.execute("SELECT DISTINCT doc_id FROM chunks")}
    batch = []
    for r in rows:
        d = dict(r)
        if d["status"] == "error" and d["id"] in has_chunks:
            d["status"] = "ok"
        d["source"] = db.source_for(d["host"], d["url"])
        d["parser"] = 1
        batch.append(d)
    cols = DOC_COLS + ["source", "parser"]
    conn.executemany(f"INSERT INTO docs({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
                     [tuple(d[c] for c in cols) for d in batch])
    conn.execute("SELECT setval('docs_id_seq', (SELECT MAX(id) FROM docs))")
    conn.commit()
    stats["belge"] = len(batch)
    log.info("belgeler aktarıldı: %d", len(batch))

    docs = {r["id"]: r for r in batch}
    n, buf = 0, []
    insert = f"INSERT INTO chunks(id, doc_id, ord, page, text, unit_n, tags, tsv) VALUES(?,?,?,?,?,?,?, {TSV_SQL})"
    for c in src.execute("SELECT id, doc_id, ord, page, text FROM chunks ORDER BY id"):
        d = docs.get(c["doc_id"])
        if not d:
            continue
        url = d["final_url"] or d["url"]
        title = d["title"] or d["anchor_text"] or d["site_title"] or url
        title_n = normalize_for_index(" ".join(x for x in {title, d["anchor_text"] or ""} if x))
        unit_n = normalize_for_index(unit_label(url, d["site_title"]))
        tags = tags_for(c["text"])
        buf.append((c["id"], c["doc_id"], c["ord"], c["page"], c["text"], unit_n, tags, title_n, unit_n,
                    body_for_index(c["text"], tags)))
        if len(buf) >= 5000:
            conn.executemany(insert, buf)
            conn.commit()
            n += len(buf)
            buf = []
            log.info("parçalar: %d", n)
    if buf:
        conn.executemany(insert, buf)
        n += len(buf)
    conn.execute("SELECT setval('chunks_id_seq', (SELECT MAX(id) FROM chunks))")
    conn.commit()
    stats["parca"] = n

    people = src.execute("SELECT tip, unvan, ad, soyad, gorev, birim, telefon, dahili, eposta, search, updated_at FROM people").fetchall()
    conn.executemany("INSERT INTO people(tip, unvan, ad, soyad, gorev, birim, telefon, dahili, eposta, search, updated_at) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?)", [tuple(p) for p in people])
    units = src.execute("SELECT name, source, code, url FROM units").fetchall()
    conn.executemany("INSERT INTO units(name, source, code, url) VALUES(?,?,?,?) ON CONFLICT DO NOTHING", [tuple(u) for u in units])
    for k, v in src.execute("SELECT key, value FROM meta"):
        db.set_meta(conn, k, v)
    conn.commit()
    conn.execute("ANALYZE")
    conn.commit()
    stats.update(rehber=len(people), birim=len(units), saniye=int(time.time() - t0))
    return stats

"""PostgreSQL (+ pgvector) şeması ve yardımcıları.

docs   : keşfedilen her URL (sayfa veya dosya), kaynağı ve tarama durumu — aynı zamanda tarama kuyruğu
chunks : belgelerden çıkarılmış Markdown parçalar; tam metin (tsvector) ve anlamsal (halfvec) indeks
people : telefon rehberinden çekilen personel kayıtları
units  : rehberdeki birimler ve keşfedilen alt siteler
jobs   : iş kuyruğu (site tarama, UNIS, KBÜ Medya, güncelleme...) — kesilirse kaldığı yerden sürer
meta   : son güncelleme zamanları vb.

Kod SQLite döneminden kalma `?` parametre biçimini kullanır; Conn bunu psycopg'nin `%s` biçimine çevirir.
"""

import re
from contextlib import contextmanager
from functools import lru_cache

import psycopg

from . import config

SCHEMA = f"""
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS docs (
    id            BIGSERIAL PRIMARY KEY,
    url           TEXT NOT NULL UNIQUE,
    final_url     TEXT,
    host          TEXT NOT NULL,
    source        TEXT NOT NULL DEFAULT 'site',   -- site | unis | medya | bologna
    kind          TEXT,                           -- html, pdf, docx, xlsx, ...
    status        TEXT NOT NULL DEFAULT 'queued', -- queued, recheck, ok, error, removed, skipped, duplicate
    depth         INTEGER NOT NULL DEFAULT 0,
    parent_url    TEXT,
    anchor_text   TEXT,               -- bu URL'ye verilen link metni (dosyalar için çok değerli)
    site_title    TEXT,               -- <title>: genelde birim adı
    title         TEXT,               -- sayfanın/belgenin kendi başlığı
    http_status   INTEGER,
    etag          TEXT,
    last_modified TEXT,
    content_hash  TEXT,
    content_date  TEXT,               -- içerikten / Last-Modified'dan çıkarılan tarih (ISO)
    text_len      INTEGER,
    pages         INTEGER,
    needs_ocr     INTEGER NOT NULL DEFAULT 0,
    parser        INTEGER NOT NULL DEFAULT 0,     -- çıkarıcı sürümü (config.PARSER_VERSION)
    error         TEXT,
    fail_count    INTEGER NOT NULL DEFAULT 0,
    first_seen    TEXT NOT NULL DEFAULT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS'),
    fetched_at    TEXT,
    checked_at    TEXT
);
CREATE INDEX IF NOT EXISTS docs_status ON docs(status);
CREATE INDEX IF NOT EXISTS docs_host ON docs(host);
CREATE INDEX IF NOT EXISTS docs_hash ON docs(content_hash);
CREATE INDEX IF NOT EXISTS docs_final ON docs(final_url);

CREATE TABLE IF NOT EXISTS chunks (
    id        BIGSERIAL PRIMARY KEY,
    doc_id    BIGINT NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
    ord       INTEGER NOT NULL,
    page      INTEGER,
    text      TEXT NOT NULL,      -- Markdown
    unit_n    TEXT,               -- normalize birim etiketi (sıralamada kullanılır)
    tags      TEXT,               -- 'telefon', 'eposta' (parçada numara / adres var)
    tsv       tsvector,           -- başlık (A) + birim (B) + gövde (C), Türkçe katlanmış
    embedding halfvec({config.EMBED_DIM})
);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS chunks_tsv ON chunks USING gin(tsv);
CREATE INDEX IF NOT EXISTS chunks_noemb ON chunks(id) WHERE embedding IS NULL;

CREATE TABLE IF NOT EXISTS people (
    id         SERIAL PRIMARY KEY,
    tip        TEXT, unvan TEXT, ad TEXT, soyad TEXT, gorev TEXT,
    birim      TEXT, telefon TEXT, dahili TEXT, eposta TEXT,
    search     TEXT,
    updated_at TEXT NOT NULL DEFAULT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')
);

CREATE TABLE IF NOT EXISTS units (
    id     SERIAL PRIMARY KEY,
    name   TEXT NOT NULL,
    source TEXT NOT NULL,
    code   TEXT,
    url    TEXT,
    UNIQUE(name, source)
);

CREATE TABLE IF NOT EXISTS jobs (
    id          SERIAL PRIMARY KEY,
    kind        TEXT NOT NULL,          -- site | unis | medya | update | update_hot | people | ocr | embed
    target      TEXT,                   -- site işinde başlangıç URL'si
    status      TEXT NOT NULL DEFAULT 'pending',   -- pending, running, done, failed
    attempts    INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    result      TEXT,
    created_at  TEXT NOT NULL DEFAULT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS'),
    started_at  TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, id);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# CONCURRENTLY: kurulum sırasında tarama/yazma işleri beklemez (otomatik commit bağlantısında çalıştırılır)
VECTOR_INDEX = "CREATE INDEX CONCURRENTLY IF NOT EXISTS chunks_emb ON chunks USING hnsw (embedding halfvec_cosine_ops)"


class Row(tuple):
    """sqlite3.Row gibi: hem r[0] hem r["kolon"]; dict(r) çalışır."""

    def __new__(cls, values, idx):
        r = super().__new__(cls, values)
        r._idx = idx
        return r

    def __getitem__(self, k):
        return tuple.__getitem__(self, self._idx[k] if isinstance(k, str) else k)

    def keys(self):
        return self._idx.keys()

    def get(self, k, default=None):
        return self[k] if k in self._idx else default


def _row_factory(cur):
    idx = {c.name: i for i, c in enumerate(cur.description or [])}
    return lambda values: Row(values, idx)


@lru_cache(maxsize=512)
def _q(sql: str) -> str:
    return sql.replace("%", "%%").replace("?", "%s")


class Conn:
    """psycopg bağlantısı üzerinde sqlite3 benzeri ince arayüz (execute → cursor)."""

    def __init__(self, pg: psycopg.Connection):
        self.pg = pg

    def execute(self, sql: str, params=()):
        cur = self.pg.cursor(row_factory=_row_factory)
        cur.execute(_q(sql), tuple(params) if params else None)
        return cur

    def executemany(self, sql: str, seq):
        seq = [tuple(p) for p in seq]
        cur = self.pg.cursor(row_factory=_row_factory)
        if seq:
            cur.executemany(_q(sql), seq)
        return cur

    def commit(self) -> None:
        self.pg.commit()

    def rollback(self) -> None:
        self.pg.rollback()

    def close(self) -> None:
        self.pg.close()

    @property
    def closed(self) -> bool:
        return self.pg.closed


def connect(readonly: bool = False, url: str | None = None) -> Conn:
    """readonly=True: otomatik commit (okuyucu sunucu uzun süre açık işlem bırakmasın)."""
    pg = psycopg.connect(url or config.DATABASE_URL, autocommit=readonly, connect_timeout=10,
                         application_name="kbu-mcp")
    return Conn(pg)


def available() -> bool:
    try:
        connect(readonly=True).close()
        return True
    except Exception:
        return False


def init(conn: Conn) -> None:
    conn.execute(SCHEMA)
    conn.commit()


@contextmanager
def tx(conn: Conn):
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def set_meta(conn, key: str, value: str) -> None:
    conn.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def get_meta(conn, key: str, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def delete_chunks(conn, doc_id: int) -> None:
    conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))


def source_for(host: str, url: str = "") -> str:
    if host == config.UNIS_HOST:
        return "unis"
    if host == config.MEDYA_HOST:
        return "medya"
    if re.search(r"obs\.karabuk\.edu\.tr/oibs/bologna", url, re.I):
        return "bologna"
    return "site"

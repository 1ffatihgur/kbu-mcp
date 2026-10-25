"""SQLite şeması ve yardımcıları.

docs     : keşfedilen her URL (sayfa veya dosya) ve tarama durumu
chunks   : belgelerden çıkarılmış metin parçaları
chunks_fts: parçalar üzerinde FTS5 tam metin indeksi (normalize edilmiş metin)
people   : telefon rehberinden çekilen personel kayıtları
units    : rehberdeki birimler ve keşfedilen alt siteler
"""

import sqlite3
from contextlib import contextmanager

from . import config

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS docs (
    id            INTEGER PRIMARY KEY,
    url           TEXT NOT NULL UNIQUE,
    final_url     TEXT,
    host          TEXT NOT NULL,
    kind          TEXT,               -- html, pdf, docx, xlsx, ...
    status        TEXT NOT NULL DEFAULT 'queued',  -- queued, ok, error, removed, skipped, duplicate
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
    error         TEXT,
    fail_count    INTEGER NOT NULL DEFAULT 0,
    first_seen    TEXT NOT NULL DEFAULT (datetime('now')),
    fetched_at    TEXT,
    checked_at    TEXT
);
CREATE INDEX IF NOT EXISTS docs_status ON docs(status);
CREATE INDEX IF NOT EXISTS docs_host ON docs(host);
CREATE INDEX IF NOT EXISTS docs_hash ON docs(content_hash);

CREATE TABLE IF NOT EXISTS chunks (
    id      INTEGER PRIMARY KEY,
    doc_id  INTEGER NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
    ord     INTEGER NOT NULL,
    page    INTEGER,
    text    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    title, unit, body,
    tokenize = 'unicode61 remove_diacritics 2',
    prefix = '3 4 5'
);

CREATE TABLE IF NOT EXISTS people (
    id         INTEGER PRIMARY KEY,
    tip        TEXT, unvan TEXT, ad TEXT, soyad TEXT, gorev TEXT,
    birim      TEXT, telefon TEXT, dahili TEXT, eposta TEXT,
    search     TEXT,                   -- normalize edilmiş arama metni
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS units (
    id     INTEGER PRIMARY KEY,
    name   TEXT NOT NULL,
    source TEXT NOT NULL,              -- rehber | site
    code   TEXT,                       -- rehber birim kodu
    url    TEXT,
    UNIQUE(name, source)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect(path=None, readonly: bool = False) -> sqlite3.Connection:
    path = path or config.DB_PATH
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30, check_same_thread=False)
    else:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=60, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=60000")
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


@contextmanager
def tx(conn: sqlite3.Connection):
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
    ids = [r[0] for r in conn.execute("SELECT id FROM chunks WHERE doc_id=?", (doc_id,))]
    if ids:
        conn.executemany("DELETE FROM chunks_fts WHERE rowid=?", [(i,) for i in ids])
        conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))

"""Anlamsal (embedding) arama desteği.

Embedding'ler OpenAI uyumlu bir `/v1/embeddings` servisinden alınır: yerelde Ollama (`ollama pull bge-m3`),
sunucuda vLLM / TEI / kurumun LLM sunucusu. Servis yoksa sistem anahtar kelime aramasıyla çalışmaya devam eder.

Parça metnine belge başlığı eklenerek gömülür ("bağlamlı embedding"): "Ücret Tarifesi" gibi kısa
tablolar hangi belgeye ait olduklarıyla birlikte temsil edilir.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import httpx

from . import config, db

log = logging.getLogger("kbu.embed")
_down_until = 0.0   # servis yanıt vermezse bir süre sorgu embedding'i denenmez (arama yavaşlamasın)


def embed_texts(texts: list[str], timeout: float = 120) -> list[list[float]]:
    headers = {"Authorization": f"Bearer {config.EMBED_API_KEY}"} if config.EMBED_API_KEY else {}
    r = httpx.post(f"{config.EMBED_URL}/embeddings", json={"model": config.EMBED_MODEL, "input": texts},
                   headers=headers, timeout=timeout)
    r.raise_for_status()
    data = sorted(r.json()["data"], key=lambda d: d["index"])
    return [d["embedding"][: config.EMBED_DIM] for d in data]


def to_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.5f}" for x in vec) + "]"


@lru_cache(maxsize=2048)
def query_vector(query: str) -> str | None:
    """Sorgu embedding'i (pgvector literal). Servis kapalıysa None — arama anahtar kelimeyle sürer."""
    global _down_until
    if time.time() < _down_until:
        return None
    try:
        return to_literal(embed_texts([query.strip()[:2000]], timeout=8)[0])
    except Exception as e:
        log.warning("sorgu embedding'i alınamadı (%s); yalnızca anahtar kelime araması", e)
        _down_until = time.time() + 60
        query_vector.cache_clear()
        return None


def passage(title: str | None, text: str) -> str:
    return (f"{title}\n\n{text}" if title else text)[:4000]


def run_embed(conn, limit: int | None = None, heartbeat=None) -> dict:
    """Embedding'i olmayan parçaları doldurur. Kesilirse kaldığı yerden sürer."""
    done, started, errors = 0, time.time(), 0
    last_log = 0.0
    while limit is None or done < limit:
        rows = conn.execute(
            """SELECT c.id, c.text, COALESCE(NULLIF(d.anchor_text, ''), d.title, d.site_title) AS title
               FROM chunks c JOIN docs d ON d.id = c.doc_id
               WHERE c.embedding IS NULL AND d.status IN ('ok', 'recheck') ORDER BY c.id LIMIT 512"""
        ).fetchall()
        if not rows:
            break
        batches = [rows[i:i + config.EMBED_BATCH] for i in range(0, len(rows), config.EMBED_BATCH)]
        try:  # iki eşzamanlı istek GPU'yu boş bekletmiyor (~%40 hızlı)
            with ThreadPoolExecutor(config.EMBED_PARALLEL) as pool:
                vecs = list(pool.map(lambda b: embed_texts([passage(r["title"], r["text"]) for r in b]), batches))
        except Exception as e:
            errors += 1
            if errors >= 5:
                raise RuntimeError(f"embedding servisi yanıt vermiyor ({config.EMBED_URL}): {e}") from e
            log.warning("embedding hatası, tekrar denenecek: %s", e)
            time.sleep(5 * errors)
            continue
        errors = 0
        for batch, vs in zip(batches, vecs):
            conn.executemany("UPDATE chunks SET embedding=?::halfvec WHERE id=?",
                             [(to_literal(v), r["id"]) for v, r in zip(vs, batch)])
            done += len(batch)
        conn.commit()
        if time.time() - last_log > 30:
            last_log = time.time()
            if heartbeat:
                heartbeat()
            left = conn.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NULL").fetchone()[0]
            log.info("embedding: %d yeni, %d kaldı, %.1f parça/sn", done, left, done / max(1, time.time() - started))
    if done:  # ilk dolumdan sonra kurulur; sonraki eklemeler indekse kendiliğinden girer
        ac = db.connect(readonly=True)
        try:
            ac.execute("SET maintenance_work_mem = '512MB'")  # 1GB + paralel işçiler Docker'da belleği aşıp sunucuyu düşürdü
            ac.execute("SET max_parallel_maintenance_workers = 1")
            ac.execute(db.VECTOR_INDEX)
        finally:
            ac.close()
    return {"gomulen_parca": done, "saniye": int(time.time() - started)}


def status(conn) -> dict:
    total, missing = conn.execute(
        "SELECT COUNT(*), COUNT(*) FILTER (WHERE embedding IS NULL) FROM chunks").fetchone()
    return {"model": config.EMBED_MODEL, "servis": config.EMBED_URL, "parca": total, "embeddingsiz": missing}

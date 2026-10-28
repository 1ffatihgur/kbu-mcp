"""İş kuyruğu ve işçi (worker).

    kbu jobs add site https://bbf.karabuk.edu.tr/ https://sks.karabuk.edu.tr/   # site başına bir iş
    kbu jobs add site --all          # bilinen bütün siteler (her biri ayrı iş, sırayla)
    kbu jobs add unis | medya | update | update_hot | people | ocr | embed
    kbu jobs list                    # durum, hata nedeni
    kbu worker                       # işleri sırayla çalıştırır; boşta kalınca zamanlanmış güncellemeleri yapar

- İşler PostgreSQL'deki `jobs` tablosunda durur. İşçi durdurulur / çöker / sunucu yeniden başlarsa yarım kalan
  iş ('running') tekrar sıraya alınır; tarama kuyruğu da (docs.status='queued') belge düzeyinde kaldığı yerden sürer.
- Başarısız iş 3 kez denenir; nedeni `jobs.error` alanında ve data/crawl.log'da görülür.
- Toplam istek hızı KBU_CRAWL_MAX_PER_HOUR ile sınırlanır (sunucular yoğun istekte engelliyor).
"""

import asyncio
import json
import logging
import time
import traceback

from . import config, db, updater, urls
from .crawler import now_iso

log = logging.getLogger("kbu.jobs")

KINDS = {"site", "unis", "medya", "update", "update_hot", "people", "ocr", "embed"}
MAX_ATTEMPTS = 3
IDLE_SLEEP = 60


def add(conn, kind: str, targets: list[str] | None = None) -> list[int]:
    if kind not in KINDS:
        raise ValueError(f"bilinmeyen iş türü: {kind} ({', '.join(sorted(KINDS))})")
    ids = []
    for t in targets or [None]:
        if kind == "site":
            t = urls.normalize(t or "")
            if not t or not urls.is_crawlable_url(t):
                raise ValueError(f"taranabilir bir *.karabuk.edu.tr adresi değil: {t}")
        dup = conn.execute("SELECT id FROM jobs WHERE kind=? AND target IS NOT DISTINCT FROM ? AND status IN ('pending','running')",
                           (kind, t)).fetchone()
        if dup:
            ids.append(dup["id"])
            continue
        ids.append(conn.execute("INSERT INTO jobs(kind, target) VALUES(?, ?) RETURNING id", (kind, t)).fetchone()["id"])
    conn.commit()
    return ids


def all_site_seeds(conn) -> list[str]:
    """Bilinen her site için ana sayfa (başlangıç adresleri + indeksteki hostlar)."""
    hosts = {urls.host_of(u) for u in config.SEED_URLS}
    hosts |= {r[0] for r in conn.execute("SELECT DISTINCT host FROM docs WHERE status='ok'")}
    seeds = []
    for h in sorted(hosts):
        u = f"https://{h}/"
        if urls.is_crawlable_url(u):
            seeds.append(u)
    return seeds


def listing(conn, limit: int = 100) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, kind, target, status, attempts, error, result, created_at, started_at, finished_at "
        "FROM jobs ORDER BY id DESC LIMIT ?", (limit,))]


def _run(conn, job) -> dict:
    from .crawler import crawl

    kind, target = job["kind"], job["target"]
    if kind == "site":
        return asyncio.run(crawl(conn, seeds=[target], only_hosts=[urls.host_of(target)], heartbeat=updater.heartbeat))
    if kind == "medya":
        return asyncio.run(crawl(conn, seeds=[f"https://{config.MEDYA_HOST}/"], only_hosts=[config.MEDYA_HOST],
                                 recheck_hours=24, heartbeat=updater.heartbeat))
    if kind == "unis":
        from .unis import refresh_unis
        return asyncio.run(refresh_unis(conn, heartbeat=updater.heartbeat))
    if kind == "people":
        from .people import refresh_people
        return {"rehber": asyncio.run(refresh_people(conn))}
    if kind == "ocr":
        from .ocr import run_ocr
        return run_ocr(conn, heartbeat=updater.heartbeat)
    if kind == "embed":
        from .embed import run_embed
        return run_embed(conn, heartbeat=updater.heartbeat)
    if kind in ("update", "update_hot"):
        return updater.run_update(hot=kind == "update_hot", conn=conn)
    raise ValueError(kind)


def run_next(conn) -> bool:
    """Sıradaki işi çalıştırır. İş yoksa False."""
    job = conn.execute(
        "UPDATE jobs SET status='running', started_at=?, attempts=attempts+1 WHERE id = ("
        " SELECT id FROM jobs WHERE status='pending' ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *",
        (now_iso(),)).fetchone()
    conn.commit()
    if not job:
        return False
    log.info("iş #%d başladı: %s %s", job["id"], job["kind"], job["target"] or "")
    try:
        with updater.update_lock():
            result = _run(conn, job)
        conn.execute("UPDATE jobs SET status='done', finished_at=?, error=NULL, result=? WHERE id=?",
                     (now_iso(), json.dumps(result, ensure_ascii=False, default=str)[:4000], job["id"]))
        log.info("iş #%d bitti: %s", job["id"], result)
    except Exception as e:
        conn.rollback()
        err = f"{type(e).__name__}: {e}"
        status = "pending" if job["attempts"] < MAX_ATTEMPTS and not isinstance(e, updater.Busy) else "failed"
        if isinstance(e, updater.Busy):
            status = "pending"
        conn.execute("UPDATE jobs SET status=?, finished_at=?, error=? WHERE id=?",
                     (status, now_iso(), (err + "\n" + traceback.format_exc()[-1500:])[:3000], job["id"]))
        log.error("iş #%d başarısız (%s, deneme %d): %s", job["id"], status, job["attempts"], err)
        if isinstance(e, updater.Busy):
            time.sleep(IDLE_SLEEP)
    conn.commit()
    return True


def worker(schedule: bool = True) -> None:
    conn = db.connect()
    db.init(conn)
    n = conn.execute("UPDATE jobs SET status='pending' WHERE status='running'").rowcount  # yarım kalanlar
    conn.commit()
    if n:
        log.info("%d yarım kalan iş yeniden sıraya alındı", n)
    log.info("işçi başladı (hız sınırı: %s istek/saat)", config.CRAWL_MAX_PER_HOUR or "yok")
    while True:
        try:
            if run_next(conn):
                continue
            if schedule:
                mode = updater.needed_mode()
                if mode and not updater.lock_active():
                    add(conn, "update" if mode == "full" else "update_hot")
                    continue
        except Exception:
            log.exception("işçi hatası")
            try:
                conn.close()
            except Exception:
                pass
            time.sleep(10)
            conn = db.connect()
            continue
        time.sleep(IDLE_SLEEP)

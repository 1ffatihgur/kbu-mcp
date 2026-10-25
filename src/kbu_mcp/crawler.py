"""Asenkron, nazik ve artımlı tarayıcı.

- Kuyruk veritabanında tutulur (docs.status='queued'); tarama yarıda kesilirse kaldığı yerden sürer.
- Host başına eşzamanlılık ve gecikme sınırı vardır.
- Yeniden kontrolde ETag / Last-Modified ile koşullu GET yapılır (304 → indirme yok).
- ASP.NET GridView sayfalamaları (__doPostBack 'Page$N') POST ile gezilip linkleri toplanır.
"""

import asyncio
import logging
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
from selectolax.lexbor import LexborHTMLParser

from . import config, db, urls
from .extract import Extracted, extract_bytes, extract_html, sniff_kind
from .indexer import content_hash, store

log = logging.getLogger("kbu.crawler")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def decode_html(resp: httpx.Response, data: bytes) -> str:
    enc = None
    ct = resp.headers.get("content-type", "")
    m = re.search(r"charset=([\w-]+)", ct, re.I)
    if m:
        enc = m.group(1)
    else:
        m = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", data[:4000], re.I)
        if m:
            enc = m.group(1).decode()
    for e in (enc, "utf-8", "cp1254"):
        if not e:
            continue
        try:
            text = data.decode(e)
            if text.count("�") < 5:
                return text
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", "replace")


HOT_URL_RE = re.compile(
    r"^https://[a-z0-9.-]+\.karabuk\.edu\.tr/("
    r"|index\.aspx|en/?|[a-z]+/?|"                                   # site ve bölüm ana sayfaları
    r"(Duyuru|Etkinlik|Haber)_tum\.aspx\?.*|"                         # duyuru / etkinlik / haber arşivleri
    r"(haberler|duyurular|category/[a-z-]+)/?)$",                     # WordPress liste sayfaları
    re.IGNORECASE,
)


def referer_for(url: str) -> str:
    return f"https://{urls.host_of(url)}/index.aspx"


def last_modified_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except (TypeError, ValueError):
        return None


class Crawler:
    def __init__(self, conn, max_pages: int | None = None, max_depth: int = config.MAX_DEPTH,
                 max_postback: int = config.MAX_POSTBACK_PAGES, heartbeat=None):
        self.conn = conn
        self.heartbeat = heartbeat
        self.max_pages = max_pages
        self.max_postback = max_postback
        self.max_depth = max_depth
        self.host_active: dict[str, int] = defaultdict(int)
        self.host_last: dict[str, float] = defaultdict(float)
        self.host_count: dict[str, int] = defaultdict(int)
        self.path_count: dict[tuple[str, str], int] = defaultdict(int)
        self.host_dead: dict[str, int] = {}
        self.processed = 0
        self.stats = defaultdict(int)
        self.pending_commit = 0
        self.last_commit = time.time()
        for row in conn.execute("SELECT host, COUNT(*) c FROM docs GROUP BY host"):
            self.host_count[row["host"]] = row["c"]
        self.client = httpx.AsyncClient(
            headers={"User-Agent": config.USER_AGENT, "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.5"},
            timeout=httpx.Timeout(config.REQUEST_TIMEOUT, connect=20),
            follow_redirects=True,
            verify=False,  # bazı alt sitelerin sertifika zinciri eksik
            limits=httpx.Limits(max_connections=config.GLOBAL_CONCURRENCY * 2, max_keepalive_connections=40),
        )

    # ------------------------------------------------------------ kuyruk

    def enqueue(self, url: str, depth: int, parent: str | None = None, anchor: str | None = None) -> None:
        host = urls.host_of(url)
        if not urls.is_crawlable_url(url) or urls.should_skip(url) or depth > self.max_depth:
            return
        if len(url) > 1500 or config.SKIP_URL_RE.search(url):
            return
        sp = urls.urlsplit(url)
        path_key = (host, sp.path.lower())
        if sp.query:
            if self.path_count[path_key] >= config.MAX_URLS_PER_PATH:
                return
            self.path_count[path_key] += 1
        anchor = (anchor or "").strip() or None
        cur = self.conn.execute("SELECT id, anchor_text FROM docs WHERE url=?", (url,)).fetchone()
        if cur:
            if anchor and not cur["anchor_text"]:
                self.conn.execute("UPDATE docs SET anchor_text=? WHERE id=?", (anchor, cur["id"]))
            return
        if self.host_count[host] >= config.MAX_PAGES_PER_HOST:
            return
        self.conn.execute(
            "INSERT OR IGNORE INTO docs(url, host, kind, depth, parent_url, anchor_text) VALUES(?,?,?,?,?,?)",
            (url, host, urls.kind_from_url(url), depth, parent, anchor),
        )
        self.host_count[host] += 1

    def seed(self, seed_urls) -> None:
        for u in seed_urls:
            n = urls.normalize(u)
            if not n:
                continue
            row = self.conn.execute("SELECT id FROM docs WHERE url=?", (n,)).fetchone()
            if row:
                self.conn.execute("UPDATE docs SET status='recheck' WHERE id=? AND status IN ('ok','error')", (row["id"],))
            else:
                self.enqueue(n, 0)
        self.conn.commit()

    def mark_recheck(self, older_than_hours: float, hot_only: bool = False) -> int:
        """Eski kontrol edilmiş belgeleri yeniden kontrol kuyruğuna alır."""
        cond = "status IN ('ok','duplicate') AND (checked_at IS NULL OR checked_at < datetime('now', ?))"
        params = [f"-{older_than_hours} hours"]
        if hot_only:
            # Ana sayfalar, bölüm ana sayfaları (/bilgisayar) ve duyuru/etkinlik/haber listeleri:
            # yeni içeriğin linkleri buralarda çıkar, link takibiyle yeni sayfalar da taranır.
            rows = self.conn.execute(f"SELECT id, url FROM docs WHERE {cond} AND kind='html'", params).fetchall()
            ids = [(r["id"],) for r in rows if HOT_URL_RE.match(r["url"])]
            self.conn.executemany("UPDATE docs SET status='recheck' WHERE id=?", ids)
            self.conn.commit()
            return len(ids)
        cur = self.conn.execute(f"UPDATE docs SET status='recheck' WHERE {cond}", params)
        # Hata alanları da bir şans daha (3 başarısızlıktan sonra bırakılır)
        self.conn.execute("UPDATE docs SET status='recheck' WHERE status='error' AND fail_count < 3")
        self.conn.commit()
        return cur.rowcount

    def _load_batch(self, per_host: int = 40) -> dict[str, deque]:
        # Host başına sınırlı sayıda iş: kuyruğun tek bir siteye yığılıp diğerlerini bekletmesini önler.
        rows = self.conn.execute(
            """SELECT * FROM (
                   SELECT *, ROW_NUMBER() OVER (PARTITION BY host ORDER BY depth, (kind IS NOT NULL), id) AS rn
                   FROM docs WHERE status IN ('queued','recheck')
               ) WHERE rn <= ? ORDER BY depth, id""",
            (per_host,),
        ).fetchall()
        by_host: dict[str, deque] = defaultdict(deque)
        for r in rows:
            by_host[r["host"]].append(dict(r))
        return by_host

    # ------------------------------------------------------------ ana döngü

    async def run(self) -> dict:
        started = time.time()
        last_log = 0.0
        in_flight: set[asyncio.Task] = set()
        in_flight_ids: set[int] = set()
        try:
            while True:
                if self.max_pages and self.processed >= self.max_pages:
                    break
                by_host = self._load_batch()
                for q in by_host.values():
                    while q and q[0]["id"] in in_flight_ids:
                        q.popleft()
                if not any(by_host.values()) and not in_flight:
                    break
                progressed = True
                while progressed and len(in_flight) < config.GLOBAL_CONCURRENCY:
                    progressed = False
                    for host, q in by_host.items():
                        if not q or self.host_active[host] >= config.PER_HOST_CONCURRENCY:
                            continue
                        if len(in_flight) >= config.GLOBAL_CONCURRENCY:
                            break
                        doc = q.popleft()
                        if doc["id"] in in_flight_ids:
                            continue
                        in_flight_ids.add(doc["id"])
                        self.host_active[host] += 1
                        t = asyncio.create_task(self._process(doc))
                        t.doc_id = doc["id"]  # type: ignore[attr-defined]
                        t.host = host  # type: ignore[attr-defined]
                        in_flight.add(t)
                        progressed = True
                if not in_flight:
                    await asyncio.sleep(0.2)
                    continue
                done, _ = await asyncio.wait(in_flight, timeout=5, return_when=asyncio.FIRST_COMPLETED)
                for t in done:
                    in_flight.discard(t)
                    in_flight_ids.discard(t.doc_id)
                    self.host_active[t.host] -= 1
                    if t.exception():
                        log.warning("görev hatası: %r", t.exception())
                # Sık commit: yazma kilidini uzun tutmak, aynı anda çalışan OCR/rehber işlerini kilitliyordu.
                if self.pending_commit and (self.pending_commit >= 25 or time.time() - self.last_commit > 2):
                    self.conn.commit()
                    self.pending_commit = 0
                    self.last_commit = time.time()
                if time.time() - last_log > 20:
                    last_log = time.time()
                    if self.heartbeat:
                        self.heartbeat()
                    q = self.conn.execute("SELECT COUNT(*) FROM docs WHERE status IN ('queued','recheck')").fetchone()[0]
                    log.info("işlenen=%d kuyruk=%d aktif=%d hız=%.1f/sn %s", self.processed, q, len(in_flight),
                             self.processed / max(1, time.time() - started), dict(self.stats))
        finally:
            for t in in_flight:
                t.cancel()
            self.conn.commit()
            await self.client.aclose()
        db.set_meta(self.conn, "last_crawl_finished", now_iso())
        self.conn.commit()
        return dict(self.stats, processed=self.processed, seconds=int(time.time() - started))

    # ------------------------------------------------------------ tek belge

    async def _polite_wait(self, host: str) -> None:
        wait = self.host_last[host] + config.PER_HOST_DELAY - time.time()
        self.host_last[host] = max(time.time(), self.host_last[host] + config.PER_HOST_DELAY)
        if wait > 0:
            await asyncio.sleep(wait)

    async def _get(self, url: str, headers: dict) -> tuple[httpx.Response, bytes]:
        last_exc = None
        for attempt, u in enumerate([url, url, url.replace("https://", "http://", 1)]):
            try:
                async with self.client.stream("GET", u, headers=headers) as resp:
                    if resp.status_code in (429, 503) and attempt < 2:
                        await asyncio.sleep(5 * (attempt + 1))
                        continue
                    cl = int(resp.headers.get("content-length") or 0)
                    if cl > config.MAX_FILE_BYTES:
                        return resp, b""
                    buf = bytearray()
                    async for chunk in resp.aiter_bytes():
                        buf.extend(chunk)
                        if len(buf) > config.MAX_FILE_BYTES:
                            break
                    return resp, bytes(buf)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError,
                    httpx.ReadError, httpx.WriteError, httpx.PoolTimeout) as e:
                last_exc = e
                await asyncio.sleep(1.5 * (attempt + 1))
        raise last_exc or RuntimeError("istek başarısız")

    async def _process(self, doc: dict) -> None:
        url, host = doc["url"], doc["host"]
        await self._polite_wait(host)
        # Bazı sayfalar (ör. Duyuru_tum.aspx) Referer yoksa ana sayfaya yönlendiriyor.
        parent = doc.get("parent_url")
        headers = {"Referer": parent if parent and urls.host_of(parent) == host else referer_for(url)}
        if doc["status"] == "recheck":
            if doc.get("etag"):
                headers["If-None-Match"] = doc["etag"]
            if doc.get("last_modified"):
                headers["If-Modified-Since"] = doc["last_modified"]
        if self.host_dead.get(host, 0) >= 5:
            self._set(doc["id"], status="error", error="host ulaşılamıyor", fail_count=3, checked_at=now_iso())
            self.processed += 1
            return
        try:
            resp, data = await self._get(url, headers)
        except Exception as e:
            if isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout)):
                self.host_dead[host] = self.host_dead.get(host, 0) + 1  # art arda bağlantı hatası: host ölü
            self._fail(doc, None, f"{type(e).__name__}: {e}"[:300])
            return
        finally:
            self.processed += 1
        self.host_dead[host] = 0

        status = resp.status_code
        if status == 304:
            self.conn.execute("UPDATE docs SET status='ok', checked_at=?, fail_count=0 WHERE id=?", (now_iso(), doc["id"]))
            self.stats["not_modified"] += 1
            self.pending_commit += 1
            return
        if status >= 400:
            self._fail(doc, status, f"HTTP {status}")
            return

        final_url = urls.normalize(str(resp.url)) or url
        final_host = urls.host_of(final_url)
        if not urls.is_allowed_host(final_host):
            self._set(doc["id"], status="skipped", http_status=status, final_url=final_url, error="site dışına yönlendirme", checked_at=now_iso())
            return
        if final_url != url and not urls.is_crawlable_url(final_url):
            self._set(doc["id"], status="skipped", http_status=status, final_url=final_url, error="giriş/sistem sayfasına yönlendirme", checked_at=now_iso())
            return
        if not data:
            self._set(doc["id"], status="skipped", http_status=status, final_url=final_url, error="boş veya çok büyük dosya", checked_at=now_iso())
            return

        kind = sniff_kind(data, resp.headers.get("content-type", ""), urls.kind_from_url(final_url) or doc.get("kind"))
        if kind is None:
            self._set(doc["id"], status="skipped", http_status=status, final_url=final_url, kind=None,
                      error=f"desteklenmeyen tür: {resp.headers.get('content-type', '')}"[:200], checked_at=now_iso())
            return

        html_text = None
        try:
            if kind == "html":
                html_text = decode_html(resp, data)
                ex = await asyncio.to_thread(extract_html, html_text, final_url)
            else:
                ex = await asyncio.to_thread(extract_bytes, data, kind)
        except Exception as e:
            self._fail(doc, status, f"çıkarma hatası: {type(e).__name__}: {e}"[:300], permanent=True)
            return
        if ex is None:
            ex = Extracted()

        depth = doc["depth"]
        for link, anchor in ex.links:
            self.enqueue(link, depth + 1, url, anchor)
        if kind == "html" and ex.postback_pages and html_text:
            try:
                await self._crawl_postback(final_url, html_text, depth)
            except Exception as e:
                log.debug("postback hatası %s: %r", url, e)

        h = content_hash(ex.text) if ex.text else None
        etag = resp.headers.get("etag")
        lm = resp.headers.get("last-modified")
        cdate = last_modified_date(lm) if kind != "html" else None
        cdate = cdate or ex.content_date
        fields = dict(
            final_url=final_url, kind=kind, http_status=status, etag=etag, last_modified=lm,
            site_title=ex.site_title, title=ex.title, content_hash=h, content_date=cdate,
            text_len=len(ex.text), pages=ex.pages, needs_ocr=int(ex.needs_ocr), error=None,
            fail_count=0, fetched_at=now_iso(), checked_at=now_iso(),
        )
        if h and doc.get("content_hash") == h and doc["status"] == "recheck":
            self._set(doc["id"], status="ok", **fields)  # değişmemiş; parçalar zaten güncel
            self.stats["unchanged"] += 1
            return
        dup = None
        if h:
            dup = self.conn.execute(
                "SELECT id FROM docs WHERE content_hash=? AND id<>? AND status='ok' LIMIT 1", (h, doc["id"])
            ).fetchone()
        if dup or not ex.text.strip():
            db.delete_chunks(self.conn, doc["id"])
            self._set(doc["id"], status="duplicate" if dup else "ok", **fields)
            self.stats["duplicate" if dup else "empty"] += 1
            return
        n = store(self.conn, doc["id"], final_url, ex, doc.get("anchor_text"))
        self._set(doc["id"], status="ok", **fields)
        self.stats[f"ok_{kind}"] += 1
        self.stats["chunks"] += n

    async def _crawl_postback(self, url: str, html: str, depth: int) -> None:
        """GridView sayfalarını POST ile gezip içindeki linkleri kuyruğa ekler."""
        seen_args = {"Page$1"}
        pending = [(t, a) for t, a in re.findall(r"__doPostBack\(&#39;([^&]+?)&#39;,&#39;(Page\$\d+)&#39;\)", html)]
        pending += re.findall(r"__doPostBack\('([^']+?)','(Page\$\d+)'\)", html)
        state_html = html
        count = 0
        while pending and count < self.max_postback:
            target, arg = pending.pop(0)
            if arg in seen_args:
                continue
            seen_args.add(arg)
            tree = LexborHTMLParser(state_html)
            form = {i.attributes.get("name"): i.attributes.get("value") or "" for i in tree.css("input[type=hidden]") if i.attributes.get("name")}
            form["__EVENTTARGET"] = target
            form["__EVENTARGUMENT"] = arg
            await self._polite_wait(urls.host_of(url))
            resp = await self.client.post(url, data=form, headers={"Referer": url})
            if resp.status_code != 200:
                break
            state_html = resp.text
            count += 1
            ex = extract_html(state_html, url)
            for link, anchor in ex.links:
                self.enqueue(link, depth + 1, url, anchor)
            for t, a in ex.postback_pages:
                if a.startswith("Page$") and a[5:].isdigit() and a not in seen_args:
                    pending.append((t, a))
        self.stats["postback_pages"] += count

    # ------------------------------------------------------------ durum yazımı

    def _set(self, doc_id: int, **fields) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE docs SET {cols} WHERE id=?", (*fields.values(), doc_id))
        self.pending_commit += 1

    def _fail(self, doc: dict, http_status: int | None, error: str, permanent: bool = False) -> None:
        fails = (doc.get("fail_count") or 0) + 1
        was_ok = doc["status"] == "recheck" and doc.get("content_hash")
        gone = http_status in (404, 410)
        if gone and was_ok and fails >= 2:
            status = "removed"
            db.delete_chunks(self.conn, doc["id"])
        elif was_ok and not permanent and fails < 3:
            status = "ok"  # geçici hata: eski içerik kalsın, sonra tekrar denenir
        else:
            status = "error"
        self._set(doc["id"], status=status, http_status=http_status, error=error, fail_count=fails, checked_at=now_iso())
        self.stats["errors"] += 1


async def crawl(conn, seeds=None, max_pages=None, recheck_hours=None, hot_only=False, heartbeat=None) -> dict:
    # Saatlik (hot) modda arşiv sayfalamasının sadece en yeni sayfaları gezilir.
    c = Crawler(conn, max_pages=max_pages, max_postback=2 if hot_only else config.MAX_POSTBACK_PAGES, heartbeat=heartbeat)
    c.seed(seeds or config.SEED_URLS)
    if recheck_hours is not None:
        n = c.mark_recheck(recheck_hours, hot_only=hot_only)
        log.info("%d belge yeniden kontrol kuyruğunda", n)
    return await c.run()

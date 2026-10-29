"""Komut satırı: kbu update | status | search | stats | crawl | refresh | unis | people | ocr | embed | jobs | worker |
errors | sample | migrate-sqlite | reindex | queries | serve"""

import argparse
import asyncio
import json
import logging
import os
import sys
from contextlib import contextmanager

from . import __version__, config, db


@contextmanager
def _locked():
    """Veritabanına yazan komutlar aynı anda tek çalışır (sunucunun başlattığı arka plan güncellemesi dahil)."""
    from .updater import Busy, update_lock

    try:
        with update_lock():
            yield
    except Busy as e:
        sys.exit(f"{e} Durum için: kbu status")


def cmd_update(args) -> None:
    from .updater import Busy, run_update

    try:
        print(json.dumps(run_update(hot=args.hot), ensure_ascii=False, indent=2))
    except Busy as e:
        sys.exit(f"{e} Durum için: kbu status")


def cmd_status(args) -> None:
    from .updater import needed_mode, status

    out = status()
    out["gereken_guncelleme"] = needed_mode() or "yok (güncel)"
    print(json.dumps(out, ensure_ascii=False, indent=2))


def _setup_logging(verbose: bool) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    handlers = [logging.StreamHandler(sys.stderr), logging.FileHandler(config.DATA_DIR / "crawl.log", encoding="utf-8")]
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def cmd_crawl(args) -> None:
    from .crawler import crawl

    from .updater import heartbeat

    with _locked():
        conn = db.connect()
        db.init(conn)
        stats = asyncio.run(crawl(conn, seeds=args.seed or None, max_pages=args.max_pages, heartbeat=heartbeat))
    print(json.dumps(stats, ensure_ascii=False, indent=2))


def cmd_refresh(args) -> None:
    """Artımlı güncelleme: eski kontrol edilmiş belgeleri koşullu GET ile yeniden dener, yeni linkleri tarar."""
    from .crawler import crawl

    from .updater import heartbeat

    with _locked():
        conn = db.connect()
        db.init(conn)
        stats = asyncio.run(crawl(conn, max_pages=args.max_pages, recheck_hours=args.older_than, hot_only=args.hot,
                                  heartbeat=heartbeat))
    print(json.dumps(stats, ensure_ascii=False, indent=2))


def cmd_people(args) -> None:
    from .people import refresh_people

    conn = db.connect()
    db.init(conn)
    n = asyncio.run(refresh_people(conn))
    print(f"{n} rehber kaydı güncellendi")


def cmd_ocr(args) -> None:
    from .ocr import run_ocr

    from .updater import heartbeat

    with _locked():
        conn = db.connect()
        db.init(conn)
        print(json.dumps(run_ocr(conn, limit=args.limit, heartbeat=heartbeat, retry_failed=args.retry_failed), ensure_ascii=False))


def cmd_unis(args) -> None:
    from .unis import refresh_unis
    from .updater import heartbeat

    with _locked():
        conn = db.connect()
        db.init(conn)
        print(json.dumps(asyncio.run(refresh_unis(conn, limit=args.limit, users=args.user, heartbeat=heartbeat)), ensure_ascii=False))


def cmd_embed(args) -> None:
    from .embed import run_embed, status
    from .updater import heartbeat

    conn = db.connect()
    db.init(conn)
    if args.status:
        print(json.dumps(status(conn), ensure_ascii=False))
        return
    with _locked():
        print(json.dumps(run_embed(conn, limit=args.limit, heartbeat=heartbeat), ensure_ascii=False))


def cmd_jobs(args) -> None:
    from . import jobs

    conn = db.connect()
    db.init(conn)
    if args.action == "add":
        targets = args.targets
        if args.kind == "site" and args.all:
            targets = jobs.all_site_seeds(conn)
        try:
            ids = jobs.add(conn, args.kind, targets or None)
        except ValueError as e:
            sys.exit(str(e))
        print(f"{len(ids)} iş sırada: {ids[:20]}{' ...' if len(ids) > 20 else ''}  (çalıştırmak için: kbu worker)")
    elif args.action == "retry":
        conn.execute("UPDATE jobs SET status='pending', attempts=0 WHERE status='failed'" + (" AND id=?" if args.targets else ""),
                     tuple(args.targets[:1]))
        conn.commit()
    else:
        for j in reversed(jobs.listing(conn, args.limit)):
            err = (j["error"] or "").split("\n", 1)[0]
            print(f"#{j['id']:<5} {j['status']:<8} {j['kind']:<10} {(j['target'] or ''):<45} deneme={j['attempts']} "
                  f"{j['finished_at'] or j['started_at'] or j['created_at']}  {err}")


def cmd_worker(args) -> None:
    from .jobs import worker

    worker(schedule=not args.no_schedule)


def cmd_errors(args) -> None:
    """Taranamayan adresler ve nedenleri (site başına)."""
    conn = db.connect(readonly=True)
    where, params = "status IN ('error','skipped')", []
    if args.host:
        where += " AND host = ?"
        params.append(args.host)
    rows = conn.execute(
        f"""SELECT host, split_part(COALESCE(error, '-'), ':', 1) AS neden, COUNT(*) AS n, MIN(url) AS ornek
            FROM docs WHERE {where} GROUP BY 1, 2 ORDER BY 3 DESC LIMIT ?""", (*params, args.limit)).fetchall()
    for r in rows:
        print(f"{r['n']:>6}  {r['host']:<35} {r['neden'][:45]:<45} örn: {r['ornek']}")


def cmd_sample(args) -> None:
    from .sample import main

    out = main(args.url)
    print((out / "RAPOR.md").read_text(encoding="utf-8"))
    print(f"Markdown çıktılar: {out}")


def cmd_migrate(args) -> None:
    from .migrate import migrate

    print(json.dumps(migrate(args.path), ensure_ascii=False))


def cmd_reindex(args) -> None:
    from .indexer import rebuild_fts

    conn = db.connect()
    db.init(conn)
    print(f"{rebuild_fts(conn)} parça yeniden indekslendi")


def cmd_stats(args) -> None:
    conn = db.connect()
    db.init(conn)
    out = {
        "durum": {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM docs GROUP BY status")},
        "tur": {str(r[0]): r[1] for r in conn.execute("SELECT kind, COUNT(*) FROM docs WHERE status='ok' GROUP BY kind ORDER BY 2 DESC")},
        "parca": conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
        "ocr_gereken": conn.execute("SELECT COUNT(*) FROM docs WHERE needs_ocr=1 AND status='ok'").fetchone()[0],
        "host_sayisi": conn.execute("SELECT COUNT(DISTINCT host) FROM docs WHERE status='ok'").fetchone()[0],
        "rehber_kisi": conn.execute("SELECT COUNT(*) FROM people").fetchone()[0],
        "son_tarama": db.get_meta(conn, "last_crawl_finished"),
    }
    if args.hosts:
        out["hostlar"] = {r[0]: r[1] for r in conn.execute(
            "SELECT host, COUNT(*) FROM docs WHERE status='ok' GROUP BY host ORDER BY 2 DESC")}
    if args.errors:
        out["hatalar"] = {r[0]: r[1] for r in conn.execute(
            "SELECT substr(error,1,60), COUNT(*) FROM docs WHERE status IN ('error','skipped') GROUP BY 1 ORDER BY 2 DESC LIMIT 25")}
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_search(args) -> None:
    from .search import search

    conn = db.connect(readonly=True)
    res = search(conn, args.query, unit=args.unit, kind=args.kind, limit=args.limit, source=args.source,
                 semantic=not args.keyword_only)
    for r in res:
        print(f"\n### {r['baslik']}  [{r['kaynak']}/{r['tur']}] {r['tarih'] or ''}  skor={r['skor']} ({r['eslesme']})\n{r['url']}")
        for p in r["parcalar"][:1]:
            print(("  s." + str(p["sayfa"]) + " " if p["sayfa"] else "  ") + p["metin"][:400].replace("\n", " ⏎ "))


def cmd_queries(args) -> None:
    """Uzak sunucuya gelen araç çağrılarını okunur biçimde listeler."""
    path = config.DATA_DIR / "queries.jsonl"
    if not path.exists():
        print("Henüz kayıtlı sorgu yok (uzak sunucu --http ile çalışırken araç çağrıları kaydedilir).")
        return
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.ip:
        rows = [r for r in rows if r.get("ip") == args.ip]
    for r in rows[-args.last:]:
        p = r.get("parametreler") or {}
        detail = p.get("query") or p.get("url") or ", ".join(f"{k}={v}" for k, v in p.items() if v) or "-"
        print(f"{r['zaman']}  {r.get('ip', '?'):<15}  {r.get('arac', '?'):<20}  {detail}")
    print(f"\n{len(rows)} kayıt · dosya: {path}")


def cmd_serve(args) -> None:
    from .server import main

    main(["--http", "--port", str(args.port)] if args.http else [])


def main(argv=None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="kbu", description="Karabük Üniversitesi site indeksleyici ve MCP sunucusu")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--version", action="version", version=f"kbu-mcp {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("update", help="İndeksi oluştur/güncelle (ilk seferde tam tarama; sonra artımlı + rehber + OCR)")
    p.add_argument("--hot", action="store_true", help="Sadece ana sayfalar ve duyuru listeleri (saatlik iş)")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("status", help="Veri klasörü, son güncellemeler, çalışan güncelleme")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("crawl", help="Tam tarama (yarıda kalırsa kaldığı yerden devam eder)")
    p.add_argument("--seed", action="append", help="Ek başlangıç URL'si (birden fazla verilebilir)")
    p.add_argument("--max-pages", type=int)
    p.set_defaults(func=cmd_crawl)

    p = sub.add_parser("refresh", help="Artımlı güncelleme")
    p.add_argument("--older-than", type=float, default=24, help="Bu kadar saatten eski kontrolleri yenile")
    p.add_argument("--hot", action="store_true", help="Sadece ana sayfalar ve duyuru listeleri")
    p.add_argument("--max-pages", type=int)
    p.set_defaults(func=cmd_refresh)

    p = sub.add_parser("people", help="Telefon rehberini yerel önbelleğe al")
    p.set_defaults(func=cmd_people)

    p = sub.add_parser("ocr", help="Taranmış PDF'leri OCR ile metne çevir (Tesseract gerekir)")
    p.add_argument("--limit", type=int)
    p.add_argument("--retry-failed", action="store_true", help="Daha önce okunamamış belgeleri de yeniden dene")
    p.set_defaults(func=cmd_ocr)

    p = sub.add_parser("unis", help="UNIS akademisyen profillerini (yayın, proje, tez, idari görev) indeksle")
    p.add_argument("--limit", type=int)
    p.add_argument("--user", action="append", help="Yalnızca bu UNIS kullanıcı adı (ör. iturker)")
    p.set_defaults(func=cmd_unis)

    p = sub.add_parser("embed", help="Anlamsal arama için eksik embedding'leri doldur")
    p.add_argument("--limit", type=int)
    p.add_argument("--status", action="store_true")
    p.set_defaults(func=cmd_embed)

    p = sub.add_parser("jobs", help="İş kuyruğu: add <tür> [url...] | list | retry [id]")
    p.add_argument("action", choices=["add", "list", "retry"])
    p.add_argument("kind", nargs="?", default="site", help="site | unis | medya | update | update_hot | people | ocr | embed")
    p.add_argument("targets", nargs="*", help="site işleri için başlangıç adresleri (retry için iş no)")
    p.add_argument("--all", action="store_true", help="site: bilinen bütün siteler için birer iş")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_jobs)

    p = sub.add_parser("worker", help="Kuyruk işçisi: işleri sırayla çalıştırır, boşta zamanlanmış güncellemeleri yapar")
    p.add_argument("--no-schedule", action="store_true", help="Sadece kuyruktaki işler (zamanlayıcı kapalı)")
    p.set_defaults(func=cmd_worker)

    p = sub.add_parser("errors", help="Taranamayan adresler ve nedenleri")
    p.add_argument("--host")
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(func=cmd_errors)

    p = sub.add_parser("sample", help="Küçük ölçekli parse testi (site + UNIS + KBÜ Medya örnekleri → data/sample)")
    p.add_argument("--url", action="append", help="Kendi seçtiğiniz adresler")
    p.set_defaults(func=cmd_sample)

    p = sub.add_parser("migrate-sqlite", help="v1 SQLite indeksini (data/kbu.db) PostgreSQL'e taşı")
    p.add_argument("path", nargs="?")
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser("reindex", help="Tam metin indeksini mevcut parçalardan yeniden kur")
    p.set_defaults(func=cmd_reindex)

    p = sub.add_parser("stats", help="İndeks istatistikleri")
    p.add_argument("--hosts", action="store_true")
    p.add_argument("--errors", action="store_true")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("search", help="İndekste deneme araması")
    p.add_argument("query")
    p.add_argument("--unit")
    p.add_argument("--kind")
    p.add_argument("--source", help="site | unis | medya | bologna")
    p.add_argument("--keyword-only", action="store_true", help="Anlamsal aramayı kapat")
    p.add_argument("--limit", type=int, default=8)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("queries", help="Uzak sunucuya gelen araç çağrıları (sorgu logu)")
    p.add_argument("--last", type=int, default=50, help="Son kaç kayıt (varsayılan 50)")
    p.add_argument("--ip", help="Sadece bu IP adresinden gelenler")
    p.set_defaults(func=cmd_queries)

    p = sub.add_parser("serve", help="MCP sunucusunu başlat (varsayılan stdio; --http ile uzak sunucu)")
    p.add_argument("--http", action="store_true")
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", "7860")))
    p.set_defaults(func=cmd_serve)

    args = ap.parse_args(argv)
    if args.cmd not in ("serve", "status", "jobs", "errors"):
        _setup_logging(args.verbose)
    args.func(args)


if __name__ == "__main__":
    main()

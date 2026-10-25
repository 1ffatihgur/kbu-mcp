"""Komut satırı: kbu update | status | search | stats | crawl | refresh | people | ocr | reindex | serve"""

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
        print(json.dumps(run_ocr(conn, limit=args.limit, heartbeat=heartbeat), ensure_ascii=False))


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
    res = search(conn, args.query, unit=args.unit, kind=args.kind, limit=args.limit)
    for r in res:
        print(f"\n### {r['baslik']}  [{r['tur']}] {r['tarih'] or ''}  skor={r['skor']}\n{r['url']}")
        for p in r["parcalar"][:1]:
            print(("  s." + str(p["sayfa"]) + " " if p["sayfa"] else "  ") + p["metin"][:400].replace("\n", " ⏎ "))


def cmd_snapshot(args) -> None:
    from . import snapshot

    if args.action == "export":
        print(snapshot.export(args.file or config.DATA_DIR / snapshot.FILENAME))
    elif args.action == "push":
        print(snapshot.push())
    else:
        print("kuruldu" if snapshot.pull() else "KBU_SNAPSHOT_REPO veya KBU_SNAPSHOT_URL tanımlı değil")


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
    p.set_defaults(func=cmd_ocr)

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
    p.add_argument("--limit", type=int, default=8)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("snapshot", help="İndeks anlık görüntüsü: export | push (HF dataset) | pull")
    p.add_argument("action", choices=["export", "push", "pull"])
    p.add_argument("file", nargs="?")
    p.set_defaults(func=cmd_snapshot)

    p = sub.add_parser("serve", help="MCP sunucusunu başlat (varsayılan stdio; --http ile uzak sunucu)")
    p.add_argument("--http", action="store_true")
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", "7860")))
    p.set_defaults(func=cmd_serve)

    args = ap.parse_args(argv)
    if args.cmd not in ("serve", "status"):
        _setup_logging(args.verbose)
    args.func(args)


if __name__ == "__main__":
    main()

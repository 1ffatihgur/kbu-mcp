"""İndeks güncelleme orkestrasyonu.

`kbu update` tek giriş noktasıdır: indeks yoksa tam tarama, varsa artımlı yenileme; ardından
telefon rehberi önbelleği ve (Tesseract varsa) yeni taranmış PDF'lerin OCR'ı. Aynı anda tek bir
güncelleme çalışır (kilit dosyası + nabız). MCP sunucusu açılırken gerekirse bunu arka planda,
istemciden bağımsız bir süreç olarak başlatır; böylece kullanıcı hiçbir şey çalıştırmadan indeks
oluşur ve güncel kalır.
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config, db

log = logging.getLogger("kbu.update")

LOCK_PATH = config.DATA_DIR / "update.lock"
LOCK_STALE_SECONDS = 15 * 60   # nabız bu kadar süre gelmezse kilit ölü sayılır


class Busy(RuntimeError):
    pass


def lock_active() -> bool:
    try:
        return time.time() - LOCK_PATH.stat().st_mtime < LOCK_STALE_SECONDS
    except FileNotFoundError:
        return False


def heartbeat() -> None:
    try:
        LOCK_PATH.touch()
    except OSError:
        pass


@contextmanager
def update_lock():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if lock_active():
        raise Busy("Başka bir güncelleme zaten çalışıyor.")
    LOCK_PATH.write_text(f"{os.getpid()} {datetime.now(timezone.utc).isoformat()}", encoding="utf-8")
    try:
        yield
    finally:
        try:
            LOCK_PATH.unlink()
        except OSError:
            pass


def _hours_since(value: str | None) -> float:
    if not value:
        return float("inf")
    try:
        t = datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return float("inf")
    return (datetime.now(timezone.utc) - t).total_seconds() / 3600


def needed_mode() -> str | None:
    """'full', 'hot' veya None (güncel)."""
    if not config.DB_PATH.exists():
        return "full"
    try:
        conn = db.connect(readonly=True)
        ok = conn.execute("SELECT COUNT(*) FROM docs WHERE status='ok'").fetchone()[0]
        last_full = db.get_meta(conn, "last_update_full") or db.get_meta(conn, "last_crawl_finished")
        last_hot = db.get_meta(conn, "last_update_hot")
        queued = conn.execute("SELECT COUNT(*) FROM docs WHERE status IN ('queued','recheck')").fetchone()[0]
        conn.close()
    except Exception:
        return "full"
    if ok < 100 or queued > 0 or _hours_since(last_full) >= config.FULL_UPDATE_HOURS:
        return "full"  # yarım kalmış ilk tarama da buradan devam eder
    if min(_hours_since(last_hot), _hours_since(last_full)) >= config.HOT_UPDATE_HOURS:
        return "hot"
    return None


def run_update(hot: bool = False) -> dict:
    """Kilit altında güncelleme yapar ve özet döner."""
    from .crawler import crawl, now_iso
    from .people import refresh_people

    with update_lock():
        conn = db.connect()
        db.init(conn)
        fresh = conn.execute("SELECT COUNT(*) FROM docs WHERE status='ok'").fetchone()[0] < 100
        summary: dict = {"mod": "ilk tarama" if fresh else ("saatlik" if hot else "tam yenileme")}
        if fresh:
            summary["tarama"] = asyncio.run(crawl(conn, heartbeat=heartbeat))
        else:
            summary["tarama"] = asyncio.run(crawl(conn, recheck_hours=0.9 if hot else 24, hot_only=hot, heartbeat=heartbeat))
        if not hot:
            if _hours_since(db.get_meta(conn, "people_refreshed")) >= 20:
                try:
                    summary["rehber"] = asyncio.run(refresh_people(conn))
                except Exception as e:
                    summary["rehber_hata"] = repr(e)
            heartbeat()
            try:
                from .ocr import find_tessdata, run_ocr
                if find_tessdata(auto_download=True):
                    summary["ocr"] = run_ocr(conn, heartbeat=heartbeat)
                else:
                    summary["ocr"] = "Tesseract bulunamadı; taranmış PDF'ler atlandı"
            except Exception as e:
                summary["ocr_hata"] = repr(e)
            db.set_meta(conn, "last_update_full", now_iso())
        db.set_meta(conn, "last_update_hot", now_iso())
        conn.commit()
        conn.close()
        return summary


def _python_exe() -> str:
    # Pencere açmayan yorumlayıcı (Windows'ta pythonw.exe) varsa onu kullan
    exe = sys.executable
    if os.name == "nt":
        w = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(w):
            return w
    return exe


def spawn_background_update(mode: str) -> bool:
    """Güncellemeyi MCP istemcisinden bağımsız bir süreçte başlatır; istemci kapansa da sürer.

    Windows'ta MCP istemcileri sunucuyu KILL_ON_JOB_CLOSE ayarlı bir Job Object içinde çalıştırır; normal (hatta
    CREATE_BREAKAWAY_FROM_JOB ile başlatılan) alt süreç istemci kapanınca öldürülür (denendi). Bu yüzden süreç
    WMI (Win32_Process.Create) ile başlatılır: WMI'ın yarattığı süreç istemcinin job'ına dahil olmaz.
    WMI kullanılamazsa ayrık Popen'a düşülür (istemci açık kaldıkça çalışır, sonraki açılışta devam eder).
    """
    if lock_active():
        return False
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    args = [_python_exe(), "-m", "kbu_mcp.bgupdate", str(config.DATA_DIR)] + (["--hot"] if mode == "hot" else [])
    kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    if os.name != "nt":
        try:
            subprocess.Popen(args, start_new_session=True, **kwargs)
            return True
        except OSError as e:
            log.warning("arka plan güncellemesi başlatılamadı: %r", e)
            return False

    DETACHED, NEW_GROUP, NO_WINDOW = 0x08, 0x200, 0x08000000
    cmdline = subprocess.list2cmdline(args).replace("'", "''")
    ps = (f"$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments "
          f"@{{CommandLine='{cmdline}'}}; exit $r.ReturnValue")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True,
                           timeout=60, creationflags=NO_WINDOW)
        if r.returncode == 0:
            return True
        log.warning("WMI ile başlatılamadı (kod %s), ayrık süreç deneniyor", r.returncode)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("WMI ile başlatılamadı: %r", e)
    try:
        subprocess.Popen(args, creationflags=DETACHED | NEW_GROUP | NO_WINDOW, **kwargs)
        return True
    except OSError as e:
        log.warning("arka plan güncellemesi başlatılamadı: %r", e)
        return False


def maybe_auto_update() -> str | None:
    if not config.AUTO_UPDATE:
        return None
    mode = needed_mode()
    if mode and spawn_background_update(mode):
        return mode
    return None


def status() -> dict:
    out = {"veri_klasoru": str(config.DATA_DIR), "guncelleme_calisiyor": lock_active()}
    if config.DB_PATH.exists():
        try:
            conn = db.connect(readonly=True)
            out["son_tam_guncelleme"] = db.get_meta(conn, "last_update_full") or db.get_meta(conn, "last_crawl_finished")
            out["son_saatlik_guncelleme"] = db.get_meta(conn, "last_update_hot")
            out["kuyruk"] = conn.execute("SELECT COUNT(*) FROM docs WHERE status IN ('queued','recheck')").fetchone()[0]
            conn.close()
        except Exception:
            pass
    return out


if __name__ == "__main__":
    print(json.dumps(status(), ensure_ascii=False, indent=2))

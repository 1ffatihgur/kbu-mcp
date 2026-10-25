"""İndeksin anlık görüntüsü (snapshot): dışa aktarma, Hugging Face Dataset'e yükleme ve oradan indirme.

Uzak sunucu (ör. Hugging Face Spaces) kalıcı diske sahip olmadığında her açılışta indeksi 1-2 saatte
yeniden taramak yerine son anlık görüntüyü dakikalar içinde indirir, sonra artımlı güncellemeyle devam eder.

Ortam değişkenleri:
  KBU_SNAPSHOT_REPO  Hugging Face dataset deposu (ör. "kullanici/kbu-mcp-index"); özel (private) olması önerilir
  HF_TOKEN           Okuma (indirme) / yazma (yükleme) yetkili Hugging Face token'ı
  KBU_SNAPSHOT_URL   Alternatif: doğrudan indirilebilir .db.gz adresi (yalnızca indirme)
"""

import gzip
import logging
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

import httpx

from . import config

log = logging.getLogger("kbu.snapshot")

FILENAME = "kbu.db.gz"


def _repo() -> str | None:
    return os.environ.get("KBU_SNAPSHOT_REPO") or None


def _token() -> str | None:
    return os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN") or None


def export(dest: Path) -> Path:
    """Tutarlı bir kopya (VACUUM INTO) alıp gzip'ler. Çalışan güncellemeyi durdurmaz."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
        raw = Path(tmp) / "kbu.db"
        src = sqlite3.connect(config.DB_PATH, timeout=120)
        src.execute("VACUUM INTO ?", (str(raw),))
        src.close()
        with open(raw, "rb") as fi, gzip.open(dest, "wb", compresslevel=6) as fo:
            shutil.copyfileobj(fi, fo, length=8 * 1024 * 1024)
    log.info("anlık görüntü hazır: %s (%.0f MB)", dest, dest.stat().st_size / 1e6)
    return dest


def push() -> str:
    """Anlık görüntüyü Hugging Face dataset deposuna yükler."""
    repo, token = _repo(), _token()
    if not repo or not token:
        raise SystemExit("KBU_SNAPSHOT_REPO ve HF_TOKEN (yazma yetkili) gerekli.")
    from huggingface_hub import HfApi

    api = HfApi(token=token)
    api.create_repo(repo, repo_type="dataset", private=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=config.DATA_DIR) as tmp:
        f = export(Path(tmp) / FILENAME)
        api.upload_file(path_or_fileobj=str(f), path_in_repo=FILENAME, repo_id=repo, repo_type="dataset",
                        commit_message="indeks anlık görüntüsü")
    return f"https://huggingface.co/datasets/{repo}"


def _install(gz_path: Path) -> None:
    """İndirilen .gz dosyasını açıp mevcut veritabanının yerine atomik olarak koyar."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp_db = config.DB_PATH.with_suffix(".db.new")
    with gzip.open(gz_path, "rb") as fi, open(tmp_db, "wb") as fo:
        shutil.copyfileobj(fi, fo, length=8 * 1024 * 1024)
    conn = sqlite3.connect(tmp_db)
    ok = conn.execute("PRAGMA quick_check").fetchone()[0]
    conn.close()
    if ok != "ok":
        tmp_db.unlink(missing_ok=True)
        raise RuntimeError(f"indirilen anlık görüntü bozuk: {ok}")
    for suffix in ("-wal", "-shm"):
        Path(str(config.DB_PATH) + suffix).unlink(missing_ok=True)
    os.replace(tmp_db, config.DB_PATH)


def pull() -> bool:
    """Anlık görüntüyü indirip kurar. Kaynak tanımlı değilse False."""
    repo, url = _repo(), os.environ.get("KBU_SNAPSHOT_URL")
    if not repo and not url:
        return False
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if repo:
        from huggingface_hub import hf_hub_download

        path = hf_hub_download(repo_id=repo, filename=FILENAME, repo_type="dataset", token=_token(),
                               cache_dir=str(config.DATA_DIR / "hf-cache"))
        _install(Path(path))
        shutil.rmtree(config.DATA_DIR / "hf-cache", ignore_errors=True)
    else:
        with tempfile.TemporaryDirectory(dir=config.DATA_DIR) as tmp:
            gz = Path(tmp) / FILENAME
            with httpx.stream("GET", url, follow_redirects=True, timeout=600) as r:
                r.raise_for_status()
                with open(gz, "wb") as f:
                    for chunk in r.iter_bytes(8 * 1024 * 1024):
                        f.write(chunk)
            _install(gz)
    log.info("anlık görüntü kuruldu: %s", config.DB_PATH)
    return True

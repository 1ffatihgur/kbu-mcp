"""Proje genel ayarları: yollar, alan adı kuralları, tarama limitleri."""

import os
import re
import sys
from pathlib import Path

from . import __version__

ROOT = Path(__file__).resolve().parents[2]


def _default_data_dir() -> Path:
    """Geliştirme kopyasında depo içindeki data/, kurulu pakette (uvx/pip) kullanıcı veri klasörü."""
    if (ROOT / "pyproject.toml").exists() and (ROOT / "src" / "kbu_mcp").is_dir():
        return ROOT / "data"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "kbu-mcp"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "kbu-mcp"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "kbu-mcp"


def _load_dotenv() -> None:
    """Depo kökündeki .env (docker compose ile ortak) — ortam değişkenlerini ezmez."""
    f = ROOT / ".env"
    if not f.is_file():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        k, sep, v = line.strip().partition("=")
        if sep and k and not k.startswith("#"):
            os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


DATA_DIR = Path(os.environ.get("KBU_DATA_DIR") or _default_data_dir())
SQLITE_PATH = Path(os.environ.get("KBU_DB_PATH") or DATA_DIR / "kbu.db")   # yalnızca v1 verisini taşımak için

# PostgreSQL (+ pgvector). docker compose ile gelen db servisi varsayılandır.
DATABASE_URL = os.environ.get("KBU_DATABASE_URL") or (
    f"postgresql://kbu:{os.environ.get('POSTGRES_PASSWORD', 'kbu')}@127.0.0.1:{os.environ.get('POSTGRES_PORT', '5432')}/kbu"
)

# Embedding (anlamsal arama): OpenAI uyumlu /v1/embeddings servisi (Ollama, vLLM, TEI, kurum LLM sunucusu).
EMBED_URL = (os.environ.get("KBU_EMBED_URL") or "http://127.0.0.1:11434/v1").rstrip("/")
EMBED_MODEL = os.environ.get("KBU_EMBED_MODEL") or "bge-m3"
EMBED_API_KEY = os.environ.get("KBU_EMBED_API_KEY") or ""
EMBED_DIM = _env_int("KBU_EMBED_DIM", 1024)
EMBED_BATCH = _env_int("KBU_EMBED_BATCH", 64)
EMBED_PARALLEL = _env_int("KBU_EMBED_PARALLEL", 2)

# Araç cevap boyutları: bağlamı küçük modellerde (ör. 16-32k token) taşmayı önler. Model gerekirse parametreyle
# daha fazlasını ister (read_document offset / max_chars).
SEARCH_LIMIT = _env_int("KBU_SEARCH_LIMIT", 5)
SEARCH_CHUNKS_PER_DOC = _env_int("KBU_SEARCH_CHUNKS_PER_DOC", 1)
SNIPPET_CHARS = _env_int("KBU_SNIPPET_CHARS", 700)
DOC_CHARS = _env_int("KBU_DOC_CHARS", 8000)

# Uzak MCP erişim anahtarları (boşsa kimlik doğrulama kapalı)
API_KEYS = {k.strip() for k in (os.environ.get("KBU_API_KEYS") or "").split(",") if k.strip()}

REPO_URL = "https://github.com/1ffatihgur/kbu-mcp"
USER_AGENT = f"KBU-MCP/{__version__} (+{REPO_URL})"

# Sunucu açılırken indeks yoksa / eskiyse arka planda güncelleme başlatılsın mı (KBU_AUTO_UPDATE=0 kapatır)
AUTO_UPDATE = os.environ.get("KBU_AUTO_UPDATE", "1") not in ("0", "false", "no")
FULL_UPDATE_HOURS = 24
HOT_UPDATE_HOURS = 1

# Sadece bu alan adı ve alt alan adlarına istek atılır.
ALLOWED_HOST_RE = re.compile(r"^([a-z0-9-]+\.)*karabuk\.edu\.tr$")

# Giriş gerektiren, test/geliştirme veya içerik barındırmayan sistemler: taranmaz.
EXCLUDED_HOST_RE = re.compile(
    r"^(obs|ebys|kimlik|odeme|www\.odeme|kbupos|cp|www\.cp|deneme|ays-test|avesis-demo|"
    r"sinavpanel|apiybs|ybs|chatdev|chatdev-api|kbuaiapi|cloud-kbubis|kbubis|fax|kbunet|"
    r"abis-files|abisis|isgys|labsis|kalsis|bapsis|kabis|notebookllm|eposta|mail|webmail|"
    r"posta|sanalkampus|uzem|lms|ekutuphane|dev\d*|test\d*|kbudosya|harcindirimi|"
    r"basvuru|www\.basvuru|tomersertifika|uygep|unikasaglik)\.karabuk\.edu\.tr$"
)

# Kapalı bir alan adında herkese açık olan bölümler: OBS içindeki Bologna / AKTS ders bilgi paketi.
EXCEPTION_URL_RE = re.compile(
    r"^https://obs\.karabuk\.edu\.tr/oibs/bologna/("
    r"unitSelection\.aspx\?.*lang=tr.*|"
    r"index\.aspx\?.*curOp=showPac.*|"
    r"prog(About|Courses|Officials|Contact|AdmissionReq|GraduationReq|LearnOutcomes|Degree|OccupationalProf|"
    r"AcademicStaff|Profile|GoalsObjectives|AccessFurhterStudies|QualifyReqReg)\.aspx\?(?=.*lang=tr).*)$",
    re.IGNORECASE,
)

# Taranmaz ama fetch_page ile canlı okunabilir (çok büyük / dinamik siteler).
LIVE_ONLY_HOST_RE = re.compile(r"^(avesis|akts|abs|oys|rehber|unis|takvim)\.karabuk\.edu\.tr$")

SEED_URLS = [
    "https://www.karabuk.edu.tr/",
    "https://www.karabuk.edu.tr/en/",
    "https://akademiktakvim.karabuk.edu.tr/index.aspx",
    "https://aday.karabuk.edu.tr/",
    "https://erasmus.karabuk.edu.tr/",
    "https://kapgem.karabuk.edu.tr/",
    "https://karabuktomer.karabuk.edu.tr/",
    "https://kbumedya.karabuk.edu.tr/",
    "https://yos.karabuk.edu.tr/",
    "https://aicenter.karabuk.edu.tr/",
    "https://demircelik.karabuk.edu.tr/",
    "https://kbudeha.karabuk.edu.tr/",
    "https://kbusanaltur.karabuk.edu.tr/",
    "https://sinav.karabuk.edu.tr/",
    "https://luyos.karabuk.edu.tr/",
    "https://bbf.karabuk.edu.tr/",
    "https://muh.karabuk.edu.tr/",
    "https://oidb.karabuk.edu.tr/",
    "https://sks.karabuk.edu.tr/",
    # Bologna / AKTS: lisans, önlisans (MYO), yüksek lisans, doktora program listeleri
    "https://obs.karabuk.edu.tr/oibs/bologna/unitSelection.aspx?type=lis&lang=tr",
    "https://obs.karabuk.edu.tr/oibs/bologna/unitSelection.aspx?type=myo&lang=tr",
    "https://obs.karabuk.edu.tr/oibs/bologna/unitSelection.aspx?type=yls&lang=tr",
    "https://obs.karabuk.edu.tr/oibs/bologna/unitSelection.aspx?type=dok&lang=tr",
]

DOC_EXTENSIONS = {
    ".pdf": "pdf", ".docx": "docx", ".doc": "doc", ".xlsx": "xlsx", ".xls": "xls",
    ".pptx": "pptx", ".ppt": "ppt", ".odt": "odt", ".ods": "ods", ".rtf": "rtf",
    ".txt": "txt", ".csv": "csv", ".zip": "zip",
}
SKIP_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".ico", ".tif", ".tiff",
    ".mp4", ".avi", ".mov", ".wmv", ".mkv", ".webm", ".mp3", ".wav", ".ogg", ".flv",
    ".css", ".js", ".woff", ".woff2", ".ttf", ".eot", ".exe", ".msi", ".apk", ".dmg",
    ".iso", ".rar", ".7z", ".gz", ".tar", ".psd", ".ai", ".eps", ".dwg", ".xml", ".json",
}

# Tarama tuzakları ve içeriksiz URL kalıpları (WordPress beslemeleri, giriş sayfaları, paylaşım linkleri...)
SKIP_URL_RE = re.compile(
    r"/(feed|wp-json|wp-admin|wp-login\.php|xmlrpc\.php|comments/feed|trackback)(/|$|\?)|"
    r"[?&](replytocom|share|print|format=amp|ical|outlook-ical|tribe-bar-date|eventDisplay|"
    r"orderby|order|filter_|add-to-cart|login|logout|redirect_to)=|"
    r"/(login|logout|giris|cikis)\.aspx",
    re.IGNORECASE,
)
MAX_URLS_PER_PATH = 8000       # aynı yolun farklı sorgu parametreli kopyaları için tavan

# Linklerden atılan sorgu parametreleri (aynı sayfanın kopyalarını üretiyorlar).
# Not: ASP.NET sitelerindeki BA parametresi ATILMAZ: sayfanın hangi alt sitede (ör. BA=bilgisayar
# → Bilgisayar Mühendisliği) gösterileceğini belirler; yoksa sunucu ana sayfaya yönlendirir.
DROP_QUERY_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_content", "fbclid", "gclid"}

PER_HOST_CONCURRENCY = 3
GLOBAL_CONCURRENCY = 24
PER_HOST_DELAY = 0.25          # saniye
REQUEST_TIMEOUT = 45.0
MAX_FILE_BYTES = _env_int("KBU_MAX_FILE_MB", 300) * 1024 * 1024
MAX_OCR_PAGES = _env_int("KBU_MAX_OCR_PAGES", 300)   # çok büyük taranmış PDF'lerde OCR edilen sayfa tavanı
# Tüm sitelere toplam saatlik istek sınırı (0 = yalnızca host başı gecikme). Üniversite sunucuları
# yoğun istekte engelleyebildiği için üretimde sınır önerilir.
CRAWL_MAX_PER_HOUR = _env_int("KBU_CRAWL_MAX_PER_HOUR", 0)
MEDYA_HOST = "kbumedya.karabuk.edu.tr"
MEDYA_MAX_AGE_DAYS = _env_int("KBU_MEDYA_MAX_AGE_DAYS", 730)   # KBÜ Medya: son 2 yıl
UNIS_HOST = "unis.karabuk.edu.tr"
PARSER_VERSION = 3   # 3: PDF tabloları Markdown; çıkarıcı değişince eski sürümle işlenmiş belgeler koşulsuz yeniden indirilir
MAX_DEPTH = 15
MAX_PAGES_PER_HOST = 25000
MAX_POSTBACK_PAGES = 150       # ASP.NET GridView sayfalama sınırı

CHUNK_CHARS = 1600
CHUNK_OVERLAP = 200

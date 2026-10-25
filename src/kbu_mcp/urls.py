"""URL normalizasyonu ve alan adı kuralları."""

import posixpath
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

from . import config


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def is_allowed_host(host: str) -> bool:
    return bool(config.ALLOWED_HOST_RE.match(host))


def is_crawlable_host(host: str) -> bool:
    return (
        is_allowed_host(host)
        and not config.EXCLUDED_HOST_RE.match(host)
        and not config.LIVE_ONLY_HOST_RE.match(host)
    )


def is_exception_url(url: str) -> bool:
    return bool(config.EXCEPTION_URL_RE.match(url))


def is_crawlable_url(url: str) -> bool:
    return is_crawlable_host(host_of(url)) or is_exception_url(url)


def is_live_fetchable(url: str) -> bool:
    host = host_of(url)
    return (is_allowed_host(host) and not config.EXCLUDED_HOST_RE.match(host)) or is_exception_url(url)


def extension(url: str) -> str:
    path = unquote(urlsplit(url).path).lower()
    _, ext = posixpath.splitext(path)
    return ext


def kind_from_url(url: str) -> str | None:
    return config.DOC_EXTENSIONS.get(extension(url))


def normalize(url: str, base: str | None = None) -> str | None:
    """Mutlak, karşılaştırılabilir URL üretir; taranmaya uygun değilse None."""
    if not url:
        return None
    url = url.strip().replace("\\", "/")
    low = url.lower()
    if low.startswith(("javascript:", "mailto:", "tel:", "data:", "#", "file:", "whatsapp:", "sms:")):
        return None
    if base:
        url = urljoin(base, url)
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return None
    if host == "karabuk.edu.tr":
        host = "www.karabuk.edu.tr"
    port = parts.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    path = parts.path or "/"
    path = posixpath.normpath(path) + ("/" if path.endswith("/") and path != "/" else "")
    if not path.startswith("/"):
        path = "/" + path
    path = path.replace("//", "/")
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in config.DROP_QUERY_PARAMS]
    query.sort()
    # Tüm site https destekliyor; http/https kopyalarını tek URL'de birleştir.
    return urlunsplit(("https", netloc, path, urlencode(query, doseq=True), ""))


def should_skip(url: str) -> bool:
    return extension(url) in config.SKIP_EXTENSIONS

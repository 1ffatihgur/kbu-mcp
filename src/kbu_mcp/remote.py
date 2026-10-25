"""Uzak (remote) MCP sunucusu: Streamable HTTP + tanıtım sayfası + hız sınırı + sunucu içi zamanlayıcı.

    kbu-mcp --http [--port 7860]

Hugging Face Spaces gibi kalıcı diski olmayan ücretsiz platformlar için tasarlandı:
açılışta indeksin son anlık görüntüsünü indirir (KBU_SNAPSHOT_REPO), saatlik/günlük güncellemeleri
kendisi çalıştırır ve günlük güncellemeden sonra yeni anlık görüntüyü geri yükler (HF_TOKEN yazma yetkili ise).
"""

import html
import json
import logging
import os
import subprocess
import sys
import threading
import time
from collections import defaultdict, deque

from . import __version__, config, snapshot, updater

log = logging.getLogger("kbu.remote")

RATE_LIMIT = int(os.environ.get("KBU_RATE_LIMIT_PER_MIN", "120"))   # IP başına dakikadaki MCP isteği
SCHEDULER_INTERVAL = 600                                              # güncelleme ihtiyacı kontrol aralığı (sn)
_state = {"snapshot": "bekleniyor", "started": time.time()}


# ============================================================================ hız sınırı

class RateLimitMiddleware:
    """IP başına kayan pencereli basit hız sınırı (yalnızca /mcp istekleri)."""

    def __init__(self, app, per_minute: int):
        self.app = app
        self.per_minute = per_minute
        self.hits: dict[str, deque] = defaultdict(deque)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/mcp") or self.per_minute <= 0:
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        fwd = headers.get(b"x-forwarded-for", b"").decode().split(",")[0].strip()
        ip = fwd or (scope.get("client") or ("?",))[0]
        now = time.monotonic()
        q = self.hits[ip]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_minute:
            body = json.dumps({"hata": "Çok fazla istek; lütfen bir dakika sonra tekrar deneyin."}, ensure_ascii=False).encode()
            await send({"type": "http.response.start", "status": 429,
                        "headers": [(b"content-type", b"application/json; charset=utf-8"), (b"retry-after", b"60")]})
            await send({"type": "http.response.body", "body": body})
            return
        q.append(now)
        if len(self.hits) > 50000:  # bellek koruması
            self.hits.clear()
        return await self.app(scope, receive, send)


# ============================================================================ arka plan işleri

def _run(args: list[str]) -> int:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, "-m", "kbu_mcp", *args], env=env).returncode


def _bootstrap_and_schedule() -> None:
    # 1) İndeks yoksa son anlık görüntüyü indir (dakikalar) — yoksa ilk tarama zamanlayıcıda başlar (1-2 saat)
    if not config.DB_PATH.exists():
        try:
            _state["snapshot"] = "indiriliyor"
            _state["snapshot"] = "kuruldu" if snapshot.pull() else "tanımlı değil (ilk tarama yapılacak)"
        except Exception as e:
            log.exception("anlık görüntü indirilemedi")
            _state["snapshot"] = f"hata: {e!r}"[:200]
    else:
        _state["snapshot"] = "mevcut indeks kullanılıyor"
    if not config.AUTO_UPDATE:
        return
    # 2) Zamanlayıcı: gerektiğinde saatlik / tam güncelleme; tam güncellemeden sonra anlık görüntüyü yükle
    can_push = bool(os.environ.get("KBU_SNAPSHOT_REPO") and (os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")))
    while True:
        try:
            mode = updater.needed_mode()
            if mode and not updater.lock_active():
                log.info("güncelleme başlıyor: %s", mode)
                rc = _run(["update"] + (["--hot"] if mode == "hot" else []))
                if mode == "full" and rc == 0 and can_push and os.environ.get("KBU_SNAPSHOT_PUSH", "1") != "0":
                    _run(["snapshot", "push"])
        except Exception:
            log.exception("zamanlayıcı hatası")
        time.sleep(SCHEDULER_INTERVAL)


# ============================================================================ sayfalar

_PAGE = """<!doctype html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>KBÜ MCP</title>
<style>
:root{{--bg:#fff;--fg:#1d2433;--mut:#5b6475;--card:#f4f6fa;--acc:#0b5cad;--code:#eef1f6}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0f1420;--fg:#e6e9ef;--mut:#9aa3b5;--card:#182031;--acc:#6aa8ff;--code:#1f2940}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}}
main{{max-width:760px;margin:0 auto;padding:32px 16px}}
h1{{font-size:28px;margin:0 0 4px}} h2{{font-size:19px;margin:28px 0 8px}}
p,li{{color:var(--fg)}} .mut{{color:var(--mut)}}
.card{{background:var(--card);border-radius:12px;padding:16px 18px;margin:12px 0}}
code,pre{{background:var(--code);border-radius:6px;font:14px ui-monospace,Consolas,monospace}}
code{{padding:2px 6px}} pre{{padding:12px;overflow-x:auto;white-space:pre-wrap;word-break:break-all}}
.url{{font-size:17px;font-weight:600;color:var(--acc)}} a{{color:var(--acc)}}
</style></head><body><main>
<h1>🎓 KBÜ MCP</h1>
<p class="mut">Karabük Üniversitesi web sitesi (*.karabuk.edu.tr) için uzak MCP sunucusu · sürüm {version}</p>
<div class="card">🔗 <b>Remote MCP adresi:</b><br><span class="url">{mcp_url}</span></div>

<h2>Claude Desktop / claude.ai</h2>
<ol><li><b>Settings → Connectors → Add custom connector</b></li>
<li><b>Name:</b> <code>KBÜ MCP</code> &nbsp; <b>URL:</b> <code>{mcp_url}</code></li>
<li><b>Add</b> — hemen kullanmaya başlayın.</li></ol>

<h2>Claude Code</h2>
<pre>claude mcp add --scope user --transport http karabuk-universitesi {mcp_url}</pre>

<h2>Codex</h2>
<pre>codex mcp add karabuk-universitesi --url {mcp_url}</pre>

<h2>Durum</h2>
<div class="card">{status}</div>
<p class="mut">Bu proje Karabük Üniversitesi'nin resmi ürünü değildir; yalnızca herkese açık sayfaları okur.
Kaynak kod ve yerel kurulum: <a href="{repo}">{repo}</a></p>
</main></body></html>"""


def _status_dict() -> dict:
    st = updater.status()
    st["anlik_goruntu"] = _state["snapshot"]
    st["calisma_suresi_dk"] = int((time.time() - _state["started"]) / 60)
    if config.DB_PATH.exists():
        try:
            from . import db
            c = db.connect(readonly=True)
            st["belge_sayisi"] = c.execute("SELECT COUNT(*) FROM docs WHERE status='ok'").fetchone()[0]
            c.close()
        except Exception:
            pass
    return st


def build_app():
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.requests import Request
    from starlette.responses import HTMLResponse, JSONResponse

    from .server import mcp

    @mcp.custom_route("/", methods=["GET"], include_in_schema=False)
    async def home(request: Request):
        base = str(request.base_url).rstrip("/")
        proto = request.headers.get("x-forwarded-proto")
        if proto and base.startswith("http://") and proto == "https":
            base = "https://" + base[len("http://"):]
        st = _status_dict()
        status = (f"Belge sayısı: <b>{st.get('belge_sayisi', '—')}</b> · Son tam güncelleme: "
                  f"<b>{html.escape(str(st.get('son_tam_guncelleme') or '—'))}</b> UTC · "
                  f"Güncelleme çalışıyor: <b>{'evet' if st.get('guncelleme_calisiyor') else 'hayır'}</b>")
        return HTMLResponse(_PAGE.format(version=__version__, mcp_url=html.escape(base + "/mcp"),
                                         status=status, repo=config.REPO_URL))

    @mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def health(request: Request):
        return JSONResponse({"ok": True, "surum": __version__, **_status_dict()})

    app = mcp.streamable_http_app(
        stateless_http=True,       # oturumsuz: yeniden başlatma/uyku sonrası istemciler sorunsuz devam eder
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),  # proxy arkasında
        host="0.0.0.0",
    )
    return RateLimitMiddleware(app, RATE_LIMIT)


def run(port: int) -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    threading.Thread(target=_bootstrap_and_schedule, name="kbu-scheduler", daemon=True).start()
    uvicorn.run(build_app(), host="0.0.0.0", port=port, proxy_headers=True, forwarded_allow_ips="*",
                log_level="info")

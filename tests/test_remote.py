import asyncio

from kbu_mcp import live
from kbu_mcp.remote import RateLimitMiddleware


def test_ttl_cache_caches_and_returns_copies(monkeypatch):
    calls = []

    @live.ttl_cached("test")
    async def f(x):
        calls.append(x)
        return {"liste": [x]}

    async def run():
        a = await f(1)
        a["liste"].append("değişti")  # çağıranın değişikliği önbelleği bozmamalı
        b = await f(1)
        return b

    b = asyncio.run(run())
    assert calls == [1]
    assert b == {"liste": [1]}


def test_rate_limit_blocks_after_limit():
    sent = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})

    mw = RateLimitMiddleware(app, per_minute=3)

    async def send(msg):
        if msg["type"] == "http.response.start":
            sent.append(msg["status"])

    async def run():
        for _ in range(5):
            await mw({"type": "http", "path": "/mcp", "headers": [(b"x-forwarded-for", b"1.2.3.4")]}, None, send)
        await mw({"type": "http", "path": "/", "headers": []}, None, send)  # tanıtım sayfası sınırlanmaz

    asyncio.run(run())
    assert sent == [200, 200, 200, 429, 429, 200]

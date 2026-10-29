"""Açık kaynak arama motoru denemesi: aynı veriyle Meilisearch (hibrit) ve KBÜ MCP aramasının karşılaştırması.

    docker run -d --name kbu-meili -p 127.0.0.1:7700:7700 -e MEILI_MASTER_KEY=deneme getmeili/meilisearch:v1.15
    python eval/compare_meilisearch.py --load      # PostgreSQL'deki parçaları + embedding'leri yükler (bir kez)
    python eval/compare_meilisearch.py             # altın soru setiyle recall@5 karşılaştırması

Meilisearch'e bizim kullandığımız parçalar ve bge-m3 vektörleri (userProvided embedder) verilir; böylece fark
yalnızca arama/sıralama motorundan gelir.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

from kbu_mcp import db, embed
from kbu_mcp.search import search
from kbu_mcp.textnorm import normalize_for_index

URL = "http://127.0.0.1:7700"
KEY = "deneme"
H = {"Authorization": f"Bearer {KEY}"}
IDX = "kbu"


def _wait(task_uid: int) -> dict:
    while True:
        t = httpx.get(f"{URL}/tasks/{task_uid}", headers=H, timeout=60).json()
        if t["status"] in ("succeeded", "failed", "canceled"):
            return t
        time.sleep(2)


def load() -> None:
    httpx.delete(f"{URL}/indexes/{IDX}", headers=H, timeout=60)
    _wait(httpx.post(f"{URL}/indexes", headers=H, json={"uid": IDX, "primaryKey": "id"}, timeout=60).json()["taskUid"])
    settings = {
        "searchableAttributes": ["title", "unit", "text"],
        "filterableAttributes": ["source", "kind", "doc_id"],
        "sortableAttributes": ["date"],
        "distinctAttribute": "doc_id",
        "embedders": {"bge": {"source": "userProvided", "dimensions": 1024}},
    }
    _wait(httpx.patch(f"{URL}/indexes/{IDX}/settings", headers=H, json=settings, timeout=60).json()["taskUid"])
    conn = db.connect(readonly=True)
    last, n, t0 = 0, 0, time.time()
    while True:
        rows = conn.execute(
            """SELECT c.id, c.doc_id, c.text, c.unit_n, c.embedding::text AS emb, d.title, d.anchor_text, d.url, d.final_url,
                      d.kind, d.source, d.content_date
               FROM chunks c JOIN docs d ON d.id = c.doc_id
               WHERE c.id > ? AND d.status IN ('ok', 'recheck') AND c.embedding IS NOT NULL ORDER BY c.id LIMIT 2000""",
            (last,)).fetchall()
        if not rows:
            break
        docs = [{"id": r["id"], "doc_id": r["doc_id"], "text": r["text"], "unit": r["unit_n"],
                 "title": " ".join(x for x in (r["title"], r["anchor_text"]) if x), "url": r["final_url"] or r["url"],
                 "kind": r["kind"], "source": r["source"], "date": r["content_date"] or "",
                 "_vectors": {"bge": json.loads(r["emb"])}} for r in rows]
        task = httpx.post(f"{URL}/indexes/{IDX}/documents", headers=H, json=docs, timeout=300).json()
        last, n = rows[-1]["id"], n + len(rows)
        print(f"{n} parça gönderildi ({n / (time.time() - t0):.0f}/sn)", flush=True)
    print("indeksleme bekleniyor...", flush=True)
    print(_wait(task["taskUid"])["status"], f"{time.time() - t0:.0f} sn")


def meili(query: str, ratio: float, k: int) -> list[dict]:
    body = {"q": query, "limit": k, "attributesToRetrieve": ["url", "title", "text"]}
    if ratio > 0:
        vec = embed.query_vector(query)
        body |= {"vector": json.loads(vec), "hybrid": {"embedder": "bge", "semanticRatio": ratio}}
    return httpx.post(f"{URL}/indexes/{IDX}/search", headers=H, json=body, timeout=60).json()["hits"]


def evaluate(k: int) -> None:
    questions = [q for q in json.loads((Path(__file__).parent / "questions.json").read_text(encoding="utf-8"))
                 if q.get("tool", "search") == "search"]
    conn = db.connect(readonly=True)
    systems = {
        "KBÜ MCP hibrit": lambda q: [{"url": r["url"], "title": r["baslik"], "text": " ".join(p["metin"] for p in r["parcalar"])}
                                     for r in search(conn, q, limit=k, chunks_per_doc=3)],
        "Meilisearch anahtar kelime": lambda q: meili(q, 0.0, k),
        "Meilisearch hibrit (0.5)": lambda q: meili(q, 0.5, k),
    }
    for name, fn in systems.items():
        hits, t0, misses = 0, time.time(), []
        for q in questions:
            expect = normalize_for_index(q["expect"])
            ok = any(expect in normalize_for_index(f"{h.get('title') or ''} {h.get('text') or ''}")
                     or (q.get("expect_url") and q["expect_url"] in (h.get("url") or "")) for h in fn(q["query"]))
            hits += ok
            if not ok:
                misses.append(q["query"])
        dt = (time.time() - t0) / len(questions)
        print(f"{name:28} recall@{k}: {hits}/{len(questions)} = {hits / len(questions):.0%}   ort. {dt * 1000:.0f} ms/sorgu")
        for m in misses:
            print(f"{'':30}✗ {m}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--load", action="store_true")
    ap.add_argument("--k", type=int, default=5)
    args = ap.parse_args()
    load() if args.load else evaluate(args.k)

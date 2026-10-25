"""Telefon rehberinin tamamını yerel önbelleğe alır (canlı rehber yanıt vermezse yedek olarak)."""

import asyncio
import logging

from . import db
from .crawler import now_iso
from .live import rehber_options, rehber_search
from .textnorm import normalize_for_index

log = logging.getLogger("kbu.people")


async def refresh_people(conn) -> int:
    opts = await rehber_options()
    rows: list[dict] = []
    # Tek "Tümü" sorgusu sayfa sınırına takılabileceği için birim birim çekiyoruz.
    for name in opts["birimler"]:
        try:
            res = await rehber_search(birim=name, max_pages=40)
        except Exception as e:
            log.warning("rehber birimi alınamadı %s: %r", name, e)
            continue
        if res["eslenen_birim"] != name:
            continue
        rows.extend(res["kisiler"])
        await asyncio.sleep(0.3)
    if not rows:
        return 0
    with db.tx(conn):
        conn.execute("DELETE FROM people")
        for r in rows:
            search = normalize_for_index(" ".join(str(r.get(k, "")) for k in ("unvan", "ad", "soyad", "gorev", "birim", "dahili", "eposta")))
            conn.execute(
                "INSERT INTO people(tip, unvan, ad, soyad, gorev, birim, telefon, dahili, eposta, search) VALUES(?,?,?,?,?,?,?,?,?,?)",
                tuple(r.get(k) for k in ("tip", "unvan", "ad", "soyad", "gorev", "birim", "telefon", "dahili", "eposta")) + (search,),
            )
        conn.execute("DELETE FROM units WHERE source='rehber'")
        conn.executemany("INSERT OR IGNORE INTO units(name, source) VALUES(?, 'rehber')", [(n,) for n in opts["birimler"]])
        db.set_meta(conn, "people_refreshed", now_iso())
    return len(rows)

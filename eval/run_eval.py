"""Arama kalitesi ölçümü: her soru için beklenen metnin ilk k sonuç içinde geçip geçmediğine bakar.

Kullanım:  .venv\\Scripts\\python eval\\run_eval.py [--k 5] [--verbose]
"""

import argparse
import json
import sys
from pathlib import Path

from kbu_mcp import db
from kbu_mcp.search import search
from kbu_mcp.textnorm import normalize_for_index


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    questions = json.loads((Path(__file__).parent / "questions.json").read_text(encoding="utf-8"))
    conn = db.connect(readonly=True)
    hits, by_cat = 0, {}
    for q in questions:
        if q.get("tool", "search") != "search":
            continue
        res = search(conn, q["query"], unit=q.get("unit"), limit=args.k, chunks_per_doc=3)
        expect = normalize_for_index(q["expect"])
        rank = None
        for i, r in enumerate(res, start=1):
            blob = normalize_for_index(" ".join(p["metin"] for p in r["parcalar"]) + " " + (r["baslik"] or ""))
            if expect in blob or (q.get("expect_url") and q["expect_url"] in r["url"]):
                rank = i
                break
        ok = rank is not None
        hits += ok
        c = by_cat.setdefault(q.get("cat", "genel"), [0, 0])
        c[0] += ok
        c[1] += 1
        if args.verbose or not ok:
            mark = f"✓ #{rank}" if ok else "✗"
            print(f"{mark:5} [{q.get('cat')}] {q['query']}  →  beklenen: {q['expect']}")
            if not ok and res:
                print("        ilk sonuç:", res[0]["baslik"], res[0]["url"])
    total = sum(v[1] for v in by_cat.values())
    print(f"\nrecall@{args.k}: {hits}/{total} = {hits / max(1, total):.0%}")
    for cat, (h, n) in sorted(by_cat.items()):
        print(f"  {cat:14} {h}/{n}")


if __name__ == "__main__":
    main()

"""Hibrit arama: PostgreSQL tam metin (anahtar kelime) + pgvector (anlamsal) → Reciprocal Rank Fusion.

1. Anahtar kelime: Türkçe katlanmış tsvector, kök önekleri (muhendisli:*), başlık/birim ağırlıkları;
   sonuçlar başlık/birim kapsamı, iletişim niyeti ve bölüm alt sitesi kurallarıyla yeniden puanlanır.
2. Anlamsal: sorgu embedding'ine en yakın parçalar (eş anlamlı / farklı ifade edilmiş sorular).
3. RRF: iki sıralama birleştirilir; ardından tazelik (yeni tarihli belge öne) uygulanır.
"""

import logging
import math
import os
import re
import time
from datetime import date

from . import embed
from .textnorm import STOPWORDS, _ts, build_tsquery, fold, normalize_for_index, phrase_query, query_terms, stem, words

log = logging.getLogger("kbu.search")

QUESTION_WORDS = STOPWORDS - {"üniversitesi", "üniversite", "karabük", "kbü", "kbu"}

KIND_NAMES = {
    "sayfa": ["html"], "html": ["html"], "pdf": ["pdf"], "word": ["docx", "doc", "odt", "rtf"],
    "excel": ["xlsx", "xls", "ods", "csv"], "sunum": ["pptx", "ppt"], "dosya": ["pdf", "docx", "doc", "xlsx", "xls", "pptx", "ppt", "odt", "ods", "rtf", "zip", "txt", "csv"],
}


def _recency_factor(d: str | None) -> float:
    if not d:
        return 1.0
    try:
        age_days = (date.today() - date.fromisoformat(d[:10])).days
    except ValueError:
        return 1.0
    if age_days < 0:
        return 1.15
    # 0 gün: 1.12, 1 yıl: ~1.05, 4+ yıl: ~1.0
    return 1.0 + 0.12 * math.exp(-age_days / 400)


def _query_stems(query: str) -> list[str]:
    return [fold(stem(w))[:6] for w in words(query) if w not in QUESTION_WORDS and len(w) > 1]


def _coverage(qstems: list[str], text_norm: str) -> float:
    """Sorgu köklerinin (soru kelimeleri hariç, 'üniversite' dahil) metinde geçme oranı."""
    if not qstems:
        return 0.0
    tw = text_norm.split()
    return sum(1 for t in qstems if any(x.startswith(t) for x in tw)) / len(qstems)


def _title_in_query(qstems: list[str], title_norm: str) -> bool:
    """Başlığın tüm kelimeleri sorguda geçiyor mu? ("Üniversite Yönetim Kurulu" ⊆ sorgu)"""
    tw = [w for w in title_norm.split() if len(w) > 2]
    if len(tw) < 2 or len(tw) > 8:
        return False
    return all(any(w.startswith(q) or q.startswith(w[:6]) for q in qstems) for w in tw)


CONTACT_STEMS = ("telefo", "tel", "numara", "dahili", "adres", "eposta", "mail", "iletis", "ulas", "contac", "phone")
CONTACT_URL_RE = re.compile(r"iletisim|contact|personel|rehber", re.I)


def _boost(qstems: list[str], title_norm: str, unit_norm: str, url: str, body_norm: str) -> float:
    b = 1 + 0.8 * _coverage(qstems, title_norm)
    if _title_in_query(qstems, title_norm):
        b *= 1.8
    # Sorgudaki birim adı (ör. "bilgisayar", "mühendislik") sonucun birim etiketinde geçiyorsa
    b *= 1 + 0.5 * _coverage([q for q in qstems if not q.startswith(CONTACT_STEMS)], unit_norm)
    # "fakülte" sorusunda bölüm alt sitesi (BA=bilgisayar...) sayfaları geri, "bölüm" sorusunda öne
    if re.search(r"[?&]BA=(?!index\.aspx|en(&|$))", url, re.I):
        if any(q.startswith("fakult") for q in qstems) and not any(q.startswith("bol") for q in qstems):
            b *= 0.7
        elif any(q.startswith("bol") for q in qstems):
            b *= 1.15
    # İletişim sorusu: iletişim/personel sayfaları ve numara içeren parçalar öne
    if any(q.startswith(CONTACT_STEMS) for q in qstems):
        if CONTACT_URL_RE.search(url) or "iletisim" in title_norm:
            b *= 1.6
        if "numara dahili iletisim" in body_norm or "eposta mail iletisim" in body_norm:
            b *= 1.3
    return b


def _unit_filter(unit: str | None) -> str | None:
    """Birim adı → yalnızca birim etiketinde (ağırlık B) geçen önekler."""
    if not unit:
        return None
    toks = [re.sub(r"[^0-9a-z]", "", fold(w)) for w in words(unit)]
    toks = [t for t in toks if len(t) >= 2]
    if not toks:
        return None
    return " & ".join(f"{t[:6]}:*B" if len(t) >= 3 else f"{t}:B" for t in toks)


SOURCES = {"site", "unis", "medya", "bologna"}
RRF_K = 60
# Eşit ağırlık eval setinde en iyisi (0.6: %93, 1.0: %96, 1.5: %93). Anahtar kelime sıralaması alan kurallarını içerir;
# anlamsal sıralama eş anlamlı / farklı ifade edilmiş soruları yakalar.
SEMANTIC_WEIGHT = float(os.environ.get("KBU_SEMANTIC_WEIGHT", "1.0"))
_COLS = """c.id AS chunk_id, c.doc_id, c.page, c.text, c.unit_n, c.tags, d.url, d.final_url, d.kind, d.title,
           d.site_title, d.anchor_text, d.content_date, d.checked_at, d.needs_ocr, d.source"""
_TAG_WORDS = {"telefon": "numara dahili iletisim", "eposta": "eposta mail iletisim"}


def _filters(kinds, date_from, source) -> tuple[str, list]:
    sql, params = " AND d.status IN ('ok', 'recheck')", []  # 'recheck': yeniden kontrol sırasında da aranabilir
    if kinds:
        sql += f" AND d.kind IN ({','.join('?' * len(kinds))})"
        params += kinds
    if date_from:
        sql += " AND d.content_date >= ?"
        params.append(date_from)
    if source:
        sql += " AND d.source = ?"
        params.append(source)
    return sql, params


def _ts_group(g: list[str]) -> str:
    return "(" + " | ".join(_ts(t) for t in g) + ")" if len(g) > 1 else _ts(g[0])


_DF_CACHE: dict[str, tuple[float, int]] = {}
_W = "'{0.05, 0.17, 0.33, 1.0}'"   # D, C (gövde), B (birim), A (başlık) — v1'deki BM25 ağırlıklarıyla aynı oran


def _idf(conn, term_q: str) -> float:
    """BM25 tarzı IDF: nadir terimler (ör. 'bilgisayar') sık terimlerden ('mühendislik') daha belirleyici.
    PostgreSQL ts_rank IDF içermediği için terim başına belge sıklığı ölçülür (1 saat önbellek)."""
    now = time.time()
    hit = _DF_CACHE.get(term_q)
    if hit and now - hit[0] < 3600:
        return hit[1]
    n = _DF_CACHE.get("__N__")
    if not n or now - n[0] > 3600:
        n = (now, conn.execute("SELECT reltuples::bigint FROM pg_class WHERE relname='chunks'").fetchone()[0] or 1)
        _DF_CACHE["__N__"] = n
    df = conn.execute("SELECT COUNT(*) FROM chunks WHERE tsv @@ to_tsquery('simple', ?)", (term_q,)).fetchone()[0]
    idf = math.log(1 + (n[1] - df + 0.5) / (df + 0.5))
    if len(_DF_CACHE) > 5000:
        _DF_CACHE.clear()
    _DF_CACHE[term_q] = (now, idf)
    return idf


# BM25F: alan ağırlıkları (başlık, birim, gövde), k1, b ve ortalama alan uzunlukları (kelime)
_FIELD_W = (6.0, 2.0, 1.0)
_AVG_LEN = (8.0, 12.0, 230.0)
_K1, _B = 1.2, 0.75


def _tf(term_groups: list[list[str]], tokens: list[str]) -> list[int]:
    """Her kelime grubu için alandaki eşleşme sayısı (önek 'x*', tam '"x"', ifade '"a b"')."""
    out = []
    for g in term_groups:
        n = 0
        for t in g:
            if t.endswith("*"):
                p = t[:-1]
                n += sum(1 for w in tokens if w.startswith(p))
            else:
                ws = t.strip('"').split()
                if len(ws) == 1:
                    n += tokens.count(ws[0])
                else:
                    n += sum(1 for k in range(len(tokens) - len(ws) + 1) if tokens[k:k + len(ws)] == ws)
        out.append(n)
    return out


def _bm25f(term_groups, idfs, fields: tuple[list[str], list[str], list[str]]) -> float:
    """v1'deki SQLite FTS5 bm25(6, 2, 1) ile aynı mantık: terim tekrarı doygun, uzun parça cezalı."""
    score = 0.0
    for w, avg, toks in zip(_FIELD_W, _AVG_LEN, fields):
        if not toks:
            continue
        norm = _K1 * (1 - _B + _B * len(toks) / avg)
        for idf, tf in zip(idfs, _tf(term_groups, toks)):
            if tf:
                score += w * idf * tf * (_K1 + 1) / (tf + norm)
    return score


def _keyword(conn, query, uf, kinds, date_from, source, limit) -> dict[int, dict]:
    qstems = _query_stems(query)
    hits: dict[int, dict] = {}
    fsql, fparams = _filters(kinds, date_from, source)
    term_groups = query_terms(query)
    groups = [_ts_group(g) for g in term_groups]
    try:
        idfs = [_idf(conn, g) for g in groups]
    except Exception as e:
        log.warning("IDF hesaplanamadı: %s", e)
        idfs = [1.0] * len(groups)
    # Aday seçimi PostgreSQL'de (IDF ağırlıklı ts_rank), son puan Python'da BM25F ile
    rank_sql = " + ".join(f"{idf:.4f} * ts_rank({_W}, c.tsv, to_tsquery('simple', ?), 33)" for idf in idfs) or "0"
    for i, mode in enumerate(("and", "or")):
        expr = build_tsquery(query, mode)
        if not expr:
            return hits
        if uf:
            expr = f"({expr}) & ({uf})"
        sql = f"""SELECT {_COLS}, ({rank_sql}) AS rank
                  FROM chunks c JOIN docs d ON d.id = c.doc_id
                  WHERE c.tsv @@ to_tsquery('simple', ?) {fsql} ORDER BY rank DESC LIMIT 800"""
        try:
            rows = conn.execute(sql, [*groups, expr, *fparams]).fetchall()
        except Exception as e:
            log.warning("anahtar kelime sorgusu hatası (%s): %s", expr, e)
            continue
        penalty = 1.0 if i == 0 else 0.55  # OR sonuçları AND sonuçlarının gerisinde kalsın
        for r in rows:
            title_norm = normalize_for_index(" ".join(x for x in (r["title"], r["anchor_text"]) if x))
            tags_n = " ".join(_TAG_WORDS[t] for t in (r["tags"] or "").split() if t in _TAG_WORDS)
            body = (normalize_for_index(r["text"]) + " " + tags_n).split()
            bm25 = _bm25f(term_groups, idfs, (title_norm.split(), (r["unit_n"] or "").split(), body))
            score = bm25 * penalty * _boost(qstems, title_norm, r["unit_n"] or "", r["final_url"] or r["url"], tags_n)
            prev = hits.get(r["chunk_id"])
            if not prev or prev["kw"] < score:
                hits[r["chunk_id"]] = {**dict(r), "kw": score}
        if i == 0 and len({h["doc_id"] for h in hits.values()}) >= limit:
            break
    p = phrase_query(query)
    if p:  # tam ifade geçen parçalara ek puan
        needle = p.strip('"')
        for h in hits.values():
            if needle in normalize_for_index(h["text"]):
                h["kw"] *= 1.5
    return hits


def _semantic(conn, query, uf, kinds, date_from, source, k: int = 80) -> list[dict]:
    vec = embed.query_vector(query)
    if not vec:
        return []
    fsql, fparams = _filters(kinds, date_from, source)
    if uf:
        fsql += " AND c.tsv @@ to_tsquery('simple', ?)"
        fparams.append(uf)
    sql = f"""SELECT {_COLS}, c.embedding <=> ?::halfvec AS dist
              FROM chunks c JOIN docs d ON d.id = c.doc_id
              WHERE c.embedding IS NOT NULL {fsql} ORDER BY c.embedding <=> ?::halfvec LIMIT {k}"""
    try:
        with conn.pg.transaction():
            conn.execute("SET LOCAL hnsw.ef_search = 200")
            # pgvector ≥ 0.8: filtreli aramada HNSW sonuç eksiltmesin
            conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
            return [dict(r) for r in conn.execute(sql, [vec, *fparams, vec]).fetchall()]
    except Exception as e:
        log.warning("anlamsal arama hatası: %s", e)
        return []


def search(conn, query: str, unit: str | None = None, kind: str | None = None, date_from: str | None = None,
           limit: int = 8, chunks_per_doc: int = 2, source: str | None = None, semantic: bool = True) -> list[dict]:
    uf = _unit_filter(unit)
    kinds = KIND_NAMES.get((kind or "").lower()) if kind else None
    source = source if source in SOURCES else None

    hits = _keyword(conn, query, uf, kinds, date_from, source, limit)
    sem = _semantic(conn, query, uf, kinds, date_from, source) if semantic else []

    # Reciprocal Rank Fusion: anahtar kelime sıralaması (kurallarla yeniden puanlanmış) + anlamsal sıralama
    for rank, h in enumerate(sorted(hits.values(), key=lambda x: -x["kw"]), start=1):
        h["score"] = 1.0 / (RRF_K + rank)
    for rank, r in enumerate(sem, start=1):
        h = hits.setdefault(r["chunk_id"], {**r, "score": 0.0})
        h["score"] += SEMANTIC_WEIGHT / (RRF_K + rank)
        h["sem"] = True
    for h in hits.values():
        h["score"] *= _recency_factor(h["content_date"])

    by_doc: dict[int, list[dict]] = {}
    for h in sorted(hits.values(), key=lambda x: -x["score"]):
        lst = by_doc.setdefault(h["doc_id"], [])
        if len(lst) < chunks_per_doc:
            lst.append(h)
    ranked = sorted(by_doc.values(), key=lambda lst: -lst[0]["score"])[:limit]

    results = []
    for lst in ranked:
        top = lst[0]
        results.append({
            "url": top["final_url"] or top["url"],
            "kaynak": top["source"],
            "tur": top["kind"],
            "baslik": top["anchor_text"] if top["kind"] != "html" and top["anchor_text"] else top["title"],
            "birim_sitesi": top["site_title"],
            "tarih": top["content_date"],
            "son_kontrol": top["checked_at"],
            # needs_ocr: 1 = taranmış, metin eksik; 2 = OCR ile okundu; 3 = OCR denendi, okunamadı
            "ocr_gerekebilir": top["needs_ocr"] in (1, 3),
            "ocr_ile_okundu": top["needs_ocr"] == 2,
            "eslesme": ("anahtar+anlamsal" if "kw" in top else "anlamsal") if top.get("sem") else "anahtar",
            "skor": round(top["score"] * 1000, 2),
            "parcalar": [{"sayfa": h["page"], "metin": h["text"]} for h in lst],
        })
    return results


def search_people(conn, query: str, limit: int = 30) -> list[dict]:
    """Önbellekteki rehber kayıtlarında arama (canlı rehbere erişilemezse yedek)."""
    """Kelime başı eşleşmesiyle puanlar: sorgu köklerinin kaçı kişinin ad/görev/birim kelimelerinin başında
    geçiyor; görevin tamamı sorguda geçiyorsa (ör. "dekan", "bölüm sekreteri") ek puan."""
    toks = []
    for g in query_terms(query):  # kök önekleri: "dekanı" → "dekan", "fakültesi" → "fakultes"
        t = g[0].strip('"*')
        if len(t) >= 2:
            toks.append(t)
    if not toks:
        return []
    scored = []
    for r in conn.execute("SELECT * FROM people"):
        name_w = normalize_for_index(f"{r['ad']} {r['soyad']}").split()
        role_w = normalize_for_index(f"{r['gorev']} {r['unvan']}").split()
        unit_w = normalize_for_index(r["birim"] or "").split()
        all_w = name_w + role_w + unit_w + [str(r["dahili"] or ""), (r["eposta"] or "").split("@")[0]]
        hit = [t for t in toks if any(w.startswith(t) for w in all_w)]
        if not hit:
            continue
        score = float(len(hit))
        gorev_w = normalize_for_index(r["gorev"] or r["unvan"] or "").split()
        if gorev_w and all(any(g.startswith(t) or t.startswith(g[:5]) for t in toks) for g in gorev_w):
            score += 1.5 + 0.5 * len(gorev_w)  # "Genel Sekreter" > "Sekreter"
        if all(any(w.startswith(t) for w in name_w) for t in toks if len(t) > 2) and len(toks) <= 3:
            score += 1.0  # sadece isimle arama
        scored.append((score, len(hit), r))
    if not scored:
        return []
    best = max(s for s, _, _ in scored)
    need = max(2.0, len(toks) - 0.5) if len(toks) > 1 else 1.0
    rows = [r for s, n, r in sorted(scored, key=lambda x: -x[0]) if s >= max(need, best - 0.5)][:limit]
    return [{k: r[k] for k in ("tip", "unvan", "ad", "soyad", "gorev", "birim", "telefon", "dahili", "eposta", "updated_at")} for r in rows]

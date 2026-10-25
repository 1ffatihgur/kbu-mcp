"""İndeks üzerinde arama: FTS5 BM25 + başlık/birim ağırlığı + tazelik etkisi."""

import math
import re
from datetime import date

from .textnorm import STOPWORDS, build_fts_query, fold, normalize_for_index, phrase_query, query_terms, stem, words

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
    if not unit:
        return None
    toks = [re.sub(r"[^0-9a-z]", "", fold(w)) for w in words(unit)]
    toks = [t for t in toks if len(t) >= 2]
    if not toks:
        return None
    return "unit : (" + " AND ".join(f"{t[:6]}*" if len(t) >= 3 else t for t in toks) + ")"


def search(conn, query: str, unit: str | None = None, kind: str | None = None, date_from: str | None = None,
           limit: int = 8, chunks_per_doc: int = 2) -> list[dict]:
    exprs = []
    p = phrase_query(query)
    for mode in ("and", "or"):
        q = build_fts_query(query, mode)
        if q:
            exprs.append(q)
    if not exprs:
        return []
    uf = _unit_filter(unit)
    kinds = KIND_NAMES.get((kind or "").lower()) if kind else None

    qstems = _query_stems(query)
    hits: dict[int, dict] = {}
    for i, expr in enumerate(exprs):
        match = f"({expr}) AND {uf}" if uf else expr
        sql = """
            SELECT c.id AS chunk_id, c.doc_id, c.page, c.text, d.url, d.final_url, d.kind, d.title, d.site_title,
                   d.anchor_text, d.content_date, d.checked_at, d.needs_ocr,
                   chunks_fts.unit AS unit_n, chunks_fts.body AS body_n, bm25(chunks_fts, 6.0, 2.0, 1.0) AS rank
            FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid JOIN docs d ON d.id = c.doc_id
            WHERE chunks_fts MATCH ? AND d.status = 'ok'
        """
        params: list = [match]
        if kinds:
            sql += f" AND d.kind IN ({','.join('?' * len(kinds))})"
            params += kinds
        if date_from:
            sql += " AND d.content_date >= ?"
            params.append(date_from)
        sql += " ORDER BY rank LIMIT 300"
        try:
            rows = conn.execute(sql, params).fetchall()
        except Exception:
            continue
        penalty = 1.0 if i == 0 else 0.55  # OR sonuçları AND sonuçlarının gerisinde kalsın
        for r in rows:
            title_norm = normalize_for_index(" ".join(x for x in (r["title"], r["anchor_text"]) if x))
            score = -r["rank"] * penalty * _recency_factor(r["content_date"]) * _boost(
                qstems, title_norm, r["unit_n"], r["final_url"] or r["url"], r["body_n"][-60:])
            prev = hits.get(r["chunk_id"])
            if not prev or prev["score"] < score:
                hits[r["chunk_id"]] = {**dict(r), "score": score}
        if i == 0 and len({h["doc_id"] for h in hits.values()}) >= limit:
            break

    # Tam ifade geçen parçalara ek puan
    if p:
        needle = p.strip('"')
        for h in hits.values():
            if needle in normalize_for_index(h["text"]):
                h["score"] *= 1.5

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
            "tur": top["kind"],
            "baslik": top["anchor_text"] if top["kind"] != "html" and top["anchor_text"] else top["title"],
            "birim_sitesi": top["site_title"],
            "tarih": top["content_date"],
            "son_kontrol": top["checked_at"],
            # needs_ocr: 1 = taranmış, metin eksik; 2 = OCR ile okundu; 3 = OCR denendi, okunamadı
            "ocr_gerekebilir": top["needs_ocr"] in (1, 3),
            "ocr_ile_okundu": top["needs_ocr"] == 2,
            "skor": round(top["score"], 2),
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

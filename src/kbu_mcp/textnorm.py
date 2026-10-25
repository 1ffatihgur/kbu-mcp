"""Türkçe metin normalizasyonu ve FTS5 sorgu üretimi.

İndekse yazılan metin ve sorgular aynı şekilde normalize edilir:
Türkçe kurallarıyla küçük harf (İ→i, I→ı), ardından aksan katlama
(ç→c, ğ→g, ı→i, ö→o, ş→s, ü→u). Böylece "Müdürlüğü" ile "mudurlugu" eşleşir.
Sorgu kelimeleri Snowball Türkçe kök bulucuyla köklerine indirilip önek
araması (kök*) yapılır; ekli/eksiz bütün biçimler yakalanır.
"""

import re
import unicodedata

import snowballstemmer

_stemmer = snowballstemmer.stemmer("turkish")

_FOLD = str.maketrans({"ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ş": "s", "ü": "u", "â": "a", "î": "i", "û": "u"})
_WORD_RE = re.compile(r"[0-9a-zçğıöşüâîû]+", re.IGNORECASE)

STOPWORDS = {
    "ve", "veya", "ile", "ya", "da", "de", "mi", "mı", "mu", "mü", "mısın", "misin", "midir", "mıdır",
    "ne", "neler", "nedir", "nelerdir", "kim", "kimdir", "kimler", "kaç", "kaçta", "nasıl", "nerede",
    "nereden", "nereye", "hangi", "hangisi", "zaman", "ne zaman", "için", "icin", "bu", "şu", "o", "bir",
    "olan", "olarak", "gibi", "daha", "en", "çok", "her", "hakkında", "hakkinda", "bilgi", "bilgisi",
    "ver", "verir", "misiniz", "musunuz", "lütfen", "acaba", "var", "yok", "the", "of", "and", "is",
    "what", "who", "how", "where", "when", "a", "an", "in", "to", "for", "üniversitesi", "üniversite",
    "karabük", "kbü", "kbu",
}

# Sorgu genişletme: kullanıcıların sık kullandığı kısaltma ve eş anlamlılar.
SYNONYMS = {
    "telefon": ["tel", "dahili"],
    "telefonu": ["tel", "dahili"],
    "adres": ["adres", "iletişim"],
    "adresi": ["adres", "iletişim"],
    "iletişim": ["iletişim", "tel"],
    "numara": ["tel", "telefon"],
    "numarası": ["tel", "telefon"],
    "mail": ["eposta", "e-posta"],
    "email": ["eposta", "e-posta"],
    "eposta": ["mail"],
    "müh": ["mühendisliği"],
    "muh": ["mühendisliği"],
    "bşk": ["başkanı", "başkanlığı"],
    "baskan": ["başkanı"],
    "hoca": ["öğretim", "dr"],
    "yemekhane": ["yemek", "menü"],
    "yurt": ["yurt", "barınma"],
    "harç": ["harç", "ücret"],
    "ücret": ["harç", "ücret"],
    "staj": ["staj", "işletmede"],
    "çap": ["çift", "anadal"],
    "erasmus": ["erasmus", "hareketlilik"],
}


def tr_lower(s: str) -> str:
    return s.replace("İ", "i").replace("I", "ı").lower()


def fold(s: str) -> str:
    """Türkçe küçük harf + aksan katlama (indeks ve sorgu için ortak biçim)."""
    s = unicodedata.normalize("NFC", tr_lower(s))
    s = s.replace("i̇", "i")  # "İ".lower() artığı
    return s.translate(_FOLD)


def normalize_for_index(text: str) -> str:
    return " ".join(_WORD_RE.findall(fold(text)))


def words(text: str) -> list[str]:
    return _WORD_RE.findall(tr_lower(unicodedata.normalize("NFC", text)))


def stem(word: str) -> str:
    st = _stemmer.stemWord(word)
    # Çok kısa köklerde önek araması fazla gürültü üretir.
    if len(st) < 3:
        return word
    return st


def _term(word: str, prefix: bool = True) -> str | None:
    w = word
    if w.isdigit():
        return f'"{w}"'
    fw = re.sub(r"[^0-9a-z]", "", fold(w))
    if not fw:
        return None
    s = fw
    if prefix:
        # Snowball kökü sondaki ünsüzü sertleştirir ("mühendisliği"→"mühendislik", "doğa"→"dok").
        # Kök ile kelimenin ortak öneki hem eki atar hem yumuşamayı yakalar: "muhendisli*", "doga*".
        st = re.sub(r"[^0-9a-z]", "", fold(stem(w)))
        common = 0
        while common < min(len(st), len(fw)) and st[common] == fw[common]:
            common += 1
        s = fw[:common] if common >= 3 else fw
    if prefix and len(s) >= 3:
        return f"{s}*"
    return f'"{s}"'


def query_terms(query: str) -> list[list[str]]:
    """Her anlamlı kelime için (asıl terim + eş anlamlılar) grubu döner."""
    groups: list[list[str]] = []
    for w in words(query):
        if w in STOPWORDS or (len(w) < 2 and not w.isdigit()):
            continue
        group = []
        t = _term(w)
        if t:
            group.append(t)
        for syn in SYNONYMS.get(w, []):
            for sw in words(syn):
                st = _term(sw)
                if st and st not in group:
                    group.append(st)
        if group:
            groups.append(group)
    return groups


def build_fts_query(query: str, mode: str = "and") -> str | None:
    """FTS5 MATCH ifadesi. mode='and': tüm kelime grupları; 'or': herhangi biri."""
    groups = query_terms(query)
    if not groups:
        return None
    parts = ["(" + " OR ".join(g) + ")" if len(g) > 1 else g[0] for g in groups]
    joiner = " AND " if mode == "and" else " OR "
    return joiner.join(parts)


def phrase_query(query: str) -> str | None:
    ws = [fold(w) for w in words(query) if w not in STOPWORDS]
    ws = [re.sub(r"[^0-9a-z]", "", w) for w in ws]
    ws = [w for w in ws if w]
    if len(ws) < 2:
        return None
    return '"' + " ".join(ws) + '"'

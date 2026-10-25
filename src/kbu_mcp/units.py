"""URL'den birim (fakülte / bölüm / daire başkanlığı) etiketi üretimi."""

import re
from urllib.parse import urlsplit

from .extract import clean_text

# Bazı alt alan adlarının açık adları (sitelerin <title>'ı boş veya genel olabiliyor).
HOST_NAMES = {
    "www": "Karabük Üniversitesi (Rektörlük / ana site)",
    "oidb": "Öğrenci İşleri Daire Başkanlığı",
    "sks": "Sağlık Kültür ve Spor Daire Başkanlığı",
    "personel": "Personel Daire Başkanlığı",
    "bislem": "Bilgi İşlem Daire Başkanlığı",
    "gensek": "Genel Sekreterlik",
    "kutuphane": "Kütüphane ve Dokümantasyon Daire Başkanlığı",
    "strateji": "Strateji Geliştirme Daire Başkanlığı",
    "imid": "İdari ve Mali İşler Daire Başkanlığı",
    "yapiisleri": "Yapı İşleri ve Teknik Daire Başkanlığı",
    "kalite": "Kalite Koordinatörlüğü",
    "bap": "Bilimsel Araştırma Projeleri Koordinatörlüğü",
    "kbuzem": "Uzaktan Eğitim Uygulama ve Araştırma Merkezi (KBUZEM)",
    "uluslararasi": "Uluslararası İlişkiler Ofisi",
    "erasmus": "Erasmus Ofisi",
    "akademiktakvim": "Akademik Takvim",
    "aday": "Aday Öğrenci",
    "kbumedya": "KBÜ Medya (haberler)",
    "basin": "Basın ve Halkla İlişkiler",
    "rimer": "Rektörlük İletişim Merkezi",
    "bbf": "Bilgisayar ve Bilişim Bilimleri Fakültesi",
    "muh": "Mühendislik ve Doğa Bilimleri Fakültesi",
    "tip": "Tıp Fakültesi",
    "iibf": "İktisadi ve İdari Bilimler Fakültesi",
    "sbf": "Sağlık Bilimleri Fakültesi",
    "isletme": "İşletme Fakültesi",
    "sinavmerkezi": "Sınav Merkezi",
    "etikkurul": "Etik Kurullar",
    "yos": "Yabancı Uyruklu Öğrenci Sınavı (YÖS)",
    "kapgem": "KAPGEM",
    "sertifika": "Sürekli Eğitim Merkezi (KASEM)",
    "karabuktomer": "TÖMER",
    "iso": "İş Sağlığı ve Güvenliği",
    "obs": "Bologna Bilgi Paketi (AKTS, ders planları, program bilgileri)",
    "lisansustu": "Lisansüstü Eğitim Enstitüsü",
    "dishek": "Diş Hekimliği Fakültesi",
}


def host_key(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[: -len(".karabuk.edu.tr")] if host.endswith(".karabuk.edu.tr") else host


def first_segment(url: str) -> str | None:
    path = urlsplit(url).path
    m = re.match(r"^/([A-Za-z][A-Za-z0-9_-]*)(/|$)", path)
    if m and "." not in m.group(1) and not re.match(r"^\d", m.group(1)):
        return m.group(1).lower()
    return None


def ba_param(url: str) -> str | None:
    """ASP.NET alt sitelerinde BA=bilgisayar gibi bölüm/alt site kodu."""
    m = re.search(r"[?&]BA=([^&]+)", url, re.I)
    if m and m.group(1).lower() not in ("index.aspx", "default.aspx", ""):
        return m.group(1).lower().split("/")[0]
    return None


def unit_label(url: str, site_title: str | None) -> str:
    key = host_key(url)
    parts = [HOST_NAMES.get(key, "")]
    st = clean_text(site_title or "")
    if st and st.lower() not in parts[0].lower() and len(st) < 150:
        parts.append(st)
    seg = ba_param(url) or first_segment(url)
    if seg and key != "www":
        parts.append(seg)
    parts.append(key)
    return " / ".join(p for p in parts if p)

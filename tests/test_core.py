from kbu_mcp import urls
from kbu_mcp.extract import extract_html, find_date
from kbu_mcp.indexer import body_for_index, split_text
from kbu_mcp.textnorm import build_fts_query, fold, normalize_for_index
from kbu_mcp.units import unit_label


# ---------------------------------------------------------------- Türkçe normalizasyon

def test_fold_turkish_letters():
    assert fold("İSTANBUL Iğdır ÇAĞRI Şükrü Öğün") == "istanbul igdir cagri sukru ogun"


def test_query_handles_suffixes_and_softening():
    q = build_fts_query("bilgisayar mühendisliği bölüm telefonu")
    assert "muhendisli*" in q          # k→ğ yumuşaması: "mühendislik" kökü "mühendisliği"ni de bulmalı
    assert "telefo*" in q and "tel*" in q  # eş anlamlı genişletme
    assert "doga*" in build_fts_query("doğa bilimleri")


def test_question_words_are_dropped():
    assert build_fts_query("rektör kimdir") == "rektor*"


# ---------------------------------------------------------------- URL kuralları

def test_ba_param_is_kept():
    u = urls.normalize("/icerikGoster.aspx?K=D&id=5&BA=bilgisayar", "https://bbf.karabuk.edu.tr/bilgisayar")
    assert u == "https://bbf.karabuk.edu.tr/icerikGoster.aspx?BA=bilgisayar&K=D&id=5"


def test_domain_whitelist():
    assert urls.is_crawlable_url("https://muh.karabuk.edu.tr/")
    assert not urls.is_crawlable_url("https://obs.karabuk.edu.tr/oibs/ogrenci/login.aspx")
    assert not urls.is_crawlable_url("https://example.com/karabuk.edu.tr")
    assert urls.is_crawlable_url(urls.normalize("https://obs.karabuk.edu.tr/oibs/bologna/progCourses.aspx?lang=tr&curSunit=5972"))
    assert urls.normalize("http://karabuk.edu.tr/x") == "https://www.karabuk.edu.tr/x"


def test_unit_label_uses_ba():
    lbl = unit_label("https://bbf.karabuk.edu.tr/akademikPersonel.aspx?BA=bilgisayar", "Bilgisayar Mühendisliği")
    assert "Bilgisayar ve Bilişim Bilimleri Fakültesi" in lbl and "bilgisayar" in lbl


# ---------------------------------------------------------------- çıkarma / indeks

def test_extract_html_content_area_and_links():
    html = """<html><head><title>Bilgisayar Mühendisliği</title></head><body>
      <nav id="main-nav"><a href="/menu">Menü öğesi</a></nav>
      <span id="txtIcerik"><h2>Staj Uygulama Esasları</h2><p>Staj başvuruları 1 Kasım 2026 tarihine kadar.</p>
      <a href="/yuklenen/dosyalar/1.pdf">Staj Formu</a></span>
      <div id="footerGrup">Altbilgi</div></body></html>"""
    ex = extract_html(html, "https://bbf.karabuk.edu.tr/icerikGoster.aspx?BA=bilgisayar&K=D&id=1")
    assert ex.title == "Staj Uygulama Esasları"
    assert "Menü öğesi" not in ex.text and "Altbilgi" not in ex.text
    assert ex.content_date == "2026-11-01"
    assert ("https://bbf.karabuk.edu.tr/yuklenen/dosyalar/1.pdf", "Staj Formu") in ex.links


def test_find_date_formats():
    assert find_date("Duyuru tarihi: 05.09.2026") == "2026-09-05"
    assert find_date("23 Ekim 2026 Cuma") == "2026-10-23"


def test_phone_tagging():
    assert body_for_index("Bilgisayar Mühendisliği 0 370 418 93 51").endswith("iletisim")
    assert "telefon" not in body_for_index("Sadece metin")


def test_split_text_respects_size():
    text = "\n".join(f"Paragraf {i} " + "kelime " * 40 for i in range(30))
    parts = split_text(text, size=800, overlap=100)
    assert len(parts) > 5 and all(len(p) <= 900 for p in parts)
    assert normalize_for_index(parts[0]).startswith("paragraf 0")

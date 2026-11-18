from collector.text import cap_excerpt, detect_language, hamming, normalize_arabic, simhash


def test_excerpt_strips_html_and_caps_on_word_boundary():
    text = "<p>" + "كلمة " * 200 + "</p>"
    out = cap_excerpt(text, 300)
    assert len(out) <= 301
    assert out.endswith("…")
    assert "<p>" not in out


def test_short_excerpt_untouched():
    assert cap_excerpt("<b>Riyadh</b> announced a package.", 300) == "Riyadh announced a package."


def test_arabic_normalization_for_matching():
    assert normalize_arabic("أرامكو") == normalize_arabic("ارامكو")
    assert normalize_arabic("مدرسةٌ") == normalize_arabic("مدرسه")
    assert normalize_arabic("٢٠٢٦") == "2026"


def test_simhash_near_duplicates_close_and_unrelated_far():
    a = simhash("أعلنت المملكة العربية السعودية اليوم عن حزمة استثمارات جديدة تستهدف تطوير مشاريع الطاقة")
    b = simhash("أعلنت المملكة العربية السعودية اليوم عن حزمة استثمارات جديدة تستهدف تطوير مشاريع الطاقة.")
    c = simhash("قرر البنك المركزي الإبقاء على أسعار الفائدة دون تغيير في اجتماعه اليوم")
    assert hamming(a, b) <= 3
    assert hamming(a, c) > 10
    assert -(2**63) <= a < 2**63


def test_language_detection():
    assert detect_language("السعودية تعلن استثمارات جديدة", "en") == "ar"
    assert detect_language("Saudi Arabia unveils package", "ar") == "en"
    assert detect_language("Türkiye'de enflasyon düştü, ışık", None) == "tr"
    assert detect_language("Le Maroc annonce", "fr-FR") == "fr"

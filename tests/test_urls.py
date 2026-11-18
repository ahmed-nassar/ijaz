from collector.urls import normalize_url, same_site, url_hash


def test_removes_tracking_and_fragment_and_sorts_query():
    url = "HTTPS://WWW.Example.com:443/news/1/?utm_source=rss&b=2&fbclid=x&a=1#top"
    assert normalize_url(url) == "https://www.example.com/news/1?a=1&b=2"


def test_amp_variants_collapse():
    assert normalize_url("https://amp.example.com/a/b/amp/") == "https://example.com/a/b"
    assert normalize_url("https://example.com/a/b?amp=1") == "https://example.com/a/b"


def test_arabic_slug_encoded_or_not_is_equal():
    raw = "https://example.com/news/السعودية-تعلن"
    encoded = "https://example.com/news/%D8%A7%D9%84%D8%B3%D8%B9%D9%88%D8%AF%D9%8A%D8%A9-%D8%AA%D8%B9%D9%84%D9%86"
    assert normalize_url(raw) == normalize_url(encoded)
    assert url_hash(normalize_url(raw)) == url_hash(normalize_url(encoded))


def test_relative_link_resolved_against_feed():
    assert normalize_url("/news/5", base="https://example.com/rss.xml") == "https://example.com/news/5"


def test_keeps_meaningful_query_parameters():
    assert normalize_url("https://example.com/article?id=55&page=2") == "https://example.com/article?id=55&page=2"


def test_same_site():
    assert same_site("https://www.example.com/a", "https://example.com/b")
    assert same_site("https://m.example.com/a", "https://www.example.com/b")
    assert not same_site("https://example.com/a", "https://other.com/a")

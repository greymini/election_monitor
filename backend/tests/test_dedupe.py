"""News deduplication (LLD 6.2). A duplicate that gets through is money spent
labelling the same story twice."""

from news.dedupe import (
    find_duplicate,
    from_signed_64,
    hamming,
    is_near_duplicate,
    simhash,
    to_signed_64,
    url_hash,
)


def test_url_hash_ignores_tracking_parameters_and_trailing_slash():
    base = url_hash("https://example.com/news/giridih-story")
    assert url_hash("https://example.com/news/giridih-story/") == base
    assert url_hash("https://example.com/news/giridih-story?utm_source=whatsapp") == base
    assert url_hash("https://example.com/news/giridih-story#top") == base
    assert url_hash("https://example.com/news/other-story") != base


def test_identical_titles_are_duplicates():
    t = "गिरिडीह में उपचुनाव की तैयारी शुरू"
    assert is_near_duplicate(simhash(t), simhash(t))


def test_republished_title_with_small_edit_is_caught():
    a = simhash("गिरिडीह में उपचुनाव की तैयारी शुरू")
    b = simhash("गिरिडीह में उपचुनाव की तैयारी शुरू।")
    assert is_near_duplicate(a, b), hamming(a, b)


def test_different_stories_are_not_duplicates():
    a = simhash("गिरिडीह में उपचुनाव की तैयारी शुरू")
    b = simhash("पीरटांड़ में जलापूर्ति योजना का उद्घाटन")
    assert not is_near_duplicate(a, b), hamming(a, b)


def test_signed_conversion_round_trips_for_postgres_bigint():
    for title in ["गिरिडीह", "Giridih bypoll", "a", "मधुबन पारसनाथ विवाद"]:
        h = simhash(title)
        assert from_signed_64(to_signed_64(h)) == h
        assert -(2 ** 63) <= to_signed_64(h) < 2 ** 63


def test_find_duplicate_returns_the_matching_id():
    existing = [
        (11, to_signed_64(simhash("पीरटांड़ में जलापूर्ति योजना का उद्घाटन"))),
        (12, to_signed_64(simhash("गिरिडीह में उपचुनाव की तैयारी शुरू"))),
    ]
    assert find_duplicate("गिरिडीह में उपचुनाव की तैयारी शुरू", existing) == 12
    assert find_duplicate("कोयला खदान में हादसा, दो घायल", existing) is None


def test_empty_title_does_not_match_everything():
    assert simhash("") == 0
    assert find_duplicate("कोई खबर नहीं", [(1, 0)]) is None

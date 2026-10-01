"""news.crawl_rss.tag_acs: which constituencies an article is about.

Nothing wrote news_item.ac_ids, and /acs/{ac}/news filters on it, so every
crawled item was invisible on every constituency's news page.
"""

from __future__ import annotations

from news.crawl_rss import fold, tag_acs

AC_KEYS = {
    1: {fold("Giridih"), fold("गिरिडीह"), fold("Pirtand")},
    2: {fold("Dumri"), fold("डुमरी")},
}


def test_an_article_naming_one_constituency_is_tagged_with_it():
    assert tag_acs("गिरिडीह में सड़क निर्माण", "", AC_KEYS) == [1]


def test_a_block_name_is_enough():
    assert tag_acs("Pirtand water crisis", "", AC_KEYS) == [1]


def test_an_article_about_two_constituencies_gets_both():
    assert tag_acs("Giridih and Dumri rally", "", AC_KEYS) == [1, 2]


def test_the_summary_counts_too():
    assert tag_acs("Jharkhand news", "Event in Dumri today", AC_KEYS) == [2]


def test_an_unrelated_article_gets_none():
    assert tag_acs("Ranchi traffic update", "", AC_KEYS) == []

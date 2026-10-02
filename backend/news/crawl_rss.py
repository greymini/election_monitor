"""News crawler (LLD 6.1). Runs every 4 hours.

**What is kept.** Two kinds of item:

* anything that names one of our constituencies' places - a road opening or a
  water protest in Pirtand is exactly the local issue that moves a vote, so
  local news is kept whether or not it is political, at a low relevance when it
  is not;
* Jharkhand *political* news (an election, a party, a legislator) that names no
  constituency - the by-election is fought by state parties over state issues.

What a state feed carries beyond that - crime, weather and sport elsewhere in
Jharkhand - is dropped, since every item kept is one a reader scrolls past and,
later, one that costs money to label.

**How it is tagged.** Each item gets the constituencies whose places it names
(`ac_ids`, from the AC, block and area names in the database plus a few
landmarks), `scope` 'ac' or 'state', the terms that matched, and a first
relevance score that news/label_rules.py refines.

This crawler used to keep only Giridih matches and never wrote `ac_ids`, while
/news filters on it - so nothing it collected could appear on any page.

Matching is on whole words after `fold()`, never raw substrings: "inc" would
otherwise match "since", and "kanke" a URL slug. Hindi terms may take a suffix
(गिरिडीह matches गिरिडीहवासी), because Hindi inflects by suffix and news prose uses
those forms constantly; Latin terms must match exactly.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from common.config import get_settings
from common.db import query, query_one
from common.jobs import job_context
from common.logging_setup import get_logger
from common.textnorm import fold, normalize_text
from news.dedupe import find_duplicate, simhash, title_hash, to_signed_64, url_hash

log = get_logger(__name__)

# Is it about Jharkhand at all? Districts and the capital, in both scripts. The
# constituencies' own places are added from the database.
STATE_TERMS = [
    "झारखंड", "झारखण्ड", "jharkhand", "रांची", "ranchi", "गिरिडीह", "giridih",
    "धनबाद", "dhanbad", "बोकारो", "bokaro", "कोडरमा", "koderma", "हजारीबाग", "hazaribagh",
]

# Is it political? Election and office words. Party names are added below.
POLITICAL_TERMS = [
    "चुनाव", "उपचुनाव", "विधानसभा", "मतदान", "मतदाता", "मतगणना", "वोट", "प्रत्याशी",
    "उम्मीदवार", "विधायक", "सांसद", "मंत्री", "मुख्यमंत्री", "गठबंधन", "नामांकन", "एसआईआर",
    "election", "elections", "bypoll", "by election", "by-election", "byelection", "poll",
    "polls", "voter", "voters", "voting", "mla", "mp", "candidate", "candidates",
    "assembly", "chief minister", "minister", "alliance", "nomination", "sir",
    "electoral roll", "ceo jharkhand",
]

# Bodies that hold elections of their own. "झारखंड चैंबर चुनाव" (the chamber of
# commerce) and "हॉकी झारखंड के चुनाव" were the first false positives the live
# crawl kept: Jharkhand, and an election word, and nothing to do with politics.
# An item naming one of these is kept only if a party is also named.
NON_POLITICAL_BODIES = [
    "चैंबर", "चेंबर", "chamber", "हॉकी", "hockey", "क्रिकेट", "cricket", "फुटबॉल", "football",
    "ओलंपिक", "olympic", "बार एसोसिएशन", "bar association", "bar council", "बार काउंसिल",
    "छात्र संघ", "student union", "students union", "सहकारी", "cooperative", "co operative",
    "संघ चुनाव", "association election", "एसोसिएशन",
]

# News spellings of the parties that matter here. db/seed/party_alias.csv holds
# the Form 20 spellings and is merged in at run time; these are the short forms
# headlines use, which a ballot header never does.
PARTY_TERMS: dict[str, list[str]] = {
    "JMM": ["jmm", "झामुमो", "झारखंड मुक्ति मोर्चा", "jharkhand mukti morcha", "हेमंत सोरेन",
            "hemant soren"],
    "BJP": ["bjp", "भाजपा", "बीजेपी", "भारतीय जनता पार्टी", "bharatiya janata party"],
    "INC": ["congress", "कांग्रेस", "inc"],
    "AJSU": ["ajsu", "आजसू", "सुदेश महतो", "sudesh mahto"],
    "JLKM": ["jlkm", "जेएलकेएम", "झारखंड लोकतांत्रिक क्रांतिकारी मोर्चा", "जयराम महतो",
             "jairam mahto", "jairam mahato"],
    "RJD": ["rjd", "राजद", "राष्ट्रीय जनता दल"],
    "CPIML": ["cpi ml", "cpiml", "भाकपा माले", "माले"],
}

# Landmarks that identify a constituency but are not an AC, block or area name,
# as (English, Hindi) so the News page can show them as one place.
AC_LANDMARKS: dict[int, list[tuple[str, str]]] = {
    32: [("Parasnath", "पारसनाथ"), ("Madhuban", "मधुबन"), ("Marang Buru", "मरांग बुरु")],
}
AC_EXTRA_TERMS: dict[int, list[str]] = {
    ac: [t for pair in pairs for t in (pair[1], pair[0].lower())]
        + ([f"ac {ac}", f"एसी {ac}"] if ac == 32 else [])
    for ac, pairs in AC_LANDMARKS.items()
}

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
# Block names carry a suffix that is not how anyone writes the place.
_BLOCK_SUFFIX = re.compile(r"\s*(block|प्रखंड|municipal corporation|नगर निगम)\s*$", re.I)


@dataclass
class Matchers:
    state: list[str]
    political: list[str]
    parties: dict[str, list[str]]
    ac_terms: dict[int, list[str]] = field(default_factory=dict)


@dataclass
class Verdict:
    keep: bool
    ac_ids: list[int]
    parties: list[str]
    matched: list[str]
    relevance: float

    @property
    def scope(self) -> str:
        return "ac" if self.ac_ids else "state"


def _terms(values) -> list[str]:
    return sorted({fold(v) for v in values if v and fold(v)})


def term_pattern(term: str) -> str:
    """The regex for `term` as whole words (see has_term)."""
    tail = r"[ऀ-ॿ]*" if _DEVANAGARI.search(term) else ""
    return rf"(?<![\wऀ-ॿ]){re.escape(term)}{tail}(?![\wऀ-ॿ])"


def has_term(haystack: str, term: str) -> bool:
    """`term` as whole words in `haystack` (both already folded).

    A Devanagari term may run into a suffix on its last word; a Latin term must
    end at a word boundary.
    """
    # The substring test is a cheap necessary condition: with hundreds of
    # village names per constituency, most terms are not in the text at all.
    if not term or term not in haystack:
        return False
    return re.search(term_pattern(term), haystack) is not None


def classify(title: str, summary: str, m: Matchers) -> Verdict:
    """Whether to keep an item, and what it is about. Pure, so it is testable."""
    text = fold(f"{title} {summary}")
    matched: list[str] = []

    parties = sorted(p for p, terms in m.parties.items()
                     if any(has_term(text, t) for t in terms))
    political_hits = [t for t in m.political if has_term(text, t)]
    state_hits = [t for t in m.state if has_term(text, t)]
    ac_ids = []
    for ac_id, terms in m.ac_terms.items():
        hits = [t for t in terms if has_term(text, t)]
        if hits:
            ac_ids.append(ac_id)
            matched.extend(hits)

    political = bool(political_hits or parties)
    if political and not parties and any(has_term(text, fold(t)) for t in NON_POLITICAL_BODIES):
        political = False
    jharkhand = bool(state_hits or ac_ids)
    # Local news is kept whatever its subject; state news only if political.
    keep = bool(ac_ids) or (political and jharkhand)
    matched = sorted(set(matched + political_hits + state_hits + [f"party:{p}" for p in parties]))

    # A first relevance score; label_rules refines it. A named constituency
    # place with an election word is the core of this product, a party in
    # Jharkhand with no election word the periphery.
    relevance = 0.0
    if keep and not political:
        relevance = 0.2  # local, but not about politics: context, not campaign
    elif keep:
        relevance = 0.3
        if political_hits:
            relevance += 0.2
        if parties:
            relevance += 0.1
        if ac_ids:
            relevance += 0.3
    return Verdict(keep, sorted(ac_ids), parties, matched, round(min(relevance, 1.0), 2))


def build_matchers() -> Matchers:
    """Keyword sets from the database: every AC's name, blocks and areas."""
    parties = {p: _terms(t) for p, t in PARTY_TERMS.items()}
    try:
        for row in query("SELECT a.alias, a.party_abbr FROM ("
                         "  SELECT pa.alias, p.abbr AS party_abbr FROM party_alias pa "
                         "  JOIN party p ON p.party_id = pa.party_id) a"):
            if row["party_abbr"] in parties and len(fold(row["alias"])) > 3:
                parties[row["party_abbr"]] = sorted(set(parties[row["party_abbr"]])
                                                    | {fold(row["alias"])})
    except Exception as exc:
        log.warning("party aliases unavailable (%s); using built-in spellings", exc)

    ac_terms: dict[int, set[str]] = {}
    try:
        for r in query("SELECT ac_id, ac_number, name_en, name_hi FROM ac"):
            ac_terms.setdefault(r["ac_id"], set()).update(
                _terms([r["name_en"], r["name_hi"]] + AC_EXTRA_TERMS.get(r["ac_number"], [])))
        for r in query("SELECT ac_id, name_en, name_hi FROM block"):
            ac_terms.setdefault(r["ac_id"], set()).update(
                _terms([_BLOCK_SUFFIX.sub("", r["name_en"]), _BLOCK_SUFFIX.sub("", r["name_hi"])]))
        for r in query("SELECT ac_id, name_en, name_hi FROM area"):
            for name in (r["name_en"], r["name_hi"]):
                folded = fold(name)
                # 'Ward 7' and short names are far too generic to tag on, and
                # synthetic fixture names ("Fixture Panchayat ...") are not
                # places at all.
                if (folded and len(folded) > 4 and not folded.startswith(("ward", "वार्ड"))
                        and "fixture" not in folded and "नमूना" not in folded):
                    ac_terms.setdefault(r["ac_id"], set()).add(folded)
    except Exception as exc:
        log.warning("could not load AC geography for tagging (%s)", exc)

    places = {t for terms in ac_terms.values() for t in terms}
    return Matchers(
        state=_terms(STATE_TERMS) + sorted(places),
        political=_terms(POLITICAL_TERMS),
        parties=parties,
        ac_terms={k: sorted(v) for k, v in ac_terms.items()},
    )


def ac_keywords() -> dict[int, set[str]]:
    """Folded place names per constituency, as `build_matchers` derives them.

    Kept from restructure-and-tests, which fixed the empty-ac_ids bug
    independently with this and `tag_acs`; both now delegate to the matchers,
    so there is one definition of "a constituency's places".
    """
    return {ac_id: set(terms) for ac_id, terms in build_matchers().ac_terms.items()}


def tag_acs(title: str, summary: str, ac_keys: dict[int, set[str]]) -> list[int]:
    """The constituencies an article names, by whole-word place-name match.

    An item that names no constituency stays untagged rather than being guessed
    into one. Unlike `classify`, this does not decide whether to keep the item.
    """
    text = fold(f"{title} {summary}")
    return sorted(ac for ac, names in ac_keys.items() if any(has_term(text, n) for n in names))


def clean_title(title: str, publisher: str | None) -> str:
    """Google News appends ' - Publisher' to every title; drop it."""
    if publisher and title.endswith(f" - {publisher}"):
        return title[: -len(publisher) - 3].rstrip()
    return title


def fetch_feed(url: str, timeout: int, user_agent: str) -> list[dict[str, Any]]:
    import feedparser
    import httpx

    try:
        response = httpx.get(url, timeout=timeout, follow_redirects=True,
                             headers={"User-Agent": user_agent})
        response.raise_for_status()
    except Exception as exc:
        log.warning("feed failed %s: %s", url, exc)
        raise

    parsed = feedparser.parse(response.content)
    items = []
    for entry in parsed.entries:
        published = None
        for field_name in ("published_parsed", "updated_parsed"):
            value = getattr(entry, field_name, None)
            if value:
                published = datetime(*value[:6], tzinfo=UTC).date()
                break
        source = getattr(entry, "source", None)
        publisher = normalize_text(source.get("title")) if source else None
        items.append({
            "url": getattr(entry, "link", "") or "",
            "title": clean_title(normalize_text(getattr(entry, "title", "")), publisher),
            "summary": normalize_text(
                getattr(entry, "summary", "") or getattr(entry, "description", "")
            ),
            "published": published,
            "publisher": publisher,
        })
    return items


def crawl(limit: int | None = None, pause: float = 1.0) -> dict:
    settings = get_settings()
    matchers = build_matchers()
    sources = query("SELECT source_id, name, url, kind, lang FROM news_source WHERE is_active "
                    "ORDER BY source_id")
    stats = {"sources": len(sources), "fetched": 0, "relevant": 0, "inserted": 0,
             "duplicates": 0, "failed_sources": 0, "ac_tagged": 0}
    per_ac: dict[int, int] = {}
    budget = limit or settings.news_max_items

    # Recent simhashes to compare against - a duplicate older than this is not
    # worth the memory.
    existing = [(r["news_id"], r["simhash"]) for r in query(
        "SELECT news_id, simhash FROM news_item "
        "WHERE simhash IS NOT NULL AND fetched_at > now() - interval '30 days'"
    )]

    for index, source in enumerate(sources):
        if index and pause:
            time.sleep(pause)  # one request a second; these are free services
        try:
            items = fetch_feed(source["url"], settings.news_timeout, settings.news_user_agent)
        except Exception as exc:
            stats["failed_sources"] += 1
            query_one("UPDATE news_source SET last_error = %s WHERE source_id = %s RETURNING source_id",
                      (str(exc)[:500], source["source_id"]))
            continue
        query_one("UPDATE news_source SET last_ok_at = now(), last_error = NULL "
                  "WHERE source_id = %s RETURNING source_id", (source["source_id"],))

        for item in items:
            stats["fetched"] += 1
            if stats["inserted"] >= budget:
                break
            if not item["url"] or not item["title"]:
                continue
            verdict = classify(item["title"], item["summary"], matchers)
            if not verdict.keep:
                continue
            stats["relevant"] += 1
            if find_duplicate(item["title"], existing) is not None:
                stats["duplicates"] += 1
                continue
            h = simhash(item["title"])
            row = query_one(
                "INSERT INTO news_item (url, url_hash, title_hash, simhash, published, source, "
                "feed, title, body, ac_ids, scope, relevance, matched_terms, parties) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (url) DO NOTHING RETURNING news_id",
                (item["url"], url_hash(item["url"]), title_hash(item["title"]),
                 to_signed_64(h), item["published"], item.get("publisher") or source["name"],
                 source["name"], item["title"], item["summary"], verdict.ac_ids,
                 verdict.scope, verdict.relevance, verdict.matched, verdict.parties),
            )
            if row:
                stats["inserted"] += 1
                existing.append((row["news_id"], to_signed_64(h)))
                if verdict.ac_ids:
                    stats["ac_tagged"] += 1
                for ac_id in verdict.ac_ids:
                    per_ac[ac_id] = per_ac.get(ac_id, 0) + 1
            else:
                stats["duplicates"] += 1
    stats["per_ac"] = per_ac
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Crawl configured news sources")
    ap.add_argument("--limit", type=int, default=None, help="max new items this run")
    args = ap.parse_args(argv)
    with job_context("news.crawl") as job:
        stats = crawl(args.limit)
        job.set(**{k: v for k, v in stats.items() if k != "per_ac"})
        job.log_line(
            f"{stats['sources']} source(s), {stats['fetched']} item(s) seen, "
            f"{stats['relevant']} Jharkhand-political, {stats['inserted']} new "
            f"({stats['ac_tagged']} tagged to a constituency), "
            f"{stats['duplicates']} duplicate, {stats['failed_sources']} source(s) failed"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

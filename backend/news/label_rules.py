"""Keyword-rule labelling for crawled news (news plan, task 2).

The LLM labeller (news/label_batch.py) needs an Anthropic key, and without one
nothing was ever labelled, so /news - which lists labelled items only - stayed
empty. This labels every crawled item with keyword rules instead: cheap,
explainable, and good enough to count what the press is talking about. An item
labelled here carries `label_method = 'rules'`, and label_batch re-labels it
with the LLM once a key is configured; a rules run never overwrites an LLM
label.

What it sets, and what it deliberately does not:

* `parties`: the parties named, most-mentioned first (news spellings from
  news.crawl_rss.PARTY_TERMS plus db/seed/party_alias.csv).
* `issues`: values of label_batch.ISSUE_ENUM whose keywords appear; 'other'
  when none does.
* `area_ids` / `area_names_raw`: areas (panchayats, wards) whose name or alias
  appears. A name that matches nothing is not queued for review - keyword
  matching cannot tell a place name from any other word, so there is nothing to
  queue.
* `persons`: candidates from the `candidate` table only. Privacy rule (HLD 4):
  no private individuals, and keyword rules cannot tell who is one.
* `sentiment`, `sentiment_by_party`: left NULL. Tone is not something a keyword
  list can read, and the UI says "tone not analysed yet" rather than guessing.
* `relevance`: a weighted score - a named place in one of our constituencies
  counts most, then an election word, then a party, a candidate, an issue.

Matching reuses the crawler's `has_term` (whole words after `fold()`, Hindi
terms may take a suffix), so the two never disagree about what a word is.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field

from common.db import query, query_one
from common.jobs import job_context
from common.logging_setup import get_logger
from common.textnorm import fold
from news.crawl_rss import _BLOCK_SUFFIX, PARTY_TERMS, POLITICAL_TERMS, has_term, term_pattern
from news.label_batch import ISSUE_ENUM

log = get_logger(__name__)

LABELLED_BY = "rules-v1"

# Keywords per issue, Hindi and English. A Hindi term matches its suffixed forms
# (योजना -> योजनाओं), so stems that are also the start of an unrelated word are
# left out: पुल (bridge) would match पुलिस, नल (tap) would match नलिनी.
ISSUE_TERMS: dict[str, list[str]] = {
    "water": ["पानी", "पेयजल", "जलापूर्ति", "जल संकट", "जलसंकट", "नल जल", "चापाकल", "जलमीनार",
              "water", "drinking water", "water supply", "water crisis"],
    "roads": ["सड़क", "सडक", "पुलिया", "फोरलेन", "हाईवे", "road", "roads", "bridge", "highway"],
    "electricity": ["बिजली", "विद्युत", "ट्रांसफार्मर", "लोडशेडिंग", "electricity", "power cut",
                    "power supply", "transformer"],
    "health": ["अस्पताल", "स्वास्थ्य", "डॉक्टर", "चिकित्सक", "इलाज", "एंबुलेंस", "एम्बुलेंस",
               "hospital", "health", "doctor", "doctors", "ambulance"],
    "education": ["स्कूल", "विद्यालय", "शिक्षा", "शिक्षक", "कॉलेज", "विश्वविद्यालय", "छात्र",
                  "school", "schools", "education", "teacher", "teachers", "college",
                  "university", "students"],
    "employment/migration": ["रोजगार", "बेरोजगार", "नौकरी", "पलायन", "प्रवासी", "नियुक्ति",
                             "भर्ती", "employment", "unemployment", "jobs", "migrant",
                             "migrants", "migration", "recruitment", "jssc"],
    "mining/coal": ["कोयला", "खनन", "खदान", "बालू", "कोलियरी", "सीसीएल", "बीसीसीएल", "माइका",
                    "ढिबरा", "coal", "mining", "mine", "mines", "colliery", "ccl", "bccl",
                    "mica"],
    "Parasnath/Marang Buru": ["पारसनाथ", "मरांग बुरु", "मरांग बुरू", "शिखरजी", "parasnath",
                              "marang buru", "shikharji"],
    "law-and-order": ["हत्या", "अपराध", "अपराधी", "पुलिस", "गिरफ्तार", "लूट", "नक्सल",
                      "उग्रवादी", "murder", "crime", "police", "arrested", "naxal", "naxals",
                      "maoist", "maoists"],
    "welfare-schemes": ["योजना", "मंईयां सम्मान", "मईयां सम्मान", "मंईयां", "पेंशन", "राशन",
                        # not bare आवास: it matches आवासीय (a residence certificate)
                        "आवास योजना", "अबुआ आवास", "पीएम आवास", "प्रधानमंत्री आवास", "मनरेगा", "scheme", "schemes", "pension", "ration",
                        "maiyan samman", "mgnrega", "mnrega", "yojana"],
    "corruption": ["भ्रष्टाचार", "घोटाला", "घोटाले", "रिश्वत", "ईडी", "सीबीआई", "corruption",
                   "scam", "bribe", "bribery", "cbi"],
    # The special intensive revision of the rolls and the "vote chori" campaign
    # were the largest political story of September 2026 and had no issue of
    # their own, so they all read as 'other'.
    "electoral-roll/SIR": ["एसआईआर", "मतदाता सूची", "वोटर लिस्ट", "वोट चोरी", "बीएलओ",
                           "विशेष गहन पुनरीक्षण", "sir", "electoral roll", "electoral rolls",
                           "voter list", "voters list", "vote chori", "vote theft", "blo",
                           "special intensive revision", "सीईसी", "cec"],
    "candidate/organisation": ["प्रत्याशी", "उम्मीदवार", "टिकट", "नामांकन", "संगठन",
                               "कार्यकर्ता", "जिलाध्यक्ष", "प्रदेश अध्यक्ष", "candidate",
                               "candidates", "ticket", "nomination", "party workers",
                               "workers"],
    "alliance": ["गठबंधन", "महागठबंधन", "एनडीए", "सीट बंटवारा", "alliance", "nda",
                 "india bloc", "seat sharing"],
}
assert set(ISSUE_TERMS) | {"other"} == set(ISSUE_ENUM), "ISSUE_TERMS must cover ISSUE_ENUM"

# The election subset of the crawler's political words: these make an item
# about the vote, not merely about politics.
ELECTION_TERMS = [
    "चुनाव", "उपचुनाव", "मतदान", "मतदाता", "मतगणना", "वोट", "प्रत्याशी", "उम्मीदवार",
    "नामांकन", "एसआईआर", "election", "elections", "bypoll", "by election", "by-election",
    "byelection", "poll", "polls", "voter", "voters", "voting", "candidate", "candidates",
    "nomination", "sir", "electoral roll",
]

# Hindi spellings for candidates, keyed by the Form 20 name. Form 20 prints
# names in English only and `candidate.name_hi` is empty, while most of the
# local press is Hindi. Matching keys only - nothing here is displayed.
CANDIDATE_HI: dict[str, list[str]] = {
    "SUDIVYA KUMAR": ["सुदिव्य कुमार", "सुदिव्य सोनू", "सुदिव्य कुमार सोनू"],
    "NIRBHAY KUMAR SHAHABADI": ["निर्भय कुमार शाहाबादी", "निर्भय शाहाबादी"],
    "NAVIN ANAND": ["नवीन आनंद"],
    "CHUNNU KANT": ["चुन्नू कांत"],
    "JAIRAM MAHATO": ["जयराम महतो", "जयराम कुमार महतो", "jairam mahto"],
    "BABY DEVI": ["बेबी देवी"],
    "SURESH KUMAR BAITHA": ["सुरेश बैठा", "सुरेश कुमार बैठा"],
    "JITU CHARAN RAM": ["जीतू चरण राम"],
}

# Weights for relevance. A place in one of our constituencies is the core of
# this product; the rest adds to it.
W_AREA, W_AC, W_ELECTION, W_POLITICAL, W_PARTY, W_PERSON, W_ISSUE = (
    0.35, 0.3, 0.25, 0.1, 0.15, 0.1, 0.05)
LOCAL_NON_POLITICAL_CAP = 0.25


@dataclass
class Rules:
    parties: dict[str, list[str]]
    issues: dict[str, list[str]]
    election: list[str]
    political: list[str]
    persons: dict[str, list[str]] = field(default_factory=dict)       # display name -> terms
    person_ac: dict[str, list[int]] = field(default_factory=dict)     # display name -> ac_ids
    areas: dict[int, list[str]] = field(default_factory=dict)         # area_id -> terms
    area_ac: dict[int, int] = field(default_factory=dict)             # area_id -> ac_id


@dataclass
class Label:
    parties: list[str]                  # most-mentioned first
    party_mentions: dict[str, int]
    issues: list[str]
    persons: list[str]
    area_ids: list[int]
    area_names: list[str]
    ac_ids: list[int]
    relevance: float

    @property
    def scope(self) -> str:
        return "ac" if self.ac_ids else "state"


def _terms(values) -> list[str]:
    return sorted({fold(v) for v in values if v and fold(v)})


def _count(text: str, term: str) -> int:
    """How many times `term` occurs, by the same whole-word rule as has_term."""
    return len(re.findall(term_pattern(term), text)) if term else 0


def label(title: str, body: str, rules: Rules, ac_ids: list[int] | None = None) -> Label:
    """Label one item. Pure, so it is testable. `ac_ids` are the crawler's tags."""
    text = fold(f"{title} {body or ''}")

    mentions = {}
    for party, terms in rules.parties.items():
        n = sum(_count(text, t) for t in terms)
        if n:
            mentions[party] = n
    parties = [p for p, _ in sorted(mentions.items(), key=lambda kv: (-kv[1], kv[0]))]

    issues = [issue for issue, terms in rules.issues.items()
              if any(has_term(text, t) for t in terms)]
    election = any(has_term(text, t) for t in rules.election)
    political_other = any(has_term(text, t) for t in rules.political)
    # A candidate is tagged only in a political story: "बेबी देवी" in a crime
    # report is far more likely a namesake than the MLA, and naming a private
    # person is exactly what the privacy rule forbids.
    persons = []
    if election or political_other or parties:
        persons = sorted(name for name, terms in rules.persons.items()
                         if any(has_term(text, t) for t in terms))

    area_ids, area_names = [], []
    for area_id, terms in rules.areas.items():
        hits = [t for t in terms if has_term(text, t)]
        if hits:
            area_ids.append(area_id)
            area_names.extend(hits)
    # A candidate's constituency is concerned by news about them (the death of
    # Giridih's MLA is Giridih news even when the headline names no place).
    tagged = (set(ac_ids or []) | {rules.area_ac[a] for a in area_ids if a in rules.area_ac}
              | {ac for name in persons for ac in rules.person_ac.get(name, [])})

    political = election or political_other or bool(parties) or bool(persons)

    place = W_AREA if area_ids else W_AC if tagged else 0.0
    real_issues = bool(issues)
    if not political:
        relevance = min(place, LOCAL_NON_POLITICAL_CAP - W_ISSUE) + (W_ISSUE if real_issues else 0)
    else:
        relevance = (place + (W_ELECTION if election else 0)
                     + (W_POLITICAL if political_other and not election else 0)
                     + (W_PARTY if parties else 0) + (W_PERSON if persons else 0)
                     + (W_ISSUE if real_issues else 0))

    return Label(
        parties=parties, party_mentions=mentions,
        issues=issues or ["other"], persons=persons,
        area_ids=sorted(area_ids), area_names=sorted(set(area_names)),
        ac_ids=sorted(tagged), relevance=round(min(relevance, 1.0), 2),
    )


def _usable_area_name(folded: str) -> bool:
    """The crawler's rule: 'Ward 7', short names, placeholders and synthetic
    fixture names are not places a headline would name."""
    return (bool(folded) and len(folded) > 4 and not folded.startswith(("ward", "वार्ड"))
            and "fixture" not in folded and "नमूना" not in folded
            and "unassigned" not in folded)


def build_rules() -> Rules:
    """Keyword sets, with parties, candidates and areas from the database."""
    parties = {p: _terms(t) for p, t in PARTY_TERMS.items()}
    try:
        for row in query("SELECT pa.alias, p.abbr FROM party_alias pa "
                         "JOIN party p ON p.party_id = pa.party_id"):
            if row["abbr"] in parties and len(fold(row["alias"])) > 3:
                parties[row["abbr"]] = sorted(set(parties[row["abbr"]]) | {fold(row["alias"])})
    except Exception as exc:
        log.warning("party aliases unavailable (%s); using built-in spellings", exc)

    persons: dict[str, set[str]] = {}
    person_ac: dict[str, set[int]] = {}
    try:
        for row in query("SELECT DISTINCT name_en, name_hi, ac_id FROM candidate"):
            name = (row["name_en"] or "").strip()
            # A single word is not a person anyone can be sure of; a seed
            # placeholder such as "(JLKM candidate, name not in source)" is none.
            if len(name.split()) < 2 or "(" in name:
                continue
            display = " ".join(w.capitalize() for w in name.split())
            persons.setdefault(display, set()).update(
                _terms([name, row["name_hi"]] + CANDIDATE_HI.get(name.upper(), [])))
            person_ac.setdefault(display, set()).add(row["ac_id"])
    except Exception as exc:
        log.warning("candidates unavailable (%s); no persons will be tagged", exc)

    areas: dict[int, set[str]] = {}
    area_ac: dict[int, int] = {}
    try:
        # A panchayat named like its block or constituency (Dumri, Gandey) is
        # not what "Dumri by-election" means: the larger unit wins, and the
        # crawler already tags it.
        larger = {fold(_BLOCK_SUFFIX.sub("", n or "")) for r in query(
            "SELECT name_en, name_hi FROM ac UNION ALL SELECT name_en, name_hi FROM block")
            for n in (r["name_en"], r["name_hi"])}
        for row in query("SELECT area_id, ac_id, name_en, name_hi FROM area"):
            area_ac[row["area_id"]] = row["ac_id"]
            for name in (row["name_en"], row["name_hi"]):
                if _usable_area_name(fold(name)) and fold(name) not in larger:
                    areas.setdefault(row["area_id"], set()).add(fold(name))
        for row in query("SELECT alias, area_id FROM area_alias"):
            if _usable_area_name(fold(row["alias"])) and fold(row["alias"]) not in larger:
                areas.setdefault(row["area_id"], set()).add(fold(row["alias"]))
    except Exception as exc:
        log.warning("areas unavailable (%s); no areas will be tagged", exc)

    return Rules(
        parties=parties,
        issues={k: _terms(v) for k, v in ISSUE_TERMS.items()},
        election=_terms(ELECTION_TERMS),
        political=_terms(t for t in POLITICAL_TERMS if t not in ELECTION_TERMS),
        persons={k: sorted(v) for k, v in persons.items()},
        person_ac={k: sorted(v) for k, v in person_ac.items()},
        areas={k: sorted(v) for k, v in areas.items()},
        area_ac=area_ac,
    )


def run(relabel: bool = False, limit: int | None = None) -> dict:
    """Label pending items. `relabel` also redoes earlier rules labels (after a
    keyword change); LLM labels are never touched."""
    rules = build_rules()
    where = ("label_method = 'rules' OR labelled_at IS NULL" if relabel
             else "labelled_at IS NULL")
    rows = query(f"SELECT news_id, title, body, ac_ids FROM news_item "
                 f"WHERE ({where}) AND label_method IS DISTINCT FROM 'llm' "
                 f"ORDER BY news_id" + (" LIMIT %s" if limit else ""),
                 (limit,) if limit else None)
    stats = {"labelled": 0, "with_party": 0, "with_issue": 0, "with_area": 0,
             "with_person": 0, "ac_scope": 0}
    issue_counts: Counter = Counter()
    for row in rows:
        lab = label(row["title"] or "", row["body"] or "", rules, row["ac_ids"])
        query_one(
            "UPDATE news_item SET parties = %s, issues = %s, persons = %s, area_ids = %s, "
            "area_names_raw = %s, ac_ids = %s, scope = %s, relevance = %s, "
            "sentiment = NULL, sentiment_by_party = NULL, label_method = 'rules', "
            "labelled_by = %s, labelled_at = now() "
            "WHERE news_id = %s AND label_method IS DISTINCT FROM 'llm' RETURNING news_id",
            (lab.parties, lab.issues, lab.persons, lab.area_ids, lab.area_names, lab.ac_ids,
             lab.scope, lab.relevance, LABELLED_BY, row["news_id"]),
        )
        stats["labelled"] += 1
        stats["with_party"] += bool(lab.parties)
        stats["with_issue"] += lab.issues != ["other"]
        stats["with_area"] += bool(lab.area_ids)
        stats["with_person"] += bool(lab.persons)
        stats["ac_scope"] += lab.scope == "ac"
        issue_counts.update(lab.issues)
    stats["issues"] = dict(issue_counts.most_common())
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Label crawled news with keyword rules")
    ap.add_argument("--relabel", action="store_true",
                    help="also redo earlier rules labels (never LLM labels)")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)
    with job_context("news.label_rules") as job:
        stats = run(args.relabel, args.limit)
        job.set(**{k: v for k, v in stats.items() if k != "issues"})
        job.log_line(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

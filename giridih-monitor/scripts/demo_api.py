"""DEMO SERVER - synthetic data, for looking at the dashboard without a database.

    python -m scripts.demo_api

THIS IS NOT THE REAL BACKEND. api/main.py is, and it needs PostgreSQL with
PostGIS and pgvector. This file exists only so the front end can be viewed and
clicked through on a machine with no database.

Every number it serves is generated. The AC-level totals are made to tie to the
published 2024 figures (JMM 94,042 / BJP 90,204 / JLKM 10,787 / NOTA 2,004 on
3,04,898 electors) so the shapes look plausible, but the per-booth split is
invented and the constituency name is suffixed "DEMO DATA" so nobody mistakes a
screenshot of this for analysis.

It mirrors the response shapes of api/routers/*.py. If those change, this drifts.
"""

from __future__ import annotations

import math
import random
from datetime import date, timedelta

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(title="Giridih Monitor - DEMO (synthetic data)")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

RNG = random.Random(32)

# --- synthetic geography -----------------------------------------------------
# Ward count (36) and block structure follow HLD 3. Panchayat names here are
# INVENTED placeholders - the real system derives them from the published PS
# list precisely so that inventing them never happens (db/seed/README.md).
BLOCKS = [
    {"block_id": 1, "name_en": "Giridih Municipal Corporation", "name_hi": "गिरिडीह नगर निगम", "kind": "ulb"},
    {"block_id": 2, "name_en": "Giridih Block", "name_hi": "गिरिडीह प्रखंड", "kind": "rural"},
    {"block_id": 3, "name_en": "Pirtand Block", "name_hi": "पीरटांड़ प्रखंड", "kind": "rural"},
]

AREAS: list[dict] = []
for i in range(1, 37):
    AREAS.append({"area_id": i, "block_id": 1, "kind": "ward",
                  "name_en": f"Ward {i}", "name_hi": f"वार्ड {i}", "code": str(i)})
for i in range(1, 16):
    AREAS.append({"area_id": 100 + i, "block_id": 2, "kind": "panchayat",
                  "name_en": f"Giridih Panchayat {i}", "name_hi": f"गिरिडीह पंचायत {i}", "code": None})
for i in range(1, 18):
    AREAS.append({"area_id": 200 + i, "block_id": 3, "kind": "panchayat",
                  "name_en": f"Pirtand Panchayat {i}", "name_hi": f"पीरटांड़ पंचायत {i}", "code": None})

PARTIES = [
    {"party_id": 1, "abbr": "JMM", "name_en": "Jharkhand Mukti Morcha", "name_hi": "झारखंड मुक्ति मोर्चा",
     "alliance_2024": "INDIA", "colour": "#1a6b39"},
    {"party_id": 2, "abbr": "BJP", "name_en": "Bharatiya Janata Party", "name_hi": "भारतीय जनता पार्टी",
     "alliance_2024": "NDA", "colour": "#ff8c42"},
    {"party_id": 3, "abbr": "AJSU", "name_en": "All Jharkhand Students Union", "name_hi": "आजसू पार्टी",
     "alliance_2024": "NDA", "colour": "#c98500"},
    {"party_id": 4, "abbr": "JLKM", "name_en": "Jharkhand Loktantrik Krantikari Morcha",
     "name_hi": "झारखंड लोकतांत्रिक क्रांतिकारी मोर्चा", "alliance_2024": "NONE", "colour": "#6d3fc4"},
    {"party_id": 5, "abbr": "NOTA", "name_en": "None of the Above", "name_hi": "इनमें से कोई नहीं",
     "alliance_2024": "NONE", "colour": "#52514e"},
]

ELECTIONS = [
    {"election_id": 1, "label": "VS-2014", "type": "VS", "year": 2014, "is_baseline": False},
    {"election_id": 2, "label": "VS-2019", "type": "VS", "year": 2019, "is_baseline": False},
    {"election_id": 3, "label": "VS-2024", "type": "VS", "year": 2024, "is_baseline": True},
    {"election_id": 4, "label": "LS-2024 (AC seg)", "type": "LS", "year": 2024, "is_baseline": False},
]

N_BOOTHS = 348
GIRIDIH = (24.1854, 86.3094)


def _build_booths() -> list[dict]:
    """Generate booths whose 2024 votes sum to the published AC totals.

    Shares are drawn per booth with an urban/rural tilt so the map has real
    structure, then scaled so the column totals land exactly on the published
    figures. This is presentation plausibility, not analysis.
    """
    booths = []
    for index in range(N_BOOTHS):
        area = AREAS[index % len(AREAS)]
        urban = area["block_id"] == 1
        pirtand = area["block_id"] == 3

        electors = RNG.randint(620, 1250)
        turnout = RNG.uniform(0.58, 0.78) if urban else RNG.uniform(0.64, 0.84)
        votes = int(electors * turnout)

        # Urban wards lean BJP, the Parasnath belt leans JMM, JLKM is scattered.
        jmm_share = RNG.gauss(0.42 if urban else 0.49 if pirtand else 0.45, 0.10)
        bjp_share = RNG.gauss(0.49 if urban else 0.38 if pirtand else 0.44, 0.10)
        jlkm_share = max(0.005, RNG.gauss(0.052, 0.035))
        nota_share = max(0.002, RNG.gauss(0.010, 0.004))
        jmm_share, bjp_share = max(0.12, jmm_share), max(0.12, bjp_share)
        total_share = jmm_share + bjp_share + jlkm_share + nota_share

        jmm = int(votes * jmm_share / total_share)
        bjp = int(votes * bjp_share / total_share)
        jlkm = int(votes * jlkm_share / total_share)
        nota = max(0, votes - jmm - bjp - jlkm)

        # Scatter within the district bounding box, tighter for the town.
        spread = 0.035 if urban else 0.16
        lat = GIRIDIH[0] + RNG.uniform(-spread, spread) + (0.06 if pirtand else 0)
        lon = GIRIDIH[1] + RNG.uniform(-spread, spread) - (0.10 if pirtand else 0)

        booths.append({
            "booth_uid": f"B{index + 1:04d}",
            "ps_number": index + 1,
            "area": area,
            "building": f"{'Primary School' if index % 3 else 'Middle School'} "
                        f"{area['name_en']} #{index // len(AREAS) + 1}",
            "ps_name_hi": f"{'प्रा०वि०' if index % 3 else 'म०वि०'} {area['name_hi']}",
            "village": area["name_en"],
            "electors": electors, "votes": jmm + bjp + jlkm + nota,
            "jmm": jmm, "bjp": bjp, "jlkm": jlkm, "nota": nota,
            "lat": lat, "lon": lon,
        })

    # Scale onto the published 2024 AC totals so the headline numbers tie out.
    targets = {"jmm": 94042, "bjp": 90204, "jlkm": 10787, "nota": 2004}
    for key, target in targets.items():
        current = sum(b[key] for b in booths) or 1
        factor = target / current
        for b in booths:
            b[key] = int(b[key] * factor)
        drift = target - sum(b[key] for b in booths)
        booths[0][key] += drift

    elector_target = 304898
    factor = elector_target / (sum(b["electors"] for b in booths) or 1)
    for b in booths:
        b["electors"] = int(b["electors"] * factor)
        b["votes"] = b["jmm"] + b["bjp"] + b["jlkm"] + b["nota"]
    booths[0]["electors"] += elector_target - sum(b["electors"] for b in booths)

    for b in booths:
        winner, runner = ("JMM", "BJP") if b["jmm"] >= b["bjp"] else ("BJP", "JMM")
        margin = abs(b["jmm"] - b["bjp"])
        b["winner_party"], b["runner_party"] = winner, runner
        b["margin_votes"] = margin
        b["margin_pct"] = round(100 * margin / b["votes"], 2) if b["votes"] else 0
        b["turnout_pct"] = round(100 * b["votes"] / b["electors"], 2) if b["electors"] else 0
        # Signed for the diverging scale: negative = JMM lead, positive = BJP lead.
        b["signed_margin"] = b["margin_pct"] if winner == "BJP" else -b["margin_pct"]
        b["additions"] = int(b["electors"] * RNG.uniform(0.03, 0.15))
        b["deletions"] = int(b["electors"] * RNG.uniform(0.005, 0.045))
        b["new_voter_pct"] = round(100 * b["additions"] / b["electors"], 2)
        b["floating_pct"] = round(abs(RNG.gauss(14, 7)), 2)
        b["margin_stddev"] = round(abs(RNG.gauss(7, 3)), 2)
    return booths


BOOTHS = _build_booths()
BY_UID = {b["booth_uid"]: b for b in BOOTHS}


def _priority(b: dict) -> float:
    """Same shape as mv_booth_priority: tight margin, new voters, volatility,
    floating vote - each normalised, then weighted 0.35/0.25/0.20/0.20."""
    margin = 1 - min(1.0, b["margin_pct"] / 25)
    new_voter = min(1.0, b["new_voter_pct"] / 18)
    volatility = min(1.0, b["margin_stddev"] / 14)
    floating = min(1.0, b["floating_pct"] / 30)
    return round(100 * (0.35 * margin + 0.25 * new_voter + 0.20 * volatility + 0.20 * floating), 1)


for _b in BOOTHS:
    _b["priority_score"] = _priority(_b)
_ranked = sorted(BOOTHS, key=lambda x: -x["priority_score"])
for _i, _b in enumerate(_ranked):
    _b["priority_quartile"] = min(4, 1 + _i * 4 // len(_ranked))


# --- endpoints, mirroring api/routers/*.py ----------------------------------

@app.post("/auth/login")
def login(body: dict) -> dict:
    """The demo accepts any credentials. The real app checks a bcrypt hash."""
    return {"access_token": "demo-token", "token_type": "bearer",
            "role": "admin", "name": body.get("phone", "Demo user"), "block_id": None}


@app.get("/auth/me")
def me() -> dict:
    return {"user_id": 1, "name": "Demo admin", "role": "admin", "block_id": None,
            "sees_caste": True, "daily_token_budget": 150000}


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "database": False, "demo": True}


@app.get("/summary")
def summary() -> dict:
    deadline = date(2027, 3, 6)
    total_votes = sum(b["votes"] for b in BOOTHS)
    per_election = []
    for election in ELECTIONS:
        scale = {2014: 0.72, 2019: 0.87, 2024: 1.0}.get(election["year"], 1.0)
        per_election.append({
            "label": election["label"], "type": election["type"], "year": election["year"],
            "is_baseline": election["is_baseline"], "booths": N_BOOTHS,
            "votes": int(total_votes * scale),
            "electors": int(sum(b["electors"] for b in BOOTHS) * scale),
        })
    return {
        "constituency": {"code": "AC-32", "name_en": "Giridih — DEMO DATA",
                         "name_hi": "गिरिडीह — डेमो डेटा", "parent_pc": "PC-11 Giridih"},
        "bypoll": {
            "vacancy_date": "2026-09-06", "deadline": deadline.isoformat(),
            "days_to_deadline": (deadline - date.today()).days,
            "note": "The ECI must hold the poll within six months of the vacancy.",
        },
        "elections": per_election,
        "baseline": {
            "label": "VS-2024",
            "jmm": sum(b["jmm"] for b in BOOTHS), "bjp": sum(b["bjp"] for b in BOOTHS),
            "jlkm": sum(b["jlkm"] for b in BOOTHS), "nota": sum(b["nota"] for b in BOOTHS),
            "electors": sum(b["electors"] for b in BOOTHS), "votes": total_votes,
        },
        "data_health": {"booths": N_BOOTHS, "open_reviews": 7, "weak_crosswalks": 23,
                        "latest_roll": "2026-08-01"},
        "scope": {"block_id": None, "sees_caste": True},
    }


@app.get("/areas")
def areas() -> dict:
    counts: dict[int, int] = {}
    for b in BOOTHS:
        counts[b["area"]["area_id"]] = counts.get(b["area"]["area_id"], 0) + 1
    return {
        "blocks": BLOCKS,
        "areas": [{**a, "booths": counts.get(a["area_id"], 0)} for a in AREAS],
        "elections": ELECTIONS,
        "parties": PARTIES,
    }


@app.get("/booths")
def booths(metric: str = "margin_pct") -> dict:
    features = []
    for b in BOOTHS:
        value = b["signed_margin"] if metric == "margin_pct" else b.get(metric)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [b["lon"], b["lat"]]},
            "properties": {
                "booth_uid": b["booth_uid"], "ps_name_hi": b["ps_name_hi"],
                "building": b["building"], "village_or_locality": b["village"],
                "current_ps_number": b["ps_number"], "geocode_conf": 0.8,
                "area_id": b["area"]["area_id"], "area_hi": b["area"]["name_hi"],
                "area_en": b["area"]["name_en"], "area_kind": b["area"]["kind"],
                "block_id": b["area"]["block_id"],
                "margin_pct": b["margin_pct"], "turnout_pct": b["turnout_pct"],
                "new_voter_pct": b["new_voter_pct"], "priority_score": b["priority_score"],
                "floating_pct": b["floating_pct"], "margin_stddev": b["margin_stddev"],
                "electors": b["electors"], "winner_party": b["winner_party"],
                "runner_party": b["runner_party"],
                "metric": value, "metric_name": metric,
            },
        })
    return {"type": "FeatureCollection", "features": features,
            "meta": {"count": len(features), "ungeocoded": 0, "metric": metric,
                     "election_label": "VS-2024"}}


@app.get("/booths/{booth_uid}/card")
def booth_card(booth_uid: str) -> dict:
    b = BY_UID.get(booth_uid)
    if b is None:
        return JSONResponse({"detail": f"No booth {booth_uid}"}, status_code=404)
    block = next(x for x in BLOCKS if x["block_id"] == b["area"]["block_id"])

    results = []
    for election in ELECTIONS:
        scale = {2014: 0.72, 2019: 0.87, 2024: 1.0}.get(election["year"], 0.95)
        jmm, bjp = int(b["jmm"] * scale), int(b["bjp"] * scale)
        if election["type"] == "LS":
            jmm, bjp = int(jmm * 0.82), int(bjp * 0.78)
        votes = jmm + bjp + int(b["jlkm"] * scale) + int(b["nota"] * scale)
        winner, runner = ("JMM", "BJP") if jmm >= bjp else ("BJP", "JMM")
        results.append({
            "election_label": election["label"], "election_type": election["type"],
            "election_year": election["year"], "electors": int(b["electors"] * scale),
            "votes_counted": votes, "jmm": jmm, "bjp": bjp,
            "ajsu": int(b["jlkm"] * scale * 2) if election["type"] == "LS" else 0,
            "jlkm": int(b["jlkm"] * scale), "inc": 0, "rjd": 0, "jvm": 0,
            "others": 0, "nota": int(b["nota"] * scale),
            "winner_party": winner, "runner_party": runner,
            "margin_votes": abs(jmm - bjp),
            "margin_pct": round(100 * abs(jmm - bjp) / votes, 2) if votes else 0,
            "turnout_pct": round(100 * votes / max(1, int(b["electors"] * scale)), 2),
            "source_doc": f"form20_{election['label'].split()[0].lower()}.pdf",
            "source_page": 3 + b["ps_number"] // 20,
            "ps_numbers": str(b["ps_number"]),
        })

    communities = [
        ("Kurmi (Mahato)", "कुर्मी (महतो)", "OBC"), ("Muslim", "मुस्लिम", "MUSLIM"),
        ("Yadav", "यादव", "OBC"), ("Baniya", "बनिया", "OBC"),
        ("Santhal", "संताल", "ST"), ("Brahmin", "ब्राह्मण", "GEN"),
        ("Dusadh / Paswan", "दुसाध / पासवान", "SC"),
    ]
    raw = [max(0.5, RNG.gauss(14, 9)) for _ in communities]
    total = sum(raw)
    caste = [
        {"name_en": name_en, "name_hi": name_hi, "category": cat,
         "est_count": int(b["electors"] * value / total),
         "est_pct": round(100 * value / total, 1),
         "confidence": round(RNG.uniform(0.28, 0.82), 2), "source": "blend"}
        for (name_en, name_hi, cat), value in zip(communities, raw, strict=True)
    ]
    caste.sort(key=lambda c: -c["est_pct"])

    crosswalk = [
        {"label": "VS-2024", "ps_number": b["ps_number"], "match_method": "anchor",
         "confidence": 1.0, "reviewed": True},
        {"label": "VS-2019", "ps_number": b["ps_number"] - 2, "match_method": "fuzzy",
         "confidence": round(RNG.uniform(0.72, 0.99), 2), "reviewed": RNG.random() > 0.4},
        {"label": "VS-2014", "ps_number": b["ps_number"] - 5, "match_method": "fuzzy",
         "confidence": round(RNG.uniform(0.66, 0.95), 2), "reviewed": RNG.random() > 0.6},
    ]
    caveats = []
    weak = [c for c in crosswalk if c["confidence"] < 0.85 and not c["reviewed"]]
    if weak:
        caveats.append(
            f"Cross-year matching is unconfirmed for {', '.join(c['label'] for c in weak)} "
            f"(confidence below 0.85, not yet reviewed). Treat swing against those years as "
            f"provisional."
        )

    return {
        "booth": {"booth_uid": b["booth_uid"], "ps_name_hi": b["ps_name_hi"],
                  "building": b["building"], "village_or_locality": b["village"],
                  "current_ps_number": b["ps_number"], "geocode_conf": 0.8,
                  "area_id": b["area"]["area_id"], "area_hi": b["area"]["name_hi"],
                  "area_en": b["area"]["name_en"], "area_kind": b["area"]["kind"],
                  "block_id": block["block_id"], "block_en": block["name_en"],
                  "block_hi": block["name_hi"]},
        "results": results,
        "roll_snapshots": [{"label": "2026-SSR", "revision_date": "2026-08-01",
                            "electors": b["electors"], "male": int(b["electors"] * 0.52),
                            "female": int(b["electors"] * 0.48), "other": 1,
                            "age_18_19": int(b["electors"] * 0.03),
                            "age_20_29": int(b["electors"] * 0.24),
                            "age_30_39": int(b["electors"] * 0.23),
                            "age_40_49": int(b["electors"] * 0.19),
                            "age_50_59": int(b["electors"] * 0.16),
                            "age_60p": int(b["electors"] * 0.15)}],
        "roll_changes": [
            {"label": "2026-SSR", "revision_date": "2026-08-01", "is_post_sir": True,
             "additions": b["additions"], "deletions": b["deletions"], "modifications": 12,
             "add_18_19": int(b["additions"] * 0.3), "add_female": int(b["additions"] * 0.51),
             "del_death": int(b["deletions"] * 0.4), "del_shifted": int(b["deletions"] * 0.5)},
            {"label": "2026-SUP-1", "revision_date": "2026-06-01", "is_post_sir": False,
             "additions": int(b["additions"] * 0.3), "deletions": int(b["deletions"] * 0.2),
             "modifications": 4, "add_18_19": 6, "add_female": 9, "del_death": 3, "del_shifted": 2},
        ],
        "new_voters": {"electors_now": b["electors"], "additions": b["additions"],
                       "deletions": b["deletions"],
                       "net_change": b["additions"] - b["deletions"],
                       "add_18_19": int(b["additions"] * 0.3),
                       "add_female": int(b["additions"] * 0.51),
                       "new_voter_pct": b["new_voter_pct"],
                       "deleted_pct": round(100 * b["deletions"] / b["electors"], 2)},
        "priority": {"margin_pct": b["margin_pct"], "margin_votes": b["margin_votes"],
                     "new_voter_pct": b["new_voter_pct"], "margin_stddev": b["margin_stddev"],
                     "floating_pct": b["floating_pct"], "priority_score": b["priority_score"],
                     "priority_quartile": b["priority_quartile"],
                     "winner_party": b["winner_party"], "runner_party": b["runner_party"]},
        "crosswalk": crosswalk,
        "caste_estimate": caste,
        "caste_note": ("Estimates only, at booth level. Derived from surname inference, "
                       "Census 2011 proportions and any ground survey. No individual voter is "
                       "tagged. Figures below 0.4 confidence are too weak to use."),
        "caveats": caveats,
    }


@app.get("/results/{election_label}/booths")
def results(election_label: str) -> dict:
    rows = []
    for b in BOOTHS:
        block = next(x for x in BLOCKS if x["block_id"] == b["area"]["block_id"])
        rows.append({
            "booth_uid": b["booth_uid"], "ps_numbers": str(b["ps_number"]),
            "area_hi": b["area"]["name_hi"], "area_en": b["area"]["name_en"],
            "block_en": block["name_en"], "building": b["building"],
            "village_or_locality": b["village"], "electors": b["electors"],
            "votes_counted": b["votes"], "turnout_pct": b["turnout_pct"],
            "jmm": b["jmm"], "bjp": b["bjp"], "ajsu": 0, "jlkm": b["jlkm"],
            "inc": 0, "rjd": 0, "jvm": 0, "others": 0, "nota": b["nota"],
            "winner_party": b["winner_party"], "runner_party": b["runner_party"],
            "margin_votes": b["margin_votes"], "margin_pct": b["margin_pct"],
            "source_doc": "form20_vs2024.pdf", "source_page": 3 + b["ps_number"] // 20,
        })
    return {"election_label": election_label, "rows": rows, "count": len(rows)}


@app.get("/rolls/revisions")
def roll_revisions() -> dict:
    return {"rows": [
        {"revision_id": 2, "label": "2026-SSR", "revision_date": "2026-08-01",
         "is_post_sir": True, "is_mother": True},
        {"revision_id": 1, "label": "2026-SUP-1", "revision_date": "2026-06-01",
         "is_post_sir": False, "is_mother": False},
    ]}


@app.get("/rolls/changes")
def roll_changes(revision_label: str | None = None) -> dict:
    rows = []
    for b in BOOTHS:
        for label, revision_date, sir, scale in (
            ("2026-SSR", "2026-08-01", True, 1.0), ("2026-SUP-1", "2026-06-01", False, 0.3),
        ):
            if revision_label and label != revision_label:
                continue
            additions = int(b["additions"] * scale)
            deletions = int(b["deletions"] * scale)
            rows.append({
                "booth_uid": b["booth_uid"], "revision": label, "revision_date": revision_date,
                "is_post_sir": sir, "area_hi": b["area"]["name_hi"],
                "area_en": b["area"]["name_en"], "block_id": b["area"]["block_id"],
                "additions": additions, "deletions": deletions, "modifications": 12,
                "add_18_19": int(additions * 0.3), "add_female": int(additions * 0.51),
                "del_death": int(deletions * 0.4), "del_shifted": int(deletions * 0.5),
                "del_other": int(deletions * 0.1), "electors": b["electors"],
                "additions_pct": round(100 * additions / b["electors"], 2),
                "deletions_pct": round(100 * deletions / b["electors"], 2),
            })
    return {"rows": rows, "count": len(rows)}


@app.get("/caste")
def caste(min_conf: float = 0.4) -> dict:
    communities = [
        ("Kurmi (Mahato)", "कुर्मी (महतो)", "OBC"), ("Muslim", "मुस्लिम", "MUSLIM"),
        ("Yadav", "यादव", "OBC"), ("Baniya", "बनिया", "OBC"),
        ("Santhal", "संताल", "ST"), ("Brahmin", "ब्राह्मण", "GEN"),
        ("Dusadh / Paswan", "दुसाध / पासवान", "SC"),
    ]
    rng = random.Random(7)
    rows = []
    for b in BOOTHS:
        raw = [max(0.5, rng.gauss(14, 9)) for _ in communities]
        total = sum(raw)
        for (name_en, name_hi, cat), value in zip(communities, raw, strict=True):
            confidence = round(rng.uniform(0.25, 0.85), 2)
            if confidence < min_conf:
                continue
            rows.append({
                "booth_uid": b["booth_uid"], "area_id": b["area"]["area_id"],
                "area_hi": b["area"]["name_hi"], "area_en": b["area"]["name_en"],
                "community_en": name_en, "community_hi": name_hi, "category": cat,
                "est_count": int(b["electors"] * value / total),
                "est_pct": round(100 * value / total, 1),
                "confidence": confidence, "source": "blend",
            })
    return {"rows": rows, "count": len(rows), "min_conf": min_conf,
            "disclaimer": ("Estimates at booth level only, derived from surname inference, "
                           "Census 2011 proportions and any ground survey. No individual voter "
                           "is tagged with a community.")}


@app.get("/transfer")
def transfer(year: int = 2024) -> dict:
    rows = []
    for b in BOOTHS:
        for party, ls_factor, vs_votes in (
            ("AJSU", 1.35, 0), ("JMM", 0.82, b["jmm"]),
            ("JLKM", 2.9, b["jlkm"]), ("BJP", 0.30, b["bjp"]),
        ):
            base = b["jlkm"] if party == "AJSU" else (b["bjp"] if party == "BJP" else vs_votes)
            ls_votes = max(0, int(base * ls_factor))
            ls_total = b["votes"]
            rows.append({
                "booth_uid": b["booth_uid"], "area_hi": b["area"]["name_hi"],
                "area_en": b["area"]["name_en"], "block_id": b["area"]["block_id"],
                "party": party, "ls_votes": ls_votes, "vs_votes": vs_votes,
                "delta_votes": vs_votes - ls_votes,
                "ls_share_pct": round(100 * ls_votes / ls_total, 2) if ls_total else 0,
                "vs_share_pct": round(100 * vs_votes / b["votes"], 2) if b["votes"] else 0,
                "delta_share_pct": round(
                    (100 * vs_votes / b["votes"] if b["votes"] else 0)
                    - (100 * ls_votes / ls_total if ls_total else 0), 2),
                "floating_pct": b["floating_pct"],
            })
    return {"year": year, "rows": rows, "count": len(rows),
            "note": ("Lok Sabha figures here are the AC-32 segment of PC-11 Giridih, not the "
                     "whole parliamentary seat.")}


@app.get("/local-results")
def local_results(seat_type: str | None = None) -> dict:
    rng = random.Random(11)
    rows = []
    for index, area in enumerate(AREAS):
        kind = "ward" if area["kind"] == "ward" else "mukhiya"
        if seat_type and kind != seat_type:
            continue
        votes = rng.randint(900, 4200)
        runner = int(votes * rng.uniform(0.55, 0.92))
        rows.append({
            "local_result_id": index, "election": "WARD-2018" if kind == "ward" else "PANCHAYAT-2022",
            "seat_type": kind, "seat_name": area["name_en"],
            "area_hi": area["name_hi"], "area_en": area["name_en"],
            "winner": f"Candidate {index + 1}", "runner_up": f"Candidate {index + 40}",
            "tagged_party": rng.choice(["JMM", "BJP", "AJSU", None, None]),
            "tag_source": "Local reporter, Sep 2026", "tag_confidence": 0.6,
            "votes": votes, "runner_up_votes": runner, "margin": votes - runner,
        })
    return {"rows": rows, "count": len(rows),
            "note": "Panchayat elections are contested without party symbols."}


ISSUES = ["water", "roads", "electricity", "health", "education", "employment/migration",
          "mining/coal", "Parasnath/Marang Buru", "law-and-order", "welfare-schemes",
          "corruption", "candidate/organisation", "alliance", "other"]

HEADLINES = [
    ("गिरिडीह उपचुनाव की तैयारी तेज, प्रशासन ने बुलाई बैठक", "Prabhat Khabar", ["candidate/organisation"]),
    ("पीरटांड़ के कई गांवों में जलापूर्ति ठप, ग्रामीणों का प्रदर्शन", "Dainik Bhaskar", ["water"]),
    ("पारसनाथ मुद्दे पर आदिवासी संगठनों की महारैली", "Hindustan", ["Parasnath/Marang Buru"]),
    ("कोयला खदान में हादसा, दो मजदूर घायल", "Prabhat Khabar", ["mining/coal", "law-and-order"]),
    ("गिरिडीह नगर निगम की सड़कों की हालत खराब, वार्डवासी नाराज़", "Dainik Bhaskar", ["roads"]),
    ("जेएलकेएम ने उपचुनाव लड़ने का ऐलान किया", "Hindustan", ["candidate/organisation", "alliance"]),
    ("मनरेगा भुगतान में देरी से मजदूर परेशान", "Prabhat Khabar", ["employment/migration", "welfare-schemes"]),
    ("बिजली कटौती से गिरिडीह शहर में आक्रोश", "Dainik Bhaskar", ["electricity"]),
    ("सदर अस्पताल में डॉक्टरों की कमी, मरीज परेशान", "Hindustan", ["health"]),
    ("इंडिया गठबंधन की बैठक, सीट पर दावेदारी तय", "Prabhat Khabar", ["alliance"]),
]


@app.get("/news")
def news(q: str | None = None, issue: str | None = None, limit: int = 50) -> dict:
    rng = random.Random(3)
    rows = []
    for index in range(min(limit, 40)):
        title, source, issue_tags = HEADLINES[index % len(HEADLINES)]
        if issue and issue not in issue_tags:
            continue
        published = date.today() - timedelta(days=index * 2 + rng.randint(0, 2))
        rows.append({
            "news_id": 1000 + index, "published": published.isoformat(), "source": source,
            "title": title,
            "summary_hi": "स्थानीय स्तर पर इस मुद्दे को लेकर चर्चा तेज है। "
                          "प्रशासन ने जांच का आश्वासन दिया है।",
            "summary_en": "The issue is being widely discussed locally; the administration has "
                          "promised an inquiry.",
            "issues": issue_tags,
            "parties": rng.sample(["JMM", "BJP", "AJSU", "JLKM"], k=rng.randint(1, 2)),
            "sentiment": rng.randint(-2, 2),
            "sentiment_by_party": {"JMM": rng.randint(-1, 1), "BJP": rng.randint(-1, 1)},
            "area_ids": [rng.choice(AREAS)["area_id"]],
            "url": f"https://example.invalid/giridih/story-{1000 + index}",
            "similarity": round(rng.uniform(0.62, 0.94), 2) if q else None,
        })
    return {"rows": rows, "count": len(rows), "issues": ISSUES}


@app.get("/news/issues")
def news_issues(days: int = 30) -> dict:
    rng = random.Random(5)
    counts: dict[str, int] = {}
    for _, _, tags in HEADLINES:
        for tag in tags:
            counts[tag] = counts.get(tag, 0) + rng.randint(2, 9)
    by_issue = sorted(
        ({"issue": k, "items": v, "avg_sentiment": round(rng.uniform(-1.2, 0.8), 2)}
         for k, v in counts.items()),
        key=lambda r: -r["items"],
    )
    weeks = [{"week": (date.today() - timedelta(weeks=i)).isoformat(),
              "items": rng.randint(8, 30)} for i in range(6)][::-1]
    by_party = [{"party": p, "mentions": rng.randint(5, 40)}
                for p in ("JMM", "BJP", "JLKM", "AJSU")]
    return {"since": (date.today() - timedelta(days=days)).isoformat(),
            "by_issue": by_issue, "by_week": weeks, "by_party": by_party}


@app.get("/priority")
def priority(limit: int = 50) -> dict:
    rows = sorted(BOOTHS, key=lambda b: -b["priority_score"])[:limit]
    return {"rows": [{
        "booth_uid": b["booth_uid"], "area_hi": b["area"]["name_hi"],
        "area_en": b["area"]["name_en"], "block_id": b["area"]["block_id"],
        "building": b["building"], "margin_pct": b["margin_pct"],
        "margin_votes": b["margin_votes"], "electors": b["electors"],
        "turnout_pct": b["turnout_pct"], "new_voter_pct": b["new_voter_pct"],
        "additions": b["additions"], "margin_stddev": b["margin_stddev"],
        "floating_pct": b["floating_pct"], "priority_score": b["priority_score"],
        "priority_quartile": b["priority_quartile"], "winner_party": b["winner_party"],
        "runner_party": b["runner_party"],
    } for b in rows], "count": len(rows),
        "formula": "0.35 tight margin + 0.25 new voters + 0.20 volatility + 0.20 floating vote"}


@app.post("/scenario")
def scenario(body: dict) -> dict:
    """Runs the REAL engine from analytics/scenario.py on the synthetic baseline,
    so what is shown here is the actual projection arithmetic."""
    import sys
    from pathlib import Path

    root = str(Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.insert(0, root)
    from analytics.scenario import (
        BoothBaseline, ScenarioInput, jlkm_transfer_scenario, project,
    )

    baseline = [
        BoothBaseline(b["booth_uid"],
                      {"JMM": b["jmm"], "BJP": b["bjp"], "JLKM": b["jlkm"], "NOTA": b["nota"]},
                      additions=b["additions"])
        for b in BOOTHS
    ]
    transfer_matrix = dict(body.get("transfer") or {})
    if body.get("jlkm_to_bjp") is not None:
        transfer_matrix.update(jlkm_transfer_scenario(float(body["jlkm_to_bjp"])))

    result = project(baseline, ScenarioInput(
        turnout_multiplier=float(body.get("turnout_multiplier", 1.0)),
        transfer=transfer_matrix,
        sympathy_swing=float(body.get("sympathy_swing", 0.0)),
        new_voter_turnout=float(body.get("new_voter_turnout", 0.6)),
        draws=int(body.get("draws", 500)),
        noise=float(body.get("noise", 0.05)),
        seed=body.get("seed", 42),
    ))
    return {
        "booths": len(baseline), "votes": result.votes,
        "margin": {"point": result.margin_point, "p10": result.margin_p10,
                   "p50": result.margin_p50, "p90": result.margin_p90},
        "winner": result.winner, "total_votes": result.total_votes,
        "high_variance_booths": result.booth_variance, "draws": result.draws,
        "assumptions": result.assumptions,
        "disclaimer": ("This is arithmetic on the assumptions above applied to the 2024 booth "
                       "result. It is not a forecast. NOTE: this demo runs it on SYNTHETIC "
                       "booth data."),
    }


@app.get("/admin/review-queue")
def review_queue() -> dict:
    rows = [
        {"id": 1, "kind": "crosswalk", "ref": "VS-2019#PS147",
         "payload": {"components": {"building": 0.78, "place": 0.71, "roll_part": None},
                     "runner_up": ["B0149", 0.74], "score": 0.76},
         "status": "open",
         "note": "PS 147 -> B0148 at 0.760 (between 0.65 and 0.85) - confirm or correct",
         "created_at": "2026-09-19T08:12:00Z"},
        {"id": 2, "kind": "form20_row", "ref": "form20_vs2019.pdf#PS212",
         "payload": {"ps_number": 212, "page": 14, "computed": 742, "printed": 748},
         "status": "open",
         "note": "PS 212: candidate votes + NOTA = 742, printed valid total = 748",
         "created_at": "2026-09-19T08:12:00Z"},
        {"id": 3, "kind": "area_alias", "ref": "batch:msgbatch_demo",
         "payload": {"names": ["मधुबन", "Madhuban", "पीरटाँड़"]},
         "status": "open",
         "note": "3 place name(s) in the news could not be matched to an area - add the "
                 "spelling variants to area_alias",
         "created_at": "2026-09-20T07:02:00Z"},
        {"id": 4, "kind": "ocr_page", "ref": "form20_vs2014.pdf#p22",
         "payload": {"confidence": 61.4, "file": "form20_vs2014.pdf"},
         "status": "open", "note": "OCR confidence 61.4, below the floor of 70",
         "created_at": "2026-09-18T22:40:00Z"},
    ]
    return {"rows": rows, "count": len(rows),
            "open_by_kind": [{"kind": "crosswalk", "open": 23}, {"kind": "form20_row", "open": 4},
                             {"kind": "area_alias", "open": 3}, {"kind": "ocr_page", "open": 1}]}


@app.post("/admin/review-queue/{item_id}")
def resolve_item(item_id: int, body: dict) -> dict:
    return {"updated": 1}


@app.get("/admin/usage")
def usage(days: int = 30) -> dict:
    rng = random.Random(13)
    by_day = []
    for i in range(10):
        day = (date.today() - timedelta(days=i)).isoformat()
        for model, purpose in (("claude-haiku-4-5", "router"),
                               ("claude-haiku-4-5", "chat.lookup"),
                               ("claude-sonnet-5", "chat.analysis"),
                               ("claude-haiku-4-5", "news.label")):
            by_day.append({"day": day, "model": model, "purpose": purpose,
                           "input_tokens": rng.randint(2000, 60000),
                           "cached_tokens": rng.randint(20000, 300000),
                           "output_tokens": rng.randint(400, 9000),
                           "cost_usd": round(rng.uniform(0.02, 1.9), 4),
                           "calls": rng.randint(5, 180)})
    return {"by_day": by_day,
            "month_to_date": {"cost_usd": 41.83, "calls": 2317},
            "cache_hit_rate": {"pct": 93.4},
            "by_user_today": [{"name": "Demo admin", "role": "admin",
                               "tokens": 84210, "cost_usd": 1.62}]}


@app.get("/admin/jobs")
def jobs(limit: int = 50) -> dict:
    now = date.today().isoformat()
    last = [
        {"job": "analytics.caste_estimate", "started": f"{now}T07:45:00Z",
         "finished": f"{now}T07:45:31Z", "status": "ok"},
        {"job": "analytics.refresh", "started": f"{now}T07:30:00Z",
         "finished": f"{now}T07:30:12Z", "status": "ok"},
        {"job": "ceo.check_new_supplement", "started": f"{now}T06:00:00Z",
         "finished": f"{now}T06:00:04Z", "status": "ok"},
        {"job": "news.crawl", "started": f"{now}T08:00:00Z",
         "finished": f"{now}T08:00:19Z", "status": "ok"},
        {"job": "news.embed", "started": f"{now}T07:15:00Z",
         "finished": f"{now}T07:16:02Z", "status": "ok"},
        {"job": "news.label_collect", "started": f"{now}T07:00:00Z",
         "finished": f"{now}T07:00:44Z", "status": "ok"},
        {"job": "ops.backup", "started": f"{now}T03:00:00Z",
         "finished": f"{now}T03:01:22Z", "status": "ok"},
        {"job": "ops.usage_report", "started": f"{now}T08:00:00Z",
         "finished": None, "status": "failed"},
    ]
    return {"recent": last, "last_per_job": last}


@app.get("/ground-reports")
def ground_reports(limit: int = 50) -> dict:
    return {"rows": []}


@app.post("/chat")
async def chat(request: Request) -> StreamingResponse:
    """Mimics the SSE shape of api/routers/chat.py so the panel can be seen
    working. It does NOT call a model - it replays a canned exchange."""
    body = await request.json()
    question = (body.get("message") or "").strip()

    async def events():
        import asyncio
        import json as _json

        yield f"event: status\ndata: {_json.dumps({'state': 'thinking'})}\n\n"
        await asyncio.sleep(0.4)

        sql = ("SELECT booth_uid, bjp, jmm, (bjp - jmm) AS lead\n"
               "FROM mv_result_booth_wide\nWHERE election_label = 'VS-2024'\n"
               "ORDER BY lead DESC LIMIT 20")
        yield ("event: tool\ndata: " + _json.dumps({
            "name": "run_sql", "arguments": {"sql": sql}, "is_error": False,
            "seconds": 0.41,
            "preview": "20 row(s) from mv_result_booth_wide\n\nbooth_uid,bjp,jmm,lead\n"
                       "B0007,412,188,224\nB0031,398,201,197\n...",
        }, ensure_ascii=False) + "\n\n")
        await asyncio.sleep(0.5)

        top = sorted(BOOTHS, key=lambda b: b["bjp"] - b["jmm"], reverse=True)[:5]
        listing = "\n".join(
            f"{i + 1}. {b['booth_uid']} ({b['area']['name_hi']}) — BJP {b['bjp']:,}, "
            f"JMM {b['jmm']:,}, लीड {b['bjp'] - b['jmm']:,}"
            for i, b in enumerate(top)
        )
        answer = (
            f"2024 में BJP की सबसे बड़ी लीड इन बूथों पर रही:\n\n{listing}\n\n"
            f"ये सभी आंकड़े VS-2024 के फॉर्म 20 से हैं [VS-2024, Form20 p.7]।\n\n"
            f"⚠️ डेमो सर्वर: ये संख्याएँ कृत्रिम (synthetic) हैं।"
        ) if any("ऀ" <= ch <= "ॿ" for ch in question) else (
            f"BJP's largest booth leads in VS-2024:\n\n{listing}\n\n"
            f"All figures from the VS-2024 Form 20 [VS-2024, Form20 p.7].\n\n"
            f"⚠️ Demo server: these numbers are synthetic."
        )
        yield ("event: answer\ndata: " + _json.dumps({
            "text": answer, "intent": "lookup", "lang": "hi", "model": "demo",
            "rounds": 1, "truncated": False, "degraded": False,
            "notice": "Demo server - no model was called and the data is synthetic.",
            "cost_usd": 0.0031,
        }, ensure_ascii=False) + "\n\n")
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    print("=" * 74)
    print(" DEMO SERVER - SYNTHETIC DATA. Not the real backend (api/main.py).")
    print(" Booth-level numbers are generated; AC totals are scaled onto the")
    print(" published 2024 figures so the shapes look plausible.")
    print("=" * 74)
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")

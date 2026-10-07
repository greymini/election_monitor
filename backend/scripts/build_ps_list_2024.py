#!/usr/bin/env python
"""Giridih (AC-32) 2024 polling stations: place each one in a village, panchayat or ward.

    python scripts/build_ps_list_2024.py [--geocode]

**Source.** CEO Jharkhand's polling-station register
(https://ceojh.jharkhand.gov.in/PSdetails/default.aspx, District Giridih,
AC 32), saved as `raw/ps_list/ceojh_psdetails_ac32.csv`: 367 stations,
numbered 1-367, with English and Hindi names. 367 is the station count of the
VS-2024 and LS-2024 Form 20s, and the names agree with the voting pattern of
the same numbers (stations 1-125 vote like a town; Pirtand's villages come
last), so this is taken as the 2024 numbering. It is **not** the 2019
numbering: VS-2019 station 22 was a different building.

The register gives a building and, inside its name, a locality. Everything
else here is derived, and each row says how (`locate_method`, `locate_conf`):

1. **Village.** The name is reduced to place-like words (school and building
   words removed) and compared, as a Latin key, with every LGD village of
   Giridih and Pirtand blocks; the Hindi name's last word is compared too. A
   match must be close on the key and on the spelling, beat the runner-up and
   be the only village of that name (`build_ps_list_current.match`).
2. **Setting and block.** Stations are numbered along a route, so the town,
   Giridih's villages and Pirtand's villages each form one run. The two cut
   points are the ones that agree with the most matched stations. A matched
   village that disagrees with its run is reported, not forced.
3. **Rural stations** take the panchayat of their village and sit inside that
   village's polygon (india-geodata LGD villages, CC0). An unmatched rural
   station takes the panchayat its neighbours share and sits inside that
   panchayat, with lower confidence.
4. **Town stations** are located from the locality in their name, looked up
   in OpenStreetMap place names (`raw/geo/osm/`, ODbL) and, with
   `--geocode`, Nominatim (cached, one request a second). The ward is the
   Swachh Bharat Mission ward polygon (Dec 2021) containing that point. Ward
   numbers printed in station names are **not** used: they follow the old
   Nagar Parishad numbering (station 49 says "Ward No-15" for Makatpur, which
   is ward 9 on the 2021 map). A station with no usable locality takes the
   ward of the nearest located station in the sequence.

Several stations in one building or village get distinct points ~100 m apart
inside the same polygon, so markers do not stack. Points are approximate: the
register has no coordinates.

Writes `db/seed/ps_list/giridih_ps2024.csv`.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import pathlib
import re
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

RAW = ROOT / "raw"
PS_LIST = RAW / "ps_list" / "ceojh_psdetails_ac32.csv"
VILLAGES = RAW / "geo" / "lgd" / "lgd_30Sep2026_giridih_villages.csv"
VILLAGE_SHAPES = RAW / "geo" / "lgd" / "lgd_villages_giridih_district.geojson"
GP_SHAPES = RAW / "geo" / "lgd" / "gp_from_lgd_villages_giridih_district.geojson"
GP_FALLBACK = RAW / "geo" / "lgd" / "lgd_gp_giridih_district.geojson"
WARDS = RAW / "geo" / "lgd" / "SBM_Wards_giridih.geojson"
OSM = RAW / "geo" / "osm" / "giridih_town_names.json"
NOMINATIM_CACHE = RAW / "geo" / "osm" / "nominatim_localities.json"
OUT = ROOT / "db" / "seed" / "ps_list" / "giridih_ps2024.csv"

SOURCE = ("CEO Jharkhand polling-station register, AC 32 Giridih "
          "(ceojh.jharkhand.gov.in/PSdetails), 367 stations")
BLOCKS = ("Giridih", "Pirtand")
TOWN_BBOX = (86.22, 24.13, 86.37, 24.24)          # lon/lat, a margin around the wards

# Words in a station name that are about the building, not the place.
BUILDING_WORDS = set("""
ums u m s ups p hs uhs us uus ms school middle primary prathmik upgraded utkramit high
girls girl boys kanya balika urdu hindi bangla bangali english medium new old building
bhawan bhavan samudayik samudayak samudaik community panchayat anganbadi anganwadi kendra
centre center east west north south middle part purvi paschimi uttari dakshini room left
right tola sc st no project public library office karyalay college mahila vidya vidyalaya
mandir mission sah located awasthit awashtith istith sthit road ward nagar parishad
nagarpalika sri shri sent saint and of the at holy trinity convent dharamshala dharam shala
institute academy residential awasiye kasturba gandhi govt government rajkiya adarsh new
main naya purana bazar chowk colony
""".split())
MIN_TOKEN = 4
SECOND_KEY, SECOND_FULL = 0.92, 0.8      # looser than the strict rule; needs context to accept
NEAR_DEGREES = 0.08                      # ~8 km from the neighbouring stations, at most


def _rows(path: pathlib.Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def place_words(name: str) -> list[str]:
    words = re.sub(r"[^A-Za-z]", " ", name).lower().split()
    return [w for w in words if w not in BUILDING_WORDS and len(w) >= 3]


def candidates(name_en: str, name_hi: str) -> list[str]:
    from build_ps_list_current import latin, village_of

    words = place_words(name_en)
    out = set(words)
    out |= {a + b for a, b in zip(words, words[1:], strict=False)}
    out |= {f"{a} {b}" for a, b in zip(words, words[1:], strict=False)}
    hi = village_of(name_hi)
    if hi:
        out.add(latin(hi).lower())
    # Station names write the flapped ड़ as d ("Pipratand"), LGD as r ("Pipratanr").
    out |= {re.sub(r"tand", "tanr", c).replace("pahad", "pahar").replace("gadh", "garh")
            for c in out}
    return sorted(c for c in out
                  if len(re.sub(r"[^a-z]", "", c)) >= MIN_TOKEN and c not in BUILDING_WORDS)


def best_village(cands: list[str], villages: list[dict]) -> dict | None:
    """The best LGD village over every candidate string, with the same acceptance
    rule as the current-parts matcher (key and spelling close, clear margin,
    unique name)."""
    from build_ps_list_current import match

    best = None
    for c in cands:
        m = match(c, villages, latin_input=True)
        rank = (m["status"] == "matched", m["score"], m["full"])
        if best is None or rank > (best["status"] == "matched", best["score"], best["full"]):
            best = m | {"token": c}
    return best


def options(cands: list[str], villages: list[dict]) -> list[tuple[float, float, dict, str]]:
    """Every village close to any candidate string: (key score, spelling score, village, token)."""
    import jellyfish
    from build_ps_list_current import key

    out = []
    for c in cands:
        kc = key(c)
        for v in villages:
            k = jellyfish.jaro_winkler_similarity(kc, key(v["name"]))
            if k < SECOND_KEY:
                continue
            f = jellyfish.jaro_winkler_similarity(c, v["name"].lower())
            if f >= SECOND_FULL:
                out.append((k, f, v, c))
    return sorted(out, key=lambda t: (-t[0], -t[1]))


def second_pass(out: list[dict], rows: list[dict], villages: list[dict], vshape: dict) -> int:
    """Settle stations the strict rule left open, using where their neighbours are.

    A name several villages share ("Barwadih", "Ranidih") or a near spelling
    ("Kalhamanjho" for Kolhamanjo) is accepted when the village lies in the
    station's block run and either belongs to a panchayat of the matched
    stations within four places either side, or - for an exact name shared by
    several villages - is the namesake nearest to those stations.
    """
    from shapely.geometry import Point

    fixed = 0
    for i, rec in enumerate(out):
        if rec["_run"] == "town" or rec["village_en"]:
            continue
        want = rec["_run"]
        near = [o for o in out[max(0, i - 4):i + 5] if o is not rec and o["village_en"]
                and o["_run"] == want and o["village_lgd_code"] in vshape]
        near_gps = {o["panchayat_lgd_code"] for o in near}
        ops = [t for t in options(rows[i]["cands"], villages) if t[2]["block"] == want]
        if not ops or not near:
            continue
        pts = [vshape[o["village_lgd_code"]].representative_point() for o in near]
        cx, cy = sum(p.x for p in pts) / len(pts), sum(p.y for p in pts) / len(pts)

        centre = Point(cx, cy)

        def dist(v, centre=centre):
            poly = vshape.get(v["code"])
            return poly.representative_point().distance(centre) if poly else 9.0

        top_k = ops[0][0]
        exact = [t for t in ops if t[0] >= 0.99 and t[1] >= 0.95]
        pick, how = None, ""
        if exact:
            pick = min(exact, key=lambda t: dist(t[2]))
            how = "namesake nearest its neighbours" if len({t[2]["code"] for t in exact}) > 1 else "exact name"
        else:
            same_gp = [t for t in ops if t[0] >= top_k - 0.02 and t[2]["gp_code"] in near_gps]
            if same_gp:
                pick, how = same_gp[0], "near spelling, panchayat of its neighbours"
        if pick is None or dist(pick[2]) > NEAR_DEGREES:
            continue
        k, f, v, tok = pick
        rec.update(village_en=v["name"], village_lgd_code=v["code"], village_census_code=v["census"],
                   panchayat_en=v["gp"], panchayat_lgd_code=v["gp_code"], match_score=round(k, 2),
                   locate_method=f"lgd-village-context:{tok}", locate_conf=0.5,
                   note=how)
        fixed += 1
    return fixed


def cut_points(labels: list[str | None]) -> tuple[int, int]:
    """(last town station, last Giridih-village station) that agree with the most
    labelled stations. labels[i] is 'town', 'giridih', 'pirtand' or None."""
    n = len(labels)
    best, best_score = (0, 0), -1
    pre = {k: [0] * (n + 1) for k in ("town", "giridih", "pirtand")}
    for i, lab in enumerate(labels):
        for k in pre:
            pre[k][i + 1] = pre[k][i] + (lab == k)
    for a in range(n + 1):
        for b in range(a, n + 1):
            score = pre["town"][a] + (pre["giridih"][b] - pre["giridih"][a]) \
                + (pre["pirtand"][n] - pre["pirtand"][b])
            if score > best_score:
                best, best_score = (a, b), score
    return best


def spread(polygon, anchor, k: int):
    """The k-th distinct point near `anchor` inside `polygon` (golden-angle spiral, ~100 m)."""
    from shapely.geometry import Point

    if k == 0 and polygon.contains(anchor):
        return anchor
    for step in range(k, k + 60):
        r = 0.0009 * math.sqrt(step + 1)              # ~100 m per ring at this latitude
        t = step * 2.399963
        p = Point(anchor.x + r * math.cos(t), anchor.y + r * math.sin(t))
        if polygon.contains(p):
            return p
    return polygon.representative_point()


def nominatim(term: str, cache: dict, allow_network: bool) -> tuple[float, float] | None:
    if term in cache:
        hit = cache[term]
        return tuple(hit) if hit else None
    if not allow_network:
        return None
    import httpx

    from common.config import get_settings

    time.sleep(1.1)
    lon0, lat0, lon1, lat1 = TOWN_BBOX
    try:
        res = httpx.get("https://nominatim.openstreetmap.org/search", params={
            "q": f"{term}, Giridih, Jharkhand", "format": "json", "limit": 1,
            "viewbox": f"{lon0},{lat1},{lon1},{lat0}", "bounded": 1},
            headers={"User-Agent": get_settings().nominatim_user_agent}, timeout=20).json()
    except Exception:
        return None
    cache[term] = [float(res[0]["lon"]), float(res[0]["lat"])] if res else None
    return tuple(cache[term]) if cache[term] else None


def build(allow_network: bool = False) -> list[dict]:
    from build_ps_list_current import key
    from shapely.geometry import Point, shape

    villages = []
    for v in _rows(VILLAGES):
        if v["Development Block Name (In English)"] not in BLOCKS:
            continue
        gp_code = v["Local Body Code"] if v["Local Body Code"] not in ("", "0") else ""
        villages.append({
            "name": v["Village Name (In English)"].strip(), "code": v["Village Code"],
            "census": v["Village Census 2011 Code"],
            "gp": v["Local Body Name (In English)"].strip() if gp_code else "", "gp_code": gp_code,
            "block": v["Development Block Name (In English)"]})
    rural = [v for v in villages if v["gp_code"]]
    vshape = {str(f["properties"]["vil_lgd"]): shape(f["geometry"]) for f in
              json.loads(VILLAGE_SHAPES.read_text(encoding="utf-8"))["features"]}
    gshape = {str(f["properties"]["gpcode"]): shape(f["geometry"]) for f in
              json.loads(GP_FALLBACK.read_text(encoding="utf-8"))["features"]
              if f["properties"].get("gpcode")}
    gshape |= {str(f["properties"]["gp_code"]): shape(f["geometry"]) for f in
               json.loads(GP_SHAPES.read_text(encoding="utf-8"))["features"]}
    wards = [(int(f["properties"]["wardcode"]), shape(f["geometry"])) for f in
             json.loads(WARDS.read_text(encoding="utf-8"))["features"]]
    ward_poly = dict(wards)
    osm = {}
    for e in json.loads(OSM.read_text(encoding="utf-8"))["elements"]:
        t = e["tags"]
        if "place" not in t or t["place"] in ("city", "town"):     # "Giridih" itself says nothing
            continue
        lon, lat = (e["lon"], e["lat"]) if "lon" in e else (e["center"]["lon"], e["center"]["lat"])
        for name in {t["name"], t.get("name:en", "")} - {""}:
            osm[key(name)] = (lon, lat, name)
    cache = json.loads(NOMINATIM_CACHE.read_text(encoding="utf-8")) if NOMINATIM_CACHE.exists() else {}

    def ward_at(point):
        hit = next((n for n, poly in wards if poly.contains(point)), None)
        return hit if hit is not None else min(wards, key=lambda w: w[1].distance(point))[0]

    def in_town(point):
        return any(poly.contains(point) for _, poly in wards)

    def town_part(poly):
        """The largest piece of a village polygon inside one ward, or None."""
        best = max((w.intersection(poly) for _, w in wards), key=lambda g: g.area)
        return best if best.area > 0 else None

    municipal = [v for v in villages if not v["gp_code"] and v["block"] == "Giridih"
                 and v["code"] in vshape and town_part(vshape[v["code"]]) is not None]

    # Pass 1: candidates, a provisional village match, an OSM locality.
    stations = sorted(_rows(PS_LIST), key=lambda r: int(r["ps_number"]))
    rows = []
    for st in stations:
        cands = candidates(st["ps_name"], st["ps_name_hi"])
        m = best_village(cands, rural)
        ok = m is not None and m["status"] == "matched"
        town_hit = None
        for c in cands:
            hit = osm.get(key(c))
            if hit and in_town(Point(hit[0], hit[1])):
                town_hit = (hit[0], hit[1], f"osm:{hit[2]}")
                break
        label = (m["block"].lower() if ok else
                 "town" if town_hit or re.search(r"\bward\b", st["ps_name"], re.I) else None)
        rows.append({"st": st, "cands": cands, "m": m if ok else None, "town_hit": town_hit,
                     "label": label})

    # Pass 2: the three runs - the old town, Giridih's villages, Pirtand's.
    town_end, giridih_end = cut_points([r["label"] for r in rows])

    out = []
    for i, r in enumerate(rows):
        st = r["st"]
        run = "town" if i < town_end else "Giridih" if i < giridih_end else "Pirtand"
        rec = {"ac_number": 32, "ps_number": int(st["ps_number"]), "ps_name_en": st["ps_name"].strip(),
               "ps_name_hi": st["ps_name_hi"].strip(), "setting": "", "block_name_en": "",
               "village_en": "", "village_lgd_code": "", "village_census_code": "",
               "panchayat_en": "", "panchayat_lgd_code": "", "ward_no": "",
               "lon": "", "lat": "", "locate_method": "", "locate_conf": "", "match_score": "",
               "note": "", "source": SOURCE, "_run": run, "_point": None, "_poly": None}
        if run == "town":
            rec["setting"] = "urban"
            if r["town_hit"]:
                rec["_point"] = Point(r["town_hit"][0], r["town_hit"][1])
                rec["locate_method"], rec["locate_conf"] = r["town_hit"][2], 0.5
            else:
                # A village the Municipal Corporation absorbed (Koldiha, Bakshidih):
                # the part of its LGD polygon inside the wards.
                m = best_village(r["cands"], municipal)
                if m is not None and m["status"] == "matched":
                    inter = town_part(vshape[m["village_code"]])
                    if inter is not None:
                        rec["_point"], rec["_poly"] = inter.representative_point(), inter
                        rec["locate_method"], rec["locate_conf"] = f"lgd-village:{m['village_lgd']}", 0.45
            if rec["_point"] is None:
                for c in r["cands"]:
                    hit = nominatim(c, cache, allow_network)
                    if hit and in_town(Point(*hit)):
                        rec["_point"] = Point(*hit)
                        rec["locate_method"], rec["locate_conf"] = f"nominatim:{c}", 0.45
                        break
        else:
            # Matched again inside the run's block. Villages with no panchayat
            # are included: in Giridih block they are the areas the Municipal
            # Corporation absorbed in 2016 (Sihodih, Dandidih, Sirsiya ...).
            m = best_village(r["cands"], [v for v in villages if v["block"] == run])
            if m is not None and m["status"] == "matched":
                rec.update(village_en=m["village_lgd"], village_lgd_code=m["village_code"],
                           village_census_code=m["census"], panchayat_en=m["panchayat"],
                           panchayat_lgd_code=m["panchayat_code"], match_score=m["score"],
                           locate_method=f"lgd-village:{m['token']}", locate_conf=0.6)
            elif r["m"] is not None:
                rec["note"] = f"name matches a village of {r['m']['block']} block, but it is in the {run} run"
        out.append(rec)

    second = second_pass(out, rows, villages, vshape)

    # Pass 3: urban or rural. A matched village without a panchayat is municipal.
    for rec in out:
        if not rec["setting"] and rec["village_en"]:
            rec["setting"] = "rural" if rec["panchayat_lgd_code"] else "urban"
    for i, rec in enumerate(out):
        if rec["setting"]:
            continue
        prev = next((o for o in reversed(out[:i]) if o["village_en"] and o["_run"] == rec["_run"]), None)
        nxt = next((o for o in out[i + 1:] if o["village_en"] and o["_run"] == rec["_run"]), None)
        both = prev is not None and nxt is not None
        src = prev or nxt
        if src is None:
            rec["setting"], rec["note"] = "rural", "no matched neighbour"
            continue
        if both and prev["setting"] == nxt["setting"] == "urban":
            rec["setting"] = "urban"
            rec["locate_method"], rec["locate_conf"] = "sequence:between two municipal stations", 0.3
        elif both and prev["panchayat_lgd_code"] and prev["panchayat_lgd_code"] == nxt["panchayat_lgd_code"]:
            rec.update(setting="rural", panchayat_en=prev["panchayat_en"],
                       panchayat_lgd_code=prev["panchayat_lgd_code"],
                       locate_method="sequence:between two stations of this panchayat", locate_conf=0.35)
        else:
            rec.update(setting=src["setting"], panchayat_en=src["panchayat_en"],
                       panchayat_lgd_code=src["panchayat_lgd_code"],
                       locate_method=f"sequence:nearest matched station (PS {src['ps_number']})",
                       locate_conf=0.2)
    for rec in out:
        rec["block_name_en"] = ("Giridih Municipal Corporation" if rec["setting"] == "urban"
                                else f"{rec['_run']} Block")

    # Pass 4: an anchor point and the polygon it must stay in.
    for rec in out:
        poly = vshape.get(rec["village_lgd_code"]) if rec["village_lgd_code"] else None
        if rec["setting"] == "rural":
            if poly is None and rec["panchayat_lgd_code"]:
                poly = gshape.get(rec["panchayat_lgd_code"])
            if poly is not None:
                rec["_point"], rec["_poly"] = poly.representative_point(), poly
        elif rec["_point"] is None and poly is not None:
            # A municipal village: inside the ward that holds most of it.
            ward = max(wards, key=lambda w: w[1].intersection(poly).area)
            inter = ward[1].intersection(poly)
            if inter.area > 0:
                rec["_point"], rec["_poly"] = inter.representative_point(), inter
            else:
                rec["_point"] = poly.representative_point()
    # Town stations between two located ones: on the line between them, in
    # proportion to their place in the numbering (stations are numbered along
    # a route), when the two are less than ~3 km apart.
    for i, rec in enumerate(out):
        if rec["setting"] != "urban" or rec["_point"] is not None:
            continue
        a = next((j for j in range(i - 1, -1, -1) if out[j]["setting"] == "urban"
                  and out[j]["_point"] is not None), None)
        b = next((j for j in range(i + 1, len(out)) if out[j]["setting"] == "urban"
                  and out[j]["_point"] is not None), None)
        if a is None or b is None:
            continue
        pa, pb = out[a]["_point"], out[b]["_point"]
        if pa.distance(pb) > 0.03:
            continue
        t = (i - a) / (b - a)
        q = Point(pa.x + t * (pb.x - pa.x), pa.y + t * (pb.y - pa.y))
        if in_town(q):
            rec["_point"] = q
            rec["locate_method"] = f"sequence:between PS {out[a]['ps_number']} and PS {out[b]['ps_number']}"
            rec["locate_conf"] = 0.25
    # Town stations still unplaced: a walk over the wards. Starting from the
    # ward of the last placed station, each ward takes stations up to the
    # town's average (municipal stations / 36 wards), then the walk moves to
    # the nearest ward still below it. Stations are numbered along a route,
    # so neighbours in the numbering end up in neighbouring wards.
    urban = [rec for rec in out if rec["setting"] == "urban"]
    target = math.ceil(len(urban) / len(wards))
    count = {n: 0 for n, _ in wards}
    for rec in urban:
        if rec["_point"] is not None:
            count[ward_at(rec["_point"])] += 1
    current = None
    for rec in urban:
        if rec["_point"] is not None:
            current = ward_at(rec["_point"])
            continue
        if current is None or count[current] >= target:
            here = ward_poly[current].centroid if current is not None else wards[0][1].centroid
            open_wards = [n for n, _ in wards if count[n] < target] or [n for n, _ in wards]
            current = min(open_wards, key=lambda n: ward_poly[n].centroid.distance(here))
        count[current] += 1
        rec["_point"], rec["_poly"] = ward_poly[current].representative_point(), ward_poly[current]
        rec["locate_method"], rec["locate_conf"] = "sequence:ward walk from the last placed station", 0.15
    # Stations still without a point borrow the nearest placed station of the same setting.
    for i, rec in enumerate(out):
        if rec["_point"] is not None:
            continue
        for d in range(1, len(out)):
            near = [out[j] for j in (i - d, i + d) if 0 <= j < len(out)
                    and out[j]["_point"] is not None and out[j]["setting"] == rec["setting"]
                    and not out[j]["locate_method"].startswith("sequence:near PS")]
            if near:
                o = near[0]
                rec["_point"], rec["_poly"] = o["_point"], o["_poly"]
                if not rec["locate_method"].startswith("lgd-"):
                    rec["locate_method"] = f"sequence:near PS {o['ps_number']}"
                    rec["locate_conf"] = 0.2
                if rec["setting"] == "rural" and not rec["panchayat_lgd_code"]:
                    rec["panchayat_en"], rec["panchayat_lgd_code"] = o["panchayat_en"], o["panchayat_lgd_code"]
                break

    # Pass 5: distinct points; ward and panchayat from where the point is.
    used: dict[str, int] = {}
    for rec in out:
        point, poly = rec.pop("_point"), rec.pop("_poly")
        rec.pop("_run")
        if point is None:
            rec["note"] = (rec["note"] + "; could not be placed").strip("; ")
            continue
        if rec["setting"] == "urban" and (poly is None or not in_town(poly.representative_point())):
            poly = ward_poly[ward_at(point)]
        if poly is None:
            poly = point.buffer(0.004)
        slot = f"{point.x:.5f},{point.y:.5f}"
        k = used.get(slot, 0)
        used[slot] = k + 1
        p = spread(poly, point, k)
        rec["lon"], rec["lat"] = round(p.x, 5), round(p.y, 5)
        if rec["setting"] == "urban":
            rec["ward_no"] = ward_at(p)
            rec["panchayat_en"] = rec["panchayat_lgd_code"] = ""
        elif not rec["panchayat_lgd_code"]:
            block = rec["block_name_en"].replace(" Block", "")
            codes = {v["gp_code"]: v["gp"] for v in villages if v["block"] == block and v["gp_code"]}
            code = min((c for c in codes if c in gshape), key=lambda c: gshape[c].distance(p))
            rec.update(panchayat_en=codes[code], panchayat_lgd_code=code,
                       note=(rec["note"] + "; panchayat from the point's location").strip("; "))

    if allow_network:
        NOMINATIM_CACHE.parent.mkdir(parents=True, exist_ok=True)
        NOMINATIM_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1, sort_keys=True),
                                   encoding="utf-8")
    print(f"second pass placed {second} more stations by context")
    return out


def main(argv: list[str] | None = None) -> int:
    from collections import Counter

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--geocode", action="store_true",
                    help="look up town localities missing from the cache on Nominatim")
    args = ap.parse_args(argv)
    rows = build(allow_network=args.geocode)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} stations: {dict(Counter(r['block_name_en'] for r in rows))}")
    print("located by:", dict(Counter(r["locate_method"].split(":")[0] for r in rows)))
    print("no point:", sum(1 for r in rows if r["lon"] == ""),
          "| block disagreements:", sum(1 for r in rows if "its run says" in r["note"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

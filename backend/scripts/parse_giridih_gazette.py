"""Parse 7_Giridih.pdf (District Gazette, 2022 Panchayat election results).

The gazette uses Kruti Dev 010 font encoding (ASCII-mapped Devanagari).
pdfplumber extracts the raw codepoints, giving ASCII-looking strings.

Strategy:
- Extract tables page by page using pdfplumber
- Identify section type from the 'post' column (hardcoded Kruti Dev strings)
- For block/GP filtering: parse numeric codes from constituency-ID string
- Block 10 = Pirtand, Block 11 = Giridih (AC-32)
- Use hardcoded (block_num, gp_num) → area_id lookup table
- Decode winner names best-effort via Kruti Dev → Unicode converter

Usage:
    python -m scripts.parse_giridih_gazette
    python -m scripts.parse_giridih_gazette --dry-run   # print first 30 rows per section
    python -m scripts.parse_giridih_gazette --all-blocks  # include all 13 blocks
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

import pdfplumber

# ---------------------------------------------------------------------------
# Kruti Dev → Unicode Devanagari converter (partial, covers names in gazette)
# ---------------------------------------------------------------------------

# Single-char mapping (Kruti Dev 010 / Kruti Dev Standard)
_KD_SINGLE: dict[str, str] = {
    # Independent vowels
    "v": "अ",
    "b": "इ",
    "m": "उ",
    "Å": "ऊ",
    "ß": "ए",
    # Vowel matras (combine with preceding consonant in Unicode)
    "k": "ा",
    "h": "ी",
    "q": "ु",
    "w": "ू",
    "s": "े",
    "S": "ै",
    "ks": "ो",   # handled as two-char combo below
    "kS": "ौ",  # handled as two-char combo below
    # 'f' (ि) is handled separately (reorder: precedes consonant in Kruti Dev)
    # Anusvara / chandrabindu
    "a": "ं",
    "¡": "ँ",
    # Visarga
    "%": "ः",
    # Halant (virama)
    "~": "्",
    # Nukta
    "+": "़",
    # Punctuation
    "&": "–",
    # Consonants
    "d": "क",
    "[": "ख",
    "x": "ग",
    "?k": "घ",
    "p": "च",
    "N": "छ",
    "t": "ज",
    ">k": "झ",
    "V": "ट",
    "B": "ठ",
    "M": "ड",
    "<": "ढ",
    ".k": "ण",
    "r": "त",
    "F": "थ",
    "n": "द",
    "/": "ध",
    "u": "न",
    "i": "प",
    "j": "र",
    "Q": "फ",
    "c": "ब",
    "Hk": "भ",
    "e": "म",
    "y": "ल",
    "o": "व",
    "'k": "श",
    "'": "श",
    '"': "ष",
    "l": "स",
    "g": "ह",
    # Combined conjuncts
    "{": "क्ष",
    "=": "त्र",
    "K": "ज्ञ",
    ";": "य",
    # Special
    "Ø": "क्र",
    "z": "्र",  # ra-phala (subscript ra, follows consonant)
    # Half-forms (uppercase = half/conjunct of corresponding lowercase)
    "L": "स्",
    "U": "न्",
    "H": "भ्",
    "I": "प्",
    "R": "त्",
    "X": "ग्",
    "T": "ज्",
    "C": "ब्",
    "W": "श्",
    "J": "श्र",  # श्र conjunct (common in Shri/Shreemati)
    "Z": "र्",   # standing reph (ra-virama before next consonant)
    # Additional mappings
    "?": "ञ",
    "A": "ए",
    "F": "थ",
    "G": "घ",
    "O": "ओ",
    "P": "छ",
    "Q": "फ",
    "Y": "ल्",   # half-la (for ल् conjuncts like in कौशल्या)
    "D": "ड",    # alternate ड
    "E": "म",    # alternate म (some Kruti Dev variants use uppercase)
}

# Two-char sequences to check first
_KD_TWO: dict[str, str] = {
    "vk": "आ",
    "bZ": "ई",
    "mZ": "ऊ",
    "ks": "ो",
    "kS": "ौ",
    "?k": "घ",
    ">k": "झ",
    ".k": "ण",
    "Hk": "भ",
    "'k": "श",
    "M+": "ड़",
    "<+": "ढ़",
}


def _kd_to_unicode(text: str) -> str:
    """Convert a Kruti Dev 010 encoded string to Unicode Devanagari.

    Handles the main reordering challenge: Kruti Dev places the ि (i-matra,
    chr 0x66 = 'f') BEFORE the consonant it modifies; Unicode places it AFTER.
    """
    if not text:
        return text

    result: list[str] = []
    i = 0
    n = len(text)
    pending_i_matra = False  # True when we've seen 'f' and must attach ि to next consonant

    while i < n:
        ch = text[i]

        # 'f' (0x66) = ि matra — needs to go AFTER the next consonant
        if ch == "f":
            pending_i_matra = True
            i += 1
            continue

        # Check two-char combos first
        two = text[i : i + 2] if i + 1 < n else ""
        if two in _KD_TWO:
            uni = _KD_TWO[two]
            if pending_i_matra:
                result.append(uni + "ि")
                pending_i_matra = False
            else:
                result.append(uni)
            i += 2
            continue

        # Single-char mapping
        if ch in _KD_SINGLE:
            uni = _KD_SINGLE[ch]
            if pending_i_matra:
                result.append(uni + "ि")
                pending_i_matra = False
            else:
                result.append(uni)
            i += 1
            continue

        # 'z' (ra-phala) — subscript ra comes AFTER previous consonant
        # already in _KD_SINGLE, handled above

        # Pass through ASCII digits, spaces, punctuation unchanged
        if pending_i_matra and (ch.isalpha() or ch.isdigit()):
            # Unresolved pending ि — attach to this char (fallback)
            result.append(ch + "ि")
            pending_i_matra = False
        else:
            if pending_i_matra:
                result.append("ि")
                pending_i_matra = False
            result.append(ch)
        i += 1

    if pending_i_matra:
        result.append("ि")

    return "".join(result)


# ---------------------------------------------------------------------------
# Known constant strings in Kruti Dev for structured fields
# ---------------------------------------------------------------------------

# Post (column 2) → seat_type
POST_TO_SEATTYPE: dict[str, str] = {
    "ftyk ifj\"kn lnL;": "ZP",
    "iapk;r lfefr lnL;": "panchayat_samiti",
    "eqf[k;k": "mukhiya",
    "xzke iapk;r lnL;": "ward",
    # partial / alternate spellings that appear in some rows
    "ftyk ifj”kn lnL;": "ZP",  # smart-quote variant
    "ftyk ifj‘kn lnL;": "ZP",
}

# Reservation (column 3)
RESERVATION_MAP: dict[str, str] = {
    "vukjf{kr": "UR",       # Unreserved / General
    "vuqlwfpr tkfr": "SC",  # Scheduled Caste
    "vuqlwfpr tutkfr": "ST",  # Scheduled Tribe
    # occasional OCR variants
    "vukjkf{kr": "UR",
    "vukj{kr": "UR",
}

# Gender (column 4)
GENDER_MAP: dict[str, str] = {
    "efgyk": "F",   # Mahila (female)
    "vU;": "M",     # Anya (other/male)
    "efgyky": "F",  # typo variant
}

# ---------------------------------------------------------------------------
# Block + GP number → area_id lookup (hardcoded from LGD data)
# ---------------------------------------------------------------------------

# Block 10 = Pirtand; ordered by GP number in the gazette
PIRTAND_GPS: dict[int, tuple[str, int]] = {
    # gp_num → (english_name, area_id / LGD code)
    1:  ("Kumharlalo",       112980),
    2:  ("Bharati Chalkari", 112972),
    3:  ("Chirki",           112975),
    4:  ("Madhuban",         112981),
    5:  ("Bandh",            112971),
    6:  ("Chilga",           112974),
    7:  ("Palganj",          112984),
    8:  ("Nawadih",          112983),
    9:  ("Bishunpur",        112973),
    10: ("Kharpoka",         112977),
    11: ("Simarkothi",       112985),
    12: ("Harladih",         112976),
    13: ("Mandaro",          112982),
    14: ("Khukhara",         112978),
    15: ("Tuiyo",            112986),
    16: ("Badgawan",         112970),
    17: ("Kudko",            112979),
}

# Block 11 = Giridih; ordered by GP number in the gazette
GIRIDIH_GPS: dict[int, tuple[str, int]] = {
    1:  ("Pahadpur",      112913),
    2:  ("Sindwariya",    112924),
    3:  ("Leda",          112908),
    4:  ("Bajto",         112893),
    5:  ("Algunda",       112891),
    6:  ("Senadoni",      112921),
    7:  ("Jitpur",        112905),
    8:  ("Badgunda Khurd",112892),
    9:  ("Berdonga",      112896),
    10: ("Palmo",         112914),
    11: ("Barahmoriya",   112894),
    12: ("Karharbari",    112906),
    13: ("Telodih",       112926),
    14: ("Khawa",         112907),
    15: ("Pindatand",     112919),
    16: ("Sikdardih",     112923),
    17: ("Parsatand",     112917),
    18: ("Maheshlundi",   112909),
    19: ("Akdoni Kala",   112889),
    20: ("Matrukha",      112911),
    21: ("Purnanagar",    112920),
    22: ("Chunjaka",      112899),
    23: ("Akdoni Khurd",  112890),
    24: ("Mangarodih",    112910),
    25: ("Patrodih",      112918),
    26: ("Udanabad",      112927),
    27: ("Mohanpur",      112912),
    28: ("Gadi Srirampur",112902),
    29: ("Jaspur",        112904),
    30: ("Phulchi",       112901),
}

# Panchayat Samiti block names in Kruti Dev → English block name
PS_BLOCK_KD: dict[str, str] = {
    "ihjVkaM+": "Pirtand",
    "fxfjMhg": "Giridih",
}

AC32_BLOCKS = {10, 11}  # 10=Pirtand, 11=Giridih

# ---------------------------------------------------------------------------
# Constituency ID parser
# ---------------------------------------------------------------------------

# Patterns observed in the gazette:
# ZP:      VII fxfjMhg&¼N½
# PS:      VII fxfjMhg@BLOCK_NUM@BLOCK_NAME_KD&¼SEAT½
# Mukhiya: VII fxfjMhg@BLOCK_NUM@GP_NUM&GP_NAME_KD
# Ward:    VII fxfjMhg@BLOCK_NUM@GP_NUM@GP_NAME_KD&¼WARD_NUM½
#          (some entries: fxfjMhg@... without the leading "VII ")

def _clean_cid(raw: str) -> str:
    """Strip page-header noise (VII, linebreaks) from constituency field."""
    if raw is None:
        return ""
    s = raw.replace("\n", " ").strip()
    # Remove stray 'VII' fragments that appear in multi-line rows
    s = re.sub(r"\bVII\b", "", s).strip()
    return s


def parse_constituency(raw: str) -> dict:
    """Parse constituency ID string. Returns dict with keys:
    seat_type_hint, block_num, gp_num, ward_num, gp_name_kd, seat_num_zp,
    block_name_kd, district.
    """
    cid = _clean_cid(raw)
    result: dict = {
        "raw": cid,
        "block_num": None,
        "gp_num": None,
        "ward_num": None,
        "gp_name_kd": None,
        "block_name_kd": None,
        "seat_num": None,
    }

    # Bracket notation ¼N½ or (N) for seat numbers
    # ¼ = 0xBC, ½ = 0xBD
    bracket_match = re.search(r"[\xbc(](\d+)[\xbd)]", cid)
    seat_in_bracket = int(bracket_match.group(1)) if bracket_match else None

    # Ward pattern: @BLOCK@GP@NAME&¼WARD½  (spaces may appear between @ and digits)
    # e.g. "fxfjMhg@11@02@flUnofj;k& ¼1½"  or "fxfjMhg@11 @06@..."
    ward_m = re.search(r"fxfjMhg@\s*(\d+)\s*@\s*(\d+)\s*@\s*([^&@\s]+)[^¼\xbc(]*[\xbc(](\d+)[\xbd)]", cid)
    if ward_m:
        result["block_num"] = int(ward_m.group(1))
        result["gp_num"] = int(ward_m.group(2))
        result["gp_name_kd"] = ward_m.group(3).strip()
        result["ward_num"] = int(ward_m.group(4))
        return result

    # Mukhiya pattern: @BLOCK@GP&NAME  (spaces may appear)
    mukh_m = re.search(r"fxfjMhg@\s*(\d+)\s*@\s*(\d+)\s*&\s*([^\s]+)", cid)
    if mukh_m:
        result["block_num"] = int(mukh_m.group(1))
        result["gp_num"] = int(mukh_m.group(2))
        result["gp_name_kd"] = mukh_m.group(3).strip()
        return result

    # PS pattern: @BLOCK@BLOCKNAME&¼SEAT½  (space before ¼ in some entries)
    ps_m = re.search(r"fxfjMhg@(\d+)@([^&@\xbc(]+)&\s*[\xbc(](\d+)[\xbd)]", cid)
    if ps_m:
        result["block_num"] = int(ps_m.group(1))
        result["block_name_kd"] = ps_m.group(2).strip()
        result["seat_num"] = int(ps_m.group(3))
        return result

    # ZP pattern: fxfjMhg&¼N½ or fxfjMhg– (N)
    zp_m = re.search(r"fxfjMhg[&–-][\xbc(](\d+)[\xbd)]", cid)
    if zp_m:
        result["seat_num"] = int(zp_m.group(1))
        return result
    # ZP alternate without brackets
    zp_m2 = re.search(r"fxfjMhg[^\d]*(\d+)", cid)
    if zp_m2 and bracket_match is None:
        result["seat_num"] = int(zp_m2.group(1))
        return result

    if bracket_match:
        result["seat_num"] = seat_in_bracket

    return result


# ---------------------------------------------------------------------------
# Row normaliser
# ---------------------------------------------------------------------------

UNOPPOSED_KD = "fufoZjks/k"  # Kruti Dev for "निर्विरोध" (unopposed)
VACANT_KD = "fjDr"           # Kruti Dev for "रिक्त" (vacant)


def _extract_name(raw: str | None) -> tuple[str, bool]:
    """Return (cleaned_name_kd, is_unopposed)."""
    if not raw:
        return "", False
    s = raw.strip()
    unopposed = UNOPPOSED_KD in s
    s = s.replace(f"({UNOPPOSED_KD})", "").replace(f"¼{UNOPPOSED_KD}½", "")
    s = s.replace(UNOPPOSED_KD, "").strip()
    return s, unopposed


def _gazette_tag(reservation: str, gender: str, unopposed: bool) -> str:
    """Build a structured tag_source string encoding gazette metadata.

    Stored in local_result.tag_source so the API can surface reservation
    category and unopposed status without a schema migration.
    Format: "gazette | {RES} | {GEN}" or "gazette | {RES} | {GEN} | unopposed"
    """
    parts = ["gazette", reservation or "UR", gender or ""]
    if unopposed:
        parts.append("unopposed")
    return " | ".join(p for p in parts if p)


def _normalise_row(cols: list[str | None], seat_type: str) -> dict | None:
    """Normalise a table row into a structured record. Returns None to skip."""
    # Different pages have different column counts (5 vs 9 columns due to OCR merges)
    # Attempt to locate name, post, reservation, gender, constituency columns
    flat = [c.strip() if c else "" for c in (cols or [])]

    if len(flat) < 4:
        return None

    # Remove entirely blank rows and block-header rows
    non_empty = [c for c in flat if c]
    if not non_empty:
        return None

    # Detect block-header rows: single non-empty cell matching प्रखण्ड pattern
    if len(non_empty) == 1:
        return None

    # For 5-col rows (ZP, mukhiya, most PS): cols = [name, post, reservation, gender, constituency]
    # For 9-col rows (ward pages): cols = [_, name, _, post, reservation, _, _, gender, constituency]
    # Identify constellation by looking for known post strings
    name_kd = post_kd = res_kd = gen_kd = cid_kd = ""

    for idx, val in enumerate(flat):
        if val in POST_TO_SEATTYPE or any(k in val for k in POST_TO_SEATTYPE):
            # Found post column; guess other columns relative to it
            post_kd = val
            # Name: column before post (or the first non-empty)
            cand_names = [f for f in flat[:idx] if f and f not in POST_TO_SEATTYPE
                         and f not in RESERVATION_MAP and f not in GENDER_MAP]
            if cand_names:
                name_kd = cand_names[-1]
            # Columns after post: reservation, gender, constituency
            after = [f for f in flat[idx + 1:] if f]
            for j, v in enumerate(after):
                if not res_kd and v in RESERVATION_MAP:
                    res_kd = v
                elif not res_kd and any(k in v for k in RESERVATION_MAP):
                    res_kd = next((k for k in RESERVATION_MAP if k in v), v)
                elif not gen_kd and v in GENDER_MAP:
                    gen_kd = v
                elif not cid_kd and ("fxfjMhg" in v or "fxfjMhg" in v):
                    cid_kd = v
                elif not cid_kd and j == len(after) - 1 and v:
                    cid_kd = v
            break

    # Fallback: first cell = name, others by position
    if not post_kd:
        if len(flat) >= 5:
            name_kd, post_kd, res_kd, gen_kd, cid_kd = flat[0], flat[1], flat[2], flat[3], flat[4]
        else:
            return None

    if not name_kd or not cid_kd:
        return None

    # Resolve post → seat_type
    st = None
    for k, v in POST_TO_SEATTYPE.items():
        if k in post_kd:
            st = v
            break
    if st is None:
        st = seat_type  # inherit current section

    # Skip vacant seats
    if VACANT_KD in name_kd:
        return None

    name_raw, unopposed = _extract_name(name_kd)
    if not name_raw:
        return None

    cid = parse_constituency(cid_kd)
    block_num = cid.get("block_num")
    gp_num = cid.get("gp_num")
    ward_num = cid.get("ward_num")
    seat_num = cid.get("seat_num")

    # Resolve area info
    area_name = ""
    area_id = None
    block_name_en = ""

    if block_num == 10:
        block_name_en = "Pirtand"
        gp_info = PIRTAND_GPS.get(gp_num)
        if gp_info:
            area_name, area_id = gp_info
    elif block_num == 11:
        block_name_en = "Giridih"
        gp_info = GIRIDIH_GPS.get(gp_num)
        if gp_info:
            area_name, area_id = gp_info
    elif block_num is not None:
        block_name_en = f"Block-{block_num}"

    # Build seat_name
    if st == "ZP":
        seat_name = f"ZP Seat {seat_num}" if seat_num else "ZP"
        area_name = area_name or "Giridih District"
    elif st == "panchayat_samiti":
        bname = PS_BLOCK_KD.get(cid.get("block_name_kd", ""), block_name_en)
        seat_name = f"PS Seat {seat_num} – {bname}" if seat_num else f"PS – {bname}"
        if not area_name:
            area_name = bname
    elif st == "mukhiya":
        seat_name = f"Mukhiya – {area_name}" if area_name else "Mukhiya"
    elif st == "ward":
        seat_name = f"Ward {ward_num} – {area_name}" if (ward_num and area_name) else f"Ward {ward_num or ''}"
    else:
        seat_name = ""

    reservation = RESERVATION_MAP.get(res_kd, res_kd or "")
    gender = GENDER_MAP.get(gen_kd, gen_kd or "")

    winner_unicode = _kd_to_unicode(name_raw)

    return {
        "seat_type": st,
        "seat_name": seat_name,
        "area_name": area_name,
        "area_id_lgd": area_id,
        "winner": winner_unicode,
        "winner_kd": name_raw,
        "runner_up": "",
        "votes": "",
        "runner_up_votes": "",
        "tagged_party": "",
        "tag_source": _gazette_tag(reservation, gender, unopposed),
        "tag_confidence": "",
        "reservation": reservation,
        "gender_category": gender,
        "unopposed": str(unopposed).lower(),
        "block_num": str(block_num) if block_num else "",
        "block_name": block_name_en,
        "gp_num": str(gp_num) if gp_num else "",
        "ward_num": str(ward_num) if ward_num else "",
        "raw_cid": cid.get("raw", ""),
    }


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

# Columns that can span 5 or 9 depending on page layout
_KNOWN_POSTS = set(POST_TO_SEATTYPE.keys())
# Detect section transitions by first cell or page header
_ZP_HEADER_TOKENS = {"ftyk ifj", "ftyk ifj\"kn"}
_PS_HEADER_TOKENS = {"iapk;r lfefr"}
_MUKHIYA_HEADER_TOKENS = {"eqf[k;k"}
_WARD_HEADER_TOKENS = {"xzke iapk;r"}


def _detect_section_from_text(text: str) -> str | None:
    """Guess section from page header text line."""
    if not text:
        return None
    # Look for table of contents / section header lines near top of page
    lines = text.split("\n")[:6]
    for line in lines:
        for tok in _WARD_HEADER_TOKENS:
            if tok in line:
                return "ward"
        for tok in _MUKHIYA_HEADER_TOKENS:
            if tok in line and "lnL;" not in line:
                return "mukhiya"
        for tok in _PS_HEADER_TOKENS:
            if tok in line:
                return "panchayat_samiti"
    return None


def parse_pdf(
    pdf_path: Path,
    all_blocks: bool = False,
    dry_run: bool = False,
) -> list[dict]:
    records: list[dict] = []
    current_section = "ZP"  # gazette starts with ZP

    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        for pg_idx, page in enumerate(pdf.pages):
            text = page.extract_text() or ""

            # Detect section transitions from page text
            new_sec = _detect_section_from_text(text)
            if new_sec:
                current_section = new_sec

            tables = page.extract_tables()
            if not tables:
                continue

            for table in tables:
                for row in table:
                    if not row or all(c is None or str(c).strip() == "" for c in row):
                        continue
                    # Skip header rows (contain 'O;fDr dk uke' etc.)
                    row_text = " ".join(c or "" for c in row)
                    if "O;fDr dk uke" in row_text or "vuqlwph" in row_text:
                        continue

                    rec = _normalise_row(list(row), current_section)
                    if rec is None:
                        continue

                    # Determine section from row itself (more reliable than page header)
                    post_col = ""
                    for cell in row:
                        if cell and any(k in str(cell) for k in _KNOWN_POSTS):
                            post_col = str(cell)
                            break
                    if post_col:
                        for k, v in POST_TO_SEATTYPE.items():
                            if k in post_col:
                                current_section = v
                                rec["seat_type"] = v
                                break

                    # Filter by block
                    bn = rec.get("block_num")
                    bn_int = int(bn) if bn else None
                    if not all_blocks:
                        # Skip rows with no block or non-AC32 blocks
                        if bn_int not in AC32_BLOCKS:
                            continue

                    records.append(rec)

    if dry_run:
        from itertools import groupby
        print(f"\nTotal records extracted: {len(records)}")
        for sect, grp in groupby(records, key=lambda r: r["seat_type"]):
            rows = list(grp)
            print(f"\n=== {sect} ({len(rows)} rows) ===")
            for r in rows[:5]:
                print(f"  {r['winner']!r:30s} | {r['seat_name']!r:40s} | "
                      f"res={r['reservation']} gen={r['gender_category']} "
                      f"block={r['block_name']} gp={r['gp_num']}")

    return records


# ---------------------------------------------------------------------------
# CSV writers
# ---------------------------------------------------------------------------

AC32_COLS = [
    "seat_type", "seat_name", "area_name",
    "winner", "runner_up", "votes", "runner_up_votes",
    "tagged_party", "tag_source", "tag_confidence",
]

ALL_COLS = AC32_COLS + [
    "reservation", "gender_category", "unopposed",
    "block_num", "block_name", "gp_num", "ward_num",
    "area_id_lgd", "winner_kd", "raw_cid",
]


def write_csvs(records: list[dict], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    ac32 = [r for r in records if r.get("block_num") in ("10", "11")]
    all_recs = records

    ac32_path = out_dir / "gazette_results_ac32.csv"
    all_path = out_dir / "gazette_results_all.csv"

    with ac32_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=AC32_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(ac32)

    with all_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=ALL_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_recs)

    print(f"\nWrote {len(ac32)} AC-32 rows → {ac32_path}")
    print(f"Wrote {len(all_recs)} total rows → {all_path}")

    # Row count summary
    from collections import Counter
    ac32_counts = Counter(r["seat_type"] for r in ac32)
    print("\nAC-32 row counts by seat_type:")
    for st, cnt in sorted(ac32_counts.items()):
        print(f"  {st:20s}: {cnt}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Parse Giridih District Gazette PDF")
    ap.add_argument("--pdf", default="../data_giridih/7_Giridih.pdf",
                    metavar="PATH", help="Path to gazette PDF")
    ap.add_argument("--out", default="../data_giridih",
                    metavar="DIR", help="Output directory for CSVs")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print first 5 rows per section, no files written")
    ap.add_argument("--all-blocks", action="store_true",
                    help="Include all 13 blocks (default: AC-32 only)")
    args = ap.parse_args(argv)

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"ERROR: PDF not found at {pdf_path}", file=sys.stderr)
        return 1

    print(f"Parsing {pdf_path} …")
    records = parse_pdf(pdf_path, all_blocks=args.all_blocks, dry_run=args.dry_run)

    if not args.dry_run:
        write_csvs(records, Path(args.out))

    return 0


if __name__ == "__main__":
    sys.exit(main())

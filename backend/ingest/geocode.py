"""Geocode booths for the map (LLD 4.5).

Nominatim (OpenStreetMap), free, rate-limited to one request per second by
policy - so a full pass over ~350 booths takes about six minutes and is run
once, not on a schedule. Anything Nominatim cannot place falls back to the
centroid of its area polygon, and anything still unplaced gets a low
geocode_conf so the admin pin-drop UI can surface it.

    python -m ingest.geocode --limit 50
    python -m ingest.geocode --pin B0042 --lat 24.1854 --lon 86.3094
"""

from __future__ import annotations

import argparse
import sys
import time

from common.config import get_settings
from common.db import execute, query, query_one
from common.jobs import job_context
from common.logging_setup import get_logger
from common.textnorm import normalize_text

log = get_logger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Giridih district bounding box, used to reject wild matches. Nominatim will
# happily return a same-named village in another state otherwise.
BBOX = {"min_lat": 23.9, "max_lat": 24.8, "min_lon": 85.8, "max_lon": 86.6}


def in_district(lat: float, lon: float) -> bool:
    return (BBOX["min_lat"] <= lat <= BBOX["max_lat"]
            and BBOX["min_lon"] <= lon <= BBOX["max_lon"])


def geocode_one(building: str, village: str, area: str) -> tuple[float, float, float] | None:
    """(lat, lon, confidence) or None."""
    import httpx

    settings = get_settings()
    parts = [normalize_text(building), normalize_text(village), normalize_text(area),
             "Giridih", "Jharkhand", "India"]
    query_text = ", ".join(p for p in parts if p)

    try:
        response = httpx.get(
            NOMINATIM_URL,
            params={"q": query_text, "format": "json", "limit": 1, "countrycodes": "in"},
            headers={"User-Agent": settings.nominatim_user_agent},
            timeout=20,
        )
        response.raise_for_status()
        results = response.json()
    except Exception as exc:
        log.warning("nominatim failed for %r: %s", query_text[:60], exc)
        return None

    if not results:
        return None
    hit = results[0]
    lat, lon = float(hit["lat"]), float(hit["lon"])
    if not in_district(lat, lon):
        log.info("rejected out-of-district match for %r (%.4f, %.4f)", query_text[:50], lat, lon)
        return None

    # Nominatim's importance is a rough proxy for match quality; a village-level
    # hit for a school name is a weak match however confident it sounds.
    importance = float(hit.get("importance") or 0.3)
    place_type = hit.get("type", "")
    confidence = min(0.9, 0.4 + importance)
    if place_type in {"village", "town", "city", "suburb"}:
        confidence = min(confidence, 0.6)   # the village, not the building
    return lat, lon, round(confidence, 2)


def area_centroid_fallback() -> int:
    """Place any remaining booth at its area's stored centroid.

    N4: this read `ST_Centroid(a.geom)`. The centroid is now a stored pair of
    columns, because computing it was the only thing the polygon was read for.

    Worth knowing before relying on the number this returns: nothing populates
    area boundaries or centroids yet - no loader writes them - so this fallback
    has never placed a booth and will keep returning 0 until area geometry is
    loaded. That was equally true of the PostGIS version, where
    `a.geom IS NOT NULL` was never satisfied; the rewrite did not break it, it
    made the emptiness visible.
    """
    return execute(
        "UPDATE booth b SET lon = a.centroid_lon, lat = a.centroid_lat, "
        "geocode_conf = 0.2, geocode_source = 'area_centroid' FROM area a "
        "WHERE a.area_id = b.area_id AND b.lon IS NULL "
        "  AND a.centroid_lon IS NOT NULL"
    )


def run(limit: int | None = None, redo_weak: bool = False) -> dict:
    settings = get_settings()
    delay = 1.0 / max(0.1, settings.nominatim_rps)

    clause = ("b.lon IS NULL" if not redo_weak
              else "(b.lon IS NULL OR b.geocode_conf < 0.4)")
    rows = query(
        f"SELECT b.booth_uid, b.building, b.village_or_locality, a.name_en AS area "
        f"FROM booth b JOIN area a ON a.area_id = b.area_id "
        f"WHERE b.is_active AND {clause} ORDER BY b.booth_uid" + (" LIMIT %s" if limit else ""),
        (limit,) if limit else None,
    )

    stats = {"attempted": len(rows), "located": 0, "missed": 0, "centroid_fallback": 0}
    for row in rows:
        hit = geocode_one(row["building"] or "", row["village_or_locality"] or "", row["area"] or "")
        if hit:
            lat, lon, conf = hit
            execute(
                "UPDATE booth SET lon = %s, lat = %s, "
                "geocode_conf = %s, geocode_source = 'nominatim' WHERE booth_uid = %s",
                (lon, lat, conf, row["booth_uid"]),
            )
            stats["located"] += 1
        else:
            stats["missed"] += 1
        time.sleep(delay)

    stats["centroid_fallback"] = area_centroid_fallback()
    return stats


def pin(booth_uid: str, lat: float, lon: float) -> bool:
    """Manual pin-drop. Confidence 1.0, so the job will not overwrite it."""
    if query_one("SELECT 1 AS ok FROM booth WHERE booth_uid = %s", (booth_uid,)) is None:
        return False
    execute(
        "UPDATE booth SET lon = %s, lat = %s, "
        "geocode_conf = 1.0, geocode_source = 'manual' WHERE booth_uid = %s",
        (lon, lat, booth_uid),
    )
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Geocode booths")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--redo-weak", action="store_true", help="retry booths below 0.4 confidence")
    ap.add_argument("--pin", metavar="BOOTH_UID", help="set one booth by hand")
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    args = ap.parse_args(argv)

    if args.pin:
        if args.lat is None or args.lon is None:
            ap.error("--pin needs --lat and --lon")
        ok = pin(args.pin, args.lat, args.lon)
        log.info("pinned %s" if ok else "no booth %s", args.pin)
        return 0 if ok else 2

    with job_context("ingest.geocode") as job:
        stats = run(args.limit, args.redo_weak)
        job.set(**stats)
        job.log_line(
            f"{stats['located']} located, {stats['missed']} not found, "
            f"{stats['centroid_fallback']} placed at their area centroid"
        )
        if stats["missed"]:
            log.warning("%d booth(s) could not be geocoded - pin them by hand in "
                        "the admin view, or with --pin", stats["missed"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

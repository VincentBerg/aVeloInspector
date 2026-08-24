"""Convert an EngagementHQ map-marker export into data/suggested_stations.json,
keeping ONLY the àVélo (vélo-partage) suggestions and dropping every other
category (RTC autobus, STAC, FlexiBus, tramway, work zones, bus stops, …).

    python import_suggestions.py markers.json
    python import_suggestions.py page1.json page2.json ...      # paginated export
    python import_suggestions.py markers.json --category 7674    # override category id

Where to get markers.json
-------------------------
The source is RTC's "Voie libre" consultation map
(https://www.voielibre.rtcquebec.ca/carte-interactive/places/carte-interactive-de-quebec).
A committed full dump lives at data/voielibre_markers.json, so normally you can
just re-run this script on it. To refresh the dump from the live map:

  1. GET the map page above and pull the anonymous bearer token out of the HTML
     ("token":"<jwt>" under anonymousUser).
  2. Page through the markers API with that token as `Authorization: Bearer`:
         /api/v2/projects/28756/maps/3450/markers?page=N&per_page=30
     Paginate until links.next is null (~107 pages, ~3200 markers across all
     categories). NOTE: page=71 at per_page=30 returns HTTP 500 (a server-side
     offset bug); re-fetch that offset window at per_page=15 (pages 141-142).
  3. Concatenate the `data` arrays into one JSON list and feed it here.

This script accepts a JSON:API document ({"data": [...]}) or a plain list of
markers, and concatenates multiple files.

The àVélo category on that map is id 7674 (icon "bicycle", colour #1ccabd);
of ~3200 total markers, ~2920 are àVélo.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
OUT_PATH = os.path.join(DATA_DIR, "suggested_stations.json")

# How the àVélo (vélo-partage) category is identified on the consultation map.
AVELO_CATEGORY_ID = "7674"
AVELO_ICON = "bicycle"
AVELO_NAME_HINT = "vélo"  # matches "àVélo (vélo-partage)" case-insensitively

LAT_KEYS = ("lat", "latitude")
LON_KEYS = ("lng", "lon", "long", "longitude")


def _first_number(obj: dict, keys) -> float | None:
    """Return the first present key parsed as a float (markers store them as strings)."""
    for k in keys:
        if k in obj and obj[k] not in (None, ""):
            try:
                return float(obj[k])
            except (TypeError, ValueError):
                pass
    return None


def _markers_and_categories(doc):
    """Normalise a loaded export into (list_of_markers, {category_id: category_meta}).

    Handles JSON:API ({"data": [...], "included": [...]}) and a bare list.
    """
    if isinstance(doc, list):
        return doc, {}
    if not isinstance(doc, dict):
        return [], {}
    markers = doc.get("data", doc.get("markers", []))
    if isinstance(markers, dict):
        markers = [markers]
    categories = {}
    for inc in doc.get("included", []):
        if str(inc.get("type", "")).replace("-", "_").startswith("marker_categor"):
            categories[str(inc.get("id"))] = inc.get("attributes", inc)
    return markers, categories


def _category_id(marker: dict) -> str | None:
    """Pull the category id from JSON:API relationships or flat attributes."""
    rel = marker.get("relationships", {})
    for key in ("marker_category", "marker-category", "category"):
        node = rel.get(key, {})
        data = node.get("data") if isinstance(node, dict) else None
        if isinstance(data, dict) and data.get("id") is not None:
            return str(data["id"])
    attrs = marker.get("attributes", marker)
    for key in ("marker_category_id", "marker-category-id", "category_id"):
        if attrs.get(key) is not None:
            return str(attrs[key])
    return None


def _is_avelo(marker: dict, categories: dict, target_id: str) -> bool:
    cid = _category_id(marker)
    if cid is not None and cid == target_id:
        return True
    # Fallbacks if ids are absent: match the category's name/icon, wherever it lives.
    meta = categories.get(cid or "", {})
    attrs = marker.get("attributes", marker)
    blob = {**meta, **attrs}
    icon = str(blob.get("icon", "")).lower()
    name = str(blob.get("name", blob.get("category", blob.get("category_name", "")))).lower()
    return icon == AVELO_ICON or AVELO_NAME_HINT in name


def extract(paths, target_id: str = AVELO_CATEGORY_ID):
    out, skipped, no_coords = [], 0, 0
    for path in paths:
        with open(path, encoding="utf-8") as f:
            markers, categories = _markers_and_categories(json.load(f))
        for m in markers:
            if not _is_avelo(m, categories, target_id):
                skipped += 1
                continue
            attrs = m.get("attributes", m)
            lat = _first_number(attrs, LAT_KEYS)
            lon = _first_number(attrs, LON_KEYS)
            if lat is None or lon is None:
                no_coords += 1
                continue
            out.append({"lat": round(lat, 6), "lon": round(lon, 6), "category": "àVélo"})
    return out, skipped, no_coords


def main() -> int:
    ap = argparse.ArgumentParser(description="Filter an EngagementHQ marker export to àVélo only.")
    ap.add_argument("files", nargs="+", help="marker export JSON file(s)")
    ap.add_argument("--category", default=AVELO_CATEGORY_ID,
                    help=f"àVélo category id on the map (default {AVELO_CATEGORY_ID})")
    ap.add_argument("-o", "--out", default=OUT_PATH, help=f"output path (default {OUT_PATH})")
    args = ap.parse_args()

    rows, skipped, no_coords = extract(args.files, args.category)
    if not rows:
        print("No àVélo markers found. Check the category id (--category) and input file(s).",
              file=sys.stderr)
        return 1
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f"wrote {len(rows)} àVélo suggestions -> {args.out}")
    print(f"  (skipped {skipped} non-àVélo markers, {no_coords} àVélo markers missing coords)")
    print("Next: python build_db.py   # reloads the suggested_station table")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

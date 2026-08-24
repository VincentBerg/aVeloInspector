"""Build a local SQLite database from the committed history.

The git repo stores history as text (data/stations.json + data/*.jsonl). This
script rebuilds a queryable SQLite database from those files. The database is a
*derived* artifact — it is gitignored and can be regenerated at any time.

    python build_db.py            # builds stations.db from data/

Example queries once built:
    -- latest availability per station
    SELECT s.name, o.bikes, o.docks, o.ts
    FROM observation o
    JOIN station s USING (station_id)
    WHERE o.ts = (SELECT MAX(ts) FROM observation o2 WHERE o2.station_id = o.station_id);

    -- a single station's history over time
    SELECT ts, bikes, docks, status FROM observation
    WHERE station_id = '2' ORDER BY ts;
"""

from __future__ import annotations

import glob
import json
import math
import os
import sqlite3

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stations.db")


def _point_in_ring(x: float, y: float, ring: list) -> bool:
    """Ray-casting point-in-polygon test for a single linear ring."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _load_neighborhoods() -> list:
    """Return [(name, [polygons])] from data/quartiers.geojson, or [] if absent.

    Each polygon is a list of rings [outer, hole, hole, ...] following the
    GeoJSON convention. Handles both Polygon and MultiPolygon geometries.
    """
    path = os.path.join(DATA_DIR, "quartiers.geojson")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        geo = json.load(f)
    out = []
    for feature in geo["features"]:
        geom = feature["geometry"]
        if geom["type"] == "Polygon":
            polys = [geom["coordinates"]]
        elif geom["type"] == "MultiPolygon":
            polys = geom["coordinates"]
        else:
            continue
        out.append((feature["properties"]["NOM"], polys))
    return out


def _locate(lon: float, lat: float, neighborhoods: list):
    """Name of the neighborhood containing (lon, lat), or None."""
    if lon is None or lat is None:
        return None
    for name, polys in neighborhoods:
        for rings in polys:
            outer = rings[0]
            if _point_in_ring(lon, lat, outer) and not any(
                _point_in_ring(lon, lat, hole) for hole in rings[1:]
            ):
                return name
    return None


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two lat/lon points."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _nearest_station(lat: float, lon: float, stations: list):
    """(name, metres) of the closest station to (lat, lon), or (None, None)."""
    if lat is None or lon is None or not stations:
        return None, None
    best_name, best_m = None, None
    for name, slat, slon in stations:
        if slat is None or slon is None:
            continue
        d = _haversine_m(lat, lon, slat, slon)
        if best_m is None or d < best_m:
            best_name, best_m = name, d
    return best_name, best_m


def build(db_path: str = DB_PATH) -> sqlite3.Connection:
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE station (
            station_id   TEXT PRIMARY KEY,
            name         TEXT,
            lat          REAL,
            lon          REAL,
            capacity     INTEGER,
            address      TEXT,
            neighborhood TEXT
        );
        CREATE TABLE observation (
            station_id TEXT REFERENCES station(station_id),
            ts         INTEGER,
            bikes      INTEGER,
            docks      INTEGER,
            status     TEXT,
            ebikes     INTEGER,
            mechanical INTEGER
        );
        CREATE TABLE suggested_station (
            lat             REAL,
            lon             REAL,
            category        TEXT,
            neighborhood    TEXT,
            nearest_station TEXT,
            nearest_station_m INTEGER
        );
        """
    )

    # Québec City neighborhood (quartier) boundaries, loaded once and reused to
    # tag both real stations and citizen suggestions by point-in-polygon.
    neighborhoods = _load_neighborhoods()

    # Static roster. Each station is tagged with the neighborhood whose boundary
    # contains it, derived at build time from the committed data/quartiers.geojson
    # (Ville de Québec open data, CC-BY).
    station_coords = []  # (name, lat, lon) — used to measure suggestion access gaps
    stations_file = os.path.join(DATA_DIR, "stations.json")
    if os.path.exists(stations_file):
        with open(stations_file, encoding="utf-8") as f:
            static = json.load(f)
        rows = list(static.values())
        for row in rows:
            row["neighborhood"] = _locate(row.get("lon"), row.get("lat"), neighborhoods)
        station_coords = [(r["name"], r["lat"], r["lon"]) for r in rows]
        conn.executemany(
            "INSERT INTO station "
            "VALUES (:station_id,:name,:lat,:lon,:capacity,:address,:neighborhood)",
            rows,
        )

    # Citizen-suggested àVélo locations from RTC's "Voie libre" consultation
    # map (https://www.voielibre.rtcquebec.ca/). Static, not a live feed. Each
    # suggestion is also tagged with the walking distance to the nearest existing
    # station, so access gaps (demand far from any station) can be surfaced.
    suggested_file = os.path.join(DATA_DIR, "suggested_stations.json")
    if os.path.exists(suggested_file):
        with open(suggested_file, encoding="utf-8") as f:
            suggested = json.load(f)
        for row in suggested:
            row["neighborhood"] = _locate(row.get("lon"), row.get("lat"), neighborhoods)
            name, dist = _nearest_station(row.get("lat"), row.get("lon"), station_coords)
            row["nearest_station"] = name
            row["nearest_station_m"] = round(dist) if dist is not None else None
        conn.executemany(
            "INSERT INTO suggested_station "
            "(lat,lon,category,neighborhood,nearest_station,nearest_station_m) "
            "VALUES (:lat,:lon,:category,:neighborhood,:nearest_station,:nearest_station_m)",
            suggested,
        )

    # Observations from every day's change log.
    for path in sorted(glob.glob(os.path.join(DATA_DIR, "*.jsonl"))):
        with open(path, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
        conn.executemany(
            "INSERT INTO observation (station_id,ts,bikes,docks,status,ebikes,mechanical) "
            "VALUES (:station_id,:ts,:bikes,:docks,:status,:ebikes,:mechanical)",
            rows,
        )

    conn.execute("CREATE INDEX idx_obs_station_ts ON observation (station_id, ts)")
    conn.commit()
    return conn


if __name__ == "__main__":
    conn = build()
    n_stations = conn.execute("SELECT COUNT(*) FROM station").fetchone()[0]
    n_obs = conn.execute("SELECT COUNT(*) FROM observation").fetchone()[0]
    print(f"built {DB_PATH}: {n_stations} stations, {n_obs} observations\n")

    print("stations with the most recorded changes:")
    rows = conn.execute(
        """
        SELECT s.name, COUNT(*) AS changes
        FROM observation o JOIN station s USING (station_id)
        GROUP BY o.station_id ORDER BY changes DESC LIMIT 5
        """
    ).fetchall()
    for name, changes in rows:
        print(f"  {changes:>4}  {name}")
    conn.close()

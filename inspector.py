"""Fetcher for the àVélo Québec GBFS feeds.

àVélo's public bikeshare data is served as a standard GBFS feed by the
operator (PBSC). No API key or auth is required.

Feed index:
    https://quebec.publicbikesystem.net/customer/ube/gbfs/v1/gbfs.json

The two feeds we care about for station locations/availability:
    {lang}/station_information  -> static: id, name, lat, lon, capacity, address
    {lang}/station_status       -> live:   bikes/docks available, status, last_reported

Uses only the standard library (urllib), so it runs with no extra installs.
"""

from __future__ import annotations

import json
import math
import urllib.request
import urllib.error
from dataclasses import dataclass

BASE_URL = "https://quebec.publicbikesystem.net/customer/ube/gbfs/v1/"
USER_AGENT = "aVeloInspector/0.1 (+https://aveloquebec.ca)"


@dataclass
class Station:
    """A station with its static info merged with live status."""

    station_id: str
    name: str
    lat: float
    lon: float
    capacity: int
    address: str
    status: str
    bikes_available: int
    docks_available: int
    last_reported: int  # unix timestamp

    def distance_to(self, lat: float, lon: float) -> float:
        """Great-circle distance in metres from (lat, lon) to this station."""
        return _haversine(self.lat, self.lon, lat, lon)


def _fetch_json(url: str, timeout: float = 10.0) -> dict:
    """GET a URL and parse the JSON body."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} fetching {url}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error fetching {url}: {e.reason}") from e


def fetch_feed(name: str, lang: str = "en") -> dict:
    """Fetch a single GBFS feed (e.g. 'station_information') and return its
    `data` payload."""
    url = f"{BASE_URL}{lang}/{name}"
    return _fetch_json(url)["data"]


def list_feeds(lang: str = "en") -> dict[str, str]:
    """Return {feed_name: url} from the GBFS discovery document."""
    index = _fetch_json(f"{BASE_URL}gbfs.json")
    feeds = index["data"][lang]["feeds"]
    return {f["name"]: f["url"] for f in feeds}


def fetch_stations(lang: str = "en", include_out_of_service: bool = False) -> list[Station]:
    """Fetch and merge station_information + station_status into Station objects."""
    information = {s["station_id"]: s for s in fetch_feed("station_information", lang)["stations"]}
    status = {s["station_id"]: s for s in fetch_feed("station_status", lang)["stations"]}

    stations: list[Station] = []
    for sid, info in information.items():
        st = status.get(sid)
        if st is None:
            continue  # station with no live status yet
        if not include_out_of_service and st.get("status") == "NOT_IN_SERVICE":
            continue
        stations.append(
            Station(
                station_id=sid,
                name=info["name"],
                lat=float(info["lat"]),
                lon=float(info["lon"]),
                capacity=int(info.get("capacity", 0)),
                address=info.get("address", ""),
                status=st.get("status", ""),
                bikes_available=int(st.get("num_bikes_available", 0)),
                docks_available=int(st.get("num_docks_available", 0)),
                last_reported=int(st.get("last_reported", 0)),
            )
        )
    return stations


def to_geojson(stations: list[Station]) -> dict:
    """Build a GeoJSON FeatureCollection from stations.

    Each station becomes a Point feature. Note GeoJSON uses [lon, lat] order.
    """
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [s.lon, s.lat]},
                "properties": {
                    "station_id": s.station_id,
                    "name": s.name,
                    "address": s.address,
                    "capacity": s.capacity,
                    "status": s.status,
                    "bikes_available": s.bikes_available,
                    "docks_available": s.docks_available,
                    "last_reported": s.last_reported,
                },
            }
            for s in stations
        ],
    }


def export_geojson(stations: list[Station], path: str) -> None:
    """Write stations to `path` as a GeoJSON file."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(to_geojson(stations), f, ensure_ascii=False, indent=1)


def nearest_stations(
    lat: float, lon: float, stations: list[Station], n: int = 5
) -> list[tuple[Station, float]]:
    """Return the `n` stations closest to (lat, lon), as (station, metres) pairs."""
    ranked = sorted(stations, key=lambda s: s.distance_to(lat, lon))
    return [(s, s.distance_to(lat, lon)) for s in ranked[:n]]


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two lat/lon points."""
    r = 6_371_000  # Earth radius, metres
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


if __name__ == "__main__":
    stations = fetch_stations()
    print(f"{len(stations)} stations in service\n")

    export_geojson(stations, "stations.geojson")
    print("Wrote stations.geojson")

    # Old Québec (Château Frontenac) as a demo origin.
    origin = (46.8118, -71.2055)
    print(f"Nearest stations to {origin}:")
    for station, metres in nearest_stations(*origin, stations):
        print(
            f"  {metres:6.0f} m  {station.name:<35} "
            f"{station.bikes_available:>2} bikes / {station.docks_available:>2} docks"
        )

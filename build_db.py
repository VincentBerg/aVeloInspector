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
import os
import sqlite3

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stations.db")


def build(db_path: str = DB_PATH) -> sqlite3.Connection:
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE station (
            station_id TEXT PRIMARY KEY,
            name       TEXT,
            lat        REAL,
            lon        REAL,
            capacity   INTEGER,
            address    TEXT
        );
        CREATE TABLE observation (
            station_id TEXT,
            ts         INTEGER,
            bikes      INTEGER,
            docks      INTEGER,
            status     TEXT,
            ebikes     INTEGER,
            mechanical INTEGER
        );
        """
    )

    # Static roster.
    stations_file = os.path.join(DATA_DIR, "stations.json")
    if os.path.exists(stations_file):
        with open(stations_file, encoding="utf-8") as f:
            static = json.load(f)
        conn.executemany(
            "INSERT INTO station VALUES (:station_id,:name,:lat,:lon,:capacity,:address)",
            list(static.values()),
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

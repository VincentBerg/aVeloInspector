# aVeloInspector

Tracks the [àVélo Québec](https://aveloquebec.ca/stations/) bikeshare network
over time. It reads àVélo's public **GBFS** feeds (served by the operator, PBSC
— no API key needed) and records how bike/dock availability at each station
changes, using the [git-scraping](https://simonwillison.net/2020/Oct/9/git-scraping/)
pattern: a scheduled job appends changes to text files, so the **git history is
the time-series**.

## Data source

Base feed URL:

```
https://quebec.publicbikesystem.net/customer/ube/gbfs/v1/
```

| Feed | Contents |
|------|----------|
| `gbfs.json` | feed index (lists the `fr`/`en` feeds below) |
| `{lang}/station_information` | static: `station_id`, `name`, `lat`, `lon`, `capacity`, `address`, `groups` |
| `{lang}/station_status` | live: `num_bikes_available`, `num_docks_available`, `status`, `num_bikes_available_types`, `last_reported` |

The aggregated feed regenerates every ~30s (`ttl: 29`); individual stations
report on a ~1–2 min rhythm. Polling runs every ~5 min — enough to capture
availability trends, and the practical floor for GitHub Actions cron.

## Data layout (`data/`, committed)

Everything in `data/` is plain text so git diffs stay small and meaningful.

| File | Role |
|------|------|
| `data/stations.json` | Static roster keyed by `station_id` (`name`, `lat`, `lon`, `capacity`, `address`). Rewritten **only when it changes**, so its git history records station additions / renames / relocations. |
| `data/state.json` | Last-known dynamic state per station. Lets each stateless poll diff against the previous run. Not for analysis — just bookkeeping. |
| `data/YYYY-MM-DD.jsonl` | Append-only **change log**, one UTC day per file. One JSON line per station *whenever its state changes*. Quiet periods (e.g. overnight) add no lines. |

Each line in a `*.jsonl` file:

```json
{"ts": 1782267909, "station_id": "2", "bikes": 19, "docks": 4, "status": "IN_SERVICE", "ebikes": 19, "mechanical": 0}
```

- `ts` — poll time, Unix seconds.
- `bikes` / `docks` — available bikes / open docks.
- `ebikes` / `mechanical` — breakdown of `bikes` (àVélo is essentially all-electric).
- `status` — `IN_SERVICE`, `MAINTENANCE`, or `PLANNED` (`NOT_IN_SERVICE` is filtered out).

## Components

| File | What it does |
|------|--------------|
| `inspector.py` | Library: fetch + merge the feeds into `Station` objects; GeoJSON export; nearest-station search. Run directly to write a current `stations.geojson` and print nearest stations. |
| `poll.py` | One poll cycle: fetch, update `data/stations.json` if changed, append changed rows to today's `.jsonl`, refresh `data/state.json`. |
| `build_db.py` | Rebuilds a local **SQLite** `stations.db` from `data/`. |
| `.github/workflows/poll.yml` | Runs `poll.py` every ~5 min and commits any changes. |

## Usage

```bash
python poll.py        # one poll cycle → updates data/
python build_db.py    # rebuild stations.db from data/ (gitignored)
python inspector.py   # write stations.geojson + print nearest stations
```

Polling runs automatically via GitHub Actions; you don't need to run `poll.py`
yourself. `stations.db`, `stations.geojson`, and `.venv/` are gitignored —
they're derived artifacts, rebuildable from `data/` at any time.

## Querying the history

`build_db.py` creates two tables: `station` (static info) and `observation`
(the change log), indexed on `(station_id, ts)`.

```sql
-- a single station's availability over time
SELECT ts, bikes, docks, status
FROM observation
WHERE station_id = '2'
ORDER BY ts;

-- latest availability for every station
SELECT s.name, o.bikes, o.docks, datetime(o.ts, 'unixepoch') AS at
FROM observation o
JOIN station s USING (station_id)
WHERE o.ts = (SELECT MAX(ts) FROM observation o2 WHERE o2.station_id = o.station_id);
```

Because the JSONL is a *change* log, a station's availability between two
recorded rows is whatever the earlier row says (it's unchanged until the next
row). Carry the last value forward when reconstructing a continuous series.

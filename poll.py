"""Poll the àVélo feeds and append changes to a git-friendly history.

Designed for the "git scraping" pattern: run on a schedule (GitHub Actions),
commit the resulting text files, and let git history be the time-series.

Each run:
  1. Fetches current stations (inspector.fetch_stations).
  2. Rewrites data/stations.json only if the static roster/info changed.
  3. Diffs each station's dynamic state against data/state.json and appends a
     line per *changed* station to data/<UTC-date>.jsonl.
  4. Rewrites data/state.json with the current states.

Pure standard library — no extra installs.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

import inspector

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
STATIONS_FILE = os.path.join(DATA_DIR, "stations.json")
STATE_FILE = os.path.join(DATA_DIR, "state.json")


def _load_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        # Corrupt/half-written file (e.g. leftover merge markers). Don't crash
        # the long-lived poller — fall back to the default and let this run
        # rewrite a clean file. For state.json this re-logs one full snapshot.
        print(f"warning: {path} is not valid JSON; ignoring it")
        return default


def _dump_json(path: str, obj) -> None:
    """Write JSON deterministically (sorted keys) so unchanged data → no diff."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")


def poll() -> int:
    """Run one poll cycle. Returns the number of changed rows written."""
    os.makedirs(DATA_DIR, exist_ok=True)
    stations = inspector.fetch_stations()
    ts = int(time.time())

    # 1. Static roster — only rewrite when it actually changes.
    static = {s.station_id: s.static_info() for s in stations}
    if static != _load_json(STATIONS_FILE, None):
        _dump_json(STATIONS_FILE, static)

    # 2. Diff dynamic state and append changed rows to today's log.
    prev_state = _load_json(STATE_FILE, {})
    new_state = {}
    changed = []
    for s in stations:
        state = s.dynamic_state()
        new_state[s.station_id] = state
        if prev_state.get(s.station_id) != state:
            changed.append((s.station_id, state))

    if changed:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_path = os.path.join(DATA_DIR, f"{date}.jsonl")
        with open(log_path, "a", encoding="utf-8") as f:
            for sid, state in changed:
                row = {"ts": ts, "station_id": sid, **state}
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # 3. Persist current state for the next run to diff against.
    _dump_json(STATE_FILE, new_state)
    return len(changed)


if __name__ == "__main__":
    n = poll()
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"wrote {n} changed rows to data/{date}.jsonl")

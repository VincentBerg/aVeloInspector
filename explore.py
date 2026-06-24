"""Rebuild the SQLite DB from data/ and launch Datasette to explore it.

    python explore.py            # builds stations.db, opens Datasette at :8001

Requires the analysis extras:  pip install datasette datasette-vega
The "Hottest stations" and other views are predefined in metadata.yaml.
"""

from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    import build_db

    build_db.build()
    print("Launching Datasette — open http://127.0.0.1:8001/stations\n")
    return subprocess.call(
        [
            sys.executable, "-m", "datasette",
            os.path.join(HERE, "stations.db"),
            "-m", os.path.join(HERE, "metadata.yaml"),
            "--port", "8001",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())

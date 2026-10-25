#!/usr/bin/env python3
"""Query the silver tables with SQL from the command line.

    python scripts/silver_sql.py                       # coverage per event
    python scripts/silver_sql.py "select * from matches limit 5"
    python scripts/silver_sql.py "select card, sum(qty) n from decklist_cards
                                  where section='main' group by 1 order by n desc limit 20"

Each Parquet file under data/silver/melee/ is available as a view named after
the table: events, matches, decklists, decklist_cards, players.
"""

from __future__ import annotations

import sys

import duckdb

from mtgmeta.config import Settings
from mtgmeta.silver.melee import TABLES, silver_root

DEFAULT = """
select substr(start, 1, 10) as start, tournament_id, players_estimate as players,
       swiss_matches as swiss, swiss_both_known as known,
       round(100.0 * swiss_both_known / nullif(swiss_matches, 0)) as pct,
       decklist_ids as lists, unusable_decklists as empty, substr(name, 1, 48) as name
from events order by start
"""


def main() -> int:
    sql = " ".join(sys.argv[1:]) or DEFAULT
    root = silver_root(Settings.from_env().data_root)
    con = duckdb.connect()
    for table in TABLES:
        path = root / f"{table}.parquet"
        if path.exists():
            con.execute(f"create view {table} as select * from '{path.as_posix()}'")
    if not con.execute("select count(*) from duckdb_views() where internal = false").fetchone()[0]:
        print(f"no silver tables under {root}; run build_silver.py or pull them from R2")
        return 1
    con.execute("set max_rows = 500")
    print(con.sql(sql))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

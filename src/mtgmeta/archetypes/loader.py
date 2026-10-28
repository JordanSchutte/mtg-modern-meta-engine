"""Load main decks from silver for classification and mining."""

from __future__ import annotations

from pathlib import Path

import duckdb

from mtgmeta.silver.melee import silver_root


def load_main_decks(data_root: Path, fmt: str = "Modern", since: str | None = None,
                    ) -> dict[str, list[tuple[int, str]]]:
    """``{decklist_id: [(qty, card), ...]}`` for usable lists of the format.

    A list's format and first event come from the matches that reference it;
    ``since`` filters on that event's start date.
    """
    root = silver_root(data_root)
    con = duckdb.connect()
    sql = f"""
        select c.decklist_id, c.qty, c.card
        from '{(root / "decklist_cards.parquet").as_posix()}' c
        join '{(root / "decklists.parquet").as_posix()}' d using (decklist_id)
        join '{(root / "events.parquet").as_posix()}' e
          on e.tournament_id = d.first_tournament_id
        where c.section = 'main' and d.usable and d.format = ?
          {"and e.start >= ?" if since else ""}
        order by c.decklist_id
    """
    params = [fmt] + ([since] if since else [])
    rows = con.execute(sql, params).fetchall()
    con.close()
    decks: dict[str, list[tuple[int, str]]] = {}
    for did, qty, card in rows:
        decks.setdefault(did, []).append((int(qty), card))
    return decks

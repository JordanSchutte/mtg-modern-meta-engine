"""Load main decks from silver for classification and mining."""

from __future__ import annotations

from pathlib import Path

import duckdb

from mtgmeta.silver.melee import silver_root
from mtgmeta.silver.mtgo import silver_root as mtgo_root


def load_main_decks(data_root: Path, fmt: str = "Modern", since: str | None = None,
                    source: str = "melee") -> dict[str, list[tuple[int, str]]]:
    """``{decklist_id: [(qty, card), ...]}`` for usable lists of the format.

    Melee: a list's format and first event come from the matches that reference
    it; ``since`` filters on that event's start date. MTGO: the list's own event.
    """
    if source == "mtgo":
        return _load_mtgo_main_decks(data_root, since)
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


def _load_mtgo_main_decks(data_root: Path, since: str | None) -> dict[str, list[tuple[int, str]]]:
    root = mtgo_root(data_root)
    if not (root / "decklist_cards.parquet").exists():
        return {}
    con = duckdb.connect()
    rows = con.execute(f"""
        select c.decklist_id, c.qty, c.card
        from '{(root / "decklist_cards.parquet").as_posix()}' c
        join '{(root / "decklists.parquet").as_posix()}' d using (decklist_id)
        where c.section = 'main' and d.usable {"and d.day >= ?" if since else ""}
        order by c.decklist_id
    """, [since] if since else []).fetchall()
    con.close()
    decks: dict[str, list[tuple[int, str]]] = {}
    for did, qty, card in rows:
        decks.setdefault(did, []).append((int(qty), card))
    return decks


def load_deck_dates(data_root: Path, fmt: str = "Modern", source: str = "melee",
                    ) -> dict[str, str]:
    """``{decklist_id: 'YYYY-MM-DD'}`` — the date of the list's (first) event."""
    if source == "mtgo":
        root = mtgo_root(data_root)
        if not (root / "decklists.parquet").exists():
            return {}
        con = duckdb.connect()
        path = (root / "decklists.parquet").as_posix()
        rows = con.execute(f"select decklist_id, day from '{path}' where usable").fetchall()
        con.close()
        return dict(rows)
    root = silver_root(data_root)
    con = duckdb.connect()
    rows = con.execute(f"""
        select d.decklist_id, substr(e.start, 1, 10)
        from '{(root / "decklists.parquet").as_posix()}' d
        join '{(root / "events.parquet").as_posix()}' e on e.tournament_id = d.first_tournament_id
        where d.usable and d.format = ?
    """, [fmt]).fetchall()
    con.close()
    return {did: day for did, day in rows if day}

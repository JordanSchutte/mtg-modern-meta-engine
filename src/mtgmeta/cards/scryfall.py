"""Scryfall's oracle-cards bulk file -> ``silver/cards.parquet``.

One row per card (oracle identity), with the fields the archetype work needs:
colors, type line, mana value, and whether it's a land. Names are stored both
as Scryfall prints them (``Sink into Stupor // Soporific Springs``) and as the
front face alone, because decklists use either.

Scryfall asks bulk users to download at most once a day; we keep the file for
``MAX_AGE`` and let the watcher refresh it.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

log = logging.getLogger(__name__)

BULK_INDEX = "https://api.scryfall.com/bulk-data/oracle-cards"
MAX_AGE = timedelta(days=7)
_UA = "mtg-modern-meta-engine (personal research)"


def cards_path(data_root: Path) -> Path:
    return data_root / "silver" / "cards.parquet"


def normalize(name: str) -> str:
    """Card names as a join key: front face, lowercase, no punctuation noise."""
    front = name.split("//")[0]
    return re.sub(r"[^a-z0-9]+", " ", front.lower()).strip()


def rows_from_bulk(cards: list[dict]) -> list[dict]:
    out = []
    for c in cards:
        if c.get("layout") in {"token", "double_faced_token", "emblem", "art_series"}:
            continue
        faces = c.get("card_faces") or []
        colors = c.get("colors")
        if colors is None and faces:
            colors = sorted({x for f in faces for x in (f.get("colors") or [])})
        type_line = c.get("type_line") or (faces[0].get("type_line") if faces else "") or ""
        out.append({
            "oracle_id": c.get("oracle_id"),
            "name": c["name"],
            "front_name": c["name"].split(" // ")[0],
            "key": normalize(c["name"]),
            "colors": "".join(sorted(colors or [])),
            "color_identity": "".join(sorted(c.get("color_identity") or [])),
            "type_line": type_line,
            "mana_value": float(c.get("cmc") or 0.0),
            "is_land": type_line.split("//")[0].strip().startswith("Land")
                       or " Land" in type_line.split("//")[0],
            "legal_modern": (c.get("legalities") or {}).get("modern") == "legal",
        })
    # One row per key: prefer the Modern-legal printing's data (identical anyway).
    seen: dict[str, dict] = {}
    for r in out:
        if r["key"] not in seen or (r["legal_modern"] and not seen[r["key"]]["legal_modern"]):
            seen[r["key"]] = r
    return sorted(seen.values(), key=lambda r: r["name"])


def download(client: httpx.Client | None = None) -> list[dict]:
    client = client or httpx.Client(timeout=120, headers={"User-Agent": _UA,
                                                          "Accept": "application/json"})
    index = client.get(BULK_INDEX).raise_for_status().json()
    if isinstance(index, dict) and isinstance(index.get("data"), list):  # list wrapper
        index = next((i for i in index["data"] if i.get("type") == "oracle_cards"), {})
    uri = index.get("download_uri") or index.get("download_url") or index.get("uri")
    if not uri:
        raise RuntimeError(f"unexpected Scryfall bulk-data response: keys={sorted(index)}")
    log.info("downloading %s (%s MB)", uri, round((index.get("size") or 0) / 1e6))
    return client.get(uri, follow_redirects=True).raise_for_status().json()


def is_fresh(path: Path, now: datetime | None = None) -> bool:
    if not path.exists():
        return False
    now = now or datetime.now(UTC)
    mtime = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    return now - mtime < MAX_AGE


def update(data_root: Path, force: bool = False) -> Path:
    path = cards_path(data_root)
    if is_fresh(path) and not force:
        log.info("cards table is fresh (%s); not downloading", path)
        return path
    rows = rows_from_bulk(download())
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    pq.write_table(pa.Table.from_pylist(rows), tmp)
    tmp.replace(path)
    log.info("wrote %d cards to %s", len(rows), path)
    return path


def load(data_root: Path) -> dict[str, dict]:
    """Cards keyed by normalized name."""
    table = pq.read_table(cards_path(data_root))
    return {r["key"]: r for r in table.to_pylist()}


def write_rows(rows: list[dict], data_root: Path) -> Path:
    """For tests and offline use: write a cards table from prepared rows."""
    path = cards_path(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)
    return path

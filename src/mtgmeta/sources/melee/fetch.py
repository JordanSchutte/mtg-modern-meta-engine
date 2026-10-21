"""Fetch one completed Melee event into bronze.

Layout under ``<data_root>/bronze/melee/``::

    events/<tournament_id>/<version>/tournament.html
    events/<tournament_id>/<version>/rounds/<round_id>.json  # every DataTables page, as returned
    events/<tournament_id>/<version>/manifest.json           # what was fetched, when, counts
    decklists/<decklist_id>.html                             # shared: the same 75 is reused

``<version>`` is the fetch timestamp (``20261021T142233Z``).

Guarantees:

* **Validate before writing.** Each response is parsed (contract-checked) as it
  arrives. If anything breaks, nothing for that event is written.
* **Atomic per event.** Files are staged in a temp directory and moved into
  place only when the whole event succeeded.
* **Idempotent.** An event that already has a version is skipped; a decklist
  already in bronze is never requested again.
* **Append-only.** ``force=True`` (e.g. an organizer corrected results) writes
  a *new* version beside the old one; nothing in bronze is overwritten or deleted.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from mtgmeta import __version__

from . import BASE_URL
from .http import PoliteClient
from .parse import (
    MATCH_COLUMNS,
    check_datatables_page,
    datatables_form,
    parse_decklist_page,
    parse_round_matches,
    parse_tournament_page,
)

log = logging.getLogger(__name__)

PAGE_SIZE = 100


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def bronze_root(data_root: Path) -> Path:
    return data_root / "bronze" / "melee"


def latest_version(event_root: Path) -> Path | None:
    """Newest complete version of an event (one with a manifest), if any."""
    if not event_root.exists():
        return None
    versions = sorted(p for p in event_root.iterdir() if (p / "manifest.json").exists())
    return versions[-1] if versions else None


def fetch_event(
    tournament_id: int,
    client: PoliteClient,
    data_root: Path,
    force: bool = False,
    fetch_decklists: bool = True,
) -> dict:
    root = bronze_root(data_root)
    event_root = root / "events" / str(tournament_id)
    deck_dir = root / "decklists"
    existing = latest_version(event_root)
    if existing is not None and not force:
        log.info("event %s already in bronze (%s); skipping", tournament_id, existing.name)
        return json.loads((existing / "manifest.json").read_text())

    referer = f"{BASE_URL}/Tournament/View/{tournament_id}"
    started = datetime.now(UTC)
    version = started.strftime("%Y%m%dT%H%M%SZ")
    requests_before = client.requests_made

    with tempfile.TemporaryDirectory(prefix=f"melee-{tournament_id}-") as tmp:
        stage = Path(tmp) / "event"
        (stage / "rounds").mkdir(parents=True)

        page = client.get_text(f"/Tournament/View/{tournament_id}")
        meta = parse_tournament_page(page, tournament_id)
        (stage / "tournament.html").write_text(page, encoding="utf-8")

        decklist_ids: set[str] = set()
        rounds_summary = []
        for rnd in meta.rounds:
            pages: list[dict] = []
            start = 0
            while True:
                form = datatables_form(MATCH_COLUMNS, start, PAGE_SIZE)
                payload = client.post_form_json(f"/Match/GetRoundMatches/{rnd.id}", form, referer)
                total, data = check_datatables_page(payload)
                pages.append(payload)
                start += PAGE_SIZE
                if not data or start >= total:
                    break
            matches = parse_round_matches(pages)
            (stage / "rounds" / f"{rnd.id}.json").write_text(json.dumps(pages), encoding="utf-8")
            for m in matches:
                decklist_ids.update(c.decklist_id for c in m.competitors if c.decklist_id)
            rounds_summary.append({
                "round_id": rnd.id,
                "name": rnd.name,
                "is_swiss": rnd.is_swiss,
                "matches": len(matches),
                "both_decks_known": sum(m.both_decks_known for m in matches),
                "formats": sorted({m.format for m in matches if m.format}),
            })

        # Decklists are shared across events (players reuse an exact 75).
        new_decks: dict[str, str] = {}
        if fetch_decklists:
            for did in sorted(decklist_ids):
                if (deck_dir / f"{did}.html").exists():
                    continue
                deck_page = client.get_text(f"/Decklist/View/{did}", referer=referer)
                parse_decklist_page(deck_page, did)  # contract check before keeping it
                new_decks[did] = deck_page

        manifest = {
            "source": "melee",
            "tournament_id": tournament_id,
            "name": meta.name,
            "start": meta.start,
            "version": version,
            "fetched_at": started.isoformat(),
            "fetcher_version": __version__,
            "requests": client.requests_made - requests_before,
            "rounds": rounds_summary,
            "decklist_ids": sorted(decklist_ids),
            "tournament_html_sha256": _sha256(page),
        }
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        # Everything validated: publish to bronze.
        deck_dir.mkdir(parents=True, exist_ok=True)
        for did, text in new_decks.items():
            (deck_dir / f"{did}.html").write_text(text, encoding="utf-8")
        event_root.mkdir(parents=True, exist_ok=True)
        shutil.move(str(stage), str(event_root / version))

    log.info("event %s: %d rounds, %d decklists (%d new), %d requests",
             tournament_id, len(meta.rounds), len(decklist_ids), len(new_decks),
             manifest["requests"])
    return manifest

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
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mtgmeta import __version__

from . import BASE_URL
from .http import PoliteClient
from .parse import (
    MATCH_COLUMNS,
    UnusableDecklistError,
    check_datatables_page,
    datatables_form,
    parse_decklist_page,
    parse_round_matches,
    parse_tournament_page,
)

log = logging.getLogger(__name__)

PAGE_SIZE = 100


class TooSmallError(Exception):
    """The event has fewer players than asked for; nothing was written.

    Decided from round 1's match count (players ~ 2 x matches, byes aside)
    after two requests, so settling a small watchlist event is cheap.
    """

    def __init__(self, tournament_id: int, players_estimate: int, min_players: int) -> None:
        self.tournament_id = tournament_id
        self.players_estimate = players_estimate
        super().__init__(f"event {tournament_id}: about {players_estimate} players, "
                         f"below the {min_players} minimum")


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


def matches_signature(matches) -> str:
    """Order-independent fingerprint of what the matrix cares about: who played
    whom, with which lists, and the result. Timestamps and display fields are
    left out so a refresh only sees real changes."""
    rows = sorted(
        (m.match_guid, m.round_id, m.result, m.winner_player_id, m.game_draws,
         tuple(sorted((c.player_id, c.decklist_id, c.game_wins) for c in m.competitors)))
        for m in matches
    )
    return _sha256(json.dumps(rows, default=str))


def stored_signature(version_dir: Path) -> str:
    """Signature of a stored version, from its round pages."""
    matches = []
    for f in sorted((version_dir / "rounds").glob("*.json")):
        matches.extend(parse_round_matches(json.loads(f.read_text(encoding="utf-8"))))
    return matches_signature(matches)


def last_refresh_at(event_root: Path) -> datetime | None:
    path = event_root / "refreshes.json"
    if not path.exists():
        return None
    history = json.loads(path.read_text(encoding="utf-8"))
    return datetime.fromisoformat(history[-1]["checked_at"]) if history else None


def events_to_refresh(data_root: Path, days: float, now: datetime | None = None,
                      min_gap: timedelta = timedelta(hours=20)) -> list[int]:
    """Events in bronze that started within ``days`` and weren't checked in the last ``min_gap``."""
    now = now or datetime.now(UTC)
    out = []
    events_dir = bronze_root(data_root) / "events"
    if not events_dir.exists():
        return out
    for event_root in sorted(events_dir.iterdir()):
        version = latest_version(event_root)
        if version is None:
            continue
        manifest = json.loads((version / "manifest.json").read_text(encoding="utf-8"))
        start_text = manifest.get("start") or manifest["fetched_at"]
        start = datetime.fromisoformat(str(start_text)[:19]).replace(tzinfo=UTC)
        if start < now - timedelta(days=days):
            continue
        last = last_refresh_at(event_root)
        if last is not None and last > now - min_gap:
            continue
        out.append(int(event_root.name))
    return out


def log_refresh(event_root: Path, record: dict) -> None:
    """Append one refresh check to events/<id>/refreshes.json (never rewritten)."""
    path = event_root / "refreshes.json"
    history = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    history.append(record)
    path.write_text(json.dumps(history, indent=2), encoding="utf-8")


def fetch_event(
    tournament_id: int,
    client: PoliteClient,
    data_root: Path,
    force: bool = False,
    fetch_decklists: bool = True,
    min_players: int = 0,
    refresh: bool = False,
) -> dict:
    """Fetch an event into bronze (see module docstring).

    ``refresh``: for an event already in bronze, re-pull the tournament page
    and round JSON (cheap: no decklists) and compare with the latest stored
    version. Unchanged -> nothing written except a line in refreshes.json.
    Changed -> a new version beside the old one, plus any newly published
    decklists; lists that were empty last time are requested again.
    """
    root = bronze_root(data_root)
    event_root = root / "events" / str(tournament_id)
    deck_dir = root / "decklists"
    existing = latest_version(event_root)
    if existing is not None and not force and not refresh:
        log.info("event %s already in bronze (%s); skipping", tournament_id, existing.name)
        return json.loads((existing / "manifest.json").read_text())
    refreshing = refresh and existing is not None
    previous = json.loads((existing / "manifest.json").read_text()) if existing else {}

    referer = f"{BASE_URL}/Tournament/View/{tournament_id}"
    started = datetime.now(UTC)
    version = started.strftime("%Y%m%dT%H%M%SZ")
    while (event_root / version).exists():  # two writes in one second (tests, retries)
        version += "x"
    requests_before = client.requests_made

    with tempfile.TemporaryDirectory(prefix=f"melee-{tournament_id}-") as tmp:
        stage = Path(tmp) / "event"
        (stage / "rounds").mkdir(parents=True)

        page = client.get_text(f"/Tournament/View/{tournament_id}")
        meta = parse_tournament_page(page, tournament_id)
        (stage / "tournament.html").write_text(page, encoding="utf-8")

        decklist_ids: set[str] = set()
        rounds_summary = []
        matches_by_round = []
        players_estimate: int | None = None
        for rnd in meta.rounds:
            pages: list[dict] = []
            start = 0
            while True:
                form = datatables_form(MATCH_COLUMNS, start, PAGE_SIZE)
                payload = client.post_form_json(f"/Match/GetRoundMatches/{rnd.id}", form, referer)
                total, data = check_datatables_page(payload)
                if players_estimate is None:
                    players_estimate = 2 * total  # round 1: every player is in one match row
                    if min_players and players_estimate < min_players:
                        raise TooSmallError(tournament_id, players_estimate, min_players)
                pages.append(payload)
                start += PAGE_SIZE
                if not data or start >= total:
                    break
            matches = parse_round_matches(pages)
            matches_by_round.append(matches)
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

        all_matches = [m for group in matches_by_round for m in group]
        signature = matches_signature(all_matches)
        if refreshing:
            before_sig = previous.get("matches_sha256") or stored_signature(existing)
            retry_decks = set(previous.get("unusable_decklists") or {})
            changed = signature != before_sig
            log_refresh(event_root, {
                "checked_at": started.isoformat(),
                "against_version": existing.name,
                "changed": changed,
                "requests": client.requests_made - requests_before,
                "decklists_then": len(previous.get("decklist_ids") or []),
                "decklists_now": len(decklist_ids),
            })
            if not changed and not retry_decks:
                log.info("event %s unchanged since %s; nothing written",
                         tournament_id, existing.name)
                return previous
        else:
            retry_decks = set()
            changed = True

        # Decklists are shared across events (players reuse an exact 75).
        new_decks: dict[str, str] = {}
        unusable: dict[str, int] = {}
        if fetch_decklists:
            for did in sorted(decklist_ids):
                if (deck_dir / f"{did}.html").exists() and did not in retry_decks:
                    continue
                deck_page = client.get_text(f"/Decklist/View/{did}", referer=referer)
                try:
                    parse_decklist_page(deck_page, did)  # contract check before keeping it
                except UnusableDecklistError as exc:
                    unusable[did] = exc.main_count
                    if did in retry_decks:
                        continue  # still empty: the stored copy already says so
                    # Real page, no list: keep the raw HTML so it's never re-requested.
                    log.warning("%s; keeping page, deck counts as unknown", exc)
                new_decks[did] = deck_page

        if refreshing and not changed and not new_decks:
            log.info("event %s unchanged since %s (empty lists still empty); nothing written",
                     tournament_id, existing.name)
            return previous

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
            "players_estimate": players_estimate,
            "matches_sha256": signature,
            "refreshed_from": existing.name if refreshing else None,
            "decklist_ids": sorted(decklist_ids),
            "unusable_decklists": unusable,
            "tournament_html_sha256": _sha256(page),
        }
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        # Everything validated: publish to bronze.
        deck_dir.mkdir(parents=True, exist_ok=True)
        for did, text in new_decks.items():
            target = deck_dir / f"{did}.html"
            if target.exists():  # an empty list that has since been published: keep both
                target.rename(deck_dir / f"{did}.superseded-{version}.html")
            target.write_text(text, encoding="utf-8")
        event_root.mkdir(parents=True, exist_ok=True)
        shutil.move(str(stage), str(event_root / version))

    log.info("event %s: %d rounds, %d decklists (%d new), %d requests",
             tournament_id, len(meta.rounds), len(decklist_ids), len(new_decks),
             manifest["requests"])
    return manifest

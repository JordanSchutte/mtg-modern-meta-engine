"""Find Modern events on MTGO's monthly decklist index.

``https://www.mtgo.com/decklists/YYYY/MM`` lists every published event of the
month as ``/decklist/<site_name>`` links. The site name carries everything
needed to decide what to fetch::

    modern-challenge-32-2026-11-0712857682   kind=challenge-32, day, event id
    modern-league-2026-11-0111161            kind=league, day, play-event id

A league's id is the same every day (it is the league, not the day), so the
site name is the unique key for every kind.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

LINK_RE = re.compile(r'href="/decklist/(modern-[a-z0-9-]+)"', re.I)
NAME_RE = re.compile(r"^modern-(?P<kind>[a-z0-9-]+?)-(?P<day>\d{4}-\d{2}-\d{2})(?P<id>\d+)$", re.I)


@dataclass(frozen=True)
class EventRef:
    site_name: str
    kind: str          # challenge-32, challenge-16, league, preliminary, showcase-challenge...
    day: date
    event_id: str

    @property
    def path(self) -> str:
        return f"/decklist/{self.site_name}"


def parse_site_name(site_name: str) -> EventRef | None:
    m = NAME_RE.match(site_name)
    if not m:
        return None
    try:
        day = date.fromisoformat(m.group("day"))
    except ValueError:
        return None
    return EventRef(site_name, m.group("kind").lower(), day, m.group("id"))


def parse_index(html: str) -> list[EventRef]:
    """Modern events linked from an index page, oldest first. 'premodern-...' is
    not matched: the pattern anchors on the ``modern-`` prefix."""
    refs = {}
    for name in LINK_RE.findall(html):
        ref = parse_site_name(name)
        if ref is not None:
            refs[ref.site_name] = ref
    return sorted(refs.values(), key=lambda r: (r.day, r.site_name))


def months(since: date, until: date) -> list[str]:
    """``YYYY/MM`` index paths covering [since, until]."""
    out = []
    y, m = since.year, since.month
    while (y, m) <= (until.year, until.month):
        out.append(f"{y:04d}/{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out

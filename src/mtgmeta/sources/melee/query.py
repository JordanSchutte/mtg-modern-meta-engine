"""Melee *Find Events* search URLs.

The search page keeps its state in a base64url-encoded JSON query, e.g.::

    {"context": 2, "game": "MagicTheGathering", "search": null,
     "filters": ["Modern", "Past"]}

``context`` 2 is the Tournaments tab. ``filters`` holds format names, tag names
(such as ``"Regional Championship [MTG]"``) and date presets (``"Past"``).
"""

from __future__ import annotations

import base64
import json
from collections.abc import Sequence

from . import BASE_URL

TOURNAMENTS_CONTEXT = 2


def encode_query(
    filters: Sequence[str],
    game: str = "MagicTheGathering",
    search: str | None = None,
    context: int = TOURNAMENTS_CONTEXT,
) -> str:
    payload = {"context": context, "game": game, "search": search, "filters": list(filters)}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_query(q: str) -> dict:
    padded = q + "=" * (-len(q) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


def search_url(filters: Sequence[str], **kwargs) -> str:
    return f"{BASE_URL}/Search?q={encode_query(filters, **kwargs)}"

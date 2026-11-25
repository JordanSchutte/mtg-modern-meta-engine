import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def melee_fixture():
    def load(name: str) -> str:
        return (FIXTURES / "melee" / name).read_text(encoding="utf-8")

    return load


# MTGO events with lists the classifier cannot use (``usable = false``): the real
# fixtures (5 lists, all 60 cards) plus one synthetic challenge on 2026-11-08 whose
# four lists are two with an empty ``main_deck``, one at 59 cards and one intact.
MTGO_SHORT_EVENT = "modern-challenge-32-2026-11-0812857700"


@pytest.fixture
def mtgo_bronze(tmp_path):
    """Write the fixture pages plus the synthetic short-list event into
    ``tmp_path/bronze/mtgo`` the way ``fetch_event`` does; returns ``tmp_path``."""
    from mtgmeta.sources.mtgo.parse import extract_data

    mtgo = FIXTURES / "mtgo"
    root = tmp_path / "bronze" / "mtgo" / "events"
    pages = {
        "modern-challenge-16-2026-11-0312857420": ("challenge-16", "2026-11-03",
                                                   (mtgo / "challenge.html").read_text()),
        "modern-league-2026-11-0111161": ("league", "2026-11-01",
                                          (mtgo / "league.html").read_text()),
    }
    data = extract_data(pages["modern-challenge-16-2026-11-0312857420"][2])
    data["event_id"] = "12857700"
    data["starttime"] = "2026-11-08 10:00:00"
    data["description"] = "Modern Challenge 32"
    lists = [json.loads(json.dumps(d)) for d in data["decklists"]]
    lists[0]["main_deck"] = []
    lists[1]["main_deck"] = []
    short = json.loads(json.dumps(lists[2]))
    short["loginid"] = "9999999"
    short["player"] = "short_one"
    short["main_deck"][0]["qty"] = str(int(short["main_deck"][0]["qty"]) - 1)
    data["decklists"] = [*lists, short]
    pages[MTGO_SHORT_EVENT] = ("challenge-32", "2026-11-08",
                               "<html><script>\nwindow.MTGO.decklists.data = "
                               + json.dumps(data) + ";\n</script></html>")
    for site, (kind, day, html) in pages.items():
        d = root / site
        d.mkdir(parents=True)
        (d / "page.html").write_text(html, encoding="utf-8")
        (d / "manifest.json").write_text(json.dumps({
            "site_name": site, "status": "complete", "kind": kind, "day": day,
            "fetched_at": "2026-11-09T00:00:00+00:00"}), encoding="utf-8")
    return tmp_path

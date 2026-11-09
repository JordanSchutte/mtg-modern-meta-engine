#!/usr/bin/env python3
"""Fetch MTGO's decklists index and one Modern event page, raw, for inspection.

    python scripts/probe_mtgo.py --out probe/

Writes the index HTML, the first Modern Challenge page found, and a short
note of what was seen. No parsing beyond finding a link: this exists to look
at the real markup before writing the parser.
"""

from __future__ import annotations

import argparse
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx

from mtgmeta.sources.melee.http import BROWSER_UA as UA
from mtgmeta.sources.mtgo import BASE_URL


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=Path("probe"))
    ap.add_argument("--month", default=datetime.now(UTC).strftime("%Y/%m"))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    notes = []
    with httpx.Client(headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"},
                      timeout=60, follow_redirects=True) as client:
        for name, url in (("decklists_root.html", f"{BASE_URL}/decklists"),
                          ("decklists_month.html", f"{BASE_URL}/decklists/{args.month}")):
            r = client.get(url)
            notes.append(f"{url} -> {r.status_code}, {len(r.text)} bytes, final {r.url}")
            (args.out / name).write_text(r.text, encoding="utf-8")
        month = (args.out / "decklists_month.html").read_text(encoding="utf-8")
        links = sorted(set(re.findall(r'href="(/decklist/[^"]*modern[^"]*)"', month, re.I)))
        notes.append(f"modern decklist links on month page: {len(links)}")
        notes += [f"  {link}" for link in links[:40]]
        for kind in ("challenge", "league", "preliminary", "showcase"):
            pick = next((link for link in links if kind in link.lower()), None)
            if pick:
                r = client.get(BASE_URL + pick)
                (args.out / f"event_{kind}.html").write_text(r.text, encoding="utf-8")
                notes.append(f"{kind}: {pick} -> {r.status_code}, {len(r.text)} bytes")
                # Where is the data? Look for embedded JSON blobs.
                for m in re.finditer(r"window\.MTGO\.(\w+)\.(\w+)\s*=", r.text):
                    notes.append(f"  embedded: window.MTGO.{m.group(1)}.{m.group(2)}")
                for key in ("decklists", "standings", "brackets", "player", "loginid", "wins",
                            "losses", "final_rank", "placement", "card_attributes"):
                    notes.append(f"  '{key}' occurs {r.text.count(key)}x")
        # Round 2: the page is a shell; data comes from census.daybreakgames.com via the
        # decklist-item bundle. Pull the bundle, list the census calls, try them.
        page = (args.out / "event_challenge.html").read_text(encoding="utf-8") \
            if (args.out / "event_challenge.html").exists() else ""
        census = re.search(r'censusUrl\s*=\s*"([^"]+)"', page)
        notes.append(f"censusUrl: {census.group(1) if census else None}")
        bundles = re.findall(r'src="(https://www\.mtgo\.com/minify-js[^"]+)"', page)
        bundle_url = next((u for u in bundles if "decklist-item" in u), None)
        notes.append(f"bundle: {bundle_url}")
        event_id = re.search(r'"event_id"\s*:\s*"?(\d+)', page)
        if bundle_url:
            js = client.get(bundle_url.replace("&amp;", "&")).text
            (args.out / "decklist-item.js").write_text(js, encoding="utf-8")
            pat = r"""["'`]([^"'`]*(?:mtgo:v1|/get/|census)[^"'`]*)["'`]"""
            calls = sorted(set(re.findall(pat, js)))
            notes.append(f"census-looking strings in bundle: {len(calls)}")
            notes += [f"  {c}" for c in calls]
            for i, line in enumerate(js.splitlines()):
                if "census" in line.lower() or "mtgo:v1" in line:
                    notes.append(f"  js:{i}: {line.strip()[:300]}")
            if census and event_id:
                for c in calls:
                    url = c if c.startswith("http") else census.group(1) + c
                    url = re.sub(r"\$\{[^}]*\}|'\s*\+\s*[\w.]+\s*\+\s*'", event_id.group(1), url)
                    if "mtgo:v1" not in url:
                        continue
                    r = client.get(url)
                    name = re.sub(r"\W+", "_", url.split("mtgo:v1")[-1])[:60]
                    (args.out / f"census{name}.json").write_text(r.text, encoding="utf-8")
                    notes.append(f"GET {url} -> {r.status_code}, {len(r.text)} bytes")
        for kind in ("challenge", "league"):
            f = args.out / f"event_{kind}.html"
            if f.exists():
                t = f.read_text(encoding="utf-8")
                m = re.search(r"<title>(.*?)</title>", t, re.S)
                notes.append(f"{kind} page title: {m.group(1).strip() if m else None}")
    (args.out / "NOTES.txt").write_text("\n".join(notes) + "\n", encoding="utf-8")
    print("\n".join(notes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

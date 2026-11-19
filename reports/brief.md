# Modern meta brief — week of 2026-11-16

_Generated 2026-11-19 from the gold tables. "This week" below is 2026-11-12 to 2026-11-19._

## 1. Forecast field for the weekend of 2026-11-21

Model: `blend(0.5,1,1)` — 50% paper (recent weeks weighted more, half-life 1 week(s)) + 50% recent MTGO lists (last 1 week(s)). Chosen by backtest over 6 past paper week(s): its log-loss was 3.149 against 3.186 for the paper-only baseline (`paper_last`). Log-loss measures how surprised the model was by the lists that actually showed up (lower is better; 0 would mean it called every list).

| Archetype | Forecast share | Paper, last 2 wks | MTGO, last 2 wks |
| --- | ---: | ---: | ---: |
| Broodscale Combo | 13.0% | 0.0% | 10.7% |
| Izzet Prowess | 8.6% | 0.0% | 5.9% |
| Energy | 8.5% | 0.0% | 9.0% |
| Azorius Control | 7.7% | 0.0% | 7.6% |
| Phoenix | 7.0% | 0.0% | 11.9% |
| Affinity | 5.7% | 0.0% | 6.2% |
| Reanimator | 5.3% | 0.0% | 4.8% |
| Eldrazi Tron | 5.1% | 0.0% | 7.2% |
| Amulet Titan | 5.1% | 0.0% | 7.2% |
| Blink | 4.5% | 0.0% | 1.8% |
| Domain Zoo | 4.3% | 0.0% | 2.6% |
| Living End | 3.2% | 0.0% | 4.7% |

## 2. Movers

No earlier brief to compare against yet; movers start next week.

## 3. Best-positioned decks

Expected win rate = the deck's matchup win rates weighted by the forecast field (a mirror counts 50%). Matchup win rates are *shrunk*: pulled toward 50% by ten imaginary even matches, so a 3–0 record doesn't read as 100%. Coverage is how much of the forecast field the deck has a known matchup against; decks below 90% coverage are left out.

| Archetype | Expected WR | Forecast share | Coverage | Matches |
| --- | ---: | ---: | ---: | ---: |
| Phoenix | 53.3% | 7.0% | 100% | 909 |
| Creativity | 52.9% | 0.5% | 96% | 158 |
| Broodscale Combo | 52.7% | 13.0% | 100% | 9590 |
| Azorius Control | 52.1% | 7.7% | 100% | 3785 |
| Tameshi Belcher | 51.3% | 1.1% | 100% | 806 |

Underplayed (forecast share under 3% but expected win rate over 52%): **Creativity** (52.9% on 0.5% of the field, 158 matches).

## 4. Matrix changes

35 new MTGO top-8 bracket matches this week (they count toward win rates, never toward meta share).

Matchups with at least 30 matches whose shrunk win rate moved 3 points or more once this week's matches were added: none.

## 5. Data notes

Paper: 0 usable Melee lists from 0 event(s) this week, against 0 last week (+0). MTGO: 8 event(s) fetched with 233 lists.

<!-- mtgmeta-brief {"field": {"Affinity": 0.057491, "Amulet Titan": 0.050754, "Azorius Control": 0.077408, "Blink": 0.045313, "Broodscale Combo": 0.129512, "Burn": 0.003876, "Creativity": 0.004503, "Death's Shadow": 0.002273, "Devoted Druid": 0.022488, "Dimir Midrange": 0.014405, "Domain Zoo": 0.043409, "Dredge": 0.000978, "Eldrazi Aggro": 0.001121, "Eldrazi Tron": 0.0508, "Energy": 0.084961, "Esper Midrange": 0.006585, "Fight Rigging": 0.006085, "Goyf Fling": 0.002711, "Green Tron": 0.003515, "Grinding Station": 0.011062, "Hammer": 0.002744, "Hollow One": 0.006221, "Infect": 0.000238, "Izzet Murktide": 0.005257, "Izzet Prowess": 0.085745, "Living End": 0.031922, "Merfolk": 0.001643, "Mill": 0.003315, "Mono-Red Belcher": 0.00327, "Nantuko Combo": 0.001222, "Necro": 0.007093, "Neobrand": 0.022462, "Phoenix": 0.069949, "Ponza": 0.011816, "Reanimator": 0.053471, "Rhinos": 0.000646, "Ritual Oculus": 0.003879, "Ritual Omnath": 0.004595, "Ruby Storm": 0.030181, "Samwise Combo": 0.012402, "Song of Creation": 0.001563, "Tameshi Belcher": 0.011087, "Wizards": 0.006115, "Yawgmoth": 0.003915}, "generated": "2026-11-19", "model": "blend(0.5,1,1)", "target_week": "2026-11-16"} -->

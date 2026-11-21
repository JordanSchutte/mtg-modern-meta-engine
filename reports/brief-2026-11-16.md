# Modern meta brief — week of 2026-11-16

_Generated 2026-11-21 from the gold tables. "This week" below is 2026-11-14 to 2026-11-21._

## 1. Forecast field for the weekend of 2026-11-21

Model: `blend(0.5,1,1)` — 50% paper (recent weeks weighted more, half-life 1 week(s)) + 50% recent MTGO lists (last 1 week(s)). Chosen by backtest over 6 past paper week(s): its log-loss was 3.149 against 3.186 for the paper-only baseline (`paper_last`). Log-loss measures how surprised the model was by the lists that actually showed up (lower is better; 0 would mean it called every list).

| Archetype | Forecast share | Paper, last 2 wks | MTGO, last 2 wks |
| --- | ---: | ---: | ---: |
| Broodscale Combo | 13.5% | 0.0% | 11.2% |
| Izzet Prowess | 8.6% | 0.0% | 5.9% |
| Energy | 8.5% | 0.0% | 9.0% |
| Azorius Control | 7.9% | 0.0% | 7.8% |
| Phoenix | 6.9% | 0.0% | 11.8% |
| Affinity | 5.8% | 0.0% | 6.2% |
| Reanimator | 5.2% | 0.0% | 4.7% |
| Eldrazi Tron | 4.9% | 0.0% | 7.0% |
| Amulet Titan | 4.8% | 0.0% | 6.9% |
| Blink | 4.5% | 0.0% | 1.8% |
| Domain Zoo | 4.2% | 0.0% | 2.4% |
| Living End | 3.1% | 0.0% | 4.6% |

## 2. Movers

No earlier brief to compare against yet; movers start next week.

## 3. Best-positioned decks

Expected win rate = the deck's matchup win rates weighted by the forecast field (a mirror counts 50%). Matchup win rates are *shrunk*: pulled toward 50% by ten imaginary even matches, so a 3–0 record doesn't read as 100%. Coverage is how much of the forecast field the deck has a known matchup against; decks below 90% coverage are left out.

| Archetype | Expected WR | Forecast share | Coverage | Matches |
| --- | ---: | ---: | ---: | ---: |
| Creativity | 53.0% | 0.4% | 96% | 158 |
| Phoenix | 52.8% | 6.9% | 100% | 931 |
| Broodscale Combo | 52.6% | 13.5% | 100% | 9636 |
| Azorius Control | 52.3% | 7.9% | 100% | 3807 |
| Tameshi Belcher | 51.3% | 1.1% | 100% | 809 |

Underplayed (forecast share under 3% but expected win rate over 52%): **Creativity** (53.0% on 0.4% of the field, 158 matches).

## 4. Matrix changes

77 new MTGO top-8 bracket matches this week (they count toward win rates, never toward meta share).

Matchups with at least 30 matches whose shrunk win rate moved 3 points or more once this week's matches were added: none.

## 5. Data notes

Paper: 0 usable Melee lists from 0 event(s) this week, against 0 last week (+0). MTGO: 13 event(s) fetched with 394 lists.

<!-- mtgmeta-brief {"field": {"Affinity": 0.057571, "Amulet Titan": 0.048023, "Azorius Control": 0.079269, "Blink": 0.045358, "Broodscale Combo": 0.134685, "Burn": 0.00367, "Creativity": 0.004194, "Death's Shadow": 0.00217, "Devoted Druid": 0.021974, "Dimir Midrange": 0.015729, "Domain Zoo": 0.041661, "Dredge": 0.000978, "Eldrazi Aggro": 0.001121, "Eldrazi Tron": 0.049348, "Energy": 0.084823, "Esper Midrange": 0.00735, "Fight Rigging": 0.005983, "Goyf Fling": 0.003579, "Green Tron": 0.003515, "Grinding Station": 0.011313, "Hammer": 0.002641, "Hollow One": 0.00581, "Infect": 0.000238, "Izzet Murktide": 0.005051, "Izzet Prowess": 0.085618, "Living End": 0.03119, "Merfolk": 0.001643, "Mill": 0.003109, "Mono-Red Belcher": 0.003064, "Nantuko Combo": 0.001119, "Necro": 0.006681, "Neobrand": 0.023421, "Phoenix": 0.069091, "Ponza": 0.011302, "Reanimator": 0.052226, "Rhinos": 0.000646, "Ritual Oculus": 0.003776, "Ritual Omnath": 0.004492, "Ruby Storm": 0.030169, "Samwise Combo": 0.012242, "Song of Creation": 0.00146, "Tameshi Belcher": 0.011339, "Wizards": 0.00744, "Yawgmoth": 0.003915}, "generated": "2026-11-21", "model": "blend(0.5,1,1)", "target_week": "2026-11-16"} -->

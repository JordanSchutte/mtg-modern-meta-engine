# Modern meta brief — week of 2026-11-23

_Generated 2026-11-22 from the gold tables. "This week" below is 2026-11-15 to 2026-11-22._

## 1. Forecast field for the weekend of 2026-11-28

Model: `blend(0.5,1,1)` — 50% paper (recent weeks weighted more, half-life 1 week(s)) + 50% recent MTGO lists (last 1 week(s)). Chosen by backtest over 6 past paper week(s): its log-loss was 3.149 against 3.186 for the paper-only baseline (`paper_last`). Log-loss measures how surprised the model was by the lists that actually showed up (lower is better; 0 would mean it called every list).

| Archetype | Forecast share | Paper, last 2 wks | MTGO, last 2 wks |
| --- | ---: | ---: | ---: |
| Broodscale Combo | 13.1% | 0.0% | 10.7% |
| Energy | 11.2% | 0.0% | 11.7% |
| Izzet Prowess | 8.8% | 0.0% | 6.9% |
| Phoenix | 7.4% | 0.0% | 11.6% |
| Affinity | 6.8% | 0.0% | 7.1% |
| Azorius Control | 6.6% | 0.0% | 7.6% |
| Blink | 5.6% | 0.0% | 2.2% |
| Reanimator | 5.2% | 0.0% | 5.4% |
| Amulet Titan | 5.1% | 0.0% | 6.5% |
| Domain Zoo | 3.7% | 0.0% | 2.7% |
| Eldrazi Tron | 3.3% | 0.0% | 4.8% |
| Ruby Storm | 2.6% | 0.0% | 3.0% |

## 2. Movers

Compared with the brief for the week of 2026-11-16, archetypes whose forecast share moved by more than 1.5 points:

| Archetype | Now | Then | Change |
| --- | ---: | ---: | ---: |
| Energy | 11.2% | 8.5% | +2.7 pts |
| Eldrazi Tron | 3.3% | 4.9% | -1.6 pts |

## 3. Best-positioned decks

Expected win rate = the deck's matchup win rates weighted by the forecast field (a mirror counts 50%). Matchup win rates are *shrunk*: pulled toward 50% by ten imaginary even matches, so a 3–0 record doesn't read as 100%. Coverage is how much of the forecast field the deck has a known matchup against; decks below 90% coverage are left out.

| Archetype | Expected WR | Forecast share | Coverage | Matches |
| --- | ---: | ---: | ---: | ---: |
| Creativity | 53.0% | 0.2% | 97% | 158 |
| Phoenix | 52.8% | 7.4% | 100% | 931 |
| Broodscale Combo | 52.4% | 13.1% | 100% | 9636 |
| Azorius Control | 52.0% | 6.6% | 100% | 3807 |
| Affinity | 51.3% | 6.8% | 100% | 4388 |

Underplayed (forecast share under 3% but expected win rate over 52%): **Creativity** (53.0% on 0.2% of the field, 158 matches).

## 4. Matrix changes

56 new MTGO top-8 bracket matches this week (they count toward win rates, never toward meta share).

Matchups with at least 30 matches whose shrunk win rate moved 3 points or more once this week's matches were added: none.

## 5. Data notes

Paper: 0 usable Melee lists from 0 event(s) this week, against 0 last week (+0). MTGO: 9 event(s) fetched with 281 lists.

<!-- mtgmeta-brief {"field": {"Affinity": 0.06831, "Amulet Titan": 0.051467, "Azorius Control": 0.066174, "Blink": 0.055913, "Broodscale Combo": 0.131018, "Burn": 0.003975, "Creativity": 0.00159, "Death's Shadow": 0.001302, "Devoted Druid": 0.023756, "Dimir Midrange": 0.012562, "Domain Zoo": 0.037108, "Dredge": 0.000978, "Eldrazi Aggro": 0.001121, "Eldrazi Tron": 0.032947, "Energy": 0.112027, "Esper Midrange": 0.005614, "Fight Rigging": 0.005115, "Goyf Fling": 0.004752, "Green Tron": 0.005556, "Grinding Station": 0.025645, "Hammer": 0.001773, "Hollow One": 0.004379, "Infect": 0.000238, "Izzet Murktide": 0.005356, "Izzet Prowess": 0.088499, "Living End": 0.020866, "Merfolk": 0.001643, "Mill": 0.001373, "Mono-Red Belcher": 0.001328, "Nantuko Combo": 0.000251, "Necro": 0.00525, "Neobrand": 0.019173, "Phoenix": 0.073892, "Ponza": 0.011044, "Reanimator": 0.051847, "Rhinos": 0.000646, "Ritual Oculus": 0.002908, "Ritual Omnath": 0.003624, "Ruby Storm": 0.025921, "Samwise Combo": 0.012897, "Song of Creation": 0.000592, "Tameshi Belcher": 0.011385, "Wizards": 0.004272, "Yawgmoth": 0.003915}, "generated": "2026-11-22", "model": "blend(0.5,1,1)", "target_week": "2026-11-23"} -->

# Modern meta brief — week of 2026-11-23

_Generated 2026-11-22 from the gold tables. "This week" below is 2026-11-15 to 2026-11-22._

## 1. Forecast field for the weekend of 2026-11-28

Model: `blend(0.5,1,1)` — 50% paper (recent weeks weighted more, half-life 1 week(s)) + 50% recent MTGO lists (last 1 week(s)). Chosen by backtest over 6 past paper week(s): its log-loss was 3.149 against 3.186 for the paper-only baseline (`paper_last`). Log-loss measures how surprised the model was by the lists that actually showed up (lower is better; 0 would mean it called every list).

| Archetype | Forecast share | Paper, last 2 wks | MTGO, last 2 wks |
| --- | ---: | ---: | ---: |
| Broodscale Combo | 12.7% | 0.0% | 10.1% |
| Energy | 10.7% | 0.0% | 11.6% |
| Izzet Prowess | 8.9% | 0.0% | 7.0% |
| Phoenix | 7.0% | 0.0% | 11.5% |
| Affinity | 6.7% | 0.0% | 7.2% |
| Azorius Control | 6.3% | 0.0% | 6.9% |
| Blink | 6.3% | 0.0% | 2.8% |
| Reanimator | 5.2% | 0.0% | 5.4% |
| Amulet Titan | 5.2% | 0.0% | 6.7% |
| Eldrazi Tron | 3.9% | 0.0% | 5.3% |
| Domain Zoo | 3.8% | 0.0% | 2.8% |
| Ruby Storm | 2.6% | 0.0% | 3.0% |

## 2. Movers

Compared with the brief for the week of 2026-11-16, archetypes whose forecast share moved by more than 1.5 points:

| Archetype | Now | Then | Change |
| --- | ---: | ---: | ---: |
| Energy | 10.7% | 8.5% | +2.2 pts |
| Blink | 6.3% | 4.5% | +1.7 pts |
| Azorius Control | 6.3% | 7.9% | -1.7 pts |

## 3. Best-positioned decks

Expected win rate = the deck's matchup win rates weighted by the forecast field (a mirror counts 50%). Matchup win rates are *shrunk*: pulled toward 50% by ten imaginary even matches, so a 3–0 record doesn't read as 100%. Coverage is how much of the forecast field the deck has a known matchup against; decks below 90% coverage are left out.

| Archetype | Expected WR | Forecast share | Coverage | Matches |
| --- | ---: | ---: | ---: | ---: |
| Creativity | 53.1% | 0.4% | 97% | 161 |
| Phoenix | 52.9% | 7.0% | 100% | 933 |
| Broodscale Combo | 52.6% | 12.7% | 100% | 9644 |
| Azorius Control | 52.0% | 6.3% | 100% | 3807 |
| Affinity | 51.4% | 6.7% | 100% | 4388 |

Underplayed (forecast share under 3% but expected win rate over 52%): **Creativity** (53.1% on 0.4% of the field, 161 matches).

## 4. Matrix changes

70 new MTGO top-8 bracket matches this week (they count toward win rates, never toward meta share).

Matchups with at least 30 matches whose shrunk win rate moved 3 points or more once this week's matches were added: none.

## 5. Data notes

Paper: 0 usable Melee lists from 0 event(s) this week, against 0 last week (+0). MTGO: 14 event(s) fetched with 424 lists.

<!-- mtgmeta-brief {"field": {"Affinity": 0.067352, "Amulet Titan": 0.051689, "Azorius Control": 0.062742, "Blink": 0.062686, "Broodscale Combo": 0.127484, "Burn": 0.003331, "Creativity": 0.004383, "Death's Shadow": 0.001302, "Devoted Druid": 0.021824, "Dimir Midrange": 0.013314, "Domain Zoo": 0.038077, "Dredge": 0.000978, "Eldrazi Aggro": 0.002517, "Eldrazi Tron": 0.038859, "Energy": 0.107101, "Esper Midrange": 0.008408, "Fight Rigging": 0.005115, "Goyf Fling": 0.005504, "Green Tron": 0.004911, "Grinding Station": 0.023393, "Hammer": 0.001773, "Hollow One": 0.005131, "Infect": 0.000238, "Izzet Murktide": 0.004712, "Izzet Prowess": 0.089474, "Living End": 0.022587, "Merfolk": 0.001643, "Mill": 0.001373, "Mono-Red Belcher": 0.001328, "Nantuko Combo": 0.000251, "Necro": 0.007399, "Neobrand": 0.015308, "Phoenix": 0.06993, "Ponza": 0.011152, "Reanimator": 0.051852, "Rhinos": 0.000646, "Ritual Oculus": 0.002908, "Ritual Omnath": 0.003624, "Ruby Storm": 0.026246, "Samwise Combo": 0.011073, "Song of Creation": 0.000592, "Tameshi Belcher": 0.009452, "Wizards": 0.005025, "Yawgmoth": 0.005312}, "generated": "2026-11-22", "model": "blend(0.5,1,1)", "target_week": "2026-11-23"} -->

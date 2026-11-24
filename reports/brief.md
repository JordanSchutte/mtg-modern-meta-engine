# Modern meta brief — week of 2026-11-23

_Generated 2026-11-24 from the gold tables. "This week" below is 2026-11-17 to 2026-11-24._

## 1. Forecast field for the weekend of 2026-11-28

Model: `blend(0.5,1,1)` — 50% paper (recent weeks weighted more, half-life 1 week(s)) + 50% recent MTGO lists (last 1 week(s)). Chosen by backtest over 6 past paper week(s): its log-loss was 3.149 against 3.186 for the paper-only baseline (`paper_last`). Log-loss measures how surprised the model was by the lists that actually showed up (lower is better; 0 would mean it called every list).

| Archetype | Forecast share | Paper, last 2 wks | MTGO, last 2 wks |
| --- | ---: | ---: | ---: |
| Broodscale Combo | 12.5% | 0.0% | 9.7% |
| Energy | 10.5% | 0.0% | 11.8% |
| Izzet Prowess | 8.6% | 0.0% | 6.7% |
| Azorius Control | 6.9% | 0.0% | 7.2% |
| Phoenix | 6.6% | 0.0% | 11.1% |
| Affinity | 5.9% | 0.0% | 6.6% |
| Blink | 5.8% | 0.0% | 2.7% |
| Reanimator | 5.2% | 0.0% | 5.4% |
| Amulet Titan | 5.0% | 0.0% | 6.6% |
| Eldrazi Tron | 4.1% | 0.0% | 5.2% |
| Domain Zoo | 3.5% | 0.0% | 2.5% |
| Ruby Storm | 2.9% | 0.0% | 3.2% |

## 2. Movers

Compared with the brief for the week of 2026-11-16, archetypes whose forecast share moved by more than 1.5 points:

| Archetype | Now | Then | Change |
| --- | ---: | ---: | ---: |
| Energy | 10.5% | 8.5% | +2.0 pts |

## 3. Best-positioned decks

Expected win rate = the deck's matchup win rates weighted by the forecast field (a mirror counts 50%). Matchup win rates are *shrunk*: pulled toward 50% by ten imaginary even matches, so a 3–0 record doesn't read as 100%. Coverage is how much of the forecast field the deck has a known matchup against; decks below 90% coverage are left out.

| Archetype | Expected WR | Forecast share | Coverage | Matches |
| --- | ---: | ---: | ---: | ---: |
| Creativity | 53.0% | 0.5% | 96% | 161 |
| Phoenix | 52.9% | 6.6% | 100% | 935 |
| Broodscale Combo | 52.6% | 12.5% | 100% | 9656 |
| Azorius Control | 51.9% | 6.9% | 100% | 3818 |
| Tameshi Belcher | 51.6% | 1.0% | 100% | 812 |

Underplayed (forecast share under 3% but expected win rate over 52%): **Creativity** (53.0% on 0.5% of the field, 161 matches).

## 4. Matrix changes

84 new MTGO top-8 bracket matches this week (they count toward win rates, never toward meta share).

Matchups with at least 30 matches whose shrunk win rate moved 3 points or more once this week's matches were added: none.

## 5. Data notes

Paper: 0 usable Melee lists from 0 event(s) this week, against 0 last week (+0). MTGO: 18 event(s) fetched with 547 lists.

<!-- mtgmeta-brief {"field": {"Affinity": 0.058659, "Amulet Titan": 0.04998, "Azorius Control": 0.068912, "Blink": 0.058419, "Broodscale Combo": 0.12459, "Burn": 0.00478, "Creativity": 0.005385, "Death's Shadow": 0.001302, "Devoted Druid": 0.022378, "Dimir Midrange": 0.020009, "Domain Zoo": 0.035443, "Dredge": 0.000978, "Eldrazi Aggro": 0.002069, "Eldrazi Tron": 0.040627, "Energy": 0.104684, "Esper Midrange": 0.008461, "Fight Rigging": 0.005115, "Goyf Fling": 0.006506, "Green Tron": 0.005412, "Grinding Station": 0.026109, "Hammer": 0.001773, "Hollow One": 0.004235, "Infect": 0.000238, "Izzet Murktide": 0.00711, "Izzet Prowess": 0.08592, "Living End": 0.021904, "Merfolk": 0.001643, "Mill": 0.001373, "Mono-Red Belcher": 0.001328, "Nantuko Combo": 0.000251, "Necro": 0.010799, "Neobrand": 0.014519, "Phoenix": 0.065798, "Ponza": 0.011706, "Reanimator": 0.051882, "Rhinos": 0.000646, "Ritual Oculus": 0.003857, "Ritual Omnath": 0.003624, "Ruby Storm": 0.028856, "Samwise Combo": 0.013129, "Song of Creation": 0.000592, "Tameshi Belcher": 0.010006, "Wizards": 0.004129, "Yawgmoth": 0.004864}, "generated": "2026-11-24", "model": "blend(0.5,1,1)", "target_week": "2026-11-23"} -->

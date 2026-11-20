# Modern meta brief — week of 2026-11-16

_Generated 2026-11-20 from the gold tables. "This week" below is 2026-11-13 to 2026-11-20._

## 1. Forecast field for the weekend of 2026-11-21

Model: `blend(0.5,1,1)` — 50% paper (recent weeks weighted more, half-life 1 week(s)) + 50% recent MTGO lists (last 1 week(s)). Chosen by backtest over 6 past paper week(s): its log-loss was 3.149 against 3.186 for the paper-only baseline (`paper_last`). Log-loss measures how surprised the model was by the lists that actually showed up (lower is better; 0 would mean it called every list).

| Archetype | Forecast share | Paper, last 2 wks | MTGO, last 2 wks |
| --- | ---: | ---: | ---: |
| Broodscale Combo | 13.3% | 0.0% | 11.0% |
| Energy | 8.5% | 0.0% | 9.0% |
| Izzet Prowess | 8.5% | 0.0% | 5.8% |
| Azorius Control | 7.9% | 0.0% | 7.7% |
| Phoenix | 6.9% | 0.0% | 11.8% |
| Affinity | 5.8% | 0.0% | 6.2% |
| Reanimator | 5.3% | 0.0% | 4.7% |
| Amulet Titan | 5.0% | 0.0% | 7.1% |
| Eldrazi Tron | 4.9% | 0.0% | 7.0% |
| Blink | 4.6% | 0.0% | 1.9% |
| Domain Zoo | 4.2% | 0.0% | 2.5% |
| Living End | 3.2% | 0.0% | 4.7% |

## 2. Movers

No earlier brief to compare against yet; movers start next week.

## 3. Best-positioned decks

Expected win rate = the deck's matchup win rates weighted by the forecast field (a mirror counts 50%). Matchup win rates are *shrunk*: pulled toward 50% by ten imaginary even matches, so a 3–0 record doesn't read as 100%. Coverage is how much of the forecast field the deck has a known matchup against; decks below 90% coverage are left out.

| Archetype | Expected WR | Forecast share | Coverage | Matches |
| --- | ---: | ---: | ---: | ---: |
| Phoenix | 53.0% | 6.9% | 100% | 921 |
| Creativity | 53.0% | 0.4% | 96% | 158 |
| Broodscale Combo | 52.7% | 13.3% | 100% | 9626 |
| Azorius Control | 52.3% | 7.9% | 100% | 3801 |
| Tameshi Belcher | 51.4% | 1.1% | 100% | 809 |

Underplayed (forecast share under 3% but expected win rate over 52%): **Creativity** (53.0% on 0.4% of the field, 158 matches).

## 4. Matrix changes

42 new MTGO top-8 bracket matches this week (they count toward win rates, never toward meta share).

Matchups with at least 30 matches whose shrunk win rate moved 3 points or more once this week's matches were added: none.

## 5. Data notes

Paper: 0 usable Melee lists from 0 event(s) this week, against 0 last week (+0). MTGO: 9 event(s) fetched with 261 lists.

<!-- mtgmeta-brief {"field": {"Affinity": 0.057504, "Amulet Titan": 0.04974, "Azorius Control": 0.078811, "Blink": 0.045788, "Broodscale Combo": 0.133111, "Burn": 0.003766, "Creativity": 0.004337, "Death's Shadow": 0.002218, "Devoted Druid": 0.022212, "Dimir Midrange": 0.0151, "Domain Zoo": 0.042472, "Dredge": 0.000978, "Eldrazi Aggro": 0.001121, "Eldrazi Tron": 0.049091, "Energy": 0.084842, "Esper Midrange": 0.007446, "Fight Rigging": 0.00603, "Goyf Fling": 0.002711, "Green Tron": 0.003515, "Grinding Station": 0.010731, "Hammer": 0.002689, "Hollow One": 0.006001, "Infect": 0.000238, "Izzet Murktide": 0.005147, "Izzet Prowess": 0.084731, "Living End": 0.03243, "Merfolk": 0.001643, "Mill": 0.003205, "Mono-Red Belcher": 0.003159, "Nantuko Combo": 0.001167, "Necro": 0.006872, "Neobrand": 0.023412, "Phoenix": 0.068528, "Ponza": 0.011541, "Reanimator": 0.052789, "Rhinos": 0.000646, "Ritual Oculus": 0.003824, "Ritual Omnath": 0.00454, "Ruby Storm": 0.03016, "Samwise Combo": 0.012766, "Song of Creation": 0.001507, "Tameshi Belcher": 0.010757, "Wizards": 0.00681, "Yawgmoth": 0.003915}, "generated": "2026-11-20", "model": "blend(0.5,1,1)", "target_week": "2026-11-16"} -->

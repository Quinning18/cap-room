# Strategy research (November 2026)

Backtests behind Cap Room's Cash and Tournament modes. Data: Sleeper projections and box scores for
2026 NFL weeks 1-10 and NBA Oct 20 - Nov 18, plus DraftKings salaries for 7 NFL and 28 NBA past main slates.
The field is simulated (300 optimizer lineups on noisy Sleeper projections), so "cash rate" means beating
that field's median, not a real contest result. Samples are small; treat differences of a few points as noise.

| | NFL cash rate | NBA cash rate | NFL top-1% | NBA top-1% |
|---|---|---|---|---|
| Top projected lineup (Sleeper) | 71% | ~68% | | |
| Balanced (salary floors, no Questionable) | 71% (lower avg percentile) | 62% | | |
| NBA blend 60% Sleeper / 40% season avg | | 62% | | |
| 20 lineups, top-20 projected | | | 14% | 14% |
| 20 lineups, simulation-picked | | | 43% | 28% |

Measured correlations (results vs projection, NFL 2026): QB-WR1 +.37, QB-TE1 +.30, QB-WR2/3 +.24,
QB-opposing QB +.22, DST-opposing QB -.43, DST-opposing RB1 -.29. NBA teammates -.04, opponents -.02.

Projection accuracy (NBA): Sleeper MAE 7.87 DK pts; 60/40 blend with season average 7.69, but blended
lineups did worse, so the app uses Sleeper alone. Sleeper's final projections already adjust for
teammates ruled out, so building after the last injury news matters more than any adjustment.

Scripts expect the downloaded data under the `BT` path in `data.py`; `backtest.py <code dir> nfl|nba`.

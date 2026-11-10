# DraftKings NFL Lineup Optimizer (Sleeper projections)

Builds the highest-projected DraftKings NFL lineups under the salary cap, using:

- **Projections** from Sleeper (the projected stat lines behind Players > Leaders > Projections),
  converted to **DraftKings points**: full PPR, 300/100-yard bonuses, -1 INT/fumble, DK defense
  scoring. `--scoring league` uses your Sleeper league's scoring instead.
- **Salaries, slates, salary cap and roster slots** pulled live from DraftKings
- **Every player on the slate** is considered, whoever owns them in your league

## Web app (docs/index.html)
Live at https://quinning18.github.io/cap-room/. Each time it opens it pulls the current week's Sleeper
projections, DraftKings slates, salaries and salary cap.

- **Classic or Showdown** (Captain Mode: 1 CPT at 1.5x points and salary + 5 FLEX, both teams required).
- **Projections board** by position, with salary and points per $1K.
- **Lock** any player (in Showdown, **CPT** locks him as Captain). Locked players go into every lineup
  built, and **Best Around My Locks** builds the top 5 lineups around them. Locks are remembered per slate
  on your device.
- **Lineup types:** Top 5 Balanced, Top 5 Projected, and Top QB / RB / WR / TE (in Showdown, that player
  is Captain). Balanced salary minimums are adjustable.
- Lineups download as a DraftKings upload file. If DraftKings refuses the live request, the page asks for
  the salary CSV from any contest page (Export to CSV) instead.

## Command line setup
```
pip install -r requirements.txt
```
`config.json` holds your Sleeper league ID and the default scoring (`dk`).

## Usage
```
python -m dkopt                         # 1 lineup, main Classic slate, current week
python -m dkopt -n 20 --max-exposure 0.5 --min-unique 3
python -m dkopt --stack 2 --bringback   # QB + 2 pass-catchers + 1 opponent
python -m dkopt --lock "Puka Nacua" --exclude "Drew Lock"
python -m dkopt --list-slates           # see slate IDs (e.g. Afternoon Only, Primetime)
python -m dkopt --slate 155997          # optimize a specific slate
python -m dkopt --type showdown --list-slates
python -m dkopt --type showdown --slate 156014 -n 5
python -m dkopt --rankings-only --rankings 30            # rankings by position, no lineups
python -m dkopt --rankings-only --pos WR --rankings 40   # one position, like Sleeper's position filter
python -m dkopt --scoring league        # use your Sleeper league's scoring instead of DraftKings
```

Each run writes to `output/`:
- `dk_upload_*.csv` - lineups in DraftKings bulk-upload format (draftable IDs). On DraftKings, go to
  Lineups > Upload Lineups.
- `rankings_*.csv` - every slate player ranked by position with salary, league projection,
  DraftKings-scoring projection and points per $1K.

## Defaults
- Classic: QB is stacked with at least 1 of his WR/TE; your DST never faces your own players.
- Players DraftKings lists as Out, IR or Doubtful are skipped; Questionable players stay in.
- FLEX gets the latest-starting flex player, which keeps late-swap options open.

## Notes
- DraftKings has no official API. These are the endpoints its own website uses, so they could change.
- Projected yardage bonuses are counted as expected value (3 pts x the chance of reaching the threshold).

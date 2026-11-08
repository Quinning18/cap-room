"""Sleeper API: NFL state, league info, rosters and weekly projections."""
from .http import get_json

API = "https://api.sleeper.app/v1"
PROJ_API = "https://api.sleeper.com/projections/nfl"
POSITIONS = ["QB", "RB", "WR", "TE", "DEF", "K"]


def nfl_state():
    return get_json(f"{API}/state/nfl")


def league(league_id):
    return get_json(f"{API}/league/{league_id}")


def league_users(league_id):
    return get_json(f"{API}/league/{league_id}/users")


def league_rosters(league_id):
    return get_json(f"{API}/league/{league_id}/rosters")


def projections(season, week, season_type="regular"):
    """Return {player_id: record} where record has 'stats', 'player', 'team', 'opponent'."""
    params = [("season_type", season_type)] + [("position[]", p) for p in POSITIONS]
    rows = get_json(f"{PROJ_API}/{season}/{week}", params=params)
    out = {}
    for r in rows:
        if r.get("stats") and r.get("player"):
            out[r["player_id"]] = r
    return out


def league_points(stats, scoring):
    """Projected points under the league's scoring settings - the same calculation Sleeper
    uses for Players > Leaders > Projections: each projected stat x its scoring value."""
    return round(sum((stats.get(k) or 0) * v for k, v in scoring.items()), 2)


def roster_ownership(league_id):
    """Return {player_id: owner display name} for every rostered player in the league."""
    users = {u["user_id"]: u.get("display_name") for u in league_users(league_id)}
    own = {}
    for r in league_rosters(league_id):
        name = users.get(r.get("owner_id"), f"roster {r.get('roster_id')}")
        for pid in r.get("players") or []:
            own[pid] = name
    return own

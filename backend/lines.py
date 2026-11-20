"""Market-implied projection inputs for Cap Room.

Runs on a schedule (GitHub Actions). For upcoming NBA and NFL games it collects:
  * Game lines (ESPN, free): each team's implied points from the spread and total, compared with the points
    Sleeper's projections imply for that team -> a team factor.
  * Player props (The Odds API, needs ODDS_API_KEY): the consensus median of each prop stat after removing
    the sportsbook margin.
and writes them to a JSON file. The web app blends propped stats 50/50 with Sleeper's projected stats and
applies half of the team factor to stats without props. Nothing about this is shown in the app.

Usage: python backend/lines.py OUT.json [PREVIOUS.json]
Env:   ODDS_API_KEY (optional; without it only game lines are used)
       PROPS_REFRESH_HOURS (default 3): refetch a game's props only if older than this,
       PROPS_CLOSE_HOURS (default 2): ...or if the game starts within this many hours (refresh every run).
       PROPS_WINDOW_HOURS (default 36): only fetch props for games starting within this many hours.
"""
import json, math, os, re, sys, time, unicodedata, statistics as st
from datetime import datetime, timedelta, timezone
import urllib.request, urllib.parse

ESPN = "https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard?dates={dates}"
SLEEPER_STATE = "https://api.sleeper.app/v1/state/{sport}"
SLEEPER_PROJ = "https://api.sleeper.com/projections/{sport}/{season}/{week}?season_type=regular{pos}"
ODDS = "https://api.the-odds-api.com/v4/sports/{key}"
SPORTS = {
    "nba": dict(espn="basketball/nba", odds="basketball_nba", pos="",
                markets=["player_points", "player_rebounds", "player_assists", "player_threes"]),
    "nfl": dict(espn="football/nfl", odds="americanfootball_nfl",
                pos="".join(f"&position[]={p}" for p in ("QB", "RB", "WR", "TE", "DEF")),
                markets=["player_pass_yds", "player_pass_tds", "player_pass_interceptions", "player_rush_yds",
                         "player_receptions", "player_reception_yds", "player_anytime_td"]),
}
# Prop market -> Sleeper stat key, and a spread (sd) model used to turn a line plus its no-vig odds into a median.
MARKET_STAT = {
    "player_points": ("pts", lambda L: 0.30 * L + 2.0), "player_rebounds": ("reb", lambda L: 0.35 * L + 1.0),
    "player_assists": ("ast", lambda L: 0.40 * L + 1.0), "player_threes": ("tpm", lambda L: 0.55 * L + 0.6),
    "player_pass_yds": ("pass_yd", lambda L: 0.22 * L + 15), "player_pass_tds": ("pass_td", lambda L: 0.55 * L + 0.5),
    "player_pass_interceptions": ("pass_int", lambda L: 0.6 * L + 0.4), "player_rush_yds": ("rush_yd", lambda L: 0.45 * L + 8),
    "player_receptions": ("rec", lambda L: 0.35 * L + 1.0), "player_reception_yds": ("rec_yd", lambda L: 0.45 * L + 8),
}
ESPN_TO_SLEEPER = {"nba": {"GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS"},
                   "nfl": {"WSH": "WAS"}}

def get(url, retries=3):
    for k in range(retries):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json",
                                                       "User-Agent": "cap-room-lines/1.0 (+https://github.com/Quinning18/cap-room)"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.load(r), dict(r.headers)
        except Exception as e:
            last = e
            time.sleep(2 ** k)
    raise RuntimeError(f"GET {url.split('apiKey=')[0]} failed: {last}")

def norm(n):
    s = unicodedata.normalize("NFKD", n).encode("ascii", "ignore").decode().lower().replace("-", " ")
    return " ".join(w for w in re.sub(r"[^a-z ]", "", s).split() if w not in {"jr", "sr", "ii", "iii", "iv", "v"})

def inv_norm(p):
    """Inverse standard normal CDF (Acklam's approximation)."""
    p = min(max(p, 1e-6), 1 - 1e-6)
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549671010180036e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
    if p < 0.02425:
        q = math.sqrt(-2 * math.log(p)); return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > 1 - 0.02425:
        q = math.sqrt(-2 * math.log(1 - p)); return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5; r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)

def implied_prob(american):
    a = float(american)
    return 100 / (a + 100) if a > 0 else -a / (-a + 100)

# ------------------------------------------------------------------ game lines
def espn_games(sport, days):
    """Upcoming games with ESPN (DraftKings) lines: {(home, away): dict(start, total, spread_home, teams)}."""
    cfg, out = SPORTS[sport], {}
    now = datetime.now(timezone.utc)
    dates = [(now + timedelta(days=d)).strftime("%Y%m%d") for d in range(days)]
    seen = set()
    for d in dates if sport == "nba" else [""]:
        url = ESPN.format(path=cfg["espn"], dates=d) if d else ESPN.split("?")[0].format(path=cfg["espn"])
        data, _ = get(url)
        for ev in data.get("events", []):
            if ev["id"] in seen: continue
            seen.add(ev["id"])
            c = ev["competitions"][0]
            if c["status"]["type"]["state"] != "pre": continue
            fix = ESPN_TO_SLEEPER[sport]
            teams = {t["homeAway"]: dict(abbr=fix.get(t["team"]["abbreviation"], t["team"]["abbreviation"]), nick=t["team"]["name"]) for t in c["competitors"]}
            o = (c.get("odds") or [None])[0]
            g = dict(start=ev["date"], home=teams["home"]["abbr"], away=teams["away"]["abbr"],
                     nick={teams["home"]["abbr"]: teams["home"]["nick"], teams["away"]["abbr"]: teams["away"]["nick"]})
            if o and o.get("overUnder") is not None and o.get("spread") is not None:
                total, sp = float(o["overUnder"]), float(o["spread"])   # spread is from the home team's view
                g["implied"] = {g["home"]: (total - sp) / 2, g["away"]: (total + sp) / 2}
            out[(g["home"], g["away"])] = g
    return out

# ------------------------------------------------------------------ Sleeper
def sleeper_rows(sport):
    state, _ = get(SLEEPER_STATE.format(sport=sport))
    season, week = state["season"], int(state["week"] or 1)
    rows = []
    for w in (week, week + 1):
        data, _ = get(SLEEPER_PROJ.format(sport=sport, season=season, week=w, pos=SPORTS[sport]["pos"]))
        rows += [r for r in data if r.get("stats") and r.get("player")]
    return rows

def et_date(iso):
    t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (t - timedelta(hours=5)).strftime("%Y-%m-%d")

def team_factors(sport, games, rows):
    """Vegas implied points / Sleeper-implied points for each team in each upcoming game."""
    out = {}
    if sport == "nfl":
        # Sleeper's projected points allowed by a defense is its implied score for the opposing offense.
        pairs = {(g["home"], g["away"]) for g in games.values()} | {(g["away"], g["home"]) for g in games.values()}
        allow = {r["opponent"]: float(r["stats"].get("pts_allow") or 0) for r in rows
                 if r["player"].get("position") == "DEF" and (r.get("team"), r.get("opponent")) in pairs}
        for g in games.values():
            for t, v in (g.get("implied") or {}).items():
                if allow.get(t): out[t] = v / allow[t]
        return out
    # NBA: sum of the team's projected points that day; only relative differences matter, so normalize to mean 1.
    raw = {}
    for g in games.values():
        date = et_date(g["start"])
        for t, v in (g.get("implied") or {}).items():
            s = sum(float(r["stats"].get("pts") or 0) for r in rows if r.get("team") == t and r.get("date") == date)
            if s > 50: raw[f"{t}|{date}"] = v / s
    if raw:
        mu = st.mean(raw.values())
        out = {k: v / mu for k, v in raw.items()}
    return out

# ------------------------------------------------------------------ props
def consensus_props(event_odds, sport):
    """{normalized player name: {stat: median}} from one event's bookmakers."""
    per = {}   # (name, market) -> list of medians across books
    for bk in event_odds.get("bookmakers", []):
        for mk in bk.get("markets", []):
            key = mk.get("key")
            lines = {}
            for o in mk.get("outcomes", []):
                who = o.get("description") or ""
                side = (o.get("name") or "").lower()
                if not who: continue
                lines.setdefault((who, o.get("point")), {})[side] = o.get("price")
            for (who, point), sides in lines.items():
                if key == "player_anytime_td":
                    if "yes" not in sides: continue
                    p = implied_prob(sides["yes"])
                    p = p / (p + implied_prob(sides["no"])) if "no" in sides else p / 1.07   # remove margin
                    per.setdefault((norm(who), "td_any"), []).append(-math.log(max(1e-6, 1 - min(p, 0.95))))
                elif key in MARKET_STAT and point is not None and "over" in sides and "under" in sides:
                    po, pu = implied_prob(sides["over"]), implied_prob(sides["under"])
                    p_over = po / (po + pu)
                    stat, sdf = MARKET_STAT[key]
                    L = float(point)
                    per.setdefault((norm(who), stat), []).append(max(0.0, L + sdf(L) * inv_norm(p_over)))
    out = {}
    for (who, stat), vals in per.items():
        out.setdefault(who, {})[stat] = round(st.median(vals), 3)
    return out

def odds_events(sport, key):
    data, hdr = get(f"{ODDS.format(key=SPORTS[sport]['odds'])}/events?apiKey={key}")
    return data, hdr

def match_event(ev, games):
    """Find the ESPN game for an Odds API event by team nicknames."""
    for g in games.values():
        names = {t: n.lower() for t, n in g["nick"].items()}
        if names[g["home"]] in ev["home_team"].lower() and names[g["away"]] in ev["away_team"].lower():
            return g
    return None

def main():
    out_path = sys.argv[1]
    prev = {}
    if len(sys.argv) > 2 and os.path.exists(sys.argv[2]):
        try: prev = json.load(open(sys.argv[2]))
        except Exception: prev = {}
    key = os.environ.get("ODDS_API_KEY", "").strip()
    refresh_h = float(os.environ.get("PROPS_REFRESH_HOURS", 3)); close_h = float(os.environ.get("PROPS_CLOSE_HOURS", 2))
    window_h = float(os.environ.get("PROPS_WINDOW_HOURS", 36))
    now = datetime.now(timezone.utc)
    result = {"updated": now.isoformat(timespec="seconds")}
    for sport in ("nba", "nfl"):
        try:
            games = espn_games(sport, days=2)
            rows = sleeper_rows(sport)
        except Exception as e:
            print(f"{sport}: skipped ({e})"); result[sport] = prev.get(sport, {}); continue
        teams = team_factors(sport, games, rows)
        # Sleeper player lookup by normalized name within each team
        by_name = {}
        for r in rows:
            nm = norm(f"{r['player'].get('first_name', '')} {r['player'].get('last_name', '')}")
            by_name.setdefault((nm, r.get("team")), []).append(r)
        players, events_meta = {}, {}
        prev_sport = prev.get(sport, {})
        if key:
            try:
                evs, hdr = odds_events(sport, key)
            except Exception as e:
                print(f"{sport}: props skipped ({e})"); evs = []
            for ev in evs:
                start = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
                hours = (start - now).total_seconds() / 3600
                if hours < 0 or hours > window_h: continue
                g = match_event(ev, games)
                if not g: continue
                old = prev_sport.get("events", {}).get(ev["id"])
                fresh = old and (now - datetime.fromisoformat(old["fetched"])).total_seconds() / 3600 < refresh_h and hours > close_h
                if fresh:
                    events_meta[ev["id"]] = old
                    players.update({k: v for k, v in prev_sport.get("players", {}).items() if v.get("_ev") == ev["id"]})
                    continue
                q = urllib.parse.urlencode({"apiKey": key, "regions": "us", "markets": ",".join(SPORTS[sport]["markets"]), "oddsFormat": "american"})
                try:
                    data, hdr = get(f"{ODDS.format(key=SPORTS[sport]['odds'])}/events/{ev['id']}/odds?{q}")
                except Exception as e:
                    print(f"  {ev['away_team']} @ {ev['home_team']}: {e}"); continue
                props = consensus_props(data, sport)
                date = et_date(ev["commence_time"])
                n = 0
                for who, stats in props.items():
                    for team in (g["home"], g["away"]):
                        cands = by_name.get((who, team))
                        if not cands: continue
                        r = next((c for c in cands if sport == "nfl" or c.get("date") == date), None)
                        if not r: continue
                        k = r["player_id"] + (f"|{date}" if sport == "nba" else "")
                        players[k] = dict(stats, _ev=ev["id"]); n += 1
                events_meta[ev["id"]] = {"fetched": now.isoformat(timespec="seconds"), "players": n}
                print(f"  {sport} {ev['away_team']} @ {ev['home_team']}: {n} players with props; credits left {hdr.get('x-requests-remaining', hdr.get('X-Requests-Remaining', '?'))}")
        result[sport] = {"teams": {k: round(v, 4) for k, v in teams.items()}, "players": players, "events": events_meta}
        print(f"{sport}: {len(games)} games, {len(teams)} team factors, {len(players)} players with props")
    json.dump(result, open(out_path, "w"), separators=(",", ":"))

if __name__ == "__main__":
    main()

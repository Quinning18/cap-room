"""Nightly performance check for Cap Room (run by a scheduled task at 1am Central).

1. Record today's main DraftKings Classic slates (NBA, NFL) and snapshot Sleeper's player lines for them
   (lines disappear once games start).
2. Grade recorded slates whose games are final, using Sleeper box scores:
     - projection accuracy: Sleeper alone vs. blended with the lines at several weights
     - outcome spread by projection size (feeds the simulator)
     - Cash: did the top projected lineup beat the median of a simulated field?
     - Tournament: did the simulation-picked set of lineups reach the field's top 1%?
3. Tune docs/tuning.json within guardrails when enough graded data supports a change.
4. Write research/nightly/report.md.

Usage: python3 research/nightly.py            (normal nightly run)
       python3 research/nightly.py --no-grade (only record slates)
"""
import sys, os, json, math, re, time, csv, collections, statistics as st, urllib.request
from datetime import datetime, timedelta, timezone
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "nightly")
TUNING = os.path.join(ROOT, "docs", "tuning.json")
sys.path.insert(0, HERE)
import backtest as B
from data import nfl_proj_dk, nfl_act_dk, nba_proj_dk, nba_act_dk, nba_min

UA = {"User-Agent": "cap-room-nightly/1.0 (+https://github.com/Quinning18/cap-room)", "Accept": "application/json"}
CONTEST = {"nba": 70, "nfl": 21}
LINE_STATS = {
    "nba": {"points": ("pts", lambda L: .30 * L + 2), "rebounds": ("reb", lambda L: .35 * L + 1), "assists": ("ast", lambda L: .40 * L + 1),
            "threes_made": ("tpm", lambda L: .55 * L + .6)},
    "nfl": {"passing_yards": ("pass_yd", lambda L: .22 * L + 15), "passing_touchdowns": ("pass_td", lambda L: .55 * L + .5),
            "interceptions": ("pass_int", lambda L: .6 * L + .4), "rushing_yards": ("rush_yd", lambda L: .45 * L + 8),
            "receptions": ("rec", lambda L: .35 * L + 1), "receiving_yards": ("rec_yd", lambda L: .45 * L + 8),
            "anytime_touchdowns": ("td_any", None)},
}
WEIGHTS = [0.0, 0.25, 0.5, 0.75, 1.0]

def get(url, retries=3):
    last = None
    for k in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return json.load(r)
        except Exception as e:
            last = e; time.sleep(2 ** k)
    raise RuntimeError(f"GET {url} failed: {last}")

def jload(path, default):
    try: return json.load(open(path))
    except Exception: return default

def jsave(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(obj, open(path, "w"), indent=1, sort_keys=True)

def inv_norm(p):
    from statistics import NormalDist
    return NormalDist().inv_cdf(min(max(p, 1e-6), 1 - 1e-6))

def parse_time(iso):
    """DraftKings/Sleeper UTC timestamps like 2026-11-21T00:00:00.0000000Z."""
    return datetime.fromisoformat(iso.replace("Z", "").split(".")[0]).replace(tzinfo=timezone.utc)

def et_date(iso):
    return (parse_time(iso) - timedelta(hours=5)).strftime("%Y-%m-%d")

# ------------------------------------------------------------------ 1. record slates + lines
def line_medians(lines, sport):
    out = {}
    for ln in lines:
        spec = LINE_STATS.get(ln.get("sport"), {}).get(ln.get("wager_type")) if ln.get("sport") == sport else None
        if not spec or ln.get("outcome_type") != "over_under" or ln.get("status") != "active": continue
        o = {x["outcome"]: x for x in ln.get("options", [])}
        if "over" not in o or "under" not in o: continue
        try: mo, mu = float(o["over"]["payout_multiplier"]), float(o["under"]["payout_multiplier"])
        except Exception: continue
        if mo <= 1 or mu <= 1: continue
        p_over = (1 / mo) / (1 / mo + 1 / mu); L = float(o["over"]["outcome_value"])
        stat, sd = spec
        val = -math.log(1 - min(p_over, .95)) if stat == "td_any" else max(0.0, L + sd(L) * inv_norm(p_over))
        out.setdefault(f"{ln['subject_id']}|{ln['game_id']}", {})[stat] = round(val, 3)
    return out

def record(state, now):
    try: lines = get("https://api.sleeper.app/lines/available?dynamic=true")
    except Exception as e: lines = []; print("lines unavailable:", e)
    for sport in ("nba", "nfl"):
        try: groups = get(f"https://api.draftkings.com/draftgroups/v1/?sport={sport.upper()}")["draftGroups"]
        except Exception as e: print(sport, "slates unavailable:", e); continue
        for g in groups:
            if g.get("contestType", {}).get("contestTypeId") != CONTEST[sport] or (g.get("startTimeSuffix") or "").strip(): continue
            start = parse_time(g["minStartTime"])
            gid = str(g["draftGroupId"])
            if gid in state["pending"] or gid in state["graded"] or not (now < start < now + timedelta(hours=40)): continue
            med = line_medians(lines, sport)
            state["pending"][gid] = {"sport": sport, "start": g["minStartTime"], "end": g.get("maxStartTime", g["minStartTime"]),
                                     "games": [x["description"] for x in g.get("games", [])], "recorded": now.isoformat(timespec="seconds")}
            jsave(os.path.join(OUT, "lines", f"{gid}.json"), med)
            print(f"recorded {sport} main slate {gid} starting {g['minStartTime']} ({len(g.get('games', []))} games, {len(med)} player lines)")

# ------------------------------------------------------------------ 2. grade
def build_players(sport, gid, info):
    dg = get(f"https://api.draftkings.com/draftgroups/v1/draftgroups/{gid}/draftables")["draftables"]
    state = get(f"https://api.sleeper.app/v1/state/{sport}")
    season, week = state["season"], int(state["week"] or 1)
    games = {tuple(sorted(re.split(r" @ | vs ", d))) for d in info["games"]}
    pos = "" if sport == "nba" else "".join(f"&position[]={p}" for p in ("QB", "RB", "WR", "TE", "DEF"))
    best = None
    for w in [week, week - 1, week + 1] + list(range(week - 2, 0, -1)):
        if w < 1: continue
        if w < week - 1 and best and best[0] > 0: break      # older weeks only when recent ones don't match
        proj = [r for r in get(f"https://api.sleeper.com/projections/{sport}/{season}/{w}?season_type=regular{pos}") if r.get("stats") and r.get("player")]
        stats = [r for r in get(f"https://api.sleeper.com/stats/{sport}/{season}/{w}?season_type=regular{pos}") if r.get("stats")]
        if sport == "nfl":
            share = sum(1 for r in proj if r["player"].get("position") == "DEF" and tuple(sorted((r.get("team"), r.get("opponent") or ""))) in games)
        else:
            dates = {et_date(x["competition"]["startTime"]) for x in dg}
            share = sum(1 for r in proj if r.get("date") in dates)
        if not best or share > best[0]: best = (share, proj, stats)
    _, proj, stats = best
    dates = {et_date(x["competition"]["startTime"]) for x in dg} if sport == "nba" else None
    P = {}; A = {}
    for r in proj:
        if dates is not None and r.get("date") not in dates: continue
        P[(B.norm(f"{r['player'].get('first_name','')} {r['player'].get('last_name','')}"), r.get("team"), r["player"].get("position"))] = r
    for r in stats:
        if dates is not None and r.get("date") not in dates: continue
        A[r["player_id"]] = r
    defs = {r["team"]: r for k, r in P.items() if k[2] == "DEF"}
    byname = {(k[0], k[1]): r for k, r in P.items() if k[2] != "DEF"}
    players = {}
    for x in dg:
        if x.get("isDisabled"): continue
        pid = x["playerId"]
        if pid not in players:
            pos_ = x["position"]; team = x["teamAbbreviation"]
            r = defs.get(team) if pos_ == "DST" else byname.get((B.norm(x["displayName"]), team))
            if not r: continue
            a = A.get(r["player_id"])
            if sport == "nba":
                act = nba_act_dk(a["stats"]) if a and nba_min(a["stats"]) > 0 else 0.0
                played = bool(a and nba_min(a["stats"]) > 0)
            else:
                spos = "DEF" if pos_ == "DST" else pos_
                played = bool(a and (spos == "DEF" or float(a["stats"].get("gp") or 0) > 0))
                act = nfl_act_dk(a["stats"], spos) if played else 0.0
            players[pid] = dict(name=x["displayName"], pos=pos_, team=team, opp=r.get("opponent"), salary=x["salary"],
                                status=x.get("status") or "None", stats=r["stats"], sid=r["player_id"], game_id=str(r.get("game_id")),
                                act=act, played=played, elig=[], has_actual=a is not None)
        if sport == "nba" and pid in players and x["rosterSlotId"] in B.NBA_SLOTS: players[pid]["elig"].append(B.NBA_SLOTS[x["rosterSlotId"]])
    return list(players.values())

def dk_from(stats, sport, pos):
    return nba_proj_dk(stats) if sport == "nba" else nfl_proj_dk(stats, "DEF" if pos == "DST" else pos)

def blended(p, sport, med, w):
    mk = med.get(f"{p['sid']}|{p['game_id']}")
    if not mk or w == 0: return dk_from(p["stats"], sport, p["pos"])
    s = dict(p["stats"]); clamp = lambda x: min(1.5, max(0.67, x))
    for k, v in mk.items():
        if k == "pass_int" and p["pos"] != "QB": continue
        if k == "td_any":
            cur = float(s.get("rush_td") or 0) + float(s.get("rec_td") or 0)
            if cur > .02:
                f = clamp(((1 - w) * cur + w * v) / cur)
                for t in ("rush_td", "rec_td"):
                    if s.get(t): s[t] = float(s[t]) * f
            continue
        cur = float(s.get(k) or 0)
        s[k] = cur * clamp(((1 - w) * cur + w * v) / cur) if cur > 0 else w * v
    return dk_from(s, sport, p["pos"])

def grade(gid, info, tuning, rng):
    sport = info["sport"]; B.SPORT = sport
    players = build_players(sport, gid, info)
    if sum(p["has_actual"] for p in players) < 0.5 * len(players): return None     # not final yet
    med = jload(os.path.join(OUT, "lines", f"{gid}.json"), {})
    w_now = tuning["lineWeight"][sport]
    for p in players:
        p["sleeper"] = dk_from(p["stats"], sport, p["pos"])
        p["proj"] = blended(p, sport, med, w_now)
        p["has_line"] = f"{p['sid']}|{p['game_id']}" in med
    pl = [p for p in players if p["played"] and p["sleeper"] >= 5]
    res = dict(gid=gid, sport=sport, date=et_date(info["start"]), games=len(info["games"]), players=len(pl))
    res["mae_sleeper"] = round(st.mean(abs(p["act"] - p["sleeper"]) for p in pl), 3)
    res["mae_app"] = round(st.mean(abs(p["act"] - p["proj"]) for p in pl), 3)
    res["bias"] = round(st.mean(p["act"] - p["sleeper"] for p in pl), 3)
    lp = [p for p in pl if p["has_line"]]
    res["line_players"] = len(lp)
    res["line_mae"] = {str(w): round(sum(abs(p["act"] - blended(p, sport, med, w)) for p in lp), 3) for w in WEIGHTS}
    # residuals by projection bucket (sum, sum of squares, count) for the simulator's spread model
    buckets = collections.defaultdict(lambda: [0, 0.0, 0.0])
    for p in pl:
        if sport == "nfl" and p["pos"] == "DST": continue
        b = int(p["proj"] // 5) * 5; r = p["act"] - p["proj"]
        buckets[b][0] += 1; buckets[b][1] += r; buckets[b][2] += r * r
    res["buckets"] = {str(k): v for k, v in buckets.items()}
    # ---- lineups (same field model as the research backtest)
    P = [dict(p) for p in players if p["status"] not in B.NEVER]
    act = np.array([p["act"] for p in P]); proj = np.array([p["proj"] for p in P])
    base = B.Model(P); stacked = B.Model(P, stack=1, no_def_vs_off=True) if sport == "nfl" else base
    stack2 = B.Model(P, stack=2, no_def_vs_off=True, bringback=True) if sport == "nfl" else base
    field = []
    for k in range(150):
        lu = (stacked if sport == "nfl" and k % 2 else base).solve(proj * np.exp(B.rng.normal(0, .15, len(P))))
        if lu: field.append(lu)
    if len(field) < 50: return res
    fs = np.array([act[list(lu)].sum() for lu in field]); med_f, p99 = np.median(fs), np.percentile(fs, 99)
    cash = base.solve(proj)
    if cash:
        s = act[list(cash)].sum(); res.update(cash_score=round(float(s), 2), cash_line=round(float(med_f), 2), cashed=bool(s > med_f), cash_pct=round(float((fs < s).mean()), 3))
    sims_c = B.simulate(P, 80); cands = set()
    for k in range(80):
        lu = (stack2 if sport == "nba" or k % 3 else stacked).solve(.5 * proj + .5 * sims_c[k])
        if lu: cands.add(lu)
    cands = list(cands); sims = B.simulate(P, 1000)
    fsim = np.stack([sims[:, list(lu)].sum(1) for lu in field], 1); line99 = np.percentile(fsim, 99, axis=1); fmax = fsim.max(1)
    cs = np.stack([sims[:, list(lu)].sum(1) for lu in cands], 1)
    score = (cs >= line99[:, None]).mean(0) + 2 * (cs > fmax[:, None]).mean(0)
    chosen = []
    for c in np.argsort(-score):
        if all(len(cands[c] & ch) <= (6 if sport == "nfl" else 5) for ch in chosen): chosen.append(cands[c])
        if len(chosen) == 20: break
    best = max(act[list(lu)].sum() for lu in chosen)
    res.update(gpp_best=round(float(best), 2), gpp_top1_line=round(float(p99), 2), gpp_top1=bool(best >= p99), gpp_won=bool(best > fs.max()))
    return res

# ------------------------------------------------------------------ 3. tune
def tune(tuning, history, now):
    changes = []
    for sport in ("nba", "nfl"):
        rows = [r for r in history if r["sport"] == sport][-40:]
        # lines blend weight: needs 300+ graded player-games with lines; move one step toward the best weight
        n = sum(r.get("line_players", 0) for r in rows)
        if n >= 300:
            mae = {w: sum(r["line_mae"][str(w)] for r in rows if r.get("line_mae")) / n for w in WEIGHTS}
            best = min(mae, key=mae.get); cur = tuning["lineWeight"][sport]
            if best != cur and mae[best] < mae.get(cur, mae[0.5]) * 0.99:
                new = cur + (0.25 if best > cur else -0.25)
                changes.append(f"{sport}: lines blend weight {cur} -> {new} (MAE {mae.get(cur, 0):.2f} -> best {best} at {mae[best]:.2f} over {n} player-games)")
                tuning["lineWeight"][sport] = new
        # simulator spread: refit sd = a + b * projection when 1,500+ player-games; change only if off by 10%+
        agg = collections.defaultdict(lambda: [0, 0.0, 0.0])
        for r in rows:
            for k, (c, s1, s2) in r.get("buckets", {}).items():
                agg[int(k)][0] += c; agg[int(k)][1] += s1; agg[int(k)][2] += s2
        pts = [(k + 2.5, math.sqrt(max(s2 / c - (s1 / c) ** 2, 0)), c) for k, (c, s1, s2) in agg.items() if c >= 40 and k >= 5]
        if sum(c for *_, c in pts) >= 1500 and len(pts) >= 3:
            X = np.array([[1, x] for x, _, _ in pts]); y = np.array([s for _, s, _ in pts]); W = np.diag([c for *_, c in pts])
            a, b = np.linalg.solve(X.T @ W @ X, X.T @ W @ y)
            a, b = float(np.clip(a, 0, 15)), float(np.clip(b, .05, .8))
            ca, cb = tuning["sd"][sport]
            mid = 25 if sport == "nba" else 12
            if abs((a + b * mid) - (ca + cb * mid)) / (ca + cb * mid) > 0.10:
                changes.append(f"{sport}: simulator spread {ca}+{cb}*proj -> {a:.2f}+{b:.3f}*proj (fit on {sum(c for *_, c in pts)} player-games)")
                tuning["sd"][sport] = [round(a, 2), round(b, 3)]
    if changes: tuning["updated"] = now.strftime("%Y-%m-%d")
    return changes

def report(history, changes, now):
    lines = [f"# Nightly check - {now.strftime('%Y-%m-%d %H:%M')} UTC", ""]
    for sport in ("nba", "nfl"):
        rows = [r for r in history if r["sport"] == sport]
        if not rows: lines.append(f"## {sport.upper()}\nNo graded slates yet.\n"); continue
        last = rows[-30:]
        cash = [r for r in last if "cashed" in r]; g = [r for r in last if "gpp_top1" in r]
        lines += [f"## {sport.upper()} (last {len(last)} graded slates)",
                  f"- Projection error (avg points off per player): Sleeper {st.mean(r['mae_sleeper'] for r in last):.2f}, app {st.mean(r['mae_app'] for r in last):.2f}",
                  f"- Cash lineup beat the simulated field median: {sum(r['cashed'] for r in cash)}/{len(cash)}" if cash else "- Cash: n/a",
                  f"- Tournament set reached the top 1%: {sum(r['gpp_top1'] for r in g)}/{len(g)}, won {sum(r['gpp_won'] for r in g)}/{len(g)}" if g else "- Tournament: n/a",
                  f"- Most recent: {rows[-1]['date']} - Sleeper MAE {rows[-1]['mae_sleeper']}, app MAE {rows[-1]['mae_app']}, "
                  f"cash {'yes' if rows[-1].get('cashed') else 'no'}, top 1% {'yes' if rows[-1].get('gpp_top1') else 'no'}", ""]
    lines += ["## Changes tonight", *(f"- {c}" for c in changes)] if changes else ["## Changes tonight", "- None"]
    open(os.path.join(OUT, "report.md"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))

def main():
    now = datetime.now(timezone.utc)
    os.makedirs(OUT, exist_ok=True)
    state = jload(os.path.join(OUT, "state.json"), {"pending": {}, "graded": {}})
    tuning = jload(TUNING, {"lineWeight": {"nba": .5, "nfl": .5}, "sd": {"nba": [4, .2], "nfl": [2, .45], "nflDst": 5.2}})
    hist_path = os.path.join(OUT, "history.json")
    history = jload(hist_path, [])
    if "--seed" in sys.argv:
        # One-time backfill: grade past main slates (given as gid:sport,...) without line snapshots.
        for item in sys.argv[sys.argv.index("--seed") + 1].split(","):
            gid, sport = item.split(":")
            if gid in state["graded"]: continue
            g = get(f"https://api.draftkings.com/draftgroups/v1/{gid}")["draftGroup"]
            info = {"sport": sport, "start": g["minStartTime"], "end": g["maxStartTime"], "games": [x["description"] for x in g["games"]]}
            try: res = grade(gid, info, tuning, None)
            except Exception as e: print(f"seed {gid} failed: {e}"); continue
            if res: history.append(res); state["graded"][gid] = res["date"]; print(f"seeded {sport} {res['date']}: cashed {res.get('cashed')}, top1 {res.get('gpp_top1')}")
            jsave(os.path.join(OUT, "state.json"), state); jsave(hist_path, history)
        history.sort(key=lambda r: r["date"])
    record(state, now)
    if "--no-grade" not in sys.argv and "--seed" not in sys.argv:
        for gid, info in sorted(state["pending"].items(), key=lambda kv: kv[1]["start"]):
            end = parse_time(info["end"])
            if now < end + timedelta(hours=5): continue
            try:
                res = grade(gid, info, tuning, None)
            except Exception as e:
                print(f"grading {gid} failed: {e}"); res = None
            if res is None:
                if now > end + timedelta(days=3): print(f"dropping {gid}: no final stats after 3 days"); state["pending"].pop(gid)
                continue
            history.append(res); state["graded"][gid] = res["date"]; state["pending"].pop(gid)
            print(f"graded {res['sport']} {res['date']}: Sleeper MAE {res['mae_sleeper']}, app MAE {res['mae_app']}, "
                  f"cashed {res.get('cashed')}, top1 {res.get('gpp_top1')}")
    changes = tune(tuning, history, now)
    if changes:
        jsave(TUNING, tuning)
        with open(os.path.join(OUT, "changes.md"), "a") as f:
            for c in changes: f.write(f"- {now.strftime('%Y-%m-%d')}: {c}\n")
    jsave(os.path.join(OUT, "state.json"), state); jsave(hist_path, history)
    report(history, changes, now)

if __name__ == "__main__":
    main()

"""Command line: python -m dkopt [options]"""
import argparse
import csv
import json
import os
import sys
from datetime import datetime

from . import draftkings as dk
from . import sleeper
from .matching import attach_projections, norm
from .optimizer import optimize_classic, optimize_showdown
from .scoring import dk_points

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXCLUDED_STATUS = {"OUT", "IR", "D"}  # DK statuses: Out, Injured Reserve, Doubtful
RANK_POSITIONS = ["QB", "RB", "WR", "TE", "DST", "K"]


def load_config():
    path = os.path.join(ROOT, "config.json")
    return json.load(open(path)) if os.path.exists(path) else {}


def parse_args(cfg):
    ap = argparse.ArgumentParser(prog="dkopt", description="DraftKings NFL optimizer with Sleeper projections")
    ap.add_argument("--league", default=cfg.get("league_id"), help="Sleeper league ID")
    ap.add_argument("--scoring", choices=["league", "dk"], default=cfg.get("scoring", "dk"),
                    help="dk (default) = Sleeper projected stats scored with DraftKings rules; "
                         "league = Sleeper Leaders > Projections numbers (your league's scoring)")
    ap.add_argument("--type", choices=["classic", "showdown"], default="classic")
    ap.add_argument("--slate", help="DraftKings draft group ID (default: main slate)")
    ap.add_argument("--list-slates", action="store_true", help="List open slates and exit")
    ap.add_argument("--week", type=int, help="NFL week (default: current week from Sleeper)")
    ap.add_argument("--rankings", type=int, default=10, metavar="N",
                    help="Show the top N projected players per position (0 to hide)")
    ap.add_argument("--pos", help="Only show rankings for this position, e.g. WR")
    ap.add_argument("--rankings-only", action="store_true", help="Show rankings without building lineups")
    ap.add_argument("-n", "--lineups", type=int, default=1)
    ap.add_argument("--stack", type=int, default=1, help="WR/TE teammates required with QB (classic)")
    ap.add_argument("--bringback", action="store_true", help="Require 1 opponent of your QB (classic)")
    ap.add_argument("--allow-def-vs-off", action="store_true", help="Allow DST against your own players")
    ap.add_argument("--min-unique", type=int, default=2, help="Players that must differ between lineups")
    ap.add_argument("--max-exposure", type=float, default=1.0, help="Max share of lineups per player, 0-1")
    ap.add_argument("--min-salary", type=int, default=0)
    ap.add_argument("--lock", action="append", default=[], help="Player name to force in (repeatable)")
    ap.add_argument("--exclude", action="append", default=[], help="Player name to leave out (repeatable)")
    return ap.parse_args()


def print_rankings(players, n, only_pos=None):
    """Like Sleeper's Players > Leaders > Projections, limited to players on this DK slate,
    with salary and value (projected points per $1,000) added."""
    rows = [p for p in players if p.get("slot", "FLEX") != "CPT" and p["raw_proj"] > 0]
    want = only_pos.upper().replace("DEF", "DST") if only_pos else None
    for pos in RANK_POSITIONS:
        if want and pos != want:
            continue
        group = sorted([p for p in rows if p["position"] == pos], key=lambda p: -p["raw_proj"])[:n]
        if not group:
            continue
        print(f"\n{pos} - top {len(group)} projected")
        print(f"  {'#':>2}  {'Player':<26} {'Team':<4} {'Game':<10} {'Proj':>6} {'Salary':>7} {'Pts/$1K':>7}  Status")
        for i, p in enumerate(group, 1):
            flag = "" if p["status"] == "None" else p["status"] + (" (skipped)" if p["status"] in EXCLUDED_STATUS else "")
            print(f"  {i:>2}  {p['name']:<26} {p['team']:<4} {p['game']:<10} {p['raw_proj']:6.2f} "
                  f"${p['salary']:>6,} {1000 * p['raw_proj'] / p['salary']:7.2f}  {flag}")


def write_rankings_csv(players, path):
    order = lambda p: (RANK_POSITIONS.index(p["position"]) if p["position"] in RANK_POSITIONS else 9, -p["raw_proj"])
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["pos_rank", "name", "position", "slot", "team", "game", "salary", "status",
                    "league_proj", "dk_proj", "pts_per_1k", "draftable_id"])
        rank, last = 0, None
        for p in sorted(players, key=order):
            rank = rank + 1 if (p["position"], p.get("slot")) == last else 1
            last = (p["position"], p.get("slot"))
            w.writerow([rank, p["name"], p["position"], p.get("slot", ""), p["team"], p["game"], p["salary"],
                        p["status"], p["league_pts"], round(p["dk_pts"], 2),
                        round(1000 * p["raw_proj"] / p["salary"], 2), p["draftable_id"]])


def main():
    cfg = load_config()
    args = parse_args(cfg)

    if args.list_slates:
        for s in dk.slates(args.type):
            print(f"{s['id']:>8}  {s['start'][:16]}  {s['games']:>2} games  {s['label']}{'  *featured' if s['featured'] else ''}")
        return
    if args.scoring == "league" and not args.league:
        sys.exit("--scoring league needs a Sleeper league ID (--league or config.json)")

    state = sleeper.nfl_state()
    season, week = state["season"], args.week or state["week"]
    lg = sleeper.league(args.league) if args.league else None

    slate = {"id": int(args.slate), "label": f"draft group {args.slate}"} if args.slate else dk.pick_main_slate(args.type)
    rules = dk.rules(args.type)
    cap = rules["salary_cap"]
    src = (f"Sleeper Leaders > Projections, scored with '{lg['name'].strip()}' settings"
           if args.scoring == "league" else "Sleeper projected stats, rescored with DraftKings rules")
    print(f"Projections: {season} week {week} - {src}")
    print(f"DraftKings {rules['name']}: slate {slate['id']} ({slate.get('label')}), "
          f"salary cap ${cap:,}, roster {' '.join(rules['slots'])}")

    players = dk.players(slate["id"], args.type)
    proj = sleeper.projections(season, week)
    unmatched = attach_projections(players, proj)

    locks = {norm(n) for n in args.lock}
    excludes = {norm(n) for n in args.exclude}
    for p in players:
        pos = "DEF" if p["position"] == "DST" else p["position"]
        p["league_pts"] = sleeper.league_points(p["stats"], lg["scoring_settings"]) if lg and p["stats"] else 0.0
        p["dk_pts"] = dk_points(p["stats"], pos) if p["stats"] else 0.0
        p["raw_proj"] = p["league_pts"] if args.scoring == "league" else p["dk_pts"]
        p["proj"] = p["raw_proj"]
        n = norm(p["name"])
        p["locked"] = n in locks
        if n in excludes or (p["status"] in EXCLUDED_STATUS and not p["locked"]):
            p["proj"] = 0.0

    relevant = [p for p in unmatched if p["salary"] >= 4000 and p["status"] not in EXCLUDED_STATUS]
    if relevant:
        print(f"Note: no Sleeper projection for {len(relevant)} notable player(s): "
              + ", ".join(f"{p['name']} ({p['team']})" for p in relevant[:10]))

    if args.rankings or args.rankings_only:
        print_rankings(players, args.rankings or 25, args.pos)

    out_dir = os.path.join(ROOT, "output")
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    rankfile = os.path.join(out_dir, f"rankings_{args.type}_wk{week}_{stamp}.csv")
    write_rankings_csv(players, rankfile)

    if args.rankings_only:
        print(f"\nFull rankings: {rankfile}")
        return

    missing_locks = locks - {norm(p["name"]) for p in players}
    if missing_locks:
        sys.exit(f"Locked player(s) not on this slate: {', '.join(missing_locks)}")

    if args.type == "classic":
        lineups = optimize_classic(players, cap, n=args.lineups, stack=args.stack, bringback=args.bringback,
                                   no_def_vs_off=not args.allow_def_vs_off, min_unique=args.min_unique,
                                   max_exposure=args.max_exposure, min_salary=args.min_salary)
    else:
        lineups = optimize_showdown(players, cap, n=args.lineups, min_unique=max(1, args.min_unique - 1),
                                    max_exposure=args.max_exposure)
    if not lineups:
        sys.exit("No valid lineup found - try loosening locks or stack settings.")

    for k, lu in enumerate(lineups, 1):
        print(f"\nLineup {k}: {lu['proj']:.2f} projected pts, salary ${lu['salary']:,} / ${cap:,}")
        for slot, p in zip(lu["slots"], lu["players"]):
            status = f" ({p['status']})" if p["status"] != "None" else ""
            print(f"  {slot:<4} {p['name'] + status:<28} {p['team']:<4} ${p['salary']:>6,}  {p['proj']:6.2f}")

    upload = os.path.join(out_dir, f"dk_upload_{args.type}_wk{week}_{stamp}.csv")
    with open(upload, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(lineups[0]["slots"])
        for lu in lineups:
            w.writerow([p["draftable_id"] for p in lu["players"]])
    print(f"\nDraftKings upload CSV: {upload}\nFull rankings:         {rankfile}")


if __name__ == "__main__":
    main()

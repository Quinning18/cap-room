"""Integer-programming lineup optimizer for DraftKings NFL Classic and Showdown."""
import math
import pulp

FLEX_POS = ("RB", "WR", "TE")


def _solver():
    return pulp.HiGHS(msg=False) if pulp.HiGHS().available() else pulp.PULP_CBC_CMD(msg=False)


def _binary(prob, name):
    # PuLP 4 creates variables through the problem; older versions use LpVariable.
    if hasattr(prob, "add_variable"):
        return prob.add_variable(name, cat="Binary")
    return pulp.LpVariable(name, cat="Binary")


def _optimal(result):
    # PuLP 4 returns a stats object with .status; older versions return an int (1 = optimal).
    return int(getattr(result, "status", result)) == 1


def _opponent(p):
    teams = p["game"].replace(" vs ", " @ ").split(" @ ")
    return next((t for t in teams if t != p["team"]), None) if len(teams) == 2 else None


def optimize_classic(players, cap, n=1, stack=1, bringback=False, no_def_vs_off=True,
                     min_unique=2, max_exposure=1.0, min_salary=0):
    pool = [p for p in players if p["proj"] > 0]
    exposure_limit = max(1, math.ceil(n * max_exposure))
    counts = {}
    lineups = []
    for _ in range(n):
        prob = pulp.LpProblem("dk_classic", pulp.LpMaximize)
        x = {i: _binary(prob, f"x{i}") for i in range(len(pool))}
        by = lambda pred: [x[i] for i, p in enumerate(pool) if pred(p)]
        prob += pulp.lpSum(pool[i]["proj"] * x[i] for i in x)
        prob += pulp.lpSum(pool[i]["salary"] * x[i] for i in x) <= cap
        if min_salary:
            prob += pulp.lpSum(pool[i]["salary"] * x[i] for i in x) >= min_salary
        prob += pulp.lpSum(x.values()) == 9
        prob += pulp.lpSum(by(lambda p: p["position"] == "QB")) == 1
        prob += pulp.lpSum(by(lambda p: p["position"] == "DST")) == 1
        prob += pulp.lpSum(by(lambda p: p["position"] == "RB")) >= 2
        prob += pulp.lpSum(by(lambda p: p["position"] == "WR")) >= 3
        prob += pulp.lpSum(by(lambda p: p["position"] == "TE")) >= 1
        prob += pulp.lpSum(by(lambda p: p["position"] == "TE")) <= 2

        for i, p in enumerate(pool):
            if p["position"] == "QB":
                team, opp = p["team"], _opponent(p)
                if stack:
                    mates = by(lambda q: q["team"] == team and q["position"] in ("WR", "TE"))
                    prob += pulp.lpSum(mates) >= stack * x[i]
                if bringback and opp:
                    opps = by(lambda q: q["team"] == opp and q["position"] in FLEX_POS)
                    prob += pulp.lpSum(opps) >= x[i]
            if p["position"] == "DST" and no_def_vs_off:
                opp = _opponent(p)
                for j, q in enumerate(pool):
                    if q["team"] == opp and q["position"] != "DST":
                        prob += x[i] + x[j] <= 1
            if p.get("locked"):
                prob += x[i] == 1
            if counts.get(p["dk_player_id"], 0) >= exposure_limit and not p.get("locked"):
                prob += x[i] == 0
        for lu in lineups:
            ids = {q["dk_player_id"] for q in lu["players"]}
            prob += pulp.lpSum(x[i] for i, p in enumerate(pool) if p["dk_player_id"] in ids) <= 9 - min_unique

        if not _optimal(prob.solve(_solver())):
            break
        chosen = [pool[i] for i in x if x[i].value() > 0.5]
        for p in chosen:
            counts[p["dk_player_id"]] = counts.get(p["dk_player_id"], 0) + 1
        lineups.append(_order_classic(chosen))
    return lineups


def _order_classic(chosen):
    """Arrange into QB, RB, RB, WR, WR, WR, TE, FLEX, DST. The extra RB/WR/TE goes in
    FLEX, using the latest-starting player of that position (best for late swap)."""
    need = {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "DST": 1}
    by_pos = {}
    for p in sorted(chosen, key=lambda p: p["start"]):
        by_pos.setdefault(p["position"], []).append(p)
    extra = next(pos for pos in FLEX_POS if len(by_pos.get(pos, [])) > need[pos])
    flex = by_pos[extra].pop()
    order = [(pos, p) for pos in ("QB", "RB", "WR", "TE") for p in by_pos[pos]]
    order += [("FLEX", flex), ("DST", by_pos["DST"][0])]
    return _summary(order)


def optimize_showdown(players, cap, n=1, min_unique=1, max_exposure=1.0):
    pool = [dict(p, proj=p["proj"] * (1.5 if p["slot"] == "CPT" else 1)) for p in players if p["proj"] > 0]
    exposure_limit = max(1, math.ceil(n * max_exposure))
    counts, lineups = {}, []
    teams = sorted({p["team"] for p in pool})
    for _ in range(n):
        prob = pulp.LpProblem("dk_showdown", pulp.LpMaximize)
        x = {i: _binary(prob, f"x{i}") for i in range(len(pool))}
        prob += pulp.lpSum(pool[i]["proj"] * x[i] for i in x)
        prob += pulp.lpSum(pool[i]["salary"] * x[i] for i in x) <= cap
        prob += pulp.lpSum(x[i] for i, p in enumerate(pool) if p["slot"] == "CPT") == 1
        prob += pulp.lpSum(x[i] for i, p in enumerate(pool) if p["slot"] == "FLEX") == 5
        for pid in {p["dk_player_id"] for p in pool}:
            prob += pulp.lpSum(x[i] for i, p in enumerate(pool) if p["dk_player_id"] == pid) <= 1
        for t in teams:  # DK requires players from both teams
            prob += pulp.lpSum(x[i] for i, p in enumerate(pool) if p["team"] == t) >= 1
        for i, p in enumerate(pool):
            if p.get("locked"):
                prob += pulp.lpSum(x[j] for j, q in enumerate(pool) if q["dk_player_id"] == p["dk_player_id"]) == 1
            if counts.get(p["dk_player_id"], 0) >= exposure_limit and not p.get("locked"):
                prob += x[i] == 0
        for lu in lineups:
            keys = {(q["dk_player_id"], q["slot"]) for q in lu["players"]}
            prob += pulp.lpSum(x[i] for i, p in enumerate(pool) if (p["dk_player_id"], p["slot"]) in keys) <= 6 - min_unique
        if not _optimal(prob.solve(_solver())):
            break
        chosen = sorted([pool[i] for i in x if x[i].value() > 0.5], key=lambda p: (p["slot"] != "CPT", -p["salary"]))
        for p in chosen:
            counts[p["dk_player_id"]] = counts.get(p["dk_player_id"], 0) + 1
        lineups.append(_summary([(p["slot"], p) for p in chosen]))
    return lineups


def _summary(order):
    return {
        "slots": [s for s, _ in order],
        "players": [p for _, p in order],
        "salary": sum(p["salary"] for _, p in order),
        "proj": sum(p["proj"] for _, p in order),
    }

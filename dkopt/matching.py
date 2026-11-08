"""Match DraftKings players to Sleeper projections by name + team + position."""
import re
import unicodedata

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
NICKNAMES = {"hollywood": "marquise", "chig": "chigoziem", "gabe": "gabriel", "josh": "joshua"}


def norm(name):
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z ]", "", s.replace("-", " "))
    parts = [p for p in s.split() if p not in SUFFIXES]
    return " ".join(parts)


def _keys(name):
    n = norm(name)
    parts = n.split()
    keys = {n}
    if parts and parts[0] in NICKNAMES:
        keys.add(" ".join([NICKNAMES[parts[0]]] + parts[1:]))
    return keys


def attach_projections(dk_players, sleeper_proj):
    """Sets 'sleeper_id' and 'stats' on each DK player. Returns list of unmatched."""
    by_team = {}
    by_name = {}
    defenses = {}
    for pid, rec in sleeper_proj.items():
        pl = rec["player"]
        pos = pl.get("position")
        team = rec.get("team") or pl.get("team")
        if pos == "DEF":
            defenses[team] = pid
            continue
        full = f"{pl.get('first_name', '')} {pl.get('last_name', '')}"
        for k in _keys(full):
            by_team[(k, team)] = pid
            by_name.setdefault(k, []).append(pid)

    unmatched = []
    for p in dk_players:
        pid = None
        if p["position"] == "DST":
            pid = defenses.get(p["team"])
        else:
            for k in _keys(p["name"]):
                pid = by_team.get((k, p["team"]))
                if not pid and len(by_name.get(k, [])) == 1:
                    pid = by_name[k][0]
                if pid:
                    break
        p["sleeper_id"] = pid
        p["stats"] = sleeper_proj[pid]["stats"] if pid else {}
        if not pid:
            unmatched.append(p)
    return unmatched

"""DraftKings (unofficial) endpoints: NFL slates, salaries and contest rules."""
from .http import get_json

LOBBY = "https://www.draftkings.com/lobby/getcontests"
DRAFTABLES = "https://api.draftkings.com/draftgroups/v1/draftgroups/{}/draftables"
RULES = "https://api.draftkings.com/lineups/v1/gametypes/{}/rules"

GAME_TYPES = {"classic": 1, "showdown": 96}
CPT_SLOT_ID = 511  # showdown captain roster slot (scores 1.5x, costs 1.5x)


def slates(contest_type="classic"):
    """List NFL draft groups (slates) for a contest type, earliest first."""
    gt = GAME_TYPES[contest_type]
    data = get_json(LOBBY, params={"sport": "NFL"})
    groups = [g for g in data.get("DraftGroups", []) if g.get("GameTypeId") == gt]
    out = [{
        "id": g["DraftGroupId"],
        "games": g.get("GameCount"),
        "start": g.get("StartDateEst"),
        "label": (g.get("ContestStartTimeSuffix") or "").strip() or "Main",
        "featured": g.get("DraftGroupTag") == "Featured",
    } for g in groups]
    return sorted(out, key=lambda s: s["start"])


def pick_main_slate(contest_type="classic"):
    """Pick the main slate: the featured slate with the most games starting soonest."""
    ss = slates(contest_type)
    if not ss:
        raise RuntimeError(f"No DraftKings NFL {contest_type} slates are open right now.")
    if contest_type == "classic":
        nxt = ss[0]["start"][:10]
        same_day = [s for s in ss if s["start"][:10] == nxt and s["label"] == "Main"] or ss
        return max(same_day, key=lambda s: s["games"])
    featured = [s for s in ss if s["featured"]] or ss
    return featured[0]


def rules(contest_type="classic"):
    """Salary cap and roster slots straight from DraftKings."""
    r = get_json(RULES.format(GAME_TYPES[contest_type]))
    slots = [t["rosterSlot"]["name"] for t in sorted(r["lineupTemplate"], key=lambda t: t["order"])]
    return {"salary_cap": r["salaryCap"]["maxValue"], "slots": slots, "name": r["gameTypeName"]}


def players(draft_group_id, contest_type="classic"):
    """One row per player with salary/position/team/game. For showdown, CPT and FLEX
    entries are kept separately (CPT costs 1.5x)."""
    data = get_json(DRAFTABLES.format(draft_group_id))
    rows = {}
    for d in data["draftables"]:
        if d.get("isDisabled"):
            continue
        comp = d.get("competition") or {}
        row = {
            "draftable_id": d["draftableId"],
            "dk_player_id": d["playerId"],
            "name": d["displayName"],
            "position": d["position"],
            "team": d["teamAbbreviation"],
            "salary": d["salary"],
            "status": d.get("status") or "None",
            "game": comp.get("name", ""),
            "start": comp.get("startTime", ""),
            "roster_slot_id": d["rosterSlotId"],
        }
        if contest_type == "classic":
            # Each player appears once per eligible slot (e.g. RB and FLEX); keep one.
            rows.setdefault(d["playerId"], row)
        else:
            rows[d["draftableId"]] = row
    out = list(rows.values())
    if contest_type == "showdown":
        for r in out:
            r["slot"] = "CPT" if r["roster_slot_id"] == CPT_SLOT_ID else "FLEX"
    return out

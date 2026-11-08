"""Convert Sleeper projected stat lines into DraftKings NFL fantasy points.

DraftKings scoring:
  Pass: 0.04/yd, 4/TD, -1/INT, +3 bonus at 300 yds, 2/2pt
  Rush: 0.1/yd, 6/TD, +3 bonus at 100 yds, 2/2pt
  Rec:  1/rec, 0.1/yd, 6/TD, +3 bonus at 100 yds, 2/2pt
  Misc: -1/fumble lost, 6/return TD, 6/offensive fumble-recovery TD
  DST:  1/sack, 2/INT, 2/fum rec, 6/TD, 2/safety, 2/blocked kick,
        points allowed 0:+10, 1-6:+7, 7-13:+4, 14-20:+1, 21-27:0, 28-34:-1, 35+:-4
  K (showdown): 1/XP, 3 for 0-39, 4 for 40-49, 5 for 50+ yd FG

Projections are averages, so yardage bonuses are scored as 3 x the chance of
reaching the threshold (normal approximation) rather than all-or-nothing.
"""
import math


def _p_at_least(mean, threshold, sd):
    if mean <= 0:
        return 0.0
    z = (threshold - mean) / sd
    return 0.5 * math.erfc(z / math.sqrt(2))


def _bonus(mean, threshold, sd):
    return 3.0 * _p_at_least(mean, threshold, sd)


PTS_ALLOWED = [(0, 0, 10), (1, 6, 7), (7, 13, 4), (14, 20, 1), (21, 27, 0), (28, 34, -1), (35, 99, -4)]


def _dst_points_allowed(mean, sd=9.0):
    """Expected DK points-allowed score given projected points allowed."""
    total = 0.0
    for lo, hi, pts in PTS_ALLOWED:
        p = _p_at_least(mean, lo - 0.5, sd) - _p_at_least(mean, hi + 0.5, sd)
        total += p * pts
    return total


def dk_points(stats, position):
    g = lambda k: float(stats.get(k) or 0)
    if position in ("DEF", "DST"):
        pts = (g("sack") + 2 * g("int") + 2 * g("fum_rec") + 2 * g("safe") + 2 * g("blk_kick")
               + 6 * (g("def_td") + g("def_st_td") + g("st_td") + g("def_kr_td") + g("def_pr_td")))
        if "pts_allow" in stats:
            pts += _dst_points_allowed(g("pts_allow"))
        return pts
    if position == "K":
        return (g("xpm") + 3 * (g("fgm_0_19") + g("fgm_20_29") + g("fgm_30_39"))
                + 4 * g("fgm_40_49") + 5 * g("fgm_50p"))

    pass_yd, rush_yd, rec_yd = g("pass_yd"), g("rush_yd"), g("rec_yd")
    pts = (0.04 * pass_yd + 4 * g("pass_td") - g("pass_int")
           + 0.1 * rush_yd + 6 * g("rush_td")
           + g("rec") + 0.1 * rec_yd + 6 * g("rec_td")
           + 2 * (g("pass_2pt") + g("rush_2pt") + g("rec_2pt"))
           - g("fum_lost")
           + 6 * (g("kr_td") + g("pr_td") + g("fum_rec_td")))
    pts += _bonus(pass_yd, 300, 65)
    pts += _bonus(rush_yd, 100, max(15.0, 0.45 * rush_yd))
    pts += _bonus(rec_yd, 100, max(15.0, 0.45 * rec_yd))
    return pts

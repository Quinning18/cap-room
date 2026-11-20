"""Shared backtest data: Sleeper projections/actuals and DraftKings scoring for NFL and NBA."""
import json, math, collections, statistics as st
BT = '/tmp/claude-0/-home-user/5a5edca1-a3c7-5afc-b509-15c6f255902f/scratchpad/bt'

def erfc_p(mean, thr, sd):
    return 0 if mean <= 0 else 0.5 * math.erfc((thr - mean) / sd / math.sqrt(2))

PA = [(0, 0, 10), (1, 6, 7), (7, 13, 4), (14, 20, 1), (21, 27, 0), (28, 34, -1), (35, 99, -4)]
def nfl_proj_dk(s, pos):
    g = lambda k: float(s.get(k) or 0)
    if pos == 'DEF':
        pts = g('sack') + 2*g('int') + 2*g('fum_rec') + 2*g('safe') + 2*g('blk_kick') + 6*(g('def_td')+g('def_st_td')+g('st_td')+g('def_kr_td')+g('def_pr_td'))
        if 'pts_allow' in s:
            m = g('pts_allow'); pts += sum(v*(erfc_p(m, lo-.5, 9)-erfc_p(m, hi+.5, 9)) for lo, hi, v in PA)
        return pts
    py, ry, rcy = g('pass_yd'), g('rush_yd'), g('rec_yd')
    return (0.04*py + 4*g('pass_td') - g('pass_int') + 0.1*ry + 6*g('rush_td') + g('rec') + 0.1*rcy + 6*g('rec_td')
            + 2*(g('pass_2pt')+g('rush_2pt')+g('rec_2pt')) - g('fum_lost') + 6*(g('kr_td')+g('pr_td')+g('fum_rec_td'))
            + 3*erfc_p(py, 300, 65) + 3*erfc_p(ry, 100, max(15, .45*ry)) + 3*erfc_p(rcy, 100, max(15, .45*rcy)))

def nfl_act_dk(s, pos):
    g = lambda k: float(s.get(k) or 0)
    if pos == 'DEF':
        pts = g('sack') + 2*g('int') + 2*g('fum_rec') + 2*g('safe') + 2*g('blk_kick') + 6*(g('def_td')+g('def_st_td')+g('st_td')+g('def_kr_td')+g('def_pr_td')+g('fum_rec_td')*0)
        pa = g('pts_allow')
        pts += next(v for lo, hi, v in PA if lo <= pa <= hi or (lo == 35 and pa >= 35))
        return pts
    py, ry, rcy = g('pass_yd'), g('rush_yd'), g('rec_yd')
    return (0.04*py + 4*g('pass_td') - g('pass_int') + 0.1*ry + 6*g('rush_td') + g('rec') + 0.1*rcy + 6*g('rec_td')
            + 2*(g('pass_2pt')+g('rush_2pt')+g('rec_2pt')) - g('fum_lost') + 6*(g('kr_td')+g('pr_td')+g('fum_rec_td'))
            + 3*(py >= 300) + 3*(ry >= 100) + 3*(rcy >= 100))

def nba_proj_dk(s):
    g = lambda k: float(s.get(k) or 0)
    return g('pts')+0.5*g('tpm')+1.25*g('reb')+1.5*g('ast')+2*g('stl')+2*g('blk')-0.5*g('to')+1.5*g('dd')+3*g('td')
def nba_act_dk(s):
    g = lambda k: float(s.get(k) or 0)
    cats = sum(1 for k in ('pts', 'reb', 'ast', 'stl', 'blk') if g(k) >= 10)
    return g('pts')+0.5*g('tpm')+1.25*g('reb')+1.5*g('ast')+2*g('stl')+2*g('blk')-0.5*g('to')+1.5*(cats >= 2)+3*(cats >= 3)
nba_min = lambda s: float(s.get('min') or 0) or float(s.get('sp') or 0)/60

def load_nfl():
    """Returns {week: {pid: dict(pos, team, opp, name, proj, act)}} for weeks with actuals."""
    out = {}
    for w in range(1, 11):
        P = {r['player_id']: r for r in json.load(open(f'{BT}/nfl_proj_w{w}.json')) if r.get('stats') and r.get('player')}
        A = {r['player_id']: r for r in json.load(open(f'{BT}/nfl_stats_w{w}.json')) if r.get('stats')}
        wk = {}
        for pid, p in P.items():
            pos = p['player']['position']
            if pos not in ('QB', 'RB', 'WR', 'TE', 'DEF'): continue
            pr = nfl_proj_dk(p['stats'], pos)
            a = A.get(pid)
            act = nfl_act_dk(a['stats'], pos) if a and float(a['stats'].get('gp') or (1 if pos=='DEF' else 0)) > 0 else None
            wk[pid] = dict(pos=pos, team=p.get('team'), opp=p.get('opponent'), name=f"{p['player'].get('first_name','')} {p['player'].get('last_name','')}",
                           proj=pr, act=act, pid=pid)
        out[w] = wk
    return out

def load_nba(today='2026-11-20'):
    """Returns {date: {pid: dict(team, opp, name, proj, act, pmin, amin, pos)}}."""
    P = {}; A = {}
    for w in range(1, 6):
        for r in json.load(open(f'{BT}/nba_proj_w{w}.json')):
            if r.get('stats') and r.get('date') and r['date'] < today: P[(r['player_id'], r['date'])] = r
        for r in json.load(open(f'{BT}/nba_stats_w{w}.json')):
            if r.get('date') and r['date'] < today and r.get('stats') and nba_min(r['stats']) > 0: A[(r['player_id'], r['date'])] = r
    out = collections.defaultdict(dict)
    for (pid, d), p in P.items():
        a = A.get((pid, d))
        out[d][pid] = dict(team=p['team'], opp=p.get('opponent'), name=f"{p['player'].get('first_name','')} {p['player'].get('last_name','')}",
                           proj=nba_proj_dk(p['stats']), pmin=float(p['stats'].get('sp') or 0)/60,
                           act=nba_act_dk(a['stats']) if a else None, amin=nba_min(a['stats']) if a else 0, pid=pid,
                           pos=p['player'].get('fantasy_positions') or [])
    # actual-only players (no projection) - needed for season averages
    acts = collections.defaultdict(list)
    for (pid, d), a in A.items(): acts[pid].append((d, nba_act_dk(a['stats']), nba_min(a['stats'])))
    for v in acts.values(): v.sort()
    return out, acts

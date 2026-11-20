"""Backtest lineup strategies on past DraftKings main slates using Sleeper projections and actual results.

Field model: 300 lineups optimized on noisy Sleeper projections (half stacked in NFL) stand in for opponents.
Cash = beat the field median. GPP = reach the field's 99th percentile / beat the whole field.
"""
import sys, json, math, re, unicodedata, collections, statistics as st, random
import numpy as np, highspy
sys.path.insert(0, sys.argv[1]); from data import load_nfl, load_nba, BT
SPORT = sys.argv[2]
rng = np.random.default_rng(7)

SUF = {'jr', 'sr', 'ii', 'iii', 'iv', 'v'}
def norm(n):
    s = unicodedata.normalize('NFKD', n).encode('ascii', 'ignore').decode().lower().replace('-', ' ')
    return ' '.join(p for p in re.sub(r'[^a-z ]', '', s).split() if p not in SUF)

NBA_SLOTS = {458: 'PG', 459: 'SG', 460: 'SF', 461: 'PF', 462: 'C', 464: 'G', 463: 'F', 465: 'UTIL'}
ORDER_NBA = ['PG', 'SG', 'SF', 'PF', 'C', 'G', 'F', 'UTIL']

# ------------------------------------------------------------------ slates
def et_date(iso):
    from datetime import datetime, timedelta
    t = datetime.fromisoformat(iso.replace('Z', '').split('.')[0]) - timedelta(hours=5)
    return t.strftime('%Y-%m-%d')

def build_slates():
    mains = json.load(open(f'{BT}/mains.json'))
    slates = []
    if SPORT == 'nfl':
        nfl = load_nfl()
        hist = collections.defaultdict(list)    # pid -> [(week, act)]
        for w in sorted(nfl):
            for p in nfl[w].values():
                if p['act'] is not None: hist[p['pid']].append((w, p['act']))
        for d, gid, gt, n in mains:
            if gt != 1 or not ('2026-09-20' <= d <= '2026-11-16') or gid == 153728: continue
            dg = json.load(open(f'{BT}/dg/{gid}.json'))['draftables']
            games = {tuple(sorted(re.split(r' @ | vs ', x['competition']['name']))) for x in dg}
            # pick the week whose matchups are this slate's games
            match = lambda w: sum(1 for p in nfl[w].values() if p['pos'] == 'DEF' and tuple(sorted((p['team'], p['opp'] or ''))) in games)
            week = max(nfl, key=match)
            if match(week) < 1.5 * len(games): print('  skip', d, gid, 'games not matched'); continue
            wk = nfl[week]
            idx = {}; defs = {}
            for p in wk.values():
                if p['pos'] == 'DEF': defs[p['team']] = p
                else: idx[(norm(p['name']), p['team'])] = p
            seen = {}
            for x in dg:
                if x['playerId'] in seen or x.get('isDisabled'): continue
                pos = x['position']; team = x['teamAbbreviation']
                sp = defs.get(team) if pos == 'DST' else idx.get((norm(x['displayName']), team))
                if not sp: continue
                prev = [a for w, a in hist[sp['pid']] if w < week]
                seen[x['playerId']] = dict(name=x['displayName'], pos=pos, team=team, opp=sp['opp'], salary=x['salary'],
                    status=x.get('status') or 'None', proj=sp['proj'], act=sp['act'] if sp['act'] is not None else 0.0,
                    avg=st.mean(prev) if len(prev) >= 2 else None, start=x['competition']['startTime'])
            slates.append(dict(label=f'NFL wk{week} {d}', players=list(seen.values())))
    else:
        nba, acts = load_nba()
        for d, gid, gt, n in mains:
            if gt != 70 or not ('2026-10-21' <= d <= '2026-11-20'): continue
            dg = json.load(open(f'{BT}/dg/{gid}.json'))['draftables']
            if not dg: continue
            date = et_date(dg[0]['competition']['startTime'])
            day = nba.get(date)
            if not day: continue
            idx = {(norm(p['name']), p['team']): p for p in day.values()}
            seen = {}
            for x in dg:
                if x.get('isDisabled'): continue
                pid = x['playerId']
                if pid not in seen:
                    sp = idx.get((norm(x['displayName']), x['teamAbbreviation']))
                    prev = [a for dd, a, m in acts.get(sp['pid'], [])] if sp else []
                    prev = [a for dd, a, m in acts.get(sp['pid'], []) if dd < date] if sp else []
                    seen[pid] = dict(name=x['displayName'], pos=x['position'], team=x['teamAbbreviation'], salary=x['salary'],
                        status=x.get('status') or 'None', proj=sp['proj'] if sp else 0.0,
                        act=(sp['act'] or 0.0) if sp and sp['act'] is not None else 0.0, pmin=sp['pmin'] if sp else 0,
                        avg=st.mean(prev) if len(prev) >= 3 else None, elig=[], opp=sp['opp'] if sp else None, start=x['competition']['startTime'])
                if x['rosterSlotId'] in NBA_SLOTS: seen[pid]['elig'].append(NBA_SLOTS[x['rosterSlotId']])
            pl = [p for p in seen.values() if p['proj'] > 0 or p['act'] > 0]
            slates.append(dict(label=f'NBA {date} ({n} games)', players=pl))
    return slates

# ------------------------------------------------------------------ optimizer
class Model:
    """Binary MILP for one slate; objective swapped per solve."""
    def __init__(self, players, stack=0, no_def_vs_off=False, bringback=False, minsal=None):
        self.P = players
        if SPORT == 'nba':
            self.E = [(i, s) for i, p in enumerate(players) for s in p['elig']]
        else:
            self.E = [(i, p['pos']) for i, p in enumerate(players)]
        h = self.h = highspy.Highs(); h.silent(); h.setOptionValue('mip_rel_gap', 1e-3); h.setOptionValue('time_limit', 5.0)
        n = len(self.E); inf = highspy.kHighsInf
        h.addVars(n, np.zeros(n), np.ones(n)); h.changeColsIntegrality(n, np.arange(n, dtype=np.int32), np.ones(n, dtype=np.uint8))
        h.changeObjectiveSense(highspy.ObjSense.kMaximize)
        row = lambda idx, vals, lo, hi: h.addRow(lo, hi, len(idx), np.array(idx, dtype=np.int32), np.array(vals, dtype=float))
        row(list(range(n)), [players[i]['salary'] for i, _ in self.E], -inf, 50000)
        if SPORT == 'nba':
            for s in ORDER_NBA: row([k for k, (i, ss) in enumerate(self.E) if ss == s], [1]*sum(1 for _, ss in self.E if ss == s), 1, 1)
            byp = collections.defaultdict(list)
            for k, (i, s) in enumerate(self.E): byp[i].append(k)
            for ks in byp.values(): row(ks, [1]*len(ks), 0, 1)
        else:
            cnt = lambda pos: [k for k, (i, s) in enumerate(self.E) if s == pos]
            row(list(range(n)), [1]*n, 9, 9)
            for pos, lo, hi in [('QB', 1, 1), ('DST', 1, 1), ('RB', 2, 3), ('WR', 3, 4), ('TE', 1, 2)]:
                ks = cnt(pos); row(ks, [1]*len(ks), lo, hi)
            for k, (i, s) in enumerate(self.E):
                p = players[i]
                if s == 'QB' and stack:
                    mates = [kk for kk, (j, ss) in enumerate(self.E) if ss in ('WR', 'TE') and players[j]['team'] == p['team']]
                    row(mates + [k], [1]*len(mates) + [-stack], 0, inf)
                    if bringback:
                        opps = [kk for kk, (j, ss) in enumerate(self.E) if ss in ('WR', 'TE', 'RB') and players[j]['team'] == p['opp']]
                        row(opps + [k], [1]*len(opps) + [-1], 0, inf)
                if s == 'DST' and no_def_vs_off:
                    for kk, (j, ss) in enumerate(self.E):
                        if players[j]['team'] == p['opp'] and ss != 'DST': row([k, kk], [1, 1], -inf, 1)
        self.size = 9 if SPORT == 'nfl' else 8
    def solve(self, values, banned=(), avoid=()):
        """values: per-player objective. banned: player idx forced out. avoid: earlier lineups (sets of idx) to differ from by >=1."""
        h = self.h; n = len(self.E)
        cost = np.array([values[i] for i, _ in self.E], dtype=float)
        h.changeColsCost(n, np.arange(n, dtype=np.int32), cost)
        extra = []
        ub = np.ones(n)
        for k, (i, _) in enumerate(self.E):
            if i in banned: ub[k] = 0
        h.changeColsBounds(n, np.arange(n, dtype=np.int32), np.zeros(n), ub)
        for lu in avoid:
            ks = [k for k, (i, _) in enumerate(self.E) if i in lu]
            h.addRow(-highspy.kHighsInf, self.size - 1, len(ks), np.array(ks, dtype=np.int32), np.ones(len(ks))); extra.append(h.getNumRow() - 1)
        h.run()
        stat = h.getModelStatus()
        sol = h.getSolution().col_value
        ok = stat == highspy.HighsModelStatus.kOptimal or (stat == highspy.HighsModelStatus.kTimeLimit and h.getInfo().primal_solution_status == 2)
        if extra: h.deleteRows(len(extra), np.array(extra, dtype=np.int32))
        if not ok: return None
        return frozenset(self.E[k][0] for k in range(n) if sol[k] > 0.5)

# ------------------------------------------------------------------ simulation (correlated outcomes)
ROLE_CORR_SAME = {('QB', 'WR1'): .37, ('QB', 'WR2'): .24, ('QB', 'WR3'): .25, ('QB', 'TE1'): .30, ('QB', 'RB1'): .11, ('QB', 'RB2'): .05,
                  ('RB1', 'WR1'): .06, ('WR1', 'WR2'): -.07, ('WR1', 'WR3'): -.07, ('WR2', 'WR3'): -.07, ('RB1', 'RB2'): -.03, ('DST', 'QB'): -.09}
ROLE_CORR_OPP = {('QB', 'QB'): .22, ('QB', 'WR1'): .05, ('QB', 'RB1'): .10, ('DST', 'QB'): -.43, ('DST', 'RB1'): -.29, ('DST', 'RB2'): -.15,
                 ('DST', 'WR1'): -.25, ('DST', 'WR2'): -.2, ('DST', 'WR3'): -.15, ('DST', 'TE1'): -.2}
def sd_of(p):
    if SPORT == 'nba': return 4 + 0.2 * p['proj']
    return 5.2 if p['pos'] == 'DST' else 2 + 0.45 * p['proj']

def simulate(players, n):
    """n correlated outcome draws (n x players). NFL: role-based correlation per game; NBA: independent."""
    m = len(players); z = rng.standard_normal((n, m))
    if SPORT == 'nfl':
        roles = {}
        byteam = collections.defaultdict(list)
        for i, p in enumerate(players): byteam[p['team']].append(i)
        for t, ids in byteam.items():
            for pos in ('QB', 'RB', 'WR', 'TE', 'DST'):
                r = sorted([i for i in ids if players[i]['pos'] == pos], key=lambda i: -players[i]['proj'])
                for k, i in enumerate(r): roles[i] = pos if pos in ('QB', 'DST') else pos + str(k + 1)
        games = collections.defaultdict(list)
        for i, p in enumerate(players):
            if roles[i] in ('QB', 'DST', 'RB1', 'RB2', 'WR1', 'WR2', 'WR3', 'TE1'): games[tuple(sorted((p['team'], p['opp'] or '')))].append(i)
        for g, ids in games.items():
            C = np.eye(len(ids))
            for a in range(len(ids)):
                for b in range(a + 1, len(ids)):
                    i, j = ids[a], ids[b]; ri, rj = roles[i], roles[j]
                    tab = ROLE_CORR_SAME if players[i]['team'] == players[j]['team'] else ROLE_CORR_OPP
                    c = tab.get((ri, rj), tab.get((rj, ri), 0))
                    C[a, b] = C[b, a] = c
            w, V = np.linalg.eigh(C); w = np.clip(w, 1e-3, None); C = V @ np.diag(w) @ V.T
            d = np.sqrt(np.diag(C)); C = C / np.outer(d, d)
            L = np.linalg.cholesky(C)
            z[:, ids] = z[:, ids] @ L.T
    proj = np.array([p['proj'] for p in players]); sd = np.array([sd_of(p) for p in players])
    # right-skewed outcomes: lognormal for positive projections, matched mean/sd
    out = np.empty_like(z)
    pos = proj > 0.5
    s = np.sqrt(np.log1p((sd[pos] / proj[pos]) ** 2))
    out[:, pos] = proj[pos] * np.exp(s * z[:, pos] - s ** 2 / 2)
    out[:, ~pos] = 0
    if SPORT == 'nfl':
        dst = np.array([p['pos'] == 'DST' for p in players])
        out[:, dst] = proj[dst] + sd[dst] * z[:, dst]
    return out

# ------------------------------------------------------------------ strategies
NEVER = {'OUT', 'IR', 'D'}
def run():
    slates = build_slates()
    print(f'{len(slates)} {SPORT.upper()} slates')
    res = collections.defaultdict(list)
    import time
    for sl in slates:
        t0 = time.time()
        P = [p for p in sl['players'] if p['status'] not in NEVER]
        act = np.array([p['act'] for p in P]); proj = np.array([p['proj'] for p in P])
        blend = np.array([(0.6 * p['proj'] + 0.4 * p['avg']) if p.get('avg') is not None and p['proj'] > 0 else p['proj'] for p in P])
        floor = lambda p: (5000 if p['pos'] == 'QB' else 0 if p['pos'] == 'DST' else 4000) if SPORT == 'nfl' else 4000
        bal_ban = {i for i, p in enumerate(P) if p['salary'] < floor(p) or p['status'] == 'Q'}
        base = Model(P)
        stacked = Model(P, stack=1, no_def_vs_off=True) if SPORT == 'nfl' else base
        stack2 = Model(P, stack=2, no_def_vs_off=True, bringback=True) if SPORT == 'nfl' else base
        # ---- field: noisy-projection optimizers (half stacked in NFL)
        field = []
        for k in range(200):
            noisy = proj * np.exp(rng.normal(0, 0.15, len(P)))
            m = stacked if (SPORT == 'nfl' and k % 2) else base
            lu = m.solve(noisy)
            if lu: field.append(lu)
        if len(field) < 50: print('  skip', sl['label'], 'field failed'); continue
        fscore = np.array([act[list(lu)].sum() for lu in field])
        med, p99, top = np.median(fscore), np.percentile(fscore, 99), fscore.max()
        own = collections.Counter(i for lu in field for i in lu)
        score = lambda lu: act[list(lu)].sum()
        pct = lambda s: (fscore < s).mean()
        # ---- single-lineup (cash) strategies
        singles = {
            'Projected (Sleeper)': base.solve(proj),
            'Balanced (Sleeper)': base.solve(proj, banned=bal_ban),
            'Projected (Blend)': base.solve(blend),
            'Balanced (Blend)': base.solve(blend, banned=bal_ban),
        }
        if SPORT == 'nfl':
            singles['Projected + QB stack (Sleeper)'] = stacked.solve(proj)
            singles['Balanced + QB stack (Sleeper)'] = stacked.solve(proj, banned=bal_ban)
        if SPORT == 'nba':
            mins_ban = {i for i, p in enumerate(P) if p['pmin'] < 20}
            singles['Blend + 20-min floor'] = base.solve(blend, banned=mins_ban)
            singles['Blend + 24-min floor'] = base.solve(blend, banned={i for i, p in enumerate(P) if p['pmin'] < 24})
        for name, lu in singles.items():
            if lu: res[name].append((score(lu) > med, pct(score(lu)), score(lu) >= p99, score(lu) > top))
        # ---- 20-lineup GPP sets
        def topn(model, vals, n=20):
            out = []
            for _ in range(n):
                lu = model.solve(vals, avoid=out)
                if not lu: break
                out.append(lu)
            return out
        gsets = {'GPP: top-20 projected': topn(stacked if SPORT == 'nfl' else base, blend if SPORT == 'nba' else proj)}
        # simulation-based: candidates from simulated outcomes, pick by simulated top-1% rate vs simulated field
        center = blend if SPORT == 'nba' else proj
        Pc = [dict(p, proj=float(c)) for p, c in zip(P, center)]
        cand_model = stack2 if SPORT == 'nfl' else base
        sims_c = simulate(Pc, 120)
        cands = set()
        for k in range(120):
            m = cand_model if (SPORT == 'nba' or k % 3) else stacked
            lu = m.solve(0.5 * center + 0.5 * sims_c[k])
            if lu: cands.add(lu)
        cands = list(cands)
        sims = simulate(Pc, 1500)
        fs = np.stack([sims[:, list(lu)].sum(1) for lu in field], 1)     # sims x field
        line99 = np.percentile(fs, 99, axis=1); fmax = fs.max(1)
        cs = np.stack([sims[:, list(lu)].sum(1) for lu in cands], 1)     # sims x cands
        top1 = (cs >= line99[:, None]).mean(0); win = (cs > fmax[:, None]).mean(0)
        order = np.argsort(-(top1 + 2 * win))
        chosen = []
        for c in order:
            if all(len(cands[c] & ch) <= (6 if SPORT == 'nfl' else 5) for ch in chosen): chosen.append(cands[c])
            if len(chosen) == 20: break
        gsets['GPP: simulation-picked 20'] = chosen
        for name, lus in gsets.items():
            sc = [score(lu) for lu in lus]
            res[name].append((max(sc) > med, pct(max(sc)), max(sc) >= p99, max(sc) > top))
        res['_hindsight'].append((0, 0, 0, 0))
        print(f"  [{time.time()-t0:5.0f}s]", end='')
        print(f"  {sl['label']:26} field median {med:6.1f}  99th {p99:6.1f}  best {top:6.1f} | " +
              '  '.join(f"{n.split(' (')[0][:12]}{'+' if 'Blend' in n else ''}:{score(lu):5.1f}" for n, lu in list(singles.items())[:4] if lu))
    print(f"\n{'strategy':34} {'cash rate':>9} {'avg pctile':>10} {'top-1%':>7} {'won':>5}   (slates={len(slates)})")
    for name, v in res.items():
        if name.startswith('_'): continue
        print(f"{name:34} {np.mean([x[0] for x in v]):9.0%} {np.mean([x[1] for x in v]):10.0%} {np.mean([x[2] for x in v]):7.0%} {np.mean([x[3] for x in v]):5.0%}")
run()

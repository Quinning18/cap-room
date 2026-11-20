import sys, statistics as st, collections, math
sys.path.insert(0, sys.argv[1]); from data import load_nfl, load_nba
import numpy as np
# ---- NFL: standardized residual correlations by role ----
nfl = load_nfl()
res = collections.defaultdict(list)   # pid-level z residual per week
pairs = collections.defaultdict(list)
for w, wk in nfl.items():
    played = [p for p in wk.values() if p['act'] is not None and p['proj'] >= 4]
    # role inside team by projection rank
    byteam = collections.defaultdict(list)
    for p in played: byteam[p['team']].append(p)
    role = {}
    for t, ps in byteam.items():
        for pos in ('QB', 'RB', 'WR', 'TE', 'DEF'):
            r = sorted([p for p in ps if p['pos'] == pos], key=lambda p: -p['proj'])
            for i, p in enumerate(r): role[p['pid']] = pos + (str(i+1) if pos in ('RB', 'WR', 'TE') else '')
    for p in played:
        sd = 3 + 0.45 * p['proj'] if p['pos'] != 'DEF' else 5.5
        p['z'] = (p['act'] - p['proj']) / sd
    for t, ps in byteam.items():
        opp = byteam.get(ps[0]['opp'], [])
        for a in ps:
            for b in ps:
                if a['pid'] < b['pid']:
                    k = tuple(sorted((role[a['pid']], role[b['pid']])))
                    pairs[('same',) + k].append((a['z'], b['z']) if (role[a['pid']], role[b['pid']]) == k else (b['z'], a['z']))
            for b in opp:
                k = (role[a['pid']], 'opp ' + role[b['pid']])
                pairs[('opp',) + k].append((a['z'], b['z']))
def corr(v): return np.corrcoef(np.array(v).T)[0, 1] if len(v) > 30 else float('nan')
print('NFL residual correlations (2026 weeks 1-10):')
for k in [('same','QB','WR1'),('same','QB','WR2'),('same','QB','WR3'),('same','QB','TE1'),('same','QB','RB1'),('same','RB1','WR1'),('same','DEF','RB1'),('same','DEF','QB'),('same','WR1','WR2'),('same','RB1','RB2'),
          ('opp','QB','opp QB'),('opp','QB','opp WR1'),('opp','QB','opp DEF'),('opp','WR1','opp WR1'),('opp','RB1','opp DEF'),('opp','QB','opp RB1')]:
    print(f'  {" ".join(k):22} r={corr(pairs[k]):+.2f}  n={len(pairs[k])}')
# NFL sd by position as fraction of projection
for pos in ('QB','RB','WR','TE','DEF'):
    v = [(p['proj'], p['act']) for wk in nfl.values() for p in wk.values() if p['pos']==pos and p['act'] is not None and p['proj']>=5]
    for lo,hi in [(5,10),(10,15),(15,20),(20,40)]:
        vv=[a-pr for pr,a in v if lo<=pr<hi]
        if len(vv)>20: print(f'  {pos} proj {lo}-{hi}: n={len(vv)} bias {st.mean(vv):+.2f} sd {st.pstdev(vv):.2f}')
# ---- NBA ----
nba, _ = load_nba()
same, opp = [], []
sds = collections.defaultdict(list)
for d, day in nba.items():
    pl = [p for p in day.values() if p['act'] is not None and p['proj'] >= 10]
    for p in pl:
        p['z'] = (p['act'] - p['proj']) / (4 + 0.2 * p['proj'])
    byteam = collections.defaultdict(list)
    for p in pl: byteam[p['team']].append(p)
    for t, ps in byteam.items():
        for i, a in enumerate(ps):
            for b in ps[i+1:]: same.append((a['z'], b['z']))
            for b in byteam.get(a['opp'], []): opp.append((a['z'], b['z']))
print(f'NBA residual correlation: teammates r={corr(same):+.3f} (n={len(same)}), opponents r={corr(opp):+.3f} (n={len(opp)})')

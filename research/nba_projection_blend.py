import json, statistics as st, collections, sys, itertools
D, TODAY = sys.argv[1], '2026-11-20'
def dk(s):
    g=lambda k: float(s.get(k) or 0)
    return g('pts')+0.5*g('tpm')+1.25*g('reb')+1.5*g('ast')+2*g('stl')+2*g('blk')-0.5*g('to')+1.5*g('dd')+3*g('td')
def dkact(s):
    g=lambda k: float(s.get(k) or 0)
    cats=sum(1 for k in ('pts','reb','ast','stl','blk') if g(k)>=10)
    return g('pts')+0.5*g('tpm')+1.25*g('reb')+1.5*g('ast')+2*g('stl')+2*g('blk')-0.5*g('to')+(1.5 if cats>=2 else 0)+(3 if cats>=3 else 0)
mins=lambda s: float(s.get('min') or 0) or float(s.get('sp') or 0)/60
P={}; A={}
for w in range(1,6):
    for r in json.load(open(f'{D}/nba_proj_w{w}.json')):
        if r.get('stats') and r.get('date') and r['date']<TODAY: P[(r['player_id'],r['date'])]=r
    for r in json.load(open(f'{D}/nba_stats_w{w}.json')):
        if r.get('date') and r['date']<TODAY and r.get('stats') and mins(r['stats'])>0: A[(r['player_id'],r['date'])]=r
hist=collections.defaultdict(list)
for (pid,d),a in sorted(A.items(), key=lambda x:x[0][1]): hist[pid].append((d, dkact(a['stats']), mins(a['stats'])))
rows=[]
for (pid,d),p in P.items():
    if (pid,d) not in A: continue
    prev=[x for x in hist[pid] if x[0]<d]
    if len(prev)<3: continue
    pp=dk(p['stats'])
    if pp<8: continue
    season=st.mean(x[1] for x in prev); last5=st.mean(x[1] for x in prev[-5:])
    # per-minute rate x projected minutes
    rate=sum(x[1] for x in prev)/max(1,sum(x[2] for x in prev)); pmin=float(p['stats'].get('sp',0))/60
    rows.append(dict(d=d, sl=pp, season=season, last5=last5, rate_x_min=rate*pmin, act=dkact(A[(pid,d)]['stats'])))
rows.sort(key=lambda r:r['d']); half=len(rows)//2; train, test = rows[:half], rows[half:]
print('player-games', len(rows), 'train dates', train[0]['d'], train[-1]['d'], 'test', test[0]['d'], test[-1]['d'])
def ev(rs, f):
    e=[r['act']-f(r) for r in rs]; return st.mean(map(abs,e)), st.correlation([f(r) for r in rs],[r['act'] for r in rs]), st.mean(e)
print(f"{'model':38} {'train MAE':>9} {'test MAE':>9} {'test corr':>9} {'test bias':>9}")
models={'Sleeper only': lambda r:r['sl'], 'season avg only': lambda r:r['season'], 'last-5 avg only': lambda r:r['last5'], 'min-proj x season pts/min': lambda r:r['rate_x_min']}
best=None
for a,b,c in itertools.product([x/10 for x in range(0,11)], repeat=3):
    if a+b+c>1.0001: continue
    w=1-a-b-c
    f=lambda r,a=a,b=b,c=c,w=w: w*r['sl']+a*r['season']+b*r['last5']+c*r['rate_x_min']
    m=ev(train,f)[0]
    if best is None or m<best[0]: best=(m,(w,a,b,c),f)
models[f'blend fit on train (Sleeper {best[1][0]:.1f}, season {best[1][1]:.1f}, last5 {best[1][2]:.1f}, rate {best[1][3]:.1f})']=best[2]
k=st.mean(r['act'] for r in train)/st.mean(r['sl'] for r in train)
models[f'Sleeper x {k:.3f} (train bias fix)']=lambda r:r['sl']*k
for name,f in models.items():
    tr=ev(train,f); te=ev(test,f); print(f'{name[:70]:70} {tr[0]:9.2f} {te[0]:9.2f} {te[1]:9.3f} {te[2]:+9.2f}')

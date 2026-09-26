"""Sergio's Betts — NBA REB/AST morning scan (rulebook: NBA REB/AST model, blend + injury v2).
Run in chat:  python3 nba_scan.py YYYY-MM-DD   (date in US Eastern game days)
       Injury alert: python3 nba_scan.py --injury-diff   (official-report status changes, last ~75 min)
Pipeline: ESPN box scores (last + current season) -> minutes x per-minute rate, shrunk, opponent factor
-> injury adjustment from the official NBA injury report (ESPN feed as fallback) -> blend with Pinnacle no-vig (Pinnapi) -> edge vs Kalshi bid/ask.
Frozen parameters (2026-09-25): K=30 min, opp w=1.0, NegBin r=30, injury gamma=0.15,
blend REB w=0.35 c=-0.02, AST w=0.25 c=0.00. Edge >= 3 pts at the limit price, zone 35-75c.
"""
import json, math, sys, os, time, re, unicodedata, datetime, collections, statistics
import urllib.request, concurrent.futures as cf

PINNAPI_KEY = os.environ.get('PINNAPI_KEY', '')
CACHE = '/tmp/nba_scan_cache'; os.makedirs(CACHE, exist_ok=True)
K, W_OPP, R_NB, GAMMA = 30, 1.0, 30, 0.15
BLEND = {'REB': (0.35, -0.02), 'AST': (0.25, 0.00)}
SERIES = {'REB': 'KXNBAREB', 'AST': 'KXNBAAST'}
EDGE_MIN, ZONE = 0.03, (0.35, 0.75)
BETA_START = 7.0   # REB only: minutes += 7.0 x (starts today - start rate L10). Stage 2 PASSED 2026-09-26; AST failed.

def get(url, headers=None, tries=3):
    for a in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=30))
        except Exception:
            time.sleep(1.5)
    return None

def nrm(s): return re.sub(r'[^a-z]', '', unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower().replace(' jr', '').replace(' iii', '').replace(' ii', ''))
def lg(p): p = min(max(p, 1e-4), 1 - 1e-4); return math.log(p / (1 - p))
def sg(x): return 1 / (1 + math.exp(-x))

def nb_sf(k, mu, r):
    if mu <= 0: return 0.0
    p = r / (r + mu); pm = 0.0; term = p ** r
    for x in range(k):
        if x > 0: term *= (x - 1 + r) / x * (1 - p)
        pm += term
    return max(0.0, 1 - pm)
GH = [(-1.3556, 0.1995), (0.0, 0.6005), (1.3556, 0.1995)]

def power_devig(o, u):
    a, b = 1 / o, 1 / u; lo, hi = 0.5, 3
    for _ in range(100):
        k = (lo + hi) / 2
        if a ** k + b ** k > 1: lo = k
        else: hi = k
    return a ** k

# ---------- 1. box scores (cached) ----------
def season_game_ids(start, end):
    days = [start + datetime.timedelta(days=i) for i in range((end - start).days + 1)]
    def one(d):
        fn = f'{CACHE}/sb_{d}.json'
        if os.path.exists(fn) and d < datetime.date.today() - datetime.timedelta(days=1): return json.load(open(fn))
        j = get(f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={d:%Y%m%d}")
        if j is None: return []
        ev = [(e['id'], e['date']) for e in j.get('events', []) if e['season']['type'] == 2 and e['status']['type']['completed']]
        json.dump(ev, open(fn, 'w')); return ev
    with cf.ThreadPoolExecutor(8) as ex: res = list(ex.map(one, days))
    return [x for r in res for x in r]

def box(gid):
    fn = f'{CACHE}/b2_{gid}.json'   # b2 = includes starter flag
    if os.path.exists(fn): return json.load(open(fn))
    j = get(f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={gid}")
    if not j: return None
    comp = j['header']['competitions'][0]; teams = [c['team']['abbreviation'] for c in comp['competitors']]
    rows = []
    for t in j['boxscore']['players']:
        ab = t['team']['abbreviation']; opp = [x for x in teams if x != ab][0]; st = t['statistics'][0]
        for a in st['athletes']:
            if a.get('didNotPlay') or not a.get('stats'): continue
            s = dict(zip(st['keys'], a['stats']))
            try: mn = float(s.get('minutes') or 0)
            except ValueError: mn = 0
            if mn <= 0: continue
            rows.append(dict(pid=a['athlete']['id'], name=a['athlete']['displayName'], team=ab, opp=opp, min=mn, starter=bool(a.get('starter')), REB=int(s.get('rebounds', 0)), AST=int(s.get('assists', 0))))
    out = dict(id=gid, date=comp['date'][:10], rows=rows); json.dump(out, open(fn, 'w')); return out

def load_history(today):
    y = today.year if today.month >= 7 else today.year - 1   # season start year
    ids = season_game_ids(datetime.date(y - 1, 10, 20), datetime.date(y, 4, 15)) + season_game_ids(datetime.date(y, 10, 20), today - datetime.timedelta(days=1))
    with cf.ThreadPoolExecutor(8) as ex: G = [g for g in ex.map(lambda x: box(x[0]), ids) if g]
    G.sort(key=lambda g: g['date']); return G

# ---------- 2. model state ----------
def build_state(G):
    hist = collections.defaultdict(list); allow = collections.defaultdict(collections.Counter); lgc = collections.Counter(); team_games = collections.defaultdict(list)
    for g in G:
        for r in g['rows']:
            hist[r['pid']].append(r)
            for st in ('REB', 'AST', 'min'): allow[r['opp']][st] += r[st]; lgc[st] += r[st]
        for t in {r['team'] for r in g['rows']}: team_games[t].append({r['pid'] for r in g['rows'] if r['team'] == t})
    return hist, allow, lgc, team_games

def player_mu(pid, opp, state, injured_ids, team, start_today=None):
    hist, allow, lgc, team_games = state; h = hist[pid]
    if len(h) < 10: return None
    wins = [(0.35, h), (0.30, h[-20:]), (0.25, h[-10:]), (0.10, h[-5:])]
    mhat = sum(w * statistics.mean(x['min'] for x in s) for w, s in wins); sd = statistics.pstdev([x['min'] for x in h[-20:]])
    # teammates out: rotation players (>=15 min over their last 10) listed Out/Doubtful
    rot = [p for p in injured_ids if len(hist[p]) >= 5 and hist[p][-1]['team'] == team and statistics.mean(x['min'] for x in hist[p][-10:]) >= 15 and p != pid]
    team_min = sum(statistics.mean(x['min'] for x in hist[p][-10:]) for g in team_games[team][-1:] for p in g if hist[p]) or 240
    share = mhat / max(team_min, 1)
    out = {}
    for st in ('REB', 'AST'):
        prior = lgc[st] / lgc['min']
        rate = sum(w * ((sum(x[st] for x in s) + K * prior) / (sum(x['min'] for x in s) + K)) for w, s in wins)
        a = allow[opp]; f = 1 + W_OPP * ((a[st] / a['min']) / (lgc[st] / lgc['min']) - 1) if a['min'] else 1
        miss = sum(statistics.mean(x[st] for x in hist[p][-10:]) for p in rot)
        out[st] = (rate * f, GAMMA * miss * share)
    res = dict(mhat=mhat, sd=sd, st=out, n_out=len(rot))
    if start_today is not None and all('starter' in x for x in h[-10:]):
        srate = statistics.mean(x['starter'] for x in h[-10:])
        res['mhat_REB'] = mhat + BETA_START * (int(start_today) - srate)
        res['role_change'] = abs(int(start_today) - srate) >= 0.5
    return res

def prob(m, st, k):
    rate, add = m['st'][st]
    mh = m.get('mhat_' + st, m['mhat'])
    return sum(w * nb_sf(k, rate * max(mh + z * m['sd'], 1) + add, R_NB) for z, w in GH)

# ---------- 3. live inputs ----------
def todays_games(day):
    j = get(f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={day:%Y%m%d}") or {}
    out = []
    for e in j.get('events', []):
        c = e['competitions'][0]; ab = {x['homeAway']: x['team']['abbreviation'] for x in c['competitors']}
        out.append(dict(id=e['id'], tip=e['date'], home=ab['home'], away=ab['away'], state=e['status']['type']['state']))
    return out

# Official NBA injury report (league source ESPN copies from): PDF every 15 min, times in ET.
IR_URL = 'https://ak-static.cms.nba.com/referee/injury/Injury-Report_{:%Y-%m-%d_%I_%M%p}.pdf'
IR_TEAMS = ['Atlanta Hawks','Boston Celtics','Brooklyn Nets','Charlotte Hornets','Chicago Bulls','Cleveland Cavaliers','Dallas Mavericks','Denver Nuggets','Detroit Pistons','Golden State Warriors','Houston Rockets','Indiana Pacers','LA Clippers','Los Angeles Clippers','Los Angeles Lakers','Memphis Grizzlies','Miami Heat','Milwaukee Bucks','Minnesota Timberwolves','New Orleans Pelicans','New York Knicks','Oklahoma City Thunder','Orlando Magic','Philadelphia 76ers','Phoenix Suns','Portland Trail Blazers','Sacramento Kings','San Antonio Spurs','Toronto Raptors','Utah Jazz','Washington Wizards']
_NM = r"[A-Z][A-Za-z\.\'\-]*(?: [A-Za-z\.\'\-]+)*"
IR_ROW = re.compile(r"(" + _NM + r", " + _NM + r")\s{2,}(Out|Doubtful|Questionable|Probable|Available)\b")

def ir_parse(pdf_bytes):
    import subprocess, tempfile
    with tempfile.NamedTemporaryFile(suffix='.pdf') as f:
        f.write(pdf_bytes); f.flush()
        txt = subprocess.run(['pdftotext', '-layout', f.name, '-'], capture_output=True, text=True).stdout
    out, team, game, gdate = {}, None, None, None
    for ln in txt.splitlines():
        m = re.search(r'\b(\d\d/\d\d/\d{4})\b', ln)
        if m: gdate = datetime.datetime.strptime(m.group(1), '%m/%d/%Y').date()
        m = re.search(r'\b([A-Z]{2,3}@[A-Z]{2,3})\b', ln)
        if m: game = m.group(1)
        for t in IR_TEAMS:
            if t in ln: team = t
        r = IR_ROW.search(ln)
        if r:
            last, first = [x.strip() for x in r.group(1).split(',', 1)]
            name = f'{first} {last}'
            out[nrm(name)] = dict(name=name, status=r.group(2), team=team, game=game, date=gdate)
    return out

def official_report(before=None, max_back=16):
    """Latest official report at or before `before` (ET, default now). Returns (report_time_ET, {nrm_name: row}) or (None, {})."""
    from zoneinfo import ZoneInfo
    t = (before or datetime.datetime.now(ZoneInfo('America/New_York'))).replace(second=0, microsecond=0)
    t = t.replace(minute=t.minute // 15 * 15)
    for _ in range(max_back):
        try:
            req = urllib.request.Request(IR_URL.format(t), headers={'User-Agent': 'curl/8'})
            data = urllib.request.urlopen(req, timeout=30).read()
            if data[:4] == b'%PDF': return t, ir_parse(data)
        except Exception:
            pass
        t -= datetime.timedelta(minutes=15)
    return None, {}

def injuries():
    """{nrm_name: (name, status)} — official NBA report first, ESPN feed fills players the report doesn't list."""
    out = {}
    j = get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries") or {}
    for t in j.get('injuries', []):
        for i in t.get('injuries', []):
            out[nrm(i['athlete']['displayName'])] = (i['athlete']['displayName'], i.get('status'))
    rt, rep = official_report()
    for k, r in rep.items(): out[k] = (r['name'], r['status'])
    injuries.source = f"official NBA report {rt:%I:%M %p ET}" if rt else "ESPN feed only (official report unavailable)"
    return out

def injury_diff(minutes=75, at=None):
    """Status changes between the latest official report and the one ~`minutes` earlier, for today's games."""
    t1, new = official_report(at)
    if not t1: print('Official injury report unavailable'); return []
    t0, old = official_report(t1 - datetime.timedelta(minutes=minutes), max_back=4)
    today = t1.date(); bad, good = ('Out', 'Doubtful'), ('Probable', 'Available')
    ch = []
    for k in set(new) | set(old):
        n, o = new.get(k), old.get(k)
        row = n or o
        if row['date'] and row['date'] != today: continue
        ns, os_ = (n or {}).get('status', 'Not listed'), (o or {}).get('status', 'Not listed')
        if ns == os_: continue
        if (ns in bad and os_ not in bad) or (os_ in bad + ('Questionable',) and ns in good + ('Not listed',)):
            ch.append(dict(player=row['name'], team=row['team'], game=row['game'], old=os_, new=ns))
    print(f"Official report {t1:%I:%M %p ET} vs {t0.strftime('%I:%M %p ET') if t0 else 'none'}: {len(ch)} relevant changes")
    for c in ch: print(c)
    return ch

def lineups(day):
    """{nrm_name: True/False starter} for `day`. Source: RotoWire nba-lineups.php (reachable; other lineup sites block us).
    Parser pending: page is empty in the offseason — build + verify against real pages in NBA preseason (early Oct 2026).
    Until then returns {} and the starter adjustment is simply off."""
    lineups.source = 'not wired yet (RotoWire reader pending NBA preseason)'
    return {}

# ---------- 4. scan ----------
def scan(day):
    games = [g for g in todays_games(day) if g['state'] == 'pre']
    if not games: print('No pre-game NBA games for', day); return []
    G = load_history(day); state = build_state(G); hist = state[0]
    byname = {}
    for pid, h in hist.items(): byname[nrm(h[-1]['name'])] = pid
    inj = injuries(); out_ids = [pid for pid, h in hist.items() if nrm(h[-1]['name']) in inj and inj[nrm(h[-1]['name'])][1] in ('Out', 'Doubtful')]
    teams = {g['home']: g['away'] for g in games} | {g['away']: g['home'] for g in games}
    starters = lineups(day)
    P = pinnacle_props(); plays = []; seen = 0
    for st in ('REB', 'AST'):
        w, c = BLEND[st]
        for m in kalshi_markets(st):
            pid = byname.get(nrm(m['name']))
            if not pid or pid in out_ids: continue
            team = hist[pid][-1]['team']
            if team not in teams: continue
            mod = player_mu(pid, teams[team], state, out_ids, team, start_today=starters.get(nrm(m['name'])))
            if not mod: continue
            pin = P.get((nrm(m['name']), st, m['line']))
            seen += 1
            if pin is None: continue            # no sharp anchor -> Route 3 paper only, skipped here
            pm = prob(mod, st, m['k']); blend = sg(w * lg(pm) + (1 - w) * lg(pin) + c)
            for side, fair, limit in (('YES', blend, m['bid'] + 0.01), ('NO', 1 - blend, (1 - m['ask']) + 0.01)):
                if m['bid'] <= 0 or m['ask'] <= 0: continue
                edge = fair - limit
                if edge >= EDGE_MIN and ZONE[0] <= limit <= ZONE[1]:
                    plays.append(dict(stat=st, player=m['name'], line=f"{m['k']}+", side=side, limit=round(limit * 100), bid_ask=f"{m['bid']*100:.0f}/{m['ask']*100:.0f}",
                                      model=round(pm * 100, 1), role_change=mod.get('role_change', False), pinnacle=round(pin * 100, 1), blend=round(blend * 100, 1), edge=round(edge * 100, 1), teammates_out=mod['n_out']))
    plays.sort(key=lambda p: -p['edge'])
    print(f"{day}: {len(games)} games | injuries: {injuries.source} | lineups: {lineups.source} | Kalshi REB/AST markets matched to model {seen} | Pinnacle props {len(P)} | plays {len(plays)}")
    for p in plays: print(p)
    return plays

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--injury-diff':
        injury_diff()
    else:
        d = datetime.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else datetime.date.today()
        scan(d)

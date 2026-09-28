"""Sergio's Betts — NHL Shots On Goal (SOG) morning scan (rulebook: NHL SOG model + Track B).
Run in chat:  python3 nhl_scan.py YYYY-MM-DD

Pipeline: NHL API box scores -> TOI x SOG/60 rate model (window-weighted, shrunk, opponent factor,
NegBin r=40) -> Pinnacle no-vig fair price -> price vs execution venues (Kalshi has NO SOG market —
confirmed live 2026-09-27, see rulebook NHL SOG venue-check note). Model contributes ~0 blend weight
vs Pinnacle (rulebook closing-line test: w_model = 0.00) — it's used only as (a) a filter: skip an
Under if the model favors Over by >=3pts even though Pinnacle-vs-exec shows an edge, and (b) a pricer
for players Pinnacle doesn't list (Route 3, paper/unconfirmed). NHL SOG = Unders only. Overs = no bet.

FAIR-PRICE / EXECUTION SOURCES — status as of 2026-09-27, checked live, not assumed:
  1. SportsGameOdds Pro trial (SGO_KEY, expires 2026-10-04 — dead after that, key was cancelled,
     access runs out at end of billing period). Confirmed REAL market-key pattern by pulling actual
     NHL events today:
       oddID  shots_onGoal-{PLAYER_NAME}_1_NHL-game-ou-{over|under}
       byBookmaker keys seen for this market: pinnacle, fliff, draftkings, fanduel, betmgm, etc.
     Checked THREE separate dates (2026-09-27 preseason, 2025-11-10, 2026-03-05 regular season) for
     novig / prophetexchange coverage of the shots_onGoal market specifically — neither ever appeared,
     even though Novig confirmed live in-app for tonight's game (Sergio's screenshot). So: SGO gives
     us Pinnacle (fair) + Fliff (one real execution venue) for NHL SOG. It does NOT give us Novig or
     ProphetX for this market — don't assume it does just because it works for other sports/markets.
     Also checked: preseason games (gameType 1, happening right now) mostly have ZERO Pinnacte SOG
     data (thin exhibition markets) — of 3 preseason games sampled, only 1 had any Pinnacle SOG price
     at all. Real usable Pinnacle SOG coverage needs regular-season games (gameType 2), which for
     2026-27 likely start after the SGO trial is already dead. So this path may end up being useless
     in practice for THIS season's live scan — confirm regular-season start date vs Oct 4 before
     counting on it.
  2. OddsPapi (ODDSPAPI_KEY) — paid Normal tier w/ Player Props add-on, sports scoped to include NHL
     (rulebook Track B paid-plan note). sportId / marketId for the NHL Shots On Goal O/U market are
     NOT hardcoded here because they have NOT been discovered yet — the free-tier key has returned
     429 REQUEST_LIMIT_EXCEEDED every time it's been tried this session, including on the lightweight
     /v4/sports lookup, so the paid tier is not confirmed active. discover_oddspapi_nhl_ids() below
     hits /v4/sports + /v4/markets and prints what it finds — run that FIRST once the paid plan is
     confirmed live, read the real NHL sportId and the real Shots On Goal marketId from its output,
     and hardcode them at the top of this file (mirroring softness_scan.py's HITS_MARKET_OVER/UNDER
     pattern) before trusting oddspapi_sog_odds(). Novig slug there is 'novig.us', ProphetX is
     'prophetx' (confirmed working slugs from softness_scan.py, just not yet confirmed for THIS sport).

Usage: python3 nhl_scan.py YYYY-MM-DD [--edge 3] [--discover-oddspapi]
"""
import json, math, os, sys, time, re, unicodedata, datetime, collections, statistics
import urllib.request, concurrent.futures as cf

SGO_KEY = os.environ.get('SGO_KEY', 'bb9cfaece214108ec95bde6bd1b910a5')  # trial — dead 2026-10-04
SGO_EXPIRES = datetime.date(2026, 10, 4)
ODDSPAPI_KEY = os.environ.get('ODDSPAPI_KEY', '15490352-5f73-404d-9964-353ab0783e01')
CACHE = '/tmp/nhl_scan_cache'
os.makedirs(CACHE, exist_ok=True)

# Frozen params (rulebook NHL SOG model, backtested 2026-09-25)
WINS = (0.35, 0.30, 0.25, 0.10)          # season / L20 / L10 / L5 windows (TOI and rate both)
K_RATE, W_OPP, R_NB = 120, 0.5, 40        # shrink window (min), opponent-allowed factor weight, NegBin r (~Poisson)
EDGE_MIN_PIN, EDGE_MIN_EXCH = 0.03, 0.04  # Track B bars: Pinnacle-based >=3pts, exchange-based >=4pts
ZONE = (0.35, 0.75)
GH = [(-1.3556, 0.1995), (0.0, 0.6005), (1.3556, 0.1995)]  # 3-pt Gauss-Hermite (same as nba_scan.py)
NHL_TEAMS = ['ANA', 'BOS', 'BUF', 'CGY', 'CAR', 'CHI', 'COL', 'CBJ', 'DAL', 'DET', 'EDM', 'FLA', 'LAK',
             'MIN', 'MTL', 'NSH', 'NJD', 'NYI', 'NYR', 'OTT', 'PHI', 'PIT', 'SJS', 'SEA', 'STL', 'TBL',
             'TOR', 'UTA', 'VAN', 'VGK', 'WSH', 'WPG']


def get(url, headers=None, tries=3):
    """NHL API (api-web.nhle.com) blocks Python's default User-Agent with a 403 -- confirmed
    2026-09-27, same issue as Pinnapi (see rulebook Credits & pulls). Always send curl/8 unless
    the caller passes its own headers (SGO needs x-api-key instead)."""
    h = {'User-Agent': 'curl/8'}
    h.update(headers or {})
    for _ in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30))
        except Exception:
            time.sleep(1.5)
    return None


def nrm(s):
    return re.sub(r'[^a-z]', '', unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower())


def american_to_prob(american):
    try:
        a = float(american)
    except (TypeError, ValueError):
        return None
    return 100.0 / (a + 100.0) if a > 0 else -a / (-a + 100.0)


def devig_power(p_a, p_b):
    """Power method: same implementation as softness_scan.py / nba_scan.py (verified consistent)."""
    lo, hi = 0.01, 10.0
    for _ in range(200):
        mid = (lo + hi) / 2
        s = p_a ** (1 / mid) + p_b ** (1 / mid)
        if s > 1:
            hi = mid
        else:
            lo = mid
    k = (lo + hi) / 2
    fa, fb = p_a ** (1 / k), p_b ** (1 / k)
    return fa / (fa + fb)


def nb_sf(k, mu, r):
    if mu <= 0:
        return 0.0
    p = r / (r + mu)
    pm = 0.0
    term = p ** r
    for x in range(k):
        if x > 0:
            term *= (x - 1 + r) / x * (1 - p)
        pm += term
    return max(0.0, 1 - pm)


# ---------- 1. NHL API box scores (cached) ----------
_roster_cache = {}


def roster(team, season):
    """{playerId: 'First Last'} for one team/season. Confirmed schema 2026-09-27:
    GET /v1/roster/{team}/{season} -> {forwards, defensemen, goalies}, each row has
    id, firstName.default, lastName.default."""
    key = (team, season)
    if key not in _roster_cache:
        fn = f'{CACHE}/roster_{team}_{season}.json'
        if os.path.exists(fn):
            _roster_cache[key] = json.load(open(fn))
        else:
            j = get(f"https://api-web.nhle.com/v1/roster/{team}/{season}") or {}
            names = {}
            for grp in ('forwards', 'defensemen', 'goalies'):
                for p in j.get(grp, []):
                    names[str(p['id'])] = f"{p['firstName']['default']} {p['lastName']['default']}"
            json.dump(names, open(fn, 'w'))
            _roster_cache[key] = names
    return _roster_cache[key]


def toi_to_min(s):
    if not s:
        return 0.0
    m, sec = s.split(':')
    return int(m) + int(sec) / 60


def season_game_ids(start, end):
    """[(gameId, gameType)] for finished regular-season games in [start, end]. Confirmed schema
    2026-09-27: GET /v1/score/{date} -> games[] with id, gameType (1=pre, 2=regular, 3=playoffs),
    gameState (FUT/OFF/FINAL)."""
    days = [start + datetime.timedelta(days=i) for i in range((end - start).days + 1)]

    def one(d):
        fn = f'{CACHE}/sc_{d}.json'
        if os.path.exists(fn) and d < datetime.date.today() - datetime.timedelta(days=1):
            return json.load(open(fn))
        j = get(f"https://api-web.nhle.com/v1/score/{d:%Y-%m-%d}")
        if j is None:
            return []
        ids = [(g['id'], g.get('gameType')) for g in j.get('games', []) if g.get('gameState') in ('OFF', 'FINAL')]
        json.dump(ids, open(fn, 'w'))
        return ids

    with cf.ThreadPoolExecutor(8) as ex:
        res = list(ex.map(one, days))
    return [x for r in res for x in r if x[1] == 2]  # regular season only


def box(gid):
    """Per-player rows for one finished game: pid, team, opp, home, min (TOI), sog. Confirmed schema
    2026-09-27: GET /v1/gamecenter/{gid}/boxscore -> playerByGameStats.{home,away}Team.{forwards,
    defense}[], each row has playerId, toi 'MM:SS', sog."""
    fn = f'{CACHE}/bx_{gid}.json'
    if os.path.exists(fn):
        return json.load(open(fn))
    j = get(f"https://api-web.nhle.com/v1/gamecenter/{gid}/boxscore")
    if not j:
        return None
    home_ab, away_ab = j['homeTeam']['abbrev'], j['awayTeam']['abbrev']
    rows = []
    for side, ab, opp, is_home in ((j['playerByGameStats']['homeTeam'], home_ab, away_ab, True),
                                    (j['playerByGameStats']['awayTeam'], away_ab, home_ab, False)):
        for grp in ('forwards', 'defense'):
            for p in side.get(grp, []):
                mn = toi_to_min(p.get('toi'))
                if mn <= 0:
                    continue
                rows.append(dict(pid=str(p['playerId']), team=ab, opp=opp, home=is_home, min=mn, sog=int(p.get('sog', 0) or 0)))
    out = dict(id=gid, date=j.get('gameDate'), rows=rows)
    json.dump(out, open(fn, 'w'))
    return out


def load_history(today):
    y = today.year if today.month >= 7 else today.year - 1
    ids = [g[0] for g in season_game_ids(datetime.date(y - 1, 10, 1), datetime.date(y, 6, 30))] + \
          [g[0] for g in season_game_ids(datetime.date(y, 10, 1), today - datetime.timedelta(days=1))]
    with cf.ThreadPoolExecutor(8) as ex:
        G = [g for g in ex.map(box, ids) if g]
    G.sort(key=lambda g: g['date'] or '')
    return G


# ---------- 2. model state ----------
def build_state(G):
    hist = collections.defaultdict(list)
    allow = collections.defaultdict(lambda: [0, 0.0])  # team -> [sog_allowed, min_allowed]
    lg_sog, lg_min = 0, 0.0
    for g in G:
        for r in g['rows']:
            hist[r['pid']].append(r)
            allow[r['opp']][0] += r['sog']
            allow[r['opp']][1] += r['min']
            lg_sog += r['sog']
            lg_min += r['min']
    return hist, allow, lg_sog, lg_min


def player_mu(pid, opp, state):
    hist, allow, lg_sog, lg_min = state
    h = hist[pid]
    if len(h) < 10:
        return None
    wins = list(zip(WINS, (h, h[-20:], h[-10:], h[-5:])))
    mhat = sum(w * statistics.mean(x['min'] for x in s) for w, s in wins)
    sd = statistics.pstdev([x['min'] for x in h[-20:]]) or 1.0
    prior = lg_sog / lg_min if lg_min else 0.5
    rate = sum(w * ((sum(x['sog'] for x in s) + K_RATE * prior) / (sum(x['min'] for x in s) + K_RATE)) for w, s in wins)
    a_sog, a_min = allow[opp]
    f = 1 + W_OPP * ((a_sog / a_min) / prior - 1) if a_min and prior else 1
    return dict(mhat=mhat, sd=sd, rate=rate * f)


def prob_over(m, k):
    """P(SOG >= k) via NegBin(r=40) mixed over TOI uncertainty (3-pt Gauss-Hermite, matches nba_scan.py)."""
    return sum(w * nb_sf(k, m['rate'] * max(m['mhat'] + z * m['sd'], 1), R_NB) for z, w in GH)


# ---------- 3. Pinnacle fair + execution venues ----------
def sgo_events(day, league='NHL'):
    d0, d1 = day.isoformat(), (day + datetime.timedelta(days=1)).isoformat()
    j = get(f"https://api.sportsgameodds.com/v2/events?leagueID={league}&startsAfter={d0}&startsBefore={d1}&limit=50",
            headers={'x-api-key': SGO_KEY}) or {}
    return j.get('data', []) if j.get('success') else []


def sgo_sog_odds(event_id):
    """{(norm_name, line): {venue_slug: fair_under_prob}} for one event's shots_onGoal market.
    Confirmed 2026-09-27: only pinnacle + fliff actually carry this market on SGO for NHL — novig
    and prophetexchange were checked on 3 separate dates and never present. Don't assume otherwise."""
    j = get(f"https://api.sportsgameodds.com/v2/events?eventID={event_id}&oddsAvailable=true",
            headers={'x-api-key': SGO_KEY}) or {}
    if not j.get('success') or not j.get('data'):
        return {}
    odds = j['data'][0].get('odds', {})
    out = {}
    for k, v in odds.items():
        if not (k.startswith('shots_onGoal-') and k.endswith('-game-ou-over')):
            continue
        v_u = odds.get(k.replace('-over', '-under'))
        if not v_u:
            continue
        raw = v.get('statEntityID', '')  # e.g. AARON_EKBLAD_1_NHL
        name = raw.rsplit('_', 2)[0].replace('_', ' ').title() if raw.endswith('_NHL') else raw
        line = v.get('bookOverUnder') or v.get('fairOverUnder')
        if line is None:
            continue
        for slug in ('pinnacle', 'fliff', 'novig', 'prophetexchange'):
            bo, bu = v.get('byBookmaker', {}).get(slug), v_u.get('byBookmaker', {}).get(slug)
            if not bo or not bu:
                continue
            po, pu = american_to_prob(bo.get('odds')), american_to_prob(bu.get('odds'))
            if po is None or pu is None:
                continue
            fair_over = devig_power(po, pu)
            out.setdefault((nrm(name), float(line)), {})[slug] = round(1 - fair_over, 4)  # UNDER fair prob
    return out


def discover_oddspapi_nhl_ids():
    """Run this FIRST once the OddsPapi paid tier is confirmed live. Prints the sports list (find
    NHL's sportId) and, once you have it, look up its markets to find the Shots On Goal O/U
    marketId — then hardcode both at the top of this file. Every attempt this session has 429'd,
    so nothing here is a guess; it's unverified and left that way on purpose."""
    if not ODDSPAPI_KEY:
        print('No ODDSPAPI_KEY set.')
        return None
    sports = get(f"https://api.oddspapi.io/v4/sports?apiKey={ODDSPAPI_KEY}")
    print('sports response (look for NHL / Ice Hockey):')
    print(json.dumps(sports, indent=2)[:3000] if sports else '(no response / still 429)')
    return sports


# ---------- 4. scan ----------
def todays_games(day):
    j = get(f"https://api-web.nhle.com/v1/score/{day:%Y-%m-%d}") or {}
    return [dict(id=g['id'], home=g['homeTeam']['abbrev'], away=g['awayTeam']['abbrev'],
                 season=g.get('season'), gameType=g.get('gameType'), state=g.get('gameState'))
            for g in j.get('games', [])]


def scan(day):
    games = [g for g in todays_games(day) if g['state'] == 'FUT']
    if not games:
        print('No upcoming NHL games for', day, '(or none returned pre-game state)')
        return []
    if day > SGO_EXPIRES:
        print(f'WARNING: SGO trial key expired {SGO_EXPIRES} — no Pinnacle/Fliff data available from that path anymore.')

    G = load_history(day)
    state = build_state(G)
    hist = state[0]
    print(f"{day}: {len(games)} NHL games | history: {len(G)} games / {len(hist)} players tracked")

    sgo_evs = {e['eventID']: e for e in sgo_events(day)}
    plays = []
    for g in games:
        # match SGO event by team names
        ev_id = None
        for eid, ev in sgo_evs.items():
            th, ta = ev.get('teams', {}).get('home', {}), ev.get('teams', {}).get('away', {})
            if th.get('names', {}).get('short') == g['home'] and ta.get('names', {}).get('short') == g['away']:
                ev_id = eid
                break
        if not ev_id:
            print(f"  {g['away']}@{g['home']}: no matching SGO event (or trial dead) — skip")
            continue
        odds = sgo_sog_odds(ev_id)
        if not odds:
            print(f"  {g['away']}@{g['home']}: no Pinnacle SOG data on SGO for this game (likely too early/thin)")
            continue
        season = g['season'] or (day.year * 10000 + day.year + 1 if day.month >= 7 else (day.year - 1) * 10000 + day.year)
        home_roster, away_roster = roster(g['home'], str(season)), roster(g['away'], str(season))
        name_to_pid = {nrm(n): (pid, g['home']) for pid, n in home_roster.items()}
        name_to_pid.update({nrm(n): (pid, g['away']) for pid, n in away_roster.items()})

        for (norm_name, line), venues in odds.items():
            fair_under = venues.get('pinnacle')
            if fair_under is None:
                continue  # no Pinnacle anchor -> not scanned here (would be Route 3 / model-priced, TODO)
            exec_venue, exec_price = None, None
            for slug in ('fliff', 'novig', 'prophetexchange'):
                if slug in venues:
                    exec_venue, exec_price = slug, venues[slug]
                    break
            if exec_venue is None:
                continue  # Pinnacle listed it but no execution venue quoted it -- nothing to bet
            edge = fair_under - exec_price
            if edge < EDGE_MIN_PIN:  # Pinnacle-based bar (Track B rule)
                continue
            match = name_to_pid.get(norm_name)
            if match:
                pid, team = match
                opp = g['away'] if team == g['home'] else g['home']
                m = player_mu(pid, opp, state)
                if m:
                    model_over = prob_over(m, math.ceil(line))
                    if model_over - (1 - fair_under) >= 0.03:  # model strongly favors Over -> filter out this Under
                        continue
            plays.append(dict(game=f"{g['away']}@{g['home']}", player=norm_name, line=line, venue=exec_venue,
                               fair_under=round(fair_under * 100, 1), price_under=round(exec_price * 100, 1),
                               edge=round(edge * 100, 1)))
    plays.sort(key=lambda p: -p['edge'])
    print(f"\n{len(plays)} Under plays clear the Pinnacle-based edge bar (>= {EDGE_MIN_PIN*100:.0f}pts):")
    for p in plays:
        print(p)
    return plays


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--discover-oddspapi':
        discover_oddspapi_nhl_ids()
    else:
        d = datetime.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith('--') else datetime.date.today()
        scan(d)

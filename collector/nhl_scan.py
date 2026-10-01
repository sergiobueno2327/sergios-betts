"""Sergio's Betts — NHL Shots On Goal (SOG) morning scan (rulebook: NHL SOG model + Track B).
Run in chat:  python3 nhl_scan.py YYYY-MM-DD

Pipeline: NHL API box scores -> TOI x SOG/60 rate model (window-weighted, shrunk, opponent factor,
NegBin r=40) -> Pinnacle no-vig fair price -> price vs execution venues (Kalshi has NO SOG market —
confirmed live 2026-09-27, see rulebook NHL SOG venue-check note). Model contributes ~0 blend weight
vs Pinnacle (rulebook closing-line test: w_model = 0.00) — it's used only as (a) a filter: skip an
Under if the model favors Over by >=3pts even though Pinnacle-vs-exec shows an edge, and (b) a pricer
for players Pinnacle doesn't list (Route 3, paper/unconfirmed). NHL SOG = Unders only. Overs = no bet.

FAIR PRICE + EXECUTION VENUES — rewired 2026-09-28 off the SportsGameOdds trial (dies 2026-10-04)
onto the paid-plan pipeline (same pattern as nfl_scan.py, confirmed live against real 2026-27
season-opener games this session):
  1. Pinnacle fair price: OddsPapi (ODDSPAPI_KEY, Normal tier, Player Props add-on covers NHL,
     real sportId=15). Shots On Goal is catalog marketName "Over Under Player Shots On Goal (incl.
     overtime)" / marketType "playertotals-shotsongoal" (9 line variants in the full /v4/markets
     catalog, e.g. marketId=15136/handicap=0.5) — found via the same full-catalog lookup approach
     as nfl_scan.py (each specific SOG line is its own marketId, not one fixed line like MLB's
     HITS_MARKET). Bookmaker slug is "pinnacle+30"; this key's plan is Pinnacle-only on OddsPapi
     (confirmed live 2026-09-28: requesting novig.us/fliff/kalshi here 400s RESTRICTED_ACCESS) —
     venues come from The Odds API instead, per the actual purchase split. NB: don't pass
     &bookmakers=pinnacle+30 in the URL -- the '+' decodes to a space server-side and 400s; omit
     the param entirely (this key only has access to pinnacle+30 anyway, so it's the default).
     Real finding 2026-09-28: the 2026-27 regular season already started (games from 2026-09-29 on
     carry tournamentSlug=='nhl' with hasOdds=True) -- the old assumption that the season starts
     "after the SGO trial is already dead" was wrong; check hasOdds/startTime, not a guessed
     season-start date. Also confirmed: player props (including SOG) don't post on OddsPapi until
     roughly 24-48h before puck drop even once the game-level markets go live -- a fixture with no
     SOG entries yet isn't a bug, it's just too early to have them.
  2. Execution venues (Novig, Fliff, ProphetX, PrizePicks): The Odds API (THEODDSAPI_KEY, new 20K
     tier key -- NOT the old free-tier key). Real market key: player_shots_on_goal. Same
     /v4/sports/icehockey_nhl/events/{id}/odds?bookmakers=...&markets=player_shots_on_goal
     &oddsFormat=decimal schema as NFL -- bookmakers[].markets[].outcomes[] = {name, description
     (player full name), price, point}.
  3. Kalshi: confirmed on 3 separate dates this season (preseason + two regular-season samples)
     that Kalshi carries NO shots-on-goal market for NHL at all -- not a gap to fix, it just
     doesn't exist there. Not called here.

Known OddsPapi quirk (confirmed live 2026-09-28, not yet fully understood): the /v4/fixtures
response for a given sportId/date range is NOT perfectly consistent call-to-call -- repeated calls
seconds apart sometimes returned 5 real NHL games for 2026-09-29, sometimes only 3 (2 missing
entirely), and hasOdds/statusName have both been seen to flip between calls on the same fixture
(same issue independently confirmed in nfl_scan.py). Don't treat one run's fixture list as
complete or authoritative -- a game silently missing from one run should show up on the next
scheduled re-run as OddsPapi's cache catches up. This is a real, observed platform quirk, not
guessed, and not something this script can fix client-side beyond the retry/backoff already in
get().

Usage: python3 nhl_scan.py YYYY-MM-DD [--edge 3]
"""
import json, math, os, sys, time, re, unicodedata, datetime, collections, statistics
import urllib.request, concurrent.futures as cf

THEODDSAPI_KEY = os.environ.get('THEODDSAPI_KEY', '')
ODDSPAPI_KEY = os.environ.get('ODDSPAPI_KEY', '')
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

# NHL abbrev -> The Odds API's full team name (for matching a fixture to an Odds-API event). Real
# names confirmed live 2026-09-28 -- note "Montréal Canadiens" (accent), "Utah Mammoth" (the
# team's actual current name, not "Utah Hockey Club"), "St Louis Blues" (no period).
ODDSAPI_TEAM_NAME = {
    'ANA': 'Anaheim Ducks', 'BOS': 'Boston Bruins', 'BUF': 'Buffalo Sabres', 'CGY': 'Calgary Flames',
    'CAR': 'Carolina Hurricanes', 'CHI': 'Chicago Blackhawks', 'COL': 'Colorado Avalanche',
    'CBJ': 'Columbus Blue Jackets', 'DAL': 'Dallas Stars', 'DET': 'Detroit Red Wings',
    'EDM': 'Edmonton Oilers', 'FLA': 'Florida Panthers', 'LAK': 'Los Angeles Kings',
    'MIN': 'Minnesota Wild', 'MTL': 'Montréal Canadiens', 'NSH': 'Nashville Predators',
    'NJD': 'New Jersey Devils', 'NYI': 'New York Islanders', 'NYR': 'New York Rangers',
    'OTT': 'Ottawa Senators', 'PHI': 'Philadelphia Flyers', 'PIT': 'Pittsburgh Penguins',
    'SJS': 'San Jose Sharks', 'SEA': 'Seattle Kraken', 'STL': 'St Louis Blues',
    'TBL': 'Tampa Bay Lightning', 'TOR': 'Toronto Maple Leafs', 'UTA': 'Utah Mammoth',
    'VAN': 'Vancouver Canucks', 'VGK': 'Vegas Golden Knights', 'WSH': 'Washington Capitals',
    'WPG': 'Winnipeg Jets',
}


def get(url, headers=None, tries=4):
    """NHL API (api-web.nhle.com) and OddsPapi both block Python's default User-Agent with a 403
    -- always send curl/8."""
    h = {'User-Agent': 'curl/8'}
    h.update(headers or {})
    for a in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30))
        except Exception:
            time.sleep(1.5 * (a + 1))
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


# ---------- 3. OddsPapi: Pinnacle SOG fair price (catalog-based) ----------
_catalog_cache = None
SOG_MARKET_NAME = 'Over Under Player Shots On Goal (incl. overtime)'
SOG_MARKET_TYPE = 'playertotals-shotsongoal'


def oddspapi_catalog():
    """{str(marketId): (handicap, over_outcomeId, under_outcomeId)} for the NHL SOG O/U market.
    Fetches the full ~33k-entry /v4/markets catalog once per run (no sportId/marketId filter is
    offered by the endpoint itself) and filters locally -- mirrors nfl_scan.py."""
    global _catalog_cache
    if _catalog_cache is None:
        cat = get(f"https://api.oddspapi.io/v4/markets?apiKey={ODDSPAPI_KEY}") or []
        lookup = {}
        for c in cat:
            if c.get('sportId') != 15 or not c.get('playerProp'):
                continue
            if c.get('marketName') != SOG_MARKET_NAME or c.get('marketType') != SOG_MARKET_TYPE:
                continue
            oids = {o['outcomeName']: o['outcomeId'] for o in c.get('outcomes', [])}
            lookup[str(c['marketId'])] = (c['handicap'], oids.get('Over'), oids.get('Under'))
        _catalog_cache = lookup
        print(f"  OddsPapi catalog: {len(lookup)} NHL SOG line markets tracked")
    return _catalog_cache


# OddsPapi's own team abbreviations don't always match the NHL API's (NHL_TEAMS above) -- confirmed
# live 2026-10-01 by diffing OddsPapi's full NHL fixture abbrev set against NHL_TEAMS: OddsPapi uses
# LA/NJ/SJ/TB where the NHL API (and this script's games/roster lookups) use LAK/NJD/SJS/TBL. All other
# 28 team abbreviations matched exactly across a 7-day fixture sample. REAL BUG this caused (found
# 2026-10-01): oddspapi_fixtures_for_day's output was keyed on OddsPapi's own abbrevs, so
# scan()'s `oddspapi_fx.get((g['home'], g['away']))` lookup (keyed on NHL API abbrevs) silently
# missed every Devils/Kings/Sharks/Lightning game since the OddsPapi rewire (2026-09-28) -- e.g.
# 2026-10-01's PHI@NJD and TBL@NYR both had real OddsPapi fixtures with hasOdds=True, but were
# printed as "no OddsPapi fixture with odds yet" and skipped entirely, never reaching the Pinnacle
# SOG pull at all. Not a timing issue -- a silent abbreviation mismatch.
ODDSPAPI_TO_NHL_ABBR = {'LA': 'LAK', 'NJ': 'NJD', 'SJ': 'SJS', 'TB': 'TBL'}


def oddspapi_fixtures_for_day(day):
    d0, d1 = day.isoformat(), (day + datetime.timedelta(days=1)).isoformat()
    fx = get(f"https://api.oddspapi.io/v4/fixtures?apiKey={ODDSPAPI_KEY}&sportId=15&from={d0}&to={d1}") or []
    out = []
    for f in fx:
        if f.get('tournamentSlug') != 'nhl':
            continue  # sportId=15 also carries AHL/KHL/juniors/women's -- real NHL only
        home = ODDSPAPI_TO_NHL_ABBR.get(f.get('participant1Abbr'), f.get('participant1Abbr'))
        away = ODDSPAPI_TO_NHL_ABBR.get(f.get('participant2Abbr'), f.get('participant2Abbr'))
        out.append(dict(fixtureId=f['fixtureId'], home=home, away=away,
                         start=f.get('startTime'), hasOdds=f.get('hasOdds')))
    return out


PLAYER_ID_CACHE_PATH = os.path.join(os.path.dirname(__file__), 'data', 'oddspapi_player_id_cache.json')


def _load_player_id_cache():
    """{sportId(str): {playerId(str): 'First Last'}} -- persistent, built as a free byproduct of
    every live oddspapi_pinnacle_sog() call (added 2026-09-28). REASON: confirmed live 2026-09-28
    that OddsPapi's /v4/historical-odds endpoint (needed to re-run the NHL SOG backtest against
    the paid plan's real data) never includes playerName -- only a numeric playerId -- and NO
    REST endpoint resolves that id to a name (checked /v4/participants, /v4/fixture,
    /v4/historical-odds with a playerId filter; confirmed via OddsPapi's own docs pages for
    historical-odds, participants, and the websocket API too -- only the LIVE /v4/odds endpoint
    carries playerName, and only for current/future fixtures). Since these numeric ids are stable
    per real player (confirmed by design intent, not yet independently cross-checked across two
    live pulls of the same player), caching every id->name pair seen on a live pull turns each
    day's scan into a standing crosswalk -- by a few weeks into the season this becomes real,
    freshly-sourced data usable for an actual backtest, instead of trying to force the historical
    endpoint to do something it structurally can't."""
    try:
        with open(PLAYER_ID_CACHE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_player_id_cache(cache):
    os.makedirs(os.path.dirname(PLAYER_ID_CACHE_PATH), exist_ok=True)
    with open(PLAYER_ID_CACHE_PATH, 'w') as f:
        json.dump(cache, f, indent=1, sort_keys=True)


def oddspapi_pinnacle_sog(fixture_id):
    """{norm_name: {'name': 'First Last', 'line': float, 'fair_under': prob}} -- UNDER fair prob
    specifically, since NHL SOG = Unders only per the rulebook (Overs = no bet)."""
    lookup = oddspapi_catalog()
    # NB: don't pass &bookmakers=pinnacle+30 -- the '+' decodes to a space server-side and 400s
    # ("Invalid bookmakers: pinnacle 30", found live 2026-09-28). This key's plan only has access
    # to pinnacle+30 anyway, so omitting the param returns it directly.
    j = get(f"https://api.oddspapi.io/v4/odds?apiKey={ODDSPAPI_KEY}&fixtureId={fixture_id}") or {}
    markets = j.get('bookmakerOdds', {}).get('pinnacle+30', {}).get('markets', {})
    out = {}
    cache = _load_player_id_cache()
    sport_cache = cache.setdefault('15', {})
    cache_dirty = False
    for mid, m in markets.items():
        info = lookup.get(mid)
        if not info:
            continue
        handicap, over_id, under_id = info
        outcomes = m.get('outcomes', {})
        over_players = outcomes.get(str(over_id), {}).get('players', {})
        under_players = outcomes.get(str(under_id), {}).get('players', {})
        for pid, po in over_players.items():
            pu = under_players.get(pid)
            if not pu or not po.get('price') or not pu.get('price'):
                continue
            raw = po.get('playerName') or ''
            if ',' in raw:
                last, first = [x.strip() for x in raw.split(',', 1)]
                name = f"{first} {last}"
            else:
                name = raw
            if name and sport_cache.get(pid) != name:
                sport_cache[pid] = name
                cache_dirty = True
            fair_over = devig_power(1.0 / po['price'], 1.0 / pu['price'])
            out[nrm(name)] = dict(name=name, line=float(handicap), fair_under=1 - fair_over)
    if cache_dirty:
        _save_player_id_cache(cache)
    return out


# ---------- 4. The Odds API: Novig / Fliff / ProphetX / PrizePicks ----------
def oddsapi_events_for_week():
    return get(f"https://api.the-odds-api.com/v4/sports/icehockey_nhl/events?apiKey={THEODDSAPI_KEY}") or []


def match_oddsapi_event(events, home_abbr, away_abbr):
    home_full, away_full = ODDSAPI_TEAM_NAME.get(home_abbr), ODDSAPI_TEAM_NAME.get(away_abbr)
    for e in events:
        if e.get('home_team') == home_full and e.get('away_team') == away_full:
            return e['id']
    return None


def oddsapi_sog_venues(event_id):
    """{norm_name: {line: {'venue': slug, 'under': prob}}} -- raw one-sided UNDER price, NOT
    de-vigged (matches nfl_scan.py / softness_scan.py -- de-vigging an execution venue would erase
    the mispricing Track B is looking for)."""
    url = (f"https://api.the-odds-api.com/v4/sports/icehockey_nhl/events/{event_id}/odds"
           f"?apiKey={THEODDSAPI_KEY}&bookmakers=novig,fliff,prophetx,prizepicks&markets=player_shots_on_goal&oddsFormat=decimal")
    j = get(url) or {}
    out = collections.defaultdict(dict)
    for bm in j.get('bookmakers', []):
        venue = bm['key']
        for mkt in bm.get('markets', []):
            if mkt.get('key') != 'player_shots_on_goal':
                continue
            for o in mkt.get('outcomes', []):
                if o.get('name') != 'Under' or not o.get('price') or o.get('point') is None or not o.get('description'):
                    continue
                out[nrm(o['description'])][float(o['point'])] = dict(venue=venue, under=1.0 / o['price'])
    return out


# ---------- 5. scan ----------
def todays_games(day):
    j = get(f"https://api-web.nhle.com/v1/score/{day:%Y-%m-%d}") or {}
    return [dict(id=g['id'], home=g['homeTeam']['abbrev'], away=g['awayTeam']['abbrev'],
                 season=g.get('season'), gameType=g.get('gameType'), state=g.get('gameState'),
                 start=g.get('startTimeUTC'))
            for g in j.get('games', [])]


def scan(day):
    games = [g for g in todays_games(day) if g['state'] == 'FUT']
    if not games:
        print('No upcoming NHL games for', day, '(or none returned pre-game state)')
        return []

    G = load_history(day)
    state = build_state(G)
    hist = state[0]
    print(f"{day}: {len(games)} NHL games | history: {len(G)} games / {len(hist)} players tracked")

    oddspapi_fx = {(f['home'], f['away']): f for f in oddspapi_fixtures_for_day(day) if f['hasOdds']}
    oddsapi_events = oddsapi_events_for_week()
    plays = []
    for g in games:
        fx = oddspapi_fx.get((g['home'], g['away']))
        if not fx:
            print(f"  {g['away']}@{g['home']}: no OddsPapi fixture with odds yet for this game -- skip")
            continue
        pin = oddspapi_pinnacle_sog(fx['fixtureId'])
        if not pin:
            print(f"  {g['away']}@{g['home']}: no Pinnacle SOG data yet (props usually post 24-48h out) -- skip")
            continue
        event_id = match_oddsapi_event(oddsapi_events, g['home'], g['away'])
        venues = oddsapi_sog_venues(event_id) if event_id else {}
        if not event_id:
            print(f"  {g['away']}@{g['home']}: no matching event on The Odds API -- no execution venue for this game")

        season = g['season'] or (day.year * 10000 + day.year + 1 if day.month >= 7 else (day.year - 1) * 10000 + day.year)
        home_roster, away_roster = roster(g['home'], str(season)), roster(g['away'], str(season))
        name_to_pid = {nrm(n): (pid, g['home']) for pid, n in home_roster.items()}
        name_to_pid.update({nrm(n): (pid, g['away']) for pid, n in away_roster.items()})

        for norm_name, p in pin.items():
            fair_under, line = p['fair_under'], p['line']
            exec_venue, exec_price = None, None
            for exec_line, info in venues.get(norm_name, {}).items():
                if abs(exec_line - line) > 0.01:
                    continue  # different line than Pinnacle's -- not a clean comparison, skip
                exec_venue, exec_price = info['venue'], info['under']
                break
            if exec_venue is None:
                continue  # Pinnacle listed it but no execution venue quoted this exact line
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
            plays.append(dict(game=f"{g['away']}@{g['home']}", player=p['name'], line=line, venue=exec_venue,
                               fair_under=round(fair_under * 100, 1), price_under=round(exec_price * 100, 1),
                               edge=round(edge * 100, 1), start=g.get('start'), date=day.isoformat()))
    plays.sort(key=lambda p: -p['edge'])
    print(f"\n{len(plays)} Under plays clear the Pinnacle-based edge bar (>= {EDGE_MIN_PIN*100:.0f}pts):")
    for p in plays:
        print(p)
    return plays


def to_ledger_docs(plays):
    """Turn flagged Under plays into ledger-ready docs (rulebook grader fields: kalshi_ticker,
    start, pinn, side, sport). Mirrors softness_scan.py's to_ledger_docs -- built 2026-09-28,
    NHL had never had this despite the script existing since 2026-09-27 (flagged as a known gap
    in the rulebook). kalshi_ticker is ALWAYS null here, not a lookup failure: confirmed
    repeatedly (2026-09-25, rechecked 2026-09-27, rechecked again 2026-09-28) that Kalshi carries
    no NHL shots-on-goal market at all -- the execution venue is always Novig/ProphetX/Fliff/
    PrizePicks via The Odds API (see `venue` on each play), never Kalshi. The nightly grader
    (collector/grade_plays.py) needs its own venue-close-price step for these rather than a
    Kalshi close -- not built yet, same gap softness_scan.py has for its non-Kalshi plays."""
    docs = {}
    for p in plays:
        slug = p['player'].split()[-1].lower()
        doc_id = f"{p['date']}-nhl-{slug}-sog{str(p['line']).replace('.', '')}-u"
        if doc_id in docs and docs[doc_id]['data']['edge'] <= p['edge']:
            continue
        note = f"NHL SOG scan {p['date']}. Edge +{p['edge']}pts vs {p['venue']}. Pinnacle fair {p['fair_under']}c (Under)."
        note += f" Bet placed on {p['venue']}, not Kalshi -- Kalshi carries no NHL SOG market (confirmed repeatedly); needs manual {p['venue']} close for CLV, not a Kalshi close."
        if not p['start']:
            note += " Start time missing from NHL API response -- check before logging."
        docs[doc_id] = dict(id=doc_id, data=dict(
            date=p['date'], game=p['game'], player=p['player'], market=f"Shots On Goal {p['line']}",
            entry=p['price_under'], fair=p['fair_under'],
            fairSource=f'Pinnacle no-vig SOG (OddsPapi, nhl_scan.py) vs {p["venue"]}',
            edge=p['edge'], side='NO', sport='NHL', track='B', stake=0, fee=0, orderType='taker',
            kalshi_ticker=None, start=p['start'],
            pinn=dict(who=p['player'], mkt='prop:Shots On Goal', line=p['line']),
            status=f"Paper — daily scan, pending fill ({p['venue']})",
            note=note, close=None, result=None, zone='35–75'))
    return list(docs.values())


if __name__ == '__main__':
    d = datetime.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith('--') else datetime.date.today()
    plays = scan(d)
    if '--write' in sys.argv:
        outpath = sys.argv[sys.argv.index('--write') + 1]
        docs = to_ledger_docs(plays)
        with open(outpath, 'w') as fh:
            json.dump(docs, fh, indent=2)
        print(f'\nWrote {len(docs)} ledger-ready docs to {outpath} (kalshi_ticker always null -- see to_ledger_docs docstring)')
        print('Not written to the ledger yet -- the calling session still does the ArtifactData batch write.')

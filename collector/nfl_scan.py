"""Sergio's Betts — NFL Track B scan (rulebook: Track B — Pure +EV, paper only; no model needed,
NFL's own model is "not ready" per the receptions-model note — this is pure market-mispricing
scanning, same idea as softness_scan.py but for NFL player props).

Run in chat: python3 nfl_scan.py [--edge 3] [--write out.json]

FAIR PRICE + EXECUTION VENUES — rewired 2026-09-27/28 off the SportsGameOdds trial (dies
2026-10-04) onto the paid-plan pipeline confirmed live this session:

  1. Pinnacle fair price: OddsPapi (ODDSPAPI_KEY, Normal tier, Basketball/Football/Hockey +
     Player Props add-on, $60/mo). Real, tested schema:
       - Bookmaker slug is "pinnacle+30", NOT "pinnacle" (bare "pinnacle" returns 0 markets —
         found and fixed live 2026-09-28). This key's Bookmakers Access is Pinnacle-only —
         requesting novig.us/fliff/kalshi/prophetx on OddsPapi now 400s with RESTRICTED_ACCESS
         (confirmed live 2026-09-28, post-purchase) — those venues come from The Odds API below,
         per the actual purchase split ("the other 5 venues come from odds api").
       - Player props do NOT use one fixed marketId per stat like MLB's HITS_MARKET_OVER/UNDER —
         each specific line (e.g. Pass Yards 100.5 vs 101.5) is its own marketId. The only correct
         way to know what a marketId means is GET /v4/markets?apiKey=X (no other params — this
         returns the FULL catalog, ~33k entries across all sports, one-time-per-run fetch) and
         filter by sportId==14, playerProp==true, and the exact (marketName, marketType) pairs
         hardcoded in STAT_MARKETS below. Confirmed live 2026-09-28 against CHI@PHI (fixtureId
         id1400003171515840): 49 of our 9 stats' markets were live-priced for that one game.
       - Fixtures: GET /v4/fixtures?apiKey=X&sportId=14&from=YYYY-MM-DD&to=YYYY-MM-DD, filter
         tournamentSlug=='nfl' (the raw feed also carries NCAA/CFL games under sportId=14) and
         statusName=='Pre-Game'. participant1 = home, participant2 = away (confirmed by
         cross-checking against The Odds API's home_team/away_team for the same real game).
       - Odds: GET /v4/odds?apiKey=X&fixtureId=Y -> bookmakerOdds["pinnacle+30"].markets[marketId]
         .outcomes[outcomeId].players[playerId] = {playerName ("Last, First"), price (decimal),
         priceAmerican, limit, ...}. A market entry's "outcomes" dict already contains BOTH the
         Over and Under outcomeId branches (from the catalog lookup) — no second call needed.
  2. Execution venues (Novig, Fliff, ProphetX, PrizePicks): The Odds API (THEODDSAPI_KEY, new 20K
     tier key, $30/mo — NOT the old free-tier key, which stays capped at 500/mo). Confirmed live
     2026-09-28 against the same CHI@PHI game via GET /v4/sports/americanfootball_nfl/events/{id}
     /odds?apiKey=X&bookmakers=novig,fliff,prophetx,prizepicks&markets=<9 keys>&oddsFormat=decimal
     -> bookmakers[].markets[].outcomes[] = {name: Over/Under, description: player full name
     "First Last", price: decimal, point: the O/U line}. Kalshi is CONFIRMED NOT present on this
     API for NFL player props (tested repeatedly all session, zero rows) — use Kalshi's own API
     for that venue specifically, not this one.
  3. Kalshi (direct, no key needed): api.elections.kalshi.com. Confirmed live 2026-09-28 for
     KXNFLPASSYDS: GET /events?series_ticker=KXNFLPASSYDS -> event_ticker format
     KXNFLPASSYDS-{YYMONDD ET game date}{AWAY?}{HOME?} (team-code order not guaranteed, matched by
     substring only). GET /markets?event_ticker=X -> each market has floor_strike (== Pinnacle's
     handicap directly, e.g. 224.5), title "{Player Full Name}: {strike}+ {stat}" (reliable name
     source — no jersey-number reconstruction needed), yes_bid_dollars/yes_ask_dollars/
     no_bid_dollars/no_ask_dollars as strings. Yes side = Over, No side = Under. Matched here by
     (normalized player name from title, floor_strike == Pinnacle's line) — exact, not fuzzy.

Gate 1 mandatory check (rulebook, added 2026-09-27 after the Kyler Murray/Arizona mix-up): every
player's CURRENT team is verified against a live ESPN roster pull before a play is logged — never
from memory, never from just trusting the data provider's own team label.
"""
import pin_move
import pp_fliff
import json, math, os, sys, time, re, unicodedata, datetime, collections, urllib.request
from zoneinfo import ZoneInfo

THEODDSAPI_KEY = os.environ.get('THEODDSAPI_KEY', '')
ODDSPAPI_KEY = os.environ.get('ODDSPAPI_KEY', '')
KALSHI_BASE = 'https://api.elections.kalshi.com/trade-api/v2'

EDGE_MIN_PIN, EDGE_MIN_EXCH = 0.03, 0.04  # Track B bars: Pinnacle-based >=3pts, exchange-based >=4pts
ZONE = (0.35, 0.75)
MAX_PIN_VIG = 0.08  # market-width filter (added 2026-10-04): skip plays where Pinnacle's own two-sided margin > 8% (low sharp confidence)
# Execution venues (Kalshi handled separately via its direct API). Widened 2026-10-04 at Sergio's request: he can bet at ANY book, not
# just exchanges, so scans take the best price across all of these (venue is tagged on every play). Override with env BETTS_BOOKS=a,b,c
# (The Odds API bookmaker keys) once Sergio gives his exact list/state. DFS apps (prizepicks/underdog) are pick'em style, kept only prizepicks.
# LOCKED 2026-10-05 to Sergio's actual linked accounts (Upside 'Linked Accounts' screenshot): Active = DK Pick6, Fliff, Kalshi, Novig,
# PrizePicks, Sleeper, Underdog (+1 cut off at top); Needs attention = Onyx (invalid credentials), ProphetX (2FA error) -> ProphetX dropped
# until fixed. Kalshi is pulled directly. DK Pick6 / Sleeper / Underdog are pick'em apps with no tradable price in the APIs (manual only).
_DEFAULT_BOOKS = 'novig,prizepicks'
ODDSAPI_VENUES = tuple(b for b in os.environ.get('BETTS_BOOKS', _DEFAULT_BOOKS).split(',') if b)

# stat -> (OddsPapi marketName, OddsPapi marketType, The Odds API market key, Kalshi series
# ticker, Pinnacle-unit label for the rulebook's grader `pinn.mkt` field). All confirmed real
# 2026-09-27/28, none guessed.
STAT_MARKETS = {
    'passing_interceptions': ('Over Under Player Interceptions (incl. overtime)', 'playertotals-interceptions', 'player_pass_interceptions', 'KXNFLPASSINT', 'Interceptions'),
    'passing_yards':         ('Over Under Pass Yards (incl. overtime)', 'playertotals-passyards', 'player_pass_yds', 'KXNFLPASSYDS', 'Passing Yards'),
    'passing_completions':   ('Over Under Pass Completions (incl. overtime)', 'playertotals-passcompletions', 'player_pass_completions', 'KXNFLPASSCOMP', 'Pass Completions'),
    'passing_attempts':      ('Over Under Pass Attempts (incl. overtime)', 'playertotals-passattempts', 'player_pass_attempts', 'KXNFLPASSATT', 'Pass Attempts'),
    'passing_touchdowns':    ('Over Under Player TD Passes (incl. overtime)', 'playertotals-tdpasses', 'player_pass_tds', 'KXNFLPASSTDS', 'Touchdown Passes'),
    'receiving_yards':       ('Over Under Player Receiving Yards (incl. overtime)', 'playertotals-receivingyards', 'player_reception_yds', 'KXNFLRECYDS', 'Receiving Yards'),
    'receptions':            ('Over Under Player Receptions (incl. overtime)', 'playertotals-receptions', 'player_receptions', 'KXNFLREC', 'Receptions'),
    'rushing_yards':         ('Over Under Rush Yards (incl. overtime)', 'playertotals-rushyards', 'player_rush_yds', 'KXNFLRSHYDS', 'Rushing Yards'),
    'rushing_attempts':      ('Over Under Rush Attempts (incl. overtime)', 'playertotals-rushattempts', 'player_rush_attempts', 'KXNFLRSHATT', 'Rush Attempts'),
}

# Static team map — team abbreviations don't change season to season, unlike player rosters, so
# this is safe to hardcode (contrast with player-team pairing, which must always be verified live).
NFL_TEAMS = {
    'ARI': 'ari', 'ATL': 'atl', 'BAL': 'bal', 'BUF': 'buf', 'CAR': 'car', 'CHI': 'chi', 'CIN': 'cin',
    'CLE': 'cle', 'DAL': 'dal', 'DEN': 'den', 'DET': 'det', 'GB': 'gb', 'HOU': 'hou', 'IND': 'ind',
    'JAX': 'jax', 'KC': 'kc', 'LAC': 'lac', 'LAR': 'lar', 'LV': 'lv', 'MIA': 'mia', 'MIN': 'min',
    'NE': 'ne', 'NO': 'no', 'NYG': 'nyg', 'NYJ': 'nyj', 'PHI': 'phi', 'PIT': 'pit', 'SEA': 'sea',
    'SF': 'sf', 'TB': 'tb', 'TEN': 'ten', 'WAS': 'wsh',
}

# OddsPapi/Odds-API abbrev -> The Odds API's full team name (for matching a fixture to an event).
ODDSAPI_TEAM_NAME = {
    'ARI': 'Arizona Cardinals', 'ATL': 'Atlanta Falcons', 'BAL': 'Baltimore Ravens', 'BUF': 'Buffalo Bills',
    'CAR': 'Carolina Panthers', 'CHI': 'Chicago Bears', 'CIN': 'Cincinnati Bengals', 'CLE': 'Cleveland Browns',
    'DAL': 'Dallas Cowboys', 'DEN': 'Denver Broncos', 'DET': 'Detroit Lions', 'GB': 'Green Bay Packers',
    'HOU': 'Houston Texans', 'IND': 'Indianapolis Colts', 'JAX': 'Jacksonville Jaguars', 'KC': 'Kansas City Chiefs',
    'LAC': 'Los Angeles Chargers', 'LAR': 'Los Angeles Rams', 'LV': 'Las Vegas Raiders', 'MIA': 'Miami Dolphins',
    'MIN': 'Minnesota Vikings', 'NE': 'New England Patriots', 'NO': 'New Orleans Saints', 'NYG': 'New York Giants',
    'NYJ': 'New York Jets', 'PHI': 'Philadelphia Eagles', 'PIT': 'Pittsburgh Steelers', 'SEA': 'Seattle Seahawks',
    'SF': 'San Francisco 49ers', 'TB': 'Tampa Bay Buccaneers', 'TEN': 'Tennessee Titans', 'WAS': 'Washington Commanders',
}


def get(url, headers=None, tries=4):
    """OddsPapi/SGO/ESPN all block Python's default User-Agent with a 403 -- always send curl/8.
    Kalshi's public API rate-limits aggressively (429s seen live) -- the extra try + backoff here
    covers that too."""
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


def merge_best(cur, venue, over=None, under=None):
    """Keep the BEST (lowest implied prob = cheapest) price per side across ALL books, with the book that
    offers it. Fixed 2026-10-04: previously only the LAST book's price per line survived, and books quoting
    only one side were dropped, so the scan evaluated one arbitrary book per line."""
    cur = cur or dict(venue=venue, over=None, under=None, over_venue=None, under_venue=None)
    if over is not None and (cur['over'] is None or over < cur['over']):
        cur['over'], cur['over_venue'] = over, venue
    if under is not None and (cur['under'] is None or under < cur['under']):
        cur['under'], cur['under_venue'] = under, venue
    cur['venue'] = cur['over_venue'] or cur['under_venue'] or venue
    return cur


def side_candidates(info, fair_over, edge_min, zone):
    """[(side, edge, price, venue)] for each side of a merged-best venue dict that clears the bar."""
    out = []
    for side, key in (('Over', 'over'), ('Under', 'under')):
        price = info.get(key)
        if price is None:
            continue
        edge = (fair_over if side == 'Over' else 1 - fair_over) - price
        if edge >= edge_min and zone[0] <= price <= zone[1]:
            out.append((side, edge, price, info.get(key + '_venue') or info['venue']))
    return out


def american_to_prob(american):
    try:
        a = float(american)
    except (TypeError, ValueError):
        return None
    return 100.0 / (a + 100.0) if a > 0 else -a / (-a + 100.0)


def devig_power(p_a, p_b):
    """Power method — same implementation as softness_scan.py / nba_scan.py / nhl_scan.py."""
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


# ---------- Gate 1 mandatory check: live team verification ----------
_roster_cache = {}


def verify_player_team(player_name, team_abbrev):
    """True/False/None (None = couldn't check, e.g. bad team code). Pulls the live ESPN roster —
    never trust memory or the odds provider's own team label for this (rulebook rule, added
    2026-09-27 after the Kyler Murray/Arizona mix-up)."""
    espn_abbr = NFL_TEAMS.get(team_abbrev.upper())
    if not espn_abbr:
        return None
    if espn_abbr not in _roster_cache:
        j = get(f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{espn_abbr}/roster") or {}
        names = set()
        for grp in j.get('athletes', []):
            for p in grp.get('items', []):
                names.add(nrm(p.get('fullName', '')))
        _roster_cache[espn_abbr] = names
    return nrm(player_name) in _roster_cache[espn_abbr]


# ---------- 1. OddsPapi: NFL fixtures + Pinnacle fair price (catalog-based) ----------
_catalog_cache = None


def oddspapi_catalog():
    """{str(marketId): (stat, handicap, over_outcomeId, under_outcomeId)} for our 9 NFL stats.
    Fetches the full ~33k-entry /v4/markets catalog once per run (no sportId/marketId filter is
    offered by the endpoint itself) and filters locally."""
    global _catalog_cache
    if _catalog_cache is None:
        cat = get(f"https://api.oddspapi.io/v4/markets?apiKey={ODDSPAPI_KEY}") or []
        lookup = {}
        wanted = {(mname, mtype): stat for stat, (mname, mtype, *_r) in STAT_MARKETS.items()}
        for c in cat:
            if c.get('sportId') != 14 or not c.get('playerProp'):
                continue
            stat = wanted.get((c.get('marketName'), c.get('marketType')))
            if not stat:
                continue
            oids = {o['outcomeName']: o['outcomeId'] for o in c.get('outcomes', [])}
            lookup[str(c['marketId'])] = (stat, c['handicap'], oids.get('Over'), oids.get('Under'))
        _catalog_cache = lookup
        print(f"  OddsPapi catalog: {len(lookup)} NFL player-prop markets tracked across 9 stats")
    return _catalog_cache


def oddspapi_fixtures_this_week(days=6):
    today = datetime.date.today()
    d0, d1 = today.isoformat(), (today + datetime.timedelta(days=days)).isoformat()
    fx = get(f"https://api.oddspapi.io/v4/fixtures?apiKey={ODDSPAPI_KEY}&sportId=14&from={d0}&to={d1}") or []
    out = []
    for f in fx:
        if f.get('tournamentSlug') != 'nfl':
            continue  # sportId=14 also carries NCAA/CFL games -- real NFL only
        out.append(dict(fixtureId=f['fixtureId'], home=f.get('participant1Abbr'), away=f.get('participant2Abbr'),
                         start=f.get('startTime'), status=f.get('statusName'), hasOdds=f.get('hasOdds')))
    return out


def _not_kicked_off(start_iso):
    try:
        dt = datetime.datetime.fromisoformat(start_iso.replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return False
    return dt > datetime.datetime.now(datetime.timezone.utc)


def oddspapi_pinnacle_odds(fixture_id):
    """{stat: {norm_name: {'name': 'First Last', 'line': float, 'fair_over': prob}}}. Only the
    catalog-matched marketIds present in this fixture's real pinnacle+30 odds are used -- no
    guessing at which lines exist for a given game."""
    lookup = oddspapi_catalog()
    # NB: don't pass &bookmakers=pinnacle+30 -- the '+' decodes to a space server-side and 400s
    # ("Invalid bookmakers: pinnacle 30", found live 2026-09-28). This key's plan only has access
    # to pinnacle+30 anyway, so just omitting the param returns it directly.
    j = get(f"https://api.oddspapi.io/v4/odds?apiKey={ODDSPAPI_KEY}&fixtureId={fixture_id}") or {}
    markets = j.get('bookmakerOdds', {}).get('pinnacle+30', {}).get('markets', {})
    out = collections.defaultdict(dict)
    for mid, m in markets.items():
        info = lookup.get(mid)
        if not info:
            continue
        stat, handicap, over_id, under_id = info
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
            fair_over = devig_power(1.0 / po['price'], 1.0 / pu['price'])
            out[stat][nrm(name)] = dict(name=name, line=float(handicap), fair_over=fair_over,
                                         vig=round(1.0 / po['price'] + 1.0 / pu['price'] - 1, 4))
    return out


# ---------- 2. The Odds API: Novig / Fliff / ProphetX / PrizePicks ----------
def oddsapi_events_this_week():
    return get(f"https://api.the-odds-api.com/v4/sports/americanfootball_nfl/events?apiKey={THEODDSAPI_KEY}") or []


def match_oddsapi_event(events, home_abbr, away_abbr):
    home_full, away_full = ODDSAPI_TEAM_NAME.get(home_abbr), ODDSAPI_TEAM_NAME.get(away_abbr)
    for e in events:
        if e.get('home_team') == home_full and e.get('away_team') == away_full:
            return e['id']
    return None


def oddsapi_venue_odds(event_id):
    """{stat: {norm_name: {line: {'venue': slug, 'over': prob, 'under': prob}}}} -- raw one-sided
    prices, NOT de-vigged (de-vigging an execution venue would erase the mispricing Track B is
    looking for -- matches softness_scan.py's approach)."""
    mkts = ','.join(v[2] for v in STAT_MARKETS.values())
    url = (f"https://api.the-odds-api.com/v4/sports/americanfootball_nfl/events/{event_id}/odds"
           f"?apiKey={THEODDSAPI_KEY}&bookmakers={','.join(ODDSAPI_VENUES)}&markets={mkts}&oddsFormat=decimal")
    j = get(url) or {}
    mkt_to_stat = {v[2]: k for k, v in STAT_MARKETS.items()}
    out = collections.defaultdict(lambda: collections.defaultdict(dict))
    for bm in j.get('bookmakers', []):
        venue = bm['key']
        for mkt in bm.get('markets', []):
            stat = mkt_to_stat.get(mkt['key'])
            if not stat:
                continue
            by_pl = collections.defaultdict(dict)
            for o in mkt.get('outcomes', []):
                if not o.get('price') or o.get('point') is None or not o.get('description'):
                    continue
                by_pl[(nrm(o['description']), float(o['point']))][o['name']] = 1.0 / o['price']
            for (norm_name, line), sides in by_pl.items():
                if venue == 'prizepicks':  # fixed-payout DFS: not a price -- only record that the leg is offered (see pp_fliff.py)
                    cur = out[stat][norm_name].get(line) or dict(venue=venue, over=None, under=None, over_venue=None, under_venue=None)
                    cur['pp_over'], cur['pp_under'] = 'Over' in sides, 'Under' in sides
                    out[stat][norm_name][line] = cur
                    continue
                cur = merge_best(out[stat][norm_name].get(line), venue, sides.get('Over'), sides.get('Under'))
                out[stat][norm_name][line] = cur
    return out


# ---------- 3. Kalshi direct (the one venue The Odds API doesn't carry for NFL props) ----------
_kalshi_cache = {}


def kalshi_date_tag(start_iso):
    """Kalshi event tickers use the game's ORIGINAL date in US Eastern, not UTC (confirmed live
    2026-09-28: a game at 2026-09-29T00:15Z -- 8:15pm ET the 28th -- tickers as 26SEP28)."""
    dt = datetime.datetime.fromisoformat(start_iso.replace('Z', '+00:00'))
    return dt.astimezone(ZoneInfo('America/New_York')).strftime('%y%b%d').upper()


def kalshi_series_markets(series_ticker, dtag, away, home):
    key = (series_ticker, dtag, away, home)
    if key not in _kalshi_cache:
        evs = get(f"{KALSHI_BASE}/events?series_ticker={series_ticker}") or {}
        markets = []
        for ev in evs.get('events', []) or []:
            et = ev.get('event_ticker', '')
            if dtag in et and away in et and home in et:
                mk = get(f"{KALSHI_BASE}/markets?event_ticker={et}") or {}
                markets.extend(mk.get('markets') or [])
        _kalshi_cache[key] = markets
    return _kalshi_cache[key]


def kalshi_price(series_ticker, dtag, away, home, norm_player_name, line):
    """Matched by (player name parsed from the market's own `title` field, floor_strike == the
    Pinnacle line) -- both are exact real fields, no jersey-number reconstruction needed (unlike
    the ticker suffix). Returns raw one-sided {'over': ask_prob, 'under': ask_prob} or None."""
    for m in kalshi_series_markets(series_ticker, dtag, away, home):
        title = m.get('title', '')
        pname = title.split(':')[0].strip()
        if nrm(pname) != norm_player_name:
            continue
        fs = m.get('floor_strike')
        if fs is None or abs(float(fs) - line) > 0.01:
            continue
        ya, na = m.get('yes_ask_dollars'), m.get('no_ask_dollars')
        if not ya or not na:
            continue
        return dict(over=float(ya), under=float(na))
    return None


def kalshi_ticker(series_ticker, dtag, away, home, norm_player_name, line):
    """Same match logic as kalshi_price (player name from `title`, floor_strike == the Pinnacle
    line) but returns the market's own `ticker` field, for logging to the ledger's kalshi_ticker
    grader field -- built 2026-09-28 alongside to_ledger_docs, confirmed live against a real
    market (KXNFLREC-26SEP28PHICHI-CHIDSWIFT4-2, D'Andre Swift 2+ receptions, matched floor_strike
    1.5 -> no_ask 0.39, same play manually logged before this function existed)."""
    for m in kalshi_series_markets(series_ticker, dtag, away, home):
        title = m.get('title', '')
        pname = title.split(':')[0].strip()
        if nrm(pname) != norm_player_name:
            continue
        fs = m.get('floor_strike')
        if fs is None or abs(float(fs) - line) > 0.01:
            continue
        return m.get('ticker')
    return None


# ---------- scan ----------
def scan(edge_min_override=None):
    edge_min = edge_min_override or EDGE_MIN_PIN
    # statusName is unreliable -- confirmed live 2026-09-28: repeated calls seconds apart disagreed
    # about the SAME fixture (a game scheduled 24h out came back "Finished" on one call, "Pre-Game"
    # on the next -- stale/inconsistent caching on OddsPapi's side, not real). Trust startTime vs
    # wall-clock instead, plus hasOdds for whether pinnacle+30 actually has props posted yet.
    fixtures = [f for f in oddspapi_fixtures_this_week() if f['hasOdds'] and _not_kicked_off(f['start'])]
    print(f"{len(fixtures)} upcoming NFL fixtures with live odds (OddsPapi, tournamentSlug=nfl)")
    oddsapi_events = oddsapi_events_this_week()
    plays = []
    team_check_cache = {}

    for fx in fixtures:
        pin_by_stat = oddspapi_pinnacle_odds(fx['fixtureId'])
        if not any(pin_by_stat.values()):
            print(f"  {fx['away']}@{fx['home']}: no Pinnacle player-prop odds yet on OddsPapi -- skip")
            continue
        event_id = match_oddsapi_event(oddsapi_events, fx['home'], fx['away'])
        venue_by_stat = oddsapi_venue_odds(event_id) if event_id else {}
        try:
            import ref_books
            _ref = ref_books.ref_fairs('americanfootball_nfl', event_id, [v[2] for v in STAT_MARKETS.values()]) if event_id else {}
        except Exception as e:
            print('ref_books skipped:', e); _ref = {}
        if not event_id:
            print(f"  {fx['away']}@{fx['home']}: no matching event on The Odds API -- Kalshi-only for this game")
        dtag = kalshi_date_tag(fx['start'])

        for stat, (mname, mtype, oa_key, kalshi_series, pinn_unit) in STAT_MARKETS.items():
            for norm_name, pin in pin_by_stat.get(stat, {}).items():
                _pk = pin_move.key('NFL', f"{fx['away']}@{fx['home']}", pin['name'], stat, pin['line'])
                _pm = pin_move.move(_pk, pin['fair_over'])
                pin_move.record(_pk, pin['fair_over'])
                candidates = []
                for line, info in venue_by_stat.get(stat, {}).get(norm_name, {}).items():
                    if abs(line - pin['line']) > 0.01:
                        continue  # different line than Pinnacle's -- not a clean comparison, skip
                    candidates.extend(side_candidates(info, pin['fair_over'], edge_min, ZONE))
                    candidates.extend(c for c in pp_fliff.extra_candidates(info, pin['fair_over']) if c not in candidates)
                kp = kalshi_price(kalshi_series, dtag, fx['away'], fx['home'], norm_name, pin['line'])
                if kp:
                    edge_over, edge_under = pin['fair_over'] - kp['over'], (1 - pin['fair_over']) - kp['under']
                    for side, edge, price in (('Over', edge_over, kp['over']), ('Under', edge_under, kp['under'])):
                        if edge >= edge_min and ZONE[0] <= price <= ZONE[1]:
                            candidates.append((side, edge, price, 'kalshi'))
                if not candidates:
                    continue

                # Gate 1 mandatory check -- resolve which team this player is actually on, live,
                # before logging anything (rulebook rule, added 2026-09-27, Kyler Murray case).
                if pin['name'] not in team_check_cache:
                    on_home = verify_player_team(pin['name'], fx['home'])
                    on_away = verify_player_team(pin['name'], fx['away'])
                    if on_home:
                        team_check_cache[pin['name']] = fx['home']
                    elif on_away:
                        team_check_cache[pin['name']] = fx['away']
                    else:
                        team_check_cache[pin['name']] = None
                        print(f"  GATE 1 FAIL: {pin['name']} not found on {fx['home']} or {fx['away']}'s "
                              f"live ESPN roster -- skipping all plays for this player, not guessing which team.")
                team = team_check_cache[pin['name']]
                if team is None:
                    continue
                if pin.get('vig', 0) > MAX_PIN_VIG:
                    print(f"  WIDE MARKET skip: {pin['name']} {stat} {pin['line']} -- Pinnacle vig {pin['vig']*100:.1f}% > {MAX_PIN_VIG*100:.0f}%")
                    continue
                _alt = {}
                if kp and kp.get('over') and kp.get('under'):
                    _alt['kalshi'] = round(kp['over'] / (kp['over'] + kp['under']) * 100, 1)
                for _bk, _f in _ref.get((oa_key, norm_name, pin['line']), {}).items():
                    _alt[_bk] = round(_f * 100, 1)
                for side, edge, price, venue in candidates:
                    et_dt = datetime.datetime.fromisoformat(fx['start'].replace('Z', '+00:00')).astimezone(ZoneInfo('America/New_York'))
                    plays.append(dict(game=f"{fx['away']}@{fx['home']}", team=team, start=fx['start'], stat=stat,
                                       player=pin['name'], line=pin['line'], side=side, venue=venue,
                                       fair=round((pin['fair_over'] if side == 'Over' else 1 - pin['fair_over']) * 100, 1),
                                       price=round(price * 100, 1), edge=round(edge * 100, 1), label=pp_fliff.label(venue, edge), pinn_unit=pinn_unit,
                                       date=et_dt.date().isoformat(), kalshi_series=kalshi_series, dtag=dtag,
                                       away=fx['away'], home=fx['home'], pin_vig=pin.get('vig'),
                                       alt_fairs=({k: (v if side == 'Over' else round(100 - v, 1)) for k, v in _alt.items()}),
                                       pin_move=(None if _pm is None else round(_pm if side == 'Over' else -_pm, 1))))
        time.sleep(0.3)

    plays.sort(key=lambda p: -p['edge'])
    print(f"\n{len(plays)} Track B plays clear the bar (>= {edge_min*100:.0f}pts Pinnacle-based, zone 35-75c):")
    for p in plays:
        print(p)
    return plays


def to_ledger_docs(plays):
    """Turn flagged Track B plays into ledger-ready docs (rulebook grader fields: kalshi_ticker,
    start, pinn, side, sport). Built 2026-09-28 -- nfl_scan.py's docstring had claimed a --write
    flag existed since it was written, but neither --write nor this function actually existed;
    caught only because a real play (D'Andre Swift receptions Under 1.5) had to be logged by hand
    first. Mirrors softness_scan.py/nhl_scan.py's to_ledger_docs. Unlike NHL SOG (confirmed no
    Kalshi market at all), NFL player props DO have a Kalshi venue for most stats (KX-prefixed
    series in STAT_MARKETS) -- when a play's venue is 'kalshi', look up the real ticker via
    kalshi_ticker() so the nightly grader can settle it directly; for a non-Kalshi venue
    (novig/fliff/prophetx/prizepicks) kalshi_ticker is left null with a note, same as
    softness_scan.py's non-Kalshi case -- these plays get CLV via grade_plays.py's real_result()
    path if one gets added for NFL stats (not built yet, same gap NHL SOG had until 2026-09-28)."""
    docs = {}
    for p in plays:
        slug = p['player'].split()[-1].lower()
        line_tag = str(p['line']).replace('.', '')
        side_tag = 'o' if p['side'] == 'Over' else 'u'
        doc_id = f"{p['date']}-nfl-{slug}-{p['stat']}{line_tag}-{side_tag}"
        if doc_id in docs and docs[doc_id]['data']['edge'] >= p['edge']:
            continue
        side = 'YES' if p['side'] == 'Over' else 'NO'
        ticker = None
        if p['venue'] == 'kalshi':
            ticker = kalshi_ticker(p['kalshi_series'], p['dtag'], p['away'], p['home'], nrm(p['player']), p['line'])
        note = f"NFL Track B scan {p['date']}. Edge +{p['edge']}pts vs {p['venue']}. Pinnacle fair {p['fair']}c. Pin move toward our side (24h): {p.get('pin_move')}pts."
        if p['venue'] != 'kalshi':
            note += f" Bet placed on {p['venue']}, not Kalshi -- needs manual {p['venue']} close for CLV, not a Kalshi close."
        elif not ticker:
            note += " Kalshi ticker lookup failed -- needs manual kalshi_ticker before grading."
        docs[doc_id] = dict(id=doc_id, data=dict(
            date=p['date'], game=p['game'], player=p['player'], market=f"{p['pinn_unit']} {p['side']} {p['line']}",
            entry=p['price'], fair=p['fair'],
            fairSource=f'Pinnacle no-vig (OddsPapi, nfl_scan.py) vs {p["venue"]}',
            edge=p['edge'], side=side, sport='NFL', track=('B2' if pp_fliff.is_b2(p.get('label')) else 'B'), stake=0, fee=0,
            tier=('B2' if pp_fliff.is_b2(p.get('label')) else None), label=p.get('label'),
            orderType='maker' if p['venue'] == 'kalshi' else 'taker',
            kalshi_ticker=ticker, start=p['start'],
            pinn=dict(who=p['player'], mkt=f"prop:{p['pinn_unit']}", line=p['line']),
            status=(f"Paper — {p['label']}" if p.get('label') else f"Paper — Track B scan, pending fill ({p['venue']})") if ticker or p['venue'] != 'kalshi' else 'Paper — needs kalshi_ticker',
            note=note, close=None, result=None, zone='35–75', pinMove=p.get('pin_move')))
    return list(docs.values())


if __name__ == '__main__':
    plays = scan()
    try:
        import adverse_flag; adverse_flag.annotate_plays(plays)  # info-only, non-blocking
    except Exception as e:
        print('adverse_flag skipped:', e)
    try:
        import consensus_flag; consensus_flag.annotate_plays(plays)  # info-only, non-blocking
    except Exception as e:
        print('consensus_flag skipped:', e)
    if '--write' in sys.argv:
        outpath = sys.argv[sys.argv.index('--write') + 1]
        docs = to_ledger_docs(plays)
        with open(outpath, 'w') as fh:
            json.dump(docs, fh, indent=2)
        print(f'\nWrote {len(docs)} ledger-ready docs to {outpath}')
        print('Not written to the ledger yet -- the calling session still does the ArtifactData batch write.')

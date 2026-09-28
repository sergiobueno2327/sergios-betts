"""Sergio's Betts — odds history collector (one snapshot per run).

REWIRED 2026-09-28: Pinnapi is suspended; this now pulls Pinnacle via OddsPapi for the 3
sports actually live in Sergio's Betts (NBA, NFL, NHL) plus Kalshi execution-venue prices for
those sports and college football/basketball. Scope decisions made explicitly with Sergio
2026-09-28:
  - MLB: DROPPED. OddsPapi has no MLB access at all (sportId=13 403s under this plan). The
    Odds API DOES carry Pinnacle MLB game lines (confirmed live) but NOT Pinnacle MLB player
    props (batter_hits/batter_total_bases -- confirmed live, checked us/us2/us_ex/eu/uk/au
    regions, only DraftKings/Fanatics/ProphetX/Novig/PointsBetAU have them, no Pinnacle). Since
    softness_scan.py's whole point is the player prop and MLB season is ending this week
    anyway, MLB got dropped rather than rebuilt on a soft-book (DraftKings) fair source.
  - Tennis: DROPPED. Never an active track in this system -- leftover scope from the old
    Pinnapi-era collector, which pulled it just because Pinnapi's fixtures endpoint made it
    free to include. Revisit either if circumstances change.

Coverage per run:
  Game lines (moneyline/spread/total, full game incl. overtime) for NBA/NFL/NHL via OddsPapi.
    Confirmed live 2026-09-28: game-level markets don't need the catalog lookup player props
    do -- OddsPapi embeds the line and side directly in each outcome's bookmakerOutcomeId
    ("home"/"away" for moneyline, "<line>/home"|"<line>/away" for spreads, "<line>/over"|
    "<line>/under" for totals) and flags the primary quote via mainLine=true (alt lines are
    also returned in the same call at no extra request cost, and are kept here with
    mainLine=false for anyone who wants the full menu later).
    RESOLVED 2026-09-28: spread `line` is now a verified signed number, not OddsPapi's raw
    printed string. bookmakerOutcomeId's sign turned out to be unreliable on alt lines (e.g.
    "-3.5/home" and "-3.5/away" both observed under the same marketId -- same magnitude, no
    negation on the away side). Fix: resolve spreads via the /v4/markets catalog instead (see
    spread_catalog() in this file) -- each catalog marketId's `handicap` field is always
    participant1/home's own signed line, and away is always its exact negated mirror. Verified
    against 2 independent completed real NFL games with known results (BUF 41 DET 31: home
    favored ~-3.5 pregame, matches; ATL 3 CAR 34: home a big live underdog, matches).
    Also unverified: the period tag "0" = full/incl.-overtime was only directly confirmed live
    for NFL; NBA/NHL are assumed consistent with the platform-wide catalog (all three sports'
    catalog entries share the same 'period':'result' tag) but not independently re-tested.
  Player props for the exact stats each sport's scan script trades (NFL: 9 stats, NBA: REB/AST,
  NHL: SOG) via the same OddsPapi /v4/markets catalog pattern as nfl_scan.py/nba_scan.py/
  nhl_scan.py -- built once per run, ~33k markets across all sports, filtered locally.
  Kalshi prices for the sports/leagues still in scope (dropped KXMLB*/KXATP*/KXWTA* series).

Usage:  ODDSPAPI_KEY=... python3 collect_odds.py OUT_DIR
Writes: OUT_DIR/data/YYYY-MM-DD/HHMMZ_pinnacle.jsonl.gz and HHMMZ_kalshi.jsonl.gz
        (UTC date/time of the snapshot). Prints a one-line summary.

Row formats (one JSON object per line):
  pinnacle: {ts, sport, fid, home, away, mkt, per, sel, line, price, priceAmerican, player,
             mainLine, limit}
     mkt = moneyline | spread | total | prop:<unit>
     player = null for game-level markets, "First Last" for player props
     price = decimal odds (raw, with vig). De-vig later.
  kalshi:   {ts, series, event, ticker, title, sub, strike, close_time, bid, ask, last, vol, oi}
     bid/ask in dollars (Yes side). No bid = 1 - yes ask.
No API keys are stored in this file or the output.

Known OddsPapi quirk (see nfl_scan.py/nhl_scan.py docstrings for full detail): /v4/fixtures
results aren't fully consistent call-to-call -- trust startTime vs wall clock, not statusName;
treat any single run's fixture list as potentially incomplete (scheduled re-runs self-heal).

Quota note: OddsPapi's Normal tier is 5,000 req/month, shared across every script in this
project (the 3 scan scripts, the nightly grader, and this collector). Unlike the old Pinnapi
collector (1 call per sport, odds embedded in the fixtures response), OddsPapi splits
fixtures from odds, so this collector spends 1 fixtures call/sport (3) + 1 odds call per
in-scope game found each run. At several runs/day across a full NFL/NBA/NHL slate this can
add up -- watch usage via the weekly health check and cut the run cadence or the days-ahead
fixture window (see oddspapi_fixtures) if it gets close to the cap.
"""
import gzip, json, os, sys, time, datetime, urllib.request

ODDSPAPI_KEY = os.environ.get('ODDSPAPI_KEY', '15490352-5f73-404d-9964-353ab0783e01')
ODDSPAPI_BASE = 'https://api.oddspapi.io/v4'
KALSHI = 'https://api.elections.kalshi.com/trade-api/v2'

# sportId -> (tournamentSlugs to keep, short league label)
SPORTS = {
    11: (('nba', 'nba-preseason'), 'NBA'),
    14: (('nfl',), 'NFL'),
    15: (('nhl',), 'NHL'),
}

# Full-game (incl. overtime) marketName per sportId -- confirmed live 2026-09-28 against
# OddsPapi's /v4/markets catalog; naming genuinely differs per sport, don't assume they match.
GAME_MARKET_NAMES = {
    11: {'moneyline': 'Winner (incl. overtime)', 'spreads': 'Handicap (incl. overtime)',
         'totals': 'Over Under (incl. overtime)'},
    14: {'moneyline': 'Winner (incl. overtime)', 'spreads': 'Handicap (incl. overtime)',
         'totals': 'Total (incl. overtime)'},
    15: {'moneyline': 'Winner (incl. overtime and penalties)',
         'spreads': 'Handicap (incl. overtime and penalties)',
         'totals': 'Total (incl. overtime and penalties)'},
}
# Used directly for spreads (see spread_catalog()) to resolve the marketName filter below;
# moneyline/totals still decode straight from bookmakerOutcomeId at odds-fetch time (reliable
# for those two -- see module docstring).

# Player-prop (marketName, marketType) pairs per sportId -- same values as
# nfl_scan.py/nba_scan.py/nhl_scan.py's STAT_MARKETS, kept in sync manually.
PROP_MARKETS = {
    14: {  # NFL
        'interceptions': ('Over Under Player Interceptions (incl. overtime)', 'playertotals-interceptions'),
        'passing_yards': ('Over Under Pass Yards (incl. overtime)', 'playertotals-passyards'),
        'passing_completions': ('Over Under Pass Completions (incl. overtime)', 'playertotals-passcompletions'),
        'passing_attempts': ('Over Under Pass Attempts (incl. overtime)', 'playertotals-passattempts'),
        'passing_touchdowns': ('Over Under Player TD Passes (incl. overtime)', 'playertotals-tdpasses'),
        'receiving_yards': ('Over Under Player Receiving Yards (incl. overtime)', 'playertotals-receivingyards'),
        'receptions': ('Over Under Player Receptions (incl. overtime)', 'playertotals-receptions'),
        'rushing_yards': ('Over Under Rush Yards (incl. overtime)', 'playertotals-rushyards'),
        'rushing_attempts': ('Over Under Rush Attempts (incl. overtime)', 'playertotals-rushattempts'),
    },
    11: {'REB': ('Over Under Player Rebounds (incl. overtime)', 'playertotals-rebounds'),
         'AST': ('Over Under Player Assists (incl. overtime)', 'playertotals-assists')},
    15: {'SOG': ('Over Under Player Shots On Goal (incl. overtime)', 'playertotals-shotsongoal')},
}

KALSHI_SERIES = [
    # NBA
    'KXNBAREB', 'KXNBAAST', 'KXNBAPTS', 'KXNBA3PT', 'KXNBAGAME', 'KXNBASPREAD', 'KXNBATOTAL',
    # NFL
    'KXNFLPASSYDS', 'KXNFLPASSCOMP', 'KXNFLPASSATT', 'KXNFLPASSTDS', 'KXNFLPASSINT', 'KXNFLREC',
    'KXNFLRECYDS', 'KXNFLRSHATT', 'KXNFLRSHYDS', 'KXNFLANYTD', 'KXNFLGAME', 'KXNFLSPREAD', 'KXNFLTOTAL',
    # NHL
    'KXNHLGOAL', 'KXNHLPTS', 'KXNHLAST', 'KXNHLSAVE', 'KXNHLGAME', 'KXNHLTOTAL', 'KXNHLSPREAD',
    # College football/basketball -- same OddsPapi sportIds as NFL/NBA, cheap to keep alongside
    'KXNCAAFGAME', 'KXNCAAFSPREAD', 'KXNCAAFTOTAL', 'KXNCAAMBGAME', 'KXNCAAMBSPREAD', 'KXNCAAMBTOTAL',
]
AUTO_SERIES_KEYWORDS = ('SOG', 'SHOTS')  # auto-add any new NHL shots-on-goal series Kalshi lists


def get(url, headers=None, tries=4):
    for a in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers or {'User-Agent': 'curl/8'})
            return json.load(urllib.request.urlopen(req, timeout=30))
        except Exception:
            time.sleep(1.5 * (a + 1))
    return None


_prop_catalog_cache = None
_spread_catalog_cache = None


def prop_catalog():
    """marketId(str) -> (sportId, stat, handicap, over_outcomeId, under_outcomeId). Built once
    per run from OddsPapi's full /v4/markets catalog (~33k entries across all sports) -- 1 call
    total, not per-sport. Same pattern as the 3 scan scripts. As a side effect, also populates
    _spread_catalog_cache (see spread_catalog() below) from the same pass -- no extra API call.
    """
    global _prop_catalog_cache, _spread_catalog_cache
    if _prop_catalog_cache is not None:
        return _prop_catalog_cache
    markets = get(f'{ODDSPAPI_BASE}/markets?apiKey={ODDSPAPI_KEY}') or []
    cat = {}
    spread_cat = {}
    for m in markets:
        sid = m.get('sportId')
        if sid in PROP_MARKETS and m.get('playerProp'):
            for stat, (mname, mtype) in PROP_MARKETS[sid].items():
                if m.get('marketName') == mname and m.get('marketType') == mtype:
                    outs = m.get('outcomes') or []
                    over_id = next((o['outcomeId'] for o in outs if o['outcomeName'] == 'Over'), None)
                    under_id = next((o['outcomeId'] for o in outs if o['outcomeName'] == 'Under'), None)
                    if over_id and under_id:
                        cat[str(m['marketId'])] = (sid, stat, m.get('handicap'), over_id, under_id)
        if sid in GAME_MARKET_NAMES and not m.get('playerProp') and m.get('marketType') == 'spreads' \
                and m.get('marketName') == GAME_MARKET_NAMES[sid]['spreads']:
            # RESOLVED 2026-09-28 (see module docstring): bookmakerOutcomeId's printed sign is
            # unreliable for the away/"2" side of an alt-line spread (observed printing the same
            # magnitude as the home/"1" side instead of the negated mirror). The catalog itself
            # is unambiguous and was verified against 2 independent completed real NFL games
            # (BUF 41 DET 31, ATL 3 CAR 34): outcomeName "1" is always participant1 == home, its
            # `handicap` field is that team's own signed line, and outcomeName "2" (away) is
            # always the exact negated mirror. Build home/away signed line per (marketId,
            # outcomeId) here so pinnacle_rows_for_fixture never has to parse boid for spreads.
            handicap = m.get('handicap')
            for o in (m.get('outcomes') or []):
                side = 'home' if o.get('outcomeName') == '1' else 'away'
                line = handicap if side == 'home' else (-handicap if handicap is not None else None)
                spread_cat[(str(m['marketId']), o['outcomeId'])] = (side, line)
    _prop_catalog_cache = cat
    _spread_catalog_cache = spread_cat
    return cat


def spread_catalog():
    """(marketId(str), outcomeId(int)) -> (side 'home'|'away', signed line). Populated as a side
    effect of prop_catalog() -- call that first (pinnacle_rows() already does)."""
    if _spread_catalog_cache is None:
        prop_catalog()
    return _spread_catalog_cache


def oddspapi_fixtures(sport_id, days=3):
    """Fixtures with odds, not yet started, for this sport's real league(s) only (OddsPapi
    shares one sportId across many tournaments -- e.g. sportId=14 mixes nfl/ncaa/cfl)."""
    d0 = datetime.date.today().isoformat()
    d1 = (datetime.date.today() + datetime.timedelta(days=days)).isoformat()
    fx = get(f'{ODDSPAPI_BASE}/fixtures?apiKey={ODDSPAPI_KEY}&sportId={sport_id}&from={d0}&to={d1}') or []
    slugs, _ = SPORTS[sport_id]
    now = datetime.datetime.now(datetime.timezone.utc)
    out = []
    for f in fx:
        if f.get('tournamentSlug') not in slugs or not f.get('hasOdds'):
            continue
        st = f.get('startTime') or ''
        try:
            start = datetime.datetime.fromisoformat(st.replace('Z', '+00:00'))
        except ValueError:
            continue
        if start <= now:
            continue  # started/stale -- pregame open-to-close history only
        out.append(f)
    return out


_GAME_MKT_LABEL = {'moneyline': 'moneyline', 'spreads': 'spread', 'totals': 'total'}


def pinnacle_rows_for_fixture(ts, sport_id, league, fx, catalog):
    fid = fx['fixtureId']
    home, away = fx.get('participant1Abbr'), fx.get('participant2Abbr')
    d = get(f'{ODDSPAPI_BASE}/odds?apiKey={ODDSPAPI_KEY}&fixtureId={fid}')  # no bookmakers= param
    # (pinnacle+30 has a "+" that URL-encodes to a space and 400s if passed explicitly -- the
    # key's plan only has that one bookmaker anyway, so omitting the param returns it by default)
    if not d:
        return []
    pin = (d.get('bookmakerOdds') or {}).get('pinnacle+30', {}).get('markets', {})
    rows = []
    for mid, m in pin.items():
        bmid = m.get('bookmakerMarketId', '')
        parts = bmid.split('/')
        mtype = parts[-1] if parts else ''
        period = parts[-2] if len(parts) > 1 else ''
        if mtype in _GAME_MKT_LABEL and period == '0':
            scat = spread_catalog() if mtype == 'spreads' else None
            for oid, o in m.get('outcomes', {}).items():
                p = (o.get('players') or {}).get('0')
                if not p or not p.get('active'):
                    continue
                boid = p.get('bookmakerOutcomeId') or ''
                if mtype == 'moneyline':
                    # boid is a plain "home"/"away" literal here -- reliable, no sign involved.
                    sel, line = boid, None
                elif mtype == 'spreads':
                    # boid's sign is unreliable for alt lines (see spread_catalog() docstring) --
                    # resolve side + signed line from the catalog by (marketId, outcomeId)
                    # instead of parsing the string. Falls back to the old parse if a market
                    # wasn't in the catalog pass (shouldn't happen for in-scope sports/markets).
                    got = scat.get((mid, int(oid))) if scat else None
                    if got:
                        sel, line = got
                    else:
                        line_str, _, sel = boid.rpartition('/')
                        try:
                            line = float(line_str)
                        except ValueError:
                            line = None
                else:  # totals -- "<line>/over" or "<line>/under", magnitude is unsigned, fine as-is
                    line_str, _, sel = boid.rpartition('/')
                    try:
                        line = float(line_str)
                    except ValueError:
                        line = None
                rows.append(dict(ts=ts, sport=league, fid=fid, home=home, away=away,
                                  mkt=_GAME_MKT_LABEL[mtype], per=0, sel=sel, line=line,
                                  price=p.get('price'), priceAmerican=p.get('priceAmerican'),
                                  player=None, mainLine=p.get('mainLine'), limit=p.get('limit')))
            continue
        cat = catalog.get(mid)
        if not cat:
            continue
        csid, stat, handicap, over_id, under_id = cat
        if csid != sport_id:
            continue
        for side, oid in (('Over', over_id), ('Under', under_id)):
            o = m.get('outcomes', {}).get(str(oid), {})
            for pid, p in (o.get('players') or {}).items():
                if not p.get('active'):
                    continue
                nm = p.get('playerName') or ''
                if ',' in nm:
                    last, first = [x.strip() for x in nm.split(',', 1)]
                    nm = f'{first} {last}'
                rows.append(dict(ts=ts, sport=league, fid=fid, home=home, away=away,
                                  mkt=f'prop:{stat}', per=0, sel=side, line=handicap,
                                  price=p.get('price'), priceAmerican=p.get('priceAmerican'),
                                  player=nm, mainLine=p.get('mainLine'), limit=p.get('limit')))
    return rows


def pinnacle_rows(ts):
    rows, calls = [], 0
    if not ODDSPAPI_KEY:
        return rows, calls
    catalog = prop_catalog()
    calls += 1
    for sid, (slugs, league) in SPORTS.items():
        fixtures = oddspapi_fixtures(sid)
        calls += 1
        for fx in fixtures:
            rows.extend(pinnacle_rows_for_fixture(ts, sid, league, fx, catalog))
            calls += 1
            time.sleep(0.3)
    return rows, calls


def kalshi_rows(ts):
    series = list(KALSHI_SERIES)
    s = get(f'{KALSHI}/series?category=Sports') or {}
    for x in s.get('series', []):
        t = x.get('ticker', '')
        if t.startswith('KXNHL') and any(k in t for k in AUTO_SERIES_KEYWORDS) and t not in series:
            series.append(t)
    rows, new_sog = [], [t for t in series if t not in KALSHI_SERIES]
    for st in series:
        cur = ''
        while True:
            d = get(f'{KALSHI}/events?series_ticker={st}&status=open&limit=200&with_nested_markets=true'
                    + (f'&cursor={cur}' if cur else '')) or {}
            for e in d.get('events', []):
                for m in e.get('markets', []):
                    if m.get('status') not in ('active', 'open'):
                        continue
                    f = lambda k: float(m.get(k) or 0)
                    rows.append(dict(ts=ts, series=st, event=e.get('event_ticker'), ticker=m.get('ticker'),
                                      title=m.get('title'), sub=m.get('yes_sub_title'), strike=m.get('floor_strike'),
                                      close_time=m.get('close_time'), bid=f('yes_bid_dollars'), ask=f('yes_ask_dollars'),
                                      last=f('last_price_dollars'), vol=m.get('volume_fp') or m.get('volume'),
                                      oi=m.get('open_interest_fp') or m.get('open_interest')))
            cur = d.get('cursor')
            if not cur or not d.get('events'):
                break
            time.sleep(0.2)
        time.sleep(0.1)
    return rows, new_sog


def dump(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path, 'wt') as f:
        for r in rows:
            f.write(json.dumps(r, separators=(',', ':')) + '\n')


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else '.'
    now = datetime.datetime.now(datetime.timezone.utc)
    ts = now.strftime('%Y-%m-%dT%H:%M:%SZ')
    stem = os.path.join(out, 'data', now.strftime('%Y-%m-%d'), now.strftime('%H%MZ'))
    pr, calls = pinnacle_rows(ts)
    kr, new_sog = kalshi_rows(ts)
    if pr:
        dump(stem + '_pinnacle.jsonl.gz', pr)
    if kr:
        dump(stem + '_kalshi.jsonl.gz', kr)
    leagues = {}
    for r in pr:
        leagues[r['sport']] = leagues.get(r['sport'], 0) + 1
    top = ', '.join(f'{k} {v}' for k, v in sorted(leagues.items(), key=lambda x: -x[1])[:6])
    print(f'{ts} pinnacle rows {len(pr)} ({calls} OddsPapi calls{"" if ODDSPAPI_KEY else ", NO KEY"}) [{top}] | '
          f'kalshi rows {len(kr)}' + (f' | NEW NHL SOG SERIES: {new_sog}' if new_sog else ''))


if __name__ == '__main__':
    main()

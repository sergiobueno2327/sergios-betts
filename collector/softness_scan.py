"""Sergio's Betts — Track B softness scan (rulebook: Track B — Pure +EV, paper only).
Compares Pinnacle no-vig fair price (power de-vig) against Kalshi, Novig, ProphetX, Fliff,
PrizePicks for MLB "1+ hit" (Hits O/U 0.5, OddsPapi marketId 131543). Same market shape as
Pinnacle's Total Bases 0.5 (marketId 131575) -- a hit of any kind clears both, so they're the
same underlying probability and can be compared directly.

Venue coverage confirmed 2026-09-27 (see rulebook Track B section):
  Kalshi, Novig, ProphetX, Fliff, PrizePicks -- all live via OddsPapi (slugs below).
  Underdog -- NOT on OddsPapi; only ever seen via SportsGameOdds Pro tier (paid, not subscribed).
    FanDuel Predicts / DraftKings Predicts / Betr / Sleeper / ParlayPlay / Dabble -- no API anywhere.

Usage: ODDSPAPI_KEY=... python3 softness_scan.py YYYY-MM-DD [--edge 3] [--write out.json]
Prints every player/book combo clearing the edge bar (Pinnacle fair vs book price, Track B: >=3pts).
--write out.json: also writes ledger-ready docs (fixed 2026-09-27 -- previously scan-only).
  For book=='kalshi', looks up the exact Kalshi KXMLBHIT ticker for that game/player so the
  nightly grader can auto-grade it. For every other venue (Novig/ProphetX/Fliff/PrizePicks) there
  is no Kalshi ticker to grade against -- kalshi_ticker is left null and the doc is flagged
  "needs manual Kalshi close" in its note; pinnClose still grades fine since that only needs
  sport+pinn+start, not a ticker. Caller (the Claude session) still does the actual
  ArtifactData batch write to the ledger -- this script has no ledger credentials.
"""
import json, os, sys, time, datetime, urllib.request
from collections import defaultdict

APIKEY = os.environ.get('ODDSPAPI_KEY', '')
BASE = 'https://api.oddspapi.io/v4'
KALSHI_BASE = 'https://api.elections.kalshi.com/trade-api/v2'
HITS_MARKET_OVER, HITS_MARKET_UNDER = '131543', '131544'
TB_MARKET_OVER, TB_MARKET_UNDER = '131575', '131576'  # Pinnacle's equivalent
VENUES = ['kalshi', 'novig.us', 'prophetx', 'fliff', 'prizepicks']  # NOT pinnacle -- that's the fair source


def get(url, tries=4):
    for a in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'curl/8'})
            return json.load(urllib.request.urlopen(req, timeout=25))
        except Exception:
            time.sleep(1.5 * (a + 1))
    return None


def american_to_prob(american):
    try:
        a = float(american)
    except (TypeError, ValueError):
        return None
    return 100.0 / (a + 100.0) if a > 0 else -a / (-a + 100.0)


def devig_power(p_over, p_under):
    """Power method: exponent k solving p_over^(1/k) + p_under^(1/k) = 1. f(k) increasing in k."""
    lo, hi = 0.01, 10.0
    for _ in range(200):
        mid = (lo + hi) / 2
        s = p_over ** (1 / mid) + p_under ** (1 / mid)
        if s > 1:
            hi = mid
        else:
            lo = mid
    k = (lo + hi) / 2
    fo, fu = p_over ** (1 / k), p_under ** (1 / k)
    return fo / (fo + fu)


def mlb_fixtures_today(date_str):
    d0, d1 = date_str, (datetime.date.fromisoformat(date_str) + datetime.timedelta(days=1)).isoformat()
    fx = get(f'{BASE}/fixtures?apiKey={APIKEY}&sportId=13&from={d0}&to={d1}') or []
    return [f for f in fx if f.get('hasOdds') and f.get('statusName') in ('Pre-Game', 'Live')]


def fixture_start_iso(fx):
    """OddsPapi's exact start-time field name hasn't been re-verified since the outage started
    2026-09-27 -- try the likely candidates, else None (doc gets flagged unconfirmed)."""
    for key in ('startTimeUtc', 'startTime', 'commenceTime', 'scheduled'):
        v = fx.get(key)
        if v:
            return v if v.endswith('Z') or '+' in v else v + 'Z'
    return None


_kalshi_hit_cache = {}


def kalshi_hit_ticker(date_str, away, home, player_name):
    """Look up the exact KXMLBHIT ticker for a player on Kalshi (no auth needed for market data).
    Format: KXMLBHIT-<YYMONDD><AWAY><HOME>-<TEAM><LASTNAME><JERSEY#>-1. Matching by last name only
    (no jersey # from OddsPapi) -- ambiguous if two same-lastname players from the same team are
    both live that day, which is rare enough to flag rather than guess."""
    key = (date_str, away, home)
    if key not in _kalshi_hit_cache:
        d = datetime.date.fromisoformat(date_str)
        # Kalshi ticker date format e.g. 26SEP27
        dtag = d.strftime('%y%b%d').upper()
        series_tickers = set()
        for team in (away, home):
            evs = get(f"{KALSHI_BASE}/events?series_ticker=KXMLBHIT&status=open") or {}
            for ev in (evs.get('events') or []):
                if dtag in ev.get('event_ticker', '') and away in ev.get('event_ticker', '') and home in ev.get('event_ticker', ''):
                    series_tickers.add(ev['event_ticker'])
        markets = []
        for et in series_tickers:
            mkts = get(f"{KALSHI_BASE}/markets?event_ticker={et}") or {}
            markets.extend(mkts.get('markets') or [])
        _kalshi_hit_cache[key] = markets
    last = player_name.strip().split()[-1].upper()
    for m in _kalshi_hit_cache[key]:
        ticker = m.get('ticker', '')
        if last in ticker:
            return ticker
    return None


def scan(date_str, edge_min=3.0):
    fixtures = mlb_fixtures_today(date_str)
    print(f'{date_str}: {len(fixtures)} MLB fixtures with odds')
    flags = []
    for fx in fixtures:
        fid = fx['fixtureId']
        away, home = fx.get('participant1Abbr'), fx.get('participant2Abbr')
        game = f"{away}@{home}"
        start_iso = fixture_start_iso(fx)
        url = f"{BASE}/odds?apiKey={APIKEY}&fixtureId={fid}&bookmakers=pinnacle,{','.join(VENUES)}"
        d = get(url)
        if not d:
            continue
        books = d.get('bookmakerOdds', {})
        pin = books.get('pinnacle', {}).get('markets', {}).get(TB_MARKET_OVER)
        if not pin:
            continue
        over_p = pin['outcomes'].get(TB_MARKET_OVER, {}).get('players', {})
        under_p = pin['outcomes'].get(TB_MARKET_UNDER, {}).get('players', {})
        fair = {}
        for pid, po in over_p.items():
            pu = under_p.get(pid)
            if not pu:
                continue
            po_prob, pu_prob = american_to_prob(po.get('priceAmerican')), american_to_prob(pu.get('priceAmerican'))
            if po_prob is None or pu_prob is None:
                continue
            fo = devig_power(po_prob, pu_prob)
            fair[pid] = {'name': po['playerName'], 'fair_over': fo}
        for venue in VENUES:
            hits = books.get(venue, {}).get('markets', {}).get(HITS_MARKET_OVER)
            if not hits:
                continue
            o_players = hits['outcomes'].get(HITS_MARKET_OVER, {}).get('players', {})
            u_players = hits['outcomes'].get(HITS_MARKET_UNDER, {}).get('players', {})
            for pid, info in fair.items():
                po, pu = o_players.get(pid), u_players.get(pid)
                if not po or not pu:
                    continue
                bo, bu = american_to_prob(po.get('priceAmerican')), american_to_prob(pu.get('priceAmerican'))
                if bo is None or bu is None:
                    continue
                combined = bo + bu
                if combined > 1.12 or combined < 0.98:
                    continue  # illiquid/wide exchange quote, skip
                fair_over = info['fair_over']
                edge_over = (fair_over - bo) * 100
                edge_under = ((1 - fair_over) - bu) * 100
                if edge_over >= edge_min:
                    flags.append(dict(game=game, away=away, home=home, book=venue, player=info['name'], side='OVER',
                                       fair=round(fair_over * 100, 1), price=round(bo * 100, 1), edge=round(edge_over, 1),
                                       start=start_iso, date=date_str))
                if edge_under >= edge_min:
                    flags.append(dict(game=game, away=away, home=home, book=venue, player=info['name'], side='UNDER',
                                       fair=round((1 - fair_over) * 100, 1), price=round(bu * 100, 1), edge=round(edge_under, 1),
                                       start=start_iso, date=date_str))
        time.sleep(0.3)
    flags.sort(key=lambda x: -x['edge'])
    print(f'{len(flags)} plays clear edge >= {edge_min}pts\n')
    for f in flags:
        print(f"{f['game']:10} {f['book']:10} {f['player']:22} {f['side']:6} fair {f['fair']:5.1f} price {f['price']:5.1f} edge {f['edge']:+5.1f}")
    return flags


def to_ledger_docs(flags):
    """Turn flagged plays into ledger-ready docs (rulebook grader fields: kalshi_ticker, start,
    pinn, side, sport). doc_id collisions (same player/game/side flagged on 2 venues same day)
    keep the higher-edge one -- Track B logs the best price found, not every venue separately."""
    docs = {}
    for f in flags:
        side = 'YES' if f['side'] == 'OVER' else 'NO'
        slug = f['player'].split()[-1].lower()
        doc_id = f"{f['date']}-tb-{slug}-hit1-{'o' if side == 'YES' else 'u'}"
        if doc_id in docs and docs[doc_id]['data']['edge'] <= f['edge']:
            continue
        ticker = kalshi_hit_ticker(f['date'], f['away'], f['home'], f['player']) if f['book'] == 'kalshi' else None
        note = f"Softness scan {f['date']}. Edge +{f['edge']}pts vs {f['book']}. Pinnacle fair {f['fair']}c."
        if f['book'] != 'kalshi':
            note += f" Bet placed on {f['book']}, not Kalshi -- needs manual Kalshi/venue close for CLV."
        elif not ticker:
            note += " Kalshi ticker lookup failed -- needs manual kalshi_ticker before grading."
        if not f['start']:
            note += " Start time unconfirmed (OddsPapi field name not verified since outage) -- check before logging."
        docs[doc_id] = dict(id=doc_id, data=dict(
            date=f['date'], game=f['game'], player=f['player'], market='Hits 1+',
            entry=f['price'], fair=f['fair'], fairSource=f'Pinnacle no-vig TB 0.5 (OddsPapi, softness_scan.py) vs {f["book"]}',
            edge=f['edge'], side=side, sport='MLB', track='B', stake=0, fee=0, orderType='maker',
            kalshi_ticker=ticker, start=f['start'],
            pinn=dict(who=f['player'], mkt='prop:Bases', line=0.5),
            status='Paper — softness scan, pending fill' if ticker else 'Paper — needs kalshi_ticker',
            note=note, close=None, result=None, zone='35–75'))
    return list(docs.values())


if __name__ == '__main__':
    if not APIKEY:
        sys.exit('Set ODDSPAPI_KEY')
    date_str = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().isoformat()
    edge = float(sys.argv[sys.argv.index('--edge') + 1]) if '--edge' in sys.argv else 3.0
    flagged = scan(date_str, edge)
    if '--write' in sys.argv:
        outpath = sys.argv[sys.argv.index('--write') + 1]
        docs = to_ledger_docs(flagged)
        with open(outpath, 'w') as fh:
            json.dump(docs, fh, indent=2)
        needs_ticker = sum(1 for d in docs if not d['data']['kalshi_ticker'])
        print(f'\nWrote {len(docs)} ledger-ready docs to {outpath} ({needs_ticker} missing kalshi_ticker)')
        print('Not written to the ledger yet -- the calling session still does the ArtifactData batch write.')

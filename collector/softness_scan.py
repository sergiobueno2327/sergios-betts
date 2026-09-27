"""Sergio's Betts — Track B softness scan (rulebook: Track B — Pure +EV, paper only).
Compares Pinnacle no-vig fair price (power de-vig) against Kalshi, Novig, ProphetX, Fliff,
PrizePicks for MLB "1+ hit" (Hits O/U 0.5, OddsPapi marketId 131543). Same market shape as
Pinnacle's Total Bases 0.5 (marketId 131575) -- a hit of any kind clears both, so they're the
same underlying probability and can be compared directly.

Venue coverage confirmed 2026-09-27 (see rulebook Track B section):
  Kalshi, Novig, ProphetX, Fliff, PrizePicks -- all live via OddsPapi (slugs below).
  Underdog -- NOT on OddsPapi; only ever seen via SportsGameOdds Pro tier (paid, not subscribed).
    FanDuel Predicts / DraftKings Predicts / Betr / Sleeper / ParlayPlay / Dabble -- no API anywhere.

Usage: ODDSPAPI_KEY=... python3 softness_scan.py YYYY-MM-DD [--edge 3]
Prints every player/book combo clearing the edge bar (Pinnacle fair vs book price, Track B: >=3pts).
Does NOT write to the ledger -- pipe the printed rows into the ArtifactData batch write yourself
(or extend main() to do it) since ledger writes need approval each session.
"""
import json, os, sys, time, datetime, urllib.request
from collections import defaultdict

APIKEY = os.environ.get('ODDSPAPI_KEY', '')
BASE = 'https://api.oddspapi.io/v4'
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


def scan(date_str, edge_min=3.0):
    fixtures = mlb_fixtures_today(date_str)
    print(f'{date_str}: {len(fixtures)} MLB fixtures with odds')
    flags = []
    for fx in fixtures:
        fid = fx['fixtureId']
        game = f"{fx.get('participant1Abbr')}@{fx.get('participant2Abbr')}"
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
                    flags.append(dict(game=game, book=venue, player=info['name'], side='OVER',
                                       fair=round(fair_over * 100, 1), price=round(bo * 100, 1), edge=round(edge_over, 1)))
                if edge_under >= edge_min:
                    flags.append(dict(game=game, book=venue, player=info['name'], side='UNDER',
                                       fair=round((1 - fair_over) * 100, 1), price=round(bu * 100, 1), edge=round(edge_under, 1)))
        time.sleep(0.3)
    flags.sort(key=lambda x: -x['edge'])
    print(f'{len(flags)} plays clear edge >= {edge_min}pts\n')
    for f in flags:
        print(f"{f['game']:10} {f['book']:10} {f['player']:22} {f['side']:6} fair {f['fair']:5.1f} price {f['price']:5.1f} edge {f['edge']:+5.1f}")
    return flags


if __name__ == '__main__':
    if not APIKEY:
        sys.exit('Set ODDSPAPI_KEY')
    date_str = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().isoformat()
    edge = float(sys.argv[sys.argv.index('--edge') + 1]) if '--edge' in sys.argv else 3.0
    scan(date_str, edge)

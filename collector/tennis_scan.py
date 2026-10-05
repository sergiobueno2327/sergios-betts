"""Sergio's Betts -- Tennis Track B scan (added 2026-10-04, Sergio's request).

Markets: match winner (h2h), game spread, total games. Pinnacle = fair source (power de-vig,
BOTH sides at the SAME line, via The Odds API). Execution venues: Kalshi (direct API:
KXATPMATCH/KXWTAMATCH, KXATPGTOTAL/KXWTAGTOTAL, KXATPGSPREAD) plus whatever The Odds API
returns for prophetx/fliff/novig on the same line. Same bar as the other tracks: >= 3 pts
probability edge vs Pinnacle fair, price zone 35-75c, same line only, pregame only, real Pinnacle
price required. (App EV% is return on stake, NOT the bar.)

Coverage limit: The Odds API only carries Pinnacle for the ATP/WTA tour events it lists as active
(currently China Open / Japan Open). Challenger / ITF matches Sergio sees in Upside are NOT covered
-- those still need a screenshot. No roster Gate 1 for tennis: the Pinnacle event names both players.

Run: THEODDSAPI_KEY=... python3 collector/tennis_scan.py [--write out.json]
"""
import os, sys, re, json, time, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nfl_scan import get, nrm, devig_power, american_to_prob, KALSHI_BASE, ZONE

KEY = os.environ.get('THEODDSAPI_KEY', '')
OA = 'https://api.the-odds-api.com/v4'
EDGE_MIN = 0.03
MAX_PIN_VIG = 0.08  # market-width filter: skip Pinnacle two-sided markets with margin > 8%
from nfl_scan import ODDSAPI_VENUES as VENUES


def last(name):
    return nrm(name.split()[-1])


def kalshi_events(series):
    out, cur = [], None
    for _ in range(5):
        u = f"{KALSHI_BASE}/events?series_ticker={series}&status=open&limit=200&with_nested_markets=true" + (f"&cursor={cur}" if cur else '')
        d = get(u) or {}
        out += d.get('events', [])
        cur = d.get('cursor')
        if not cur or not d.get('events'):
            break
    return out


def kalshi_book():
    """Returns list of dicts {kind, names(set of last-name tokens), market, ask_yes, bid_yes, ...}."""
    rows = []
    cfg = [('KXATPMATCH', 'h2h'), ('KXWTAMATCH', 'h2h'), ('KXATPCHALLENGERMATCH', 'h2h'),
           ('KXATPGTOTAL', 'total'), ('KXWTAGTOTAL', 'total'), ('KXATPGSPREAD', 'spread')]
    for series, kind in cfg:
        for e in kalshi_events(series):
            title = nrm(e.get('title', ''))
            for m in e.get('markets', []):
                if m.get('status') not in ('active', 'open'):
                    continue
                f = lambda k: float(m.get(k) or 0)
                bid, ask = f('yes_bid_dollars'), f('yes_ask_dollars')
                if ask <= 0 or ask >= 1:
                    continue
                rows.append(dict(kind=kind, series=series, event=e['event_ticker'], title=title, ticker=m['ticker'],
                                 sub=m.get('yes_sub_title') or '', strike=m.get('floor_strike'), bid=bid, ask=ask,
                                 close=m.get('close_time')))
        time.sleep(0.3)
    return rows


def devig_pair(a, b):
    pa, pb = american_to_prob(a), american_to_prob(b)
    if pa + pb - 1 > MAX_PIN_VIG:
        return None, None  # WIDE MARKET
    fa = devig_power(pa, pb)
    return fa, 1 - fa


def scan(edge_min=EDGE_MIN):
    if not KEY:
        sys.exit('THEODDSAPI_KEY env var is required')
    sports = [s['key'] for s in get(f'{OA}/sports?apiKey={KEY}') or [] if 'tennis' in s['key'] and s.get('active')]
    print(f"Active tennis feeds (The Odds API): {sports}")
    kb = kalshi_book()
    print(f"Kalshi tennis markets loaded: {len(kb)}")
    now = datetime.datetime.now(datetime.timezone.utc)
    plays = []
    for sp in sports:
        evs = get(f"{OA}/sports/{sp}/odds?apiKey={KEY}&regions=us,us2,us_ex,eu&markets=h2h,spreads,totals&oddsFormat=american") or []
        for ev in evs:
            start = datetime.datetime.fromisoformat(ev['commence_time'].replace('Z', '+00:00'))
            if start <= now:
                continue
            home, away = ev['home_team'], ev['away_team']
            hl, al = last(home), last(away)
            books = {b['key']: b for b in ev.get('bookmakers', [])}
            pin = books.get('pinnacle')
            if not pin:
                continue
            # --- Pinnacle fair per market/line ---
            fair = {}  # (mkt, line_key) -> {outcome_label: prob}
            for m in pin['markets']:
                outs = m['outcomes']
                if m['key'] == 'h2h' and len(outs) == 2:
                    fa, fb = devig_pair(outs[0]['price'], outs[1]['price'])
                    if fa is None:
                        continue
                    fair[('h2h', 0)] = {outs[0]['name']: fa, outs[1]['name']: fb}
                elif m['key'] == 'spreads' and len(outs) == 2:
                    # same |point| both sides; keyed by the first outcome's signed point
                    fa, fb = devig_pair(outs[0]['price'], outs[1]['price'])
                    if fa is None:
                        continue
                    fair[('spread', abs(outs[0]['point']))] = {(outs[0]['name'], outs[0]['point']): fa, (outs[1]['name'], outs[1]['point']): fb}
                elif m['key'] == 'totals' and len(outs) == 2:
                    fa, fb = devig_pair(outs[0]['price'], outs[1]['price'])
                    if fa is None:
                        continue
                    fair[('total', outs[0]['point'])] = {outs[0]['name']: fa, outs[1]['name']: fb}
            # --- candidate venue prices ---
            cands = []  # (market_desc, side_desc, fair_p, price, venue, extra)

            def add(mkt, line, side_label, fair_p, price, venue, **extra):
                edge = fair_p - price
                if edge >= edge_min and ZONE[0] <= price <= ZONE[1]:
                    cands.append(dict(mkt=mkt, line=line, side=side_label, fair=fair_p, price=price, venue=venue, edge=edge, **extra))

            # Odds-API venues
            for vk in VENUES:
                b = books.get(vk)
                if not b:
                    continue
                for m in b['markets']:
                    for o in m['outcomes']:
                        if m['key'] == 'h2h':
                            f = fair.get(('h2h', 0), {}).get(o['name'])
                            if f is not None:
                                add('Moneyline', None, o['name'], f, american_to_prob(o['price']), vk)
                        elif m['key'] == 'spreads':
                            f = fair.get(('spread', abs(o['point'])), {}).get((o['name'], o['point']))
                            if f is not None:
                                add('Game Spread', o['point'], o['name'], f, american_to_prob(o['price']), vk)
                        elif m['key'] == 'totals':
                            f = fair.get(('total', o['point']), {}).get(o['name'])
                            if f is not None:
                                add('Total Games', o['point'], o['name'], f, american_to_prob(o['price']), vk)
            # Kalshi (direct)
            for r in kb:
                if not (hl in r['title'] and al in r['title']):
                    continue
                if r['kind'] == 'h2h':
                    who = home if hl in nrm(r['sub']) else away if al in nrm(r['sub']) else None
                    if who is None:
                        continue
                    f = fair.get(('h2h', 0), {}).get(who)
                    if f is not None:
                        add('Moneyline', None, who, f, r['ask'], 'kalshi', ticker=r['ticker'], kside='YES')
                        add('Moneyline', None, 'opp:' + who, 1 - f, 1 - r['bid'], 'kalshi', ticker=r['ticker'], kside='NO',
                            opp=(away if who == home else home))
                elif r['kind'] == 'total' and r['strike'] is not None:
                    fl = fair.get(('total', float(r['strike'])))
                    if fl and 'Over' in fl:
                        add('Total Games', float(r['strike']), 'Over', fl['Over'], r['ask'], 'kalshi', ticker=r['ticker'], kside='YES')
                        add('Total Games', float(r['strike']), 'Under', fl['Under'], 1 - r['bid'], 'kalshi', ticker=r['ticker'], kside='NO')
                elif r['kind'] == 'spread' and r['strike'] is not None:
                    # "<Player> -X.5 games" Yes = player wins by >= X.5 -> spread -X.5 covers
                    mt = re.match(r'(.+?)\s-([\d.]+)\s+games', r['sub'])
                    if not mt:
                        continue
                    pl, x = mt.group(1), float(mt.group(2))
                    who = home if last(pl) == hl else away if last(pl) == al else None
                    if who is None:
                        continue
                    fl = fair.get(('spread', x))
                    if not fl:
                        continue
                    f = fl.get((who, -x))
                    if f is not None:
                        add('Game Spread', -x, who, f, r['ask'], 'kalshi', ticker=r['ticker'], kside='YES')
                    # Kalshi NO = opponent +x covers
                    opp = away if who == home else home
                    f2 = fl.get((opp, x))
                    if f2 is not None:
                        add('Game Spread', x, opp, f2, 1 - r['bid'], 'kalshi', ticker=r['ticker'], kside='NO')
            for c in cands:
                plays.append(dict(game=f"{away} @ {home}", start=ev['commence_time'], sport_key=sp,
                                  market=c['mkt'], line=c['line'], side=c['side'], venue=c['venue'],
                                  fair=round(c['fair'] * 100, 1), price=round(c['price'] * 100, 1),
                                  edge=round(c['edge'] * 100, 1), ticker=c.get('ticker'), kside=c.get('kside')))
        time.sleep(0.3)
    plays.sort(key=lambda p: -p['edge'])
    print(f"\n{len(plays)} tennis Track B plays clear the bar (>= {edge_min*100:.0f}pts vs Pinnacle, zone 35-75c):")
    for p in plays:
        print(p)
    return plays


def to_ledger_docs(plays):
    docs = {}
    for p in plays:
        et = datetime.datetime.fromisoformat(p['start'].replace('Z', '+00:00')).astimezone(datetime.timezone(datetime.timedelta(hours=-4)))
        slug = re.sub(r'[^a-z0-9]+', '', nrm(p['side'].replace('opp:', '')))[:14]
        doc_id = f"{et.date().isoformat()}-tennis-{slug}-{p['market'].split()[0].lower()}{str(p['line']).replace('.', '').replace('-', 'm') if p['line'] is not None else ''}"
        cur = docs.get(doc_id)
        if cur and cur['data']['edge'] >= p['edge']:
            continue
        side = p['side']
        if side.startswith('opp:'):
            side = p.get('opp') or side[4:]
        mk = p['market'] if p['line'] is None else f"{p['market']} {side} {p['line']}" if p['market'] == 'Game Spread' else f"{p['market']} {side} {p['line']}"
        if p['market'] == 'Moneyline':
            mk = f"Moneyline {side}"
        docs[doc_id] = dict(id=doc_id, data=dict(
            date=et.date().isoformat(), game=p['game'], player=side if p['market'] != 'Total Games' else '', market=mk,
            entry=p['price'], fair=p['fair'], fairSource=f"Pinnacle no-vig (The Odds API, tennis_scan.py) vs {p['venue']}",
            edge=p['edge'], side='YES' if p['kside'] in (None, 'YES') else 'NO', sport='Tennis', track='B', stake=0, fee=0,
            orderType='maker' if p['venue'] == 'kalshi' else 'taker', kalshi_ticker=p.get('ticker') if p['venue'] == 'kalshi' else None,
            start=p['start'], status=f"Paper — Track B tennis scan, pending fill ({p['venue']})",
            note=f"Tennis Track B scan. Edge +{p['edge']}pts vs {p['venue']}. Pinnacle fair {p['fair']}c. Verify match still pregame before betting.",
            close=None, result=None, zone='35–75'))
    return list(docs.values())


if __name__ == '__main__':
    pl = scan()
    try:
        import adverse_flag; adverse_flag.annotate_plays(pl)  # info-only, non-blocking
    except Exception as e:
        print('adverse_flag skipped:', e)
    if '--write' in sys.argv:
        out = sys.argv[sys.argv.index('--write') + 1]
        d = to_ledger_docs(pl)
        json.dump(d, open(out, 'w'), indent=1)
        print(f"\nWrote {len(d)} ledger-ready docs to {out}")
